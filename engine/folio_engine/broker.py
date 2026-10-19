"""Proceso intermediario: lanza el motor aislado y hace por el, con permiso, lo de fuera.

Arquitectura::

    aplicacion  <-- entrada/salida -->  intermediario  <-- canalizaciones -->  motor aislado
    (Electron)                          (este proceso)                         (AppContainer)

* El **motor aislado** (``--worker``) es el que lee y modifica los PDF, es
  decir, el que procesa datos no fiables con codigo en C (MuPDF). Corre en un
  AppContainer sin acceso a los archivos del usuario, sin red y sin poder crear
  procesos (ver ``winsandbox``).
* El **intermediario** nunca analiza un PDF. Reenvia las peticiones de la
  aplicacion al motor y atiende lo que este no puede hacer: leer y escribir los
  archivos que el usuario ha elegido, firmar con su certificado, conectarse al
  servidor de sello de tiempo o de revocacion y lanzar los procesos auxiliares
  (conversion a Word, busqueda del papel en las fotos, IA local).

El motor aislado se trata como no fiable (podria estar comprometido por un PDF
malicioso). Cada cosa que pide se comprueba contra lo que el usuario autorizo
en la peticion en curso (A01:2025, control de acceso; minimo privilegio):

* solo puede leer los archivos que venian en esa peticion (abrir, insertar,
  combinar...) y solo escribir en el destino elegido para esa operacion, con
  las extensiones de esa operacion;
* solo puede pedir una firma mientras dura la operacion de firma, una sola vez
  y con el resumen del tamano que corresponde;
* solo sale a la red hacia el servidor de sello de tiempo del usuario, o hacia
  direcciones publicas al validar firmas en linea, con un numero maximo de
  consultas y siempre con mensajes bien formados.

Los datos de los archivos viajan por la canalizacion: el intermediario nunca
lee ni escribe en la carpeta del motor aislado, que este controla (no se le
puede enganar con enlaces o uniones de directorio).

Si Windows no permite crear el contenedor, el motor se lanza igualmente a
traves de este intermediario pero sin AppContainer (con el objeto de trabajo y
las mitigaciones), y la aplicacion lo indica al usuario.
"""

from __future__ import annotations

import base64
import logging
import msvcrt
import os
import re
import secrets
import shutil
import sys
import sysconfig
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, Callable

from . import __version__, i18n, paths, winsandbox
from .aibroker import AI_OPS, AiService
from .errors import Cancelled, EngineError, denied, invalid, io_error
from .host import CHUNK, SCAN_TIMEOUT, WORD_TIMEOUT, claim_name, same_path, stat_pair
from .i18n import N_, _
from .protocol import FrameWriter, ProtocolError, read_frame

log = logging.getLogger("folio.broker")

READY_TIMEOUT = 60.0
MAX_UPLOAD = 4 * 1024**3  # un archivo de salida no puede pasar de aqui (no llenar el disco)
MAX_TIMESTAMP_POSTS = 4  # una firma con sello hace dos (reserva de sitio y sello real)
MAX_NET_FETCHES = 40  # consultas de revocacion por validacion en linea
MAX_LISTED_CERTIFICATES = 500
MAX_AI_CALLS = 5_000  # peticiones a la IA en una misma operacion
_PERMISSION = N_(
    "No se pudo escribir el archivo. Puede estar abierto en otro programa o la carpeta ser de solo lectura."
)

# Que puede leer o escribir el motor en cada operacion, segun sus parametros.
# Solo estas operaciones tocan archivos del usuario; para el resto, nada.
_READ_ONE = {"doc.open": "path", "pages.insertFile": "path"}
_READ_MANY = {"doc.merge": "paths", "doc.fromImages": "paths", "scan.add": "paths"}
_WRITE = {
    "doc.save": (".pdf",),
    "pages.extract": (".pdf",),
    "tools.optimize": (".pdf",),
    "tools.exportText": (".txt",),
    "tools.exportWord": (".docx",),
}
_NEW_IN = {"pages.split": (".pdf",), "tools.exportImages": (".png", ".jpg")}
_THUMBPRINT = re.compile(r"^[0-9A-Fa-f]{40}$")
# Rutas de la conversion a Word dentro de la carpeta del motor: las genera el
# motor con nombres de forma fija, y solo esas se aceptan.
_WORD_SOURCE = re.compile(r"^d[0-9a-f]{16}/word-[0-9a-f]{12}\.pdf$")
_WORD_DEST = re.compile(r"^d[0-9a-f]{16}/word-[0-9a-f]{12}\.docx$")
# Igual para la busqueda del papel en las fotos de una digitalizacion (scanworker.py).
_SCAN_REQUEST = re.compile(r"^(j[0-9a-f]{16})/detect-([0-9a-f]{12})\.json$")
_SCAN_RESULT = re.compile(r"^(j[0-9a-f]{16})/detect-([0-9a-f]{12})\.out\.json$")


def _key(raw: str) -> str:
    """Forma comparable de una ruta (mayusculas, separadores y ``..`` resueltos)."""
    return os.path.normcase(os.path.normpath(raw.replace("/", "\\")))


def _text(params: dict[str, Any], name: str, limit: int = 32_000) -> str:
    value = params.get(name)
    if not isinstance(value, str) or not value or len(value) > limit or "\x00" in value:
        raise invalid(_("Parámetros no válidos."))
    return value


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


