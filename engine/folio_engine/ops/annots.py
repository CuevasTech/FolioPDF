"""Anotaciones estandar del PDF: resaltado, notas, dibujo a mano alzada y formas.

Son anotaciones reales (no contenido de la pagina): cualquier otro visor las
muestra, y pueden editarse o quitarse despues sin tocar el documento original.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

import pymupdf
from pydantic import Field, StringConstraints

from ..errors import invalid, not_found
from ..geometry import Geom
from ..i18n import N_, _
from ..models import Color, DocParams, PageId, PageParams, Point, Quad, Rect, ShortText, Unit
from ..server import Ctx, method
from ..session import DocSession
from .document import pdf_date_to_iso

AnnotId = Annotated[str, StringConstraints(pattern=r"^a[0-9]{1,9}$")]
Width = Annotated[float, Field(ge=0.1, le=60, allow_inf_nan=False)]
Comment = Annotated[str, StringConstraints(max_length=20_000)]
Stroke = Annotated[list[Point], Field(min_length=1, max_length=20_000)]

_MARKUP = {
    "highlight": pymupdf.PDF_ANNOT_HIGHLIGHT,
    "underline": pymupdf.PDF_ANNOT_UNDERLINE,
    "strikeout": pymupdf.PDF_ANNOT_STRIKE_OUT,
    "squiggly": pymupdf.PDF_ANNOT_SQUIGGLY,
}
_KIND_BY_TYPE = {
    pymupdf.PDF_ANNOT_HIGHLIGHT: "highlight",
    pymupdf.PDF_ANNOT_UNDERLINE: "underline",
    pymupdf.PDF_ANNOT_STRIKE_OUT: "strikeout",
    pymupdf.PDF_ANNOT_SQUIGGLY: "squiggly",
    pymupdf.PDF_ANNOT_TEXT: "note",
    pymupdf.PDF_ANNOT_INK: "ink",
    pymupdf.PDF_ANNOT_SQUARE: "rect",
    pymupdf.PDF_ANNOT_CIRCLE: "ellipse",
    pymupdf.PDF_ANNOT_LINE: "line",
    pymupdf.PDF_ANNOT_FREE_TEXT: "freetext",
    pymupdf.PDF_ANNOT_STAMP: "stamp",
    pymupdf.PDF_ANNOT_POLYGON: "polygon",
    pymupdf.PDF_ANNOT_POLY_LINE: "polyline",
}
# Tipos que no se muestran como anotaciones: tienen su propia gestion.
_SKIPPED = {pymupdf.PDF_ANNOT_WIDGET, pymupdf.PDF_ANNOT_LINK, pymupdf.PDF_ANNOT_POPUP, pymupdf.PDF_ANNOT_REDACT}
_ARROW_ENDS = {pymupdf.PDF_ANNOT_LE_OPEN_ARROW, pymupdf.PDF_ANNOT_LE_CLOSED_ARROW}


def _color(value: Any) -> list[float] | None:
    if not value:
        return None
    seq = list(value)
    if len(seq) == 1:  # gris
        return [round(seq[0], 4)] * 3
    if len(seq) == 3:
        return [round(c, 4) for c in seq]
    if len(seq) == 4:  # CMYK aproximado a RGB
        c, m, y, k = seq
        return [round((1 - c) * (1 - k), 4), round((1 - m) * (1 - k), 4), round((1 - y) * (1 - k), 4)]
    return None


def annot_id(annot: pymupdf.Annot) -> str:
    return f"a{annot.xref}"


def find_annot(page: pymupdf.Page, ident: str) -> pymupdf.Annot:
    xref = int(ident[1:])
    for annot in page.annots():
        if annot.xref == xref:
            return annot
    raise not_found(_("La anotación ya no existe."))


def describe(annot: pymupdf.Annot, geom: Geom) -> dict[str, Any] | None:
    type_no = annot.type[0]
    if type_no in _SKIPPED:
        return None
    kind = _KIND_BY_TYPE.get(type_no, "other")
    info = annot.info or {}
    colors = annot.colors or {}
    border = annot.border or {}
    opacity = annot.opacity
    out: dict[str, Any] = {
        "id": annot_id(annot),
        "kind": kind,
        "rect": geom.api_rect_d(annot.rect),
        "stroke": _color(colors.get("stroke")),
        "fill": _color(colors.get("fill")),
        "width": round(float(border.get("width", 1) if border.get("width", -1) > 0 else 1), 3),
        "opacity": round(opacity, 3) if 0 <= opacity <= 1 else 1.0,
        "contents": str(info.get("content") or "")[:20_000],
        "author": str(info.get("title") or "")[:200],
        "modified": pdf_date_to_iso(info.get("modDate")),
        "editable": kind in ("highlight", "underline", "strikeout", "squiggly", "note", "ink", "rect", "ellipse", "line"),
    }
    try:
        vertices = annot.vertices
    except Exception:  # noqa: BLE001 - anotaciones malformadas: se muestran sin geometria detallada
        vertices = None
    if vertices:
        if kind in _MARKUP:
            points = [geom.api_point_d(v) for v in vertices]
            out["quads"] = [points[i : i + 4] for i in range(0, len(points) - 3, 4)]
        elif kind == "ink":
            out["strokes"] = [[geom.api_point_d(p) for p in stroke] for stroke in vertices]
        elif kind == "line" and len(vertices) >= 2:
            out["points"] = [geom.api_point_d(vertices[0]), geom.api_point_d(vertices[1])]
            try:
                out["arrow"] = annot.line_ends[1] in _ARROW_ENDS
            except Exception:  # noqa: BLE001
                out["arrow"] = False
    return out


@method("page.annots", PageParams)
def page_annots(ctx: Ctx, p: PageParams) -> dict[str, Any]:
    session = ctx.engine.session(p.doc)
    page = session.page(p.page)
    geom = Geom(page)
    items = []
    for annot in page.annots():
        item = describe(annot, geom)
        if item is not None:
            items.append(item)
    return {"annots": items}


def _finish(annot: pymupdf.Annot, *, stroke, fill, width, opacity, contents, author, fill_allowed=True) -> None:
    kwargs: dict[str, Any] = {}
    if stroke is not None:
        kwargs["stroke"] = stroke
    if fill is not None and fill_allowed:
        kwargs["fill"] = fill
    if kwargs:
        annot.set_colors(**kwargs)
    if width is not None:
        annot.set_border(width=width)
    if opacity is not None and opacity < 0.999:
        annot.set_opacity(opacity)
    info: dict[str, str] = {}
    if contents:
        info["content"] = contents
    if author:
        info["title"] = author
    if info:
        annot.set_info(**info)
    annot.update()


def _result(session: DocSession, page_id: str, ident: str | None) -> dict[str, Any]:
    page = session.page(page_id)
    item = None
    if ident is not None:
        try:
            item = describe(find_annot(page, ident), Geom(page))
        except Exception:  # noqa: BLE001
            item = None
    return session.result([page_id], annot=item)


class MarkupParams(PageParams):
    kind: Literal["highlight", "underline", "strikeout", "squiggly"]
    quads: Annotated[list[Quad], Field(min_length=1, max_length=4_000)]
    color: Color = (1.0, 0.86, 0.2)
    opacity: Unit = 1.0
    contents: Comment = ""
    author: ShortText = ""


@method("annot.addMarkup", MarkupParams)
def annot_add_markup(ctx: Ctx, p: MarkupParams) -> dict[str, Any]:
    """Resalta, subraya o tacha las zonas indicadas (normalmente, una seleccion de texto)."""
    session = ctx.engine.session(p.doc)
    session.require("annotate")
    with session.edit({"highlight": _("Resaltar"), "underline": _("Subrayar"), "strikeout": _("Tachar"), "squiggly": _("Subrayado ondulado")}[p.kind]) as tx:
        page = session.page(p.page)
        geom = Geom(page)
        quads = []
        for quad in p.quads:
            xs = [pt[0] for pt in quad]
            ys = [pt[1] for pt in quad]
            rect = geom.rect_u((min(xs), min(ys), max(xs), max(ys)))
            if rect.width > 0.2 and rect.height > 0.2:
                # Tras deshacer el giro de la pagina se reconstruye el cuadrilatero
                # con sus vertices en el orden que espera el formato.
                quads.append(rect.quad)
        if not quads:
            raise invalid(_("No hay ninguna zona que marcar."))
        add = {
            "highlight": page.add_highlight_annot,
            "underline": page.add_underline_annot,
            "strikeout": page.add_strikeout_annot,
            "squiggly": page.add_squiggly_annot,
        }[p.kind]
        annot = add(quads=quads)
        _finish(annot, stroke=p.color, fill=None, width=None, opacity=p.opacity, contents=p.contents, author=p.author)
        ident = annot_id(annot)
        tx.touch(p.page)
    return _result(session, p.page, ident)


class AddParams(PageParams):
    kind: Literal["note", "ink", "rect", "ellipse", "line", "arrow"]
    point: Point | None = None  # nota
    rect: Rect | None = None  # rect, ellipse
    points: Annotated[list[Point], Field(min_length=2, max_length=2)] | None = None  # line, arrow
    strokes: Annotated[list[Stroke], Field(min_length=1, max_length=2_000)] | None = None  # ink
    stroke: Color | None = (0.86, 0.15, 0.2)
    fill: Color | None = None
    width: Width = 1.5
    opacity: Unit = 1.0
    contents: Comment = ""
    author: ShortText = ""


def _create(page: pymupdf.Page, geom: Geom, p: AddParams) -> pymupdf.Annot:
    if p.kind == "note":
        if p.point is None:
            raise invalid(_("Falta la posición de la nota."))
        annot = page.add_text_annot(geom.point_u(p.point), p.contents or "", icon="Note")
        _finish(annot, stroke=p.stroke or (1.0, 0.82, 0.2), fill=None, width=None, opacity=p.opacity, contents=p.contents, author=p.author)
        return annot
    if p.kind in ("rect", "ellipse"):
        if p.rect is None:
            raise invalid(_("Falta el rectángulo de la forma."))
        rect = geom.rect_u(p.rect)
        if rect.width < 1 or rect.height < 1:
            raise invalid(_("La forma es demasiado pequeña."))
        annot = page.add_rect_annot(rect) if p.kind == "rect" else page.add_circle_annot(rect)
        _finish(annot, stroke=p.stroke or (0, 0, 0), fill=p.fill, width=p.width, opacity=p.opacity, contents=p.contents, author=p.author)
        return annot
    if p.kind in ("line", "arrow"):
        if p.points is None:
            raise invalid(_("Faltan los extremos de la línea."))
        a, b = geom.point_u(p.points[0]), geom.point_u(p.points[1])
        if abs(a - b) < 1:
            raise invalid(_("La línea es demasiado corta."))
        annot = page.add_line_annot(a, b)
        if p.kind == "arrow":
            annot.set_line_ends(pymupdf.PDF_ANNOT_LE_NONE, pymupdf.PDF_ANNOT_LE_OPEN_ARROW)
        _finish(annot, stroke=p.stroke or (0, 0, 0), fill=p.stroke, width=p.width, opacity=p.opacity, contents=p.contents,
                author=p.author, fill_allowed=p.kind == "arrow")
        return annot
    # ink
    if not p.strokes:
        raise invalid(_("Faltan los trazos del dibujo."))
    strokes = []
    for stroke in p.strokes:
        points = [geom.point_u(pt) for pt in stroke]
        if len(points) == 1:  # un toque sin arrastre: se dibuja un punto
            points.append(points[0] + (0.1, 0.1))
        strokes.append([(pt.x, pt.y) for pt in points])
    annot = page.add_ink_annot(strokes)
    _finish(annot, stroke=p.stroke or (0, 0, 0), fill=None, width=p.width, opacity=p.opacity, contents=p.contents, author=p.author)
    return annot


_ADD_LABEL = {"note": N_("Añadir nota"), "ink": N_("Dibujar"), "rect": N_("Añadir rectángulo"),
              "ellipse": N_("Añadir elipse"), "line": N_("Añadir línea"), "arrow": N_("Añadir flecha")}


@method("annot.add", AddParams)
def annot_add(ctx: Ctx, p: AddParams) -> dict[str, Any]:
    session = ctx.engine.session(p.doc)
    session.require("annotate")
    with session.edit(_(_ADD_LABEL[p.kind])) as tx:
        page = session.page(p.page)
        annot = _create(page, Geom(page), p)
        ident = annot_id(annot)
        tx.touch(p.page)
    return _result(session, p.page, ident)


class UpdateParams(PageParams):
    id: AnnotId
    rect: Rect | None = None
    point: Point | None = None
    points: Annotated[list[Point], Field(min_length=2, max_length=2)] | None = None
    #: desplazamiento (dx, dy) en coordenadas de visualizacion; mueve cualquier anotacion
    move: Point | None = None
    stroke: Color | None = None
    fill: Color | None = None
    clearFill: bool = False
    width: Width | None = None
    opacity: Unit | None = None
    contents: Comment | None = None


def _move_vector(geom: Geom, move: Point) -> tuple[float, float]:
    # El desplazamiento se aplica con Annot.set_rect: mismo espacio que Annot.rect.
    origin = geom.api_point_u((0.0, 0.0))
    target = geom.api_point_u(move)
    return (target.x - origin.x, target.y - origin.y)


@method("annot.update", UpdateParams)
def annot_update(ctx: Ctx, p: UpdateParams) -> dict[str, Any]:
    """Cambia el aspecto, el comentario o la posicion de una anotacion."""
    session = ctx.engine.session(p.doc)
    session.require("annotate")
    with session.edit(_("Editar anotación")) as tx:
        page = session.page(p.page)
        geom = Geom(page)
        annot = find_annot(page, p.id)
        current = describe(annot, geom)
        if current is None or not current["editable"]:
            raise invalid(_("Este tipo de anotación no se puede modificar."))
        kind = current["kind"]
        ident = p.id
        geometry_change = p.rect is not None or p.point is not None or p.points is not None or p.move is not None

        if geometry_change and kind in ("ink", "line") or (geometry_change and kind in _MARKUP):
            if kind in _MARKUP:
                raise invalid(_("Las marcas de texto no se pueden mover."))
            # Trazos y lineas no admiten cambiar de sitio en el lugar: se recrean
            # con la geometria nueva conservando su aspecto y su comentario.
            dx, dy = p.move if p.move is not None else (0.0, 0.0)
            spec: dict[str, Any] = {
                "doc": p.doc, "page": p.page,
                "stroke": p.stroke or current["stroke"], "width": p.width or current["width"],
                "opacity": p.opacity if p.opacity is not None else current["opacity"],
                "contents": p.contents if p.contents is not None else current["contents"],
                "author": current["author"],
            }
            if kind == "ink":
                spec.update(kind="ink", strokes=[[(x + dx, y + dy) for x, y in s] for s in current.get("strokes", [])])
            else:
                ends = p.points if p.points is not None else [(x + dx, y + dy) for x, y in current.get("points", [])]
                spec.update(kind="arrow" if current.get("arrow") else "line", points=ends)
            page.delete_annot(annot)
            ident = annot_id(_create(page, geom, AddParams.model_validate(spec)))
            tx.label = _("Mover anotación")
        else:
            if p.rect is not None and kind in ("rect", "ellipse"):
                rect = geom.api_rect_u(p.rect)
                if rect.width < 1 or rect.height < 1:
                    raise invalid(_("La forma es demasiado pequeña."))
                annot.set_rect(rect)
                tx.label = _("Cambiar tamaño")
            elif p.move is not None:
                dx, dy = _move_vector(geom, p.move)
                annot.set_rect(annot.rect + (dx, dy, dx, dy))
                tx.label = _("Mover anotación")
            elif p.point is not None and kind == "note":
                at = geom.api_point_u(p.point)
                size = annot.rect
                annot.set_rect(pymupdf.Rect(at.x, at.y, at.x + size.width, at.y + size.height))
                tx.label = _("Mover nota")
            colors: dict[str, Any] = {}
            if p.stroke is not None:
                colors["stroke"] = p.stroke
            if p.fill is not None and kind in ("rect", "ellipse"):
                colors["fill"] = p.fill
            elif p.clearFill and kind in ("rect", "ellipse"):
                colors["stroke"] = p.stroke or current["stroke"] or (0, 0, 0)
                colors["fill"] = []
            if colors:
                annot.set_colors(**colors)
            if p.width is not None and kind not in _MARKUP and kind != "note":
                annot.set_border(width=p.width)
            if p.opacity is not None:
                annot.set_opacity(p.opacity)
            if p.contents is not None:
                annot.set_info(content=p.contents)
            annot.update()
        tx.touch(p.page)
    return _result(session, p.page, ident)


class DeleteParams(PageParams):
    ids: Annotated[list[AnnotId], Field(min_length=1, max_length=2_000)]


@method("annot.delete", DeleteParams)
def annot_delete(ctx: Ctx, p: DeleteParams) -> dict[str, Any]:
    session = ctx.engine.session(p.doc)
    session.require("annotate")
    with session.edit(_("Eliminar anotación")) as tx:
        page = session.page(p.page)
        wanted = {int(i[1:]) for i in p.ids}
        victims = [a for a in page.annots() if a.xref in wanted and a.type[0] not in _SKIPPED]
        if not victims:
            tx.skip = True
        for annot in victims:
            page.delete_annot(annot)
        tx.touch(p.page)
    return session.result([p.page])


class FlattenParams(DocParams):
    pages: list[PageId] | None = None


@method("annot.flatten", FlattenParams)
def annot_flatten(ctx: Ctx, p: FlattenParams) -> dict[str, Any]:
    """Convierte las anotaciones en contenido fijo de la pagina (ya no se pueden mover ni quitar)."""
    session = ctx.engine.session(p.doc)
    session.require("modify")
    with session.edit(_("Acoplar anotaciones")) as tx:
        has_any = any(a.type[0] not in _SKIPPED for page in session.doc for a in page.annots())
        if not has_any:
            tx.skip = True
        else:
            session.doc.bake(annots=True, widgets=False)
            tx.touch(*session.page_ids)
    return session.result(list(session.page_ids))
