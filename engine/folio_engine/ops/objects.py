"""Elementos anadidos a la pagina: texto, imagenes, firma manuscrita y formas."""

from __future__ import annotations

import math
from typing import Annotated, Any, Literal

import pymupdf
from pydantic import Field, StringConstraints, ValidationError

from .. import content
from ..errors import invalid
from ..fonts import DEFAULT_FAMILY, available_families, known, register as register_font
from ..geometry import Geom, box_corners
from ..i18n import _
from ..imaging import load_image
from ..models import Angle, Color, Coord, ObjId, PageParams, Params, Rect, Unit
from ..server import Ctx, method
from ..session import EXPORT_HOOKS, DocSession
from ..sources import compose_texts, image_source, shape_source, text_metrics, text_source

Size = Annotated[float, Field(gt=0.2, le=20_000, allow_inf_nan=False)]
FontSize = Annotated[float, Field(ge=2, le=1_000, allow_inf_nan=False)]
Text = Annotated[str, StringConstraints(min_length=1, max_length=20_000)]


class TextStyle(Params):
    font: Annotated[str, StringConstraints(max_length=60)] = DEFAULT_FAMILY
    bold: bool = False
    italic: bool = False
    size: FontSize = 12
    color: Color = (0.0, 0.0, 0.0)
    # "justify" solo tiene efecto con un ancho fijo (sin el, se alinea a la izquierda).
    align: Literal["left", "center", "right", "justify"] = "left"
    lineHeight: Annotated[float, Field(ge=0.8, le=3.0, allow_inf_nan=False)] = 1.2
    # Ancho de la columna de texto, en puntos: las lineas se reparten solas en
    # ese ancho (un parrafo editado). Sin el, los saltos son los que se escriben.
    width: Annotated[float, Field(ge=1, le=20_000, allow_inf_nan=False)] | None = None

    def normalized(self) -> dict[str, Any]:
        data = self.model_dump()
        # La familia solo puede ser una del catalogo (lista blanca) o una extra ya
        # entregada (Aptos); si no, la predeterminada.
        if not known(data["font"]):
            data["font"] = DEFAULT_FAMILY
        data["color"] = [round(c, 4) for c in data["color"]]
        if data["width"] is None:
            del data["width"]  # un texto normal guarda lo mismo que antes
        else:
            data["width"] = round(data["width"], 3)
        return data


def _describe(placed: content.Placed, page: pymupdf.Page) -> dict[str, Any]:
    out = placed.describe(Geom(page))
    if placed.kind == "text" and "size" in placed.props:
        try:
            out["metrics"] = text_metrics(placed.props)
        except Exception:  # noqa: BLE001 - props de un PDF ajeno: se muestra sin metricas
            pass
    return out


def _anchor_shift(align: str, size: tuple[float, float]) -> tuple[float, float]:
    """Punto de la caja (respecto a su centro) que permanece fijo al cambiar el texto."""
    if align == "center":
        return (0.0, -size[1] / 2)
    if align == "right":
        return (size[0] / 2, -size[1] / 2)
    return (-size[0] / 2, -size[1] / 2)


def _rotate(vec: tuple[float, float], angle: float) -> tuple[float, float]:
    rad = math.radians(angle)
    cos, sin = math.cos(rad), math.sin(rad)
    return (vec[0] * cos - vec[1] * sin, vec[0] * sin + vec[1] * cos)


@method("fonts.list")
def fonts_list(ctx: Ctx, p: Params) -> dict[str, Any]:
    return {"fonts": available_families(), "default": DEFAULT_FAMILY}


class RegisterFontParams(Params):
    family: Literal["Aptos"]
    style: Literal["regular", "bold", "italic", "boldItalic"]


@method("fonts.register", RegisterFontParams)
def fonts_register(ctx: Ctx, p: RegisterFontParams) -> dict[str, Any]:
    """Recibe en memoria una variante de una familia extra (solo la llama la aplicacion, ver src/main/fonts.ts)."""
    try:
        register_font(p.family, p.style, ctx.binary)
    except ValueError:
        raise invalid(_("La fuente recibida no es válida.")) from None
    return {"fonts": available_families()}


@method("page.objects", PageParams)
def page_objects(ctx: Ctx, p: PageParams) -> dict[str, Any]:
    session = ctx.engine.session(p.doc)
    page = session.page(p.page)
    return {"objects": [_describe(o, page) for o in content.list_objects(session.doc, page)]}


class AddTextParams(PageParams):
    x: Coord  # esquina superior izquierda de la caja, en coordenadas de visualizacion
    y: Coord
    text: Text
    style: TextStyle = TextStyle()
    angle: Angle = 0.0
    opacity: Unit = 1.0