@dataclass(repr=False)
class _Key:
    """Clave con la que se firma en la operacion en curso (nunca sale de este proceso)."""

    kind: str  # "store" o "pfx"
    info: Any
    thumbprint: str = ""
    hwnd: int | None = None
    secret: Any = None  # clave de un .pfx; se suelta al terminar la operacion
    used: bool = False

    def __repr__(self) -> str:
        return "<_Key>"


@dataclass(repr=False)
class _Upload:
    final: Path
    tmp: Path
    handle: BinaryIO
    replace: bool
    size: int = 0


@dataclass(repr=False)
class Grant:
    """Lo que el usuario autorizo en una peticion concreta."""

    rid: int
    method: str
    read: set[str] = field(default_factory=set)
    write: dict[str, tuple[str, ...]] = field(default_factory=dict)
    new_dirs: dict[str, tuple[str, ...]] = field(default_factory=dict)
    created: set[str] = field(default_factory=set)
    readers: dict[str, tuple[BinaryIO, int | None, list[int]]] = field(default_factory=dict)
    uploads: dict[str, _Upload] = field(default_factory=dict)
    key: _Key | None = None
    tsa_url: str | None = None
    tsa_left: int = 0
    net_left: int = 0
    word: bool = False
    scan: bool = False
    # Lo que esta operacion puede pedir a la IA (ver aibroker.AI_OPS) y cuantas veces lo ha hecho.
    ai_ops: frozenset[str] = frozenset()
    ai_calls: int = 0
    cancelled: bool = False
    process: Any = None  # proceso auxiliar en curso (Word o busqueda del papel)

    def __repr__(self) -> str:
        return f"<Grant {self.method}>"


