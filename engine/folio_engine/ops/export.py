"""Exportaciones: copia optimizada, imagenes, texto plano y Word.

Ninguna modifica el documento abierto: trabajan sobre una copia en memoria del
estado actual o solo leen. El resultado se prepara en la carpeta de trabajo y
se entrega con ``host`` (host.py) a la ruta que eligio el usuario, que se valida
alli; aparece completo o no aparece, porque se instala de una sola vez.
"""

from __future__ import annotations

import importlib.util
import logging
import math
import secrets
import shutil
from pathlib import Path
from typing import Annotated, Any, Literal

import pymupdf
from pydantic import Field, StringConstraints
from pymupdf import mupdf

from .. import wordworker
from ..errors import EngineError, invalid, io_error, unsupported
from ..i18n import _
from ..models import DocParams, PageId, PathStr
from ..paths import safe_filename
from ..server import Ctx, method
from ..session import DocSession
from .render import MAX_PIXELS, render_pixmap

log = logging.getLogger("folio.engine.export")

PageList = Annotated[list[PageId], Field(min_length=1, max_length=100_000)]

# Mismas opciones de extraccion que ``doc.plainText`` (ops/text.py).
_TEXT_FLAGS = pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_MEDIABOX_CLIP
# Caracteres de control que no pintan nada en un archivo de texto (se dejan el
# tabulador y el salto de linea): un NUL haria que algunos editores lo tomasen
# por binario.
_CONTROL = {code: None for code in (*range(0, 9), 11, 12, *range(14, 32), 127)}


# ------------------------------------------------------------------ utilidades

def _local(session: DocSession, suffix: str) -> Path:
    # En la carpeta privada del documento y con nombre aleatorio (CSPRNG).
    return session.work_dir / f"export-{secrets.token_hex(6)}{suffix}"


def _destination(session: DocSession, raw: str, suffix: str) -> str:
    """Ruta de salida validada (extension en lista blanca, A01:2025/A05:2025)."""
    dest = session.host.check_output(raw, (suffix,))
    # Una exportacion es una copia: no puede sustituir al archivo del documento
    # abierto, que la sesion seguiria creyendo intacto.
    if session.source is not None and session.host.same_file(dest, session.source):
        raise invalid(_("Elige otro nombre: la copia no puede sustituir al documento que tienes abierto."))
    return dest


def _write_new(session: DocSession, folder: str, stem: str, suffix: str, data: bytes) -> str:
    """Escribe un archivo nuevo sin sobrescribir nunca uno existente (anade " (2)", " (3)"...)."""
    local = _local(session, suffix)
    try:
        local.write_bytes(data)
        return session.host.create_new(folder, stem, suffix, local)
    except OSError as exc:
        raise io_error(_("No se pudo preparar el archivo.")) from exc
    finally:
        local.unlink(missing_ok=True)


def _selection(session: DocSession, pages: list[str] | None) -> list[str]:
    ids = list(dict.fromkeys(pages)) if pages is not None else list(session.page_ids)
    for pid in ids:
        session.page_index(pid)  # falla ya si alguna pagina no existe
    return ids


def _open_copy(session: DocSession) -> pymupdf.Document:
    """Documento independiente con el estado actual, ya autenticado."""
    doc = pymupdf.open(stream=session.current_bytes(), filetype="pdf")
    try:
        # needs_pass se lee UNA vez y antes de autenticar (ver session.py).
        locked = bool(doc.needs_pass)
        if locked or session.password is not None:
            if not doc.authenticate(session.password or "") and locked:
                raise EngineError("internal", _("No se pudo abrir la copia del documento."))
        try:  # nunca se ejecuta JavaScript incrustado (contenido no fiable)
            mupdf.pdf_disable_js(pymupdf._as_pdf_document(doc))
        except Exception:  # noqa: BLE001 - PyMuPDF no lo activa por si mismo; esto es un refuerzo
            pass
    except BaseException:
        doc.close()
        raise
    return doc


# ============================================================ copia optimizada

