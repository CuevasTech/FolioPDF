"""Proceso de IA: modelos locales (llama.cpp y ONNX Runtime) en un proceso aparte.

Lo lanza el intermediario dentro del mismo AppContainer que el motor: sin red,
sin poder crear procesos y sin acceso a los archivos del usuario. Solo puede
leer la carpeta de modelos (que la aplicacion descargo y comprobo con su huella).
Nunca recibe documentos: solo trozos de texto o una imagen que le pasa el
intermediario, y devuelve vectores, entidades, una imagen o texto generado.

Con la grafica (Vulkan) no se puede activar ACG (los controladores generan
codigo al compilar sus programas); sin grafica se activa como en el motor.

Peticiones (una cabecera JSON por marco, ver protocol.py):
  configure  rutas de los modelos (relativas a la carpeta de modelos) y si se usa la grafica
  embed      textos -> vectores normalizados (float32)
  ner        textos -> entidades [tipo, inicio, fin, confianza]
  unwarp     imagen RGB -> imagen RGB aplanada
  generate   mensajes -> texto, en trozos ("token") y un final ("done"); "cancel" lo para.
             Con "reuse" (el final del documento en el prompt), lo leido hasta ahi se
             guarda y la siguiente peticion que empiece igual no lo vuelve a leer.
"""

from __future__ import annotations

import codecs
import ctypes
import json
import logging
import os
import re
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from .protocol import FrameWriter, ProtocolError, read_frame

log = logging.getLogger("folio.ai")

MAX_TEXTS = 64
MAX_TEXT_CHARS = 8_000
MAX_MESSAGES = 16
MAX_PROMPT_CHARS = 60_000
MAX_NEW_TOKENS = 2_048
MAX_IMAGE_SIDE = 4_096
NER_MAX_TOKENS = 512
_REUSE = re.compile(r"</doc-[0-9a-f]{8}>")

# Marca de agua estadistica del texto generado (Reglamento de IA, art. 50.2).
# Esquema publico ("lista verde" de Kirchenbauer et al., 2023): antes de elegir
# cada palabra, se favorece una parte del vocabulario que depende de la palabra
# anterior y de esta clave. No es secreta: cualquiera puede comprobar la marca
# con la misma clave (ver detect_watermark).
WATERMARK_KEY = 0x464F4C494F31  # "FOLIO1"
WATERMARK_GAMMA = 0.25
WATERMARK_DELTA = 4.0  # con 1,5 apenas se detectaba en respuestas de temperatura baja (z 2,7); con 4,0, z 7,8 y el mismo contenido


def _inside(root: Path, rel: str) -> Path:
    """Ruta de un modelo dentro de la carpeta de modelos (nada de rutas absolutas ni "..")."""
    if not isinstance(rel, str) or not rel or len(rel) > 300 or "\\" in rel or rel.startswith("/") or ":" in rel:
        raise ValueError("ruta de modelo no valida")
    path = (root / rel).resolve()
    if root not in path.parents or not path.is_file():
        raise ValueError("ruta de modelo no valida")
    return path


def _texts(header: dict[str, Any]) -> list[str]:
    texts = header.get("texts")
    if not isinstance(texts, list) or not 0 < len(texts) <= MAX_TEXTS:
        raise ValueError("textos no validos")
    if not all(isinstance(t, str) and len(t) <= MAX_TEXT_CHARS for t in texts):
        raise ValueError("textos no validos")
    return texts


def green_mask(prev_token: int, vocab: int) -> Any:
    import numpy as np

    rng = np.random.default_rng((WATERMARK_KEY * 1_000_003 + int(prev_token)) & 0xFFFF_FFFF_FFFF_FFFF)
    return rng.random(vocab) < WATERMARK_GAMMA


class Watermark:
    """Procesador de logits de llama.cpp que aplica la marca de agua."""

    def __call__(self, input_ids: Any, scores: Any) -> Any:
        prev = int(input_ids[-1]) if len(input_ids) else 0
        scores = scores.copy()
        scores[green_mask(prev, scores.shape[-1])] += WATERMARK_DELTA
        return scores


