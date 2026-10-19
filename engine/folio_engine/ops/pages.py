"""Organizacion de paginas (girar, mover, duplicar, insertar, extraer, dividir,
recortar) y creacion de documentos nuevos (en blanco, desde imagenes, combinados).

Por que no se usan tal cual algunas funciones de PyMuPDF (comprobado con la
version fijada en el proyecto):

* ``Document.select`` rehace el catalogo del PDF y tira el formulario, los
  adjuntos, las etiquetas de pagina, el idioma... aunque solo se cambie el
  orden. Aqui se reordena tocando unicamente el arbol de paginas.
* ``Document.fullcopy_page`` funde los flujos de contenido en uno solo (se
  pierden las etiquetas de los elementos anadidos con Folio), comparte el
  diccionario de recursos con el original y deja los campos fuera del
  formulario.
* ``move_page``, ``new_page``, ``fullcopy_page`` e ``insert_pdf`` no tienen en
  cuenta los atributos heredados del nodo del arbol donde cae la pagina
  (tamano, giro, recorte): una pagina movida o insertada puede cambiar de
  tamano o de orientacion.
* ``insert_pdf`` no copia el grupo de transparencia de la pagina (cambian los
  colores al componer) y deja en las etiquetas de los elementos un numero de
  objeto del documento de origen.
* ``delete_pages`` saca la pagina del arbol pero la deja alcanzable desde el
  formulario o el arbol de estructura, de modo que su contenido seguiria
  dentro del archivo guardado.
* ``insert_pdf`` copia cada campo de formulario con TODAS sus apariciones y
  sigue cualquier referencia: si un campo se repite en otra pagina, o un
  boton salta a ella, esa pagina (y con ella el arbol entero) viaja escondida
  en la copia. Y copia tal cual el diccionario de recursos, con imagenes de
  otras paginas que nombre aunque no las dibuje. Tras cada copia se retira
  todo eso (``_drop_stowaways`` y ``usage.release_unlisted``).
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import secrets
from pathlib import Path
from typing import Annotated, Any, Literal

import pymupdf
from pydantic import Field, StringConstraints
from pymupdf import mupdf

from .. import content, redaction, usage
from ..errors import EngineError, denied, invalid, io_error, not_found
from ..geometry import Geom
from ..i18n import _
from ..imaging import MAX_IMAGE_BYTES, images_to_pdf, sniff
from ..models import DocParams, PageId, Params, PathStr, Rect, Secret
from ..paths import safe_filename
from ..server import Ctx, method
from ..session import _PERMISSION_BITS, DocSession, _harden
from .document import describe

log = logging.getLogger("folio.engine.pages")

# Topes de recursos: ninguna peticion puede pedir un trabajo sin limite
# (paginas, archivos generados o memoria), A06:2025.
MAX_PAGES = 100_000
MAX_NEW_PAGES = 500
MAX_SPLIT_FILES = 10_000
MAX_RANGES = 2_000
MAX_INPUT_BYTES = 2 * 1024**3
MAX_OUTLINE_ITEMS = 20_000
MAX_OUTLINE_LEVEL = 30
MAX_TREE_DEPTH = 64
MAX_NAMES = 20_000

# Limites de tamano de pagina del formato PDF (ISO 32000, anexo C).
MIN_SIDE = 3.0
MAX_SIDE = 14_400.0
A4 = (595.276, 841.89)

_ALL_PERMISSIONS = 0
for _bit in _PERMISSION_BITS.values():
    _ALL_PERMISSIONS |= _bit

_N = pymupdf.PDF_NAME
_TAG_NAME = re.compile(r"^[A-Za-z0-9_.\-]{1,60}$")
_FIRST_REF = re.compile(r"^\[\s*(\d{1,10})\s+\d{1,5}\s+R")
_INHERITABLE = ("MediaBox", "CropBox", "Rotate", "Resources")
# Claves del formulario que describen al documento entero, no a sus paginas.
_FOREIGN_FORM_KEYS = ("XFA", "SigFlags")

# Todo campo llega acotado en tipo, rango y longitud (A05:2025). Los enteros
# son estrictos: no se aceptan cadenas, decimales ni booleanos en su lugar.
PageList = Annotated[list[PageId], Field(min_length=1, max_length=MAX_PAGES)]
PageIndex = Annotated[int, Field(strict=True, ge=0, lt=MAX_PAGES)]
Side = Annotated[float, Field(ge=MIN_SIDE, le=MAX_SIDE, allow_inf_nan=False)]
NewPageCount = Annotated[int, Field(strict=True, ge=1, le=MAX_NEW_PAGES)]


# ------------------------------------------------------------------ utilidades


def _pdf(doc: pymupdf.Document) -> Any:
    return pymupdf._as_pdf_document(doc)


def _page_obj(doc: pymupdf.Document, xref: int) -> Any:
    return mupdf.pdf_new_indirect(_pdf(doc), xref, 0)


def _batches(items: list[Any], size: int = 512) -> Any:
    """Trozos de una lista.

    Nota de rendimiento que explica varios bucles de este modulo: en cuanto se
    modifica un objeto del PDF, MuPDF descarta su indice de paginas y la
    siguiente busqueda de una pagina por su numero vuelve a recorrer el
    documento entero. Por eso primero se localizan o cargan todas las paginas
    de un trozo y solo despues se modifican; intercalarlo haria que el coste
    creciera con el cuadrado del numero de paginas.
    """
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _plural(count: int, one: str, many: str) -> str:
    return one if count == 1 else many


def _distinct(items: list[Any]) -> list[Any]:
    # Una pagina repetida haria ambigua la operacion (¿girarla dos veces?) y al
    # copiar dejaria dos paginas compartiendo los mismos objetos: se rechaza.
    if len(set(items)) != len(items):
        raise invalid(_("La lista de páginas contiene elementos repetidos."))
    return list(items)


def _positions(session: DocSession, ids: list[str]) -> dict[str, int]:
    """Indice actual de cada pagina pedida; falla si alguna ya no existe."""
    index = {pid: n for n, pid in enumerate(session.page_ids)}
    out: dict[str, int] = {}
    for pid in ids:
        if pid not in index:
            raise not_found(_("La página ya no existe."))
        out[pid] = index[pid]
    return out


def _insertion_index(session: DocSession, before: str | None) -> int:
    return len(session.page_ids) if before is None else session.page_index(before)


def _check_page_total(total: int) -> None:
    if total > MAX_PAGES:
        raise invalid(_("El documento tendría demasiadas páginas."), max=MAX_PAGES)


def _check_consistent(session: DocSession, page_ids: list[str]) -> None:
    # Si la lista de identificadores y el PDF dejaran de cuadrar, cada id
    # apuntaria a otra pagina: mejor abortar y que la transaccion revierta (A10:2025).
    if session.doc.page_count != len(page_ids):
        raise EngineError("internal", _("No se pudo reorganizar el documento."))


def _require_unrestricted(session: DocSession) -> None:
    """Sacar paginas a otro archivo solo se permite con acceso completo al PDF.

    El archivo generado no lleva el cifrado ni las restricciones del original,
    asi que permitirlo con acceso limitado seria una forma de saltarselas
    (control de acceso, A01:2025).
    """
    if not all(session.allowed(name) for name in _PERMISSION_BITS):
        raise denied(
            _(
                "El autor de este PDF no permite extraer sus páginas a otro archivo. "
                "Introduce la contraseña de propietario para desbloquearlo."
            ),
            action="extract",
        )


def _protection_dropped(session: DocSession) -> bool:
    """Los archivos que se saquen del documento saldran SIN la proteccion que este tiene.

    Las paginas extraidas o divididas se escriben sin cifrar. Si el documento
    esta cifrado (y no se ha pedido quitarle la proteccion) o tiene una
    proteccion pendiente de guardar, la copia sale menos protegida que el
    original: se dice en el resultado para que la interfaz avise.
    """
    return session.protection is not None or (session.encrypted and not session.remove_protection)


def _refuse_open_document(ctx: Ctx, dest: str) -> None:
    # Escribir encima del archivo de un documento abierto lo dejaria en disco
    # con otro contenido sin que la sesion lo sepa: se pide otro nombre.
    for session in ctx.engine.sessions.values():
        if session.source is not None and ctx.engine.host.same_file(dest, session.source):
            raise invalid(_("Ese archivo está abierto en Folio. Elige otro nombre para la copia."))


def _fetch(ctx: Ctx, raw: str, *, max_bytes: int, **detail: Any) -> tuple[Path, Any]:
    """Copia privada de un archivo del usuario (ver ``host.fetch``). Quien la pide la borra."""
    local = ctx.engine.work_root / f"entrada-{secrets.token_hex(8)}"
    try:
        return local, ctx.engine.host.fetch(raw, local, max_bytes=max_bytes)
    except EngineError as exc:
        raise EngineError(exc.code, exc.message, **{**exc.detail, **detail}) from exc


def _stamp(doc: pymupdf.Document) -> None:
    now = pymupdf.get_pdf_now()
    doc.set_metadata({"producer": "Folio", "creator": "Folio", "creationDate": now, "modDate": now})


# ------------------------------------------------------------ arbol de paginas


def _reorder_pages(doc: pymupdf.Document, order: list[int]) -> None:
    """Deja en el documento las paginas ``order`` (indices actuales), en ese orden.

    Solo se reescribe el arbol de paginas: todas pasan a colgar del nodo raiz
    y antes se copia en cada una lo que heredaba de sus nodos intermedios, de
    modo que conserva tamano, giro, recorte y recursos. El resto del catalogo
    (formulario, marcadores, adjuntos...) no se toca.
    """
    pdf = _pdf(doc)
    root = mupdf.pdf_dict_get(mupdf.pdf_dict_get(mupdf.pdf_trailer(pdf), _N("Root")), _N("Pages"))
    if not mupdf.pdf_is_dict(root):
        raise EngineError("internal", _("No se pudo reorganizar el documento."))
    # Todas las referencias se resuelven antes de tocar nada.
    refs = [mupdf.pdf_lookup_page_obj(pdf, index) for index in order]
    kids = mupdf.pdf_new_array(pdf, len(refs))
    parent_key = _N("Parent")
    for ref in refs:
        if not mupdf.pdf_is_dict(ref):
            raise EngineError("internal", _("No se pudo reorganizar el documento."))
        for key in _INHERITABLE:
            name = _N(key)
            if not mupdf.pdf_dict_get(ref, name).m_internal:
                value = mupdf.pdf_dict_get_inheritable(ref, name)
                if value.m_internal:
                    mupdf.pdf_dict_put(ref, name, mupdf.pdf_deep_copy_obj(value))
        # Solo se escribe lo que cambia: asi cada reordenacion anade al archivo
        # de trabajo poco mas que la lista de paginas.
        if mupdf.pdf_objcmp(mupdf.pdf_dict_get(ref, parent_key), root) != 0:
            mupdf.pdf_dict_put(ref, parent_key, root)
        mupdf.pdf_array_push(kids, ref)
    mupdf.pdf_dict_put(root, _N("Kids"), kids)
    mupdf.pdf_dict_put_int(root, _N("Count"), len(refs))
    mupdf.ll_pdf_drop_page_tree(pdf.m_internal)
    doc._reset_page_refs()


def _absent(value: Any) -> bool:
    return not value.m_internal or bool(mupdf.pdf_is_null(value))


def _pin_page(doc: pymupdf.Document, xref: int) -> None:
    """Fija en la pagina lo que de otro modo heredaria del nodo donde se ha insertado."""
    page = _page_obj(doc, xref)
    media = mupdf.pdf_dict_get(page, _N("MediaBox"))
    if _absent(media):
        mupdf.pdf_dict_put_rect(page, _N("MediaBox"), mupdf.FzRect(0, 0, 612, 792))  # lo que MuPDF supone si falta
        media = mupdf.pdf_dict_get(page, _N("MediaBox"))
    if _absent(mupdf.pdf_dict_get(page, _N("CropBox"))):
        mupdf.pdf_dict_put(page, _N("CropBox"), mupdf.pdf_deep_copy_obj(media))
    if _absent(mupdf.pdf_dict_get(page, _N("Rotate"))):
        mupdf.pdf_dict_put_int(page, _N("Rotate"), 0)
    if _absent(mupdf.pdf_dict_get(page, _N("Resources"))):
        mupdf.pdf_dict_put_dict(page, _N("Resources"), 1)


# ------------------------------------------------------------- copia de paginas


def _content_xrefs(page: Any) -> list[int]:
    """Flujos de contenido de un objeto de pagina (lo mismo que ``Page.get_contents``, sin cargarla)."""
    contents = mupdf.pdf_dict_get(page, _N("Contents"))
    if mupdf.pdf_is_array(contents):
        found = [mupdf.pdf_to_num(mupdf.pdf_array_get(contents, i)) for i in range(mupdf.pdf_array_len(contents))]
    elif contents.m_internal:
        found = [mupdf.pdf_to_num(contents)]
    else:
        found = []
    return [xref for xref in found if xref > 0]


def _xobject_names(page: Any) -> dict[str, int]:
    """Nombre -> numero de objeto de los XObject que la pagina declara en sus recursos."""
    out: dict[str, int] = {}
    xobjects = mupdf.pdf_dict_get(mupdf.pdf_dict_get_inheritable(page, _N("Resources")), _N("XObject"))
    if mupdf.pdf_is_dict(xobjects):
        for i in range(min(mupdf.pdf_dict_len(xobjects), MAX_NAMES)):
            number = mupdf.pdf_to_num(mupdf.pdf_dict_get_val(xobjects, i))
            if number > 0:
                out[mupdf.pdf_to_name(mupdf.pdf_dict_get_key(xobjects, i))] = number
    return out


def _retag(doc: pymupdf.Document, page_xref: int, fresh_ids: bool) -> None:
    """Pone al dia las etiquetas de los elementos de Folio de una pagina recien copiada.

    La etiqueta guarda el numero de objeto del XObject, que cambia al copiar a
    otro documento; con el antiguo dejaria de reconocerse el elemento. Con
    ``fresh_ids`` cada elemento recibe ademas un identificador nuevo, para que
    la copia y su original no compartan identidad dentro del mismo documento.

    La etiqueta viene dentro de un PDF, es decir, es un dato no fiable: se
    valida antes de tocarla y, si no cuadra, se deja como esta (A08:2025).
    """
    page = _page_obj(doc, page_xref)
    names: dict[str, int] | None = None
    for stream in _content_xrefs(page):
        try:
            kind, value = doc.xref_get_key(stream, content.TAG_KEY)
        except Exception:  # noqa: BLE001 - flujo ilegible: no es un elemento de Folio
            continue
        if kind != "string" or not value or len(value) > content.MAX_TAG_HEX:
            continue
        try:
            data = json.loads(bytes.fromhex(value).decode("utf-8"))
        except (ValueError, RecursionError):
            continue
        if not isinstance(data, dict) or data.get("v") != content.TAG_VERSION:
            continue
        name = data.get("n")
        if not isinstance(name, str) or not _TAG_NAME.match(name):
            continue
        if names is None:
            names = _xobject_names(page)
        xref = names.get(name)
        if xref is None:
            continue
        data["x"] = xref
        if fresh_ids:
            data["id"] = content.new_object_id()
        raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        doc.xref_set_key(stream, content.TAG_KEY, "(" + raw.hex() + ")")


def _form_dict(doc: pymupdf.Document) -> Any:
    return mupdf.pdf_dict_getp(mupdf.pdf_trailer(_pdf(doc)), "Root/AcroForm")


def _form_keys(doc: pymupdf.Document) -> set[str]:
    form = _form_dict(doc)
    if not mupdf.pdf_is_dict(form):
        return set()
    return {key for key in _FOREIGN_FORM_KEYS if mupdf.pdf_dict_gets(form, key).m_internal}


def _carry_groups(dst: pymupdf.Document, src: pymupdf.Document, xrefs: list[int], first: int) -> None:
    """Copia el grupo de transparencia de cada pagina, que ``insert_pdf`` omite.

    ``xrefs`` son los objetos de las paginas ya copiadas a ``dst``; sus
    originales son las paginas de ``src`` a partir de ``first``.
    """
    dpdf, spdf = _pdf(dst), _pdf(src)
    group_key = _N("Group")
    graft = None
    try:
        for k, xref in enumerate(xrefs):
            group = mupdf.pdf_dict_get(mupdf.pdf_lookup_page_obj(spdf, first + k), group_key)
            if not mupdf.pdf_is_dict(group):
                continue
            if graft is None:
                graft = mupdf.pdf_new_graft_map(dpdf)  # un solo mapa: lo comun (perfil de color) se copia una vez
            copy = mupdf.pdf_graft_mapped_object(graft, group)
            mupdf.pdf_dict_put(mupdf.pdf_new_indirect(dpdf, xref, 0), group_key, copy)
    finally:
        graft = None  # se suelta antes de que nadie cierre los documentos


def _prepare_source(src: pymupdf.Document) -> None:
    """Quita de las anotaciones y campos de ``src`` la referencia a su pagina (/P).

    ``src`` es siempre una copia desechable de la que se van a copiar paginas.
    ``insert_pdf`` copia cada campo con todas sus apariciones, tambien las de
    paginas que no se copian, y por su /P se llevaria esas paginas enteras.
    Quitarlo antes evita cargar con ellas (lo que aun asi se cuele lo retira
    ``_drop_stowaways``). /P es opcional y aqui nadie lo usa.
    """
    pdf = _pdf(src)
    annots_key, p_key = _N("Annots"), _N("P")
    found: list[Any] = []
    # Primero se localiza todo y despues se modifica (ver _batches).
    for index in range(src.page_count):
        annots = mupdf.pdf_dict_get(mupdf.pdf_lookup_page_obj(pdf, index), annots_key)
        if not mupdf.pdf_is_array(annots):
            continue
        for i in range(min(mupdf.pdf_array_len(annots), MAX_NAMES)):
            annot = mupdf.pdf_array_get(annots, i)
            if mupdf.pdf_is_dict(annot) and mupdf.pdf_dict_get(annot, p_key).m_internal:
                found.append(annot)
    for annot in found:
        mupdf.pdf_dict_del(annot, p_key)


def _looks_like_page(obj: Any) -> bool:
    """El diccionario es una pagina o un nodo del arbol de paginas, lleve o no su /Type."""
    kind = mupdf.pdf_dict_get(obj, _N("Type"))
    if mupdf.pdf_is_name(kind) and mupdf.pdf_to_name(kind) in ("Page", "Pages"):
        return True
    contents = mupdf.pdf_dict_get(obj, _N("Contents"))
    if contents.m_internal and (mupdf.pdf_is_stream(contents) or mupdf.pdf_is_array(contents)):
        return True  # en una anotacion /Contents es una cadena; en una pagina, flujos
    kids, count = mupdf.pdf_dict_get(obj, _N("Kids")), mupdf.pdf_dict_get(obj, _N("Count"))
    return bool(mupdf.pdf_is_array(kids) and count.m_internal and mupdf.pdf_is_int(count))


def _new_closure(doc: pymupdf.Document, floor: int, roots: list[Any]) -> set[int]:
    """Objetos con numero ``floor`` o mayor alcanzables desde ``roots`` sin pasar por objetos anteriores."""
    seen: set[int] = set()
    stack = list(roots)
    while stack:
        obj = stack.pop()
        if mupdf.pdf_is_indirect(obj):
            num = mupdf.pdf_to_num(obj)
            if num < floor or num in seen:
                continue
            seen.add(num)
            obj = mupdf.pdf_resolve_indirect(obj)
        if mupdf.pdf_is_dict(obj):
            stack.extend(mupdf.pdf_dict_get_val(obj, i) for i in range(mupdf.pdf_dict_len(obj)))
        elif mupdf.pdf_is_array(obj):
            stack.extend(mupdf.pdf_array_get(obj, i) for i in range(mupdf.pdf_array_len(obj)))
    return seen


def _drop_stowaways(dst: pymupdf.Document, floor: int, page_xrefs: list[int]) -> int:
    """Retira lo que una copia de paginas ha traido de polizon; devuelve cuantas paginas ocultas habia.

    Tras ``insert_pdf`` pueden quedar en ``dst``, fuera del arbol pero
    alcanzables, paginas del origen que nadie pidio (las arrastra un campo que
    tambien aparece en ellas, un boton «ir a la pagina N» u otra referencia).
    Su contenido viajaria oculto en el archivo y bastaria volver a colgarlas
    del arbol para leerlas (A01:2025).

    Entre los objetos nuevos (numero ``floor`` o mayor) se anula toda pagina o
    nodo de paginas que no este entre las copiadas y todo campo sin sitio en
    ellas. Lo que solo se alcanzaba a traves de esas paginas se libera: no debe
    quedar ni como objeto suelto.
    """
    pdf = _pdf(dst)
    tree = set(page_xrefs)
    placed: set[int] = set()
    for xref in page_xrefs:
        annots = mupdf.pdf_dict_get(_page_obj(dst, xref), _N("Annots"))
        if mupdf.pdf_is_array(annots):
            for i in range(mupdf.pdf_array_len(annots)):
                placed.add(mupdf.pdf_to_num(mupdf.pdf_array_get(annots, i)))
    hidden: list[int] = []
    loose: list[int] = []
    for num in range(max(floor, 1), dst.xref_length()):
        if num in tree:
            continue
        try:
            obj = mupdf.pdf_load_object(pdf, num)
        except Exception:  # noqa: BLE001 - entrada libre o ilegible: no es una pagina
            continue
        if not mupdf.pdf_is_dict(obj) or mupdf.pdf_is_stream(obj):
            continue
        if _looks_like_page(obj):
            hidden.append(num)
        elif num not in placed:
            subtype = mupdf.pdf_dict_get(obj, _N("Subtype"))
            if mupdf.pdf_is_name(subtype) and mupdf.pdf_to_name(subtype) == "Widget":
                loose.append(num)
    if not hidden and not loose:
        return 0
    # Lo que cuelga de las paginas ocultas, visto antes de anularlas.
    carried = _new_closure(dst, floor, [mupdf.pdf_new_indirect(pdf, num, 0) for num in hidden])
    kids_key, parent_key = _N("Kids"), _N("Parent")
    for num in loose:
        # Aparicion de un campo en una pagina que no se ha copiado: se
        # descuelga de su campo (que conserva las apariciones copiadas).
        node = mupdf.pdf_new_indirect(pdf, num, 0)
        kids = mupdf.pdf_dict_get(mupdf.pdf_dict_get(node, parent_key), kids_key)
        if mupdf.pdf_is_array(kids):
            at = mupdf.pdf_array_find(kids, node)
            if at >= 0:
                mupdf.pdf_array_delete(kids, at)
    for num in hidden + loose:
        dst.update_object(num, "null")
    # Lo que sigue haciendo falta: las paginas copiadas y el formulario.
    roots = [mupdf.pdf_new_indirect(pdf, xref, 0) for xref in page_xrefs]
    form = _form_dict(dst)
    if mupdf.pdf_is_dict(form):
        roots.extend(mupdf.pdf_dict_get_val(form, i) for i in range(mupdf.pdf_dict_len(form)))
        for key in ("Fields", "CO"):  # aunque la lista sea un objeto anterior, sus campos pueden ser nuevos
            items = mupdf.pdf_dict_gets(form, key)
            if mupdf.pdf_is_array(items):
                roots.extend(mupdf.pdf_array_get(items, i) for i in range(mupdf.pdf_array_len(items)))
    needed = _new_closure(dst, floor, roots)
    for num in sorted(carried - needed - set(hidden)):
        mupdf.pdf_delete_object(pdf, num)
    return len(hidden)


def _import_pages(
    dst: pymupdf.Document,
    src: pymupdf.Document,
    at: int,
    *,
    first: int = 0,
    last: int | None = None,
    fresh_ids: bool,
    pin: bool,
    prune: bool = False,
) -> int:
    """Copia a ``dst``, a partir del indice ``at``, las paginas ``first..last`` de ``src``.

    Se copia en una sola llamada para que los enlaces entre las paginas copiadas
    y los campos con varias apariciones se resuelvan juntos. Conviene que
    ``src`` haya pasado por ``_prepare_source`` (evita copiar de mas); lo que
    aun asi venga de polizon se retira aqui. Con ``prune`` se anula ademas lo
    que las paginas copiadas tienen en sus recursos sin dibujarlo (cuando solo
    se copia parte de un documento, ahi van las imagenes de las que se quedan).
    """
    if last is None:
        last = src.page_count - 1
    count = last - first + 1
    own_form = _form_keys(dst)
    floor = dst.xref_length()
    dst.insert_pdf(src, from_page=first, to_page=last, start_at=at, links=True, annots=True, widgets=True)
    xrefs = [dst.page_xref(at + k) for k in range(count)]  # todas antes de modificar nada (ver _batches)
    # Si el destino no tenia formulario, insert_pdf le copia el del origen con
    # su paquete XFA (los datos de TODOS los campos del original, tambien los de
    # paginas que no se copian) y sus indicadores de firma. Nada de eso es de
    # este documento: se retira (A06:2025).
    form = _form_dict(dst)
    if mupdf.pdf_is_dict(form):
        for key in _FOREIGN_FORM_KEYS:
            if key not in own_form and mupdf.pdf_dict_gets(form, key).m_internal:
                mupdf.pdf_dict_dels(form, key)
    _carry_groups(dst, src, xrefs, first)
    for xref in xrefs:
        if pin:
            _pin_page(dst, xref)
        _retag(dst, xref, fresh_ids)
    _drop_stowaways(dst, floor, xrefs)
    if prune:
        try:
            usage.release_unlisted(dst, [_page_obj(dst, xref) for xref in xrefs], floor=floor)
        except usage.TooDeep:
            # Contenido anidado mas de lo razonable: no se puede saber que se
            # dibuja, asi que no se anula nada (nunca se quita lo que se usa).
            log.warning("no se pudieron retirar los recursos sin uso de las paginas copiadas")
    return count


def _own_resources(doc: pymupdf.Document, source: int, target: int) -> None:
    """Da a la pagina ``target`` una copia propia de los diccionarios de recursos de ``source``.

    Las dos paginas se indican por su numero de objeto. Se copian los
    diccionarios (que nombre apunta a que objeto), no las fuentes ni las
    imagenes, que siguen compartidas. Con diccionarios propios, quitar o anadir
    un elemento en una pagina no altera la otra.
    """
    origin = _page_obj(doc, source)
    copy = _page_obj(doc, target)
    resources = mupdf.pdf_dict_get_inheritable(origin, _N("Resources"))
    if mupdf.pdf_is_dict(resources):
        own = mupdf.pdf_deep_copy_obj(mupdf.pdf_resolve_indirect(resources))
        keys = [mupdf.pdf_dict_get_key(own, i) for i in range(mupdf.pdf_dict_len(own))]
        for key in keys:
            value = mupdf.pdf_dict_get(own, key)
            if mupdf.pdf_is_indirect(value) and mupdf.pdf_is_dict(value) and not mupdf.pdf_is_stream(value):
                mupdf.pdf_dict_put(own, key, mupdf.pdf_deep_copy_obj(mupdf.pdf_resolve_indirect(value)))
        mupdf.pdf_dict_put(copy, _N("Resources"), own)
    else:
        mupdf.pdf_dict_put_dict(copy, _N("Resources"), 1)
    group = mupdf.pdf_dict_get(origin, _N("Group"))
    if mupdf.pdf_is_dict(group):
        mupdf.pdf_dict_put(copy, _N("Group"), mupdf.pdf_deep_copy_obj(mupdf.pdf_resolve_indirect(group)))


def _links_to_clone(doc: pymupdf.Document, index: int) -> list[int]:
    """Enlaces de la pagina ``index`` que ``insert_pdf`` no lleva a su copia.

    ``insert_pdf`` solo recrea los enlaces cuyo destino esta entre las paginas
    copiadas. Al duplicar dentro del mismo documento el resto de destinos
    siguen existiendo, asi que esos enlaces se pueden copiar tal cual.
    """
    wanted = []
    for link in doc[index].get_links():
        kind = link.get("kind")
        other_page = kind == pymupdf.LINK_GOTO and link.get("page") != index
        if (other_page or kind == pymupdf.LINK_NAMED) and isinstance(link.get("xref"), int) and link["xref"] > 0:
            wanted.append(link["xref"])
    return wanted


def _clone_links(doc: pymupdf.Document, wanted: list[int], target: int) -> None:
    """Copia las anotaciones de enlace ``wanted`` a la pagina cuyo objeto es ``target``."""
    if not wanted:
        return
    pdf = _pdf(doc)
    page = _page_obj(doc, target)
    annots = mupdf.pdf_dict_get(page, _N("Annots"))
    if not mupdf.pdf_is_array(annots):
        annots = mupdf.pdf_dict_put_array(page, _N("Annots"), len(wanted))
    for xref in wanted:
        original = mupdf.pdf_resolve_indirect(mupdf.pdf_new_indirect(pdf, xref, 0))
        if not mupdf.pdf_is_dict(original):
            continue
        clone = mupdf.pdf_deep_copy_obj(original)
        mupdf.pdf_dict_put(clone, _N("P"), page)
        mupdf.pdf_array_push(annots, mupdf.pdf_add_object(pdf, clone))


def _duplicate_page(doc: pymupdf.Document, snapshot: pymupdf.Document, index: int) -> None:
    """Inserta en ``index + 1`` una copia independiente de la pagina ``index``.

    La copia se trae de ``snapshot`` (el mismo documento, abierto aparte), que
    es el camino con el que PyMuPDF copia bien anotaciones y campos. Para no
    duplicar fuentes e imagenes, a esa pagina de ``snapshot`` se le han vaciado
    antes los recursos; aqui se enlazan despues los del original.
    """
    floor = doc.xref_length()
    doc.insert_pdf(snapshot, from_page=index, to_page=index, start_at=index + 1, links=True, annots=True, widgets=True)
    # Primero todo lo que hay que localizar; despues, las modificaciones (ver _batches).
    origin, copy = doc.page_xref(index), doc.page_xref(index + 1)
    links = _links_to_clone(doc, index)
    _own_resources(doc, origin, copy)
    _pin_page(doc, copy)
    _clone_links(doc, links, copy)
    _retag(doc, copy, fresh_ids=True)
    # Un campo repetido en otras paginas traeria copias ocultas de ellas: lo
    # que despues se censurase o borrase en la pagina original seguiria ahi.
    _drop_stowaways(doc, floor, [copy])


# ---------------------------------------------------------------- borrar paginas


def _prune_empty_fields(doc: pymupdf.Document, parents: set[int]) -> None:
    """Retira del formulario los campos que se han quedado sin ninguna aparicion en pagina."""
    if not parents:
        return
    pdf = _pdf(doc)
    fields = mupdf.pdf_dict_getp(mupdf.pdf_trailer(pdf), "Root/AcroForm/Fields")
    kids_key, parent_key = _N("Kids"), _N("Parent")
    for xref in parents:
        node = mupdf.pdf_new_indirect(pdf, xref, 0)
        for _ in range(MAX_TREE_DEPTH):
            if not mupdf.pdf_is_dict(node):
                break
            kids = mupdf.pdf_dict_get(node, kids_key)
            if not mupdf.pdf_is_array(kids) or mupdf.pdf_array_len(kids) > 0:
                break
            up = mupdf.pdf_dict_get(node, parent_key)
            holder = mupdf.pdf_dict_get(up, kids_key) if mupdf.pdf_is_dict(up) else fields
            if mupdf.pdf_is_array(holder):
                found = mupdf.pdf_array_find(holder, node)
                if found >= 0:
                    mupdf.pdf_array_delete(holder, found)
            number = mupdf.pdf_to_num(node)
            if number > 0:
                doc.update_object(number, "null")  # su valor no debe quedar en el archivo
            node = up


def _targets(doc: pymupdf.Document, xref: int, gone: set[int]) -> bool:
    """El marcador o enlace ``xref`` salta, con destino explicito, a una de las paginas ``gone``."""
    for key in ("Dest", "A/D"):
        kind, value = doc.xref_get_key(xref, key)
        if kind == "array":
            first = _FIRST_REF.match(value)
            if first and int(first.group(1)) in gone:
                return True
    return False


def _drop_references(doc: pymupdf.Document, gone: set[int]) -> None:
    """Deja sin destino los marcadores y enlaces que apuntaban a paginas ya retiradas.

    MuPDF resuelve un destino que ya no esta en el documento como "primera
    pagina": un marcador de una pagina borrada llevaria a otra distinta.
    """
    try:
        outline = doc.get_outline_xrefs()
    except Exception:  # noqa: BLE001 - marcadores ilegibles: no hay nada que corregir en ellos
        outline = []
    for xref in outline[:MAX_OUTLINE_ITEMS]:
        if _targets(doc, xref, gone):
            doc.xref_set_key(xref, "Dest", "null")
            doc.xref_set_key(xref, "A", "null")
    for n in range(doc.page_count):
        for item in doc.page_annot_xrefs(n):
            if item[1] == pymupdf.PDF_ANNOT_LINK and _targets(doc, item[0], gone):
                doc.xref_set_key(item[0], "Dest", "null")
                doc.xref_set_key(item[0], "A", "null")


def _int_key(doc: pymupdf.Document, xref: int, key: str) -> int | None:
    kind, value = doc.xref_get_key(xref, key)
    try:
        return int(value) if kind == "int" else None
    except ValueError:
        return None


def _delete_pages(doc: pymupdf.Document, indices: list[int]) -> dict[str, bool]:
    """Elimina paginas de forma que su contenido no pueda quedar dentro del archivo guardado.

    Al guardar, el archivo se reescribe solo con lo alcanzable desde el
    catalogo. ``delete_pages`` saca la pagina del arbol, pero sigue siendo
    alcanzable si la nombran el formulario (sus campos), el arbol de
    estructura o una accion de apertura: el texto, las anotaciones y los
    valores de los campos de una pagina "borrada" seguirian en el PDF que el
    usuario envia. Por eso ademas (A06:2025):

    * se desvinculan sus campos y se anulan el objeto de pagina y sus
      anotaciones;
    * se anula lo que la pagina dibujaba y ya no dibuja ninguna otra, aunque
      siga nombrado en unos recursos compartidos con las que se quedan (ver
      ``usage.py``);
    * si se retiran campos, se quita la copia XFA del formulario, que guarda en
      XML los valores de todos;
    * si la pagina formaba parte del arbol de estructura, este se retira
      entero, igual que al censurar: sus nodos repiten el texto de la pagina
      (/Alt, /ActualText...) y apuntan a sus formularios, y no hay una lista
      cerrada de claves que vigilar.

    Devuelve que se ha retirado del documento: ``{"structure": ..., "xfa": ...}``.
    """
    pdf = _pdf(doc)
    doomed = set(indices)
    xrefs = [doc.page_xref(n) for n in range(doc.page_count)]
    # Un mismo objeto de pagina puede aparecer dos veces en el arbol: mientras
    # quede una aparicion no se toca nada suyo.
    kept = {xref for n, xref in enumerate(xrefs) if n not in doomed}
    gone: list[int] = []
    seen: set[int] = set()
    annots: list[int] = []
    parents: set[int] = set()
    pending: list[pymupdf.Page] = []
    drawn_map = usage.ContentMap(doc)
    retired: set[int] = set()
    tagged = False
    # Primera pasada, solo de lectura (ver _batches): que hay en cada pagina.
    for n in indices:
        page_xref = xrefs[n]
        if page_xref in kept or page_xref in seen:
            continue
        seen.add(page_xref)
        gone.append(page_xref)
        page = doc[n]
        drawn = drawn_map.page(mupdf.pdf_lookup_page_obj(pdf, n), used_only=True)
        retired |= drawn
        tagged = tagged or _int_key(doc, page_xref, "StructParents") is not None
        widgets = False
        for item in page.annot_xrefs():
            annots.append(item[0])
            tagged = tagged or _int_key(doc, item[0], "StructParent") is not None
            if item[1] == pymupdf.PDF_ANNOT_WIDGET:
                widgets = True
                kind, value = doc.xref_get_key(item[0], "Parent")
                if kind == "xref":
                    parents.add(int(value.split()[0]))
        if not tagged:  # contenido etiquetado dentro de un formulario de la pagina
            tagged = any(_int_key(doc, xref, "StructParents") is not None for xref in drawn)
        if widgets:
            pending.append(page)
    had_fields = bool(pending)
    # Segunda pasada: se desvincula lo que tenian las paginas que tenian algo.
    for page in pending:
        for widget in list(page.widgets()):
            page.delete_widget(widget)  # MuPDF lo saca tambien del formulario
    pending.clear()

    total = doc.page_count
    try:
        doc.delete_pages(indices)  # tambien desactiva marcadores y enlaces que apuntaban a ellas
    except Exception:  # noqa: BLE001 - p. ej. marcadores danados que PyMuPDF no sabe recorrer
        if doc.page_count != total:
            raise  # se quedo a medias: que la transaccion revierta
        log.warning("delete_pages fallo; las paginas se retiran directamente del arbol")
        _reorder_pages(doc, [n for n in range(total) if n not in doomed])
        _drop_references(doc, set(gone))

    if annots:
        # Una anotacion compartida con una pagina que se queda (PDF mal formado) no se anula.
        still_used: set[int] = set()
        for n in range(doc.page_count):
            still_used.update(item[0] for item in doc.page_annot_xrefs(n))
        for xref in annots:
            if xref not in still_used:
                doc.update_object(xref, "null")
    for xref in gone:
        doc.update_object(xref, "null")
    _prune_empty_fields(doc, parents)
    out = {"structure": False, "xfa": False}
    if had_fields:
        out["xfa"] = redaction.drop_xfa(doc)
    if tagged:
        out["structure"] = redaction.drop_structure(doc)
    # Lo que dibujaban las paginas borradas y no dibuja ninguna de las que
    # quedan: si sigue alcanzable (recursos compartidos), se anula.
    try:
        usage.release_undrawn(doc, retired)
    except usage.TooDeep:
        # Contenido anidado mas de lo razonable: no se puede saber que dibujan
        # las paginas que quedan, asi que no se anula nada.
        log.warning("no se pudo comprobar si quedan recursos de las paginas borradas")
    return out


# ------------------------------------------------------------- abrir y escribir


def _snapshot(session: DocSession) -> pymupdf.Document:
    """Copia en memoria del estado confirmado, independiente del documento de la sesion."""
    doc = pymupdf.open(stream=session.current_bytes(), filetype="pdf")
    try:
        # needs_pass se lee una sola vez y antes de autenticar: consultarlo
        # despues invalida la clave de cifrado en memoria (ver session.py).
        locked = bool(doc.needs_pass)
        if locked or session.password is not None:
            if not doc.authenticate(session.password or ""):
                if locked:
                    raise EngineError("internal", _("No se pudo leer el documento protegido."))
                doc.authenticate("")  # un intento fallido deja la clave invalidada
        _harden(doc)
    except BaseException:
        doc.close()
        raise
    return doc


def _open_source(path: Path, password: str | None, /, **detail: Any) -> pymupdf.Document:
    """Abre un archivo de origen (PDF o imagen) como PDF del que copiar paginas.

    ``detail`` acompana a los errores para que la interfaz sepa de que archivo
    se trata cuando hay varios.
    """
    kind = sniff(path)  # por contenido, no por extension
    if kind == "image":
        try:
            data = images_to_pdf([path])
        except EngineError as exc:
            raise EngineError(exc.code, exc.message, **detail) from exc
        return pymupdf.open(stream=data, filetype="pdf")
    if kind != "pdf":
        raise EngineError("invalid_file", _("Este tipo de archivo no es compatible. Usa un PDF o una imagen."), **detail)
    try:
        # filetype fijo: el archivo solo se entrega al analizador de PDF, no a
        # los de otros formatos que MuPDF elegiria por la extension (A05:2025).
        doc = pymupdf.open(str(path), filetype="pdf")
    except Exception as exc:  # noqa: BLE001 - MuPDF lanza tipos propios
        raise EngineError("invalid_file", _("El archivo no es un PDF válido o está dañado."), **detail) from exc
    try:
        if not doc.is_pdf:
            raise EngineError("invalid_file", _("El archivo no es un PDF."), **detail)
        locked = bool(doc.needs_pass)  # una sola vez y antes de autenticar
        auth = 0
        if locked:
            if password is None:
                raise EngineError("needs_password", _("Este PDF está protegido con contraseña."), **detail)
            auth = int(doc.authenticate(password))
            if not auth:
                raise EngineError("wrong_password", _("La contraseña no es correcta."), **detail)
        # Mismas reglas que con el documento abierto: las restricciones del
        # autor se respetan salvo que se tenga la contrasena de propietario.
        # Copiar sus paginas a otro PDF las dejaria sin efecto (A01:2025).
        if not auth & 4 and (doc.permissions & _ALL_PERMISSIONS) != _ALL_PERMISSIONS:
            if locked or password is None:
                raise denied(
                    _(
                        "El autor de este PDF no permite reutilizar sus páginas en otro documento. "
                        "Indica su contraseña de propietario."
                    ),
                    action="extract",
                    **detail,
                )
            if not int(doc.authenticate(password)) & 4:
                raise EngineError("wrong_password", _("Esa no es la contraseña de propietario."), **detail)
        if doc.page_count < 1:
            raise EngineError("invalid_file", _("El PDF no contiene páginas."), **detail)
        _harden(doc)
    except BaseException:
        doc.close()
        raise
    return doc


def _build_pdf(src: pymupdf.Document, first: int, last: int) -> bytes:
    """PDF nuevo con las paginas ``first..last`` de ``src``.

    Se parte de un documento vacio y se copian solo esas paginas (lista
    blanca): asi el resultado no arrastra nada del resto del original, ni
    adjuntos, ni marcadores, ni metadatos. Tampoco las paginas que la copia
    traeria escondidas ni las imagenes de otras paginas que figuren en unos
    recursos compartidos (``prune``).
    """
    out = pymupdf.open()
    try:
        _import_pages(out, src, 0, first=first, last=last, fresh_ids=False, pin=False, prune=True)
        _stamp(out)
        return out.tobytes(garbage=3, deflate=True, use_objstms=1)
    finally:
        out.close()


def _local_copy(session: DocSession, data: bytes) -> Path:
    """Deja ``data`` en un temporal de la carpeta privada del documento (lo borra quien lo pide)."""
    local = session.work_dir / f"salida-{secrets.token_hex(6)}.pdf"
    try:
        with open(local, "xb") as fh:
            fh.write(data)
    except OSError as exc:
        _discard(local)
        raise io_error(_("No se pudo preparar el archivo.")) from exc
    return local


def _write_atomic(session: DocSession, dest: str, data: bytes) -> int:
    """Escribe ``data`` en ``dest`` sin dejar nunca un archivo a medias (ver ``host.install``)."""
    local = _local_copy(session, data)
    try:
        session.host.install(local, dest)
    finally:
        _discard(local)
    return len(data)


def _write_new(session: DocSession, directory: str, stem: str, data: bytes) -> str:
    """Escribe ``data`` como PDF nuevo en ``directory`` sin sobrescribir nunca nada."""
    local = _local_copy(session, data)
    try:
        return session.host.create_new(directory, stem, ".pdf", local)
    finally:
        _discard(local)


def _discard(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        log.warning("no se pudo borrar un archivo temporal")  # sin la ruta: puede llevar datos personales (A09:2025)


# ----------------------------------------------------------------- operaciones


class PagesParams(DocParams):
    pages: PageList


class RotateParams(DocParams):
    pages: PageList
    angle: Literal[90, 180, 270, -90]


@method("pages.rotate", RotateParams)
def pages_rotate(ctx: Ctx, p: RotateParams) -> dict[str, Any]:
    """Gira las paginas el angulo indicado, sumado a la rotacion que ya tengan."""
    session = ctx.engine.session(p.doc)
    session.require("assemble")
    ids = _distinct(p.pages)
    positions = _positions(session, ids)
    with session.edit(_("Girar páginas")) as tx:
        doc = session.doc
        for batch in _batches(ids):
            loaded = [doc[positions[pid]] for pid in batch]  # se cargan todas antes de modificar ninguna
            for page in loaded:
                page.set_rotation((page.rotation + p.angle) % 360)
        # Se anotan de una vez: tx.touch comprueba la lista entera en cada llamada.
        tx.dirty.extend(ids)
        tx.structural = True  # cambia el tamano visible: al deshacer tambien se devuelve la lista de paginas
    return session.result(ids, structural=True)


@method("pages.delete", PagesParams)
def pages_delete(ctx: Ctx, p: PagesParams) -> dict[str, Any]:
    session = ctx.engine.session(p.doc)
    session.require("assemble")
    ids = _distinct(p.pages)
    positions = _positions(session, ids)
    if len(ids) >= len(session.page_ids):
        raise invalid(_("No se pueden eliminar todas las páginas: el documento debe conservar al menos una."))
    doomed = set(ids)
    with session.edit(_plural(len(ids), _("Eliminar página"), _("Eliminar páginas"))) as tx:
        ctx.check_cancel()
        dropped = _delete_pages(session.doc, sorted(positions.values()))
        remaining = [pid for pid in session.page_ids if pid not in doomed]
        _check_consistent(session, remaining)
        tx.structural = True
        tx.page_ids = remaining
        if session.signed and session.chain_intact:
            # En un PDF firmado los cambios se guardan anadidos al original, y
            # cualquiera podria recuperar las paginas borradas quitando ese
            # anadido. Se fuerza la reescritura completa; la firma deja de
            # cubrir el documento, cosa que el borrado ya provocaba (A06:2025).
            tx.force_full = True
    # structureRemoved: el documento ha perdido sus etiquetas de accesibilidad
    # (iban ligadas a las paginas borradas); xfaRemoved: su copia XFA.
    return session.result((), structural=True, structureRemoved=dropped["structure"], xfaRemoved=dropped["xfa"])


class MoveParams(DocParams):
    pages: PageList
    before: PageId | None = None


@method("pages.move", MoveParams)
def pages_move(ctx: Ctx, p: MoveParams) -> dict[str, Any]:
    """Mueve las paginas, conservando su orden relativo, justo antes de ``before`` (o al final)."""
    session = ctx.engine.session(p.doc)
    session.require("assemble")
    ids = _distinct(p.pages)
    positions = _positions(session, ids)
    if p.before is not None:
        session.page_index(p.before)
    moving = set(ids)
    current = list(session.page_ids)
    # Si el destino es una de las paginas que se mueven, vale la primera que no se mueve tras ella.
    anchor = p.before
    if anchor in moving:
        anchor = next((pid for pid in current[positions[anchor] :] if pid not in moving), None)
    block = [pid for pid in current if pid in moving]
    rest = [pid for pid in current if pid not in moving]
    at = len(rest) if anchor is None else rest.index(anchor)
    order = rest[:at] + block + rest[at:]
    with session.edit(_plural(len(ids), _("Mover página"), _("Mover páginas"))) as tx:
        if order == current:
            tx.skip = True
        else:
            index = {pid: n for n, pid in enumerate(current)}
            _reorder_pages(session.doc, [index[pid] for pid in order])
            _check_consistent(session, order)
            tx.structural = True
            tx.page_ids = order
    return session.result((), structural=True)


@method("pages.duplicate", PagesParams)
def pages_duplicate(ctx: Ctx, p: PagesParams) -> dict[str, Any]:
    """Duplica las paginas; cada copia queda justo despues de su original."""
    session = ctx.engine.session(p.doc)
    session.require("assemble")
    ids = _distinct(p.pages)
    positions = _positions(session, ids)
    _check_page_total(len(session.page_ids) + len(ids))
    selected = set(ids)
    indices = sorted(positions.values(), reverse=True)
    new_ids: list[str] = []
    snapshot = _snapshot(session)
    try:
        _prepare_source(snapshot)
        # En la copia desechable se vacian los recursos de las paginas a copiar:
        # la copia compartira fuentes e imagenes con su original en vez de duplicarlas.
        for xref in [snapshot.page_xref(index) for index in indices]:
            snapshot.xref_set_key(xref, "Resources", "<<>>")
        with session.edit(_plural(len(ids), _("Duplicar página"), _("Duplicar páginas"))) as tx:
            # De atras hacia delante: los indices de las paginas pendientes no se mueven.
            for done, index in enumerate(indices):
                ctx.progress(done, len(ids), _("Duplicando páginas…"))
                _duplicate_page(session.doc, snapshot, index)
            new_ids = session.new_page_ids(len(ids))
            fresh = iter(new_ids)
            order: list[str] = []
            for pid in session.page_ids:
                order.append(pid)
                if pid in selected:
                    order.append(next(fresh))
            _check_consistent(session, order)
            tx.structural = True
            tx.page_ids = order
    finally:
        snapshot.close()
    return session.result((), structural=True, created=new_ids)


class InsertBlankParams(DocParams):
    before: PageId | None = None
    width: Side | None = None
    height: Side | None = None
    count: NewPageCount = 1


@method("pages.insertBlank", InsertBlankParams)
def pages_insert_blank(ctx: Ctx, p: InsertBlankParams) -> dict[str, Any]:
    session = ctx.engine.session(p.doc)
    session.require("assemble")
    index = _insertion_index(session, p.before)
    _check_page_total(len(session.page_ids) + p.count)
    # Por defecto, el tamano con que se ve la pagina vecina (la anterior, o la primera).
    neighbor = session.doc[max(index - 1, 0)].rect
    width = p.width if p.width is not None else min(max(neighbor.width, MIN_SIDE), MAX_SIDE)
    height = p.height if p.height is not None else min(max(neighbor.height, MIN_SIDE), MAX_SIDE)
    new_ids: list[str] = []
    # Las paginas se preparan aparte y se insertan de una vez, por el mismo
    # camino que las de un archivo (que ya las protege de lo heredado del arbol).
    blank = pymupdf.open()
    try:
        for _index in range(p.count):
            blank.new_page(width=width, height=height)
        with session.edit(_plural(p.count, _("Insertar página en blanco"), _("Insertar páginas en blanco"))) as tx:
            _import_pages(session.doc, blank, index, fresh_ids=False, pin=True)
            new_ids = session.new_page_ids(p.count)
            order = list(session.page_ids)
            order[index:index] = new_ids
            _check_consistent(session, order)
            tx.structural = True
            tx.page_ids = order
    finally:
        blank.close()
    return session.result((), structural=True, created=new_ids)


class InsertFileParams(DocParams):
    path: PathStr
    before: PageId | None = None
    password: Secret | None = None
    pick: Annotated[list[PageIndex], Field(min_length=1, max_length=MAX_PAGES)] | None = None


@method("pages.insertFile", InsertFileParams)
def pages_insert_file(ctx: Ctx, p: InsertFileParams) -> dict[str, Any]:
    """Inserta las paginas de otro archivo (PDF o imagen), todas o las indicadas en ``pick``."""
    session = ctx.engine.session(p.doc)
    session.require("assemble")
    index = _insertion_index(session, p.before)
    # La ruta la valida host.fetch (paths.py o el intermediario, A01:2025).
    local, _fetched = _fetch(ctx, p.path, max_bytes=MAX_INPUT_BYTES)
    new_ids: list[str] = []
    try:
        src = _open_source(local, p.password)
        try:
            if p.pick is not None:
                pick = _distinct(p.pick)
                if max(pick) >= src.page_count:
                    raise invalid(_("El archivo no tiene alguna de las páginas indicadas."), pages=src.page_count)
                if pick != list(range(src.page_count)):
                    _reorder_pages(src, pick)  # sobre la copia en memoria: el archivo de origen no se modifica
            count = src.page_count
            _check_page_total(len(session.page_ids) + count)
            _prepare_source(src)
            with session.edit(_plural(count, _("Insertar página"), _("Insertar páginas"))) as tx:
                ctx.progress(0, 1, _("Insertando páginas…"))
                # Si solo se insertan algunas paginas, lo que sus recursos nombren
                # de las demas no debe venir con ellas.
                _import_pages(session.doc, src, index, fresh_ids=True, pin=True, prune=p.pick is not None)
                new_ids = session.new_page_ids(count)
                order = list(session.page_ids)
                order[index:index] = new_ids
                _check_consistent(session, order)
                tx.structural = True
                tx.page_ids = order
        finally:
            src.close()
    finally:
        _discard(local)  # MuPDF puede ir leyendo el archivo mientras esta abierto
    return session.result((), structural=True, created=new_ids)


class ExtractParams(DocParams):
    pages: PageList
    path: PathStr


@method("pages.extract", ExtractParams)
def pages_extract(ctx: Ctx, p: ExtractParams) -> dict[str, Any]:
    """Escribe un PDF nuevo con las paginas indicadas, en el orden dado. No cambia el documento."""
    session = ctx.engine.session(p.doc)
    _require_unrestricted(session)
    ids = _distinct(p.pages)
    positions = _positions(session, ids)
    dest = ctx.engine.host.check_output(p.path, (".pdf",))
    _refuse_open_document(ctx, dest)
    ctx.progress(0, 1, _("Extrayendo páginas…"))
    src = _snapshot(session)
    try:
        order = [positions[pid] for pid in ids]
        if order != list(range(src.page_count)):
            _reorder_pages(src, order)
        _prepare_source(src)
        data = _build_pdf(src, 0, len(order) - 1)
    finally:
        src.close()
    ctx.check_cancel()
    size = _write_atomic(session, dest, data)
    return {"count": len(ids), "size": size, "protectionDropped": _protection_dropped(session)}


class SplitParams(DocParams):
    dir: PathStr
    mode: Literal["each", "every", "ranges"]
    every: Annotated[int, Field(strict=True, ge=1, le=MAX_PAGES)] | None = None
    ranges: Annotated[list[tuple[PageIndex, PageIndex]], Field(min_length=1, max_length=MAX_RANGES)] | None = None
    prefix: Annotated[str, StringConstraints(min_length=1, max_length=120)] | None = None


def _split_ranges(p: SplitParams, total: int) -> list[tuple[int, int]]:
    if p.mode == "each":
        return [(n, n) for n in range(total)]
    if p.mode == "every":
        if p.every is None:
            raise invalid(_("Indica cada cuántas páginas hay que dividir."))
        return [(n, min(n + p.every, total) - 1) for n in range(0, total, p.every)]
    if not p.ranges:
        raise invalid(_("Indica los intervalos de páginas."))
    for first, last in p.ranges:
        if first > last or last >= total:
            raise invalid(_("Hay un intervalo de páginas que no existe en el documento."), pages=total)
    return [(first, last) for first, last in p.ranges]


@method("pages.split", SplitParams)
def pages_split(ctx: Ctx, p: SplitParams) -> dict[str, Any]:
    """Divide el documento en varios PDF dentro de ``dir``. No cambia el documento."""
    session = ctx.engine.session(p.doc)
    _require_unrestricted(session)
    directory = ctx.engine.host.check_output_dir(p.dir)
    ranges = _split_ranges(p, len(session.page_ids))
    if len(ranges) > MAX_SPLIT_FILES:
        raise invalid(_("Se generarían demasiados archivos."), max=MAX_SPLIT_FILES)
    # El prefijo acaba en un nombre de archivo: se limpia de separadores y de
    # nombres reservados para que no pueda salirse de la carpeta (A01:2025).
    stem = safe_filename(p.prefix if p.prefix is not None else Path(session.name).stem, fallback=_("documento"))
    created: list[str] = []
    src = _snapshot(session)
    try:
        _prepare_source(src)
        for n, (first, last) in enumerate(ranges):
            message = _("Dividiendo el documento ({done} de {total})…", done=n + 1, total=len(ranges))
            ctx.progress(n, len(ranges), message)
            data = _build_pdf(src, first, last)
            # Nombre libre reservado en modo exclusivo: nunca sobrescribe nada.
            created.append(_write_new(session, directory, f"{stem}-{n + 1}", data))
    except BaseException:
        # Todo o nada: si falla o se cancela a medias no queda una division
        # parcial. Solo se borra lo creado aqui mismo (A10:2025).
        for path in created:
            session.host.discard(path)
        raise
    finally:
        src.close()
    return {"files": created, "protectionDropped": _protection_dropped(session)}


class CropParams(DocParams):
    pages: PageList
    box: Rect  # en coordenadas de visualizacion
    remove: bool = False  # eliminar de verdad lo que queda fuera (no solo ocultarlo)


def _crop_page(doc: pymupdf.Document, page: pymupdf.Page, box: pymupdf.Rect) -> bool:
    """Recorta la pagina a ``box`` (espacio de visualizacion); devuelve si cambio algo."""
    # Se usa la matriz con la que MuPDF dibuja de verdad la pagina: tiene en
    # cuenta el giro, el origen del MediaBox, un CropBox que se sale de el y la
    # unidad de usuario, asi que lo que se ve despues es exactamente la caja.
    pdfpage = pymupdf._as_pdf_page(page)
    bound = mupdf.FzRect(0)
    ctm = mupdf.FzMatrix()
    mupdf.pdf_page_transform(pdfpage, bound, ctm)
    to_pdf = pymupdf.Matrix(mupdf.fz_invert_matrix(ctm))
    visible = page.rect
    # Recortar solo puede reducir: nunca descubre lo que ya estaba oculto.
    x0, y0 = max(box.x0, visible.x0), max(box.y0, visible.y0)
    x1, y1 = min(box.x1, visible.x1), min(box.y1, visible.y1)
    if x1 - x0 < MIN_SIDE or y1 - y0 < MIN_SIDE:
        raise invalid(_("La zona de recorte queda fuera de la página o es demasiado pequeña."))
    if max(abs(x0 - visible.x0), abs(y0 - visible.y0), abs(x1 - visible.x1), abs(y1 - visible.y1)) < 0.01:
        return False
    # /CropBox se escribe directamente, en coordenadas PDF: Page.set_cropbox
    # haria lo mismo, pero busca la pagina por su numero en cada llamada (ver
    # _batches).
    raw = (pymupdf.Rect(x0, y0, x1, y1) * to_pdf).normalize()
    media = page.mediabox
    rx0, ry0 = max(raw.x0, media.x0), max(raw.y0, media.y0)
    rx1, ry1 = min(raw.x1, media.x1), min(raw.y1, media.y1)
    if rx1 - rx0 < 1 or ry1 - ry0 < 1:  # por debajo de un punto MuPDF descarta la caja
        raise invalid(_("La zona de recorte queda fuera de la página o es demasiado pequeña."))
    before = pymupdf.Rect(page.cropbox)
    mupdf.pdf_dict_put_rect(pdfpage.obj(), _N("CropBox"), mupdf.FzRect(rx0, ry0, rx1, ry1))
    after = pymupdf.Rect(page.cropbox)
    # Los elementos de Folio guardan su centro respecto a la esquina del
    # CropBox: al moverse esta, se corrige la etiqueta (el dibujo no cambia).
    dx, dy = after.x0 - before.x0, after.y0 - before.y0
    if dx or dy:
        for placed in content.list_objects(doc, page):
            placed.center = (placed.center[0] - dx, placed.center[1] - dy)
            doc.xref_set_key(placed.stream, content.TAG_KEY, placed.tag())
    return True


@method("pages.crop", CropParams)
def pages_crop(ctx: Ctx, p: CropParams) -> dict[str, Any]:
    """Recorta las paginas a la caja dada.

    Por defecto lo de fuera queda oculto, no eliminado (se puede deshacer
    ampliando el recorte con otro programa). Con ``remove`` se elimina de
    verdad, con el mismo cuidado que al censurar (ver ``redaction.erase_outside``).
    """
    session = ctx.engine.session(p.doc)
    session.require("modify")  # cambiar las cajas de pagina es modificar el contenido, no reorganizarlo
    ids = _distinct(p.pages)
    positions = _positions(session, ids)
    box = pymupdf.Rect(p.box).normalize()
    if not all(math.isfinite(v) for v in box) or box.width < MIN_SIDE or box.height < MIN_SIDE:
        raise invalid(_("La zona de recorte es demasiado pequeña."))
    changed: list[str] = []
    removed = 0
    with session.edit(_plural(len(ids), _("Recortar página"), _("Recortar páginas"))) as tx:
        doc = session.doc
        for batch in _batches(ids):
            loaded = [doc[positions[pid]] for pid in batch]  # se cargan todas antes de modificar ninguna
            for pid, page in zip(batch, loaded):
                if _crop_page(doc, page, box):
                    changed.append(pid)
            del loaded
        if p.remove:
            widgets: list[redaction.RemovedWidget] = []
            retired: set[int] = set()
            forms: set[int] = set()
            try:
                for n, pid in enumerate(ids):
                    ctx.progress(n, len(ids), _("Eliminando lo que queda fuera…"))
                    page = doc[positions[pid]]
                    geom = Geom(page)
                    # Lo que se ve tras recortar, en el espacio de la pagina.
                    keep = geom.rect_u((0.0, 0.0, *geom.size_d))
                    page, trimmed = redaction.erase_outside(doc, page, keep)
                    removed += trimmed.removed
                    widgets += trimmed.widgets
                    retired |= trimmed.retired
                    forms |= trimmed.forms
                    if pid not in changed:
                        changed.append(pid)
                redaction.prune_fields(doc, widgets)
                # Lo que la pagina dejo de dibujar: fuera del archivo, salvo que
                # otra pagina lo siga mostrando (entonces no se toca).
                redaction.release_retired(doc, retired, forms, tick=ctx.check_cancel)
                redaction.purge_orphans(doc, ctx.check_cancel)
            except (EngineError, MemoryError):
                raise
            except Exception as exc:  # noqa: BLE001 - MuPDF lanza tipos propios
                log.warning("fallo al recortar eliminando el contenido: %s", type(exc).__name__)
                raise redaction.failed(_("No se pudo eliminar lo que queda fuera del recorte. No se ha modificado el documento.")) from exc
            # Reescritura completa: una actualizacion incremental dejaria lo
            # eliminado en la parte anterior del archivo de trabajo.
            tx.force_full = True
        if changed:
            tx.dirty.extend(changed)
            tx.structural = True  # cambia el tamano visible
        else:
            tx.skip = True
    return session.result(changed, structural=True, removedObjects=removed)


class CreateParams(Params):
    width: Side = A4[0]
    height: Side = A4[1]
    count: NewPageCount = 1


@method("doc.create", CreateParams)
def doc_create(ctx: Ctx, p: CreateParams) -> dict[str, Any]:
    """Documento nuevo en blanco."""
    doc = pymupdf.open()
    try:
        for _index in range(p.count):
            doc.new_page(width=p.width, height=p.height)
        _stamp(doc)
        data = doc.tobytes(garbage=3, deflate=True)
    finally:
        doc.close()
    session = DocSession.from_bytes(ctx.engine.work_root, data, _("Sin título.pdf"), ctx.engine.host)
    ctx.engine.sessions[session.id] = session
    return describe(session)


class FromImagesParams(Params):
    paths: Annotated[list[PathStr], Field(min_length=1, max_length=500)]
    mode: Literal["auto", "a4", "letter", "image"] = "auto"
    margin: Annotated[float, Field(ge=0, le=200, allow_inf_nan=False)] = 0.0


class _Inputs:
    """Copias privadas de varios archivos del usuario, con un tope de tamano conjunto.

    El resultado se compone en memoria: se corta antes de reunir mas de lo que
    la agotaria (A06:2025). Las copias se borran al salir del bloque ``with``.
    """

    def __init__(self, ctx: Ctx, max_each: int) -> None:
        self._ctx = ctx
        self._max_each = max_each
        self._total = 0
        self.files: list[tuple[Path, Any]] = []

    def add(self, raw: str, **detail: Any) -> tuple[Path, Any]:
        limit = min(self._max_each, max(0, MAX_INPUT_BYTES - self._total))
        try:
            local, fetched = _fetch(self._ctx, raw, max_bytes=limit, **detail)
        except EngineError as exc:
            if exc.detail.get("tooLarge") and limit < self._max_each:
                # No es que este archivo sea demasiado grande: es que ya no cabe.
                raise io_error(_("Los archivos ocupan demasiado en conjunto."), **detail) from exc
            raise
        self._total += fetched.size
        self.files.append((local, fetched))
        return local, fetched

    def __enter__(self) -> "_Inputs":
        return self

    def __exit__(self, *_exc: Any) -> None:
        for local, _fetched in self.files:
            _discard(local)


@method("doc.fromImages", FromImagesParams)
def doc_from_images(ctx: Ctx, p: FromImagesParams) -> dict[str, Any]:
    """PDF nuevo con una pagina por imagen."""
    with _Inputs(ctx, MAX_IMAGE_BYTES) as inputs:
        # Todas las rutas se validan (y se copian) antes de empezar a componer.
        for n, raw in enumerate(p.paths):
            inputs.add(raw, path=raw, index=n)
        out = pymupdf.open()
        try:
            for n, (local, fetched) in enumerate(inputs.files):
                ctx.progress(
                    n, len(inputs.files),
                    _("Convirtiendo imágenes ({done} de {total})…", done=n + 1, total=len(inputs.files)),
                )
                detail = {"path": p.paths[n], "index": n}
                if sniff(local) != "image":
                    raise EngineError(
                        "invalid_file", _("«{name}» no es una imagen compatible.", name=fetched.name), **detail
                    )
                try:
                    # Una a una para poder avisar del avance y cancelar; la validacion
                    # y la composicion de cada pagina siguen siendo las de imaging.py.
                    data = images_to_pdf([local], mode=p.mode, margin=p.margin)
                except EngineError as exc:
                    raise EngineError(
                        exc.code, _("«{name}»: {message}", name=fetched.name, message=exc.message), **detail
                    ) from exc
                with pymupdf.open(stream=data, filetype="pdf") as one:
                    out.insert_pdf(one)
            _stamp(out)
            data = out.tobytes(garbage=3, deflate=True)
        finally:
            out.close()
        first_name = inputs.files[0][1].name
    name = Path(first_name).stem + ".pdf" if len(p.paths) == 1 else _("Imágenes.pdf")
    session = DocSession.from_bytes(ctx.engine.work_root, data, safe_filename(name, fallback=_("Imágenes.pdf")), ctx.engine.host)
    ctx.engine.sessions[session.id] = session
    return describe(session)


class MergeParams(Params):
    paths: Annotated[list[PathStr], Field(min_length=2, max_length=200)]
    passwords: Annotated[dict[PathStr, Secret], Field(max_length=200)] | None = None


def _number(value: Any, low: float, high: float) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) and low <= number <= high else None


def _outline_rows(src: pymupdf.Document, offset: int, base: int) -> list[list[Any]]:
    """Marcadores de ``src`` listos para ``set_toc``: un nivel mas abajo y con las paginas desplazadas.

    Los marcadores vienen del PDF (dato no fiable): se acotan en numero,
    profundidad y longitud, y solo se conservan los que saltan a una pagina del
    propio archivo. Los que abrian una web, otro archivo o un programa quedan
    como titulo sin accion: un PDF combinado no hereda acciones (A08:2025).
    """
    try:
        toc = src.get_toc(simple=False)
    except Exception:  # noqa: BLE001 - marcadores corruptos: se combina sin ellos
        return []
    pages = src.page_count
    rows: list[list[Any]] = []
    previous = base
    for entry in toc[:MAX_OUTLINE_ITEMS]:
        if not isinstance(entry, (list, tuple)) or len(entry) < 3:
            continue
        level, title, page = entry[0], entry[1], entry[2]
        if isinstance(level, bool) or not isinstance(level, int) or level < 1:
            continue
        level = min(base + level, previous + 1, MAX_OUTLINE_LEVEL)  # set_toc no admite saltos de nivel
        previous = level
        title = str(title)[:500].strip() or _("(sin título)")
        dest = entry[3] if len(entry) > 3 and isinstance(entry[3], dict) else {}
        internal = isinstance(page, int) and not isinstance(page, bool) and 1 <= page <= pages
        if not internal or dest.get("kind") != pymupdf.LINK_GOTO:
            rows.append([level, title, -1])
            continue
        target: dict[str, Any] = {"kind": pymupdf.LINK_GOTO}
        point = dest.get("to")
        if isinstance(point, pymupdf.Point):
            x, y = _number(point.x, -MAX_SIDE, 2 * MAX_SIDE), _number(point.y, -MAX_SIDE, 2 * MAX_SIDE)
            if x is not None and y is not None:
                target["to"] = pymupdf.Point(x, y)
        zoom = _number(dest.get("zoom"), 0, 64)
        if zoom is not None:
            target["zoom"] = zoom
        for flag in ("bold", "italic", "collapse"):
            if dest.get(flag) is True:
                target[flag] = True
        color = dest.get("color")
        if isinstance(color, (list, tuple)) and len(color) == 3:
            channels = [_number(c, 0, 1) for c in color]
            if None not in channels:
                target["color"] = tuple(channels)
        rows.append([level, title, offset + page, target])
    return rows


@method("doc.merge", MergeParams)
def doc_merge(ctx: Ctx, p: MergeParams) -> dict[str, Any]:
    """PDF nuevo con los archivos (PDF o imagenes) uno detras de otro."""
    passwords = p.passwords or {}
    by_path = {os.path.normcase(os.path.normpath(key)): value for key, value in passwords.items()}
    with _Inputs(ctx, MAX_INPUT_BYTES) as inputs:
        # Todas las rutas se validan (y se copian) antes de empezar a trabajar.
        for n, raw in enumerate(p.paths):
            inputs.add(raw, path=raw, index=n)
        out = pymupdf.open()
        try:
            toc: list[list[Any]] = []
            for n, (local, fetched) in enumerate(inputs.files):
                ctx.progress(
                    n, len(inputs.files),
                    _("Combinando archivos ({done} de {total})…", done=n + 1, total=len(inputs.files)),
                )
                password = passwords.get(p.paths[n], by_path.get(os.path.normcase(os.path.normpath(fetched.path))))
                src = _open_source(local, password, path=p.paths[n], index=n)
                try:
                    start = out.page_count
                    _check_page_total(start + src.page_count)
                    _prepare_source(src)
                    _import_pages(out, src, start, fresh_ids=True, pin=False)
                    toc.append([1, Path(fetched.name).stem[:200].strip() or _("Documento"), start + 1])
                    toc.extend(_outline_rows(src, start, 1))
                finally:
                    src.close()
            out.set_toc(toc)
            _stamp(out)
            data = out.tobytes(garbage=3, deflate=True)
        finally:
            out.close()
    session = DocSession.from_bytes(ctx.engine.work_root, data, _("Documento combinado.pdf"), ctx.engine.host)
    ctx.engine.sessions[session.id] = session
    return describe(session)
