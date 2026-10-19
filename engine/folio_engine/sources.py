"""Construccion del contenido de los elementos anadidos (texto, imagen, forma).

Cada funcion devuelve un PDF temporal de una sola pagina, sin giro ni recorte,
que despues se incorpora a la pagina de destino como XObject (ver content.py).
Dibujar aqui y no directamente en la pagina de destino evita los errores de
colocacion de PyMuPDF en paginas giradas o con CropBox desplazado.
"""

from __future__ import annotations

from typing import Any

import pymupdf

from .errors import invalid
from .fonts import get_font, split_runs
from .i18n import _
from .imaging import LoadedImage

MAX_TEXT_LINES = 400


def text_metrics(style: dict[str, Any]) -> dict[str, float]:
    """Medidas con las que se compone el texto; la interfaz usa las mismas para el editor."""
    size = float(style["size"])
    font = get_font(style["font"], bool(style.get("bold")), bool(style.get("italic")))
    ascender, descender = float(font.ascender), float(font.descender)
    line_h = size * float(style.get("lineHeight", 1.2))
    content = (ascender - descender) * size
    return {
        "padX": round(0.18 * size, 3),
        # Margen vertical: evita que la caja del XObject recorte ascendentes y
        # descendentes cuando el interlineado es menor que la altura de la fuente.
        "padY": round(max(0.0, (content - line_h) / 2) + 0.06 * size, 3),
        "lineH": round(line_h, 3),
        "ascent": round(ascender * size, 3),
        "content": round(content, 3),
    }


def _measure(text: str, font: pymupdf.Font, size: float) -> float:
    """Ancho de un texto (las letras que falten en la fuente se miden con la de reserva)."""
    return sum(f.text_length(t, fontsize=size) for f, t in split_runs(text, font))


def wrap_paragraph(text: str, font: pymupdf.Font, size: float, inner: float) -> list[str]:
    """Reparte un parrafo en lineas que caben en ``inner`` puntos.

    Se corta en los espacios; una palabra mas larga que la linea entera se
    parte donde haga falta. Sin interletraje, los anchos se suman: medir
    palabra a palabra da lo mismo que medir la linea completa.
    """
    space = _measure(" ", font, size)
    lines: list[str] = []
    current: list[str] = []
    width = 0.0
    for word in text.split(" "):
        measured = _measure(word, font, size)
        if current and width + space + measured > inner + 1e-6:
            lines.append(" ".join(current))
            current, width = [], 0.0
        if not current and measured > inner + 1e-6:
            chunk, chunk_width = "", 0.0
            for ch in word:
                step = _measure(ch, font, size)
                if chunk and chunk_width + step > inner + 1e-6:
                    lines.append(chunk)
                    chunk, chunk_width = "", 0.0
                chunk += ch
                chunk_width += step
            current, width = [chunk], chunk_width
            continue
        width = width + space + measured if current else measured
        current.append(word)
        if len(lines) > MAX_TEXT_LINES:
            break
    lines.append(" ".join(current))
    return lines


def text_source(text: str, style: dict[str, Any]) -> tuple[pymupdf.Document, tuple[float, float], dict[str, float]]:
    """Pagina con el texto compuesto. Sin ``width``, linea a linea (los saltos son los que
    escribio el usuario); con ``width``, cada parrafo se reparte en lineas de ese ancho."""
    doc, sizes, metrics = text_sources([(text, str(style.get("align", "left")))], style)
    return doc, sizes[0], metrics


def text_sources(
    items: list[tuple[str, str]], style: dict[str, Any]
) -> tuple[pymupdf.Document, list[tuple[float, float]], dict[str, float]]:
    """Varios textos con el mismo estilo en un solo documento: una pagina por ``(texto, alineacion)``.

    Sirve para lo que cambia de una pagina a otra (la numeracion): todas las
    paginas comparten una unica fuente incrustada con los glifos de todos los
    textos, en vez de llevar cada elemento la suya.
    """
    doc, sizes, _metrics = compose_texts([(text, {**style, "align": align}) for text, align in items])
    return doc, sizes, text_metrics(style)