# ----------------------------------------------------------------- graficas


@dataclass(frozen=True)
class Gpu:
    index: int  # posicion en la lista de Vulkan (la que usa GGML_VK_VISIBLE_DEVICES)
    name: str
    discrete: bool
    vram: int  # bytes de memoria propia


def list_gpus() -> list[Gpu]:
    """Graficas que ve Vulkan (sin cargar llama.cpp). Lista vacia si no hay Vulkan."""
    import ctypes

    class AppInfo(ctypes.Structure):
        _fields_ = [("sType", ctypes.c_int), ("pNext", ctypes.c_void_p), ("pApplicationName", ctypes.c_char_p),
                    ("applicationVersion", ctypes.c_uint32), ("pEngineName", ctypes.c_char_p),
                    ("engineVersion", ctypes.c_uint32), ("apiVersion", ctypes.c_uint32)]

    class InstanceInfo(ctypes.Structure):
        _fields_ = [("sType", ctypes.c_int), ("pNext", ctypes.c_void_p), ("flags", ctypes.c_uint32),
                    ("pApplicationInfo", ctypes.POINTER(AppInfo)), ("enabledLayerCount", ctypes.c_uint32),
                    ("ppEnabledLayerNames", ctypes.c_void_p), ("enabledExtensionCount", ctypes.c_uint32),
                    ("ppEnabledExtensionNames", ctypes.c_void_p)]

    class Props(ctypes.Structure):
        # VkPhysicalDeviceProperties hasta deviceName; el resto se reserva de sobra.
        _fields_ = [("apiVersion", ctypes.c_uint32), ("driverVersion", ctypes.c_uint32), ("vendorID", ctypes.c_uint32),
                    ("deviceID", ctypes.c_uint32), ("deviceType", ctypes.c_int),
                    ("deviceName", ctypes.c_char * 256), ("rest", ctypes.c_byte * 4096)]

    class MemType(ctypes.Structure):
        _fields_ = [("propertyFlags", ctypes.c_uint32), ("heapIndex", ctypes.c_uint32)]

    class MemHeap(ctypes.Structure):
        _fields_ = [("size", ctypes.c_uint64), ("flags", ctypes.c_uint32)]

    class MemProps(ctypes.Structure):
        _fields_ = [("memoryTypeCount", ctypes.c_uint32), ("memoryTypes", MemType * 32),
                    ("memoryHeapCount", ctypes.c_uint32), ("memoryHeaps", MemHeap * 16)]

    try:
        vk = ctypes.WinDLL("vulkan-1.dll")
    except OSError:
        return []
    handle = ctypes.c_void_p
    vk.vkCreateInstance.restype = ctypes.c_int
    vk.vkCreateInstance.argtypes = [ctypes.POINTER(InstanceInfo), ctypes.c_void_p, ctypes.POINTER(handle)]
    vk.vkEnumeratePhysicalDevices.restype = ctypes.c_int
    vk.vkEnumeratePhysicalDevices.argtypes = [handle, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(handle)]
    vk.vkGetPhysicalDeviceProperties.argtypes = [handle, ctypes.POINTER(Props)]
    vk.vkGetPhysicalDeviceMemoryProperties.argtypes = [handle, ctypes.POINTER(MemProps)]
    vk.vkDestroyInstance.argtypes = [handle, ctypes.c_void_p]
    app = AppInfo(0, None, b"Folio", 1, b"Folio", 1, (1 << 22) | (2 << 12))
    info = InstanceInfo(1, None, 0, ctypes.pointer(app), 0, None, 0, None)
    instance = handle()
    if vk.vkCreateInstance(ctypes.byref(info), None, ctypes.byref(instance)) != 0:
        return []
    try:
        count = ctypes.c_uint32(0)
        if vk.vkEnumeratePhysicalDevices(instance, ctypes.byref(count), None) != 0 or not count.value:
            return []
        count.value = min(count.value, 16)
        devices = (handle * count.value)()
        if vk.vkEnumeratePhysicalDevices(instance, ctypes.byref(count), devices) not in (0, 5):  # 5: VK_INCOMPLETE
            return []
        out: list[Gpu] = []
        for i in range(count.value):
            props, mem = Props(), MemProps()
            vk.vkGetPhysicalDeviceProperties(devices[i], ctypes.byref(props))
            vk.vkGetPhysicalDeviceMemoryProperties(devices[i], ctypes.byref(mem))
            heaps = [mem.memoryHeaps[h] for h in range(min(mem.memoryHeapCount, 16))]
            vram = max((h.size for h in heaps if h.flags & 1), default=0)  # 1: memoria propia de la grafica
            out.append(Gpu(i, props.deviceName.decode("utf-8", "replace")[:120], props.deviceType == 2, int(vram)))
        return out
    finally:
        vk.vkDestroyInstance(instance, None)


