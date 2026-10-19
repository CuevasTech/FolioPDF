"""Sesion de documento: copia de trabajo, historial de cambios y guardado.

Modelo de datos
---------------
El archivo del usuario nunca se abre directamente: se copia a una carpeta de
trabajo privada y se trabaja sobre la copia. Cada cambio confirmado se anade a
esa copia como *actualizacion incremental* del PDF (el formato lo permite: el
archivo crece por el final y lo anterior queda intacto) y a continuacion se
reabre el documento. De ahi salen tres propiedades:

* Deshacer es truncar el archivo a su longitud anterior (los bytes cortados se
  apartan para poder rehacer). El coste es proporcional al cambio, no al PDF.
* Lo que hay en disco es siempre el estado confirmado: si el proceso muere no
  se pierde nada de lo ya aplicado.
* Si el PDF trae firmas digitales, sus bytes originales no se tocan, asi que
  las firmas siguen siendo validables despues de rellenar o anotar.

Cuando un cambio no puede anadirse de forma incremental (documento reparado al
abrir, o contenido eliminado de verdad, como al censurar) se reescribe entero a
un archivo de trabajo nuevo y la cadena incremental empieza de nuevo ahi.
"""

from __future__ import annotations

import logging
import os
import secrets
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pymupdf

from . import locks
from .errors import EngineError, denied, invalid, io_error, locked, not_found
from .host import DirectHost, Fetched
from .i18n import N_, _

log = logging.getLogger("folio.engine.session")

MAX_HISTORY = 200

# Bits de permiso del PDF agrupados por tipo de accion de la aplicacion.
_PERMISSION_BITS = {
    "print": pymupdf.PDF_PERM_PRINT,
    "modify": pymupdf.PDF_PERM_MODIFY,
    "copy": pymupdf.PDF_PERM_COPY,
    "annotate": pymupdf.PDF_PERM_ANNOTATE,
    "form": pymupdf.PDF_PERM_FORM,
    "accessibility": pymupdf.PDF_PERM_ACCESSIBILITY,
    "assemble": pymupdf.PDF_PERM_ASSEMBLE,
    "printHq": pymupdf.PDF_PERM_PRINT_HQ,
}
_PERMISSION_TEXT = {
    "modify": N_("modificar su contenido"),
    "annotate": N_("añadir comentarios"),
    "form": N_("rellenar el formulario"),
    "assemble": N_("reorganizar sus páginas"),
    "copy": N_("copiar su contenido"),
    "print": N_("imprimirlo"),
}


# Funciones que retocan el documento en memoria justo antes de guardarlo para el
# usuario (ver ``DocSession._export``). Las registran los modulos de operaciones.
EXPORT_HOOKS: list[Callable[[pymupdf.Document], Any]] = []


@dataclass
class Revision:
    """Estado del documento tras una operacion: los primeros ``length`` bytes de ``file``."""

    file: Path
    length: int
    label: str
    page_ids: tuple[str, ...]
    dirty: tuple[str, ...] = ()
    structural: bool = False
    tail: Path | None = None
    # Lo que se sabe de las firmas pertenece a la revision y no a la sesion: al
    # deshacer o rehacer se recupera con ella. Si no, deshacer una operacion
    # que rompia las firmas dejaria el documento marcado como "firmas rotas" y
    # al guardar se reescribiria, invalidando firmas que seguian siendo validas.
    signed: bool | None = None  # None: todavia sin comprobar
    locks: Any = None  # bloqueos de sus firmas (locks.Locks); None: todavia sin leer
    intact: bool = True  # los bytes ya firmados siguen identicos en ``file``
    # ``file`` empieza en esta revision y no arrastra estados anteriores de la
    # sesion ni objetos sin uso: lo es el archivo tal como se abrio y toda
    # reescritura completa con purga.
    fresh: bool = False
    sealed: bool = False  # la revision es una firma digital


