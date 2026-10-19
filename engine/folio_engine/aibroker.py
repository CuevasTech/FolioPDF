"""La IA local vista desde el intermediario: configuracion, proceso de IA y permisos.

* La aplicacion dice que modelos hay (``ai.configure``): rutas relativas a la
  carpeta de modelos, que aqui se vuelven a comprobar (dentro de la carpeta,
  nombres de forma fija, archivos que existen). El motor aislado nunca elige
  que archivo se carga.
* El proceso de IA (aiworker.py) se lanza cuando hace falta, dentro del mismo
  AppContainer que el motor, con su propio objeto de trabajo (memoria acotada,
  sin procesos hijos) y permiso de solo lectura en la carpeta de modelos. Se
  cierra tras unos minutos sin uso para devolver la memoria.
* El motor aislado solo puede pedir a la IA lo que corresponde a la operacion
  del usuario en curso (buscar por significado, resumir...), con limites de
  tamano y de numero de peticiones (A01:2025 y LLM10:2025).
* Si con la grafica el proceso de IA falla al arrancar o al cargar el modelo,
  se vuelve a lanzar solo con el procesador.
"""

from __future__ import annotations

import ctypes
import logging
import queue
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

from . import winsandbox
from .errors import EngineError, denied, invalid
from .i18n import _
from .protocol import FrameWriter, ProtocolError, read_frame

log = logging.getLogger("folio.broker.ia")

IDLE_SECONDS = 300  # sin uso durante este tiempo, el proceso de IA se cierra
CALL_SECONDS = 180  # tope de una peticion corta (vectores, entidades, aplanar)
GENERATION_SECONDS = 900  # tope de una respuesta del modelo de lenguaje
START_SECONDS = 180  # tope para arrancar y cargar la configuracion
MAX_CALLS_PER_OPERATION = 5_000
MAX_POLL_WAIT = 0.5
MARGIN = 3 * 1024**3  # memoria del proceso de IA ademas de los modelos (contexto, calculos)

_REL = re.compile(r"^[a-z0-9-]{1,40}/[A-Za-z0-9_.-]{1,40}/[A-Za-z0-9_.-]{1,120}$")
_REUSE = re.compile(r"</doc-[0-9a-f]{8}>")
_NAMES = {"llm": {"model"}, "embed": {"model", "tokenizer"}, "ner": {"model", "tokenizer", "config"}, "unwarp": {"model"}}

# Que puede pedir a la IA cada operacion del usuario (y nada mas).
AI_OPS: dict[str, frozenset[str]] = {
    "ai.search": frozenset({"embed"}),
    "ai.ask": frozenset({"embed", "device", "start", "poll", "stop"}),
    "ai.summarize": frozenset({"embed", "device", "start", "poll", "stop"}),
    "ai.transform": frozenset({"start", "poll", "stop"}),
    "redact.candidates": frozenset({"ner"}),
    "scan.preview": frozenset({"unwarp"}),
    "scan.build": frozenset({"unwarp"}),
}


def unavailable() -> EngineError:
    return EngineError("ai_unavailable", _("La IA no está activada o sus modelos no están descargados."))


def cpu_ok() -> bool:
    """Los modelos necesitan AVX2 (procesadores desde 2013 aproximadamente)."""
    try:
        return bool(ctypes.windll.kernel32.IsProcessorFeaturePresent(40))  # PF_AVX2_INSTRUCTIONS_AVAILABLE
    except (AttributeError, OSError):
        return False


Spawn = Callable[[list[str], int], tuple[winsandbox.Child, winsandbox.Job]]


class _Process:
    """El proceso de IA en marcha y lo que llega por su salida."""

    def __init__(self, child: winsandbox.Child, job: winsandbox.Job, gpu: bool) -> None:
        self.child = child
        self.job = job
        self.gpu = gpu
        self.writer = FrameWriter(child.stdin)
        self.replies: queue.Queue[tuple[dict[str, Any], bytes] | None] = queue.Queue()
        self.text: list[str] = []
        self.done: str | None = None
        self.generating = False
        self.alive = True
        self.changed = threading.Condition()
        threading.Thread(target=self._read, name="ia-salida", daemon=True).start()
        threading.Thread(target=self._drain_errors, name="ia-errores", daemon=True).start()

    def _read(self) -> None:
        try:
            while True:
                frame = read_frame(self.child.stdout)
                if frame is None:
                    break
                header, payload = frame
                op = header.get("op")
                if op in ("token", "done"):
                    with self.changed:
                        if op == "token" and isinstance(header.get("text"), str):
                            self.text.append(header["text"][:4000])
                        elif op == "done":
                            self.done = str(header.get("reason", "stop"))[:20]
                            self.generating = False
                        self.changed.notify_all()
                else:
                    self.replies.put((header, payload))
        except (ProtocolError, OSError, ValueError):
            log.error("salida no valida del proceso de IA")
        finally:
            with self.changed:
                self.alive = False
                if self.generating:
                    self.done = "error"
                    self.generating = False
                self.changed.notify_all()
            self.replies.put(None)

    def _drain_errors(self) -> None:
        # Los avisos del proceso de IA van al registro (sin contenido de documentos).
        try:
            for line in self.child.stderr:
                text = line.decode("utf-8", "replace").strip()
                if text.startswith(("INFO folio", "WARNING folio", "ERROR folio")):
                    log.info("ia: %s", text[:300])
        except (OSError, ValueError):
            pass

    def request(self, header: dict[str, Any], payload: bytes = b"", timeout: float = CALL_SECONDS) -> tuple[dict[str, Any], bytes]:
        self.writer.write(header, payload)
        try:
            reply = self.replies.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError from None
        if reply is None:
            raise ConnectionError
        return reply

    def close(self) -> None:
        try:
            self.child.stdin.close()
        except OSError:
            pass
        if self.child.wait(3.0) is None:
            self.child.kill()
            self.child.wait(5.0)
        self.child.close()
        self.job.close()