def pick_gpu() -> Gpu | None:
    """La grafica dedicada con mas memoria. Las integradas no se usan: en la prueba
    de concepto iban tres veces mas lentas que el propio procesador."""
    try:
        dedicated = [g for g in list_gpus() if g.discrete and g.vram >= 2 * 1024**3]
    except Exception:  # noqa: BLE001 - sin Vulkan o con un controlador raro: procesador
        return None
    return max(dedicated, key=lambda g: g.vram) if dedicated else None


def gguf_block_count(path: Path) -> int | None:
    """Numero de capas del modelo, leido de la cabecera GGUF (solo la clave <arquitectura>.block_count)."""
    import struct

    sizes = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}
    try:
        with open(path, "rb") as fh:
            if fh.read(4) != b"GGUF":
                return None
            version, _tensors, kv_count = struct.unpack("<IQQ", fh.read(20))
            if version < 2 or kv_count > 10_000:
                return None

            def text() -> str:
                (n,) = struct.unpack("<Q", fh.read(8))
                if n > 1 << 20:
                    raise ValueError
                return fh.read(n).decode("utf-8", "replace")

            def skip(kind: int) -> None:
                if kind in sizes:
                    fh.seek(sizes[kind], 1)
                elif kind == 8:
                    text()
                elif kind == 9:
                    inner, n = struct.unpack("<IQ", fh.read(12))
                    if n > 1 << 24:
                        raise ValueError
                    for _i in range(n):
                        skip(inner)
                else:
                    raise ValueError

            for _i in range(kv_count):
                key = text()
                (kind,) = struct.unpack("<I", fh.read(4))
                if key.endswith(".block_count") and kind in (4, 5):
                    (value,) = struct.unpack("<I", fh.read(4))
                    return int(value) if 0 < value < 1000 else None
                skip(kind)
    except (OSError, ValueError, struct.error):
        return None
    return None


def gpu_layers_for(model: Path, gpu: Gpu | None) -> int:
    """Cuantas capas caben en la grafica (-1: todas; 0: ninguna, solo procesador)."""
    if gpu is None:
        return 0
    size = model.stat().st_size
    budget = gpu.vram * 0.85 - 600 * 1024**2  # lo que necesitan el contexto y el propio controlador
    if budget >= size * 1.1:
        return -1
    layers = gguf_block_count(model)
    if not layers or budget <= 0:
        return 0
    fitting = int(layers * budget / (size * 1.1))
    # Con menos de un cuarto del modelo en la grafica apenas se gana y se gasta su memoria.
    return fitting if fitting >= layers // 4 else 0