@method("obj.addText", AddTextParams)
def obj_add_text(ctx: Ctx, p: AddTextParams) -> dict[str, Any]:
    session = ctx.engine.session(p.doc)
    session.require("modify")
    style = p.style.normalized()
    source, size, _metrics = text_source(p.text, style)
    try:
        with session.edit(_("Añadir texto")) as tx:
            page = session.page(p.page)
            geom = Geom(page)
            shift = _rotate((size[0] / 2, size[1] / 2), p.angle)
            center_u = geom.point_u((p.x + shift[0], p.y + shift[1]))
            placed = content.place(
                session.doc, page, source, kind="text", center=(center_u.x, center_u.y), size=size,
                angle=geom.angle_u(p.angle), opacity=p.opacity, props={"text": p.text, **style},
            )
            tx.touch(p.page)
            obj_id = placed.id
    finally:
        source.close()
    return _result(session, p.page, obj_id)


class SetTextParams(PageParams):
    id: ObjId
    text: Text | None = None
    style: TextStyle | None = None


@method("obj.setText", SetTextParams)
def obj_set_text(ctx: Ctx, p: SetTextParams) -> dict[str, Any]:
    session = ctx.engine.session(p.doc)
    session.require("modify")
    page = session.page(p.page)
    old = content.find_object(session.doc, page, p.id)
    if old.kind != "text":
        raise invalid(_("El elemento no es un texto."))
    text = p.text if p.text is not None else str(old.props.get("text", ""))
    if p.style is not None:
        style = p.style.normalized()
    else:
        style = TextStyle.model_validate({k: old.props[k] for k in TextStyle.model_fields if k in old.props}).normalized()
    source, size, _metrics = text_source(text, style)
    try:
        with session.edit(_("Editar texto")) as tx:
            page = session.page(p.page)
            old = content.find_object(session.doc, page, p.id)
            # El punto de anclaje (segun la alineacion) no se mueve al cambiar el tamano de la caja.
            old_anchor = _rotate(_anchor_shift(str(old.props.get("align", "left")), old.size), old.angle)
            new_anchor = _rotate(_anchor_shift(style["align"], size), old.angle)
            center = (
                old.center[0] + old_anchor[0] - new_anchor[0],
                old.center[1] + old_anchor[1] - new_anchor[1],
            )
            position = content.remove(session.doc, page, old)
            content.place(
                session.doc, page, source, kind="text", center=center, size=size, angle=old.angle,
                opacity=old.opacity, props={"text": text, **style}, obj_id=old.id, position=position,
            )
            tx.touch(p.page)
    finally:
        source.close()
    return _result(session, p.page, p.id)


class AddImageParams(PageParams):
    rect: Rect  # caja de destino en coordenadas de visualizacion
    angle: Angle = 0.0
    opacity: Unit = 1.0
    kind: Literal["image", "signature", "stamp"] = "image"
    keepAspect: bool = True
    background: bool = False


@method("obj.addImage", AddImageParams)
def obj_add_image(ctx: Ctx, p: AddImageParams) -> dict[str, Any]:
    """Coloca la imagen recibida como carga binaria de la peticion."""
    session = ctx.engine.session(p.doc)
    session.require("modify")
    image = load_image(ctx.binary)
    box = pymupdf.Rect(p.rect).normalize()
    if box.width < 0.5 or box.height < 0.5:
        raise invalid(_("La zona de destino es demasiado pequeña."))
    w, h = box.width, box.height
    if p.keepAspect:
        scale = min(w / image.width, h / image.height)
        w, h = image.width * scale, image.height * scale
    source, _src_size = image_source(image)
    label = {"image": _("Añadir imagen"), "signature": _("Añadir firma"), "stamp": _("Añadir sello")}[p.kind]
    try:
        with session.edit(label) as tx:
            page = session.page(p.page)
            geom = Geom(page)
            center_u = geom.point_u(((box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2))
            placed = content.place(
                session.doc, page, source, kind=p.kind, center=(center_u.x, center_u.y), size=(w, h),
                angle=geom.angle_u(p.angle), opacity=p.opacity,
                props={"px": [image.width, image.height]}, background=p.background,
            )
            tx.touch(p.page)
            obj_id = placed.id
    finally:
        source.close()
    return _result(session, p.page, obj_id)


class AddShapeParams(PageParams):
    shape: Literal["rect", "ellipse", "line", "check", "cross", "dot"]
    rect: Rect
    stroke: Color | None = (0.0, 0.0, 0.0)
    fill: Color | None = None
    width: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)] = 1.0
    angle: Angle = 0.0
    opacity: Unit = 1.0