class Broker:
    def __init__(
        self, rfile: BinaryIO, wfile: BinaryIO, main_workdir: Path, resources: Path | None, *, sandbox: bool, models: Path | None = None
    ) -> None:
        self._app_in = rfile
        self._app_out = FrameWriter(wfile)
        self._main_workdir = main_workdir
        self._resources = resources
        self._want_sandbox = sandbox
        self._grants: dict[int, Grant] = {}
        self._known: set[str] = set()  # rutas que el usuario ya eligio en esta ejecucion
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="intermediario")
        self._container: winsandbox.Container | None = None
        self._job: winsandbox.Job | None = None
        self._worker: winsandbox.Child | None = None
        self._to_worker: FrameWriter | None = None
        self._worker_root: Path | None = None
        self._run_lock: BinaryIO | None = None
        self._env: dict[str, str] = {}
        self.sandboxed = False
        self.issue: str | None = None
        self._stopping = False
        self._ai = AiService(models, self._spawn_ai, self._grant_ai_read)

    # ================================================================ arranque

    def _command(self) -> tuple[list[str], str, list[Path]]:
        """Orden del motor aislado, su carpeta y lo que necesita poder leer."""
        if getattr(sys, "frozen", False):
            folder = Path(sys.executable).resolve().parent
            return [sys.executable], str(folder), [folder]
        # Modo desarrollo: el interprete base del entorno virtual, sin el
        # lanzador del entorno (crearia un proceso hijo, que el aislado no puede
        # crear). Las rutas de los paquetes se le dan de forma explicita, y con
        # -S no se cargan los paquetes instalados en el interprete base: solo
        # los del entorno virtual, con las versiones fijadas del proyecto.
        engine_dir = Path(__file__).resolve().parents[1]
        base = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
        packages = sysconfig.get_paths()["purelib"]
        boot = (
            "import site, sys; "
            f"site.addsitedir({packages!r}); sys.path.insert(0, {str(engine_dir)!r}); "
            "from folio_engine.__main__ import main; sys.exit(main(sys.argv[1:]))"
        )
        return [str(base), "-E", "-s", "-S", "-c", boot], str(engine_dir), [engine_dir, base.parent]

    def _environment(self, temp: Path) -> dict[str, str]:
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        env = {
            "SystemRoot": system_root,
            "windir": os.environ.get("windir", system_root),
            "PATH": f"{system_root}\\System32;{system_root}",
            "TEMP": str(temp),
            "TMP": str(temp),
            "PYTHONUTF8": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            # Idioma de los mensajes (ver i18n.py): el ya validado, "es" o "en",
            # nunca el valor recibido tal cual.
            i18n.ENV: i18n.language(),
        }
        # Windows las necesita para preparar el entorno del contenedor (sin
        # ellas no deja crear el proceso); el motor no puede abrir esas carpetas.
        for name in ("LOCALAPPDATA", "APPDATA", "USERPROFILE", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE"):
            value = os.environ.get(name)
            if value:
                env[name] = value
        return env

    def _profile_base(self, folder: Path) -> Path:
        """Carpeta de este perfil dentro del contenedor (ver ``winsandbox.PROFILE_PREFIX``).

        El contenedor es uno por usuario de Windows: sin esta separacion, lo
        recuperable de un perfil (o de una prueba) apareceria en otro.
        """
        if not winsandbox.plain_directory(folder):
            raise winsandbox.SandboxError("carpeta")
        base = folder / winsandbox.profile_folder_name(self._main_workdir)
        try:
            base.mkdir()
        except FileExistsError:
            pass  # ya existia; _claim_run_dir comprueba que sea una carpeta de verdad
        return base

    def _claim_run_dir(self, base: Path) -> Path:
        """Carpeta de trabajo de esta ejecucion, marcada como en uso mientras dure.

        En el contenedor, el motor aislado puede cambiar lo que hay en su
        carpeta (crear uniones de directorio que apunten a los documentos del
        usuario, por ejemplo). Por eso este proceso nunca borra de forma
        recursiva alli: los restos de ejecuciones anteriores los borra el propio
        motor aislado al arrancar (ver ``winsandbox.clean_stale_runs``), con sus
        permisos, que no alcanzan a nada del usuario.
        """
        if not winsandbox.plain_directory(base):
            raise winsandbox.SandboxError("carpeta")
        run = base / secrets.token_hex(8)
        # Comprobado justo antes de crear: nada se escribe a traves de un enlace.
        run.mkdir()
        (run / "temp").mkdir()
        # "x": se crea nuevo, nunca se abre algo que ya estuviera ahi.
        lock = open(run / winsandbox.RUN_MARKER, "xb")  # noqa: SIM115 - vive lo que la ejecucion
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        self._run_lock = lock
        return run

    def _spawn(self, sandboxed: bool) -> winsandbox.Child:
        command, cwd, readable = self._command()
        if sandboxed:
            container = winsandbox.open_container()
            self._container = container
            for target in [*readable, *([self._resources] if self._resources else [])]:
                winsandbox.ensure_read_access(target, container)
            root = self._claim_run_dir(self._profile_base(container.folder))
            # Restos de ejecuciones sin aislar (o de versiones anteriores) en la
            # carpeta de la aplicacion: con el motor aislado no los recoge nadie.
            winsandbox.clean_stale_dirs(self._main_workdir)
        else:
            root = self._claim_run_dir(self._main_workdir)
        self._worker_root = root
        self._env = self._environment(root / "temp")
        self._job = winsandbox.Job(memory_limit=winsandbox.process_memory_limit())
        args = ["--worker", "--workdir", str(root)]
        if self._resources is not None:
            args += ["--resources", str(self._resources)]
        return winsandbox.spawn(
            command + args, cwd=cwd, env=self._env, container=self._container if sandboxed else None, job=self._job
        )

    def _start(self) -> dict[str, Any]:
        """Lanza el motor (aislado si se puede) y espera a que este listo."""
        attempts = [True, False] if self._want_sandbox else [False]
        for sandboxed in attempts:
            try:
                child = self._spawn(sandboxed)
            except (winsandbox.SandboxError, OSError) as exc:
                self.issue = getattr(exc, "step", type(exc).__name__)
                log.error("no se pudo lanzar el motor aislado (%s)", self.issue)
                self._discard_attempt()
                continue
            hello = _wait_ready(child)
            if hello is not None:
                self._worker = child
                self.sandboxed = sandboxed and child.in_appcontainer
                return hello
            self.issue = "arranque"
            log.error("el motor %s no arranco", "aislado" if sandboxed else "sin aislar")
            child.kill()
            child.close()
            self._discard_attempt()
        raise EngineError("engine_down", _("No se pudo iniciar el motor de documentos."))

    def _discard_attempt(self) -> None:
        if self._job is not None:
            self._job.close()
            self._job = None
        self._release_run_dir()

    def _release_run_dir(self) -> None:
        if self._run_lock is not None:
            try:
                self._run_lock.close()
            except OSError:
                pass
            self._run_lock = None
        root = self._worker_root
        self._worker_root = None
        if root is None:
            return
        # Solo lo que este proceso creo, sin recursividad (ver _claim_run_dir).
        # Si el motor dejo algo (cambios sin guardar de una salida de golpe), el
        # proximo motor lo aparta para recuperarlo y borra lo demas.
        try:
            (root / winsandbox.RUN_MARKER).unlink()
        except OSError:
            pass
        for folder in (root / "temp", root):
            try:
                folder.rmdir()
            except OSError:
                pass

    # ================================================================ servicio

    def serve(self) -> int:
        hello = self._start()
        assert self._worker is not None
        self._to_worker = FrameWriter(self._worker.stdin)
        threading.Thread(target=self._pump_worker, name="motor", daemon=True).start()
        threading.Thread(target=self._pump_stderr, name="motor-registro", daemon=True).start()
        if not self.sandboxed:
            log.warning("el motor funciona sin aislamiento (%s)", self.issue or "desactivado")
        self._app_out.write(
            {"event": "ready", "data": {**hello, "broker": __version__, "sandbox": self.sandboxed, "sandboxIssue": self.issue}}
        )
        try:
            while True:
                frame = read_frame(self._app_in)
                if frame is None:
                    break
                self._from_app(*frame)
        except ProtocolError:
            log.error("trama no valida de la aplicacion; el intermediario se detiene")
        self._shutdown()
        return 0

    def _from_app(self, header: dict[str, Any], payload: bytes) -> None:
        method = header.get("method")
        if method == "$cancel":
            target = (header.get("params") or {}).get("id") if isinstance(header.get("params"), dict) else None
            with self._lock:
                grant = self._grants.get(target) if isinstance(target, int) else None
            if grant is not None:
                grant.cancelled = True
                if grant.process is not None:
                    grant.process.kill()
            self._send_worker(header, payload)
            return
        rid = header.get("id")
        if not isinstance(rid, int) or isinstance(rid, bool) or not isinstance(method, str):
            log.error("peticion sin id o metodo validos; se ignora")
            return
        if method in ("sign.certificates", "sign.pfxInfo", "ai.configure"):
            # No necesitan ningun PDF: se atienden aqui, donde estan las claves
            # (y la configuracion de la IA, que solo envia la aplicacion).
            self._pool.submit(self._local, rid, method, header.get("params"))
            return
        try:
            grant, header = self._authorize(rid, method, header)
        except EngineError as exc:
            self._app_out.write({"id": rid, "ok": False, "error": exc.to_json()})
            return
        except Exception as exc:  # noqa: BLE001
            log.error("error al preparar %s: %s", method, type(exc).__name__)
            self._app_out.write({"id": rid, "ok": False, "error": _internal()})
            return
        with self._lock:
            self._grants[rid] = grant
        self._send_worker(header, payload)

    def _send_worker(self, header: dict[str, Any], payload: bytes = b"") -> None:
        try:
            assert self._to_worker is not None
            self._to_worker.write(header, payload)
        except (OSError, ValueError):
            pass  # el motor ha terminado: _pump_worker cierra el intermediario

    def _pump_worker(self) -> None:
        assert self._worker is not None
        try:
            while True:
                frame = read_frame(self._worker.stdout)
                if frame is None:
                    break
                header, payload = frame
                if "call" in header:
                    self._pool.submit(self._serve_call, header, payload)
                elif isinstance(header.get("event"), str) and header.get("event") != "ready":
                    self._app_out.write(header, payload)
                elif isinstance(header.get("id"), int) and isinstance(header.get("ok"), bool):
                    self._finish(header["id"])
                    self._app_out.write(header, payload)
                else:
                    log.error("mensaje no valido del motor aislado; se detiene")
                    break
        except ProtocolError:
            log.error("trama no valida del motor aislado; se detiene")
        except Exception as exc:  # noqa: BLE001
            log.error("error leyendo del motor aislado: %s", type(exc).__name__)
        if not self._stopping:
            log.error("el motor aislado ha terminado")
            self._shutdown(code=3)

    def _pump_stderr(self) -> None:
        assert self._worker is not None
        try:
            for line in self._worker.stderr:
                text = line.decode("utf-8", "replace").rstrip()
                if text:
                    sys.stderr.write(f"aislado: {text[:2000]}\n")
                    sys.stderr.flush()
        except (OSError, ValueError):
            pass

    def _finish(self, rid: int) -> None:
        with self._lock:
            grant = self._grants.pop(rid, None)
        if grant is not None:
            _close_grant(grant)

    def _shutdown(self, code: int = 0) -> None:
        if self._stopping:
            return
        self._stopping = True
        self._ai.close()
        worker = self._worker
        if worker is not None:
            try:
                worker.stdin.close()  # el motor termina solo al cerrarse su entrada
            except OSError:
                pass
            if worker.wait(3.0) is None:
                worker.kill()
                worker.wait(5.0)
        with self._lock:
            grants = list(self._grants.values())
            self._grants.clear()
        for grant in grants:
            if grant.process is not None:
                grant.process.kill()
            _close_grant(grant)
        if self._job is not None:
            self._job.close()
        if worker is not None:
            worker.close()
        self._release_run_dir()
        if self._container is not None:
            self._container.close()
        try:
            sys.stderr.flush()
        finally:
            # Salida inmediata: puede quedar un hilo esperando a la aplicacion o a
            # Windows (un dialogo de PIN, una conexion) y no hay nada que esperar.
            os._exit(code)

    # ======================================================== autorizaciones

    def _authorize(self, rid: int, method: str, header: dict[str, Any]) -> tuple[Grant, dict[str, Any]]:
        params = header.get("params")
        params = params if isinstance(params, dict) else {}
        grant = Grant(rid, method)
        if method in _READ_ONE and isinstance(params.get(_READ_ONE[method]), str):
            grant.read.add(_key(params[_READ_ONE[method]]))
        if method in _READ_MANY and isinstance(params.get(_READ_MANY[method]), list):
            for raw in params[_READ_MANY[method]][:500]:
                if isinstance(raw, str):
                    grant.read.add(_key(raw))
        if method in _WRITE and isinstance(params.get("path"), str):
            grant.write[_key(params["path"])] = _WRITE[method]
        if method in _NEW_IN and isinstance(params.get("dir"), str):
            grant.new_dirs[_key(params["dir"])] = _NEW_IN[method]
        grant.word = method == "tools.exportWord"
        grant.scan = method == "scan.add"
        grant.ai_ops = AI_OPS.get(method, frozenset())
        if method == "sign.validate" and params.get("online") is True:
            grant.net_left = MAX_NET_FETCHES
        if method == "sign.sign":
            header = {**header, "params": self._prepare_signing(grant, params)}
        return grant, header

    def _prepare_signing(self, grant: Grant, params: dict[str, Any]) -> dict[str, Any]:
        """Carga aqui la clave elegida y le pasa al motor solo los certificados publicos."""
        from .sign import certs, net, winstore

        source = params.get("source")
        if not isinstance(source, dict):
            raise invalid(_("Parámetros no válidos."))
        url = params.get("timestampUrl")
        if isinstance(url, str) and url.strip():
            grant.tsa_url = net.check_http_url(url.strip())  # se valida antes de tocar ninguna clave
            grant.tsa_left = MAX_TIMESTAMP_POSTS
        if params.get("longTerm") is True:
            # Larga duracion: revocacion de toda la cadena (firmante y sello) y
            # un sello de tiempo mas, el del documento entero.
            grant.net_left = MAX_NET_FETCHES
            grant.tsa_left = 2 * MAX_TIMESTAMP_POSTS
        kind = source.get("type")
        if kind == "store":
            thumbprint = source.get("thumbprint")
            hwnd = source.get("hwnd")
            if not isinstance(thumbprint, str) or not _THUMBPRINT.match(thumbprint):
                raise invalid(_("Parámetros no válidos."))
            if hwnd is not None and (not isinstance(hwnd, int) or isinstance(hwnd, bool) or not 1 <= hwnd <= 0xFFFFFFFF):
                raise invalid(_("Parámetros no válidos."))
            certificate = winstore.load_certificate(thumbprint.upper())
            info = certs.describe(certificate.der)
            certs.ensure_signable(info)
            chain = tuple(certificate.chain)
            untrusted = bool(certificate.chain_errors is not None and certificate.chain_errors & winstore.CHAIN_NOT_TRUSTED_MASK)
            grant.key = _Key("store", info, thumbprint=certificate.thumbprint, hwnd=hwnd)
        elif kind == "pfx":
            contents, _warnings = certs.pfx_key(_text(source, "path"), _secret(source, "password"))
            info, chain, untrusted = contents.info, contents.chain, False
            grant.key = _Key("pfx", info, secret=contents.key)
        else:
            raise invalid(_("Parámetros no válidos."))
        brokered = {
            "type": "brokered",
            "certificate": _b64(info.der),
            "chain": [_b64(der) for der in chain[: certs.MAX_CHAIN]],
            "untrustedChain": untrusted,
        }
        return {**params, "source": brokered}

    def _local(self, rid: int, method: str, raw: Any) -> None:
        from .sign import certs

        try:
            if method == "sign.certificates":
                result: dict[str, Any] = {"certificates": certs.store_certificates(MAX_LISTED_CERTIFICATES)}
            elif method == "ai.configure":
                result = self._ai.configure(raw)
            else:
                params = raw if isinstance(raw, dict) else {}
                result = certs.load_pfx(_text(params, "path"), _secret(params, "password")).info.payload(hardware=False)
            self._app_out.write({"id": rid, "ok": True, "result": result})
        except EngineError as exc:
            self._app_out.write({"id": rid, "ok": False, "error": exc.to_json()})
        except Exception as exc:  # noqa: BLE001
            log.error("error en %s: %s", method, type(exc).__name__)
            self._app_out.write({"id": rid, "ok": False, "error": _internal()})

    # ===================================================== peticiones del motor

    def _serve_call(self, header: dict[str, Any], payload: bytes) -> None:
        cid = header.get("cid")
        name = header.get("call")
        rid = header.get("rid")
        params = header.get("params")
        if not isinstance(cid, int) or isinstance(cid, bool):
            log.error("peticion del motor sin numero; se ignora")
            return
        try:
            handler = _CALLS.get(name) if isinstance(name, str) else None
            if handler is None or not isinstance(params, dict):
                raise denied(_("Operación no permitida."))
            with self._lock:
                grant = self._grants.get(rid) if isinstance(rid, int) and not isinstance(rid, bool) else None
            if grant is None:
                # Fuera de una operacion pedida por el usuario no se concede nada.
                raise denied(_("Operación no permitida."))
            if grant.cancelled:
                raise Cancelled()
            result, data = handler(self, grant, params, payload)
            self._send_worker({"cid": cid, "ok": True, "result": result}, data)
        except EngineError as exc:
            if exc.code == "permission_denied":
                log.warning("el motor aislado pidio algo no autorizado (%s)", name if isinstance(name, str) else "?")
            self._send_worker({"cid": cid, "ok": False, "error": exc.to_json()})
        except Exception as exc:  # noqa: BLE001
            log.error("error atendiendo al motor (%s): %s", name, type(exc).__name__)
            self._send_worker({"cid": cid, "ok": False, "error": _internal()})

    def _allowed_to_look(self, grant: Grant, raw: str) -> bool:
        key = _key(raw)
        with self._lock:
            known = key in self._known
        return known or key in grant.read or key in grant.write or key in grant.created

    # --- lectura de archivos

    def _fs_open(self, grant: Grant, params: dict[str, Any], _payload: bytes) -> tuple[dict[str, Any], bytes]:
        raw = _text(params, "path")
        limit = params.get("maxBytes")
        if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or limit < 0):
            raise invalid(_("Parámetros no válidos."))
        if _key(raw) not in grant.read:
            raise denied(_("Operación no permitida."))
        path = paths.input_file(raw, max_bytes=limit)
        try:
            handle = open(path, "rb")  # noqa: SIM115 - se cierra en fs.close o al terminar la operacion
        except OSError as exc:
            raise io_error(_("No se pudo leer el archivo.")) from exc
        try:
            info = os.fstat(handle.fileno())
        except OSError as exc:
            handle.close()
            raise io_error(_("No se pudo leer el archivo.")) from exc
        token = secrets.token_hex(8)
        grant.readers[token] = (handle, limit, [0])
        with self._lock:
            self._known.add(_key(str(path)))
        return {"handle": token, "path": str(path), "name": path.name, "stat": [info.st_size, info.st_mtime_ns]}, b""

    def _fs_read(self, grant: Grant, params: dict[str, Any], _payload: bytes) -> tuple[dict[str, Any], bytes]:
        entry = grant.readers.get(_text(params, "handle", 64))
        if entry is None:
            raise invalid(_("Parámetros no válidos."))
        handle, limit, total = entry
        try:
            chunk = handle.read(CHUNK)
        except OSError as exc:
            raise io_error(_("No se pudo leer el archivo.")) from exc
        total[0] += len(chunk)
        if limit is not None and total[0] > limit:  # pudo crecer tras comprobarlo
            raise io_error(_("El archivo es demasiado grande."), tooLarge=True)
        return {"eof": len(chunk) < CHUNK}, chunk

    def _fs_close(self, grant: Grant, params: dict[str, Any], _payload: bytes) -> tuple[dict[str, Any], bytes]:
        entry = grant.readers.pop(_text(params, "handle", 64), None)
        if entry is not None:
            entry[0].close()
        return {}, b""

    # --- escritura de archivos

    def _fs_check_output(self, grant: Grant, params: dict[str, Any], _payload: bytes) -> tuple[dict[str, Any], bytes]:
        raw = _text(params, "path")
        suffixes = grant.write.get(_key(raw))
        if suffixes is None:
            raise denied(_("Operación no permitida."))
        return {"path": str(paths.output_file(raw, suffixes=suffixes))}, b""

    def _fs_check_dir(self, grant: Grant, params: dict[str, Any], _payload: bytes) -> tuple[dict[str, Any], bytes]:
        raw = _text(params, "path")
        if _key(raw) not in grant.new_dirs:
            raise denied(_("Operación no permitida."))
        return {"path": str(paths.output_dir(raw))}, b""

    def _fs_begin(self, grant: Grant, params: dict[str, Any], _payload: bytes) -> tuple[dict[str, Any], bytes]:
        mode = params.get("mode")
        if mode == "replace":
            raw = _text(params, "dest")
            suffixes = grant.write.get(_key(raw))
            if suffixes is None:
                raise denied(_("Operación no permitida."))
            final = paths.output_file(raw, suffixes=suffixes)
            replace = True
        elif mode == "new":
            folder = _text(params, "folder")
            suffix = _text(params, "suffix", 8)
            allowed = grant.new_dirs.get(_key(folder))
            if allowed is None or suffix not in allowed:
                raise denied(_("Operación no permitida."))
            # El nombre lo propone el motor: se limpia otra vez aqui y se reserva
            # en modo exclusivo, siempre dentro de la carpeta elegida.
            final = claim_name(paths.output_dir(folder), _text(params, "stem", 200), suffix)
            replace = False
        else:
            raise invalid(_("Parámetros no válidos."))
        tmp = final.with_name(f"~{secrets.token_hex(6)}.folio.tmp")
        try:
            handle = open(tmp, "xb")  # noqa: SIM115 - se cierra en fs.commit o fs.abort
        except OSError as exc:
            if not replace:
                _unlink(final)
            raise io_error(_(_PERMISSION)) from exc
        token = secrets.token_hex(8)
        grant.uploads[token] = _Upload(final, tmp, handle, replace)
        return {"handle": token}, b""

    def _fs_write(self, grant: Grant, params: dict[str, Any], payload: bytes) -> tuple[dict[str, Any], bytes]:
        upload = grant.uploads.get(_text(params, "handle", 64))
        if upload is None:
            raise invalid(_("Parámetros no válidos."))
        upload.size += len(payload)
        if upload.size > MAX_UPLOAD:
            raise io_error(_("El archivo resultante es demasiado grande."))
        try:
            upload.handle.write(payload)
        except OSError as exc:
            raise io_error(_("No se pudo guardar el archivo. Comprueba que hay espacio en el disco.")) from exc
        return {}, b""

    def _fs_commit(self, grant: Grant, params: dict[str, Any], _payload: bytes) -> tuple[dict[str, Any], bytes]:
        upload = grant.uploads.pop(_text(params, "handle", 64), None)
        if upload is None:
            raise invalid(_("Parámetros no válidos."))
        try:
            upload.handle.close()
            # Conserva los permisos del archivo que se sustituye (o del nombre
            # reservado, en un archivo nuevo).
            paths.replace_file(upload.tmp, upload.final)
            stamp = stat_pair(upload.final)
        except PermissionError as exc:
            _abort(upload)
            raise io_error(_(_PERMISSION)) from exc
        except OSError as exc:
            _abort(upload)
            raise io_error(_("No se pudo guardar el archivo.")) from exc
        key = _key(str(upload.final))
        with self._lock:
            self._known.add(key)
        if not upload.replace:
            grant.created.add(key)
        return {"path": str(upload.final), "stat": list(stamp)}, b""

    def _fs_abort(self, grant: Grant, params: dict[str, Any], _payload: bytes) -> tuple[dict[str, Any], bytes]:
        upload = grant.uploads.pop(_text(params, "handle", 64), None)
        if upload is not None:
            _abort(upload)
        return {}, b""

    def _fs_discard(self, grant: Grant, params: dict[str, Any], _payload: bytes) -> tuple[dict[str, Any], bytes]:
        raw = _text(params, "path")
        key = _key(raw)
        if key not in grant.created:  # solo lo creado en esta misma operacion
            raise denied(_("Operación no permitida."))
        _unlink(Path(raw))
        grant.created.discard(key)
        return {}, b""

    def _fs_stat(self, grant: Grant, params: dict[str, Any], _payload: bytes) -> tuple[dict[str, Any], bytes]:
        raw = _text(params, "path")
        if not self._allowed_to_look(grant, raw):
            raise denied(_("Operación no permitida."))
        try:
            return {"stat": list(stat_pair(raw))}, b""
        except OSError:
            return {"stat": None}, b""

    def _fs_same(self, grant: Grant, params: dict[str, Any], _payload: bytes) -> tuple[dict[str, Any], bytes]:
        a, b = _text(params, "a"), _text(params, "b")
        if not (self._allowed_to_look(grant, a) and self._allowed_to_look(grant, b)):
            raise denied(_("Operación no permitida."))
        return {"same": same_path(a, b)}, b""

    # --- firma y red

    def _key_sign(self, grant: Grant, params: dict[str, Any], _payload: bytes) -> tuple[dict[str, Any], bytes]:
        from .sign import certs, winstore

        key = grant.key
        if key is None or key.used:
            # Una firma por operacion de firma: el motor no puede pedir mas.
            raise denied(_("Operación no permitida."))
        hash_name = _text(params, "hash", 16)
        if hash_name != key.info.digest_name:
            raise invalid(_("Algoritmo de resumen inesperado."))
        try:
            digest = base64.b64decode(_text(params, "digest", 256), validate=True)
        except ValueError:
            raise invalid(_("Parámetros no válidos.")) from None
        if len(digest) != certs.DIGEST_SIZES[hash_name]:
            raise invalid(_("Parámetros no válidos."))
        key.used = True
        if key.kind == "store":
            raw = winstore.sign_digest(key.thumbprint, digest, hash_name=hash_name, key_kind=key.info.key_kind, hwnd=key.hwnd)
            signature = raw if key.info.key_kind != "ec" else certs.ec_raw_to_der(raw)
        else:
            signature = certs.sign_with_key(key.secret, key.info, digest)
            key.secret = None  # la clave del .pfx ya no hace falta
        return {"signature": _b64(signature)}, b""

    def _tsa_post(self, grant: Grant, _params: dict[str, Any], payload: bytes) -> tuple[dict[str, Any], bytes]:
        from .sign import net

        if grant.tsa_url is None or grant.tsa_left <= 0:
            raise denied(_("Operación no permitida."))
        grant.tsa_left -= 1
        return {}, net.post_timestamp(grant.tsa_url, payload)

    def _net_fetch(self, grant: Grant, params: dict[str, Any], payload: bytes) -> tuple[dict[str, Any], bytes]:
        from .sign import net

        if grant.net_left <= 0:
            raise denied(_("Operación no permitida."))
        grant.net_left -= 1
        method = _text(params, "method", 8)
        accept = params.get("accept") if isinstance(params.get("accept"), str) else ""
        status, kind, body = net.fetch_revocation(method, _text(params, "url", net.MAX_URL_LENGTH), payload, accept)
        return {"status": status, "contentType": kind}, body

    def _trust(self, _grant: Grant, _params: dict[str, Any], _payload: bytes) -> tuple[dict[str, Any], bytes]:
        from .sign import winstore

        material = winstore.trust_material()
        return {"roots": [_b64(der) for der in material.roots], "intermediates": [_b64(der) for der in material.intermediates]}, b""

    def _word(self, grant: Grant, params: dict[str, Any], payload: bytes) -> tuple[dict[str, Any], bytes]:
        source, dest = _text(params, "source", 200), _text(params, "dest", 200)
        if not grant.word or not _WORD_SOURCE.match(source) or not _WORD_DEST.match(dest) or len(payload) > 4096:
            raise denied(_("Operación no permitida."))
        assert self._worker_root is not None
        # Las rutas solo se pasan como argumentos a otro proceso aislado: este
        # proceso no abre nada dentro de la carpeta del motor.
        command, cwd, _readable = self._command()
        job = winsandbox.Job(memory_limit=winsandbox.process_memory_limit())
        try:
            child = winsandbox.spawn(
                [*command, "--word-worker", str(self._worker_root / source), str(self._worker_root / dest)],
                cwd=cwd, env=self._env, container=self._container if self.sandboxed else None, job=job,
            )
        except BaseException:
            job.close()
            raise
        grant.process = child
        threading.Thread(target=_drain, args=(child.stdout,), daemon=True).start()
        threading.Thread(target=_drain, args=(child.stderr,), daemon=True).start()
        try:
            try:
                child.stdin.write(payload)  # la contrasena del PDF, nunca en la linea de ordenes
                child.stdin.close()
            except OSError:
                pass  # ya ha terminado: su codigo de salida dira por que
            deadline = time.monotonic() + WORD_TIMEOUT
            while True:
                code = child.wait(0.25)
                if code is not None:
                    break
                if grant.cancelled:
                    raise Cancelled()
                if time.monotonic() > deadline:
                    log.warning("la conversion a Word ha superado el tiempo maximo")
                    raise EngineError("conversion_failed", _("La conversión a Word tarda demasiado y se ha detenido."))
            if grant.cancelled:
                raise Cancelled()
            return {"code": code}, b""
        finally:
            grant.process = None
            child.kill()
            child.close()
            job.close()


    def _scan(self, grant: Grant, params: dict[str, Any], payload: bytes) -> tuple[dict[str, Any], bytes]:
        """Busca el papel en las fotos de una digitalizacion, en otro proceso aislado (como ``_word``)."""
        request, result = _text(params, "request", 200), _text(params, "result", 200)
        asked, answer = _SCAN_REQUEST.match(request), _SCAN_RESULT.match(result)
        # La peticion y el resultado, con nombres de forma fija y del mismo trabajo.
        if not grant.scan or asked is None or answer is None or asked.groups() != answer.groups() or payload:
            raise denied(_("Operación no permitida."))
        assert self._worker_root is not None
        command, cwd, _readable = self._command()
        job = winsandbox.Job(memory_limit=winsandbox.process_memory_limit())
        try:
            child = winsandbox.spawn(
                [*command, "--scan-worker", str(self._worker_root / request), str(self._worker_root / result)],
                cwd=cwd, env=self._env, container=self._container if self.sandboxed else None, job=job,
            )
        except BaseException:
            job.close()
            raise
        grant.process = child
        threading.Thread(target=_drain, args=(child.stdout,), daemon=True).start()
        threading.Thread(target=_drain, args=(child.stderr,), daemon=True).start()
        try:
            try:
                child.stdin.close()  # no necesita nada por la entrada
            except OSError:
                pass
            deadline = time.monotonic() + SCAN_TIMEOUT
            while True:
                code = child.wait(0.25)
                if code is not None:
                    break
                if grant.cancelled:
                    raise Cancelled()
                if time.monotonic() > deadline:
                    log.warning("la busqueda del papel ha superado el tiempo maximo")
                    return {"code": 4}, b""
            if grant.cancelled:
                raise Cancelled()
            return {"code": code}, b""
        finally:
            grant.process = None
            child.kill()
            child.close()
            job.close()


    # ====================================================================== IA

    def _spawn_ai(self, args: list[str], memory: int) -> tuple[winsandbox.Child, winsandbox.Job]:
        """Lanza el proceso de IA como los demas auxiliares: aislado, sin hijos y con su memoria acotada."""
        command, cwd, _readable = self._command()
        job = winsandbox.Job(memory_limit=max(memory, winsandbox.process_memory_limit()))
        try:
            child = winsandbox.spawn(
                [*command, *args], cwd=cwd, env=self._env, container=self._container if self.sandboxed else None, job=job
            )
        except BaseException:
            job.close()
            raise
        return child, job

    def _grant_ai_read(self, folder: Path) -> None:
        """El contenedor puede leer (solo leer) la carpeta de modelos."""
        if self.sandboxed and self._container is not None:
            winsandbox.ensure_read_access(folder, self._container)

    def _ai_run(self, grant: Grant, params: dict[str, Any], payload: bytes) -> tuple[dict[str, Any], bytes]:
        """Lo que el motor aislado pide a la IA, solo si corresponde a la operacion en curso."""
        op = params.get("op")
        if not isinstance(op, str) or op not in grant.ai_ops:
            raise denied(_("Operación no permitida."))
        grant.ai_calls += 1
        if grant.ai_calls > MAX_AI_CALLS:
            raise denied(_("Operación no permitida."))
        return self._ai.run(op, params, payload)