def compose_texts(
    items: list[tuple[str, dict[str, Any]]],
) -> tuple[pymupdf.Document, list[tuple[float, float]], list[dict[str, float]]]:
    """Compone varios textos, cada uno con su estilo, en un solo documento: una pagina por texto.

    Las paginas que usan el mismo tipo de letra comparten una unica fuente
    incrustada, reducida a los glifos de todos los textos juntos.
    """
    doc = pymupdf.open()
    sizes: list[tuple[float, float]] = []
    all_metrics: list[dict[str, float]] = []
    used: dict[str, dict[int, str]] = {}  # fuente -> glifo -> caracter escrito
    wrote_any = False
    try:
        for text, style in items:
            size = float(style["size"])
            font = get_font(style["font"], bool(style.get("bold")), bool(style.get("italic")))
            metrics = text_metrics(style)
            align = style.get("align", "left")
            half_leading = (metrics["lineH"] - metrics["content"]) / 2
            paragraphs = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
            if len(paragraphs) > MAX_TEXT_LINES:
                raise invalid(_("El texto tiene demasiadas líneas."))
            column = float(style.get("width") or 0)
            if column > 0:
                # Ancho fijo: cada parrafo se reparte solo en lineas de ese ancho.
                inner = max(column, size * 0.3)
                lines, ends = [], []
                for paragraph in paragraphs:
                    pieces = wrap_paragraph(paragraph, font, size, inner)
                    lines += pieces
                    ends += [False] * (len(pieces) - 1) + [True]
                    if len(lines) > MAX_TEXT_LINES:
                        raise invalid(_("El texto tiene demasiadas líneas."))
            else:
                lines, ends = paragraphs, [True] * len(paragraphs)
            runs_per_line = [split_runs(line, font) for line in lines]
            widths = [sum(f.text_length(t, fontsize=size) for f, t in runs) for runs in runs_per_line]
            if column <= 0:
                inner = max(max(widths, default=0.0), size * 0.3)
            width = inner + 2 * metrics["padX"]
            height = len(lines) * metrics["lineH"] + 2 * metrics["padY"]
            page = doc.new_page(width=width, height=height)
            writer = pymupdf.TextWriter(page.rect, color=tuple(style.get("color", (0, 0, 0))))
            wrote = False
            for index, runs in enumerate(runs_per_line):
                baseline = metrics["padY"] + index * metrics["lineH"] + half_leading + metrics["ascent"]
                gaps = lines[index].count(" ")
                if align == "justify" and column > 0 and not ends[index] and gaps and widths[index] < inner:
                    # Justificado: el espacio que sobra se reparte entre las
                    # palabras (la ultima linea de cada parrafo, a la izquierda).
                    # Los espacios se escriben igual, para que copiar el texto
                    # y buscar en el sigan funcionando.
                    extra = (inner - widths[index]) / gaps
                    x = metrics["padX"]
                    words = lines[index].split(" ")
                    for number, word in enumerate(words):
                        piece = word + (" " if number < len(words) - 1 else "")
                        for run_font, run_text in split_runs(piece, font):
                            if run_text:
                                writer.append((x, baseline), run_text, font=run_font, fontsize=size)
                                x += run_font.text_length(run_text, fontsize=size)
                                wrote = wrote or bool(run_text.strip())
                                note_glyphs(used, run_font, run_text)
                        x += extra if number < len(words) - 1 else 0.0
                    continue
                if align == "center":
                    x = metrics["padX"] + (inner - widths[index]) / 2
                elif align == "right":
                    x = metrics["padX"] + inner - widths[index]
                else:
                    x = metrics["padX"]
                for run_font, run_text in runs:
                    if run_text:
                        writer.append((x, baseline), run_text, font=run_font, fontsize=size)
                        x += run_font.text_length(run_text, fontsize=size)
                        wrote = wrote or bool(run_text.strip())
                        note_glyphs(used, run_font, run_text)
            if wrote:
                writer.write_text(page)
                wrote_any = True
            sizes.append((width, height))
            all_metrics.append(metrics)
        if wrote_any:
            try:
                doc.subset_fonts()  # solo se incrustan los glifos usados
            except Exception:  # noqa: BLE001 - sin subconjunto el PDF es valido, solo algo mayor
                pass
            fix_text_maps(doc, used)
    except BaseException:
        doc.close()
        raise
    return doc, sizes, all_metrics