# Resolucion a partir de la cual se remuestrea una imagen, resolucion a la que
# se deja y calidad JPEG. "lossless": si las imagenes sin perdida (capturas,
# graficos) tambien pasan a JPEG; en el ajuste suave se dejan como estan.
_PRESETS: dict[str, dict[str, Any]] = {
    "light": {"dpi_threshold": 220, "dpi_target": 200, "quality": 85, "lossless": False},
    "balanced": {"dpi_threshold": 160, "dpi_target": 150, "quality": 75, "lossless": True},
    "strong": {"dpi_threshold": 110, "dpi_target": 96, "quality": 60, "lossless": True},
}


class OptimizeParams(DocParams):
    path: PathStr
    preset: Literal["light", "balanced", "strong"]
    grayscale: bool = False


@method("tools.optimize", OptimizeParams)
def tools_optimize(ctx: Ctx, p: OptimizeParams) -> dict[str, Any]:
    """Guarda una copia mas pequena del documento. El documento abierto no cambia."""
    session = ctx.engine.session(p.doc)
    # La copia lleva las imagenes recomprimidas: es un documento modificado, y
    # eso solo puede hacerlo quien tiene permiso para modificar (A01:2025).
    session.require("modify")
    dest = _destination(session, p.path, ".pdf")
    options: dict[str, Any] = {
        "garbage": 4, "deflate": True, "deflate_images": True, "deflate_fonts": True, "use_objstms": 1,
    }
    # Misma proteccion que tendria el documento si se guardase ahora: una
    # copia nunca sale menos protegida que el original (A04:2025).
    if session.remove_protection:
        options["encryption"] = pymupdf.PDF_ENCRYPT_NONE
    elif session.protection is not None:
        options.update(
            encryption=pymupdf.PDF_ENCRYPT_AES_256,
            user_pw=session.protection.user_password,
            owner_pw=session.protection.owner_password,
            permissions=session.protection.permissions,
        )
    else:
        options["encryption"] = pymupdf.PDF_ENCRYPT_KEEP

    # Referencia: el documento compactado con las imagenes intactas, en la
    # carpeta privada de trabajo. Sirve para decir con verdad cuanto se gana y
    # para no entregar nunca una "copia comprimida" mayor que un guardado normal
    # (pasa con escaneados en blanco y negro o imagenes ya muy comprimidas).
    plain = session.work_dir / f"copia-{secrets.token_hex(6)}.pdf"
    tmp = _local(session, ".pdf")
    reduced = True
    try:
        ctx.progress(0, 5, _("Preparando la copia…"))
        doc = _open_copy(session)
        try:
            doc.save(str(plain), **options)
        except Exception as exc:  # noqa: BLE001 - MuPDF lanza tipos propios
            log.warning("no se pudo preparar la copia de referencia: %s", type(exc).__name__)
            raise EngineError("internal", _("No se pudo preparar la copia del documento.")) from None
        finally:
            doc.close()
        before = plain.stat().st_size
        ctx.check_cancel()

        doc = _open_copy(session)
        try:
            ctx.progress(1, 5, _("Reduciendo las imágenes…"))
            try:
                # bitonal=False: las imagenes de 1 bit (texto escaneado en blanco
                # y negro) ya ocupan poco y remuestrearlas las deja ilegibles.
                doc.rewrite_images(bitonal=False, set_to_gray=p.grayscale, **_PRESETS[p.preset])
            except Exception as exc:  # noqa: BLE001 - imagen que MuPDF no sabe reescribir
                log.warning("no se pudieron reducir las imagenes: %s", type(exc).__name__)
                doc.close()
                doc = _open_copy(session)  # copia intacta: se guarda al menos compactada
                reduced = False
            ctx.check_cancel()
            ctx.progress(2, 5, _("Compactando las fuentes…"))
            if not doc.is_form_pdf:
                # En un formulario no: quien lo rellene despues necesita las
                # letras que todavia no se han usado.
                try:
                    doc.subset_fonts()
                except Exception as exc:  # noqa: BLE001 - sin subconjunto el PDF es valido, solo algo mayor
                    log.debug("no se pudieron compactar las fuentes: %s", type(exc).__name__)
            ctx.progress(3, 5, _("Guardando…"))
            try:
                doc.save(str(tmp), **options)
            except Exception as exc:  # noqa: BLE001 - MuPDF lanza tipos propios
                log.warning("no se pudo guardar la copia optimizada: %s", type(exc).__name__)
                raise io_error(_("No se pudo guardar la copia. Comprueba que la carpeta admite escritura.")) from None
        finally:
            doc.close()
        after = tmp.stat().st_size
        if after >= before:
            # Recomprimir no ha servido: se entrega la copia compactada.
            try:
                shutil.copyfile(plain, tmp)
            except OSError as exc:
                raise io_error(_("No se pudo guardar la copia. Comprueba que la carpeta admite escritura.")) from exc
            after = before
            reduced = False
        ctx.progress(4, 5, _("Guardando…"))
        session.host.install(tmp, dest)
    finally:
        for leftover in (tmp, plain):
            try:
                leftover.unlink(missing_ok=True)
            except OSError:
                log.warning("no se pudo borrar un temporal de la copia comprimida")
    # ``before`` es lo que ocuparia un guardado normal; ``after``, lo que ocupa la copia.
    return {"path": dest, "before": before, "after": after, "signaturesLost": session.signed, "imagesReduced": reduced}