_CALLS: dict[str, Callable[[Broker, Grant, dict[str, Any], bytes], tuple[dict[str, Any], bytes]]] = {
    "fs.open": Broker._fs_open,
    "fs.read": Broker._fs_read,
    "fs.close": Broker._fs_close,
    "fs.checkOutput": Broker._fs_check_output,
    "fs.checkDir": Broker._fs_check_dir,
    "fs.begin": Broker._fs_begin,
    "fs.write": Broker._fs_write,
    "fs.commit": Broker._fs_commit,
    "fs.abort": Broker._fs_abort,
    "fs.discard": Broker._fs_discard,
    "fs.stat": Broker._fs_stat,
    "fs.same": Broker._fs_same,
    "key.sign": Broker._key_sign,
    "tsa.post": Broker._tsa_post,
    "net.fetch": Broker._net_fetch,
    "trust.material": Broker._trust,
    "word.convert": Broker._word,
    "scan.detect": Broker._scan,
    "ai.run": Broker._ai_run,
}


# ---------------------------------------------------------------------- utilidades


def _secret(params: dict[str, Any], name: str) -> str:
    value = params.get(name)
    if not isinstance(value, str) or len(value) > 1024:
        raise invalid(_("Parámetros no válidos."))
    return value


def _internal() -> dict[str, Any]:
    return {"code": "internal", "message": _("Se produjo un error inesperado al procesar el documento.")}