@method("obj.addShape", AddShapeParams)
def obj_add_shape(ctx: Ctx, p: AddShapeParams) -> dict[str, Any]:
    session = ctx.engine.session(p.doc)
    session.require("modify")
    box = pymupdf.Rect(p.rect).normalize()
    if box.width < 0.5 and box.height < 0.5:
        raise invalid(_("La forma es demasiado pequeña."))
    size = (max(box.width, 0.5), max(box.height, 0.5))
    if p.stroke is None and p.fill is None and p.shape in ("rect", "ellipse"):
        raise invalid(_("La forma necesita color de borde o de relleno."))
    source, _size = shape_source(p.shape, size, stroke=p.stroke, fill=p.fill, width=p.width)
    props = {
        "shape": p.shape,
        "stroke": list(p.stroke) if p.stroke else None,
        "fill": list(p.fill) if p.fill else None,
        "width": p.width,
    }
    try:
        with session.edit(_("Añadir forma")) as tx:
            page = session.page(p.page)
            geom = Geom(page)
            center_u = geom.point_u(((box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2))
            placed = content.place(
                session.doc, page, source, kind="shape", center=(center_u.x, center_u.y), size=size,
                angle=geom.angle_u(p.angle), opacity=p.opacity, props=props,
            )
            tx.touch(p.page)
            obj_id = placed.id
    finally:
        source.close()
    return _result(session, p.page, obj_id)


class SetShapeParams(PageParams):
    id: ObjId
    stroke: Color | None = None
    clearStroke: bool = False
    fill: Color | None = None
    clearFill: bool = False
    width: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)] | None = None


@method("obj.setShape", SetShapeParams)
def obj_set_shape(ctx: Ctx, p: SetShapeParams) -> dict[str, Any]:
    """Cambia los colores o el grosor de una forma conservando su posicion."""
    session = ctx.engine.session(p.doc)
    session.require("modify")
    with session.edit(_("Editar forma")) as tx:
        page = session.page(p.page)
        old = content.find_object(session.doc, page, p.id)
        if old.kind != "shape" or old.props.get("shape") not in ("rect", "ellipse", "line", "check", "cross", "dot"):
            raise invalid(_("El elemento no es una forma."))
        props = dict(old.props)
        if p.clearStroke:
            props["stroke"] = None
        elif p.stroke is not None:
            props["stroke"] = list(p.stroke)
        if p.clearFill:
            props["fill"] = None
        elif p.fill is not None:
            props["fill"] = list(p.fill)
        if p.width is not None:
            props["width"] = p.width
        if props.get("stroke") is None and props.get("fill") is None:
            raise invalid(_("La forma necesita color de borde o de relleno."))
        stroke = tuple(props["stroke"]) if props.get("stroke") else None
        fill = tuple(props["fill"]) if props.get("fill") else None
        source, _size = shape_source(str(props["shape"]), old.size, stroke=stroke, fill=fill, width=float(props.get("width", 1.0)))
        try:
            position = content.remove(session.doc, page, old)
            content.place(
                session.doc, page, source, kind="shape", center=old.center, size=old.size, angle=old.angle,
                opacity=old.opacity, props=props, obj_id=old.id, position=position,
            )
        finally:
            source.close()
        tx.touch(p.page)
    return _result(session, p.page, p.id)


class TransformParams(PageParams):
    id: ObjId
    cx: Coord | None = None
    cy: Coord | None = None
    w: Size | None = None
    h: Size | None = None
    angle: Angle | None = None
    opacity: Unit | None = None


@method("obj.transform", TransformParams)
def obj_transform(ctx: Ctx, p: TransformParams) -> dict[str, Any]:
    """Mueve, escala, gira o cambia la opacidad de un elemento."""
    session = ctx.engine.session(p.doc)
    session.require("modify")
    with session.edit(_("Mover elemento")) as tx:
        page = session.page(p.page)
        geom = Geom(page)
        placed = content.find_object(session.doc, page, p.id)
        center = None
        if p.cx is not None or p.cy is not None:
            current = geom.point_d(placed.center)
            point = geom.point_u((p.cx if p.cx is not None else current[0], p.cy if p.cy is not None else current[1]))
            center = (point.x, point.y)
        size = None
        props = None
        if p.w is not None or p.h is not None:
            size = (p.w if p.w is not None else placed.size[0], p.h if p.h is not None else placed.size[1])
            if placed.kind == "text" and "size" in placed.props:
                # Al escalar un texto, su tamano de letra y su ancho de columna
                # (si lo tiene) siguen al alto de la caja.
                props = dict(placed.props)
                factor = size[1] / placed.size[1]
                props["size"] = round(float(props["size"]) * factor, 3)
                if isinstance(props.get("width"), (int, float)) and props["width"] > 0:
                    props["width"] = round(float(props["width"]) * factor, 3)
            tx.label = _("Cambiar tamaño")
        if p.angle is not None:
            tx.label = _("Girar elemento")
        content.update_placement(
            session.doc, page, placed, center=center, size=size,
            angle=geom.angle_u(p.angle) if p.angle is not None else None, opacity=p.opacity, props=props,
        )
        tx.touch(p.page)
    return _result(session, p.page, p.id)


