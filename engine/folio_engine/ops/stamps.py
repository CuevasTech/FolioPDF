"""Marcas de agua, encabezados y pies, numeracion de paginas y su retirada.

Todo lo que se anade aqui es un elemento etiquetado (ver ``content.py``) con su
tipo: ``watermark``, ``header``, ``footer`` o ``pagenum``. Gracias a la etiqueta
se puede localizar y quitar mas tarde, tambien despues de guardar y reabrir.

El contenido nunca se dibuja directamente en la pagina de destino: se compone
en una pagina fuente temporal y se coloca con ``content.place``, que es quien
resuelve bien las paginas giradas o con el CropBox desplazado.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import PurePath
from typing import Annotated, Any, Literal, Mapping

import pymupdf
from PIL import Image
from pydantic import Field, StringConstraints

from .. import content
from ..errors import EngineError, invalid, not_found
from ..geometry import Geom, matrix_operand
from ..i18n import N_, _, long_date, short_date
from ..imaging import load_image
from ..models import DocParams, PageId, Params, Unit
from ..server import Ctx, method
from ..session import DocSession
from ..sources import image_source, text_source, text_sources
from .objects import TextStyle

# Tipos de elemento que gestionan estas operaciones ("ocr" lo anade ops/ocr.py).
ADDED_KINDS = ("watermark", "header", "footer", "pagenum", "ocr")
AddedKind = Literal["watermark", "header", "footer", "pagenum", "ocr"]

# Topes de trabajo por peticion, ademas de los de la interfaz: sin ellos una
# llamada podria pedir memoria o tiempo sin limite (p. ej. un mosaico de miles
# de copias por pagina).
_MAX_PAGES = 100_000
_MAX_TILES = 400
_MAX_LINES = 6
_MAX_RESOLVED = 600
_MAX_FILE_NAME = 120
_PROBE_PIXELS = 800  # lado de la imagen con la que se mide lo que se ve de una marca
_MIN_SIZE = 0.1  # por debajo de 0,01 pt la etiqueta dejaria de reconocerse al releerla

PageList = Annotated[list[PageId], Field(min_length=1, max_length=_MAX_PAGES)]
Margin = Annotated[float, Field(ge=0, le=300, allow_inf_nan=False)]
StartNumber = Annotated[int, Field(ge=0, le=999_999)]


# Unicos marcadores que se sustituyen; cualquier otra cosa entre llaves es texto.
_MARKER = re.compile(r"\{(page|pages|date|file)\}")


# ------------------------------------------------------------------ utilidades


def resolve_pages(session: DocSession, pages: list[str] | None) -> list[str]:
    """Paginas a tratar, sin repetir y en el orden del documento (``None`` = todas)."""
    if pages is None:
        return list(session.page_ids)
    wanted = set(pages)
    if not wanted.issubset(session.page_ids):
        raise not_found(_("La página ya no existe."))
    return [pid for pid in session.page_ids if pid in wanted]


def _clean(text: str, *, multiline: bool) -> str:
    """Normaliza un texto recibido antes de componerlo.

    Se descartan los caracteres de control, de formato (p. ej. los que invierten
    el sentido de escritura y sirven para disfrazar un texto) y los sustitutos
    sueltos: no tienen glifo y solo podrian estropear el resultado o la
    etiqueta. Es una neutralizacion de la entrada (OWASP A05:2025), aunque el
    texto nunca se interpreta: llega al PDF como glifos y como hexadecimal.
    """
    out: list[str] = []
    for ch in text.replace("\r\n", "\n").replace("\r", "\n"):
        if ch == "\n":
            out.append("\n" if multiline else " ")
        elif ch == "\t":
            out.append(" ")
        elif unicodedata.category(ch)[0] != "C":
            out.append(ch)
    cleaned = "".join(out)
    if multiline and cleaned.count("\n") >= _MAX_LINES:
        raise invalid(_("El texto tiene demasiadas líneas."), max=_MAX_LINES)
    return cleaned


def _fill(template: str, values: Mapping[str, str]) -> str:
    """Sustituye los marcadores ``{page}``, ``{pages}``, ``{date}`` y ``{file}``.

    Seguridad (OWASP A05:2025, inyeccion): NO se usa ``str.format`` ni otro
    sistema de plantillas con el texto del usuario: ``format`` evalua
    expresiones como ``{page.__class__.__init__.__globals__}``, que pueden sacar
    datos internos del proceso, y falla con ``{0}`` o ``{}``. El reemplazo es
    literal y acotado:

    * solo existen los marcadores de ``_MARKER``; lo demas se deja como texto;
    * una sola pasada: el valor insertado no se vuelve a examinar (un nombre
      de archivo con ``{page}`` no se expande);
    * la longitud del resultado tiene tope.
    """
    out = _MARKER.sub(lambda match: values.get(match.group(1), match.group(0)), template)
    if len(out) > _MAX_RESOLVED:
        raise invalid(_("El texto resultante es demasiado largo."), max=_MAX_RESOLVED)
    return out


def _today() -> date:
    return date.today()


def _format_date(day: date, style: str | None) -> str:
    # Se compone a mano, en el idioma de la aplicacion: no depende de la
    # configuracion regional del equipo.
    if style == "long":
        return long_date(day)
    return short_date(day)


def _file_label(session: DocSession) -> str:
    """Nombre del documento sin extension, saneado y con longitud acotada."""
    return _clean(PurePath(session.name).stem, multiline=False).strip()[:_MAX_FILE_NAME]


def _style(style: TextStyle | None, *, default_size: float | None = None) -> dict[str, Any]:
    chosen = style or TextStyle()
    data = chosen.normalized()
    if default_size is not None and "size" not in chosen.model_fields_set:
        data["size"] = default_size
    return data


class _TextSources:
    """Origenes de texto de una operacion: un unico documento con una pagina por texto distinto.

    Primero se apuntan todos los textos que van a hacer falta (``want``), luego
    se componen de una vez (``build``) y por ultimo se colocan (``get``). Asi
    todos comparten una sola fuente incrustada, tambien los que cambian de una
    pagina a otra (la numeracion): con un origen por texto, cada pagina del
    documento cargaria con su propia copia de la fuente (unos 15 KB).
    """

    def __init__(self, style: dict[str, Any]) -> None:
        self._style = style
        self._index: dict[tuple[str, str], int] = {}
        self._book: pymupdf.Document | None = None
        self._sizes: list[tuple[float, float]] = []
        self._metrics: dict[str, float] = {}

    def want(self, text: str, align: str) -> None:
        self._index.setdefault((text, align), len(self._index))

    def build(self) -> None:
        if self._index:
            self._book, self._sizes, self._metrics = text_sources(list(self._index), self._style)

    def get(self, text: str, align: str) -> tuple[pymupdf.Document, int, tuple[float, float], dict[str, float]]:
        number = self._index[(text, align)]
        if self._book is None:
            raise EngineError("internal", _("No se pudo preparar el texto."))
        return self._book, number, self._sizes[number], self._metrics

    def close(self) -> None:
        if self._book is not None:
            self._book.close()
            self._book = None

def _line_center(
    page: tuple[float, float],
    box: tuple[float, float],
    metrics: dict[str, float],
    horizontal: str,
    vertical: str,
    side: float,
    edge: float,
) -> tuple[float, float]:
    """Centro (en D) de una caja de texto anclada a un borde de la pagina.

    Los margenes se miden hasta el texto, no hasta la caja: esta lleva un
    relleno (``padX``/``padY``) que se descuenta para que el texto quede justo
    a la distancia pedida.
    """
    width, height = box
    if horizontal == "left":
        cx = side - metrics["padX"] + width / 2
    elif horizontal == "right":
        cx = page[0] - side + metrics["padX"] - width / 2
    else:
        cx = page[0] / 2
    if vertical == "top":
        cy = edge - metrics["padY"] + height / 2
    else:
        cy = page[1] - edge + metrics["padY"] - height / 2
    return cx, cy


def _place_line(
    session: DocSession,
    page: pymupdf.Page,
    geom: Geom,
    sources: _TextSources,
    *,
    kind: str,
    text: str,
    horizontal: str,
    vertical: str,
    side: float,
    edge: float,
    props: dict[str, Any],
) -> None:
    doc, number, size, metrics = sources.get(text, horizontal)
    center = _line_center(geom.size_d, size, metrics, horizontal, vertical, side, edge)
    center_u = geom.point_u(center)
    content.place(
        session.doc, page, doc, kind=kind, center=(center_u.x, center_u.y), size=size,
        angle=geom.angle_u(0), props={"text": text, **props}, pno=number,
    )


# ---------------------------------------------------------------- marca de agua


class WatermarkParams(DocParams):
    pages: PageList | None = None
    text: Annotated[str, StringConstraints(min_length=1, max_length=200)] | None = None
    style: TextStyle | None = None
    opacity: Unit = 0.25
    # Grados en sentido horario tal como se ve en pantalla; -45 es la diagonal ascendente.
    angle: Annotated[float, Field(ge=-360, le=360, allow_inf_nan=False)] = -45.0
    position: Literal[
        "center", "top-left", "top", "top-right", "left", "right", "bottom-left", "bottom", "bottom-right"
    ] = "center"
    # Fraccion del ancho visible de la pagina que ocupa la marca.
    scale: Annotated[float, Field(ge=0.05, le=1.0, allow_inf_nan=False)] = 0.6
    margin: Margin = 36.0
    background: bool = False
    tile: bool = False


@dataclass
class _Mark:
    """Origen de la marca de agua y medida de lo que se ve de ella."""

    doc: pymupdf.Document
    size: tuple[float, float]  # caja del origen
    props: dict[str, Any]
    # Contorno de lo que realmente se ve con la marca ya girada, relativo al
    # centro de su caja y en unidades del origen (x0, y0, x1, y1).
    seen: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)


@dataclass
class _Layout:
    size: tuple[float, float]  # tamano de la caja de la marca en la pagina
    bounds: tuple[float, float]  # ancho y alto de lo que se ve
    shift: tuple[float, float]  # del centro de la caja al centro de lo que se ve


def _watermark_mark(binary: bytes, p: WatermarkParams) -> _Mark:
    if binary:
        # La imagen llega de fuera: load_image valida por contenido que es de un
        # formato admitido y acota su tamano y sus pixeles antes de usarla
        # (bombas de descompresion, archivos disfrazados).
        image = load_image(binary)
        doc, size = image_source(image)
        return _Mark(doc, size, {"image": [image.width, image.height]})
    if p.text is None:
        raise invalid(_("Indica el texto o la imagen de la marca de agua."))
    text = _clean(p.text, multiline=True).strip()
    if not text:
        raise invalid(_("El texto de la marca de agua está vacío."))
    style = _style(p.style)
    if p.style is None or "align" not in p.style.model_fields_set:
        style["align"] = "center"  # varias lineas quedan centradas entre si
    doc, size, _metrics = text_source(text, style)
    return _Mark(doc, size, {"text": text})


def _seen_box(mark: _Mark, angle: float) -> tuple[float, float, float, float]:
    """Mide lo que se ve de la marca una vez girada.

    La caja del origen no sirve para ajustar el tamano: la de un texto lleva
    relleno e interlineado y la de una imagen puede tener bordes transparentes.
    Se coloca la marca, con el mismo calculo que en la pagina real, en una
    pagina de prueba transparente y se mira que pixeles quedan pintados. Asi
    "ocupar el 60 % del ancho" se cumple para lo que se ve, con cualquier
    fuente, imagen o angulo.
    """
    width, height = mark.size
    side = math.hypot(width, height) + 4.0  # la caja girada cabe siempre en este cuadrado
    zoom = _PROBE_PIXELS / side  # la prueba mide siempre lo mismo: su coste esta acotado
    probe = pymupdf.open()
    try:
        page = probe.new_page(width=side, height=side)
        content.place(
            probe, page, mark.doc, kind="watermark", center=(side / 2, side / 2), size=(width, height), angle=angle
        )
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=True)
        alpha = Image.frombytes("RGBA", (pix.width, pix.height), bytes(pix.samples)).getchannel("A")
        box = alpha.point(lambda value: 255 if value > 16 else 0).getbbox()
    finally:
        probe.close()
    if box is None:
        # Nada visible (p. ej. una imagen totalmente transparente): vale la caja entera.
        rad = math.radians(angle)
        cos, sin = abs(math.cos(rad)), abs(math.sin(rad))
        half_w, half_h = (width * cos + height * sin) / 2, (width * sin + height * cos) / 2
        return (-half_w, -half_h, half_w, half_h)
    x0, y0, x1, y1 = box

    def partial(strip: tuple[int, int, int, int]) -> float:
        # Un pixel de borde a medio cubrir solo cuenta por la parte cubierta:
        # afina la medida por debajo del pixel.
        return 1.0 - alpha.crop(strip).getextrema()[1] / 255.0

    left = x0 + partial((x0, y0, x0 + 1, y1))
    right = x1 - partial((x1 - 1, y0, x1, y1))
    top = y0 + partial((x0, y0, x1, y0 + 1))
    bottom = y1 - partial((x0, y1 - 1, x1, y1))
    if right - left < 1.0 or bottom - top < 1.0:  # marca de menos de un pixel: sin afinar
        left, top, right, bottom = float(x0), float(y0), float(x1), float(y1)
    half = side / 2
    return (left / zoom - half, top / zoom - half, right / zoom - half, bottom / zoom - half)


def _layout(mark: _Mark, page: tuple[float, float], scale: float) -> _Layout:
    """Tamano con el que la marca ocupa ``scale`` por el ancho visible de la pagina.

    Se agranda o reduce sin deformarla. Si girada resultase mas alta que esa
    misma fraccion del alto (texto vertical, paginas apaisadas), manda el alto:
    asi nunca se sale de la pagina.
    """
    x0, y0, x1, y1 = mark.seen
    factor = min(scale * page[0] / (x1 - x0), scale * page[1] / (y1 - y0))
    return _Layout(
        size=(max(mark.size[0] * factor, _MIN_SIZE), max(mark.size[1] * factor, _MIN_SIZE)),
        bounds=((x1 - x0) * factor, (y1 - y0) * factor),
        shift=((x0 + x1) / 2 * factor, (y0 + y1) / 2 * factor),
    )


def _anchor(
    position: str, page: tuple[float, float], bounds: tuple[float, float], margin: float
) -> tuple[float, float]:
    """Centro (en D) de lo que se ve de la marca segun la posicion elegida."""

    def along(low: bool, high: bool, length: float, extent: float) -> float:
        if low:
            start = margin
        elif high:
            start = length - margin - extent
        else:
            return length / 2
        # Si el margen no cabe, la marca se queda dentro de la pagina.
        start = min(max(start, 0.0), max(length - extent, 0.0))
        return start + extent / 2

    return (
        along("left" in position, "right" in position, page[0], bounds[0]),
        along("top" in position, "bottom" in position, page[1], bounds[1]),
    )


def _tile_centers(page: tuple[float, float], bounds: tuple[float, float], gap: float) -> list[tuple[float, float]]:
    """Centros (de lo que se ve) de un mosaico que cubre la pagina, con una copia en su centro."""
    step_x, step_y = bounds[0] + gap, bounds[1] + gap

    def reach(length: float, extent: float, step: float) -> int:
        # Copias a cada lado del centro que aun asoman dentro de la pagina.
        return max(0, math.ceil((length + extent) / 2 / step) - 1)

    nx, ny = reach(page[0], bounds[0], step_x), reach(page[1], bounds[1], step_y)
    while (2 * nx + 1) * (2 * ny + 1) > _MAX_TILES:
        # Demasiadas copias: se separan mas en lugar de rechazar la peticion.
        grow = math.sqrt((2 * nx + 1) * (2 * ny + 1) / _MAX_TILES) * 1.01
        step_x, step_y = step_x * grow, step_y * grow
        nx, ny = reach(page[0], bounds[0], step_x), reach(page[1], bounds[1], step_y)
    return [
        (page[0] / 2 + i * step_x, page[1] / 2 + j * step_y)
        for j in range(-ny, ny + 1)
        for i in range(-nx, nx + 1)
    ]


def _tile_source(
    mark: _Mark, page: tuple[float, float], layout: _Layout, gap: float, angle: float
) -> tuple[pymupdf.Document, int]:
    """Pagina fuente del tamano de la pagina visible con la marca repetida.

    La marca se incorpora una vez y se dibuja en cada centro con su propia
    matriz, de modo que el mosaico entero es un unico elemento por pagina. Lo
    que sobresale lo recorta la caja de la propia pagina fuente.
    """
    centers = [
        (x - layout.shift[0], y - layout.shift[1]) for x, y in _tile_centers(page, layout.bounds, gap)
    ]
    doc = pymupdf.open()
    try:
        target = doc.new_page(width=page[0], height=page[1])
        first = content.place(doc, target, mark.doc, kind="watermark", center=centers[0], size=layout.size, angle=angle)
        geom = Geom(target)  # pagina sin giro ni recorte: U y D coinciden
        draws = [
            f"q {matrix_operand(geom.placement(first.source, center, layout.size, angle))} cm /{first.name} Do Q"
            for center in centers
        ]
        doc.update_stream(first.stream, ("\n".join(draws) + "\n").encode("ascii"))
    except BaseException:
        doc.close()
        raise
    return doc, len(centers)


@method("tools.watermark", WatermarkParams)
def tools_watermark(ctx: Ctx, p: WatermarkParams) -> dict[str, Any]:
    """Anade una marca de agua de texto o, si la peticion trae una imagen, de imagen."""
    session = ctx.engine.session(p.doc)
    session.require("modify")
    ids = resolve_pages(session, p.pages)
    mark = _watermark_mark(ctx.binary, p)
    tiles: dict[tuple[float, float], tuple[pymupdf.Document, int]] = {}
    try:
        mark.seen = _seen_box(mark, p.angle)
        # Una sola transaccion para todas las paginas: es un unico paso de
        # deshacer y, si algo falla a medias, no queda ninguna pagina marcada
        # (falla de forma segura, OWASP A10:2025).
        with session.edit(_("Añadir marca de agua")) as tx:
            for index, pid in enumerate(ids):
                ctx.progress(index, len(ids), _("Añadiendo la marca de agua"))
                page = session.page(pid)
                geom = Geom(page)
                visible = geom.size_d
                layout = _layout(mark, visible, p.scale)
                if p.tile:
                    # Las paginas del mismo tamano comparten el mismo mosaico.
                    key = (round(visible[0], 2), round(visible[1], 2))
                    if key not in tiles:
                        tiles[key] = _tile_source(mark, visible, layout, p.margin, p.angle)
                    source, copies = tiles[key]
                    center_u = geom.point_u((visible[0] / 2, visible[1] / 2))
                    content.place(
                        session.doc, page, source, kind="watermark", center=(center_u.x, center_u.y),
                        size=(max(visible[0], _MIN_SIZE), max(visible[1], _MIN_SIZE)), angle=geom.angle_u(0),
                        opacity=p.opacity, background=p.background,
                        props={**mark.props, "tile": True, "copies": copies},
                    )
                else:
                    seen_x, seen_y = _anchor(p.position, visible, layout.bounds, p.margin)
                    center_u = geom.point_u((seen_x - layout.shift[0], seen_y - layout.shift[1]))
                    content.place(
                        session.doc, page, mark.doc, kind="watermark", center=(center_u.x, center_u.y),
                        size=layout.size, angle=geom.angle_u(p.angle), opacity=p.opacity,
                        background=p.background, props=mark.props,
                    )
                tx.touch(pid)
    finally:
        mark.doc.close()
        for source, _copies in tiles.values():
            source.close()
    return session.result(ids, added=len(ids))


# ------------------------------------------------------------ encabezado y pie


SlotText = Annotated[str, StringConstraints(max_length=300)]


class Slots(Params):
    left: SlotText = ""
    center: SlotText = ""
    right: SlotText = ""


class HeaderFooterMargin(Params):
    top: Margin = 28.0
    bottom: Margin = 28.0
    side: Margin = 40.0


class HeaderFooterParams(DocParams):
    pages: PageList | None = None
    header: Slots = Slots()
    footer: Slots = Slots()
    style: TextStyle | None = None  # tamano por defecto: 9
    margin: HeaderFooterMargin = HeaderFooterMargin()
    start: StartNumber = 1  # numero que recibe la primera pagina afectada
    dateFormat: Literal["short", "long"] | None = None


@method("tools.headerFooter", HeaderFooterParams)
def tools_header_footer(ctx: Ctx, p: HeaderFooterParams) -> dict[str, Any]:
    """Texto de encabezado y de pie en hasta seis posiciones, con marcadores."""
    session = ctx.engine.session(p.doc)
    session.require("modify")
    ids = resolve_pages(session, p.pages)
    slots: list[tuple[str, str, str]] = []
    for kind, group in (("header", p.header), ("footer", p.footer)):
        for slot in ("left", "center", "right"):
            template = _clean(getattr(group, slot), multiline=True)
            if template.strip():
                slots.append((kind, slot, template))
    if not slots:
        raise invalid(_("Escribe al menos un texto para el encabezado o el pie."))
    kinds = {kind for kind, _slot, _template in slots}
    label = _("Añadir encabezado y pie") if len(kinds) == 2 else (
        _("Añadir encabezado") if "header" in kinds else _("Añadir pie de página")
    )
    fixed = {
        # Total coherente con la numeracion: el numero de la ultima pagina afectada.
        "pages": str(p.start + len(ids) - 1),
        "date": _format_date(_today(), p.dateFormat),
        "file": _file_label(session),
    }
    sources = _TextSources(_style(p.style, default_size=9.0))
    # Primero se decide que texto lleva cada pagina; despues se componen todos
    # juntos (comparten la fuente) y por ultimo se colocan.
    plan: list[list[tuple[str, str, str]]] = []
    for index in range(len(ids)):
        values = {**fixed, "page": str(p.start + index)}
        lines = []
        for kind, slot, template in slots:
            text = _fill(template, values).strip()
            if text:
                lines.append((kind, slot, text))
                sources.want(text, slot)
        plan.append(lines)
    added = 0
    try:
        sources.build()
        with session.edit(label) as tx:
            for index, pid in enumerate(ids):
                ctx.progress(index, len(ids), _("Añadiendo encabezado y pie"))
                if not plan[index]:
                    continue
                page = session.page(pid)
                geom = Geom(page)
                for kind, slot, text in plan[index]:
                    _place_line(
                        session, page, geom, sources, kind=kind, text=text,
                        horizontal=slot, vertical="top" if kind == "header" else "bottom", side=p.margin.side,
                        edge=p.margin.top if kind == "header" else p.margin.bottom, props={"slot": slot},
                    )
                    added += 1
                    tx.touch(pid)
            if not added:
                tx.skip = True
    finally:
        sources.close()
    return session.result(tx.dirty, added=added)


# ------------------------------------------------------- numeracion de paginas


class PageNumberMargin(Params):
    vertical: Margin = 28.0
    side: Margin = 40.0


class PageNumbersParams(DocParams):
    pages: PageList | None = None
    format: Annotated[str, StringConstraints(min_length=1, max_length=80)] = "{page}"
    position: Literal[
        "bottom-center", "bottom-left", "bottom-right", "top-center", "top-left", "top-right"
    ] = "bottom-center"
    start: StartNumber = 1
    style: TextStyle | None = None
    margin: PageNumberMargin = PageNumberMargin()


@method("tools.pageNumbers", PageNumbersParams)
def tools_page_numbers(ctx: Ctx, p: PageNumbersParams) -> dict[str, Any]:
    """Numera las paginas; ``format`` admite los marcadores ``{page}`` y ``{pages}``."""
    session = ctx.engine.session(p.doc)
    session.require("modify")
    ids = resolve_pages(session, p.pages)
    template = _clean(p.format, multiline=False)
    if not template.strip():
        raise invalid(_("El formato de la numeración está vacío."))
    vertical, horizontal = p.position.split("-")
    total = str(p.start + len(ids) - 1)
    sources = _TextSources(_style(p.style))
    # Aqui solo valen {page} y {pages}: {date} y {file} quedan como texto.
    texts = [_fill(template, {"page": str(p.start + index), "pages": total}).strip() for index in range(len(ids))]
    for text in texts:
        if text:
            sources.want(text, horizontal)
    added = 0
    try:
        sources.build()  # todos los numeros comparten una sola fuente incrustada
        with session.edit(_("Numerar páginas")) as tx:
            for index, pid in enumerate(ids):
                ctx.progress(index, len(ids), _("Numerando las páginas"))
                text = texts[index]
                if not text:
                    continue
                page = session.page(pid)
                _place_line(
                    session, page, Geom(page), sources, kind="pagenum", text=text,
                    horizontal=horizontal, vertical=vertical, side=p.margin.side, edge=p.margin.vertical,
                    props={"number": p.start + index},
                )
                added += 1
                tx.touch(pid)
            if not added:
                tx.skip = True
    finally:
        sources.close()
    return session.result(tx.dirty, added=added)


# ------------------------------------------------------------ listar y quitar


@method("tools.listAdded", DocParams)
def tools_list_added(ctx: Ctx, p: DocParams) -> dict[str, Any]:
    """Cuantos elementos de cada tipo hay en todo el documento."""
    session = ctx.engine.session(p.doc)
    counts = dict.fromkeys(ADDED_KINDS, 0)
    total = len(session.page_ids)
    for index in range(total):
        ctx.progress(index, total)
        for placed in content.list_objects(session.doc, session.doc[index]):
            if placed.kind in counts:
                counts[placed.kind] += 1
    return {"counts": counts}


class RemoveAddedParams(DocParams):
    kinds: Annotated[list[AddedKind], Field(min_length=1, max_length=len(ADDED_KINDS))]
    pages: PageList | None = None


_REMOVE_LABELS = {
    "watermark": N_("Quitar marca de agua"),
    "header": N_("Quitar encabezado"),
    "footer": N_("Quitar pie de página"),
    "pagenum": N_("Quitar numeración"),
    "ocr": N_("Quitar texto reconocido"),
}


@method("tools.removeAdded", RemoveAddedParams)
def tools_remove_added(ctx: Ctx, p: RemoveAddedParams) -> dict[str, Any]:
    """Quita los elementos de los tipos indicados (de todo el documento o de unas paginas)."""
    session = ctx.engine.session(p.doc)
    session.require("modify")
    ids = resolve_pages(session, p.pages)
    wanted = set(p.kinds)
    label = _(_REMOVE_LABELS[p.kinds[0]]) if len(wanted) == 1 else _("Quitar elementos añadidos")
    removed = 0
    with session.edit(label) as tx:
        for index, pid in enumerate(ids):
            ctx.progress(index, len(ids), _("Quitando elementos"))
            page = session.page(pid)
            # Solo se quita lo que lleva etiqueta valida de Folio y es de un tipo
            # pedido: el contenido original de la pagina no se toca nunca.
            found = [placed for placed in content.list_objects(session.doc, page) if placed.kind in wanted]
            for placed in found:
                content.remove(session.doc, page, placed)
            if found:
                removed += len(found)
                tx.touch(pid)
        if not removed:
            tx.skip = True
    return session.result(tx.dirty, removed=removed)