def _unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        log.warning("no se pudo borrar un archivo a medio crear")  # sin la ruta (A09:2025)


def _abort(upload: _Upload) -> None:
    try:
        upload.handle.close()
    except OSError:
        pass
    _unlink(upload.tmp)
    if not upload.replace:
        _unlink(upload.final)  # el nombre reservado se libera


def _close_grant(grant: Grant) -> None:
    for handle, _limit, _total in grant.readers.values():
        try:
            handle.close()
        except OSError:
            pass
    grant.readers.clear()
    for upload in grant.uploads.values():
        _abort(upload)
    grant.uploads.clear()
    if grant.key is not None:
        grant.key.secret = None  # la clave de un .pfx no sobrevive a la operacion
        grant.key = None


def _drain(stream: BinaryIO) -> None:
    try:
        while stream.read(65536):
            pass
    except (OSError, ValueError):
        pass


def _wait_ready(child: winsandbox.Child) -> dict[str, Any] | None:
    """Espera el saludo del motor; ``None`` si termina o no responde a tiempo."""
    box: dict[str, Any] = {}

    def read() -> None:
        try:
            frame = read_frame(child.stdout)
            if frame is not None and frame[0].get("event") == "ready" and isinstance(frame[0].get("data"), dict):
                box["hello"] = frame[0]["data"]
        except (ProtocolError, OSError, ValueError):
            pass

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    reader.join(READY_TIMEOUT)
    if reader.is_alive():
        child.kill()
        reader.join(5.0)
    return box.get("hello")


def run(
    rfile: BinaryIO, wfile: BinaryIO, main_workdir: Path, resources: Path | None, *, sandbox: bool = True, models: Path | None = None
) -> int:
    return Broker(rfile, wfile, main_workdir, resources, sandbox=sandbox, models=models).serve()