def _unicode_map(glyphs: dict[int, str]) -> bytes:
    """Tabla ToUnicode (glifo -> caracter) con solo los glifos escritos."""
    lines = [
        "/CIDInit /ProcSet findresource begin",
        "12 dict begin",
        "begincmap",
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
        "/CMapName /Adobe-Identity-UCS def",
        "/CMapType 2 def",
        "1 begincodespacerange",
        "<0000> <FFFF>",
        "endcodespacerange",
    ]
    items = sorted(glyphs.items())
    for start in range(0, len(items), 100):  # el formato admite 100 entradas por bloque
        chunk = items[start : start + 100]
        lines.append(f"{len(chunk)} beginbfchar")
        lines += [f"<{glyph:04X}> <{char.encode('utf-16-be').hex().upper()}>" for glyph, char in chunk]
        lines.append("endbfchar")
    lines += ["endcmap", "CMapName currentdict /CMap defineresource pop", "end", "end"]
    return ("\n".join(lines) + "\n").encode("ascii")


def note_glyphs(used: dict[str, dict[int, str]], font: pymupdf.Font, text: str) -> None:
    """Apunta con que glifo de ``font`` se escribe cada caracter de ``text`` (para ``fix_text_maps``)."""
    glyphs = used.setdefault(font.name, {})
    for ch in text:
        glyph = font.has_glyph(ord(ch))
        if glyph:
            glyphs.setdefault(glyph, ch)


def fix_text_maps(doc: pymupdf.Document, used: dict[str, dict[int, str]]) -> None:
    """Hace que el texto se pueda buscar y copiar tal como se escribio.

    Cuando varios caracteres comparten glifo (el guion con el guion opcional, el
    espacio con el espacio de no separacion...), MuPDF anota en la tabla
    ToUnicode el de codigo mas alto: un «-» escrito se leeria despues como
    U+00AD y una busqueda de «2026-10» no lo encontraria. Aqui se sabe que
    caracter se escribio con cada glifo, asi que la tabla se rehace con eso.
    """
    try:
        seen: set[int] = set()
        fonts = [item for number in range(doc.page_count) for item in doc.get_page_fonts(number)]
        for item in fonts:
            xref, kind, base = int(item[0]), item[2], str(item[3])
            glyphs = used.get(base.split("+", 1)[-1])
            if kind != "Type0" or not glyphs or xref in seen:
                continue
            seen.add(xref)
            where, value = doc.xref_get_key(xref, "ToUnicode")
            if where == "xref":
                doc.update_stream(int(value.split()[0]), _unicode_map(glyphs))
            _neutral_subset_tag(doc, xref)
    except Exception:  # noqa: BLE001 - sin el arreglo el texto se ve igual; solo afecta a buscar y copiar
        pass


# Comienzos de nombre con los que MuPDF toma una TrueType por china de DynaLab
# y la dibuja con su ajuste a la rejilla (ver _neutral_subset_tag).
_TRIGGER_TAGS = ("DF", "DLC")


def _pdf_name(text: str) -> str:
    """Nombre PDF con sus caracteres especiales escapados (un espacio es ``#20``)."""
    out = ["/"]
    for byte in text.encode("utf-8"):
        out.append(chr(byte) if 0x21 <= byte <= 0x7E and chr(byte) not in "()<>[]{}/%#" else f"#{byte:02X}")
    return "".join(out)


def _retag(doc: pymupdf.Document, xref: int, key: str, old: str, new: str) -> None:
    kind, value = doc.xref_get_key(xref, key)
    if kind == "name" and value.startswith("/" + old + "+"):
        doc.xref_set_key(xref, key, _pdf_name(new + value[1 + len(old) :]))