@dataclass(frozen=True)
class Protection:
    """Cifrado que se aplicara al guardar. Las contrasenas viven solo en memoria."""

    user_password: str
    owner_password: str
    permissions: int


def new_doc_id() -> str:
    # secrets (CSPRNG) y no random: el identificador nombra la carpeta de
    # trabajo y no debe ser predecible.
    return "d" + secrets.token_hex(8)


class DocSession:
    def __init__(self, sid: str, work_dir: Path, name: str, source: str | None, host: Any = None) -> None:
        self.id = sid
        self.work_dir = work_dir
        self.name = name
        # Ruta del archivo del usuario. El motor no la abre nunca directamente:
        # leer, guardar y comprobar si ha cambiado pasa por ``host`` (host.py).
        self.source = source
        self.host = host if host is not None else DirectHost()
        self.password: str | None = None
        self.auth = 0  # resultado de authenticate(): bit 2 = usuario, bit 4 = propietario
        self.doc: pymupdf.Document = None  # type: ignore[assignment]
        self.revisions: list[Revision] = []
        self.cur = 0
        self.floor = 0  # indice mas antiguo al que aun se puede deshacer
        self.saved_at: int | None = None
        self.page_ids: list[str] = []
        self.gens: dict[str, int] = {}
        self.protection: Protection | None = None
        self.remove_protection = False
        self._protection_changed = False
        self._page_seq = 0
        self._file_seq = 0
        self._token = 0
        self._source_stat: tuple[int, int] | None = None
        self.noted_token = -1  # estado de la ultima ficha de recuperacion escrita (recovery.py)
        self.cache: dict[Any, Any] = {}  # derivados del documento abierto (se vacia al cerrarlo)

    # ------------------------------------------------------------------ apertura

    @classmethod
    def from_fetched(
        cls, work_root: Path, local: Path, fetched: Fetched, password: str | None, host: Any = None
    ) -> "DocSession":
        """Abre un PDF del usuario a partir de su copia privada ``local`` (ver ``host.fetch``)."""
        sid = new_doc_id()
        work_dir = work_root / sid
        work_dir.mkdir(parents=False, exist_ok=False)
        session = cls(sid, work_dir, fetched.name, fetched.path, host)
        try:
            first = work_dir / "w0.pdf"
            os.replace(local, first)  # la copia ya es privada: se adopta sin volver a copiarla
            session._file_seq = 1
            session._start(first, password)
            session.saved_at = 0
            session._source_stat = fetched.stat
        except BaseException:
            session.close()
            raise
        return session

    @classmethod
    def from_bytes(
        cls, work_root: Path, data: bytes, name: str, host: Any = None, password: str | None = None
    ) -> "DocSession":
        """Crea una sesion para un PDF nuevo (aun sin archivo del usuario)."""
        sid = new_doc_id()
        work_dir = work_root / sid
        work_dir.mkdir(parents=False, exist_ok=False)
        session = cls(sid, work_dir, name, None, host)
        try:
            first = work_dir / "w0.pdf"
            first.write_bytes(data)
            session._file_seq = 1
            session._start(first, password)
            session.saved_at = None  # nunca se ha guardado: hay cambios pendientes
        except BaseException:
            session.close()
            raise
        return session

    def _start(self, first: Path, password: str | None) -> None:
        try:
            doc = pymupdf.open(str(first))
        except Exception as exc:  # noqa: BLE001 - MuPDF lanza tipos propios
            raise EngineError("invalid_file", _("El archivo no es un PDF válido o está dañado.")) from exc
        try:
            if not doc.is_pdf:
                raise EngineError("invalid_file", _("El archivo no es un PDF."))
            if doc.needs_pass:
                if password is None:
                    raise EngineError("needs_password", _("Este PDF está protegido con contraseña."))
                self.auth = int(doc.authenticate(password))
                if not self.auth:
                    raise EngineError("wrong_password", _("La contraseña no es correcta."))
                self.password = password
            count = doc.page_count
        finally:
            doc.close()
        if count <= 0:
            raise EngineError("invalid_file", _("El PDF no contiene páginas."))
        self.page_ids = [self._new_page_id() for _ in range(count)]
        self.gens = dict.fromkeys(self.page_ids, 0)
        self.revisions = [Revision(first, first.stat().st_size, _("Abrir"), tuple(self.page_ids), fresh=True)]
        self.cur = 0
        self._open_current()

    @property
    def signed(self) -> bool:
        """El documento, en su estado actual, lleva alguna firma digital."""
        return bool(self.revisions[self.cur].signed) if self.revisions else False

    @property
    def chain_intact(self) -> bool:
        """Los bytes firmados siguen identicos: guardar puede ser una copia exacta."""
        return self.revisions[self.cur].intact if self.revisions else True

    def _open_current(self) -> None:
        rev = self.revisions[self.cur]
        doc = pymupdf.open(str(rev.file))
        # OJO: needs_pass se lee UNA sola vez y antes de autenticar. En MuPDF esa
        # consulta reintenta la autenticacion con contrasena vacia; hecha despues
        # de autenticar invalida la clave en memoria y todo lo que se guarde a
        # continuacion queda cifrado con una clave erronea (datos ilegibles).
        locked = bool(doc.needs_pass)
        # Tambien se autentica cuando el PDF se abre sin contrasena pero el
        # usuario lo desbloqueo como propietario: asi conserva ese acceso.
        if locked or self.password is not None:
            auth = int(doc.authenticate(self.password or ""))
            if locked and not auth:
                doc.close()
                raise EngineError("internal", _("No se pudo reabrir el documento protegido."))
            self.auth = auth
        _harden(doc)
        self.doc = doc
        if rev.signed is None:
            # Una vez por revision: al volver a ella (deshacer, rehacer o
            # descartar cambios) se reutiliza lo ya comprobado.
            rev.signed = _has_signatures(doc)
        if rev.locks is None:
            rev.locks = locks.read(doc) if rev.signed else locks.NONE

    def _close_doc(self) -> None:
        # Las listas de dibujo y demas objetos derivados dependen del documento
        # abierto: se sueltan antes de cerrarlo para no dejar el archivo retenido.
        self.cache.clear()
        if self.doc is not None:
            try:
                self.doc.close()
            except Exception:  # noqa: BLE001
                log.warning("fallo al cerrar el documento de la sesion %s", self.id)
            self.doc = None  # type: ignore[assignment]

    def close(self, *, keep_unsaved: bool = False) -> None:
        """Cierra el documento y borra su copia de trabajo.

        Con ``keep_unsaved`` (la aplicacion ha desaparecido sin cerrar el
        documento: se ha colgado o la han matado), si tiene cambios sin guardar
        la copia se conserva para ofrecer recuperarla (ver recovery.py).
        """
        self._close_doc()
        if keep_unsaved and self.dirty and self.revisions:
            return
        # Los archivos de trabajo pueden contener datos sensibles del usuario:
        # se borran al cerrar el documento, no al salir de la aplicacion.
        shutil.rmtree(self.work_dir, ignore_errors=True)

    # --------------------------------------------------------------- pagina/ids

    def _new_page_id(self) -> str:
        self._page_seq += 1
        return f"p{self._page_seq}"

    def new_page_ids(self, count: int) -> list[str]:
        return [self._new_page_id() for _ in range(count)]

    def page_index(self, page_id: str) -> int:
        try:
            return self.page_ids.index(page_id)
        except ValueError:
            raise not_found(_("La página ya no existe.")) from None

    def page(self, page_id: str) -> pymupdf.Page:
        return self.doc[self.page_index(page_id)]

    def pages_payload(self) -> list[dict[str, Any]]:
        out = []
        for index, pid in enumerate(self.page_ids):
            page = self.doc[index]
            rect = page.rect
            out.append(
                {
                    "id": pid,
                    "w": round(rect.width, 3),
                    "h": round(rect.height, 3),
                    "rot": page.rotation % 360,
                    "gen": self.gens.get(pid, 0),
                }
            )
        return out

    # ----------------------------------------------------------------- permisos

    @property
    def encrypted(self) -> bool:
        return bool((self.doc.metadata or {}).get("encryption"))

    @property
    def is_owner(self) -> bool:
        return not self.encrypted or bool(self.auth & 4)

    def allowed(self, action: str) -> bool:
        if self.is_owner:
            return True
        return bool(self.doc.permissions & _PERMISSION_BITS[action])

    @property
    def locks(self) -> Any:
        """Bloqueos de las firmas del documento en su estado actual (ver locks.py)."""
        found = self.revisions[self.cur].locks if self.revisions else None
        return found if found is not None else locks.NONE

    def require_unlocked(self, action: str) -> None:
        """Respeta lo que permiten las firmas del documento (ninguna contrasena lo desbloquea)."""
        if not self.locks.allows(action):
            if self.locks.level == locks.NO_CHANGES:
                message = _("El documento está bloqueado por su firma: ya no admite cambios.")
            elif self.locks.level == locks.FORMS:
                message = _("Quien firmó este documento solo permite rellenar el formulario y firmar.")
            else:
                message = _("Quien firmó este documento solo permite rellenar, firmar y añadir comentarios.")
            raise locked(message, action=action)

    def require_permission(self, action: str) -> None:
        """Respeta las restricciones de la proteccion del PDF salvo con la contrasena de propietario."""
        if not self.allowed(action):
            what = _(_PERMISSION_TEXT[action]) if action in _PERMISSION_TEXT else _("hacer esto")
            raise denied(
                _(
                    "El autor de este PDF no permite {what}. Introduce la contraseña de propietario para desbloquearlo.",
                    what=what,
                ),
                action=action,
            )

    def require(self, action: str) -> None:
        """Comprueba que se puede hacer ``action``: lo permiten sus firmas y su proteccion."""
        self.require_unlocked(action)
        self.require_permission(action)

    def require_field(self, name: str) -> None:
        """El campo no lo ha dejado fijo una firma."""
        if self.locks.field_locked(name):
            raise locked(_("Este campo lo ha dejado fijo una firma del documento: no se puede cambiar."))

    def unlock(self, password: str) -> bool:
        """Intenta autenticarse como propietario; devuelve si se consiguio."""
        auth = int(self.doc.authenticate(password))
        if auth & 4:
            self.auth = auth
            self.password = password
            self._token += 1
            return True
        # Un intento fallido deja invalidada la clave en memoria: siempre se
        # vuelve a autenticar con el acceso que ya se tenia (vacia si no hacia
        # falta contrasena para abrir).
        self.doc.authenticate(self.password or "")
        return False

    # ---------------------------------------------------------------- historial

    @property
    def can_undo(self) -> bool:
        return self.cur > self.floor

    @property
    def can_redo(self) -> bool:
        return self.cur < len(self.revisions) - 1

    @property
    def dirty(self) -> bool:
        return self.saved_at != self.cur or self._protection_changed

    def state(self) -> dict[str, Any]:
        return {
            "token": self._token,
            "canUndo": self.can_undo,
            "canRedo": self.can_redo,
            "undoLabel": self.revisions[self.cur].label if self.can_undo else None,
            "redoLabel": self.revisions[self.cur + 1].label if self.can_redo else None,
            "dirty": self.dirty,
            "name": self.name,
            "path": str(self.source) if self.source else None,
            "signed": self.signed,
            "signaturesIntact": self.signed and self.chain_intact,
            # Lo que permiten sus firmas: nivel 1 nada, 2 rellenar y firmar, 3 ademas anotar.
            "lock": self.locks.payload(),
            "protection": self._protection_state(),
        }

    def _protection_state(self) -> str:
        if self.remove_protection:
            return "removing"
        if self.protection is not None:
            return "protected"
        return "protected" if self.encrypted else "none"

    def result(
        self,
        changed: list[str] | tuple[str, ...] = (),
        *,
        structural: bool = False,
        **extra: Any,
    ) -> dict[str, Any]:
        """Respuesta comun de toda operacion que modifica el documento."""
        out: dict[str, Any] = {
            "state": self.state(),
            "changed": [{"id": pid, "gen": self.gens.get(pid, 0)} for pid in changed if pid in self.gens],
        }
        if structural:
            out["pages"] = self.pages_payload()
        out.update(extra)
        return out

    def _bump(self, page_ids: tuple[str, ...] | list[str]) -> None:
        for pid in page_ids:
            self.gens[pid] = self.gens.get(pid, 0) + 1

    def _drop_redo(self) -> None:
        if not self.can_redo:
            return
        kept = {rev.file for rev in self.revisions[: self.cur + 1]}
        for rev in self.revisions[self.cur + 1 :]:
            if rev.tail is not None:
                rev.tail.unlink(missing_ok=True)
            if rev.file not in kept:
                rev.file.unlink(missing_ok=True)
        del self.revisions[self.cur + 1 :]

    def rollback(self) -> None:
        """Descarta los cambios en memoria no confirmados volviendo al ultimo estado en disco."""
        self._close_doc()
        self._open_current()

    def edit(self, label: str) -> "Edit":
        """Transaccion de edicion: confirma al salir bien y revierte si algo falla.

        Asi una operacion que falla a medias nunca deja el documento en un
        estado intermedio (falla de forma segura, A10:2025).
        """
        return Edit(self, label)

    def commit(
        self,
        label: str,
        *,
        dirty: list[str] | tuple[str, ...] = (),
        structural: bool = False,
        page_ids: list[str] | None = None,
        force_full: bool = False,
    ) -> None:
        """Confirma los cambios hechos en memoria como una nueva revision."""
        current = self.revisions[self.cur]
        self._drop_redo()
        new_ids = list(page_ids) if page_ids is not None else list(self.page_ids)

        target = current.file
        incremental = False
        # can_save_incrementally() es falso tras eliminar contenido de verdad
        # (censura): MuPDF permitiria igualmente el guardado incremental, pero
        # entonces lo eliminado seguiria en el archivo. Se respeta siempre.
        if not force_full and self.doc.can_save_incrementally() and self.doc.name == str(current.file):
            try:
                if current.file.stat().st_size == current.length:
                    self.doc.save(str(current.file), incremental=True, encryption=pymupdf.PDF_ENCRYPT_KEEP)
                    incremental = True
            except Exception:  # noqa: BLE001
                log.warning("guardado incremental fallido; se reescribe el archivo de trabajo")
                _truncate(current.file, current.length)
        if not incremental:
            target = self.work_dir / f"w{self._file_seq}.pdf"
            self._file_seq += 1
            try:
                # Si la operacion pide la reescritura es para que lo eliminado
                # no siga en el archivo. Guardar sin recoleccion de basura (para
                # no renumerar objetos a mitad de sesion) copiaria tambien lo que
                # ya nada referencia (una pagina borrada, un contenido censurado),
                # y si despues se firma, ese archivo se guarda byte a byte: por
                # eso antes se purga. En las demas reescrituras (PDF que no admite
                # guardado incremental) no se purga en cada cambio, que en
                # documentos grandes se nota: el archivo queda "con historial" y
                # se limpia de una vez antes de firmar.
                if force_full:
                    self._purge()
                self.doc.save(str(target), garbage=0, deflate=False, encryption=pymupdf.PDF_ENCRYPT_KEEP)
            except BaseException:
                target.unlink(missing_ok=True)
                raise

        # Los identificadores de pagina solo cambian cuando la revision ya esta
        # escrita. Si el guardado falla, el documento vuelve atras y deben
        # seguir nombrando las mismas paginas: de lo contrario la siguiente
        # operacion caeria en otra pagina sin avisar.
        self.page_ids = new_ids
        for pid in new_ids:
            self.gens.setdefault(pid, 0)
        self._close_doc()
        self.revisions.append(
            Revision(
                target, target.stat().st_size, label, tuple(self.page_ids), tuple(dirty), structural,
                # Reescribir el archivo cambia los bytes que cubrian las firmas.
                intact=current.intact and incremental,
                fresh=force_full and not incremental,
            )
        )
        self.cur += 1
        if self.cur - self.floor > MAX_HISTORY:
            self.floor = self.cur - MAX_HISTORY
        self._bump(tuple(dirty))
        self._token += 1
        self._open_current()

    def append_revision(
        self,
        label: str,
        appended: bytes,
        *,
        dirty: list[str] | tuple[str, ...] = (),
        seals: bool = False,
    ) -> None:
        """Confirma una revision producida fuera de MuPDF anadiendo sus bytes al archivo de trabajo.

        ``seals`` indica que la revision contiene una firma digital nueva: a partir
        de aqui el archivo debe guardarse byte a byte para que siga siendo valida.
        """
        current = self.revisions[self.cur]
        self._drop_redo()
        self._close_doc()
        with open(current.file, "ab") as fh:
            fh.write(appended)
        self.revisions.append(
            Revision(
                current.file, current.file.stat().st_size, label, tuple(self.page_ids), tuple(dirty), False,
                intact=True if seals else current.intact,
                sealed=seals,
            )
        )
        self.cur += 1
        if self.cur - self.floor > MAX_HISTORY:
            self.floor = self.cur - MAX_HISTORY
        self._bump(tuple(dirty))
        self._token += 1
        self._open_current()

    def _purge(self) -> None:
        """Libera los objetos del documento en memoria que ya nada referencia."""
        from .redaction import purge_orphans  # importacion tardia: redaction no depende de la sesion

        try:
            purge_orphans(self.doc)
        except EngineError:
            raise
        except Exception as exc:  # noqa: BLE001
            # La operacion pidio la reescritura para que lo eliminado no siga
            # en el archivo: sin la purga no se cumple, asi que falla entera y
            # el documento vuelve atras (A10:2025).
            log.warning("no se pudieron liberar los objetos sin uso (%s)", type(exc).__name__)
            raise EngineError(
                "internal", _("No se pudo completar la operación de forma segura. No se ha cambiado el documento.")
            ) from None

    @property
    def history_in_file(self) -> bool:
        """El archivo de trabajo contiene estados anteriores de esta sesion."""
        return not self.revisions[self.cur].fresh

    def current_bytes(self) -> bytes:
        """Bytes exactos del estado confirmado actual."""
        rev = self.revisions[self.cur]
        with open(rev.file, "rb") as fh:
            return fh.read(rev.length)

    def undo(self) -> Revision:
        if not self.can_undo:
            raise invalid(_("No hay nada que deshacer."))
        current = self.revisions[self.cur]
        previous = self.revisions[self.cur - 1]
        self._close_doc()
        if previous.file == current.file:
            # Se apartan los bytes de la revision deshecha para poder rehacerla.
            tail = self.work_dir / f"redo-{self.cur}-{secrets.token_hex(4)}.bin"
            with open(current.file, "r+b") as fh:
                fh.seek(previous.length)
                tail.write_bytes(fh.read())
                fh.truncate(previous.length)
            current.tail = tail
        self.cur -= 1
        self.page_ids = list(previous.page_ids)
        self._bump(current.dirty)
        self._token += 1
        self._open_current()
        return current

    def redo(self) -> Revision:
        if not self.can_redo:
            raise invalid(_("No hay nada que rehacer."))
        current = self.revisions[self.cur]
        following = self.revisions[self.cur + 1]
        self._close_doc()
        if following.file == current.file:
            if following.tail is None:
                raise EngineError("internal", _("Falta la información para rehacer."))
            with open(current.file, "ab") as fh:
                fh.write(following.tail.read_bytes())
            following.tail.unlink(missing_ok=True)
            following.tail = None
        self.cur += 1
        self.page_ids = list(following.page_ids)
        for pid in self.page_ids:
            self.gens.setdefault(pid, 0)
        self._bump(following.dirty)
        self._token += 1
        self._open_current()
        return following

    # ------------------------------------------------------------------ guardado

    def set_protection(self, protection: Protection | None, *, remove: bool = False) -> None:
        self.protection = protection
        self.remove_protection = remove
        self._protection_changed = True
        self._token += 1

    def changed_on_disk(self) -> bool:
        if self.source is None or self._source_stat is None:
            return False
        current = self.host.stat(self.source)
        return current is None or current != self._source_stat

    def save(self, dest: str, *, force: bool = False) -> dict[str, Any]:
        """Guarda en ``dest`` (ruta ya validada con ``host.check_output``)."""
        same_file = self.source is not None and self.host.same_file(dest, self.source)
        if same_file and not force and self.changed_on_disk():
            raise EngineError(
                "changed_on_disk",
                _("El archivo ha cambiado en el disco desde que se abrió. ¿Quieres sobrescribirlo?"),
            )
        # Se prepara en la carpeta de trabajo y despues se instala de una vez en
        # su sitio (host.install): un fallo a medias nunca deja un PDF roto.
        local = self.work_dir / f"guardar-{secrets.token_hex(6)}.pdf"
        preserved = False
        try:
            if self.signed and self.chain_intact and self.protection is None and not self.remove_protection:
                # Copia byte a byte: los rangos firmados quedan identicos.
                rev = self.revisions[self.cur]
                shutil.copyfile(rev.file, local)
                preserved = True
            else:
                self._export(local)
            stamp = self.host.install(local, dest)
        except EngineError:
            raise
        except OSError as exc:
            raise io_error(_("No se pudo guardar el archivo.")) from exc
        except Exception as exc:  # noqa: BLE001
            # MuPDF lanza sus propios tipos (no OSError) cuando no puede
            # escribir, y su mensaje lleva la ruta completa: solo se registra
            # el tipo y se responde con un error de escritura (A09:2025).
            log.warning("no se pudo guardar el documento (%s)", type(exc).__name__)
            raise io_error(_("No se pudo guardar el archivo. Comprueba que la carpeta admite escritura.")) from None
        finally:
            local.unlink(missing_ok=True)
        self.source = dest
        self.name = Path(dest).name
        self.saved_at = self.cur
        self._protection_changed = False
        self._source_stat = stamp
        self._token += 1
        return {
            "state": self.state(),
            "signaturesPreserved": preserved,
            "size": stamp[0],
            # En un documento firmado se guarda el archivo de trabajo tal cual:
            # si la sesion acumulo varios cambios tras la firma, sus estados
            # intermedios van dentro. La interfaz lo avisa.
            "historyKept": preserved and self._steps_since_base() > 1,
        }

    def _steps_since_base(self) -> int:
        """Cuantas revisiones hay encima de la ultima firma o reescritura completa."""
        steps = 0
        for revision in reversed(self.revisions[: self.cur + 1]):
            if revision.fresh or revision.sealed:
                break
            steps += 1
        return steps

    def _export(self, target: Path) -> None:
        """Reescribe el documento completo y limpio (sin historial ni objetos huerfanos)."""
        try:
            # Ajustes que solo afectan a la copia que se guarda (por ejemplo,
            # que los textos anadidos compartan fuente). El documento de la
            # sesion no cambia: al terminar se vuelve al estado confirmado.
            for hook in EXPORT_HOOKS:
                hook(self.doc)
        except Exception as exc:  # noqa: BLE001
            # Guardar no depende de estos ajustes: si uno falla, se descarta lo
            # que hubiera hecho a medias y se guarda el documento tal como esta.
            log.warning("no se pudo compactar el contenido antes de guardar (%s)", type(exc).__name__)
            self.rollback()
        doc = self.doc
        try:
            meta = dict(doc.metadata or {})
            meta.pop("format", None)
            meta.pop("encryption", None)
            meta["producer"] = "Folio"
            meta["modDate"] = pymupdf.get_pdf_now()
            doc.set_metadata(meta)
            kwargs: dict[str, Any] = {
                "garbage": 3,
                "deflate": True,
                "use_objstms": 1,
                # clean=False: no se reescriben los flujos de contenido, asi los
                # elementos anadidos con Folio siguen siendo editables despues.
                "clean": False,
            }
            if self.remove_protection:
                kwargs["encryption"] = pymupdf.PDF_ENCRYPT_NONE
            elif self.protection is not None:
                # AES-256 es el unico cifrado de PDF que se ofrece: RC4 y AES-128
                # con su derivacion de clave antigua se consideran debiles (A04:2025).
                kwargs.update(
                    encryption=pymupdf.PDF_ENCRYPT_AES_256,
                    user_pw=self.protection.user_password,
                    owner_pw=self.protection.owner_password,
                    permissions=self.protection.permissions,
                )
            else:
                kwargs["encryption"] = pymupdf.PDF_ENCRYPT_KEEP
            doc.save(str(target), **kwargs)
        finally:
            # Guardar con limpieza renumera objetos en memoria: se vuelve al
            # estado confirmado en disco para que todo siga siendo coherente.
            self.rollback()

    def export_copy(self, target: Path, **save_options: Any) -> None:
        """Guarda una copia sin cambiar el archivo asociado a la sesion."""
        try:
            self.doc.save(str(target), **save_options)
        finally:
            self.rollback()