class Models:
    def __init__(self, root: Path, gpu: Gpu | None = None) -> None:
        self.root = root.resolve()
        self.gpu = gpu
        self.paths: dict[str, dict[str, Any]] = {}
        self.llm: Any = None
        self.chat: _Chat | None = None
        self.embedder: Any = None
        self.ner: Any = None
        self.unwarper: Any = None

    # ---------------------------------------------------------------- carga

    def configure(self, header: dict[str, Any]) -> dict[str, Any]:
        models = header.get("models")
        if not isinstance(models, dict):
            raise ValueError("configuracion no valida")
        paths: dict[str, dict[str, Any]] = {}
        for kind in ("llm", "embed", "ner", "unwarp"):
            entry = models.get(kind)
            if entry is None:
                continue
            if not isinstance(entry, dict) or not isinstance(entry.get("files"), dict):
                raise ValueError("configuracion no valida")
            paths[kind] = {name: _inside(self.root, rel) for name, rel in entry["files"].items()}
            if kind == "llm":
                ctx = entry.get("ctx", 8192)
                paths[kind]["ctx"] = ctx if isinstance(ctx, int) and 2048 <= ctx <= 32768 else 8192
        self.paths = paths
        self.llm = self.chat = self.embedder = self.ner = self.unwarper = None
        return {"kinds": sorted(paths), "gpu": self.gpu.name if self.gpu else None}

    def _session(self, model: Path) -> Any:
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.intra_op_num_threads = max(1, min(4, (os.cpu_count() or 4) // 2))
        options.log_severity_level = 3
        return ort.InferenceSession(str(model), options, providers=["CPUExecutionProvider"])

    def _tokenizer(self, path: Path, limit: int) -> Any:
        from tokenizers import Tokenizer

        tok = Tokenizer.from_file(str(path))
        tok.enable_truncation(limit)
        return tok

    def need(self, kind: str) -> dict[str, Any]:
        if kind not in self.paths:
            raise LookupError(kind)
        return self.paths[kind]

    def get_llm(self) -> Any:
        if self.llm is None:
            from llama_cpp import Llama

            entry = self.need("llm")
            layers = gpu_layers_for(entry["model"], self.gpu)
            # Si no cabe (el contexto tambien ocupa memoria de la grafica), se
            # prueba con la mitad de capas y, al final, solo con el procesador.
            attempts = [layers] if layers == 0 else [layers, max((gguf_block_count(entry["model"]) or 32) // 2, 1) if layers == -1 else layers // 2, 0]
            for count in attempts:
                try:
                    self.llm = Llama(
                        model_path=str(entry["model"]),
                        n_ctx=entry["ctx"],
                        n_gpu_layers=count,
                        n_threads=max(1, (os.cpu_count() or 4) // 2),
                        verbose=False,
                    )
                    log.info("modelo cargado (%s capas en la grafica)", "todas" if count == -1 else count)
                    break
                except (ValueError, RuntimeError):
                    log.warning("el modelo no cabe con %s capas en la grafica", count)
                    if count == 0:
                        raise
        return self.llm

    def get_chat(self) -> _Chat:
        if self.chat is None:
            self.chat = _Chat(self.get_llm())
        return self.chat

    def get_embedder(self) -> tuple[Any, Any]:
        if self.embedder is None:
            entry = self.need("embed")
            tok = self._tokenizer(entry["tokenizer"], 512)
            tok.enable_padding()
            self.embedder = (tok, self._session(entry["model"]))
        return self.embedder

    def get_ner(self) -> tuple[Any, Any, dict[int, str]]:
        if self.ner is None:
            entry = self.need("ner")
            config = json.loads(entry["config"].read_text(encoding="utf-8"))
            labels = {int(k): str(v) for k, v in config.get("id2label", {}).items()}
            self.ner = (self._tokenizer(entry["tokenizer"], NER_MAX_TOKENS), self._session(entry["model"]), labels)
        return self.ner

    def get_unwarper(self) -> Any:
        if self.unwarper is None:
            self.unwarper = self._session(self.need("unwarp")["model"])
        return self.unwarper

    # ---------------------------------------------------------------- tareas

    def embed(self, texts: list[str]) -> tuple[dict[str, Any], bytes]:
        import numpy as np

        tok, session = self.get_embedder()
        encoded = tok.encode_batch(texts)
        ids = np.array([e.ids for e in encoded], dtype=np.int64)
        mask = np.array([e.attention_mask for e in encoded], dtype=np.int64)
        hidden = session.run(None, {"input_ids": ids, "attention_mask": mask})[0]
        cls = hidden[:, 0, :].astype(np.float32)  # el modelo usa el primer elemento (CLS)
        cls /= np.maximum(np.linalg.norm(cls, axis=1, keepdims=True), 1e-9)
        return {"count": int(cls.shape[0]), "dims": int(cls.shape[1])}, cls.tobytes()

    def entities(self, texts: list[str]) -> dict[str, Any]:
        import numpy as np

        tok, session, labels = self.get_ner()
        results: list[list[list[Any]]] = []
        for text in texts:
            enc = tok.encode(text)
            feeds = {"input_ids": np.array([enc.ids], np.int64), "attention_mask": np.array([enc.attention_mask], np.int64)}
            logits = session.run(None, feeds)[0][0]
            probs = np.exp(logits - logits.max(-1, keepdims=True))
            probs /= probs.sum(-1, keepdims=True)
            spans: list[list[Any]] = []
            current: list[Any] | None = None
            for i, (start, end) in enumerate(enc.offsets):
                if start == end:
                    continue
                label = labels.get(int(probs[i].argmax()), "O")
                score = float(probs[i].max())
                if label == "O" or len(label) < 3:
                    current = None
                    continue
                kind = label[2:]
                if label.startswith("B-") or current is None or current[0] != kind or start > current[2] + 1:
                    current = [kind, start, end, score]
                    spans.append(current)
                else:
                    current[2] = end
                    current[3] = min(current[3], score)
            # Sin espacios al principio (los tokenizadores los incluyen en el desplazamiento).
            for span in spans:
                while span[1] < span[2] and text[span[1]].isspace():
                    span[1] += 1
                span[3] = round(span[3], 3)
            results.append([s for s in spans if s[1] < s[2]])
        return {"entities": results}

    def unwarp(self, header: dict[str, Any], payload: bytes) -> tuple[dict[str, Any], bytes]:
        import numpy as np

        width, height = header.get("w"), header.get("h")
        if not (isinstance(width, int) and isinstance(height, int) and 16 <= width <= MAX_IMAGE_SIDE and 16 <= height <= MAX_IMAGE_SIDE):
            raise ValueError("imagen no valida")
        if len(payload) != width * height * 3:
            raise ValueError("imagen no valida")
        image = np.frombuffer(payload, dtype=np.uint8).reshape(height, width, 3)
        tensor = (image.astype(np.float32) / 255.0).transpose(2, 0, 1)[None]
        out = self.get_unwarper().run(None, {"image": tensor})[0][0]
        result = np.clip(out.transpose(1, 2, 0) * 255.0 + 0.5, 0, 255).astype(np.uint8)
        return {"w": int(result.shape[1]), "h": int(result.shape[0])}, np.ascontiguousarray(result).tobytes()


class _Chat:
    """Conversacion con el modelo de lenguaje que no relee lo ya leido.

    Leer el prompt es lo que mas tarda sin grafica (unos 40 tokens por segundo
    en un procesador de 6 nucleos: casi un minuto para un documento de 9000
    caracteres). Las peticiones sobre un mismo documento empiezan igual (las
    instrucciones y el documento; ver ai_prompts.doc_end), asi que se guarda el
    estado del modelo al final del documento y la siguiente peticion que empiece
    igual solo lee lo que falta.

    Qwen3.5 es hibrido (capas recurrentes): su estado no se puede recortar, asi
    que se guarda una copia (unos 125 MB con 2300 tokens). Un transformer normal
    (Granite) se recorta en el sitio, sin copia.
    """

    def __init__(self, llm: Any) -> None:
        from llama_cpp import llama_chat_format

        template = llm.metadata.get("tokenizer.chat_template")
        if not isinstance(template, str) or not template:
            raise ValueError("el modelo no trae plantilla de chat")
        eos, bos = llm.token_eos(), llm.token_bos()
        self.llm = llm
        self.formatter = llama_chat_format.Jinja2ChatFormatter(
            template=template,
            eos_token=llm._model.token_get_text(eos) if eos != -1 else "",
            bos_token=llm._model.token_get_text(bos) if bos != -1 else "",
            stop_token_ids=[eos],
        )
        self.recurrent = bool(getattr(llm, "_is_recurrent", False) or getattr(llm, "_is_hybrid", False))
        self.prefix: list[int] | None = None
        self.state: Any = None  # copia del estado (solo modelos recurrentes)
        self.state_size = 0

    def tokens(self, messages: list[dict[str, str]], reuse: str | None) -> tuple[list[int], list[int]]:
        """El prompt en dos partes: hasta el final del documento (reutilizable) y el resto."""
        result = self.formatter(messages=messages)
        prompt = result.prompt
        cut = prompt.rfind(reuse) + len(reuse) if reuse and reuse in prompt else 0
        add_bos = not result.added_special
        prefix = self.llm.tokenize(prompt[:cut].encode("utf-8"), add_bos=add_bos, special=True) if cut else []
        suffix = self.llm.tokenize(prompt[cut:].encode("utf-8"), add_bos=add_bos and not cut, special=True)
        return prefix, suffix

    def _restore(self, prefix: list[int]) -> bool:
        import llama_cpp

        if self.prefix != prefix:
            return False
        if not self.recurrent:
            # Transformer: se quita lo que vino despues del documento.
            return bool(self.llm._ctx.kv_cache_seq_rm(-1, len(prefix), -1))
        if self.state is None:
            return False
        memory = llama_cpp.llama_get_memory(self.llm._ctx.ctx)
        llama_cpp.llama_memory_clear(memory, True)
        return llama_cpp.llama_state_seq_set_data(self.llm._ctx.ctx, self.state, self.state_size, 0) == self.state_size

    def _save(self, prefix: list[int]) -> None:
        import llama_cpp

        self.prefix, self.state, self.state_size = prefix, None, 0
        if self.recurrent:
            ctx = self.llm._ctx.ctx
            size = llama_cpp.llama_state_seq_get_size(ctx, 0)
            buffer = (ctypes.c_uint8 * size)()
            written = llama_cpp.llama_state_seq_get_data(ctx, buffer, size, 0)
            if 0 < written <= size:
                self.state, self.state_size = buffer, written
            else:
                self.prefix = None

    def prepare(self, prefix: list[int], suffix: list[int]) -> list[int]:
        """Deja el modelo listo para leer lo que falta y lo devuelve."""
        llm = self.llm
        if prefix and self._restore(prefix):
            llm.n_tokens = len(prefix)
            llm.input_ids[: len(prefix)] = prefix
            log.info("se reutilizan %d tokens ya leidos", len(prefix))
            return suffix
        self.prefix, self.state, self.state_size = None, None, 0
        llm.reset()
        if not prefix:
            return suffix
        llm.eval(prefix)
        self._save(prefix)
        return suffix

    def generate(self, messages: list[dict[str, str]], reuse: str | None, *, max_tokens: int, temperature: float,
                 processors: Any, cancel: threading.Event) -> Any:
        """Trozos de texto ``("text", ...)`` y, al final, el motivo: "stop", "length" o "cancelled"."""
        import llama_cpp

        prefix, suffix = self.tokens(messages, reuse)
        room = self.llm.n_ctx() - len(prefix) - len(suffix)
        if room < 16:
            raise ValueError("prompt demasiado largo")
        tokens = self.prepare(prefix, suffix)
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        vocab = self.llm._model.vocab
        limit = min(max_tokens, room - 1)
        produced = 0
        for token in self.llm.generate(tokens, reset=False, temp=temperature, top_p=0.9, repeat_penalty=1.05,
                                       logits_processor=processors):
            if cancel.is_set():
                yield "cancelled"
                return
            if llama_cpp.llama_token_is_eog(vocab, token):
                break
            piece = decoder.decode(self.llm.detokenize([token], special=False))
            if piece:
                yield ("text", piece)
            produced += 1
            if produced >= limit:
                yield "length"
                return
        tail = decoder.decode(b"", final=True)
        if tail:
            yield ("text", tail)
        yield "stop"


class _Generation:
    def __init__(self) -> None:
        self.cancel = threading.Event()
        self.thread: threading.Thread | None = None


def _messages(header: dict[str, Any]) -> list[dict[str, str]]:
    messages = header.get("messages")
    if not isinstance(messages, list) or not 0 < len(messages) <= MAX_MESSAGES:
        raise ValueError("mensajes no validos")
    out: list[dict[str, str]] = []
    total = 0
    for m in messages:
        if not isinstance(m, dict) or m.get("role") not in ("system", "user", "assistant") or not isinstance(m.get("content"), str):
            raise ValueError("mensajes no validos")
        total += len(m["content"])
        out.append({"role": m["role"], "content": m["content"]})
    if total > MAX_PROMPT_CHARS:
        raise ValueError("mensajes demasiado largos")
    return out


def serve(rfile: BinaryIO, wfile: BinaryIO, root: Path, gpu: Gpu | None = None) -> int:
    models = Models(root, gpu)
    out = FrameWriter(wfile)
    generation: _Generation | None = None

    def run_generation(job: _Generation, header: dict[str, Any], messages: list[dict[str, str]]) -> None:
        produced = 0
        reason = "stop"
        try:
            max_tokens = header.get("maxTokens", 512)
            max_tokens = max_tokens if isinstance(max_tokens, int) and 1 <= max_tokens <= MAX_NEW_TOKENS else 512
            temperature = header.get("temperature", 0.2)
            temperature = float(temperature) if isinstance(temperature, (int, float)) and 0 <= temperature <= 1.5 else 0.2
            from llama_cpp import LogitsProcessorList

            processors = LogitsProcessorList([Watermark()]) if header.get("watermark") is True else None
            reuse = header.get("reuse")
            reuse = reuse if isinstance(reuse, str) and _REUSE.fullmatch(reuse) else None
            for item in models.get_chat().generate(messages, reuse, max_tokens=max_tokens, temperature=temperature,
                                                   processors=processors, cancel=job.cancel):
                if isinstance(item, tuple):
                    produced += 1
                    out.write({"op": "token", "text": item[1]})
                else:
                    reason = item
            out.write({"op": "done", "reason": reason, "chunks": produced})
        except LookupError:
            out.write({"op": "done", "reason": "missing"})
        except Exception as exc:  # noqa: BLE001
            log.error("la generacion ha fallado: %s", type(exc).__name__)
            out.write({"op": "done", "reason": "error"})

    while True:
        try:
            frame = read_frame(rfile)
        except ProtocolError:
            log.error("marco no valido; se cierra el proceso de IA")
            return 1
        if frame is None:
            return 0
        header, payload = frame
        op = header.get("op")
        try:
            if op == "cancel":
                if generation is not None:
                    generation.cancel.set()
                continue
            if generation is not None and generation.thread is not None and generation.thread.is_alive():
                out.write({"op": "error", "error": "busy"})
                continue
            if op == "configure":
                out.write({"op": "ok", **models.configure(header)})
            elif op == "embed":
                result, data = models.embed(_texts(header))
                out.write({"op": "ok", **result}, data)
            elif op == "ner":
                out.write({"op": "ok", **models.entities(_texts(header))})
            elif op == "unwarp":
                result, data = models.unwarp(header, payload)
                out.write({"op": "ok", **result}, data)
            elif op == "generate":
                messages = _messages(header)
                generation = _Generation()
                generation.thread = threading.Thread(
                    target=run_generation, args=(generation, header, messages), name="generacion", daemon=True
                )
                generation.thread.start()
            else:
                out.write({"op": "error", "error": "unknown"})
        except LookupError:
            out.write({"op": "error", "error": "missing"})
        except ValueError:
            out.write({"op": "error", "error": "invalid"})
        except Exception as exc:  # noqa: BLE001
            log.error("error en el proceso de IA (%s): %s", op if isinstance(op, str) else "?", type(exc).__name__)
            out.write({"op": "error", "error": "internal"})


def detect_watermark(token_ids: list[int], vocab: int) -> float:
    """Puntuacion z de la marca de agua en una secuencia de tokens (mayor de 4: casi seguro marcada)."""
    if len(token_ids) < 2:
        return 0.0
    hits = sum(1 for prev, tok in zip(token_ids, token_ids[1:]) if green_mask(prev, vocab)[tok])
    n = len(token_ids) - 1
    expected = WATERMARK_GAMMA * n
    return (hits - expected) / ((n * WATERMARK_GAMMA * (1 - WATERMARK_GAMMA)) ** 0.5)


def main(arguments: list[str], rfile: BinaryIO, wfile: BinaryIO) -> int:
    """``--ai-worker <carpeta de modelos> [--gpu]`` (lo lanza el intermediario)."""
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")
    if not arguments:
        return 2
    models_root = Path(arguments[0])
    if not models_root.is_absolute() or not models_root.is_dir():
        log.error("la carpeta de modelos no existe")
        return 2
    gpu = pick_gpu() if "--gpu" in arguments[1:] else None
    # llama.cpp elige la grafica al cargarse: solo la dedicada elegida, o ninguna.
    os.environ["GGML_VK_VISIBLE_DEVICES"] = str(gpu.index) if gpu else "none"
    # Todo se carga ahora, antes de leer nada: en Windows, cargar NumPy con un
    # hilo esperando en la entrada estandar se queda bloqueado (ver __main__).
    import numpy  # noqa: F401
    import onnxruntime  # noqa: F401
    import tokenizers  # noqa: F401
    import llama_cpp  # noqa: F401
    from llama_cpp import LogitsProcessorList, llama_chat_format  # noqa: F401

    if gpu is None:
        # ACG como en el motor; con grafica no se puede (ver la cabecera).
        from . import winsandbox

        if not winsandbox.lock_down_dynamic_code():
            log.warning("no se pudo prohibir la generacion de codigo en el proceso de IA")
    log.info("proceso de IA listo (%s)", f"grafica {gpu.name}" if gpu else "procesador")
    return serve(rfile, wfile, models_root, gpu)


def detect_main(arguments: list[str]) -> int:
    """``--ai-detect <carpeta de modelos> <archivo.txt>``: dice si un texto lleva la marca de Folio.

    Para cada modelo de lenguaje instalado (la marca depende de su
    vocabulario) carga solo el vocabulario, convierte el texto en tokens y
    calcula la puntuacion z. Mas de 4: casi seguro escrito por la IA de Folio.
    Esquema publico: ver WATERMARK_* y green_mask (Kirchenbauer et al., 2023).
    """
    import json as _json

    if len(arguments) != 2:
        print("uso: --ai-detect <carpeta de modelos> <archivo.txt>", file=sys.stderr)
        return 2
    root, source = Path(arguments[0]), Path(arguments[1])
    text = source.read_text(encoding="utf-8", errors="replace")[:200_000]
    from llama_cpp import Llama

    results = {}
    for model in sorted(root.glob("llm-*/*/model.gguf")):
        llm = Llama(model_path=str(model), vocab_only=True, verbose=False)
        tokens = llm.tokenize(text.encode("utf-8"), add_bos=False, special=False)
        z = detect_watermark(tokens, llm.n_vocab())
        results[f"{model.parent.parent.name}/{model.parent.name}"] = {"tokens": len(tokens), "z": round(z, 2), "marked": z > 4}
    print(_json.dumps(results, ensure_ascii=False, indent=1))
    return 0