def _neutral_subset_tag(doc: pymupdf.Document, font: int) -> None:
    """Evita que la etiqueta aleatoria de la fuente incrustada cambie como se dibuja.

    Al reducir una fuente a los glifos usados, MuPDF le antepone seis letras al
    azar («DFLSXQ+Arial»). Si esas letras empiezan por ``DF`` o ``DLC``, el
    propio MuPDF toma la fuente por una de DynaLab y la dibuja de otra manera:
    una de cada varios cientos de veces, un texto anadido se veria en Folio
    ligeramente distinto de los demas. Se cambia la primera letra y listo.
    """
    kind, value = doc.xref_get_key(font, "BaseFont")
    if kind != "name" or "+" not in value:
        return
    old = value[1:].split("+", 1)[0]
    if not old.startswith(_TRIGGER_TAGS):
        return
    new = "X" + old[1:]
    _retag(doc, font, "BaseFont", old, new)
    kind, value = doc.xref_get_key(font, "DescendantFonts")
    refs = value.strip("[] ").split() if kind in ("array", "xref") else []
    if not refs:
        return
    child = int(refs[0])
    _retag(doc, child, "BaseFont", old, new)
    kind, value = doc.xref_get_key(child, "FontDescriptor")
    if kind == "xref":
        _retag(doc, int(value.split()[0]), "FontName", old, new)


def image_source(image: LoadedImage) -> tuple[pymupdf.Document, tuple[float, float]]:
    doc = pymupdf.open()
    page = doc.new_page(width=image.width, height=image.height)
    page.insert_image(page.rect, stream=image.data, keep_proportion=False)
    return doc, (float(image.width), float(image.height))


def shape_source(
    shape: str,
    size: tuple[float, float],
    *,
    stroke: tuple[float, float, float] | None,
    fill: tuple[float, float, float] | None,
    width: float,
) -> tuple[pymupdf.Document, tuple[float, float]]:
    w, h = max(size[0], 0.5), max(size[1], 0.5)
    doc = pymupdf.open()
    page = doc.new_page(width=w, height=h)
    canvas = page.new_shape()
    inset = width / 2 if stroke is not None else 0.0
    box = pymupdf.Rect(inset, inset, w - inset, h - inset)
    if box.is_empty or box.width <= 0 or box.height <= 0:
        box = page.rect
    line_cap = 1  # extremos redondeados
    if shape == "rect":
        canvas.draw_rect(box)
        canvas.finish(color=stroke, fill=fill, width=width)
    elif shape == "ellipse":
        canvas.draw_oval(box)
        canvas.finish(color=stroke, fill=fill, width=width)
    elif shape == "dot":
        canvas.draw_oval(page.rect)
        canvas.finish(color=None, fill=fill or stroke or (0, 0, 0), width=0)
    elif shape == "line":
        canvas.draw_line((inset, h / 2), (w - inset, h / 2))
        canvas.finish(color=stroke or (0, 0, 0), width=width, lineCap=line_cap, closePath=False)
    elif shape == "check":
        pen = width if width > 0 else max(1.0, 0.12 * min(w, h))
        canvas.draw_polyline([(0.12 * w, 0.55 * h), (0.40 * w, 0.82 * h), (0.90 * w, 0.18 * h)])
        canvas.finish(color=stroke or (0, 0, 0), width=pen, lineCap=line_cap, lineJoin=1, closePath=False)
    elif shape == "cross":
        pen = width if width > 0 else max(1.0, 0.12 * min(w, h))
        canvas.draw_line((0.15 * w, 0.15 * h), (0.85 * w, 0.85 * h))
        canvas.draw_line((0.85 * w, 0.15 * h), (0.15 * w, 0.85 * h))
        canvas.finish(color=stroke or (0, 0, 0), width=pen, lineCap=line_cap, closePath=False)
    else:
        doc.close()
        raise invalid(_("Forma desconocida."))
    canvas.commit()
    return doc, (w, h)
