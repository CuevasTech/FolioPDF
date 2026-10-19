"""Eliminacion real de contenido: censura, texto oculto y objetos huerfanos.

Censurar no es tapar. Una marca pendiente es una anotacion ``Redact`` que solo
senala una zona; al aplicarla, lo que hay debajo se elimina del documento:

* texto, imagenes y dibujo vectorial del contenido de la pagina (MuPDF),
* los elementos anadidos con Folio cuya caja toca la zona,
* las anotaciones, enlaces y campos de formulario que la tocan,
* y las copias "invisibles" del mismo dato: miniatura de la pagina, datos
  privados de otras aplicaciones, texto alternativo del contenido marcado y el
  arbol de estructura (etiquetas de accesibilidad).

Despues se comprueba que no queda texto en ninguna zona ni nada que se vea al
dibujarla; si queda, se lanza un error y quien llama revierte la transaccion.

Quitar algo de una pagina no lo quita del documento. Lo retirado puede seguir
alcanzable (el diccionario de recursos que comparten otras paginas lo sigue
nombrando) o quedar suelto en la tabla de objetos. De lo primero se ocupa
``usage.release_undrawn`` con la lista de objetos retirados que devuelve cada
funcion de aqui; de lo segundo, ``purge_orphans``: el archivo de trabajo se
reescribe sin recoleccion de basura y, si no se borrasen, el contenido
censurado seguiria dentro como objeto huerfano.

Este modulo no sabe de sesiones ni de operaciones: trabaja sobre un documento
de PyMuPDF abierto y deja a ``ops/security.py`` la transaccion y los permisos.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Iterator, Sequence

import pymupdf
from PIL import Image, ImageChops, ImageDraw
from pymupdf import mupdf

from . import content, usage
from .errors import EngineError
from .geometry import Geom, box_corners, fmt, matrix_operand
from .i18n import _

REDACT = pymupdf.PDF_ANNOT_REDACT
WIDGET = pymupdf.PDF_ANNOT_WIDGET
LINK = pymupdf.PDF_ANNOT_LINK
FILE_ATTACHMENT = pymupdf.PDF_ANNOT_FILE_ATTACHMENT
# Anotaciones que no son "comentarios": enlaces y campos (se piden aparte),
# ventanas emergentes (se van con su anotacion) y marcas de censura.
NOT_COMMENTS = (LINK, pymupdf.PDF_ANNOT_POPUP, WIDGET, REDACT)

# Mismas opciones de extraccion que la busqueda de la aplicacion (ops/text.py):
# lo que se marca coincide con lo que el usuario ve al buscar.
_TEXT_FLAGS = pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_MEDIABOX_CLIP
# Para verificar se mira TODO el texto, tambien el que queda fuera del area
# visible, y los glifos reales sin sustituirlos por su /ActualText.
_VERIFY_FLAGS = pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_IGNORE_ACTUALTEXT
_HIDDEN_FLAGS = pymupdf.TEXT_PRESERVE_SPANS | pymupdf.TEXT_COLLECT_STYLES

# Opciones de censura de MuPDF (ver ``_run_mupdf``).
_IMAGES_PIXELS = mupdf.PDF_REDACT_IMAGE_PIXELS
_IMAGES_NONE = mupdf.PDF_REDACT_IMAGE_NONE
_ART_TOUCHED = mupdf.PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED
_ART_NONE = mupdf.PDF_REDACT_LINE_ART_NONE
_TEXT_REMOVE = mupdf.PDF_REDACT_TEXT_REMOVE
_TEXT_INVISIBLE = mupdf.PDF_REDACT_TEXT_REMOVE_INVISIBLE

# MuPDF quita un caracter cuando la zona entra mas de un 10 % en su caja. La
# verificacion exige un 20 %: deja margen a diferencias de metrica entre la
# extraccion de texto y el filtro, sin dar por bueno texto tapado de verdad.
_CORE = 0.20
_RETRIES = 2
# Rectangulo "toda la pagina" para las pasadas que no dependen de una zona.
_EVERYWHERE = (-30_000.0, -30_000.0, 30_000.0, 30_000.0)


def failed(message: str) -> EngineError:
    return EngineError("redaction_failed", message)


# ====================================================== objetos PDF a bajo nivel

def _pdf(doc: pymupdf.Document) -> Any:
    return pymupdf._as_pdf_document(doc)


def _get(obj: Any, key: str) -> Any:
    return mupdf.pdf_dict_gets(obj, key)


def _has(obj: Any) -> bool:
    """El objeto existe y no es ``null``."""
    return bool(getattr(obj, "m_internal", None)) and not mupdf.pdf_is_null(obj)


def _ref(obj: Any) -> int:
    """Numero del objeto si es una referencia indirecta; si no, 0."""
    return mupdf.pdf_to_num(obj) if _has(obj) and mupdf.pdf_is_indirect(obj) else 0


def _name(obj: Any) -> str:
    """Texto de un nombre PDF (sin la barra); cadena vacia si el objeto no es un nombre."""
    return mupdf.pdf_to_name(obj) if _has(obj) and mupdf.pdf_is_name(obj) else ""


def _catalog(doc: pymupdf.Document) -> Any:
    return _get(mupdf.pdf_trailer(_pdf(doc)), "Root")


def _page_obj(page: pymupdf.Page) -> Any:
    return pymupdf._as_pdf_page(page).obj()


def _page_resources(page: pymupdf.Page) -> Any:
    return mupdf.pdf_dict_get_inheritable(_page_obj(page), pymupdf.PDF_NAME("Resources"))


def _too_deep() -> EngineError:
    return failed(
        _(
            "No se pudo completar la operación de forma segura: el contenido del documento está anidado en exceso. "
            "No se ha modificado el documento."
        )
    )


def _content_objects(doc: pymupdf.Document, page_obj: Any, *, used_only: bool) -> frozenset[int]:
    """Objetos de contenido que una pagina dibuja (``used_only``) o tiene a mano (ver ``usage.py``)."""
    contents = usage.ContentMap(doc)
    found = contents.page(page_obj, used_only=used_only)
    if contents.truncated:
        # Sin saber que dibuja la pagina no se puede saber que deja de dibujar:
        # se falla del lado seguro (A10:2025).
        raise _too_deep()
    return found


_reachable = usage.reachable_objects

# Cada cuantas entradas de la tabla de objetos se da paso a quien quiera
# cancelar (``tick``): un PDF de unos cientos de bytes puede declarar millones.
_TICK_EVERY = 20_000


def _used_entries(doc: pymupdf.Document, tick: Callable[[], None] | None = None) -> Iterator[tuple[int, str, Any]]:
    """Entradas en uso de la tabla de objetos: ``(numero, tipo, entrada)``.

    La tabla puede tener millones de huecos (basta con declarar un numero de
    objeto alto): aqui se saltan con una sola consulta barata, y todo el
    trabajo caro de quien recorre el documento se hace solo sobre lo que
    existe. ``tick`` se llama de vez en cuando para poder cancelar.
    """
    pdf = _pdf(doc)
    exists = getattr(mupdf, "pdf_object_exists", None)
    entry_of, raw = mupdf.ll_pdf_get_xref_entry_no_null, pdf.m_internal
    for num in range(1, mupdf.pdf_xref_len(pdf)):
        if tick is not None and num % _TICK_EVERY == 0:
            tick()
        if exists is not None:
            # La consulta mas barata que ofrece MuPDF: descarta de una vez los
            # huecos y los objetos que ya son ``null``.
            try:
                if not exists(pdf, num):
                    continue
            except Exception:  # noqa: BLE001 - objeto ilegible: que decida quien recorre
                pass
        entry = entry_of(raw, num)
        kind = entry.type if isinstance(entry.type, str) else chr(entry.type)
        if kind in ("n", "o"):
            yield num, kind, entry


def purge_orphans(doc: pymupdf.Document, tick: Callable[[], None] | None = None) -> int:
    """Borra del documento en memoria los objetos que ya nada referencia.

    Eliminar contenido solo lo desconecta: el flujo original de la pagina, la
    imagen sin tapar o el elemento retirado siguen en la tabla de objetos. El
    archivo de trabajo se reescribe sin recoleccion de basura, asi que esos
    objetos se copiarian tal cual y lo censurado seguiria en el archivo. Aqui
    se liberan de verdad antes de confirmar: el dato eliminado no debe
    sobrevivir en ningun artefacto (A04:2025/A06:2025).
    """
    pdf = _pdf(doc)
    live = _reachable(doc)
    dead: list[int] = []
    containers: set[int] = set()
    for num, kind, entry in _used_entries(doc, tick):
        if kind == "o":
            # Objeto guardado dentro de un flujo de objetos: ese flujo hace
            # falta para poder leerlo, aunque nada lo referencie.
            containers.add(int(entry.ofs))
        if num not in live:
            dead.append(num)
    removed = 0
    for num in dead:
        if num in containers:
            continue
        # Flujos de objetos y tablas de referencias de revisiones anteriores:
        # MuPDF los descarta por su cuenta al reescribir el archivo.
        if doc.xref_get_key(num, "Type")[1] in ("/ObjStm", "/XRef"):
            continue
        mupdf.pdf_delete_object(pdf, num)
        removed += 1
    return removed


def drop_key(doc: pymupdf.Document, xref: int, key: str) -> bool:
    """Quita una clave de un objeto; devuelve si existia."""
    if doc.xref_get_key(xref, key)[0] == "null":
        return False
    doc.xref_set_key(xref, key, "null")
    return True


def _set_contents(doc: pymupdf.Document, page: pymupdf.Page, xrefs: Sequence[int]) -> None:
    doc.xref_set_key(page.xref, "Contents", "[" + " ".join(f"{x} 0 R" for x in xrefs) + "]")


def _numbers(text: str) -> list[float]:
    return [float(v) for v in re.findall(r"[-+]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][-+]?[0-9]+)?", text)]


def _array_drop(array: Any, num: int) -> None:
    if not mupdf.pdf_is_array(array):
        return
    for i in reversed(range(mupdf.pdf_array_len(array))):
        if mupdf.pdf_to_num(mupdf.pdf_array_get(array, i)) == num:
            mupdf.pdf_array_delete(array, i)


# ================================================================== geometria

def _solid(rect: Any) -> pymupdf.Rect:
    """Rectangulo normalizado con un grosor minimo (una caja degenerada tambien ocupa sitio)."""
    r = pymupdf.Rect(rect).normalize()
    if r.width < 1.0:
        mid = (r.x0 + r.x1) / 2
        r.x0, r.x1 = mid - 0.5, mid + 0.5
    if r.height < 1.0:
        mid = (r.y0 + r.y1) / 2
        r.y0, r.y1 = mid - 0.5, mid + 0.5
    return r


def finite(values: Iterable[float]) -> bool:
    return all(math.isfinite(v) and abs(v) < 1e7 for v in values)


def _overlap(a: pymupdf.Rect, b: pymupdf.Rect, eps: float = 0.01) -> bool:
    """Los rectangulos comparten superficie (rozarse por un borde no cuenta)."""
    return min(a.x1, b.x1) - max(a.x0, b.x0) > eps and min(a.y1, b.y1) - max(a.y0, b.y0) > eps


def _overlaps_any(rect: pymupdf.Rect, zones: Iterable[pymupdf.Rect]) -> bool:
    return any(_overlap(rect, zone) for zone in zones)


def _quad_hits(quad: Sequence[Sequence[float]], zone: pymupdf.Rect, eps: float = 0.01) -> bool:
    """Un paralelogramo y un rectangulo se solapan (teorema del eje separador)."""
    rect_pts = [(zone.x0, zone.y0), (zone.x1, zone.y0), (zone.x1, zone.y1), (zone.x0, zone.y1)]
    pts = [(float(p[0]), float(p[1])) for p in quad]
    axes = [(1.0, 0.0), (0.0, 1.0)]
    for i in range(2):
        ex, ey = pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]
        norm = math.hypot(ex, ey)
        if norm > 1e-9:
            axes.append((-ey / norm, ex / norm))
    for ax, ay in axes:
        a = [x * ax + y * ay for x, y in pts]
        b = [x * ax + y * ay for x, y in rect_pts]
        if min(max(a), max(b)) - max(min(a), min(b)) <= eps:
            return False
    return True


def _quad_hits_any(quad: Sequence[Sequence[float]], zones: Iterable[pymupdf.Rect]) -> bool:
    return any(_quad_hits(quad, zone) for zone in zones)


# ================================================== elementos anadidos con Folio

_PLACEMENT = re.compile(
    rb"^\s*q\s+(?:/[A-Za-z0-9_.\-]+\s+gs\s+)?((?:[-+0-9.eE]+\s+){6})cm\s+/([A-Za-z0-9_.\-]+)\s+Do\s+Q\s*$"
)


@dataclass
class _Element:
    placed: content.Placed
    claimed: list[list[float]]  # caja que declara la etiqueta, en U
    actual: list[list[float]] | None = None  # region que pinta de verdad, en U
    xobject: int = 0  # xref real del XObject en los recursos de la pagina
    genuine: bool = False  # etiqueta y flujo cuentan lo mismo


def _form_box(doc: pymupdf.Document, xref: int) -> pymupdf.Rect | None:
    """Caja de recorte de un Form XObject una vez aplicada su propia /Matrix."""
    if doc.xref_get_key(xref, "Subtype")[1] != "/Form":
        return None
    kind, value = doc.xref_get_key(xref, "BBox")
    numbers = _numbers(value) if kind == "array" else []
    if len(numbers) != 4:
        return None
    box = pymupdf.Rect(numbers).normalize()
    kind, value = doc.xref_get_key(xref, "Matrix")
    if kind == "array":
        m = _numbers(value)
        if len(m) != 6:
            return None
        box = (box * pymupdf.Matrix(*m)).normalize()
    return box if finite(box) and box.width > 0 and box.height > 0 else None


def _describe_element(doc: pymupdf.Document, page: pymupdf.Page, geom: Geom, placed: content.Placed) -> _Element:
    """Donde esta de verdad un elemento.

    La etiqueta /FolioObj viene dentro del PDF y puede estar manipulada: decir
    que el elemento esta en una esquina mientras su flujo lo dibuja sobre la
    zona censurada. Por eso la region real se calcula con la matriz del flujo
    y la caja de recorte del XObject, que es lo que un visor obedece, y no se
    confia en lo que declara la etiqueta (A08:2025, integridad de los datos).
    """
    element = _Element(placed, box_corners(placed.center, placed.size, placed.angle))
    match = _PLACEMENT.match(doc.xref_stream(placed.stream) or b"")
    if not match or match.group(2).decode("ascii") != placed.name:
        return element
    try:
        values = [float(v) for v in match.group(1).split()]
    except ValueError:
        return element
    if len(values) != 6 or not finite(values):
        return element
    xobject = _ref(_get(_get(_page_resources(page), "XObject"), placed.name))
    box = _form_box(doc, xobject) if xobject else None
    if box is None:
        return element
    matrix = pymupdf.Matrix(*values) * ~geom.u2pdf
    corners = [(box.x0, box.y0), (box.x1, box.y0), (box.x1, box.y1), (box.x0, box.y1)]
    element.actual = [list(pymupdf.Point(x, y) * matrix) for x, y in corners]
    element.xobject = xobject
    expected = tuple(geom.placement(placed.source, placed.center, placed.size, placed.angle))
    same_matrix = all(abs(a - b) <= 0.05 + 1e-3 * abs(b) for a, b in zip(values, expected))
    same_box = all(abs(a - b) <= 0.05 for a, b in zip(placed.source, box))
    element.genuine = same_matrix and same_box
    return element


def _edge_runs(order: Sequence[int], tagged: set[int]) -> tuple[set[int], set[int]]:
    """Flujos etiquetados que van antes de todo el contenido ajeno (fondo) o despues de todo el (primer plano)."""
    leading: set[int] = set()
    for xref in order:
        if xref not in tagged:
            break
        leading.add(xref)
    trailing: set[int] = set()
    for xref in reversed(order):
        if xref not in tagged or xref in leading:
            break
        trailing.add(xref)
    return leading, trailing


def _classify_elements(
    doc: pymupdf.Document, page: pymupdf.Page, geom: Geom, touches: Callable[[_Element], bool]
) -> tuple[list[_Element], list[_Element], set[int]]:
    """Reparte los elementos de la pagina en ``(tocados, protegibles)`` y dice cuales van al fondo."""
    placed = content.list_objects(doc, page)
    if not placed:
        return [], [], set()
    leading, trailing = _edge_runs(page.get_contents(), {p.stream for p in placed})
    touched: list[_Element] = []
    kept: list[_Element] = []
    for element in (_describe_element(doc, page, geom, p) for p in placed):
        if touches(element):
            touched.append(element)
        elif element.genuine and element.placed.stream in (leading | trailing):
            kept.append(element)
        # El resto pasa por el filtro de MuPDF como contenido normal: queda
        # incorporado a la pagina y deja de ser editable, que es lo seguro.
    return touched, kept, leading


def _restore_elements(doc: pymupdf.Document, page: pymupdf.Page, kept: Sequence[_Element], leading: set[int]) -> None:
    """Devuelve a la pagina los elementos que se apartaron antes de filtrarla."""
    pdf = _pdf(doc)
    page_obj = _page_obj(page)
    resources = _get(page_obj, "Resources")
    if not mupdf.pdf_is_dict(resources):
        resources = mupdf.pdf_dict_put_dict(page_obj, pymupdf.PDF_NAME("Resources"), 2)
    xobjects = _get(resources, "XObject")
    if not mupdf.pdf_is_dict(xobjects):
        xobjects = mupdf.pdf_dict_put_dict(resources, pymupdf.PDF_NAME("XObject"), 4)
    for element in kept:
        placed = element.placed
        # MuPDF renombra los recursos al filtrar: si el nombre del elemento ya
        # esta ocupado por otra cosa, se le da uno nuevo.
        name, index = placed.name, 0
        while _has(_get(xobjects, name)):
            name, index = f"FolioX{index}", index + 1
        mupdf.pdf_dict_puts(xobjects, name, mupdf.pdf_new_indirect(pdf, element.xobject, 0))
        if name != placed.name or placed.opacity < 0.995:
            # Reescribir la colocacion repone tambien el estado grafico de la
            # opacidad, que se perdio con los recursos antiguos. De paso la
            # etiqueta queda con el numero de objeto real (el que trae puede
            # estar anticuado si el archivo se guardo renumerando objetos).
            placed.name = name
            placed.xobject = element.xobject
            content.update_placement(doc, page, placed)
    streams = {e.placed.stream for e in kept}
    middle = [x for x in page.get_contents() if x not in streams]
    front = [e.placed.stream for e in kept if e.placed.stream in leading]
    back = [e.placed.stream for e in kept if e.placed.stream not in leading]
    _set_contents(doc, page, front + middle + back)


# ============================================================ motor de censura

def _run_mupdf(
    doc: pymupdf.Document,
    page: pymupdf.Page,
    kept: Sequence[_Element],
    leading: set[int],
    *,
    text: int,
    images: int,
    graphics: int,
) -> None:
    """Aplica con MuPDF las anotaciones Redact de la pagina.

    Se llama a MuPDF directamente y no a ``Page.apply_redactions``: esa funcion
    pinta despues el relleno con la matriz de PyMuPDF, que en paginas giradas
    con CropBox desplazado lo coloca en otro sitio (y taparia contenido ajeno).
    El relleno lo pone ``_draw_fills`` con la geometria propia.

    Los elementos de ``kept`` se apartan antes: MuPDF reescribe todo el
    contenido en un unico flujo y perderian su etiqueta (dejarian de ser
    editables). Solo se apartan elementos que no tocan ninguna zona.
    """
    if kept:
        hidden = {e.placed.stream for e in kept}
        _set_contents(doc, page, [x for x in page.get_contents() if x not in hidden])
    pdfpage = pymupdf._as_pdf_page(page)
    options = mupdf.PdfRedactOptions()
    options.black_boxes = 0
    options.text = text
    options.image_method = images
    options.line_art = graphics
    mupdf.pdf_redact_page(pdfpage.doc(), pdfpage, options)
    if kept:
        _restore_elements(doc, page, kept, leading)


def _reload(doc: pymupdf.Document, page: pymupdf.Page) -> pymupdf.Page:
    """Suelta la pagina y la vuelve a leer de sus objetos.

    MuPDF guarda con la pagina cargada su lista de anotaciones; si se cambia
    por debajo hay que leerla otra vez. ``Document.reload_page`` no sirve aqui:
    conserva las anotaciones ya cargadas y cada una retiene la pagina antigua.
    """
    number = page.number
    page._erase()
    return doc[number]


def _detach_annots(doc: pymupdf.Document, page: pymupdf.Page) -> tuple[pymupdf.Page, Any]:
    """Descuelga la lista de anotaciones de la pagina y la devuelve recargada sin ellas.

    MuPDF aplica TODAS las anotaciones Redact de una pagina y borra los enlaces
    que caen en la zona. Cuando se usa su censura para otra cosa (texto oculto,
    edicion), las marcas pendientes del usuario no deben aplicarse de rebote:
    se apartan y despues ``_attach_annots`` las devuelve intactas.
    """
    page_obj = _page_obj(page)
    annots = _get(page_obj, "Annots")
    if not _has(annots):
        return page, None
    mupdf.pdf_dict_dels(page_obj, "Annots")
    del page_obj
    page = _reload(doc, page)
    pdfpage = pymupdf._as_pdf_page(page)
    if mupdf.pdf_first_annot(pdfpage).m_internal or mupdf.pdf_first_widget(pdfpage).m_internal:
        # La pagina no se ha leido de nuevo y MuPDF sigue viendo anotaciones:
        # antes que arriesgarse a aplicarlas, se abandona (A10:2025).
        raise failed(_("No se pudo modificar la página de forma segura. No se ha cambiado el documento."))
    return page, annots


def _attach_annots(doc: pymupdf.Document, page: pymupdf.Page, annots: Any) -> pymupdf.Page:
    if annots is None:
        return page
    mupdf.pdf_dict_puts(_page_obj(page), "Annots", annots)
    return _reload(doc, page)


def _draw_fills(doc: pymupdf.Document, page: pymupdf.Page, geom: Geom, zones: Sequence[pymupdf.Rect]) -> None:
    """Pinta de negro las zonas censuradas, como contenido normal de la pagina."""
    width, height = geom.size_u
    parts = ["q", "0 g"]
    for zone in zones:
        box = pymupdf.Rect(max(zone.x0, 0.0), max(zone.y0, 0.0), min(zone.x1, width), min(zone.y1, height))
        if box.x1 <= box.x0 or box.y1 <= box.y0:
            continue
        # Coordenadas PDF calculadas con Geom (ver geometry.py), no con la
        # matriz de PyMuPDF.
        r = (box * geom.u2pdf).normalize()
        parts.append(f"{fmt(r.x0)} {fmt(r.y0)} {fmt(r.width)} {fmt(r.height)} re")
    if len(parts) == 2:
        return
    parts += ["f", "Q"]
    if not page.is_wrapped:
        page.wrap_contents()
    xref = doc.get_new_xref()
    doc.update_object(xref, "<<>>")
    doc.update_stream(xref, ("\n".join(parts) + "\n").encode("ascii"), new=True)
    _set_contents(doc, page, [*page.get_contents(), xref])


def _leftover_text(page: pymupdf.Page, zones: Sequence[pymupdf.Rect]) -> list[pymupdf.Rect]:
    """Cajas de los caracteres que siguen dentro de alguna zona."""
    found: list[pymupdf.Rect] = []
    data = page.get_text("rawdict", flags=_VERIFY_FLAGS)
    for block in data.get("blocks", ()):
        if block.get("type", 0) != 0:
            continue
        for line in block.get("lines", ()):
            for span in line.get("spans", ()):
                for char in span.get("chars", ()):
                    if not char["c"] or char["c"].isspace():
                        continue
                    x0, y0, x1, y1 = char["bbox"]
                    dx, dy = (x1 - x0) * _CORE, (y1 - y0) * _CORE
                    cx0, cy0, cx1, cy1 = x0 + dx, y0 + dy, x1 - dx, y1 - dy
                    for zone in zones:
                        if cx0 <= zone.x1 and cx1 >= zone.x0 and cy0 <= zone.y1 and cy1 >= zone.y0:
                            found.append(pymupdf.Rect(x0, y0, x1, y1))
                            break
    return found


_MAX_QUADS = 10_000


def _mark_zones(doc: pymupdf.Document, geom: Geom, xref: int) -> list[pymupdf.Rect]:
    """Zonas (en U) que cubre una marca, leidas igual que las lee MuPDF al aplicarla.

    MuPDF censura los /QuadPoints de la anotacion y, solo si no trae ninguno,
    su /Rect. Aqui se leen esas dos claves y ninguna otra: una marca que venga
    dentro del PDF puede traer ademas /Vertices, /L o /CL con otras coordenadas
    (``Annot.vertices`` las antepone), y entonces el recuadro negro se pintaria
    en un sitio mientras el contenido se elimina en otro. Lo que se comprueba y
    se tapa tiene que ser exactamente lo que se ha eliminado (A08:2025).
    """
    annot = mupdf.pdf_load_object(_pdf(doc), xref)
    to_u = ~geom.u2pdf
    zones: list[pymupdf.Rect] = []
    quads = _get(annot, "QuadPoints")
    count = mupdf.pdf_array_len(quads) if mupdf.pdf_is_array(quads) else 0
    for start in range(0, min(count, 8 * _MAX_QUADS), 8):
        # Como MuPDF: cada ocho numeros son un cuadrilatero, y los que falten
        # en el ultimo valen cero.
        numbers = [
            mupdf.pdf_to_real(mupdf.pdf_array_get(quads, start + k)) if start + k < count else 0.0 for k in range(8)
        ]
        points = [pymupdf.Point(numbers[k], numbers[k + 1]) * to_u for k in range(0, 8, 2)]
        zones.append(pymupdf.Rect(
            min(p.x for p in points), min(p.y for p in points), max(p.x for p in points), max(p.y for p in points),
        ))
    if not count:
        rect = mupdf.pdf_dict_get_rect(annot, pymupdf.PDF_NAME("Rect"))
        zones.append((pymupdf.Rect(rect.x0, rect.y0, rect.x1, rect.y1) * to_u).normalize())
    return [z.normalize() for z in zones if finite(z)]


def page_marks(page: pymupdf.Page) -> list[tuple[int, pymupdf.Rect, list[pymupdf.Rect]]]:
    """Marcas pendientes de la pagina: ``(xref, rectangulo, zonas)`` en U."""
    out = []
    geom = Geom(page)
    doc = page.parent
    for xref, kind, _name in page.annot_xrefs():
        if kind != REDACT:
            continue
        annot = page.load_annot(xref)
        if annot is None:
            continue
        # Annot.rect llega en el espacio de las propiedades de PyMuPDF; el
        # resto de este modulo trabaja en U (ver geometry.py).
        out.append((xref, geom.api_to_u(annot.rect), _mark_zones(doc, geom, xref)))
    return out


def add_mark(page: pymupdf.Page, rect_u: pymupdf.Rect) -> pymupdf.Annot:
    """Anade una marca de censura pendiente (rectangulo en U)."""
    # El relleno negro queda anotado en la marca por si la aplica otro
    # programa; Folio pinta siempre el suyo al aplicar (ver ``_draw_fills``).
    return page.add_redact_annot(rect_u, fill=(0, 0, 0))


# ------------------------------------------------- anotaciones, enlaces y campos

@dataclass
class RemovedWidget:
    xref: int  # widget eliminado
    parent: int  # su campo padre (0 si el widget es el propio campo)
    state: str  # estado de apariencia que tenia (botones de opcion)


def _remove_touched_annots(
    doc: pymupdf.Document, page: pymupdf.Page, zones: Sequence[pymupdf.Rect]
) -> tuple[int, list[RemovedWidget]]:
    """Elimina las anotaciones, enlaces y campos de formulario que tocan una zona.

    Un comentario, el destino de un enlace o el valor de un campo no son
    contenido de la pagina: la censura de MuPDF no los alcanza y seguirian en
    el archivo debajo del recuadro negro.
    """
    removed = 0
    widgets: list[RemovedWidget] = []
    geom = Geom(page)
    for xref in [x for x, kind, _ in page.annot_xrefs() if kind not in NOT_COMMENTS]:
        # Borrar una anotacion se lleva tambien sus respuestas: se comprueba
        # que cada una siga existiendo antes de cargarla.
        if xref not in {a[0] for a in page.annot_xrefs()}:
            continue
        annot = page.load_annot(xref)
        if annot is not None and _overlaps_any(_solid(geom.api_to_u(annot.rect)), zones):
            page.delete_annot(annot)
            removed += 1
    # PyMuPDF devuelve la zona de los enlaces en el espacio de visualizacion
    # (girado), no en U como las anotaciones: se convierte antes de comparar.
    # Sin esto, en una pagina girada el destino de un enlace tapado por la
    # censura seguiria en el archivo.
    for link in page.get_links():
        if link.get("from") is not None and _overlaps_any(_solid(geom.rect_u(link["from"])), zones):
            page.delete_link(link)
            removed += 1
    for xref in [x for x, kind, _ in page.annot_xrefs() if kind == WIDGET]:
        widget = page.load_widget(xref)
        if not widget or not _overlaps_any(_solid(geom.api_to_u(widget.rect)), zones):
            continue
        kind, value = doc.xref_get_key(xref, "Parent")
        parent = int(value.split()[0]) if kind == "xref" else 0
        kind, value = doc.xref_get_key(xref, "AS")
        widgets.append(RemovedWidget(xref, parent, value if kind == "name" else ""))
        page.delete_widget(widget)
        removed += 1
    return removed, widgets


def prune_fields(doc: pymupdf.Document, widgets: Sequence[RemovedWidget]) -> int:
    """Retira del formulario los campos que se han quedado sin ningun widget.

    MuPDF quita el widget, pero el campo padre (con su valor /V) sigue colgando
    de /AcroForm: el dato censurado continuaria en el archivo sin verse en
    ninguna pagina. Devuelve cuantos campos conservan otros widgets (su valor
    sigue a la vista en ellos y no se toca).
    """
    pdf = _pdf(doc)
    form = _get(_catalog(doc), "AcroForm")
    if not mupdf.pdf_is_dict(form):
        return 0
    top = _get(form, "Fields")
    order = _get(form, "CO")
    limit = mupdf.pdf_xref_len(pdf)
    shared: set[int] = set()
    for widget in widgets:
        _array_drop(order, widget.xref)
        _array_drop(top, widget.xref)
        child, node = widget.xref, widget.parent
        seen: set[int] = set()
        while 0 < node < limit and node not in seen:
            seen.add(node)
            obj = mupdf.pdf_load_object(pdf, node)
            kids = _get(obj, "Kids")
            _array_drop(kids, child)
            if mupdf.pdf_is_array(kids) and mupdf.pdf_array_len(kids) > 0:
                if child == widget.xref:
                    shared.add(node)
                    # Grupo de botones de opcion: si el retirado era el
                    # elegido, el nombre de esa opcion no debe quedar en /V.
                    kind, value = doc.xref_get_key(node, "V")
                    if widget.state not in ("", "/Off") and kind == "name" and value == widget.state:
                        doc.xref_set_key(node, "V", "/Off")
                break
            # El campo se ha quedado vacio: se descuelga de su padre (o de la
            # raiz del formulario) y se sigue hacia arriba.
            _array_drop(order, node)
            parent = _ref(_get(obj, "Parent"))
            if not parent:
                _array_drop(top, node)
            child, node = node, parent
    return len(shared)


def drop_xfa(doc: pymupdf.Document) -> bool:
    """Quita la copia XFA del formulario: guarda en XML los valores de todos los campos."""
    form = _get(_catalog(doc), "AcroForm")
    if not mupdf.pdf_is_dict(form) or not _has(_get(form, "XFA")):
        return False
    mupdf.pdf_dict_dels(form, "XFA")
    return True


def drop_structure(doc: pymupdf.Document) -> bool:
    """Elimina el arbol de estructura (etiquetas de accesibilidad) del documento.

    Sus elementos pueden repetir el texto de la pagina en /ActualText, /Alt, /E
    o en atributos, y MuPDF no los limpia de forma fiable al censurar (la
    version 1.28 deja intacto el /ActualText del elemento). Como no hay una
    lista cerrada de claves que vigilar, se retira entero: es la opcion que
    falla del lado seguro (A06:2025, diseno seguro).
    """
    catalog = _catalog(doc)
    if not _has(_get(catalog, "StructTreeRoot")):
        return False
    mupdf.pdf_dict_dels(catalog, "StructTreeRoot")
    # Sin arbol, el documento ya no esta "marcado".
    mupdf.pdf_dict_dels(catalog, "MarkInfo")
    return True


# ---------------------------------------------- texto alternativo del contenido

_WS = b"\x00\t\n\x0c\r "
_DELIM = b"()<>[]{}/%"
_REPLACEMENT_KEYS = ("ActualText", "Alt", "E")
_INLINE_END = re.compile(rb"[\x00\t\n\x0c\r ]EI(?=[\x00\t\n\x0c\r ]|$)")
_MAYBE_REPLACEMENT = re.compile(rb"/(?:ActualText|Alt|E)(?=[\x00\t\n\x0c\r ()<>\[\]/])|#")
_NAME_ESCAPE = re.compile(rb"#([0-9A-Fa-f]{2})")


class _Unparsable(Exception):
    pass


def _skip_space(data: bytes, pos: int) -> int:
    n = len(data)
    while pos < n:
        byte = data[pos]
        if byte in _WS:
            pos += 1
        elif byte == 0x25:  # comentario hasta fin de linea
            while pos < n and data[pos] not in b"\r\n":
                pos += 1
        else:
            break
    return pos


def _skip_object(data: bytes, pos: int) -> int:
    """Posicion siguiente al objeto o palabra clave que empieza en ``pos``."""
    n = len(data)
    if pos >= n:
        raise _Unparsable
    byte = data[pos]
    if byte == 0x28:  # cadena literal: parentesis anidados y escapes
        depth = 0
        while pos < n:
            current = data[pos]
            if current == 0x5C:
                pos += 2
                continue
            if current == 0x28:
                depth += 1
            elif current == 0x29:
                depth -= 1
                if depth == 0:
                    return pos + 1
            pos += 1
        raise _Unparsable
    if data.startswith(b"<<", pos):
        pos += 2
        while True:
            pos = _skip_space(data, pos)
            if data.startswith(b">>", pos):
                return pos + 2
            pos = _skip_object(data, pos)
    if byte == 0x3C:  # cadena hexadecimal
        end = data.find(b">", pos)
        if end < 0:
            raise _Unparsable
        return end + 1
    if byte == 0x5B:
        pos += 1
        while True:
            pos = _skip_space(data, pos)
            if pos < n and data[pos] == 0x5D:
                return pos + 1
            pos = _skip_object(data, pos)
    if byte in b")>]{}":
        raise _Unparsable
    pos += 1  # la barra de un nombre o el primer caracter de la palabra
    while pos < n and data[pos] not in _WS and data[pos] not in _DELIM:
        pos += 1
    return pos


def _strip_dict(data: bytes, start: int, end: int) -> bytes | None:
    """El diccionario ``data[start:end]`` sin las claves de texto alternativo (``None`` si no tiene ninguna)."""
    pos = start + 2
    pairs: list[bytes] = []
    changed = False
    while True:
        pos = _skip_space(data, pos)
        if data.startswith(b">>", pos):
            break
        key_end = _skip_object(data, pos)
        key = data[pos:key_end]
        if not key.startswith(b"/"):
            raise _Unparsable
        value_start = _skip_space(data, key_end)
        value_end = _skip_object(data, value_start)
        # Un nombre puede venir con escapes (#41 es "A"): se compara ya resuelto
        # para que no sirvan de disfraz.
        name = _NAME_ESCAPE.sub(lambda m: bytes([int(m.group(1), 16)]), key[1:]).decode("latin-1")
        if name in _REPLACEMENT_KEYS:
            changed = True
        else:
            pairs.append(key + b" " + data[value_start:value_end])
        pos = value_end
    if pos + 2 != end:
        raise _Unparsable
    return b"<<" + b" ".join(pairs) + b">>" if changed else None


def _strip_replacement_stream(data: bytes) -> bytes | None:
    """Flujo de contenido sin texto alternativo en sus listas de propiedades (``None`` si no cambia)."""
    pos = 0
    n = len(data)
    out: list[bytes] = []
    copied = 0
    last: tuple[int, int] | None = None  # ultimo operando visto
    while True:
        pos = _skip_space(data, pos)
        if pos >= n:
            break
        start = pos
        pos = _skip_object(data, pos)
        token = data[start:pos]
        head = token[:1]
        if head in (b"(", b"<", b"[", b"/") or head in b"+-.0123456789" or token in (b"true", b"false", b"null"):
            last = (start, pos)
            continue
        if token == b"BI":
            # Imagen en linea: tras ID vienen datos binarios hasta EI, que no
            # se pueden leer como si fueran operadores.
            while True:
                pos = _skip_space(data, pos)
                item = pos
                pos = _skip_object(data, pos)
                if data[item:pos] == b"ID":
                    break
            end = _INLINE_END.search(data, pos)
            if end is None:
                raise _Unparsable
            pos = end.end()
        elif token in (b"BDC", b"DP") and last is not None and data.startswith(b"<<", last[0]):
            stripped = _strip_dict(data, last[0], last[1])
            if stripped is not None:
                out.append(data[copied : last[0]])
                out.append(stripped)
                copied = last[1]
        last = None
    if not out:
        return None
    out.append(data[copied:])
    return b"".join(out)


def _strip_properties(resources: Any) -> int:
    props = _get(resources, "Properties")
    if not mupdf.pdf_is_dict(props):
        return 0
    count = 0
    for i in range(mupdf.pdf_dict_len(props)):
        entry = mupdf.pdf_dict_get_val(props, i)
        if not mupdf.pdf_is_dict(entry):
            continue
        for key in _REPLACEMENT_KEYS:
            if _has(_get(entry, key)):
                mupdf.pdf_dict_dels(entry, key)
                count += 1
    return count


def _strip_replacement_text(doc: pymupdf.Document, page: pymupdf.Page) -> int:
    """Quita el texto alternativo (/ActualText, /Alt, /E) del contenido marcado de la pagina.

    Ese texto sustituye al de los glifos al copiar o buscar. Si una zona cubre
    solo parte de un tramo marcado, MuPDF borra los glifos tapados pero deja la
    propiedad con el texto completo: lo censurado seguiria saliendo al copiar.
    """
    pdf = _pdf(doc)
    count = _strip_properties(_page_resources(page))
    streams = list(page.get_contents())
    for item in doc.get_page_xobjects(page.number):
        xref = int(item[0])
        streams.append(xref)
        count += _strip_properties(_get(mupdf.pdf_load_object(pdf, xref), "Resources"))
    for xref in dict.fromkeys(streams):
        data = doc.xref_stream(xref)
        if not data or not _MAYBE_REPLACEMENT.search(data):
            continue
        try:
            cleaned = _strip_replacement_stream(data)
        except (_Unparsable, RecursionError):
            # Si no se entiende el flujo no se puede asegurar que no conserve
            # el texto: se abandona la censura entera (A10:2025).
            raise failed(
                _(
                    "No se pudo censurar esta página de forma segura: su contenido tiene un formato que no se reconoce. "
                    "No se ha modificado el documento."
                )
            ) from None
        if cleaned is not None:
            doc.update_stream(xref, cleaned)
            count += 1
    return count


# --------------------------------------------------- lectura de flujos de contenido

_TEXT_SHOWING = (b"Tj", b"TJ", b"'", b'"')
_PAINTING = (b"f", b"F", b"f*", b"B", b"B*", b"b", b"b*", b"S", b"s", b"sh")


def _operators(data: bytes) -> Iterator[tuple[bytes, list[bytes]]]:
    """Operadores de un flujo de contenido con sus operandos. Lanza ``_Unparsable`` si no se entiende."""
    pos = 0
    n = len(data)
    operands: list[bytes] = []
    while True:
        pos = _skip_space(data, pos)
        if pos >= n:
            return
        start = pos
        pos = _skip_object(data, pos)
        token = data[start:pos]
        head = token[:1]
        if head in (b"(", b"<", b"[", b"/") or head in b"+-.0123456789" or token in (b"true", b"false", b"null"):
            operands.append(token)
            continue
        if token == b"BI":
            # Imagen en linea: tras ID vienen datos binarios hasta EI.
            while True:
                pos = _skip_space(data, pos)
                item = pos
                pos = _skip_object(data, pos)
                if data[item:pos] == b"ID":
                    break
            end = _INLINE_END.search(data, pos)
            if end is None:
                raise _Unparsable
            yield b"BI", [data[pos : end.start()]]
            pos = end.end()
        else:
            yield token, operands
        operands = []


_STRING_ESCAPES = {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b", b"f": b"\f"}
_STRING_PIECE = re.compile(rb"\\([0-7]{1,3}|\r\n|[\s\S])")


def _string_bytes(token: bytes) -> bytes:
    """Bytes que representa una cadena de un flujo de contenido (literal o hexadecimal)."""
    if token.startswith(b"<"):
        digits = re.sub(rb"[^0-9A-Fa-f]", b"", token)
        return bytes.fromhex((digits + b"0" if len(digits) % 2 else digits).decode("ascii"))

    def unescape(match: re.Match[bytes]) -> bytes:
        code = match.group(1)
        if code[:1].isdigit():
            return bytes([int(code, 8) & 0xFF])
        if code in (b"\n", b"\r", b"\r\n"):
            return b""  # barra al final de linea: la cadena continua
        return _STRING_ESCAPES.get(code, code)

    return _STRING_PIECE.sub(unescape, token[1:-1])


def _shown_strings(operands: Sequence[bytes]) -> bytes:
    """Texto (codigos de glifo) que muestra un operador de texto, sin los ajustes de espaciado."""
    if not operands:
        return b""
    last = operands[-1]
    if not last.startswith(b"["):
        return _string_bytes(last) if last[:1] in (b"(", b"<") else b""
    out: list[bytes] = []
    pos = 1
    end = len(last) - 1
    while True:
        pos = _skip_space(last, pos)
        if pos >= end:
            break
        start = pos
        pos = _skip_object(last, pos)
        piece = last[start:pos]
        if piece[:1] in (b"(", b"<") and not piece.startswith(b"<<"):
            out.append(_string_bytes(piece))
    return b"".join(out)


def _form_signature(doc: pymupdf.Document, xref: int, depth: int = 0) -> tuple[Any, ...] | None:
    """Lo que dibuja un Form XObject, sin su formato: texto, imagenes, formularios anidados y trazos.

    MuPDF sustituye por una copia filtrada TODOS los formularios de una pagina
    censurada, tambien los que no tocan ninguna zona (un membrete, por
    ejemplo). Comparar esta huella entre el original y las copias dice si la
    censura le quito algo de verdad. ``None`` si el flujo no se entiende.
    """
    if depth > 8:
        return None
    try:
        data = doc.xref_stream(xref)
    except Exception:  # noqa: BLE001
        return None
    if data is None:
        return None
    pdf = _pdf(doc)
    xobjects = _get(_get(mupdf.pdf_load_object(pdf, xref), "Resources"), "XObject")
    text: list[bytes] = []
    drawn: list[Any] = []
    try:
        for operator, operands in _operators(data):
            if operator in _TEXT_SHOWING:
                text.append(_shown_strings(operands))
            elif operator in _PAINTING:
                drawn.append(operator)
            elif operator == b"BI":
                drawn.append((b"BI", len(operands[0])))
            elif operator == b"Do" and operands and operands[-1].startswith(b"/"):
                name = _NAME_ESCAPE.sub(lambda m: bytes([int(m.group(1), 16)]), operands[-1][1:])
                target = _get(xobjects, name.decode("latin-1"))
                num = _ref(target)
                if not num:
                    continue
                if mupdf.pdf_is_dict(target) and doc.xref_get_key(num, "Subtype")[1] == "/Form":
                    drawn.append((b"Form", _form_signature(doc, num, depth + 1)))
                else:
                    drawn.append((b"Image", num))
    except (_Unparsable, RecursionError, ValueError):
        return None
    return (b"".join(text), tuple(drawn))


def _shows_text(doc: pymupdf.Document, xref: int, depth: int = 0) -> bool:
    """El flujo (o un formulario que dibuje) muestra texto. Si no se entiende, se supone que si."""
    if depth > 8:
        return True
    try:
        data = doc.xref_stream(xref)
    except Exception:  # noqa: BLE001
        return True
    if data is None:
        return True
    xobjects = _get(_get(mupdf.pdf_load_object(_pdf(doc), xref), "Resources"), "XObject")
    try:
        for operator, operands in _operators(data):
            if operator in _TEXT_SHOWING:
                return True
            if operator == b"Do" and operands and operands[-1].startswith(b"/"):
                name = _NAME_ESCAPE.sub(lambda m: bytes([int(m.group(1), 16)]), operands[-1][1:])
                num = _ref(_get(xobjects, name.decode("latin-1")))
                if num and doc.xref_get_key(num, "Subtype")[1] == "/Form" and _shows_text(doc, num, depth + 1):
                    return True
    except (_Unparsable, RecursionError):
        return True
    return False


def _mask_groups(resources: Any) -> list[int]:
    """Grupos de mascara suave (/ExtGState ... /SMask /G) que cuelgan de unos recursos y de sus formularios."""
    found: list[int] = []
    seen: set[int] = set()
    stack: list[tuple[Any, int]] = [(resources, 0)]
    while stack:
        res, depth = stack.pop()
        if not mupdf.pdf_is_dict(res) or depth > 16:
            continue
        states = _get(res, "ExtGState")
        if mupdf.pdf_is_dict(states):
            for i in range(min(mupdf.pdf_dict_len(states), 10_000)):
                mask = _get(mupdf.pdf_dict_get_val(states, i), "SMask")
                group = _get(mask, "G") if mupdf.pdf_is_dict(mask) else None
                num = _ref(group) if group is not None else 0
                if num and num not in seen:
                    seen.add(num)
                    found.append(num)
                    stack.append((_get(group, "Resources"), depth + 1))
        for category in ("XObject", "Pattern"):
            entries = _get(res, category)
            if not mupdf.pdf_is_dict(entries):
                continue
            for i in range(min(mupdf.pdf_dict_len(entries), 10_000)):
                value = mupdf.pdf_dict_get_val(entries, i)
                num = _ref(value)
                if num and num not in seen and mupdf.pdf_is_stream(value):
                    seen.add(num)
                    stack.append((_get(value, "Resources"), depth + 1))
    return found


def _reject_masked_text(doc: pymupdf.Document, page: pymupdf.Page) -> None:
    """Aborta si la pagina conserva texto dentro de una mascara de transparencia.

    Una mascara suave es un dibujo aparte que decide cuanto se ve de lo que se
    pinta a traves de ella. MuPDF no entra en ese dibujo al censurar: quita lo
    pintado, pero si la mascara lleva texto (un titulo con relleno degradado,
    por ejemplo), la cadena sigue en el archivo sin verse. No hay forma fiable
    de saber si cae en una zona, asi que se falla del lado seguro (A10:2025).
    """
    if any(_shows_text(doc, group) for group in _mask_groups(_page_resources(page))):
        raise failed(
            _(
                "No se pudo censurar esta página de forma segura: tiene texto dentro de una máscara de transparencia, "
                "que no se puede eliminar. No se ha modificado el documento."
            )
        )


# -------------------------------------------- verificacion de lo que queda a la vista

# La zona se dibuja a esta escala (pixeles por punto) y se deja fuera un borde:
# el suavizado de lo que queda justo al lado no cuenta.
_CHECK_SCALE = 2.0
_CHECK_INSET = 0.75
_CHECK_MAX_PIXELS = 4_000_000
# Un pixel "con dibujo" es el que se aparta de la media de sus dos vecinos: lo
# cumplen los bordes de una letra, un trazo o la textura de una foto, y no un
# fondo liso ni un degradado, por inclinado que sea.
_CHECK_EDGE = 10
_CHECK_MIN_PIXELS = 4
# Lo que puede asomar una letra vecina se mide con su tamano (hasta este tope);
# en zonas grandes solo se tolera en esta fraccion junto a cada borde. Y lado
# minimo (en puntos) de una imagen para tolerar su contorno.
_CHECK_MAX_FONT = 40.0
_CHECK_BORDER = 0.25
_CHECK_MIN_IMAGE = 8.0


def _rough_pixels(image: Image.Image, valid: Image.Image) -> int:
    """Cuantos pixeles de la imagen tienen dibujo (bordes o textura), contando solo donde ``valid`` es blanco."""
    width, height = image.size
    total = 0
    for horizontal in (True, False):
        if (width if horizontal else height) < 3:
            continue
        if horizontal:
            boxes = [(0, 0, width - 2, height), (1, 0, width - 1, height), (2, 0, width, height)]
        else:
            boxes = [(0, 0, width, height - 2), (0, 1, width, height - 1), (0, 2, width, height)]
        before, middle, after = (image.crop(box) for box in boxes)
        bend = ImageChops.difference(ImageChops.add(before, after, scale=2.0), middle).split()
        worst = ImageChops.lighter(ImageChops.lighter(bend[0], bend[1]), bend[2])
        edges = worst.point(lambda value: 255 if value >= _CHECK_EDGE else 0)
        usable = [valid.crop(box) for box in boxes]
        edges = ImageChops.darker(edges, ImageChops.darker(ImageChops.darker(usable[0], usable[1]), usable[2]))
        total += edges.histogram()[255]
    return total


def _edge_boxes(page: pymupdf.Page, zones: Sequence[pymupdf.Rect]) -> list[tuple[pymupdf.Rect, float]]:
    """Cajas (U) de las letras que quedan en la pagina junto a alguna zona, ya con su margen, y ese margen.

    MuPDF no quita un caracter que solo roza la zona, y la tinta de una letra
    se sale de su caja (el gancho de una "f" cursiva, un acento, un rabo): lo
    que asoma a la zona desde una letra vecina no es un resto de lo censurado.
    El margen va con el tamano de la letra, hasta un tope. Las letras que no
    se pintan (texto invisible) no tienen tinta que asome: no cuentan.
    """
    found: list[tuple[pymupdf.Rect, float]] = []
    data = page.get_text("rawdict", flags=_VERIFY_FLAGS | pymupdf.TEXT_COLLECT_STYLES)
    for block in data.get("blocks", ()):
        if block.get("type", 0) != 0:
            continue
        for line in block.get("lines", ()):
            for span in line.get("spans", ()):
                if _span_hidden(span):
                    continue
                try:
                    pad = 0.3 * min(max(float(span.get("size", 0.0)), 1.0), _CHECK_MAX_FONT) + 0.75
                except (TypeError, ValueError):
                    pad = 1.0
                for char in span.get("chars", ()):
                    if not char["c"] or char["c"].isspace():
                        continue
                    box = pymupdf.Rect(char["bbox"]) + (-pad, -pad, pad, pad)
                    if _overlaps_any(box, zones):
                        found.append((box, pad))
    return found


def _image_boxes(page: pymupdf.Page) -> list[pymupdf.Rect]:
    """Cajas (U) de las imagenes que dibuja la pagina."""
    try:
        infos = page.get_image_info()
    except Exception:  # noqa: BLE001 - sin esta ayuda la comprobacion solo es mas estricta
        return []
    return [pymupdf.Rect(info.get("bbox") or (0, 0, 0, 0)) for info in infos]


def _verify_nothing_visible(page: pymupdf.Page, geom: Geom, zones: Sequence[pymupdf.Rect]) -> None:
    """Comprueba, dibujando cada zona, que no queda nada reconocible debajo. Aborta si queda.

    La comprobacion de texto mira donde *dice* estar cada caracter; esta mira
    lo que de verdad se pinta. Detecta lo que MuPDF no sabe eliminar o no ve en
    su sitio: una letra cuya tinta esta lejos de su caja (fuentes Type3 o
    manipuladas), un degradado recortado con forma, una imagen que no se pudo
    reescribir. Se hace antes de pintar el recuadro negro, que lo taparia todo.

    Lo que queda en una zona bien censurada es papel en blanco, los pixeles
    borrados de una imagen (blancos) o, como mucho, un fondo liso o degradado:
    nada de eso tiene bordes. Se toleran dos cosas que si los tienen sin ser
    restos: las letras vecinas que asoman a la zona y el contorno de una imagen
    sobre un fondo de color.
    """
    width, height = geom.size_u
    boxes = []
    for zone in zones:
        box = pymupdf.Rect(
            max(zone.x0, 0.0) + _CHECK_INSET, max(zone.y0, 0.0) + _CHECK_INSET,
            min(zone.x1, width) - _CHECK_INSET, min(zone.y1, height) - _CHECK_INSET,
        )
        if box.x1 - box.x0 >= 1.0 and box.y1 - box.y0 >= 1.0:
            boxes.append(box)
    if not boxes:
        return
    listing = page.get_displaylist(annots=False)
    visible = pymupdf.Rect(listing.rect)
    tolerated: list[tuple[pymupdf.Rect, float]] | None = None
    outlines: list[pymupdf.Rect] = []
    for box in boxes:
        clip = (box * geom.u2d).normalize() & visible
        if clip.is_empty or clip.width < 0.5 or clip.height < 0.5:
            continue  # la zona queda fuera de lo que se dibuja de la pagina
        scale = min(_CHECK_SCALE, math.sqrt(_CHECK_MAX_PIXELS / max(clip.width * clip.height, 1.0)))
        pix = listing.get_pixmap(matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csRGB, clip=clip, alpha=False)
        if pix.width < 1 or pix.height < 1:
            continue
        if pix.n != 3:
            raise failed(_("No se pudo comprobar el resultado de la censura. No se ha modificado el documento."))
        image = Image.frombuffer("RGB", (pix.width, pix.height), bytes(pix.samples), "raw", "RGB", pix.stride, 1)
        if min(low for low, _high in image.getextrema()) >= 250:
            continue  # en blanco: el caso normal
        if tolerated is None:  # solo se calculan si hacen falta
            tolerated = _edge_boxes(page, zones)
            outlines = _image_boxes(page)
        valid = Image.new("L", image.size, 255)
        draw = ImageDraw.Draw(valid)

        def pixels(rect: pymupdf.Rect) -> tuple[float, float, float, float]:
            shown = (rect * geom.u2d).normalize()
            left, top = pix.x, pix.y
            return (shown.x0 * scale - left, shown.y0 * scale - top, shown.x1 * scale - left, shown.y1 * scale - top)

        reach = 0.0
        for rect, pad in tolerated:
            if _overlap(rect, box):
                draw.rectangle(pixels(rect), fill=0)
                reach = max(reach, pad)
        # La tolerancia con las letras vecinas solo vale junto a los bordes. Si
        # la zona es bastante mayor que lo que una letra puede asomar, su
        # centro se comprueba siempre: no basta rodearla de letras (aunque no
        # se vean) para dejar sin efecto la prueba.
        border = _CHECK_BORDER * min(box.width, box.height)
        if reach and border >= reach:
            draw.rectangle(pixels(box + (border, border, -border, -border)), fill=255)
        for rect in outlines:
            if rect.width < _CHECK_MIN_IMAGE or rect.height < _CHECK_MIN_IMAGE:
                continue  # el contorno de una imagen diminuta no justifica ningun borde
            x0, y0, x1, y1 = pixels(rect)
            draw.rectangle((x0 - 2.5, y0 - 2.5, x1 + 2.5, y1 + 2.5), outline=0, width=5)
        if _rough_pixels(image, valid) >= _CHECK_MIN_PIXELS:
            raise failed(
                _("No se pudo eliminar por completo lo que hay bajo una zona censurada. No se ha modificado el documento.")
            )


# ------------------------------------------------------------ censura de pagina

# Tipos de elemento de Folio que se ponen iguales en muchas paginas.
_DECORATIONS = ("watermark", "header", "footer", "pagenum")


@dataclass
class Outcome:
    marks: int = 0
    elements: int = 0
    annotations: int = 0
    widgets: list[RemovedWidget] = field(default_factory=list)
    ocr_removed: bool = False  # se ha quitado la capa de OCR anadida con Folio
    hidden_text: bool = False  # la pagina conserva texto invisible fuera de las zonas
    # Objetos de contenido que la pagina dibujaba y ya no: la imagen original
    # de una zona, el flujo sin censurar, los formularios que MuPDF ha
    # sustituido por copias filtradas... Quien llama debe pasarlos por
    # ``release_retired``: si otra pagina los sigue nombrando en sus recursos,
    # seguirian en el archivo.
    retired: set[int] = field(default_factory=set)
    # Formularios que la pagina dibuja despues (las copias filtradas de MuPDF).
    forms: set[int] = field(default_factory=set)
    # De lo retirado, lo que pertenecia a adornos de Folio que se repiten en
    # todas las paginas a proposito (marca de agua, encabezado, pie, numero).
    decoration: set[int] = field(default_factory=set)


def redact_page(doc: pymupdf.Document, page: pymupdf.Page) -> Outcome | None:
    """Aplica las marcas pendientes de una pagina. ``None`` si no tiene ninguna."""
    marks = page_marks(page)
    if not marks:
        return None
    geom = Geom(page)
    zones = [zone for _, _, mark_zones in marks for zone in mark_zones]
    outcome = Outcome(marks=len(marks))
    # Lo que la pagina dibuja ahora; al final se compara con lo que dibuje.
    before = _content_objects(doc, _page_obj(page), used_only=True)

    # (a) Elementos anadidos: su texto vive en un XObject y, aunque MuPDF
    # tambien filtra esos objetos, quitar el elemento entero no depende de ello.
    def touches(element: _Element) -> bool:
        return _quad_hits_any(element.claimed, zones) or (
            element.actual is not None and _quad_hits_any(element.actual, zones)
        )

    touched, kept, leading = _classify_elements(doc, page, geom, touches)
    adornments = usage.ContentMap(doc)
    for element in touched:
        if element.placed.kind in _DECORATIONS:
            # Una marca de agua o un encabezado se van enteros de esta pagina y
            # siguen, a proposito, en las demas: no es "lo censurado, repetido".
            outcome.decoration |= adornments.stream(element.xobject or element.placed.xobject, used_only=False)
    for element in touched:
        content.remove(doc, page, element.placed)
    outcome.elements = len(touched)
    # La capa de OCR de Folio ocupa la pagina entera: cualquier zona la toca y
    # se va completa. Se avisa para que la interfaz ofrezca reconocer de nuevo.
    outcome.ocr_removed = any(element.placed.kind == "ocr" for element in touched)

    # (b) Anotaciones, enlaces y campos.
    outcome.annotations, outcome.widgets = _remove_touched_annots(doc, page, zones)

    # La miniatura es una imagen de la pagina sin censurar, y /PieceInfo puede
    # guardar el original editable de otra aplicacion.
    drop_key(doc, page.xref, "Thumb")
    drop_key(doc, page.xref, "PieceInfo")

    # (c) Contenido de la pagina.
    #  - texto: se elimina (cualquier otra opcion es insegura);
    #  - imagenes: se borran solo los pixeles de la zona, para no perder el
    #    resto de una pagina escaneada;
    #  - dibujo vectorial: se elimina todo trazado que toque la zona. Quitar
    #    solo lo que queda cubierto por completo dejaria bajo el recuadro texto
    #    convertido a curvas o una firma manuscrita vectorial.
    _run_mupdf(doc, page, kept, leading, text=_TEXT_REMOVE, images=_IMAGES_PIXELS, graphics=_ART_TOUCHED)

    # (d) Verificacion: si algun caracter sigue dentro de una zona se intenta
    # quitar de forma dirigida y, si aun asi queda, se aborta todo.
    for attempt in range(_RETRIES + 1):
        left = _leftover_text(page, zones)
        if not left:
            break
        if attempt == _RETRIES:
            raise failed(
                _("No se pudo eliminar por completo el texto de una zona censurada. No se ha modificado el documento.")
            )
        for box in left:
            pad_x, pad_y = box.width * 0.1 + 0.5, box.height * 0.1 + 0.5
            page.add_redact_annot(box + (-pad_x, -pad_y, pad_x, pad_y), cross_out=False)
        # Solo texto: estas marcas de ayuda sobresalen del recuadro negro y no
        # deben borrar la imagen ni el dibujo de alrededor.
        _run_mupdf(doc, page, kept, leading, text=_TEXT_REMOVE, images=_IMAGES_NONE, graphics=_ART_NONE)

    # (e) Lo que MuPDF no alcanza: texto dentro de mascaras de transparencia y
    # cualquier cosa que siga viendose en la zona. Se comprueba ANTES de pintar
    # el recuadro negro; despues ya no se distinguiria nada.
    _reject_masked_text(doc, page)
    _verify_nothing_visible(page, geom, zones)
    _draw_fills(doc, page, geom, zones)

    _strip_replacement_text(doc, page)
    # Texto invisible que queda en la pagina (capa de OCR de otro programa): lo
    # que cae en las zonas ya se ha eliminado, pero si esa capa esta descolocada
    # respecto a la imagen puede conservar texto que el usuario no ve. No se
    # borra aqui (no se ha pedido), se avisa.
    outcome.hidden_text = bool(_hidden_spans(page))
    # (f) Que ha dejado de dibujar la pagina. MuPDF le ha puesto recursos
    # nuevos solo con lo que usa; lo demas se ha quedado atras.
    after = _content_objects(doc, _page_obj(page), used_only=False)
    outcome.retired = set(before - after)
    outcome.forms = {xref for xref in after if doc.xref_get_key(xref, "Subtype")[1] == "/Form"}
    return outcome


def release_retired(
    doc: pymupdf.Document,
    retired: Iterable[int],
    forms: Iterable[int] = (),
    decoration: Iterable[int] = (),
    *,
    tick: Callable[[], None] | None = None,
) -> list[int]:
    """Saca del documento lo que las paginas tratadas han dejado de dibujar.

    Un objeto retirado que ya nadie nombra lo libera ``purge_orphans``. Pero un
    documento puede seguir nombrandolo desde los recursos de otras paginas (o
    del arbol de paginas) sin que ninguna lo dibuje: se anula aqui, y deja de
    estar en el archivo. Si otra pagina SI lo dibuja, se conserva (esa pagina
    lo muestra) y se devuelven los indices de esas paginas para que la interfaz
    avise de que lo censurado sigue a la vista en ellas.

    Para no avisar en falso se descartan dos casos:

    * ``forms`` son las copias filtradas que MuPDF ha hecho de los formularios
      de las paginas censuradas. Un formulario que sigue en otras paginas (un
      membrete) solo cuenta si la censura le quito algo, es decir, si ninguna
      de sus copias dibuja lo mismo que el;
    * ``decoration`` son los objetos de los adornos de Folio (marca de agua,
      encabezado...) retirados con la zona: se repiten en las demas paginas a
      proposito.
    """
    try:
        drawn = usage.release_undrawn(doc, retired, tick=tick)
    except usage.TooDeep:
        raise _too_deep() from None
    if not drawn:
        return []
    quiet = set(decoration)
    copies: set[tuple[Any, ...]] | None = None
    pages: set[int] = set()
    for xref, where in drawn.items():
        if xref in quiet:
            continue
        if doc.xref_get_key(xref, "Subtype")[1] == "/Form":
            if copies is None:
                copies = {sig for sig in (_form_signature(doc, copy) for copy in forms) if sig is not None}
            original = _form_signature(doc, xref)
            if original is not None and original in copies:
                continue  # intacto: la censura no lo toco
        pages.update(where)
    return sorted(pages)


# ================================================== recortar de verdad la pagina

@dataclass
class Trimmed:
    removed: int = 0  # elementos, anotaciones, enlaces y campos quitados (enteros fuera)
    widgets: list[RemovedWidget] = field(default_factory=list)
    retired: set[int] = field(default_factory=set)
    forms: set[int] = field(default_factory=set)


def erase_outside(doc: pymupdf.Document, page: pymupdf.Page, keep_u: pymupdf.Rect) -> tuple[pymupdf.Page, Trimmed]:
    """Elimina de la pagina lo que queda fuera de ``keep_u`` (en U): recortar de verdad.

    Recortar solo cambia la caja visible; lo de fuera sigue en el archivo. Aqui
    se elimina con el mismo motor que la censura:

    * texto: las letras que caen fuera (o sobre el borde) se eliminan;
    * imagenes: se borran sus pixeles de fuera (la parte de dentro se queda);
    * dibujo vectorial: se eliminan los trazados que quedan enteros fuera;
    * elementos de Folio, anotaciones, enlaces, campos y marcas de censura
      pendientes que quedan enteros fuera se quitan.

    Lo que cruza el borde se conserva (se ve en parte). Quien llama debe pasar
    ``retired`` por ``release_retired`` y despues ``purge_orphans``, y
    reescribir el archivo de trabajo entero, como al censurar.
    """
    geom = Geom(page)
    keep = pymupdf.Rect(keep_u).normalize()
    room = pymupdf.Rect(_EVERYWHERE)
    bands = [
        band
        for band in (
            pymupdf.Rect(room.x0, room.y0, room.x1, keep.y0),
            pymupdf.Rect(room.x0, keep.y1, room.x1, room.y1),
            pymupdf.Rect(room.x0, keep.y0, keep.x0, keep.y1),
            pymupdf.Rect(keep.x1, keep.y0, room.x1, keep.y1),
        )
        if band.width > 0 and band.height > 0
    ]
    outcome = Trimmed()
    before = _content_objects(doc, _page_obj(page), used_only=True)

    def away(points: Sequence[Sequence[float]]) -> bool:
        xs, ys = [pt[0] for pt in points], [pt[1] for pt in points]
        return not _overlap(pymupdf.Rect(min(xs), min(ys), max(xs), max(ys)), keep)

    def touches(element: _Element) -> bool:
        return _quad_hits_any(element.claimed, bands) or (element.actual is not None and _quad_hits_any(element.actual, bands))

    touched, kept, leading = _classify_elements(doc, page, geom, touches)
    for element in touched:
        # Enteros fuera: se quitan. Los que cruzan el borde pasan por el filtro
        # como contenido normal y pierden lo que tienen fuera.
        if away(element.claimed) and (element.actual is None or away(element.actual)):
            content.remove(doc, page, element.placed)
            outcome.removed += 1

    # Las ventanas emergentes van con su anotacion: no se juzgan por separado.
    for xref in [x for x, kind, _ in page.annot_xrefs() if kind not in (WIDGET, LINK, pymupdf.PDF_ANNOT_POPUP)]:
        if xref not in {a[0] for a in page.annot_xrefs()}:
            continue  # se fue con la anotacion de la que era respuesta
        annot = page.load_annot(xref)
        if annot is not None and not _overlap(_solid(geom.api_to_u(annot.rect)), keep):
            page.delete_annot(annot)
            outcome.removed += 1
    for link in page.get_links():
        if link.get("from") is not None and not _overlap(_solid(geom.rect_u(link["from"])), keep):
            page.delete_link(link)
            outcome.removed += 1
    for xref in [x for x, kind, _ in page.annot_xrefs() if kind == WIDGET]:
        widget = page.load_widget(xref)
        if not widget or _overlap(_solid(geom.api_to_u(widget.rect)), keep):
            continue
        kind, value = doc.xref_get_key(xref, "Parent")
        parent = int(value.split()[0]) if kind == "xref" else 0
        kind, value = doc.xref_get_key(xref, "AS")
        outcome.widgets.append(RemovedWidget(xref, parent, value if kind == "name" else ""))
        page.delete_widget(widget)
        outcome.removed += 1
    drop_key(doc, page.xref, "Thumb")  # la miniatura mostraba la pagina entera

    # Las marcas de censura pendientes del usuario no deben aplicarse de rebote.
    page, annots = _detach_annots(doc, page)
    for band in bands:
        page.add_redact_annot(band, cross_out=False)
    _run_mupdf(doc, page, kept, leading, text=_TEXT_REMOVE, images=_IMAGES_PIXELS, graphics=mupdf.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED)
    page = _attach_annots(doc, page, annots)
    after = _content_objects(doc, _page_obj(page), used_only=False)
    outcome.retired = set(before - after)
    outcome.forms = {xref for xref in after if doc.xref_get_key(xref, "Subtype")[1] == "/Form"}
    return page, outcome


# ================================================= censura reforzada (a imagen)

FLATTEN_DPI = 300
_FLATTEN_MAX_PIXELS = 48_000_000  # el mismo tope que el dibujo en pantalla
_FLATTEN_CHECK_SCALE = 1.0
_FLATTEN_TOLERANCE = 6.0  # diferencia media admitida (0-255): compresion y remuestreo
# Lo que una pagina puede llevar ademas de su contenido y que, tras convertirla
# en imagen, sobra o podria guardar algo de lo que habia: miniatura, datos de
# otras aplicaciones, acciones al abrirla, hilos de articulos, separaciones de
# imprenta, presentacion y la entrada en el arbol de estructura.
_FLATTEN_DROP = (
    "Annots", "Thumb", "PieceInfo", "Group", "B", "AA", "StructParents", "Tabs", "VP",
    "Metadata", "SeparationInfo", "PresSteps", "Trans", "Dur", "ID", "PZ",
)


def _page_image(page: pymupdf.Page, scale: float) -> Image.Image:
    pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False, annots=True)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def flatten_page(doc: pymupdf.Document, page: pymupdf.Page) -> tuple[pymupdf.Page, list[RemovedWidget]]:
    """Sustituye todo lo que tiene la pagina por una imagen de como se ve ahora.

    Es la opcion de maxima seguridad tras censurar: en la pagina ya no queda
    texto, ni capas ocultas, ni objetos que alguien pudiera recuperar con otro
    programa; solo los pixeles que se ven (anotaciones incluidas). Lo demas que
    la pagina usaba deja de estar referenciado y ``purge_orphans`` lo saca del
    archivo. Devuelve la pagina recargada y los campos de formulario que se
    han quitado (para ``prune_fields``).

    La imagen se coloca con la misma transformacion con la que MuPDF dibuja la
    pagina (giro, recorte, origen del MediaBox, unidad de usuario) y despues se
    comprueba que la pagina nueva se ve igual que la de antes. Si no, se lanza
    un error y quien llama revierte la transaccion.
    """
    rect = page.rect
    scale = min(FLATTEN_DPI / 72, math.sqrt(_FLATTEN_MAX_PIXELS / max(rect.width * rect.height, 1.0)))
    before = _page_image(page, _FLATTEN_CHECK_SCALE)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False, annots=True)
    data = pix.tobytes("jpeg", jpg_quality=92)
    del pix

    widgets: list[RemovedWidget] = []
    for xref in [x for x, kind, _ in page.annot_xrefs() if kind == WIDGET]:
        kind, value = doc.xref_get_key(xref, "Parent")
        parent = int(value.split()[0]) if kind == "xref" else 0
        kind, value = doc.xref_get_key(xref, "AS")
        widgets.append(RemovedWidget(xref, parent, value if kind == "name" else ""))

    # Transformacion de la pagina: de su espacio PDF a como se ve (la misma
    # que usa MuPDF al dibujarla). Su inversa lleva lo visible al espacio PDF.
    pdfpage = pymupdf._as_pdf_page(page)
    bound = mupdf.FzRect(0)
    ctm = mupdf.FzMatrix()
    mupdf.pdf_page_transform(pdfpage, bound, ctm)
    to_pdf = pymupdf.Matrix(mupdf.fz_invert_matrix(ctm))
    # Cuadrado unidad de la imagen -> pagina tal como se ve (origen arriba a la izquierda).
    placement = pymupdf.Matrix(rect.width, 0, 0, -rect.height, rect.x0, rect.y1) * to_pdf

    xref = page.xref
    for key in _FLATTEN_DROP:
        drop_key(doc, xref, key)
    pdf = _pdf(doc)
    image = mupdf.pdf_add_image(pdf, mupdf.fz_new_image_from_buffer(mupdf.fz_new_buffer_from_copied_data(data)))
    xobjects = mupdf.pdf_new_dict(pdf, 1)
    mupdf.pdf_dict_puts(xobjects, "FolioPagina", image)
    resources = mupdf.pdf_new_dict(pdf, 1)
    mupdf.pdf_dict_puts(resources, "XObject", xobjects)
    stream = mupdf.pdf_add_stream(
        pdf, mupdf.fz_new_buffer_from_copied_data(f"q {matrix_operand(placement)} cm /FolioPagina Do Q".encode("ascii")),
        mupdf.PdfObj(), 0,
    )
    # Recursos y contenido nuevos: lo que la pagina usaba deja de estar enlazado.
    page_obj = _page_obj(page)
    mupdf.pdf_dict_puts(page_obj, "Resources", resources)
    mupdf.pdf_dict_puts(page_obj, "Contents", stream)
    del page_obj, pdfpage
    page = _reload(doc, page)

    # Comprobacion: lo que se ve ahora es lo que se veia (y no queda nada mas).
    if page.annot_xrefs() or page.get_text("text", flags=_VERIFY_FLAGS).strip():
        raise failed(_("No se pudo convertir la página en imagen de forma segura. No se ha modificado el documento."))
    after = _page_image(page, _FLATTEN_CHECK_SCALE)
    if after.size != before.size or _mean_difference(before, after) > _FLATTEN_TOLERANCE:
        raise failed(_("La página convertida en imagen no coincide con la original. No se ha modificado el documento."))
    return page, widgets


def _mean_difference(a: Image.Image, b: Image.Image) -> float:
    histogram = ImageChops.difference(a, b).convert("L").histogram()
    total = sum(histogram)
    return sum(level * count for level, count in enumerate(histogram)) / max(total, 1)


def erase_area(doc: pymupdf.Document, page: pymupdf.Page, area: Any, *, text: bool, images: bool) -> pymupdf.Page:
    """Elimina del contenido de la pagina el texto y/o las imagenes de una zona (en U).

    Es para EDITAR (quitar una linea o una imagen), no para censurar. A
    diferencia de ``Page.apply_redactions``, no aplica de rebote las marcas de
    censura pendientes de la pagina ni borra sus enlaces: una marca aplicada a
    medias dejaria un recuadro negro sobre contenido que sigue en el archivo.
    Devuelve la pagina recargada; la recibida deja de ser valida.
    """
    return erase_areas(doc, page, [area], text=text, images=images)


def erase_areas(doc: pymupdf.Document, page: pymupdf.Page, areas: list[Any], *, text: bool, images: bool) -> pymupdf.Page:
    """Como ``erase_area`` con varias zonas a la vez (las lineas de un parrafo), en una sola pasada."""
    page, annots = _detach_annots(doc, page)
    for area in areas:
        page.add_redact_annot(pymupdf.Rect(area), cross_out=False)
    _run_mupdf(
        doc, page, [], set(),
        text=_TEXT_REMOVE if text else mupdf.PDF_REDACT_TEXT_NONE,
        images=mupdf.PDF_REDACT_IMAGE_REMOVE if images else _IMAGES_NONE,
        graphics=_ART_NONE,
    )
    return _attach_annots(doc, page, annots)


# ============================================================ texto oculto

def _span_hidden(span: dict[str, Any]) -> bool:
    """El tramo de texto no se ve (mismo criterio que ``Document.scrub``). Requiere ``TEXT_COLLECT_STYLES``."""
    filled = getattr(mupdf, "FZ_STEXT_FILLED", None)
    stroked = getattr(mupdf, "FZ_STEXT_STROKED", None)
    font = span.get("font")
    flags = span.get("char_flags")
    return (
        (isinstance(font, str) and font.split("+")[-1] == "GlyphLessFont")  # fuente de OCR sin glifos
        or span.get("alpha") == 0
        or (
            isinstance(flags, int) and isinstance(filled, int) and isinstance(stroked, int)
            and not flags & filled and not flags & stroked
        )
    )


def _hidden_spans(page: pymupdf.Page) -> list[pymupdf.Rect]:
    """Cajas del texto que no se ve."""
    out: list[pymupdf.Rect] = []
    data = page.get_text("dict", flags=_HIDDEN_FLAGS)
    for block in data.get("blocks", ()):
        if block.get("type", 0) != 0:
            continue
        for line in block.get("lines", ()):
            for span in line.get("spans", ()):
                rect = pymupdf.Rect(span.get("bbox") or (0, 0, 0, 0))
                if _span_hidden(span) and not rect.is_empty and finite(rect):
                    out.append(rect)
    return out


def remove_hidden_text(doc: pymupdf.Document, page: pymupdf.Page, retired: set[int] | None = None) -> int:
    """Elimina el texto invisible de una pagina (capa de OCR, texto transparente).

    Devuelve cuantos tramos ocultos habia. ``page`` deja de ser valida al volver.
    En ``retired`` se apuntan los objetos de contenido que la pagina deja de
    dibujar (ver ``Outcome.retired``).
    """
    found = len(_hidden_spans(page))
    if not found:
        return 0
    geom = Geom(page)
    number = page.number
    before = _content_objects(doc, _page_obj(page), used_only=True) if retired is not None else frozenset()
    for placed in content.list_objects(doc, page):
        # Capa de OCR puesta con Folio, o elemento totalmente transparente.
        if placed.kind == "ocr" or placed.opacity <= 0.005:
            content.remove(doc, page, placed)
    # Las marcas de censura pendientes y los enlaces no son parte de esta
    # limpieza (la "zona" es la pagina entera): se apartan mientras dura.
    page, annots = _detach_annots(doc, page)
    for shield in (True, False):
        if not _hidden_spans(page):
            break
        kept: list[_Element] = []
        leading: set[int] = set()
        if shield:
            # Los elementos anadidos son visibles: se apartan para que sigan
            # siendo editables. Si aun asi queda texto oculto, se repite sin
            # apartar nada.
            _removed, kept, leading = _classify_elements(doc, page, geom, lambda _element: False)
        # Solo texto invisible y en toda la pagina: no depende de la precision
        # de las cajas y no toca el texto visible que haya encima.
        page.add_redact_annot(pymupdf.Rect(_EVERYWHERE), cross_out=False)
        _run_mupdf(doc, page, kept, leading, text=_TEXT_INVISIBLE, images=_IMAGES_NONE, graphics=_ART_NONE)
    left = _hidden_spans(page)
    if left:
        # Ultimo recurso (texto oculto por otros medios): quitar todo el texto
        # de esas cajas, como hace Document.scrub.
        for rect in left:
            page.add_redact_annot(rect, cross_out=False)
        _run_mupdf(doc, page, [], set(), text=_TEXT_REMOVE, images=_IMAGES_NONE, graphics=_ART_NONE)
        if _hidden_spans(page):
            raise failed(_("No se pudo eliminar todo el texto oculto. No se ha modificado el documento."))
    _attach_annots(doc, page, annots)
    if retired is not None:
        after = _content_objects(doc, mupdf.pdf_lookup_page_obj(_pdf(doc), number), used_only=False)
        retired.update(before - after)
    return found


# ============================================================ guiones y metadatos

# Acciones que llevan codigo en /JS: las de JavaScript y las de contenido
# multimedia (/Rendition), que pueden ejecutar un guion al dispararse.
_SCRIPTED_ACTIONS = ("JavaScript", "Rendition")


def scrub_objects(
    doc: pymupdf.Document, *, javascript: bool, metadata: bool, tick: Callable[[], None] | None = None
) -> tuple[int, int]:
    """Vacia los guiones y quita los metadatos de todos los objetos del documento, en una sola pasada.

    Devuelve ``(guiones, metadatos)``. Sustituye a ``Document.scrub``, que:

    * solo ve las acciones JavaScript que son un objeto propio (las escritas
      dentro de una anotacion, un campo o el catalogo se le escapan) y no mira
      las de tipo /Rendition;
    * no borra un /Info escrito directamente en el ``trailer``;
    * recorre la tabla de objetos entera serializando cada entrada, exista o
      no, sin dejar cancelar: un PDF minusculo que declare millones de objetos
      lo tiene ocupado minutos (A06:2025).
    """
    pdf = _pdf(doc)
    is_dict, is_array, is_indirect = mupdf.pdf_is_dict, mupdf.pdf_is_array, mupdf.pdf_is_indirect
    scripts = items = 0
    catalog = _catalog(doc)
    if javascript:
        names = _get(catalog, "Names")
        if is_dict(names) and _has(_get(names, "JavaScript")):
            mupdf.pdf_dict_dels(names, "JavaScript")  # guiones a nivel de documento
            scripts += 1
    if metadata:
        kind, value = doc.xref_get_key(-1, "Info")
        if kind == "xref":
            items += len(doc.xref_get_keys(int(value.split()[0])))
        elif kind == "dict":
            items += 1
        if kind != "null":
            doc.xref_set_key(-1, "Info", "null")
    blank: list[int] = []
    for num, _kind, _entry in _used_entries(doc, tick):
        top = mupdf.pdf_load_object(pdf, num)
        if metadata and is_dict(top):
            holds = _has(_get(top, "Metadata"))
            is_packet = _name(_get(top, "Type")) == "Metadata"
            if holds or is_packet:
                items += 1
            if holds:
                mupdf.pdf_dict_dels(top, "Metadata")
            if is_packet:
                blank.append(num)
        if not javascript:
            continue
        stack = [top]
        while stack:
            obj = stack.pop()
            if is_dict(obj):
                if _name(_get(obj, "S")) in _SCRIPTED_ACTIONS:
                    code = _get(obj, "JS")
                    if _has(code) and not (mupdf.pdf_is_string(code) and mupdf.pdf_to_str_len(code) == 0):
                        mupdf.pdf_dict_put_text_string(obj, pymupdf.PDF_NAME("JS"), "")
                        scripts += 1
                children = [mupdf.pdf_dict_get_val(obj, i) for i in range(mupdf.pdf_dict_len(obj))]
            elif is_array(obj):
                children = [mupdf.pdf_array_get(obj, i) for i in range(mupdf.pdf_array_len(obj))]
            else:
                continue
            # Solo lo escrito dentro del propio objeto: cada objeto indirecto
            # ya se visita en el bucle exterior.
            stack.extend(c for c in children if not is_indirect(c) and (is_dict(c) or is_array(c)))
    for num in blank:
        # El paquete XMP en si: aunque alguien lo siga nombrando con otra
        # clave, deja de tener contenido.
        doc.update_object(num, "null")
    if metadata:
        doc.init_doc()  # PyMuPDF guarda una copia de los metadatos: se pone al dia
    return scripts, items


_MAX_TREE_NODES = 100_000


def drop_embedded_files(doc: pymupdf.Document) -> int:
    """Quita los archivos adjuntos del documento; devuelve cuantos habia.

    ``Document.embfile_count`` y ``Document.scrub`` solo entienden la lista de
    adjuntos cuando es un unico nodo con /Names. El formato permite repartirla
    en un arbol con /Kids (lo hace Acrobat en cuanto hay unos cuantos): ahi no
    ven ningun adjunto y lo dejan todo dentro. Aqui se cuenta recorriendo el
    arbol y se descuelga entero, tenga la forma que tenga.
    """
    catalog = _catalog(doc)
    names = _get(catalog, "Names")
    tree = _get(names, "EmbeddedFiles") if mupdf.pdf_is_dict(names) else None
    if tree is None or not _has(tree):
        return 0
    count = 0
    uncounted = not mupdf.pdf_is_dict(tree)
    seen: set[int] = set()
    stack = [tree]
    visited = 0
    while stack:
        node = stack.pop()
        visited += 1
        if visited > _MAX_TREE_NODES:
            uncounted = True  # arbol desmesurado o con bucles: se quita igualmente
            break
        num = _ref(node)
        if num:
            if num in seen:
                continue
            seen.add(num)
        if not mupdf.pdf_is_dict(node):
            continue
        leaves = _get(node, "Names")
        if mupdf.pdf_is_array(leaves):
            count += mupdf.pdf_array_len(leaves) // 2
            uncounted = uncounted or mupdf.pdf_array_len(leaves) % 2 == 1
        kids = _get(node, "Kids")
        if mupdf.pdf_is_array(kids):
            stack.extend(mupdf.pdf_array_get(kids, i) for i in range(mupdf.pdf_array_len(kids)))
    mupdf.pdf_dict_dels(names, "EmbeddedFiles")
    # Una "cartera" de PDF solo es la forma de presentar esos adjuntos.
    if _has(_get(catalog, "Collection")):
        mupdf.pdf_dict_dels(catalog, "Collection")
    return max(count, 1) if (count or uncounted) else 0


# ============================================================ busqueda de datos

# Expresiones PREDEFINIDAS. No se aceptan expresiones regulares del usuario: una
# expresion hostil puede bloquear el motor (ReDoS, A05:2025). Las de aqui no
# tienen cuantificadores anidados ni repeticiones sin tope, y sus comprobaciones
# de contexto son de ancho fijo: el coste es lineal en la longitud de la linea.
_SEP = r"[ \xa0.\-]"
_GAP = r"[ \xa0]?-?[ \xa0]?"
_DNI_LETTER = "[A-HJ-NP-TV-Za-hj-np-tv-z]"  # las 23 letras de control (no hay I, O ni U)
_DNI = re.compile(rf"(?<![\w.,])(?<![0-9]-)(?:[0-9]{{8}}|[0-9]{{1,2}}\.[0-9]{{3}}\.[0-9]{{3}}){_GAP}{_DNI_LETTER}(?!\w)")
_NIE = re.compile(rf"(?<!\w)[XYZxyz]{_GAP}[0-9]{{7}}{_GAP}{_DNI_LETTER}(?!\w)")
_EMAIL = re.compile(
    r"(?<![A-Za-z0-9._%+\-])[A-Za-z0-9._%+\-]{1,64}@(?:[A-Za-z0-9\-]{1,63}\.){1,8}[A-Za-z]{2,24}(?![A-Za-z0-9\-])"
)
_PHONE = re.compile(
    rf"(?<![\w+])(?<![0-9]{_SEP})"
    rf"(?:(?:\+|00)[0-9]{{1,3}}\)?{_SEP}?(?:[0-9]{_SEP}?){{5,11}}[0-9]"  # con prefijo internacional
    rf"|[6-9](?:{_SEP}?[0-9]){{8}})"  # numero espanol de nueve cifras
    rf"(?!{_SEP}?[0-9])(?!\w)"
)
# Las tarjetas de pago empiezan por 2-6 (el primer digito identifica el sector).
_CARD = re.compile(r"(?<!\w)(?<![0-9][ \xa0\-])[2-6](?:[ \xa0\-]?[0-9]){12,18}(?![ \xa0\-]?[0-9])(?!\w)")
_CARD_GROUPS = re.compile(r"[0-9]{4}(?:[ \xa0\-][0-9]{4}){3}")
_IBAN_START = re.compile(r"(?<![A-Za-z0-9])[A-Z]{2}[0-9]{2}")
_DIGITS = "0123456789"
_UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
# Longitud del IBAN por pais (registro de SWIFT).
_IBAN_LENGTHS = {
    "AD": 24, "AE": 23, "AL": 28, "AT": 20, "AZ": 28, "BA": 20, "BE": 16, "BG": 22, "BH": 22, "BR": 29,
    "BY": 28, "CH": 21, "CR": 22, "CY": 28, "CZ": 24, "DE": 22, "DK": 18, "DO": 28, "EE": 20, "EG": 29,
    "ES": 24, "FI": 18, "FO": 18, "FR": 27, "GB": 22, "GE": 22, "GI": 23, "GL": 18, "GR": 27, "GT": 28,
    "HR": 21, "HU": 28, "IE": 22, "IL": 23, "IQ": 23, "IS": 26, "IT": 27, "JO": 30, "KW": 30, "KZ": 20,
    "LB": 28, "LC": 32, "LI": 21, "LT": 20, "LU": 20, "LV": 21, "MC": 27, "MD": 24, "ME": 22, "MK": 19,
    "MR": 27, "MT": 31, "MU": 30, "NL": 18, "NO": 15, "PK": 24, "PL": 28, "PS": 29, "PT": 25, "QA": 29,
    "RO": 24, "RS": 22, "SA": 24, "SC": 31, "SE": 24, "SI": 19, "SK": 24, "SM": 27, "ST": 25, "SV": 28,
    "TL": 23, "TN": 24, "TR": 26, "UA": 29, "VA": 22, "VG": 24, "XK": 20,
}
_MAX_LINE = 20_000

Spans = list[tuple[int, int]]


def _regex_finder(regex: re.Pattern[str]) -> Callable[[str], Spans]:
    return lambda text: [m.span() for m in regex.finditer(text)]


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        value = ord(ch) - 48
        if i % 2:
            value = value * 2 - 9 if value > 4 else value * 2
        total += value
    return total % 10 == 0


def _find_cards(text: str) -> Spans:
    out: Spans = []
    for match in _CARD.finditer(text):
        digits = "".join(ch for ch in match.group() if ch in _DIGITS)
        # Digito de control correcto o, si no (errata, OCR), el aspecto clasico
        # de cuatro grupos de cuatro: al censurar es peor quedarse corto.
        if _luhn(digits) or _CARD_GROUPS.fullmatch(match.group()):
            out.append(match.span())
    return out


def _iban_length(compact: str) -> int:
    """Cuantos caracteres del principio de ``compact`` forman un IBAN (0 si ninguno)."""
    known = _IBAN_LENGTHS.get(compact[:2])
    lengths = [known] if known else range(min(len(compact), 34), 14, -1)
    for length in lengths:
        if length > len(compact):
            continue
        candidate = compact[:length]
        moved = candidate[4:] + candidate[:4]
        number = "".join(ch if ch in _DIGITS else str(ord(ch) - 55) for ch in moved)
        if int(number) % 97 == 1:
            return length
        # IBAN espanol con una cifra mal copiada: tiene la forma aunque no
        # cuadre el control.
        if known and compact[:2] == "ES" and all(ch in _DIGITS for ch in candidate[4:]):
            return length
    return 0


def _find_ibans(text: str) -> Spans:
    out: Spans = []
    resume = 0
    for match in _IBAN_START.finditer(text):
        if match.start() < resume:
            continue
        compact: list[str] = []
        where: list[int] = []
        i = match.start()
        # Se recogen como mucho 34 caracteres, admitiendo UN espacio entre grupos.
        while i < len(text) and len(compact) < 34:
            ch = text[i]
            if ch in " \xa0":
                following = text[i + 1] if i + 1 < len(text) else ""
                if not following or (following not in _DIGITS and following not in _UPPER):
                    break
                i += 1
                continue
            if ch not in _DIGITS and ch not in _UPPER:
                break
            compact.append(ch)
            where.append(i)
            i += 1
        length = _iban_length("".join(compact))
        if length:
            resume = where[length - 1] + 1
            out.append((match.start(), resume))
    return out


FINDERS: dict[str, Callable[[str], Spans]] = {
    "dni": _regex_finder(_DNI),
    "nie": _regex_finder(_NIE),
    "email": _regex_finder(_EMAIL),
    "phone": _regex_finder(_PHONE),
    "iban": _find_ibans,
    "card": _find_cards,
}


def match_page(page: pymupdf.Page, kind: str) -> list[pymupdf.Rect]:
    """Cajas (U) de las coincidencias de un tipo de dato en el texto de la pagina."""
    finder = FINDERS[kind]
    textpage = page.get_textpage(flags=_TEXT_FLAGS)
    try:
        # El texto plano tiene las mismas lineas y cuesta diez veces menos: las
        # cajas por caracter solo se piden si la pagina tiene algun candidato.
        if not any(finder(line[:_MAX_LINE]) for line in textpage.extractText().split("\n")):
            return []
        raw = textpage.extractRAWDICT()
    finally:
        del textpage
    rects: list[pymupdf.Rect] = []
    for block in raw.get("blocks", ()):
        if block.get("type", 0) != 0:
            continue
        for line in block.get("lines", ()):
            chars = [c for span in line.get("spans", ()) for c in span.get("chars", ()) if len(c.get("c", "")) == 1]
            chars = chars[:_MAX_LINE]
            text = "".join(c["c"] for c in chars)
            for start, end in finder(text):
                boxes = [chars[i]["bbox"] for i in range(start, end) if not text[i].isspace()]
                if boxes:
                    rects.append(
                        pymupdf.Rect(
                            min(b[0] for b in boxes), min(b[1] for b in boxes),
                            max(b[2] for b in boxes), max(b[3] for b in boxes),
                        )
                    )
    return rects


def search_page(page: pymupdf.Page, query: str) -> list[pymupdf.Rect]:
    """Cajas (U) de un texto literal, con las mismas reglas que la busqueda de la aplicacion."""
    found = page.search_for(query, quads=True, flags=_TEXT_FLAGS | pymupdf.TEXT_DEHYPHENATE)
    return [quad.rect for quad in found]
