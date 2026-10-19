"""Proteccion con contrasena, limpieza de informacion oculta y censura.

Las operaciones de aqui ponen los permisos, la transaccion y el resultado; el
trabajo sobre el PDF (que se elimina, como y como se verifica) esta en
``redaction.py``.
"""

from __future__ import annotations

import logging
import unicodedata
from typing import Annotated, Any, Literal

import pymupdf
from pydantic import Field, SecretStr, StringConstraints

from .. import redaction
from ..errors import EngineError, denied, invalid
from ..geometry import Geom
from ..i18n import _
from ..imaging import strip_embedded_image_metadata
from ..models import DocParams, PageId, PageParams, Params, Rect
from ..server import Ctx, method
from ..session import _PERMISSION_BITS, DocSession, Protection

log = logging.getLogger("folio.engine.security")

MAX_MARK_RECTS = 500
MAX_PAGES = 100_000
# Tope de marcas por busqueda: acota el trabajo y el tamano de la respuesta
# aunque se busque algo que aparece en todas las lineas (A05:2025).
MAX_FIND_MARKS = 5_000
MIN_ZONE = 0.5

PageList = Annotated[list[PageId], Field(min_length=1, max_length=MAX_PAGES)]
# Identificador de una marca: solo esta forma, asi nunca puede confundirse con
# otra cosa ni usarse para componer nada.
MarkId = Annotated[str, StringConstraints(pattern=r"^r[0-9]{1,9}$")]


# ============================================================ proteccion (contrasenas)

# SecretStr: el valor no aparece en repr() ni en trazas, asi que no puede acabar
# en un log por descuido (A09:2025). PyMuPDF admite como maximo 40 caracteres.
UserPassword = Annotated[SecretStr, Field(max_length=40)]
OwnerPassword = Annotated[SecretStr, Field(min_length=1, max_length=40)]


class Permissions(Params):
    # Sin valores por defecto: cada permiso es una decision de seguridad que
    # debe llegar explicita; uno omitido se rechaza en vez de suponerse.
    print: bool
    modify: bool
    copy_: bool = Field(alias="copy")  # "copy" ya es un metodo de los modelos de pydantic
    annotate: bool
    form: bool
    assemble: bool
    accessibility: bool
    printHq: bool


class ProtectParams(DocParams):
    userPassword: UserPassword
    ownerPassword: OwnerPassword
    permissions: Permissions


def _check_password(value: str) -> None:
    """Rechaza contrasenas que otro lector de PDF no interpretaria igual.

    El cifrado AES-256 de PDF deriva la clave del texto normalizado (SASLprep)
    y truncado a 127 bytes. MuPDF usa los bytes tal cual: una contrasena con
    caracteres de control, espacios especiales o formas no normalizadas
    cifraria un archivo que su dueno no podria abrir en otro programa. Se
    rechaza aqui en lugar de transformarla en silencio (A07:2025).
    """
    if unicodedata.normalize("NFKC", value) != value:
        raise invalid(_("La contraseña contiene caracteres con formas especiales que otros programas no reconocerían."))
    for ch in value:
        category = unicodedata.category(ch)
        if category in ("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp") or (category == "Zs" and ch != " "):
            raise invalid(_("La contraseña contiene caracteres no admitidos (de control o espacios especiales)."))
    if len(value.encode("utf-8")) > 127:
        raise invalid(_("La contraseña es demasiado larga."))


def _require_owner(session: DocSession) -> None:
    # Cambiar la proteccion equivale a tener todos los permisos: solo puede
    # hacerlo quien demuestra ser el propietario (A01:2025, control de acceso).
    if not session.is_owner:
        raise denied(
            _("Para cambiar la protección de este PDF hace falta su contraseña de propietario."),
            action="protect",
        )


