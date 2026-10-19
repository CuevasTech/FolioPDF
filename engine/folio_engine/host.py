"""Lo que el motor necesita de fuera: archivos del usuario, firma, red, confianza, procesos auxiliares e IA.

El motor funciona de dos formas y las operaciones no distinguen entre ellas:

* ``DirectHost``: todo en el mismo proceso, con acceso directo (las pruebas y el
  modo ``--direct``).
* ``BrokeredHost``: el motor corre aislado (``--worker``, ver ``winsandbox``) y
  no puede abrir nada del usuario. Cada acceso se convierte en una peticion al
  proceso intermediario (``broker.py``), que comprueba que corresponda a lo que
  el usuario ha pedido en la operacion en curso antes de hacerlo (A01:2025,
  minimo privilegio).

Los archivos viajan como bytes por la canalizacion: el intermediario nunca
escribe ni lee en la carpeta del motor aislado, asi que este no puede
enganarle con enlaces o uniones de directorio que apunten a otro sitio.
"""

from __future__ import annotations

import base64
import logging
import os
import secrets
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import paths
from .errors import EngineError, invalid, io_error
from .i18n import N_, _

log = logging.getLogger("folio.engine.host")

CHUNK = 8 * 1024 * 1024  # trozos en que viajan los archivos por la canalizacion
MAX_NEW_NAMES = 9_999
WORD_TIMEOUT = 15 * 60  # pasado este tiempo la conversion a Word se da por colgada
SCAN_TIMEOUT = 5 * 60  # y la busqueda del papel en las fotos de una digitalizacion

_PERMISSION = N_(
    "No se pudo escribir el archivo. Puede estar abierto en otro programa o la carpeta ser de solo lectura."
)


@dataclass(frozen=True)
class Fetched:
    """Archivo del usuario ya copiado a la carpeta de trabajo."""

    path: str  # ruta del usuario, solo para mostrarla y para compararla
    name: str
    size: int
    stat: tuple[int, int]  # tamano y fecha de modificacion (para avisar si cambia en el disco)


@dataclass(frozen=True, repr=False)
class TrustMaterial:
    """Certificados de las autoridades en las que confia Windows (DER)."""

    roots: tuple[bytes, ...]
    intermediates: tuple[bytes, ...]

    def __repr__(self) -> str:
        return "<TrustMaterial>"


def stat_pair(path: Path | str) -> tuple[int, int]:
    info = os.stat(path)
    return (info.st_size, info.st_mtime_ns)


def _copy_limited(source: Any, target: Any, max_bytes: int | None) -> int:
    total = 0
    while chunk := source.read(1024 * 1024):
        total += len(chunk)
        if max_bytes is not None and total > max_bytes:  # pudo crecer tras comprobarlo
            raise io_error(_("El archivo es demasiado grande."), tooLarge=True)
        target.write(chunk)
    return total


# ============================================================ acceso directo


