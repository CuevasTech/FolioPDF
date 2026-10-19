"""Edicion del contenido original de la pagina: parrafos, lineas de texto e imagenes.

Un PDF no guarda parrafos, solo letras colocadas en coordenadas. Las lineas
seguidas que parecen un parrafo (ver ``_paragraphs``) se editan juntas: se
eliminan del contenido y en su lugar queda un elemento de texto de Folio (ver
content.py) con el ancho del parrafo, en el que las lineas se reparten solas.
Lo demas se edita linea a linea. En ambos casos el texto nuevo se alinea con
la linea base original y queda como cualquier otro texto anadido.
"""

from __future__ import annotations

import math
import re
from typing import Annotated, Any

import pymupdf
from pydantic import Field, StringConstraints, ValidationError

from .. import content, redaction
from ..errors import EngineError, invalid
from ..fonts import DEFAULT_FAMILY, known
from ..geometry import Geom
from ..i18n import _
from ..imaging import load_image
from ..models import PageParams, Rect
from ..server import Ctx, method
from ..session import DocSession
from ..sources import image_source, text_metrics, text_source
from .objects import TextStyle, _describe

_FLAGS = pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_PRESERVE_LIGATURES | pymupdf.TEXT_MEDIABOX_CLIP
_SUBSET = re.compile(r"^[A-Z]{6}\+")
_FAMILY_HINTS = (
    ("arial", "Arial"), ("helvetica", "Arial"), ("helv", "Arial"),
    ("timesnewroman", "Times New Roman"), ("times", "Times New Roman"), ("tiro", "Times New Roman"),
    ("couriernew", "Courier New"), ("courier", "Courier New"), ("cour", "Courier New"),
    ("aptos", "Aptos"), ("calibri", "Calibri"), ("verdana", "Verdana"), ("tahoma", "Tahoma"), ("georgia", "Georgia"),
    ("trebuchet", "Trebuchet MS"), ("segoe", "Segoe UI"), ("consolas", "Consolas"), ("comic", "Comic Sans MS"),
)
Index = Annotated[int, Field(ge=0, le=1_000_000)]
NewText = Annotated[str, StringConstraints(max_length=4_000)]
ParagraphText = Annotated[str, StringConstraints(max_length=20_000)]
# Tolerancia al comparar bordes de lineas, en tamanos de letra.
_ALIGN_TOLERANCE = 0.6


def match_font(name: str, flags: int) -> tuple[str, bool, bool]:
    """Familia instalada mas parecida a una fuente del PDF, con su negrita y cursiva."""
    plain = _SUBSET.sub("", name or "").lower().replace(" ", "").replace("-", "").replace(",", "").replace("_", "")
    bold = "bold" in plain or "black" in plain or "heavy" in plain or bool(flags & 16)
    italic = "italic" in plain or "oblique" in plain or bool(flags & 2)
    for hint, family in _FAMILY_HINTS:
        if hint in plain and known(family):
            return family, bold, italic
    if flags & 8:
        return "Courier New", bold, italic
    if flags & 4:
        return "Times New Roman", bold, italic
    return DEFAULT_FAMILY, bold, italic


def _rgb(value: int) -> list[float]:
    return [round(((value >> 16) & 255) / 255, 4), round(((value >> 8) & 255) / 255, 4), round((value & 255) / 255, 4)]


def _object_boxes(doc: pymupdf.Document, page: pymupdf.Page) -> list[pymupdf.Rect]:
    """Zonas (en U) ocupadas por elementos anadidos con Folio: no son contenido original."""
    boxes = []
    for placed in content.list_objects(doc, page):
        corners = content.box_corners(placed.center, placed.size, placed.angle)
        xs, ys = [c[0] for c in corners], [c[1] for c in corners]
        boxes.append(pymupdf.Rect(min(xs), min(ys), max(xs), max(ys)))
    return boxes