# ====================================================================== imagenes

class ExportImagesParams(DocParams):
    dir: PathStr
    pages: PageList | None = None
    format: Literal["png", "jpeg"]
    dpi: Annotated[int, Field(ge=36, le=600)] = 150
    quality: Annotated[int, Field(ge=30, le=100)] = 90
    prefix: Annotated[str, StringConstraints(max_length=80)] | None = None


@method("tools.exportImages", ExportImagesParams)
def tools_export_images(ctx: Ctx, p: ExportImagesParams) -> dict[str, Any]:
    """Guarda cada pagina como imagen: ``<prefijo>-<n>.<ext>``."""
    session = ctx.engine.session(p.doc)
    # Sacar las paginas como imagen es extraer su contenido: si el autor lo
    # prohibio, se respeta (A01:2025).
    session.require("copy")
    folder = session.host.check_output_dir(p.dir)
    ids = _selection(session, p.pages)
    # El prefijo pasa por safe_filename: sin separadores ni "..", no puede
    # sacar los archivos de la carpeta elegida (A01:2025, manipulacion de rutas).
    stem = safe_filename(p.prefix if p.prefix else Path(session.name).stem, _("pagina"))[:80].rstrip(" .") or _("pagina")
    digits = len(str(len(session.page_ids)))
    suffix = ".png" if p.format == "png" else ".jpg"
    files: list[str] = []
    reduced: list[dict[str, Any]] = []
    try:
        for n, pid in enumerate(ids):
            ctx.progress(n, len(ids), _("Exportando la página {page} de {total}", page=n + 1, total=len(ids)))
            index = session.page_index(pid)
            rect = session.page(pid).rect
            scale = p.dpi / 72
            # Tope de pixeles por imagen (el mismo del dibujo en pantalla):
            # acota la memoria que puede pedir una pagina enorme (A05:2025).
            limit = math.sqrt(MAX_PIXELS / max(rect.width * rect.height, 1.0)) * 0.995
            if scale > limit:
                scale = limit
                reduced.append({"page": pid, "dpi": int(scale * 72)})
            pix = render_pixmap(session, pid, scale, alpha_channel=False)
            try:
                pix.set_dpi(round(scale * 72), round(scale * 72))
                data = pix.tobytes("png") if p.format == "png" else pix.tobytes("jpg", jpg_quality=p.quality)
            finally:
                del pix
            files.append(_write_new(session, folder, f"{stem}-{index + 1:0{digits}d}", suffix, data))
    except BaseException:
        # Cancelada o fallida a medias: no se deja una exportacion incompleta
        # que parezca terminada. Solo se borra lo creado en esta llamada.
        for path in files:
            session.host.discard(path)
        raise
    return {"files": files, "reduced": reduced}