class DirectHost:
    """Acceso directo: el motor y los archivos del usuario en el mismo proceso."""

    sandboxed = False

    # ---------------------------------------------------------------- archivos

    def fetch(self, raw: str, target: Path, *, max_bytes: int | None = None) -> Fetched:
        """Copia el archivo del usuario ``raw`` (ruta validada) a ``target``, que no debe existir."""
        path = paths.input_file(raw, max_bytes=max_bytes)
        out = _create(target)
        try:
            with out, open(path, "rb") as source:
                size = _copy_limited(source, out, max_bytes)
            stamp = stat_pair(path)
        except BaseException as exc:
            _discard(target)
            if isinstance(exc, OSError):
                raise io_error(_("No se pudo leer el archivo.")) from exc
            raise
        return Fetched(str(path), path.name, size, stamp)

    def check_output(self, raw: str, suffixes: tuple[str, ...]) -> str:
        return str(paths.output_file(raw, suffixes=suffixes))

    def check_output_dir(self, raw: str) -> str:
        return str(paths.output_dir(raw))

    def install(self, local: Path, dest: str) -> tuple[int, int]:
        """Pone ``local`` en el lugar de ``dest`` de una sola vez; devuelve el estado del resultado.

        Se copia a un temporal de nombre impredecible en la carpeta de destino,
        creado en modo exclusivo (nunca se escribe a traves de algo que ya
        estuviera ahi), y se sustituye conservando los permisos del original.
        """
        target = Path(dest)
        tmp = target.with_name(f"~{secrets.token_hex(6)}.folio.tmp")
        try:
            with open(local, "rb") as source, open(tmp, "xb") as out:
                shutil.copyfileobj(source, out, 1024 * 1024)
            paths.replace_file(tmp, target)
            return stat_pair(target)
        except PermissionError as exc:
            raise io_error(_(_PERMISSION)) from exc
        except OSError as exc:
            raise io_error(_("No se pudo guardar el archivo.")) from exc
        finally:
            _discard(tmp)

    def create_new(self, folder: str, stem: str, suffix: str, local: Path) -> str:
        """Guarda ``local`` como archivo nuevo en ``folder`` sin sobrescribir nunca nada."""
        target = claim_name(Path(folder), stem, suffix)
        tmp = target.with_name(f"~{secrets.token_hex(6)}.folio.tmp")
        try:
            with open(local, "rb") as source, open(tmp, "xb") as out:
                shutil.copyfileobj(source, out, 1024 * 1024)
            paths.replace_file(tmp, target)  # ocupa el nombre reservado de una vez
        except BaseException as exc:
            _discard(tmp)
            _discard(target)
            if isinstance(exc, OSError):
                raise io_error(_("No se pudo escribir en la carpeta de destino.")) from exc
            raise
        return str(target)

    def discard(self, path: str) -> None:
        _discard(Path(path))

    def stat(self, path: str) -> tuple[int, int] | None:
        try:
            return stat_pair(path)
        except OSError:
            return None

    def same_file(self, a: str, b: str) -> bool:
        return same_path(a, b)

    # ----------------------------------------------------------- firma y red

    def timestamper(self, url: str) -> Any:
        from .sign import net

        return net.GuardedTimeStamper(url)

    def revocation_backend(self) -> Any:
        from .sign import net

        return net.revocation_backend()

    def trust_material(self) -> TrustMaterial:
        from .sign import winstore

        material = winstore.trust_material()
        return TrustMaterial(tuple(material.roots), tuple(material.intermediates))

    def sign_digest(self, digest: bytes, hash_name: str) -> bytes:
        raise invalid(_("Firma no preparada."))  # en modo directo firma el propio firmante

    def convert_to_word(self, work_root: Path, source: Path, dest: Path, password: str | None, check_cancel: Callable[[], None]) -> int:
        """Convierte ``source`` en ``dest`` (los dos en la carpeta de trabajo) en un proceso aparte."""
        command, folder = word_command()
        try:
            # Sin interprete de ordenes y con la lista de argumentos cerrada: las
            # dos rutas las genera el motor (A05:2025). La salida se descarta:
            # sus mensajes pueden citar contenido del documento.
            process = subprocess.Popen(  # noqa: S603
                [*command, str(source), str(dest)],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                cwd=folder, shell=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            log.warning("no se pudo lanzar la conversion a Word: %s", type(exc).__name__)
            raise EngineError("conversion_failed", _("No se pudo convertir este documento a Word.")) from None
        try:
            try:
                # La contrasena viaja por la entrada estandar, nunca por la
                # linea de ordenes (visible para otros procesos del equipo).
                assert process.stdin is not None
                process.stdin.write((password or "").encode("utf-8"))
                process.stdin.close()
            except OSError:
                pass  # el proceso ya termino: su codigo de salida dira por que
            deadline = time.monotonic() + WORD_TIMEOUT
            while True:
                try:
                    return process.wait(timeout=0.25)
                except subprocess.TimeoutExpired:
                    check_cancel()  # al cancelar se mata el proceso en el ``finally``
                    if time.monotonic() > deadline:
                        log.warning("la conversion a Word ha superado el tiempo maximo")
                        raise EngineError("conversion_failed", _("La conversión a Word tarda demasiado y se ha detenido.")) from None
        finally:
            if process.poll() is None:
                process.kill()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    log.warning("el proceso de conversion a Word no ha terminado tras matarlo")


    def detect_documents(self, work_root: Path, request: Path, result: Path, check_cancel: Callable[[], None]) -> int:
        """Busca el papel en las fotos que lista ``request`` (ver ``scanworker.py``), en un proceso aparte."""
        command, folder = helper_command("--scan-worker")
        try:
            # Como la conversion a Word: sin interprete de ordenes, con la lista
            # de argumentos cerrada y sin entrada (A05:2025).
            process = subprocess.Popen(  # noqa: S603
                [*command, str(request), str(result)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                cwd=folder, shell=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            log.warning("no se pudo lanzar la busqueda del papel: %s", type(exc).__name__)
            return 4
        try:
            deadline = time.monotonic() + SCAN_TIMEOUT
            while True:
                try:
                    return process.wait(timeout=0.25)
                except subprocess.TimeoutExpired:
                    check_cancel()  # al cancelar se mata el proceso en el ``finally``
                    if time.monotonic() > deadline:
                        log.warning("la busqueda del papel ha superado el tiempo maximo")
                        return 4
        finally:
            if process.poll() is None:
                process.kill()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    log.warning("el proceso de busqueda del papel no ha terminado tras matarlo")

    def ai(self, op: str, params: dict[str, Any], payload: bytes = b"") -> tuple[dict[str, Any], bytes]:
        """Sin intermediario no hay IA (el modo directo es para diagnostico y pruebas)."""
        from .aibroker import unavailable

        raise unavailable()



def helper_command(flag: str) -> tuple[list[str], str]:
    """Orden y carpeta de un proceso auxiliar (``--word-worker`` o ``--scan-worker``)."""
    if getattr(sys, "frozen", False):
        # Motor empaquetado: el mismo ejecutable atiende el modo auxiliar.
        return [sys.executable, flag], str(Path(sys.executable).resolve().parent)
    # -E y -s: el interprete ignora las variables PYTHON* y la carpeta de
    # paquetes del usuario, igual que cuando la aplicacion lanza el motor.
    return [sys.executable, "-E", "-s", "-m", "folio_engine", flag], str(Path(__file__).resolve().parents[1])


def word_command() -> tuple[list[str], str]:
    """Orden y carpeta del proceso que convierte a Word (ver ``wordworker.py``)."""
    return helper_command("--word-worker")


def claim_name(folder: Path, stem: str, suffix: str) -> Path:
    """Reserva en ``folder`` un nombre libre: ``stem.ext``, ``stem (2).ext``...

    El archivo se crea en modo exclusivo, asi que comprobar que no existe y
    reservarlo son una sola operacion (sin carrera entre las dos) y nunca se
    sigue un enlace que ya estuviera ahi. El nombre no puede sacar el archivo
    de la carpeta elegida (A01:2025, manipulacion de rutas).
    """
    clean = paths.safe_filename(stem, _("documento"))[:150].rstrip(" .") or _("documento")
    if suffix not in (".pdf", ".png", ".jpg", ".txt", ".docx"):
        raise invalid(_("Extensión de archivo no permitida."))
    for attempt in range(1, MAX_NEW_NAMES + 1):
        candidate = folder / (f"{clean}{suffix}" if attempt == 1 else f"{clean} ({attempt}){suffix}")
        if candidate.parent != folder:
            raise invalid(_("Nombre de archivo no válido."))
        try:
            handle = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        except FileExistsError:
            continue
        except OSError as exc:
            if candidate.exists():  # en Windows, una carpeta con ese nombre da "acceso denegado"
                continue
            raise io_error(_("No se pudo crear el archivo en la carpeta de destino.")) from exc
        os.close(handle)
        return candidate
    raise io_error(_("Ya hay demasiados archivos con ese nombre en la carpeta."))


def same_path(a: str | Path, b: str | Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _discard(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        log.warning("no se pudo borrar un archivo temporal")  # sin la ruta: puede llevar datos personales (A09:2025)


def _create(target: Path) -> Any:
    """Abre ``target`` como archivo nuevo (modo exclusivo) en la carpeta de trabajo."""
    try:
        return open(target, "xb")
    except OSError as exc:
        raise io_error(_("No se pudo preparar la copia de trabajo.")) from exc


# ===================================================== a traves del intermediario

Call = Callable[[str, dict[str, Any], bytes], tuple[dict[str, Any], bytes]]


def _text(value: Any, limit: int = 32_000) -> str:
    if not isinstance(value, str) or len(value) > limit:
        raise EngineError("internal", _("Respuesta no válida del proceso intermediario."))
    return value


def _stat(value: Any) -> tuple[int, int]:
    if (
        not isinstance(value, list)
        or len(value) != 2
        or not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in value)
    ):
        raise EngineError("internal", _("Respuesta no válida del proceso intermediario."))
    return (value[0], value[1])


class BrokeredHost:
    """Acceso a traves del proceso intermediario (el motor esta aislado).

    ``call(nombre, parametros, carga)`` envia la peticion y espera la respuesta;
    si el intermediario la rechaza, lanza el ``EngineError`` que este indique.
    """

    sandboxed = True

    def __init__(self, call: Call) -> None:
        self._call = call

    # ---------------------------------------------------------------- archivos

    def fetch(self, raw: str, target: Path, *, max_bytes: int | None = None) -> Fetched:
        opened, _payload = self._call("fs.open", {"path": raw, "maxBytes": max_bytes}, b"")
        handle = _text(opened.get("handle"), 64)
        received = 0
        try:
            out = _create(target)
            try:
                with out:
                    while True:
                        info, chunk = self._call("fs.read", {"handle": handle}, b"")
                        received += len(chunk)
                        if max_bytes is not None and received > max_bytes:
                            raise io_error(_("El archivo es demasiado grande."), tooLarge=True)
                        out.write(chunk)
                        if info.get("eof") is True:
                            break
            except BaseException:
                _discard(target)
                raise
        finally:
            try:
                self._call("fs.close", {"handle": handle}, b"")
            except EngineError:
                pass
        return Fetched(_text(opened.get("path")), _text(opened.get("name"), 1024), received, _stat(opened.get("stat")))

    def check_output(self, raw: str, suffixes: tuple[str, ...]) -> str:
        result, _ = self._call("fs.checkOutput", {"path": raw, "suffixes": list(suffixes)}, b"")
        return _text(result.get("path"))

    def check_output_dir(self, raw: str) -> str:
        result, _ = self._call("fs.checkDir", {"path": raw}, b"")
        return _text(result.get("path"))

    def _upload(self, opening: dict[str, Any], local: Path) -> dict[str, Any]:
        started, _ = self._call("fs.begin", opening, b"")
        handle = _text(started.get("handle"), 64)
        try:
            with open(local, "rb") as source:
                while chunk := source.read(CHUNK):
                    self._call("fs.write", {"handle": handle}, chunk)
            done, _ = self._call("fs.commit", {"handle": handle}, b"")
        except BaseException:
            try:
                self._call("fs.abort", {"handle": handle}, b"")
            except EngineError:
                pass
            raise
        return done

    def install(self, local: Path, dest: str) -> tuple[int, int]:
        return _stat(self._upload({"mode": "replace", "dest": dest}, local).get("stat"))

    def create_new(self, folder: str, stem: str, suffix: str, local: Path) -> str:
        done = self._upload({"mode": "new", "folder": folder, "stem": stem, "suffix": suffix}, local)
        return _text(done.get("path"))

    def discard(self, path: str) -> None:
        try:
            self._call("fs.discard", {"path": path}, b"")
        except EngineError:
            log.warning("no se pudo borrar un archivo a medio crear")

    def stat(self, path: str) -> tuple[int, int] | None:
        result, _ = self._call("fs.stat", {"path": path}, b"")
        value = result.get("stat")
        return None if value is None else _stat(value)

    def same_file(self, a: str, b: str) -> bool:
        result, _ = self._call("fs.same", {"a": a, "b": b}, b"")
        return result.get("same") is True

    # ----------------------------------------------------------- firma y red

    def sign_digest(self, digest: bytes, hash_name: str) -> bytes:
        result, _payload = self._call("key.sign", {"digest": base64.b64encode(digest).decode("ascii"), "hash": hash_name}, b"")
        try:
            return base64.b64decode(_text(result.get("signature"), 64_000), validate=True)
        except ValueError:
            raise EngineError("internal", _("Respuesta no válida del proceso intermediario.")) from None

    def timestamper(self, url: str) -> Any:
        # La direccion la conoce el intermediario (venia en la peticion del
        # usuario): este proceso no puede elegir a donde se conecta.
        from .sign import net

        return net.BrokeredTimeStamper(self._call)

    def revocation_backend(self) -> Any:
        from .sign import net

        return net.brokered_revocation_backend(self._call)

    def trust_material(self) -> TrustMaterial:
        result, _payload = self._call("trust.material", {}, b"")

        def decode(items: Any) -> tuple[bytes, ...]:
            if not isinstance(items, list) or len(items) > 5000:
                raise EngineError("internal", _("Respuesta no válida del proceso intermediario."))
            out = []
            for item in items:
                try:
                    out.append(base64.b64decode(_text(item, 64_000), validate=True))
                except ValueError:
                    continue
            return tuple(out)

        return TrustMaterial(decode(result.get("roots")), decode(result.get("intermediates")))

    def convert_to_word(self, work_root: Path, source: Path, dest: Path, password: str | None, check_cancel: Callable[[], None]) -> int:
        # El intermediario lanza la conversion en otro proceso aislado (este no
        # puede crear procesos). Las rutas van relativas a la carpeta de trabajo.
        params = {
            "source": source.relative_to(work_root).as_posix(),
            "dest": dest.relative_to(work_root).as_posix(),
        }
        result, _payload = self._call("word.convert", params, (password or "").encode("utf-8"))
        code = result.get("code")
        if not isinstance(code, int) or isinstance(code, bool):
            raise EngineError("conversion_failed", _("No se pudo convertir este documento a Word."))
        return code

    def detect_documents(self, work_root: Path, request: Path, result: Path, check_cancel: Callable[[], None]) -> int:
        # Igual que convert_to_word: lo lanza el intermediario.
        params = {
            "request": request.relative_to(work_root).as_posix(),
            "result": result.relative_to(work_root).as_posix(),
        }
        reply, _ = self._call("scan.detect", params, b"")
        code = reply.get("code")
        return code if isinstance(code, int) and not isinstance(code, bool) else 4


    def ai(self, op: str, params: dict[str, Any], payload: bytes = b"") -> tuple[dict[str, Any], bytes]:
        """Pide algo a la IA local; lo atiende el intermediario (aibroker.py), que decide si se permite."""
        return self._call("ai.run", {**params, "op": op}, payload)
