"""Texto de las paginas: capa de seleccion, busqueda y enlaces."""

from __future__ import annotations

from typing import Annotated, Any

import pymupdf
from pydantic import Field, StringConstraints

from ..geometry import Geom
from ..models import DocParams, PageId, PageParams
from ..server import Ctx, method
from ..session import DocSession

# Sin TEXT_PRESERVE_LIGATURES: las ligaduras ("fi") se devuelven como letras
# sueltas, que es lo que se espera al copiar y al buscar.
_TEXT_FLAGS = pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_MEDIABOX_CLIP

_MAX_WORDS_PER_PAGE = 40_000


def _round_rect(r: list[float]) -> list[float]:
    return [round(v, 2) for v in r]


@method("page.text", PageParams)
def page_text(ctx: Ctx, p: PageParams) -> dict[str, Any]:
    """Palabras de la pagina agrupadas por linea, en coordenadas de visualizacion."""
    session = ctx.engine.session(p.doc)
    page = session.page(p.page)
    geom = Geom(page)
    textpage = page.get_textpage(flags=_TEXT_FLAGS)
    try:
        words = textpage.extractWORDS()
        layout = textpage.extractDICT()
    finally:
        del textpage

    line_info: dict[tuple[int, int], tuple[tuple[float, float], float]] = {}
    for block_no, block in enumerate(layout.get("blocks", [])):
        if block.get("type", 0) != 0:
            continue
        for line_no, line in enumerate(block.get("lines", [])):
            spans = line.get("spans") or []
            size = max((s.get("size", 0.0) for s in spans), default=0.0)
            line_info[(block_no, line_no)] = (tuple(line.get("dir", (1.0, 0.0))), size)

    lines: list[dict[str, Any]] = []
    current_key: tuple[int, int] | None = None
    current: dict[str, Any] | None = None
    for count, (x0, y0, x1, y1, word, block_no, line_no, _word_no) in enumerate(words):
        if count >= _MAX_WORDS_PER_PAGE:
            break
        key = (block_no, line_no)
        if key != current_key:
            direction, size = line_info.get(key, ((1.0, 0.0), 0.0))
            current = {"d": geom.vector_d(direction), "s": round(size, 2), "w": []}
            lines.append(current)
            current_key = key
        assert current is not None
        current["w"].append([*_round_rect(geom.rect_d((x0, y0, x1, y1))), word])
    return {"lines": lines, "gen": session.gens.get(p.page, 0)}


class PlainTextParams(DocParams):
    pages: list[PageId] | None = None


@method("doc.plainText", PlainTextParams)
def doc_plain_text(ctx: Ctx, p: PlainTextParams) -> dict[str, Any]:
    """Texto plano de las paginas indicadas (o de todo el documento)."""
    session = ctx.engine.session(p.doc)
    session.require("copy")
    ids = p.pages if p.pages is not None else list(session.page_ids)
    parts = []
    for n, pid in enumerate(ids):
        ctx.progress(n, len(ids))
        parts.append(session.page(pid).get_text("text", flags=_TEXT_FLAGS))
    return {"text": "\f".join(parts)}


class SearchParams(DocParams):
    query: Annotated[str, StringConstraints(min_length=1, max_length=400)]
    case: bool = False
    whole: bool = False
    limit: Annotated[int, Field(ge=1, le=20_000)] = 5_000


def _whole_word(page: pymupdf.Page, rect: pymupdf.Rect, words: list[tuple]) -> bool:
    """El acierto empieza y acaba en limites de palabra de su linea."""
    starts = ends = False
    mid = (rect.y0 + rect.y1) / 2
    for w in words:
        if not (w[1] <= mid <= w[3]):
            continue
        if abs(w[0] - rect.x0) <= 1.0:
            starts = True
        if abs(w[2] - rect.x1) <= 1.0:
            ends = True
    return starts and ends


@method("doc.search", SearchParams)
def doc_search(ctx: Ctx, p: SearchParams) -> dict[str, Any]:
    session = ctx.engine.session(p.doc)
    query = p.query
    hits: list[dict[str, Any]] = []
    total = 0
    truncated = False
    count = len(session.page_ids)
    for index, pid in enumerate(session.page_ids):
        ctx.progress(index, count)
        page = session.doc[index]
        found = page.search_for(query, quads=True, flags=_TEXT_FLAGS | pymupdf.TEXT_DEHYPHENATE)
        if not found:
            continue
        geom = Geom(page)
        words = page.get_text("words", flags=_TEXT_FLAGS) if p.whole else []
        quads = []
        for quad in found:
            rect = quad.rect
            if p.case and query not in page.get_textbox(rect + (-0.5, -0.5, 0.5, 0.5)):
                continue
            if p.whole and not _whole_word(page, rect, words):
                continue
            quads.append([[round(v, 2) for v in pt] for pt in geom.quad_d(quad)])
            total += 1
            if total >= p.limit:
                truncated = True
                break
        if quads:
            hits.append({"page": pid, "quads": quads})
        if truncated:
            break
    return {"hits": hits, "total": total, "truncated": truncated}


# Unicos esquemas de enlace que la interfaz puede ofrecer abrir. Los enlaces
# que lanzan archivos o programas (Launch, GoToR) se descartan aqui mismo: un
# PDF no debe poder ejecutar nada en el equipo.
_SAFE_SCHEMES = ("http://", "https://", "mailto:")


@method("page.links", PageParams)
def page_links(ctx: Ctx, p: PageParams) -> dict[str, Any]:
    session = ctx.engine.session(p.doc)
    page = session.page(p.page)
    out: list[dict[str, Any]] = []
    for link in page.get_links():
        rect = link.get("from")
        if rect is None:
            continue
        # A diferencia de las anotaciones, PyMuPDF devuelve los enlaces (zona y
        # destino) ya en el espacio de visualizacion: no hay que girarlos.
        item: dict[str, Any] = {"r": _round_rect(pymupdf.Rect(rect).normalize())}
        kind = link.get("kind")
        if kind == pymupdf.LINK_GOTO:
            target = link.get("page", -1)
            if not isinstance(target, int) or not 0 <= target < len(session.page_ids):
                continue
            item["page"] = session.page_ids[target]
            point = link.get("to")
            if point is not None:
                try:
                    item["y"] = round(max(0.0, float(point.y)), 3)
                except Exception:  # noqa: BLE001
                    pass
        elif kind == pymupdf.LINK_URI:
            uri = str(link.get("uri") or "").strip()
            if not uri.lower().startswith(_SAFE_SCHEMES) or len(uri) > 2_000:
                continue
            item["uri"] = uri
        else:
            continue
        out.append(item)
    return {"links": out}


def page_words(session: DocSession, page_id: str) -> list[tuple]:
    """Palabras de la pagina en el espacio sin girar (uso interno de otras operaciones)."""
    return session.page(page_id).get_text("words", flags=_TEXT_FLAGS)
