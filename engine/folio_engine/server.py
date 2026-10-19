"""Bucle de servicio del motor: recibe peticiones, las valida y las ejecuta.

Un hilo lee tramas de la entrada estandar y las encola; el hilo principal las
ejecuta de una en una (MuPDF no admite uso concurrente). Las cancelaciones se
atienden en el hilo lector para que surtan efecto aunque haya trabajo en curso.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Callable

from pydantic import ValidationError

from . import recovery
from .errors import Cancelled, EngineError, not_found
from .host import DirectHost
from .i18n import _
from .models import NoParams, Params
from .protocol import FrameWriter, ProtocolError, read_frame
from .session import DocSession

log = logging.getLogger("folio.engine")


@dataclass(frozen=True)
class Method:
    name: str
    model: type[Params]
    fn: Callable[["Ctx", Any], Any]


_METHODS: dict[str, Method] = {}


def method(name: str, model: type[Params] = NoParams) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Registra una operacion. Solo lo registrado aqui puede invocarse (lista blanca)."""

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        if name in _METHODS:
            raise RuntimeError(f"operacion duplicada: {name}")
        _METHODS[name] = Method(name, model, fn)
        return fn

    return decorator


class Reply:
    """Resultado de una operacion acompanado de una carga binaria."""

    __slots__ = ("result", "payload", "keep")

    def __init__(self, result: dict[str, Any], payload: bytes | bytearray | memoryview, keep: Any = None) -> None:
        self.result = result
        self.payload = payload
        # ``keep`` mantiene vivo el objeto dueno de la memoria de ``payload``
        # (un memoryview sobre un Pixmap no impide que este se libere).
        self.keep = keep


class Engine:
    """Estado global del motor: documentos abiertos, carpetas de trabajo y acceso al exterior."""

    def __init__(self, work_root: Path, resources: Path | None, host: Any = None) -> None:
        self.work_root = work_root
        self.resources = resources
        # Archivos del usuario, clave de firma y red (ver host.py). Por defecto,
        # acceso directo; el motor aislado lo sustituye por el del intermediario.
        self.host = host if host is not None else DirectHost()
        self.sessions: dict[str, DocSession] = {}
        # Digitalizaciones en curso (ops/scan.py): fotos a la espera de ser PDF.
        self.scans: dict[str, Any] = {}

    def session(self, doc_id: str) -> DocSession:
        try:
            return self.sessions[doc_id]
        except KeyError:
            raise not_found(_("El documento ya no está abierto.")) from None

    def close_all(self, *, keep_unsaved: bool = False) -> None:
        if keep_unsaved:
            self.checkpoint()  # que la ficha refleje el ultimo estado antes de irse
        for session in list(self.sessions.values()):
            session.close(keep_unsaved=keep_unsaved)
        self.sessions.clear()
        # Las fotos de una digitalizacion sin terminar no se guardan: se borran.
        for job in list(self.scans.values()):
            job.discard()
        self.scans.clear()

    def checkpoint(self) -> None:
        """Pone al dia la ficha de recuperacion de los documentos que han cambiado (recovery.py)."""
        for session in list(self.sessions.values()):
            if session.noted_token != session._token and session.revisions:
                recovery.note(session)
                session.noted_token = session._token


class Ctx:
    """Contexto de una peticion en curso."""

    def __init__(self, server: "Server", req_id: int, payload: bytes) -> None:
        self._server = server
        self.id = req_id
        self.binary = payload
        self.engine = server.engine
        self._last_progress = 0.0

    def progress(self, done: float, total: float = 1.0, message: str = "", text: str | None = None) -> None:
        """Avisa del avance de una operacion larga y comprueba si se ha cancelado.

        ``text``: lo que la IA lleva escrito, para que la respuesta aparezca
        poco a poco (con texto, el aviso no se salta aunque llegue muy seguido).
        """
        self.check_cancel()
        now = time.monotonic()
        if text is None and now - self._last_progress < 0.08 and done < total:
            return
        self._last_progress = now
        data: dict[str, Any] = {"done": done, "total": total, "message": message}
        if text is not None:
            data["text"] = text[-32_000:]
        self._server.emit("progress", data, req_id=self.id)

    def check_cancel(self) -> None:
        if self._server.is_cancelled(self.id):
            raise Cancelled()