class Edit:
    """Contexto de una modificacion del documento (ver ``DocSession.edit``)."""

    def __init__(self, session: DocSession, label: str) -> None:
        self.session = session
        self.label = label
        self.dirty: list[str] = []
        self._touched: set[str] = set()
        self.structural = False
        self.page_ids: list[str] | None = None
        self.force_full = False
        self.skip = False  # la operacion resulto no cambiar nada

    def touch(self, *page_ids: str) -> None:
        # Con un conjunto al lado: marcar miles de paginas no debe costar un
        # recorrido de la lista por cada una.
        for pid in page_ids:
            if pid not in self._touched:
                self._touched.add(pid)
                self.dirty.append(pid)

    def __enter__(self) -> "Edit":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        if exc_type is not None:
            try:
                self.session.rollback()
            except Exception:  # noqa: BLE001
                log.exception("no se pudo revertir la sesion %s", self.session.id)
            return False
        if self.skip:
            return False
        try:
            self.session.commit(
                self.label,
                dirty=self.dirty,
                structural=self.structural,
                page_ids=self.page_ids,
                force_full=self.force_full,
            )
        except BaseException:
            self.session.rollback()
            raise
        return False


# ---------------------------------------------------------------------- utilidades


def _truncate(path: Path, length: int) -> None:
    with open(path, "r+b") as fh:
        fh.truncate(length)


def _harden(doc: pymupdf.Document) -> None:
    """Nunca se ejecuta JavaScript incrustado en un PDF (contenido no fiable)."""
    try:
        pdf = pymupdf._as_pdf_document(doc, required=False)
        if pdf is not None and getattr(pdf, "m_internal", None):
            pymupdf.mupdf.pdf_disable_js(pdf)
    except Exception:  # noqa: BLE001 - PyMuPDF no activa JS por si mismo; esto es un refuerzo
        log.debug("pdf_disable_js no disponible")


def _has_signatures(doc: pymupdf.Document) -> bool:
    try:
        if doc.get_sigflags() <= 0:
            return False
        for page in doc:
            for widget in page.widgets(types=[pymupdf.PDF_WIDGET_TYPE_SIGNATURE]):
                if widget.is_signed:
                    return True
    except Exception:  # noqa: BLE001
        log.warning("no se pudo comprobar si el documento tiene firmas")
    return False


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