@method("tools.protect", ProtectParams)
def tools_protect(ctx: Ctx, p: ProtectParams) -> dict[str, Any]:
    session = ctx.engine.session(p.doc)
    _require_owner(session)
    user = p.userPassword.get_secret_value()
    owner = p.ownerPassword.get_secret_value()
    _check_password(user)
    _check_password(owner)
    # Con ambas contrasenas iguales, quien abre el documento se autentica a la
    # vez como propietario y los permisos no restringen nada.
    if user and user == owner:
        raise invalid(
            _(
                "La contraseña de propietario debe ser distinta de la de apertura: "
                "si coinciden, cualquiera que abra el documento puede saltarse las restricciones y quitar la protección."
            )
        )
    bits = 0
    for name, allowed in p.permissions.model_dump(by_alias=True).items():
        if allowed:
            bits |= _PERMISSION_BITS[name]
    # El cifrado (AES-256) se aplica al guardar; hasta entonces las contrasenas
    # solo existen en memoria, nunca en la carpeta de trabajo.
    session.set_protection(Protection(user, owner, bits))
    return {"state": session.state()}


@method("tools.unprotect", DocParams)
def tools_unprotect(ctx: Ctx, p: DocParams) -> dict[str, Any]:
    session = ctx.engine.session(p.doc)
    _require_owner(session)
    session.set_protection(None, remove=True)
    return {"state": session.state()}


# ============================================================ limpieza de lo oculto

class SanitizeParams(DocParams):
    metadata: bool = True
    attachments: bool = True
    javascript: bool = True
    hiddenText: bool = True
    thumbnails: bool = True
    comments: bool = False
    links: bool = False


def _sanitize_page(
    doc: pymupdf.Document, index: int, p: SanitizeParams, removed: dict[str, int], retired: set[int]
) -> None:
    page_xref = doc.page_xref(index)
    if p.thumbnails and redaction.drop_key(doc, page_xref, "Thumb"):
        removed["thumbnails"] += 1
    if p.metadata:
        # Datos privados de la aplicacion que creo la pagina (a veces, el
        # original editable completo).
        removed["metadata"] += redaction.drop_key(doc, page_xref, "PieceInfo")
    if p.attachments:
        # Archivos asociados a la pagina. Cuentan como adjuntos: si fuesen lo
        # unico que hay y no contasen, la limpieza se daria por vacia y se
        # descartaria con ellos dentro.
        removed["attachments"] += redaction.drop_key(doc, page_xref, "AF")
    kinds = {kind for _, kind, _ in doc.page_annot_xrefs(index)}
    wanted = set()
    if p.attachments and redaction.FILE_ATTACHMENT in kinds:
        wanted.add("attachments")
    if p.comments and kinds.difference(redaction.NOT_COMMENTS, (redaction.FILE_ATTACHMENT,)):
        wanted.add("comments")
    if p.links and redaction.LINK in kinds:
        wanted.add("links")
    if not wanted and not p.hiddenText:
        return  # sin cargar la pagina
    page = doc[index]
    if "attachments" in wanted or "comments" in wanted:
        # Las marcas de censura pendientes no son comentarios: se conservan.
        # Los archivos adjuntos solo se quitan si se piden.
        for xref, kind, _ in page.annot_xrefs():
            if kind in redaction.NOT_COMMENTS:
                continue
            target = "attachments" if kind == redaction.FILE_ATTACHMENT else "comments"
            # Borrar una anotacion se lleva sus respuestas: puede que esta ya no exista.
            if target not in wanted or xref not in {a[0] for a in page.annot_xrefs()}:
                continue
            annot = page.load_annot(xref)
            if annot is not None:
                page.delete_annot(annot)
                removed[target] += 1
    if "links" in wanted:
        for link in page.get_links():
            page.delete_link(link)
            removed["links"] += 1
    if p.hiddenText:
        removed["hiddenText"] += redaction.remove_hidden_text(doc, page, retired)