class AiService:
    def __init__(self, models_root: Path | None, spawn: Spawn, grant_read: Callable[[Path], None]) -> None:
        self._root = models_root.resolve() if models_root is not None else None
        self._spawn = spawn
        self._grant_read = grant_read
        self._lock = threading.RLock()
        self._models: dict[str, Any] = {}
        self._size = 0
        self._want_gpu = False
        self._gpu_failed = False
        self._gpu_name: str | None = None
        self._process: _Process | None = None
        self._last_use = time.monotonic()
        self._started_at = 0.0
        self._stop = threading.Event()
        threading.Thread(target=self._idle_watch, name="ia-inactividad", daemon=True).start()

    # ------------------------------------------------------------ configuracion

    def configure(self, raw: Any) -> dict[str, Any]:
        """Lo que manda la aplicacion (``ai.configure``): modelos instalados y si se usa la grafica."""
        params = raw if isinstance(raw, dict) else {}
        gpu = params.get("gpu") is True
        models_in = params.get("models")
        if not isinstance(models_in, dict) or len(models_in) > len(_NAMES):
            raise invalid()
        models: dict[str, Any] = {}
        size = 0
        for kind, entry in models_in.items():
            if kind not in _NAMES or not isinstance(entry, dict) or not isinstance(entry.get("files"), dict):
                raise invalid()
            files = entry["files"]
            if set(files) != _NAMES[kind]:
                raise invalid()
            for rel in files.values():
                self._check(rel)
            declared = entry.get("size")
            if not isinstance(declared, int) or declared <= 0:
                raise invalid()
            size += declared
            out: dict[str, Any] = {"files": dict(files)}
            ctx = entry.get("ctx")
            if kind == "llm" and isinstance(ctx, int) and 2048 <= ctx <= 32768:
                out["ctx"] = ctx
            models[kind] = out
        ok = cpu_ok()
        gpu_name: str | None = None
        if gpu and models.get("llm") and ok:
            from .aiworker import pick_gpu

            found = pick_gpu()
            gpu_name = found.name if found else None
        with self._lock:
            self._close_process()
            self._models = models if ok else {}
            self._size = size
            self._want_gpu = gpu
            self._gpu_failed = False
            self._gpu_name = gpu_name
        if models and self._root is not None:
            self._grant_read(self._root)
        return {"gpu": gpu_name, "cpuOk": ok, "kinds": sorted(models)}

    def _check(self, rel: Any) -> Path:
        if self._root is None or not isinstance(rel, str) or not _REL.match(rel) or ".." in rel:
            raise invalid()
        path = (self._root / rel).resolve()
        if self._root not in path.parents or not path.is_file():
            raise invalid()
        return path

    # ----------------------------------------------------------------- proceso

    def _ensure(self) -> _Process:
        process = self._process
        if process is not None and process.alive:
            return process
        if not self._models or self._root is None:
            raise unavailable()
        for gpu in ([True, False] if self._want_gpu and not self._gpu_failed else [False]):
            args = ["--ai-worker", str(self._root)] + (["--gpu"] if gpu else [])
            child, job = self._spawn(args, self._size + MARGIN)
            process = _Process(child, job, gpu)
            try:
                reply, _payload = process.request({"op": "configure", "models": self._models}, timeout=START_SECONDS)
                if reply.get("op") != "ok":
                    raise ConnectionError
                if not gpu:
                    self._gpu_name = None
                self._process = process
                self._started_at = time.monotonic()
                return process
            except (TimeoutError, ConnectionError, OSError):
                process.close()
                if gpu:
                    log.warning("el proceso de IA no arranca con la grafica; se usa el procesador")
                    self._gpu_failed = True
                    continue
        raise EngineError("ai_failed", _("No se pudo iniciar la IA."))

    def _close_process(self) -> None:
        process, self._process = self._process, None
        if process is not None:
            process.close()

    def _idle_watch(self) -> None:
        while not self._stop.wait(30):
            with self._lock:
                process = self._process
                if process is not None and not process.generating and time.monotonic() - self._last_use > IDLE_SECONDS:
                    log.info("ia: sin uso; se cierra el proceso para liberar memoria")
                    self._close_process()

    def close(self) -> None:
        self._stop.set()
        with self._lock:
            self._close_process()

    # --------------------------------------------- peticiones del motor aislado

    def run(self, op: str, params: dict[str, Any], payload: bytes) -> tuple[dict[str, Any], bytes]:
        with self._lock:
            self._last_use = time.monotonic()
            try:
                if op in ("embed", "ner"):
                    texts = params.get("texts")
                    if not isinstance(texts, list) or not 0 < len(texts) <= 64 or not all(isinstance(t, str) and len(t) <= 8_000 for t in texts):
                        raise invalid()
                    return self._call({"op": op, "texts": texts})
                if op == "unwarp":
                    w, h = params.get("w"), params.get("h")
                    if not (isinstance(w, int) and isinstance(h, int) and 16 <= w <= 4096 and 16 <= h <= 4096 and len(payload) == w * h * 3):
                        raise invalid()
                    return self._call({"op": "unwarp", "w": w, "h": h}, payload)
                if op == "device":
                    # Sin grafica lo que mas tarda es leer el prompt: el motor le pasa menos texto.
                    return {"gpu": self._want_gpu and not self._gpu_failed and self._gpu_name is not None}, b""
                if op == "start":
                    return self._start(params), b""
                if op == "poll":
                    return self._poll(params), b""
                if op == "stop":
                    process = self._process
                    if process is not None and process.generating:
                        process.writer.write({"op": "cancel"})
                    return {"ok": True}, b""
            except (TimeoutError, ConnectionError, OSError):
                # Colgado o cerrado: se cierra y la proxima peticion lo vuelve a lanzar.
                if self._process is not None and self._process.gpu:
                    self._gpu_failed = True
                self._close_process()
                raise EngineError("ai_failed", _("La IA ha dejado de responder. Inténtalo de nuevo.")) from None
        raise denied(_("Operación no permitida."))

    def _call(self, header: dict[str, Any], payload: bytes = b"") -> tuple[dict[str, Any], bytes]:
        process = self._ensure()
        if process.generating:
            raise EngineError("ai_busy", _("La IA está ocupada con otra respuesta."))
        reply, data = process.request(header, payload)
        if reply.get("op") == "error":
            if reply.get("error") == "missing":
                raise unavailable()
            raise EngineError("ai_failed", _("La IA no ha podido hacerlo."))
        return {k: v for k, v in reply.items() if k != "op"}, data

    def _start(self, params: dict[str, Any]) -> dict[str, Any]:
        messages = params.get("messages")
        if not isinstance(messages, list) or not 0 < len(messages) <= 16:
            raise invalid()
        total = 0
        for m in messages:
            if not isinstance(m, dict) or m.get("role") not in ("system", "user", "assistant") or not isinstance(m.get("content"), str):
                raise invalid()
            total += len(m["content"])
        if total > 60_000:
            raise invalid()
        max_tokens = params.get("maxTokens")
        header = {
            "op": "generate",
            "messages": [{"role": m["role"], "content": m["content"]} for m in messages],
            "maxTokens": max_tokens if isinstance(max_tokens, int) and 1 <= max_tokens <= 2048 else 512,
            "temperature": 0.2,
            "watermark": params.get("watermark") is True,
        }
        reuse = params.get("reuse")
        if reuse is not None:
            if not isinstance(reuse, str) or not _REUSE.fullmatch(reuse):
                raise invalid()
            header["reuse"] = reuse
        process = self._ensure()
        if process.generating:
            raise EngineError("ai_busy", _("La IA está ocupada con otra respuesta."))
        with process.changed:
            process.text = []
            process.done = None
            process.generating = True
        process.writer.write(header)
        self._started_at = time.monotonic()
        return {"ok": True}

    def _poll(self, params: dict[str, Any]) -> dict[str, Any]:
        process = self._process
        if process is None:
            return {"text": "", "done": True, "reason": "error"}
        wait = params.get("wait")
        wait = min(float(wait), MAX_POLL_WAIT) if isinstance(wait, (int, float)) and wait > 0 else MAX_POLL_WAIT
        with process.changed:
            if not process.text and process.done is None and process.alive:
                process.changed.wait(wait)
            text = "".join(process.text)
            process.text = []
            done, reason = process.done is not None, process.done or ""
        if not done and time.monotonic() - self._started_at > GENERATION_SECONDS:
            process.writer.write({"op": "cancel"})
            log.warning("ia: la respuesta ha superado el tiempo maximo")
        return {"text": text, "done": done, "reason": reason}