def _inside(rect: pymupdf.Rect, boxes: list[pymupdf.Rect]) -> bool:
    centre = pymupdf.Point((rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2)
    return any(box.contains(centre) for box in boxes)


def _text_lines(page: pymupdf.Page) -> list[tuple[int, int, dict[str, Any]]]:
    out = []
    layout = page.get_text("dict", flags=_FLAGS)
    for block_no, block in enumerate(layout.get("blocks", [])):
        if block.get("type", 0) != 0:
            continue
        for line_no, line in enumerate(block.get("lines", [])):
            spans = [s for s in line.get("spans", []) if s.get("text", "").strip()]
            if spans:
                out.append((block_no, line_no, line))
    return out


class Paragraph:
    """Lineas seguidas que se comportan como un parrafo (ver ``_paragraphs``)."""

    def __init__(self, lines: list[tuple[int, int, dict[str, Any]]], style: dict[str, Any], *,
                 left: float, baseline: float, text: str, mixed: bool) -> None:
        self.lines = lines  # (bloque, linea dentro del bloque, linea)
        self.style = style  # estilo de Folio, con su ancho de columna y alineacion
        self.left = left
        self.baseline = baseline  # linea base de la primera linea
        self.text = text
        self.mixed = mixed

    @property
    def key(self) -> tuple[int, int]:
        """Se identifica por su primera linea."""
        return self.lines[0][0], self.lines[0][1]

    @property
    def rect(self) -> pymupdf.Rect:
        out = pymupdf.Rect(self.lines[0][2]["bbox"])
        for _block, _number, line in self.lines[1:]:
            out |= pymupdf.Rect(line["bbox"])
        return out


def _join_lines(parts: list[str]) -> str:
    """Une las lineas de un parrafo: con un espacio, o sin el guion de corte si la palabra sigue."""
    text = parts[0]
    for part in parts[1:]:
        if text.endswith(("-", "­")) and part[:1].islower():
            text = text[:-1] + part
        else:
            text = f"{text} {part}"
    return text


class _Line:
    """Linea de texto horizontal con lo necesario para agruparla en parrafos."""

    def __init__(self, block: int, number: int, line: dict[str, Any]) -> None:
        self.block, self.number, self.line = block, number, line
        self.spans = [s for s in line["spans"] if s.get("text", "").strip()]
        main = max(self.spans, key=lambda s: len(s["text"]))
        self.size = float(main.get("size", 0))
        self.look = (*match_font(main.get("font", ""), int(main.get("flags", 0))), int(main.get("color", 0)))
        self.baseline = float(self.spans[0]["origin"][1])
        self.x0, self.x1 = float(line["bbox"][0]), float(line["bbox"][2])


def _continues(group: list[_Line], item: _Line) -> bool:
    """``item`` sigue el parrafo ``group``: mismo aspecto, justo debajo y con el mismo margen."""
    first, last = group[0], group[-1]
    size = first.size
    if size <= 0 or abs(item.size - size) > 0.15 * size or item.look != first.look:
        return False
    gap = item.baseline - last.baseline
    if gap <= 0.5 * size or gap > 2.2 * size:
        return False  # encima, superpuesta o tras una separacion: otro parrafo
    if len(group) >= 2:
        usual = group[1].baseline - first.baseline
        if abs(gap - usual) > 0.25 * usual:
            return False
    tolerance = _ALIGN_TOLERANCE * size
    left = group[1].x0 if len(group) >= 2 else first.x0  # la primera linea puede llevar sangria
    indented = len(group) == 1 and first.x0 - 4 * size <= item.x0 <= first.x0 + tolerance
    return (
        abs(item.x0 - left) <= tolerance
        or indented
        or abs(item.x1 - last.x1) <= tolerance
        or abs((item.x0 + item.x1) / 2 - (last.x0 + last.x1) / 2) <= tolerance
    )


def _paragraphs(page: pymupdf.Page, added: list[pymupdf.Rect]) -> list[Paragraph]:
    """Grupos de lineas seguidas que parecen un parrafo de verdad.

    No basta con los bloques de MuPDF: muchos PDF escriben cada linea por
    separado y cada una acaba en su propio bloque. Se agrupan lineas seguidas
    con el mismo tamano y tipo de letra, un interlineado constante y bordes
    que encajen con una alineacion (izquierda, con o sin sangria en la
    primera linea; justificada; centrada o a la derecha). Lo que no encaja
    (una tabla, dos columnas) no se reescribe como un parrafo: sigue
    editandose por lineas.
    """
    groups: list[list[_Line]] = []
    current: list[_Line] = []
    for block_no, line_no, line in _text_lines(page):
        direction = line.get("dir", (1.0, 0.0))
        if abs(direction[0] - 1.0) > 1e-3:
            current = []  # texto girado: corta el parrafo y se edita por lineas
            continue
        item = _Line(block_no, line_no, line)
        if current and _continues(current, item):
            current.append(item)
        else:
            current = [item]
            groups.append(current)
    out: list[Paragraph] = []
    for group in groups:
        if len(group) < 2:
            continue
        size = group[0].size
        tolerance = _ALIGN_TOLERANCE * size
        x0s = [item.x0 for item in group]
        x1s = [item.x1 for item in group]
        right = max(x1s)
        body = x0s[1:]
        centres = [(a + b) / 2 for a, b in zip(x0s, x1s)]
        if max(body) - min(body) <= tolerance and x0s[0] >= min(body) - tolerance:
            left = min(body)
            full = x1s[:-1]
            align = "justify" if len(full) >= 2 and right - min(full) <= tolerance else "left"
        elif max(x1s) - min(x1s) <= tolerance:
            left, align = min(x0s), "right"
        elif max(centres) - min(centres) <= tolerance:
            left, align = min(x0s), "center"
        else:
            continue
        lines = [(item.block, item.number, item.line) for item in group]
        paragraph_rect = pymupdf.Rect(min(x0s), min(float(item.line["bbox"][1]) for item in group), right,
                                      max(float(item.line["bbox"][3]) for item in group))
        if _inside(paragraph_rect, added):
            continue
        family, bold, italic, color = group[0].look
        gaps = sorted(b.baseline - a.baseline for a, b in zip(group, group[1:]))
        try:
            # Valores acotados: un PDF con coordenadas desmesuradas no debe
            # impedir editar el resto de la pagina.
            style = TextStyle(
                font=family, bold=bold, italic=italic, size=max(2.0, min(1000.0, size)), color=tuple(_rgb(color)),
                align=align, lineHeight=max(0.8, min(3.0, gaps[len(gaps) // 2] / size)),
                width=max(1.0, min(20_000.0, right - left)),
            ).normalized()
        except ValidationError:
            continue
        spans = [s for item in group for s in item.spans]
        parts = ["".join(s.get("text", "") for s in item.line["spans"]).strip() for item in group]
        mixed = len({(s.get("font"), round(float(s.get("size", 0)), 1), s.get("color")) for s in spans}) > 1
        out.append(Paragraph(lines, style, left=left, baseline=group[0].baseline, text=_join_lines(parts), mixed=mixed))
    return out


def _paragraph_box(paragraph: Paragraph, style: dict[str, Any] | None = None) -> tuple[float, float]:
    """Esquina superior izquierda (en U) de la caja del texto nuevo: la primera
    linea base y el borde izquierdo coinciden con los del parrafo original."""
    metrics = text_metrics(style or paragraph.style)
    lead = (metrics["lineH"] - metrics["content"]) / 2
    return paragraph.left - metrics["padX"], paragraph.baseline - (metrics["padY"] + lead + metrics["ascent"])


def _upright(geom: Geom) -> bool:
    """La pagina se ve sin girar: lo horizontal del PDF tambien lo es en pantalla."""
    dx, dy = geom.vector_d((1.0, 0.0))
    return dx > 0.999 and abs(dy) < 1e-3


@method("page.editables", PageParams)
def page_editables(ctx: Ctx, p: PageParams) -> dict[str, Any]:
    """Parrafos, lineas de texto e imagenes originales de la pagina que se pueden editar."""
    session = ctx.engine.session(p.doc)
    page = session.page(p.page)
    geom = Geom(page)
    added = _object_boxes(session.doc, page)
    paragraphs = []
    # En una pagina girada se sigue editando por lineas (el editor de parrafos
    # se coloca en horizontal sobre la pantalla).
    for paragraph in _paragraphs(page, added) if _upright(geom) else []:
        left, top = _paragraph_box(paragraph)
        block, line = paragraph.key
        paragraphs.append(
            {
                "block": block,
                "line": line,
                # Lineas que forman el parrafo (la interfaz no las ofrece sueltas).
                "members": [[b, n] for b, n, _line in paragraph.lines],
                "rect": geom.rect_d(paragraph.rect),
                # Esquina superior izquierda de la caja en la que se escribira (el editor se pone ahi).
                "box": geom.point_d((left, top)),
                "text": paragraph.text,
                "style": paragraph.style,
                "mixed": paragraph.mixed,
            }
        )
    lines = []
    for block_no, line_no, line in _text_lines(page):
        rect = pymupdf.Rect(line["bbox"])
        if _inside(rect, added):
            continue
        spans = [s for s in line["spans"] if s.get("text", "").strip()]
        main = max(spans, key=lambda s: len(s["text"]))
        family, bold, italic = match_font(main.get("font", ""), int(main.get("flags", 0)))
        lines.append(
            {
                "block": block_no,
                "line": line_no,
                "rect": geom.rect_d(rect),
                "dir": geom.vector_d(line.get("dir", (1.0, 0.0))),
                "text": "".join(s.get("text", "") for s in line["spans"]).rstrip(),
                "size": round(float(main.get("size", 11)), 2),
                "font": family,
                "bold": bold,
                "italic": italic,
                "color": _rgb(int(main.get("color", 0))),
                "mixed": len({(s.get("font"), round(s.get("size", 0), 1), s.get("color")) for s in spans}) > 1,
            }
        )
    images = []
    try:
        infos = page.get_image_info(xrefs=True)
    except Exception:  # noqa: BLE001
        infos = []
    for number, info in enumerate(infos):
        rect = pymupdf.Rect(info.get("bbox", (0, 0, 0, 0)))
        xref = int(info.get("xref", 0) or 0)
        if xref <= 0 or rect.is_empty or rect.width < 4 or rect.height < 4 or _inside(rect, added):
            continue
        images.append({"xref": xref, "index": number, "rect": geom.rect_d(rect)})
    return {"paragraphs": paragraphs[:2000], "lines": lines[:5000], "images": images[:500]}


def _locate(page: pymupdf.Page, geom: Geom, block: int, line_no: int, rect_d: Rect) -> dict[str, Any]:
    """Recupera la linea indicada comprobando que sigue donde la interfaz la vio."""
    wanted = geom.rect_u(rect_d)
    for b, l, line in _text_lines(page):
        if b == block and l == line_no:
            found = pymupdf.Rect(line["bbox"])
            if abs(found.x0 - wanted.x0) < 2 and abs(found.y0 - wanted.y0) < 2 and abs(found.x1 - wanted.x1) < 2:
                return line
            break
    raise EngineError("stale", _("El contenido de la página ha cambiado. Vuelve a seleccionar el texto."))


def _remove_original(
    session: DocSession, page: pymupdf.Page, area: pymupdf.Rect | list[pymupdf.Rect], *, text: bool, images: bool
) -> pymupdf.Page:
    """Elimina del contenido original lo que hay en una o varias zonas, sin tocar los elementos anadidos.

    Devuelve la pagina recargada: la recibida deja de ser valida.
    """
    doc = session.doc
    # Los elementos de Folio se apartan mientras se reescribe el contenido
    # original y se devuelven despues, para que sigan siendo editables.
    state = content.suspend(doc, page)
    try:
        # erase_areas y no Page.apply_redactions: esta ultima aplicaria de paso
        # las marcas de censura que el usuario tenga pendientes en la pagina.
        areas = area if isinstance(area, list) else [area]
        page = redaction.erase_areas(doc, page, areas, text=text, images=images)
    finally:
        content.resume(doc, page, state)
    return page


def _line_area(line: dict[str, Any]) -> pymupdf.Rect:
    """Franja central de la linea: toca todas sus letras pero no las de las lineas vecinas."""
    rect = pymupdf.Rect(line["bbox"])
    direction = line.get("dir", (1.0, 0.0))
    if abs(direction[0]) >= abs(direction[1]):
        shrink = rect.height * 0.3
        return pymupdf.Rect(rect.x0 - 0.5, rect.y0 + shrink, rect.x1 + 0.5, rect.y1 - shrink)
    shrink = rect.width * 0.3
    return pymupdf.Rect(rect.x0 + shrink, rect.y0 - 0.5, rect.x1 - shrink, rect.y1 + 0.5)


class LineRef(PageParams):
    block: Index
    line: Index
    rect: Rect


class ReplaceTextParams(LineRef):
    text: NewText
    style: TextStyle | None = None


@method("edit.replaceText", ReplaceTextParams)
def edit_replace_text(ctx: Ctx, p: ReplaceTextParams) -> dict[str, Any]:
    session = ctx.engine.session(p.doc)
    session.require("modify")
    obj_id: str | None = None
    with session.edit(_("Editar texto")) as tx:
        page = session.page(p.page)
        geom = Geom(page)
        line = _locate(page, geom, p.block, p.line, p.rect)
        spans = [s for s in line["spans"] if s.get("text", "").strip()]
        main = max(spans, key=lambda s: len(s["text"]))
        first = spans[0]
        if p.style is not None:
            style = p.style.normalized()
        else:
            family, bold, italic = match_font(main.get("font", ""), int(main.get("flags", 0)))
            style = TextStyle(
                font=family, bold=bold, italic=italic, size=max(2.0, min(1000.0, float(main.get("size", 11)))),
                color=tuple(_rgb(int(main.get("color", 0)))),
            ).normalized()
        origin = pymupdf.Point(first.get("origin", (line["bbox"][0], line["bbox"][3])))
        direction = line.get("dir", (1.0, 0.0))
        angle = math.degrees(math.atan2(direction[1], direction[0])) % 360
        page = _remove_original(session, page, _line_area(line), text=True, images=False)
        new_text = " ".join(p.text.splitlines()).rstrip()
        if new_text.strip():
            source, size, _metrics = text_source(new_text, style)
            try:
                metrics = text_metrics(style)
                # Desplazamiento desde la esquina de la caja hasta el inicio de la
                # linea base, para que el texto nuevo caiga donde estaba el antiguo.
                lead = (metrics["lineH"] - metrics["content"]) / 2
                local = (metrics["padX"], metrics["padY"] + lead + metrics["ascent"])
                rad = math.radians(angle)
                cos, sin = math.cos(rad), math.sin(rad)
                from_centre = (local[0] - size[0] / 2, local[1] - size[1] / 2)
                centre = (
                    origin.x - (from_centre[0] * cos - from_centre[1] * sin),
                    origin.y - (from_centre[0] * sin + from_centre[1] * cos),
                )
                placed = content.place(
                    session.doc, page, source, kind="text", center=centre, size=size, angle=angle,
                    props={"text": new_text, **style},
                )
                obj_id = placed.id
            finally:
                source.close()
        tx.label = _("Editar texto") if obj_id else _("Eliminar texto")
        tx.force_full = True
        tx.touch(p.page)
    page = session.page(p.page)
    extra: dict[str, Any] = {}
    if obj_id:
        extra["object"] = _describe(content.find_object(session.doc, page, obj_id), page)
    return session.result([p.page], **extra)


class ReplaceParagraphParams(LineRef):
    """El parrafo se identifica por su primera linea (``block``, ``line``) y su caja."""

    text: ParagraphText
    style: TextStyle | None = None


def _locate_paragraph(page: pymupdf.Page, geom: Geom, block: int, line: int, rect_d: Rect) -> Paragraph:
    """Recupera el parrafo indicado comprobando que sigue donde la interfaz lo vio."""
    wanted = geom.rect_u(rect_d)
    if _upright(geom):
        for paragraph in _paragraphs(page, _object_boxes(page.parent, page)):
            if paragraph.key != (block, line):
                continue
            if all(abs(a - b) < 2 for a, b in zip(paragraph.rect, wanted)):
                return paragraph
            break
    raise EngineError("stale", _("El contenido de la página ha cambiado. Vuelve a seleccionar el párrafo."))


@method("edit.replaceParagraph", ReplaceParagraphParams)
def edit_replace_paragraph(ctx: Ctx, p: ReplaceParagraphParams) -> dict[str, Any]:
    """Reescribe un parrafo entero: las lineas nuevas se reparten solas en su ancho."""
    session = ctx.engine.session(p.doc)
    session.require("modify")
    obj_id: str | None = None
    with session.edit(_("Editar párrafo")) as tx:
        page = session.page(p.page)
        paragraph = _locate_paragraph(page, Geom(page), p.block, p.line, p.rect)
        style = p.style.normalized() if p.style is not None else dict(paragraph.style)
        style.setdefault("width", paragraph.style["width"])  # sin ancho no habria reparto en lineas
        left, top = _paragraph_box(paragraph, style)
        page = _remove_original(
            session, page, [_line_area(line) for _block, _number, line in paragraph.lines], text=True, images=False
        )
        # Los saltos que se escriban separan parrafos; los del principio y el final sobran.
        new_text = p.text.replace("\r\n", "\n").strip("\n").rstrip()
        if new_text.strip():
            source, size, _metrics = text_source(new_text, style)
            try:
                placed = content.place(
                    session.doc, page, source, kind="text",
                    center=(left + size[0] / 2, top + size[1] / 2), size=size, angle=0.0,
                    props={"text": new_text, **style},
                )
                obj_id = placed.id
            finally:
                source.close()
        tx.label = _("Editar párrafo") if obj_id else _("Eliminar párrafo")
        tx.force_full = True
        tx.touch(p.page)
    page = session.page(p.page)
    extra: dict[str, Any] = {}
    if obj_id:
        extra["object"] = _describe(content.find_object(session.doc, page, obj_id), page)
    return session.result([p.page], **extra)


@method("edit.deleteText", LineRef)
def edit_delete_text(ctx: Ctx, p: LineRef) -> dict[str, Any]:
    session = ctx.engine.session(p.doc)
    session.require("modify")
    with session.edit(_("Eliminar texto")) as tx:
        page = session.page(p.page)
        line = _locate(page, Geom(page), p.block, p.line, p.rect)
        page = _remove_original(session, page, _line_area(line), text=True, images=False)
        tx.force_full = True
        tx.touch(p.page)
    return session.result([p.page])


class ImageRef(PageParams):
    xref: Annotated[int, Field(ge=1, le=100_000_000)]
    rect: Rect


def _image_area(page: pymupdf.Page, geom: Geom, xref: int, rect_d: Rect) -> pymupdf.Rect:
    wanted = geom.rect_u(rect_d)
    for info in page.get_image_info(xrefs=True):
        found = pymupdf.Rect(info.get("bbox", (0, 0, 0, 0)))
        if int(info.get("xref", 0) or 0) == xref and abs(found.x0 - wanted.x0) < 2 and abs(found.y0 - wanted.y0) < 2:
            return found
    raise EngineError("stale", _("El contenido de la página ha cambiado. Vuelve a seleccionar la imagen."))


@method("edit.deleteImage", ImageRef)
def edit_delete_image(ctx: Ctx, p: ImageRef) -> dict[str, Any]:
    session = ctx.engine.session(p.doc)
    session.require("modify")
    with session.edit(_("Eliminar imagen")) as tx:
        page = session.page(p.page)
        area = _image_area(page, Geom(page), p.xref, p.rect)
        page = _remove_original(session, page, area + (1, 1, -1, -1), text=False, images=True)
        tx.force_full = True
        tx.touch(p.page)
    return session.result([p.page])


@method("edit.detachImage", ImageRef)
def edit_detach_image(ctx: Ctx, p: ImageRef) -> dict[str, Any]:
    """Convierte una imagen original en un elemento que se puede mover, escalar y girar."""
    session = ctx.engine.session(p.doc)
    session.require("modify")
    with session.edit(_("Mover imagen")) as tx:
        page = session.page(p.page)
        doc = session.doc
        area = _image_area(page, Geom(page), p.xref, p.rect)
        try:
            pix = pymupdf.Pixmap(doc, p.xref)
            smask = int(doc.extract_image(p.xref).get("smask", 0) or 0)
            if smask:
                pix = pymupdf.Pixmap(pix, pymupdf.Pixmap(doc, smask))
            if pix.colorspace is not None and pix.colorspace.n > 3:
                pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
            image = load_image(pix.tobytes("png"))
        except EngineError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise invalid(_("Esta imagen no se puede convertir en un elemento editable.")) from exc
        page = _remove_original(session, page, area + (1, 1, -1, -1), text=False, images=True)
        source, _size = image_source(image)
        try:
            placed = content.place(
                doc, page, source, kind="image", center=((area.x0 + area.x1) / 2, (area.y0 + area.y1) / 2),
                size=(area.width, area.height), props={"px": [image.width, image.height]},
            )
            obj_id = placed.id
        finally:
            source.close()
        tx.force_full = True
        tx.touch(p.page)
    page = session.page(p.page)
    return session.result([p.page], object=_describe(content.find_object(session.doc, page, obj_id), page))


@method("edit.replaceImage", ImageRef)
def edit_replace_image(ctx: Ctx, p: ImageRef) -> dict[str, Any]:
    """Sustituye una imagen por la recibida como carga binaria, en la misma posicion."""
    session = ctx.engine.session(p.doc)
    session.require("modify")
    image = load_image(ctx.binary)
    with session.edit(_("Reemplazar imagen")) as tx:
        page = session.page(p.page)
        _image_area(page, Geom(page), p.xref, p.rect)
        page.replace_image(p.xref, stream=image.data)
        # La misma imagen puede usarse en mas paginas: todas cambian.
        tx.touch(*session.page_ids)
    return session.result(list(session.page_ids))