# ========================================================================= texto

class ExportTextParams(DocParams):
    path: PathStr
    pages: PageList | None = None


@method("tools.exportText", ExportTextParams)
def tools_export_text(ctx: Ctx, p: ExportTextParams) -> dict[str, Any]:
    """Guarda el texto de las paginas en un archivo de texto plano (UTF-8)."""
    session = ctx.engine.session(p.doc)
    session.require("copy")
    dest = _destination(session, p.path, ".txt")
    ids = _selection(session, p.pages)
    chars = 0
    tmp = _local(session, ".txt")
    try:
        try:
            # "x": el temporal se crea nuevo; no se escribe sobre nada que
            # alguien haya dejado con ese nombre.
            with open(tmp, "x", encoding="utf-8", newline="") as out:
                for n, pid in enumerate(ids):
                    message = _("Extrayendo el texto de la página {page} de {total}", page=n + 1, total=len(ids))
                    ctx.progress(n, len(ids), message)
                    text = session.page(pid).get_text("text", flags=_TEXT_FLAGS)
                    text = text.replace("\r\n", "\n").replace("\r", "\n").translate(_CONTROL).rstrip()
                    chars += len(text)
                    heading = _("===== Página {page} =====", page=session.page_index(pid) + 1)
                    block = f"{heading}\n{text}\n\n"
                    out.write(block.replace("\n", "\r\n"))
        except OSError as exc:
            raise io_error(_("No se pudo escribir el archivo de texto.")) from exc
        session.host.install(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)
    return {"path": dest, "pageCount": len(ids), "chars": chars}


# ========================================================================== Word

class ExportWordParams(DocParams):
    path: PathStr


@method("tools.exportWord", ExportWordParams)
def tools_export_word(ctx: Ctx, p: ExportWordParams) -> dict[str, Any]:
    """Convierte el documento a Word (.docx) con la biblioteca pdf2docx, en un proceso aparte."""
    session = ctx.engine.session(p.doc)
    session.require("copy")
    dest = _destination(session, p.path, ".docx")
    # Solo se mira si la biblioteca esta instalada; cargarla es cosa del proceso
    # auxiliar (dentro del motor se quedaria bloqueada, ver wordworker.py).
    try:
        available = importlib.util.find_spec("pdf2docx") is not None
    except (ImportError, ValueError):
        available = False
    if not available:
        raise unsupported(_("La conversión a Word no está disponible en esta instalación."))

    token = secrets.token_hex(6)
    work_pdf = session.work_dir / f"word-{token}.pdf"
    work_docx = session.work_dir / f"word-{token}.docx"
    try:
        ctx.progress(0, 0, _("Convirtiendo el documento…"))
        # Copia exacta del estado actual en la carpeta privada de trabajo: si
        # el PDF esta cifrado, la copia tambien, y se borra al terminar.
        work_pdf.write_bytes(session.current_bytes())
        # En un proceso aparte (ver wordworker.py). Con el motor aislado lo
        # lanza el intermediario, porque este no puede crear procesos.
        code = session.host.convert_to_word(ctx.engine.work_root, work_pdf, work_docx, session.password, ctx.check_cancel)
        if code == wordworker.EXIT_UNAVAILABLE:
            raise unsupported(_("La conversión a Word no está disponible en esta instalación."))
        if code != wordworker.EXIT_OK or not work_docx.is_file():
            # Solo el codigo de salida: el detalle podria citar contenido (A09:2025).
            log.warning("fallo al convertir a Word (codigo %s)", code)
            raise EngineError("conversion_failed", _("No se pudo convertir este documento a Word."))
        size, _mtime = session.host.install(work_docx, dest)
    finally:
        for leftover in (work_pdf, work_docx):
            try:
                leftover.unlink(missing_ok=True)
            except OSError:
                log.warning("no se pudo borrar un temporal de la conversion a Word")
    return {"path": dest, "size": size}