"""Apariencia visible de una firma: un PDF de una pagina del tamano del campo.

Se compone aqui (y no con las plantillas de pyHanko) para controlar el
resultado: firma manuscrita a la izquierda o arriba, y a su lado el bloque de
texto con el tamano de letra ajustado a la caja. El PDF resultante se entrega a
pyHanko, que lo incrusta como apariencia del campo de firma.

Los textos proceden del certificado y de lo que escribe el usuario, es decir,
de fuera: antes de dibujarlos se limpian de caracteres de control y de los que
alteran el orden de lectura, para que lo que se ve en la firma no pueda
aparentar otra cosa (A05:2025).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import pymupdf

from ..errors import EngineError
from ..fonts import DEFAULT_FAMILY, get_font, split_runs
from ..i18n import _
from ..sources import fix_text_maps, note_glyphs
from ..imaging import LoadedImage
from .certs import clean_text  # noqa: F401 - reexportado: varias operaciones lo importan de aqui

MIN_SIDE = 10.0  # una firma visible mas pequena que esto no es legible
_MIN_FONT, _MAX_FONT = 3.0, 11.0
_LINE_HEIGHT = 1.18
_GREY = (0.35, 0.35, 0.35)
_BLACK = (0.0, 0.0, 0.0)
_DO = re.compile(rb"/([A-Za-z0-9_.\-]+)\s+Do")


@dataclass(frozen=True)
class Line:
    text: str
    bold: bool = False
    scale: float = 1.0
    color: tuple[float, float, float] = _BLACK


@dataclass(frozen=True)
class Appearance:
    """Lo que se dibuja. ``width`` y ``height`` son las de la caja tal como se ve en pantalla."""

    width: float
    height: float
    rotation: int  # giro de la pagina de destino: 0, 90, 180 o 270
    lines: tuple[Line, ...] = ()
    image: LoadedImage | None = None
    border: bool = False


def text_lines(
    *,
    label: bool,
    name: str,
    identifier: str,
    date: str,
    reason: str,
    location: str,
) -> tuple[Line, ...]:
    """Bloque de texto de la firma; cada dato solo aparece si viene con contenido."""
    out: list[Line] = []
    if label:
        out.append(Line(_("Firmado digitalmente por"), scale=0.85, color=_GREY))
    if name:
        out.append(Line(clean_text(name, 120), bold=True, scale=1.1))
    if identifier:
        out.append(Line(clean_text(identifier, 60), scale=0.92))
    if date:
        out.append(Line(clean_text(_("Fecha: {date}", date=date), 60), scale=0.92))
    if reason:
        out.append(Line(clean_text(_("Motivo: {reason}", reason=reason), 220), scale=0.92))
    if location:
        out.append(Line(clean_text(_("Lugar: {location}", location=location), 220), scale=0.92))
    return tuple(line for line in out if line.text)


def _width(text: str, bold: bool, size: float) -> float:
    font = get_font(DEFAULT_FAMILY, bold, False)
    return sum(run_font.text_length(run, fontsize=size) for run_font, run in split_runs(text, font))


def _wrap(text: str, bold: bool, size: float, limit: float) -> list[str]:
    """Parte un texto en renglones que caben en ``limit``; una palabra demasiado larga se trocea."""
    rows: list[str] = []
    current = ""
    for word in text.split(" "):
        candidate = f"{current} {word}" if current else word
        if _width(candidate, bold, size) <= limit:
            current = candidate
            continue
        if current:
            rows.append(current)
            current = ""
        while word and _width(word, bold, size) > limit:
            cut = len(word)
            while cut > 1 and _width(word[:cut], bold, size) > limit:
                cut -= 1
            rows.append(word[:cut])
            word = word[cut:]
        current = word
    if current:
        rows.append(current)
    return rows


def _layout(lines: tuple[Line, ...], width: float, height: float, base: float) -> tuple[list[tuple[Line, str]], float]:
    rows: list[tuple[Line, str]] = []
    total = 0.0
    for line in lines:
        size = base * line.scale
        for row in _wrap(line.text, line.bold, size, width):
            rows.append((line, row))
            total += size * _LINE_HEIGHT
    return rows, total


def _fit(lines: tuple[Line, ...], width: float, height: float) -> tuple[list[tuple[Line, str]], float, float]:
    """Mayor tamano de letra con el que todo el bloque cabe en el hueco disponible."""
    low, high = _MIN_FONT, _MAX_FONT
    rows, total = _layout(lines, width, height, low)
    best = (rows, low, total)
    if total > height:
        return best  # ni con la letra minima cabe: se dibuja y lo que sobre queda recortado por la caja
    for _ in range(12):
        middle = (low + high) / 2
        rows, total = _layout(lines, width, height, middle)
        if total <= height:
            best, low = (rows, middle, total), middle
        else:
            high = middle
    return best


def _draw(page: pymupdf.Page, spec: Appearance, used: dict[str, dict[int, str]]) -> None:
    width, height = spec.width, spec.height
    pad = max(1.5, min(6.0, 0.06 * min(width, height)))
    inner = pymupdf.Rect(pad, pad, width - pad, height - pad)
    image_area: pymupdf.Rect | None = None
    text_area: pymupdf.Rect | None = None
    if spec.image is not None and spec.lines:
        if width >= 1.8 * height:  # caja apaisada: la firma manuscrita va a la izquierda
            split = inner.x0 + inner.width * 0.38
            image_area = pymupdf.Rect(inner.x0, inner.y0, split, inner.y1)
            text_area = pymupdf.Rect(split + pad, inner.y0, inner.x1, inner.y1)
        else:  # caja alta o cuadrada: arriba
            split = inner.y0 + inner.height * 0.45
            image_area = pymupdf.Rect(inner.x0, inner.y0, inner.x1, split)
            text_area = pymupdf.Rect(inner.x0, split + pad / 2, inner.x1, inner.y1)
    elif spec.image is not None:
        image_area = inner
    elif spec.lines:
        text_area = inner

    if spec.border:
        canvas = page.new_shape()
        canvas.draw_rect(pymupdf.Rect(0.25, 0.25, width - 0.25, height - 0.25))
        canvas.finish(color=_GREY, fill=None, width=0.5)
        canvas.commit()

    if image_area is not None and spec.image is not None and image_area.width > 1 and image_area.height > 1:
        # keep_proportion centra la imagen en el hueco sin deformarla.
        page.insert_image(image_area, stream=spec.image.data, keep_proportion=True)

    if text_area is not None and text_area.width > 2 and text_area.height > 2:
        rows, base, total = _fit(spec.lines, text_area.width, text_area.height)
        writers: dict[tuple[float, float, float], pymupdf.TextWriter] = {}
        y = text_area.y0 + max(0.0, (text_area.height - total) / 2)
        for line, row in rows:
            size = base * line.scale
            font = get_font(DEFAULT_FAMILY, line.bold, False)
            baseline = y + (size * _LINE_HEIGHT - (font.ascender - font.descender) * size) / 2 + font.ascender * size
            x = text_area.x0
            writer = writers.setdefault(line.color, pymupdf.TextWriter(page.rect, color=line.color))
            for run_font, run in split_runs(row, font):
                if run:
                    writer.append((x, baseline), run, font=run_font, fontsize=size)
                    x += run_font.text_length(run, fontsize=size)
                    note_glyphs(used, run_font, run)
            y += size * _LINE_HEIGHT
        for writer in writers.values():
            writer.write_text(page)


def build(spec: Appearance) -> bytes:
    """PDF de una pagina listo para usarse como apariencia de un campo de firma.

    La pagina mide lo que el rectangulo del campo en coordenadas del PDF y su
    contenido va girado en sentido contrario al de la pagina de destino, de
    modo que la firma se lee derecha en pantalla aunque la pagina este girada.
    """
    if spec.width < MIN_SIDE or spec.height < MIN_SIDE:
        raise EngineError("invalid_params", _("La zona de la firma es demasiado pequeña."))
    rotation = spec.rotation % 360
    if rotation not in (0, 90, 180, 270):
        rotation = 0
    width, height = spec.width, spec.height

    design = pymupdf.open()
    final = pymupdf.open()
    try:
        used: dict[str, dict[int, str]] = {}
        _draw(design.new_page(width=width, height=height), spec, used)
        try:
            design.subset_fonts()  # solo se incrustan los glifos usados
        except Exception:  # noqa: BLE001 - sin subconjunto el PDF es valido, solo algo mayor
            pass
        # El texto de la firma (nombre, fecha con guiones...) debe poder copiarse tal cual.
        fix_text_maps(design, used)

        box_w, box_h = (width, height) if rotation in (0, 180) else (height, width)
        # pyHanko trunca a enteros el tamano de la apariencia y luego el visor la
        # estira hasta el rectangulo del campo. Se le da ya una pagina de lados
        # enteros y se compensa aqui la escala: asi nada sale deformado.
        page_w, page_h = max(1, int(box_w)), max(1, int(box_h))
        page = final.new_page(width=page_w, height=page_h)
        page.show_pdf_page(pymupdf.Rect(0, 0, 8, 8), design, 0, keep_proportion=False)
        contents = page.get_contents()
        found = _DO.search(final.xref_stream(contents[0]) or b"") if len(contents) == 1 else None
        if not found:
            raise EngineError("internal", _("No se pudo preparar la apariencia de la firma."))
        name = found.group(1).decode("ascii")
        xobject = next((x[0] for x in final.get_page_xobjects(0) if x[1] == name), None)
        if xobject is None:
            raise EngineError("internal", _("No se pudo preparar la apariencia de la firma."))
        # La colocacion la fija solo la matriz de abajo, no la que eligio PyMuPDF.
        final.xref_set_key(xobject, "Matrix", "[1 0 0 1 0 0]")
        # Giro antihorario de ``rotation`` grados (ejes del PDF, Y hacia arriba) y
        # traslacion para volver a meter el dibujo en la caja.
        a, b, c, d, e, f = {
            0: (1, 0, 0, 1, 0.0, 0.0),
            90: (0, 1, -1, 0, height, 0.0),
            180: (-1, 0, 0, -1, width, height),
            270: (0, -1, 1, 0, 0.0, width),
        }[rotation]
        sx, sy = page_w / box_w, page_h / box_h
        matrix = " ".join(f"{value:.6f}" for value in (a * sx, b * sy, c * sx, d * sy, e * sx, f * sy))
        final.update_stream(contents[0], f"q {matrix} cm /{name} Do Q\n".encode("ascii"))
        return final.tobytes(garbage=3, deflate=True)
    finally:
        final.close()
        design.close()