@method("tools.sanitize", SanitizeParams)
def tools_sanitize(ctx: Ctx, p: SanitizeParams) -> dict[str, Any]:
    """Elimina informacion que no se ve pero viaja con el documento."""
    session = ctx.engine.session(p.doc)
    session.require("modify")
    removed = dict.fromkeys(("metadata", "attachments", "javascript", "hiddenText", "thumbnails", "comments", "links"), 0)
    with session.edit(_("Limpiar información oculta")) as tx:
        doc = session.doc
        count = len(session.page_ids)
        # Objetos que las paginas dejan de dibujar al quitarles el texto oculto.
        retired: set[int] = set()
        try:
            for index in range(count):
                ctx.progress(index, count + 1, _("Limpiando…"))
                _sanitize_page(doc, index, p, removed, retired)
            ctx.progress(count, count + 1, _("Limpiando…"))
            catalog = doc.pdf_catalog()
            if p.metadata:
                removed["metadata"] += redaction.drop_key(doc, catalog, "PieceInfo")
            if p.attachments:
                removed["attachments"] += redaction.drop_embedded_files(doc)
                # Archivos asociados (PDF 2.0, facturas electronicas): cuelgan
                # del catalogo ademas de estar en la lista de adjuntos.
                removed["attachments"] += redaction.drop_key(doc, catalog, "AF")
            if p.javascript or p.metadata:
                # Una sola pasada por los objetos que existen, y cancelable.
                # No se usa Document.scrub: ver redaction.scrub_objects.
                scripts, items = redaction.scrub_objects(
                    doc, javascript=p.javascript, metadata=p.metadata, tick=ctx.check_cancel
                )
                removed["javascript"] += scripts
                removed["metadata"] += items
            if p.metadata:
                # Las fotos incrustadas tal cual llevan dentro su propio EXIF
                # (posicion GPS, camara, fecha): tambien son metadatos.
                removed["metadata"] += strip_embedded_image_metadata(doc, ctx.check_cancel)
            if any(removed.values()):
                # Lo retirado no debe seguir en el documento: ni nombrado desde
                # los recursos de otra pagina ni suelto en la tabla de objetos.
                redaction.release_retired(doc, retired, tick=ctx.check_cancel)
                redaction.purge_orphans(doc, ctx.check_cancel)
        except (EngineError, MemoryError):
            raise
        except Exception as exc:  # noqa: BLE001 - MuPDF lanza tipos propios
            # Solo el tipo de error: el mensaje podria citar contenido (A09:2025).
            log.warning("fallo al limpiar el documento: %s", type(exc).__name__)
            raise redaction.failed(_("No se pudo limpiar el documento. No se ha modificado.")) from exc
        if any(removed.values()):
            # Reescritura completa: lo eliminado no debe quedar en el archivo
            # de trabajo, y una actualizacion incremental lo conservaria.
            tx.force_full = True
            tx.touch(*session.page_ids)
        else:
            tx.skip = True
    if tx.skip:
        # No habia nada que quitar: se descarta cualquier retoque en memoria.
        session.rollback()
        return session.result([], removed=removed)
    return session.result(list(session.page_ids), removed=removed)


# ================================================================ marcas de censura

class MarkParams(PageParams):
    rects: Annotated[list[Rect], Field(min_length=1, max_length=MAX_MARK_RECTS)]


class UnmarkParams(PageParams):
    ids: Annotated[list[MarkId], Field(min_length=1, max_length=MAX_MARK_RECTS)]


class FindParams(DocParams):
    query: Annotated[str, StringConstraints(min_length=1, max_length=200)] | None = None
    pattern: Literal["dni", "nie", "email", "phone", "iban", "card"] | None = None
    pages: PageList | None = None


class ApplyParams(DocParams):
    pages: PageList | None = None
    # Censura reforzada: ademas, cada pagina censurada pasa a ser una imagen de
    # como se ve (ver redaction.flatten_page).
    flatten: bool = False


def _page_selection(session: DocSession, pages: list[str] | None) -> list[str]:
    ids = list(dict.fromkeys(pages)) if pages is not None else list(session.page_ids)
    for pid in ids:
        session.page_index(pid)  # falla ya si alguna pagina no existe
    return ids


def _has_marks(doc: pymupdf.Document, index: int) -> bool:
    """La pagina tiene marcas pendientes (se mira sin cargarla)."""
    return any(kind == redaction.REDACT for _, kind, _ in doc.page_annot_xrefs(index))


