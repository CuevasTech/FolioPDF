"""Dibujo de paginas."""

from __future__ import annotations

from typing import Annotated, Any, Literal

import pymupdf
from pydantic import Field
from pymupdf import mupdf

from ..errors import invalid
from ..i18n import _
from ..models import PageParams, Rect
from ..server import Ctx, Reply, method
from ..session import DocSession

# Tope de pixeles por peticion: acota la memoria que puede pedir un zoom
# extremo (48 MP son ~190 MB en RGBA). Por encima, la interfaz pide recortes.
MAX_PIXELS = 48_000_000
_DL_CACHE_SIZE = 8

Scale = Annotated[float, Field(gt=0.01, le=64, allow_inf_nan=False)]


def display_list(session: DocSession, page_id: str, annots: bool) -> pymupdf.DisplayList:
    """Lista de dibujo de la pagina, reutilizada entre zooms mientras no cambie el documento."""
    cache: dict[Any, pymupdf.DisplayList] = session.cache.setdefault("dl", {})
    key = (page_id, annots)
    found = cache.pop(key, None)
    if found is None:
        found = session.page(page_id).get_displaylist(annots=annots)
        while len(cache) >= _DL_CACHE_SIZE:
            cache.pop(next(iter(cache)))
    cache[key] = found  # reinsertar lo deja como el mas reciente
    return found


def _target(dl: pymupdf.DisplayList, scale: float, clip: Rect | None):
    ctm = mupdf.FzMatrix(scale, 0, 0, scale, 0, 0)
    area = mupdf.fz_bound_display_list(dl.this)
    if clip is not None:
        area = mupdf.fz_intersect_rect(area, mupdf.FzRect(*pymupdf.Rect(clip).normalize()))
    if mupdf.fz_is_empty_rect(area):
        raise invalid(_("El recorte queda fuera de la página."))
    irect = mupdf.fz_round_rect(mupdf.fz_transform_rect(area, ctm))
    width, height = irect.x1 - irect.x0, irect.y1 - irect.y0
    if width < 1 or height < 1:
        raise invalid(_("La zona a dibujar es demasiado pequeña."))
    if width * height > MAX_PIXELS:
        raise invalid(_("La zona a dibujar es demasiado grande para esta escala."))
    return ctm, area, irect


def render_pixmap(
    session: DocSession,
    page_id: str,
    scale: float,
    clip: Rect | None = None,
    *,
    annots: bool = True,
    alpha_channel: bool = True,
) -> pymupdf.Pixmap:
    """Dibuja la pagina (o un recorte, en coordenadas de visualizacion) sobre fondo blanco.

    Con ``alpha_channel`` el resultado es RGBA opaco, listo para pintarse en un
    lienzo del navegador sin conversion intermedia.
    """
    dl = display_list(session, page_id, annots)
    ctm, area, irect = _target(dl, scale, clip)
    colorspace = mupdf.FzColorspace(mupdf.FzColorspace.Fixed_RGB)
    pix = mupdf.fz_new_pixmap_with_bbox(colorspace, irect, mupdf.FzSeparations(), 1 if alpha_channel else 0)
    mupdf.fz_clear_pixmap_with_value(pix, 0xFF)
    device = mupdf.fz_new_draw_device_with_bbox(ctm, pix, irect)
    mupdf.fz_run_display_list(dl.this, device, mupdf.FzMatrix(), area, mupdf.FzCookie())
    mupdf.fz_close_device(device)
    return pymupdf.Pixmap("raw", pix)


class RenderParams(PageParams):
    scale: Scale
    clip: Rect | None = None
    annots: bool = True


@method("page.render", RenderParams)
def page_render(ctx: Ctx, p: RenderParams) -> Reply:
    session = ctx.engine.session(p.doc)
    pix = render_pixmap(session, p.page, p.scale, p.clip, annots=p.annots)
    info = {"w": pix.width, "h": pix.height, "x": pix.x, "y": pix.y, "gen": session.gens.get(p.page, 0)}
    return Reply(info, pix.samples_mv, keep=pix)


class ImageParams(PageParams):
    scale: Scale
    clip: Rect | None = None
    annots: bool = True
    format: Literal["png", "jpeg"] = "png"
    quality: Annotated[int, Field(ge=30, le=100)] = 90


@method("page.image", ImageParams)
def page_image(ctx: Ctx, p: ImageParams) -> Reply:
    """Pagina codificada como PNG o JPEG (impresion, exportacion, miniaturas a archivo)."""
    session = ctx.engine.session(p.doc)
    pix = render_pixmap(session, p.page, p.scale, p.clip, annots=p.annots, alpha_channel=False)
    if p.format == "png":
        data = pix.tobytes("png")
    else:
        data = pix.tobytes("jpg", jpg_quality=p.quality)
    return Reply({"w": pix.width, "h": pix.height, "format": p.format}, data)