class DeleteParams(PageParams):
    ids: Annotated[list[ObjId], Field(min_length=1, max_length=500)]


@method("obj.delete", DeleteParams)
def obj_delete(ctx: Ctx, p: DeleteParams) -> dict[str, Any]:
    session = ctx.engine.session(p.doc)
    session.require("modify")
    with session.edit(_("Eliminar elemento")) as tx:
        page = session.page(p.page)
        wanted = set(p.ids)
        found = [o for o in content.list_objects(session.doc, page) if o.id in wanted]
        if not found:
            tx.skip = True
        for placed in found:
            content.remove(session.doc, page, placed)
        tx.touch(p.page)
    return session.result([p.page])


class OrderParams(PageParams):
    id: ObjId
    front: bool = True


@method("obj.order", OrderParams)
def obj_order(ctx: Ctx, p: OrderParams) -> dict[str, Any]:
    session = ctx.engine.session(p.doc)
    session.require("modify")
    with session.edit(_("Traer al frente") if p.front else _("Enviar al fondo")) as tx:
        page = session.page(p.page)
        content.reorder(session.doc, page, content.find_object(session.doc, page, p.id), p.front)
        tx.touch(p.page)
    return _result(session, p.page, p.id)


def share_text_fonts(doc: pymupdf.Document) -> int:
    """Hace que los textos anadidos compartan una sola fuente incrustada por tipo de letra.

    Cada texto se crea con su propia fuente (unos 15 KB de glifos): treinta
    cuadros de texto en un formulario anadian casi medio mega. Antes de guardar
    se componen todos juntos y cada tipo de letra se incrusta una vez. Un texto
    solo se sustituye si recompuesto mide exactamente lo mismo (con otra version
    de la fuente podria no hacerlo): guardar nunca debe cambiar el aspecto.
    Devuelve cuantos textos comparten ahora fuente.
    """
    found: list[tuple[int, str, str, dict[str, Any]]] = []
    for index in range(doc.page_count):
        page = doc[index]
        for placed in content.list_objects(doc, page):
            text = placed.props.get("text")
            if placed.kind != "text" or not isinstance(text, str) or not text.strip():
                continue
            try:
                style = TextStyle.model_validate(
                    {key: placed.props[key] for key in TextStyle.model_fields if key in placed.props}
                ).normalized()
            except ValidationError:
                continue  # etiqueta con un estilo que no se reconoce: se deja como esta
            # Un texto escalado sigue dibujando su contenido original ampliado
            # por la colocacion: se recompone al tamano con que se creo.
            natural = (placed.source[2] - placed.source[0], placed.source[3] - placed.source[1])
            if natural[0] <= 0 or natural[1] <= 0:
                continue
            grown = (placed.size[0] / natural[0], placed.size[1] / natural[1])
            if abs(grown[0] - grown[1]) > 1e-3 * max(grown):
                continue  # deformado: no se puede reproducir componiendo texto
            if abs(grown[0] - 1.0) > 1e-6:
                style = {**style, "size": style["size"] / grown[0]}
                if style.get("width"):
                    style["width"] = style["width"] / grown[0]
            found.append((index, placed.id, text, style))
    if len(found) < 2:
        return 0
    book, sizes, _metrics = compose_texts([(text, style) for _index, _id, text, style in found])
    try:
        shared = 0
        for number, (index, obj_id, _text, _style) in enumerate(found):
            page = doc[index]
            placed = content.find_object(doc, page, obj_id)
            natural = (placed.source[2] - placed.source[0], placed.source[3] - placed.source[1])
            # Si no mide lo mismo (otra version de la fuente, un tamano que no se
            # reproduce con exactitud), se deja como esta.
            if abs(natural[0] - sizes[number][0]) > 0.005 or abs(natural[1] - sizes[number][1]) > 0.005:
                continue
            # Solo cambia el contenido que dibuja: su colocacion exacta, su
            # orden y su etiqueta quedan como estaban.
            if content.swap_source(doc, page, placed, book, pno=number):
                shared += 1
        return shared
    finally:
        book.close()


if share_text_fonts not in EXPORT_HOOKS:
    EXPORT_HOOKS.append(share_text_fonts)


def _result(session: DocSession, page_id: str, obj_id: str) -> dict[str, Any]:
    page = session.page(page_id)
    placed = content.find_object(session.doc, page, obj_id)
    return session.result([page_id], object=_describe(placed, page))


def corners_d(placed: content.Placed, page: pymupdf.Page) -> list[list[float]]:
    geom = Geom(page)
    return box_corners(geom.point_d(placed.center), placed.size, geom.angle_d(placed.angle))