@method("redact.mark", MarkParams)
def redact_mark(ctx: Ctx, p: MarkParams) -> dict[str, Any]:
    """Anade marcas de censura pendientes. No elimina nada todavia."""
    session = ctx.engine.session(p.doc)
    session.require("modify")
    marks: list[dict[str, Any]] = []
    with session.edit(_("Marcar para censurar")) as tx:
        page = session.page(p.page)
        geom = Geom(page)
        width, height = geom.size_d
        for raw in p.rects:
            # La zona se recorta a la pagina: fuera de ella no hay nada que el
            # usuario vea, y una marca gigante solo podria borrar de mas.
            x0, x1 = max(min(raw[0], raw[2]), 0.0), min(max(raw[0], raw[2]), width)
            y0, y1 = max(min(raw[1], raw[3]), 0.0), min(max(raw[1], raw[3]), height)
            if x1 - x0 < MIN_ZONE or y1 - y0 < MIN_ZONE:
                raise invalid(_("Una de las zonas queda fuera de la página o es demasiado pequeña."))
            annot = redaction.add_mark(page, geom.rect_u((x0, y0, x1, y1)))
            marks.append({"id": f"r{annot.xref}", "rect": geom.api_rect_d(annot.rect)})
        tx.touch(p.page)
    return session.result([p.page], marks=marks)


@method("redact.list", DocParams)
def redact_list(ctx: Ctx, p: DocParams) -> dict[str, Any]:
    """Marcas pendientes de todo el documento."""
    session = ctx.engine.session(p.doc)
    doc = session.doc
    marks: list[dict[str, Any]] = []
    for index, pid in enumerate(session.page_ids):
        if not _has_marks(doc, index):
            continue
        page = doc[index]
        geom = Geom(page)
        for xref, rect, _zones in redaction.page_marks(page):
            marks.append({"page": pid, "id": f"r{xref}", "rect": geom.rect_d(rect)})
    return {"marks": marks}


@method("redact.unmark", UnmarkParams)
def redact_unmark(ctx: Ctx, p: UnmarkParams) -> dict[str, Any]:
    session = ctx.engine.session(p.doc)
    session.require("modify")
    wanted = {int(mark[1:]) for mark in p.ids}
    removed = 0
    with session.edit(_("Quitar marca de censura")) as tx:
        page = session.page(p.page)
        # El identificador llega de fuera: solo se borra si corresponde a una
        # anotacion Redact de ESTA pagina, nunca a otro objeto del PDF
        # (A01:2025, referencia directa a objetos).
        for xref in [x for x, kind, _ in page.annot_xrefs() if kind == redaction.REDACT and x in wanted]:
            annot = page.load_annot(xref)
            if annot is not None:
                page.delete_annot(annot)
                removed += 1
        if removed:
            tx.touch(p.page)
        else:
            tx.skip = True
    return session.result([p.page] if removed else [], removed=removed)


@method("redact.find", FindParams)
def redact_find(ctx: Ctx, p: FindParams) -> dict[str, Any]:
    """Marca todas las apariciones de un texto o de un tipo de dato personal."""
    session = ctx.engine.session(p.doc)
    session.require("modify")
    if (p.query is None) == (p.pattern is None):
        raise invalid(_("Indica un texto o un tipo de dato que buscar, pero no los dos."))
    query = p.query.strip() if p.query is not None else None
    if query == "":
        raise invalid(_("Escribe el texto que quieres censurar."))
    ids = _page_selection(session, p.pages)
    per_page: list[dict[str, Any]] = []
    total = 0
    truncated = False
    with session.edit(_("Marcar para censurar")) as tx:
        for n, pid in enumerate(ids):
            ctx.progress(n, len(ids), _("Buscando…"))
            page = session.page(pid)
            # ``pattern`` es uno de los nombres de la lista cerrada: aqui no
            # llega nunca una expresion regular escrita por el usuario.
            rects = redaction.search_page(page, query) if query else redaction.match_page(page, p.pattern or "")
            if not rects:
                continue
            existing = [rect for _, rect, _ in redaction.page_marks(page)]
            added = 0
            for rect in rects:
                rect = rect.normalize()
                if not redaction.finite(rect) or rect.width <= 0 or rect.height <= 0:
                    continue
                # Lo que ya esta cubierto por una marca no se vuelve a marcar.
                if any(
                    e.x0 - 0.5 <= rect.x0 and e.y0 - 0.5 <= rect.y0 and e.x1 + 0.5 >= rect.x1 and e.y1 + 0.5 >= rect.y1
                    for e in existing
                ):
                    continue
                if total >= MAX_FIND_MARKS:
                    truncated = True
                    break
                redaction.add_mark(page, rect)
                existing.append(rect)
                added += 1
                total += 1
            if added:
                tx.touch(pid)
                per_page.append({"page": pid, "count": added})
            if truncated:
                break
        if not total:
            tx.skip = True
    # "perPage" y no "pages": en toda respuesta esa clave es la lista de paginas
    # del documento y la interfaz la aplicaria tal cual.
    return session.result([item["page"] for item in per_page], total=total, perPage=per_page, truncated=truncated)