class _Pending:
    """Peticion al intermediario que espera respuesta."""

    __slots__ = ("done", "result", "payload", "error")

    def __init__(self) -> None:
        self.done = threading.Event()
        self.result: dict[str, Any] = {}
        self.payload = b""
        self.error: EngineError | None = None


class Server:
    def __init__(self, rfile: BinaryIO, wfile: BinaryIO, engine: Engine) -> None:
        self._rfile = rfile
        self._writer = FrameWriter(wfile)
        self.engine = engine
        self._queue: queue.Queue[tuple[dict[str, Any], bytes] | None] = queue.Queue()
        self._cancelled: set[int] = set()
        self._lock = threading.Lock()
        # Peticiones del motor aislado al intermediario (ver call_host).
        self._calls: dict[int, _Pending] = {}
        self._call_seq = 0
        self._current: int | None = None
        self._closed = False

    # ------------------------------------------------------- hacia el intermediario

    def call_host(self, name: str, params: dict[str, Any], payload: bytes = b"") -> tuple[dict[str, Any], bytes]:
        """Pide algo al proceso intermediario y espera su respuesta (motor aislado).

        Puede llamarse desde varios hilos a la vez: cada peticion lleva su
        numero y la respuesta se entrega a quien la hizo. Va acompanada del
        numero de la operacion en curso, que es lo que el intermediario usa para
        decidir si la permite.
        """
        pending = _Pending()
        with self._lock:
            if self._closed:
                raise EngineError("engine_down", _("El motor de documentos se ha detenido."))
            self._call_seq += 1
            cid = self._call_seq
            self._calls[cid] = pending
        try:
            self._writer.write({"call": name, "cid": cid, "rid": self._current, "params": params}, payload)
            pending.done.wait()
        finally:
            with self._lock:
                self._calls.pop(cid, None)
        if pending.error is not None:
            raise pending.error
        return pending.result, pending.payload

    def _deliver(self, header: dict[str, Any], payload: bytes) -> None:
        cid = header.get("cid")
        with self._lock:
            pending = self._calls.get(cid) if isinstance(cid, int) else None
        if pending is None:
            log.error("respuesta del intermediario sin peticion; se ignora")
            return
        if header.get("ok") is True:
            result = header.get("result")
            pending.result = result if isinstance(result, dict) else {}
            pending.payload = payload
        else:
            error = header.get("error") if isinstance(header.get("error"), dict) else {}
            code = error.get("code") if isinstance(error.get("code"), str) else "internal"
            message = error.get("message") if isinstance(error.get("message"), str) else _("Error del proceso intermediario.")
            raw_detail = error.get("detail")
            # Solo datos simples: el detalle acompana al error hasta la interfaz.
            detail = (
                {k: v for k, v in raw_detail.items() if isinstance(k, str) and isinstance(v, (str, int, bool))}
                if isinstance(raw_detail, dict)
                else {}
            )
            pending.error = Cancelled() if code == "cancelled" else EngineError(code[:60], message[:500], **detail)
        pending.done.set()

    def _fail_pending(self) -> None:
        with self._lock:
            self._closed = True
            waiting = list(self._calls.values())
        for pending in waiting:
            pending.error = EngineError("engine_down", _("El motor de documentos se ha detenido."))
            pending.done.set()

    # ------------------------------------------------------------------ eventos

    def emit(self, event: str, data: dict[str, Any], *, req_id: int | None = None) -> None:
        header: dict[str, Any] = {"event": event, "data": data}
        if req_id is not None:
            header["id"] = req_id
        self._writer.write(header)

    def is_cancelled(self, req_id: int) -> bool:
        with self._lock:
            return req_id in self._cancelled

    # ------------------------------------------------------------------- lectura

    def _read_loop(self) -> None:
        try:
            while True:
                frame = read_frame(self._rfile)
                if frame is None:
                    break
                header, payload = frame
                if header.get("method") == "$cancel":
                    target = (header.get("params") or {}).get("id")
                    if isinstance(target, int):
                        with self._lock:
                            self._cancelled.add(target)
                    continue
                if "cid" in header and "method" not in header:
                    # Respuesta del intermediario a una peticion de este motor.
                    self._deliver(header, payload)
                    continue
                self._queue.put((header, payload))
        except ProtocolError:
            # Con el flujo desincronizado no se puede seguir: se termina.
            log.error("trama no valida en la entrada; el motor se detiene")
        except Exception:  # noqa: BLE001
            log.exception("error en el hilo lector")
        finally:
            self._fail_pending()
            self._queue.put(None)

    # ----------------------------------------------------------------- servicio

    def serve(self, hello: dict[str, Any]) -> None:
        reader = threading.Thread(target=self._read_loop, name="lector", daemon=True)
        reader.start()
        self.emit("ready", hello)
        while True:
            item = self._queue.get()
            if item is None:
                break
            self._handle(*item)

    def _handle(self, header: dict[str, Any], payload: bytes) -> None:
        req_id = header.get("id")
        name = header.get("method")
        if not isinstance(req_id, int) or isinstance(req_id, bool) or not isinstance(name, str):
            log.error("peticion sin id o metodo validos; se ignora")
            return
        started = time.perf_counter()
        self._current = req_id
        try:
            if self.is_cancelled(req_id):
                raise Cancelled()
            entry = _METHODS.get(name)
            if entry is None:
                raise EngineError("unknown_method", _("Operación desconocida."))
            params = entry.model.model_validate(header.get("params") or {})
            outcome = entry.fn(Ctx(self, req_id, payload), params)
            if isinstance(outcome, Reply):
                self._writer.write({"id": req_id, "ok": True, "result": outcome.result}, outcome.payload)
            else:
                self._writer.write({"id": req_id, "ok": True, "result": outcome if outcome is not None else {}})
        except EngineError as exc:
            self._writer.write({"id": req_id, "ok": False, "error": exc.to_json()})
        except ValidationError as exc:
            # Solo se devuelven el campo y el tipo de fallo, nunca el valor
            # recibido: podria ser una contrasena o texto del documento.
            fields = [
                {"field": ".".join(str(p) for p in err["loc"]), "type": err["type"]}
                for err in exc.errors(include_input=False, include_url=False, include_context=False)
            ]
            self._writer.write(
                {
                    "id": req_id,
                    "ok": False,
                    "error": {"code": "invalid_params", "message": _("Parámetros no válidos."), "detail": {"fields": fields}},
                }
            )
        except MemoryError:
            log.error("memoria insuficiente en %s", name)
            self._writer.write(
                {
                    "id": req_id,
                    "ok": False,
                    "error": {"code": "out_of_memory", "message": _("No hay memoria suficiente para completar la operación.")},
                }
            )
        except Exception as exc:  # noqa: BLE001
            # Hacia fuera, mensaje generico (A10:2025). Al registro van el tipo
            # de error y por donde paso (archivos y lineas del propio motor),
            # pero no su mensaje: suele citar la ruta del archivo del usuario o
            # fragmentos del documento (A09:2025).
            frames = "".join(traceback.format_tb(exc.__traceback__)[-8:])
            log.error("error interno en %s: %s\n%s", name, type(exc).__name__, frames.rstrip())
            self._writer.write(
                {
                    "id": req_id,
                    "ok": False,
                    "error": {"code": "internal", "message": _("Se produjo un error inesperado al procesar el documento.")},
                }
            )
        finally:
            self._current = None
            with self._lock:
                self._cancelled.discard(req_id)
            try:
                # Despues de responder: lo ultimo confirmado queda recuperable
                # si la aplicacion se cierra de golpe.
                self.engine.checkpoint()
            except Exception as exc:  # noqa: BLE001 - nunca debe tumbar una operacion
                log.warning("no se pudo actualizar la recuperacion (%s)", type(exc).__name__)
            elapsed = (time.perf_counter() - started) * 1000
            if elapsed > 250:
                # Se registra el nombre de la operacion y su duracion, nunca sus
                # parametros (pueden llevar contrasenas o contenido, A09:2025).
                log.info("%s tardo %.0f ms", name, elapsed)