@method("redact.apply", ApplyParams)
def redact_apply(ctx: Ctx, p: ApplyParams) -> dict[str, Any]:
    """Elimina de verdad lo que hay bajo las marcas pendientes."""
    session = ctx.engine.session(p.doc)
    session.require("modify")
    ids = _page_selection(session, p.pages)
    done: list[str] = []
    applied = elements = annotations = shared = 0
    widgets: list[redaction.RemovedWidget] = []
    ocr_pages: list[str] = []
    hidden_pages: list[str] = []
    flattened: list[str] = []
    also_on: list[str] = []
    structure = False
    with session.edit(_("Censurar")) as tx:
        doc = session.doc
        # Lo que las paginas censuradas dejan de dibujar, y las copias
        # filtradas que MuPDF pone en su lugar (ver redaction.release_retired).
        retired: set[int] = set()
        copies: set[int] = set()
        decoration: set[int] = set()
        try:
            for n, pid in enumerate(ids):
                ctx.progress(n, len(ids), _("Censurando…"))
                index = session.page_index(pid)
                if not _has_marks(doc, index):
                    continue
                outcome = redaction.redact_page(doc, doc[index])
                if outcome is None:
                    continue
                applied += outcome.marks
                elements += outcome.elements
                annotations += outcome.annotations
                widgets += outcome.widgets
                retired |= outcome.retired
                copies |= outcome.forms
                decoration |= outcome.decoration
                if p.flatten:
                    # Despues de censurar (la imagen ya lleva el recuadro negro).
                    _page, removed_fields = redaction.flatten_page(doc, doc[index])
                    widgets += removed_fields
                    flattened.append(pid)
                else:
                    if outcome.ocr_removed:
                        ocr_pages.append(pid)
                    if outcome.hidden_text:
                        hidden_pages.append(pid)
                tx.touch(pid)
                done.append(pid)
            if done:
                shared = redaction.prune_fields(doc, widgets)
                if widgets:
                    redaction.drop_xfa(doc)
                structure = redaction.drop_structure(doc)
                # La imagen o el formulario originales pueden seguir nombrados
                # en los recursos de otras paginas (o del arbol de paginas)
                # aunque nadie mas los dibuje: se anulan. Si otra pagina los
                # dibuja de verdad, se quedan y se avisa de en cuales.
                shown = redaction.release_retired(doc, retired, copies, decoration, tick=ctx.check_cancel)
                also_on = [session.page_ids[index] for index in shown if index < len(session.page_ids)]
                redaction.purge_orphans(doc, ctx.check_cancel)
        except (EngineError, MemoryError):
            raise
        except Exception as exc:  # noqa: BLE001 - MuPDF lanza tipos propios
            # Solo el tipo de error: el mensaje podria citar contenido (A09:2025).
            log.warning("fallo al censurar: %s", type(exc).__name__)
            raise redaction.failed(_("No se pudo censurar el documento. No se ha modificado.")) from exc
        if done:
            # Reescritura completa: una actualizacion incremental dejaria lo
            # censurado en la parte anterior del archivo de trabajo.
            tx.force_full = True
        else:
            tx.skip = True
    return session.result(
        done,
        applied=applied,
        removedObjects=elements,
        removedAnnotations=annotations,
        sharedFields=shared,
        structureRemoved=structure,
        removedOcrPages=ocr_pages,
        hiddenTextPages=hidden_pages,
        # Paginas que siguen mostrando algo de lo censurado porque dibujan el
        # mismo objeto (la misma imagen repetida, por ejemplo).
        alsoOnPages=also_on,
        # Paginas convertidas en imagen (censura reforzada): ya no tienen texto.
        flattenedPages=flattened,
    )
