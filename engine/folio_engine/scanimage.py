"""Fotos y escaneos de documentos: recortar, enderezar y dejar el papel blanco y el texto nitido.

Todo con Pillow y dentro del motor, que no puede cargar NumPy ni OpenCV (ver
__main__.py): las esquinas del papel las busca un proceso aparte
(scanworker.py) y aqui se aplica lo que encuentra o lo que ajusta el usuario.
Asi una vista previa se rehace al momento al cambiar un ajuste.
"""

from __future__ import annotations

import io
import math
from pathlib import Path
from typing import Sequence

from PIL import Image, ImageChops, ImageEnhance, ImageFilter, ImageMath, ImageOps, UnidentifiedImageError

from .errors import EngineError
from .i18n import _
from .imaging import _FORMATS, A4, LETTER

STYLES = ("auto", "color", "gray", "bw", "original")
# Lado mayor de la imagen con la que se trabaja: 300 ppp en un A4 son 3508 pixeles.
WORK_SIDE = 4200
# Copia para buscar el papel (scanworker.py) y para ver y ajustar sus esquinas.
DETECT_SIDE = 1000
PREVIEW_SIDE = 1400
# Resolucion con la que van las paginas al PDF: suficiente para leer e imprimir
# sin que un documento de varias paginas pese demasiado.
DPI_COLOR = 200
DPI_BW = 300
MAX_PAGE_POINTS = 14_000  # limite de tamano de pagina del formato PDF
TRIM = 0.008  # lo que se recorta del contorno del papel ya enderezado

Point = Sequence[float]


def open_photo(path: Path) -> Image.Image:
    """La imagen en RGB, girada segun su EXIF y reducida al tamano de trabajo.

    El formato se comprueba por el contenido (no por la extension) y Pillow
    frena las imagenes que se descomprimen a un tamano desmesurado (el limite
    lo fija imaging.py, A05:2025).
    """
    try:
        with Image.open(path) as probe:
            if (probe.format or "") not in _FORMATS:
                raise EngineError("invalid_file", _("Formato de imagen no compatible."))
            probe.load()  # fuerza la descodificacion: detecta archivos truncados o maliciosos
            image = ImageOps.exif_transpose(probe)
    except EngineError:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError, SyntaxError) as exc:
        raise EngineError("invalid_file", _("No se pudo leer la imagen.")) from exc
    if image.width < 16 or image.height < 16:
        raise EngineError("invalid_file", _("La imagen es demasiado pequeña."))
    if image.mode in ("RGBA", "LA", "PA") or (image.mode == "P" and "transparency" in image.info):
        # Lo transparente, sobre blanco: es el color del papel.
        rgba = image.convert("RGBA")
        white = Image.new("RGB", rgba.size, (255, 255, 255))
        white.paste(rgba, mask=rgba.getchannel("A"))
        image = white
    elif image.mode != "RGB":
        image = image.convert("RGB")
    if max(image.size) > WORK_SIDE:
        image.thumbnail((WORK_SIDE, WORK_SIDE), Image.Resampling.LANCZOS)
    return image


def scaled(image: Image.Image, side: int) -> Image.Image:
    """Copia cuyo lado mayor no pasa de ``side``."""
    if max(image.size) <= side:
        return image.copy()
    copy = image.copy()
    copy.thumbnail((side, side), Image.Resampling.LANCZOS)
    return copy


# ------------------------------------------------------------- perspectiva


def _solve(rows: list[list[float]], rhs: list[float]) -> list[float]:
    """Sistema lineal pequeno (eliminacion de Gauss con pivote parcial)."""
    n = len(rhs)
    m = [row[:] + [value] for row, value in zip(rows, rhs)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < 1e-12:
            raise EngineError("invalid_params", _("Las esquinas no forman un cuadrilátero válido."))
        m[col], m[pivot] = m[pivot], m[col]
        for r in range(col + 1, n):
            f = m[r][col] / m[col][col]
            if f:
                for c in range(col, n + 1):
                    m[r][c] -= f * m[col][c]
    out = [0.0] * n
    for r in range(n - 1, -1, -1):
        out[r] = (m[r][n] - sum(m[r][c] * out[c] for c in range(r + 1, n))) / m[r][r]
    return out


def perspective_coefficients(quad: Sequence[Point], width: int, height: int) -> tuple[float, ...]:
    """Coeficientes de ``Image.PERSPECTIVE``: de cada pixel del resultado a su sitio en la foto."""
    rows: list[list[float]] = []
    rhs: list[float] = []
    for (x, y), (u, v) in zip(((0, 0), (width, 0), (width, height), (0, height)), quad):
        rows.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        rhs.append(u)
        rows.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        rhs.append(v)
    return tuple(_solve(rows, rhs))


def check_quad(quad: Sequence[Point]) -> None:
    """Cuatro esquinas dentro de la imagen que forman un cuadrilatero convexo y con superficie."""
    if len(quad) != 4 or any(len(p) != 2 for p in quad):
        raise EngineError("invalid_params", _("Las esquinas no forman un cuadrilátero válido."))
    for x, y in quad:
        if not (math.isfinite(x) and math.isfinite(y) and -0.001 <= x <= 1.001 and -0.001 <= y <= 1.001):
            raise EngineError("invalid_params", _("Las esquinas no forman un cuadrilátero válido."))
    crosses = []
    for i in range(4):
        (x0, y0), (x1, y1), (x2, y2) = quad[i], quad[(i + 1) % 4], quad[(i + 2) % 4]
        crosses.append((x1 - x0) * (y2 - y1) - (y1 - y0) * (x2 - x1))
    area = abs(sum(quad[i][0] * quad[(i + 1) % 4][1] - quad[(i + 1) % 4][0] * quad[i][1] for i in range(4))) / 2
    if not (all(c > 0 for c in crosses) or all(c < 0 for c in crosses)) or area < 0.01:
        raise EngineError("invalid_params", _("Las esquinas no forman un cuadrilátero válido."))


def _distance(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _cross(a: Sequence[float], b: Sequence[float]) -> tuple[float, float, float]:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def true_aspect(points: Sequence[Point], width: int, height: int) -> float | None:
    """Ancho entre alto del papel de verdad, visto en perspectiva en una foto de ``width`` x ``height``.

    Metodo de Zhang y He (2007, «Whiteboard scanning and image enhancement»):
    con el centro optico en el centro de la foto, las cuatro esquinas dan la
    distancia focal de la camara y, con ella, la proporcion real. Medir los
    lados no basta: la perspectiva acorta los que se alejan y la pagina
    saldria estirada. None solo si las esquinas estan alineadas.
    """
    tl, tr, br, bl = points
    m1, m2, m3, m4 = (tl[0], tl[1], 1.0), (tr[0], tr[1], 1.0), (bl[0], bl[1], 1.0), (br[0], br[1], 1.0)
    try:
        k2 = _dot(_cross(m1, m4), m3) / _dot(_cross(m2, m4), m3)
        k3 = _dot(_cross(m1, m4), m2) / _dot(_cross(m3, m4), m2)
    except ZeroDivisionError:
        return None
    n2 = [k2 * a - b for a, b in zip(m2, m1)]
    n3 = [k3 * a - b for a, b in zip(m3, m1)]
    u0, v0 = width / 2.0, height / 2.0
    side = float(max(width, height))
    f2 = None
    if abs(n2[2]) > 1e-9 and abs(n3[2]) > 1e-9:
        f2 = -(
            (n2[0] * n3[0] - (n2[0] * n3[2] + n2[2] * n3[0]) * u0 + n2[2] * n3[2] * u0 * u0)
            + (n2[1] * n3[1] - (n2[1] * n3[2] + n2[2] * n3[1]) * v0 + n2[2] * n3[2] * v0 * v0)
        ) / (n2[2] * n3[2])
        # Fuera de lo que tiene una camara de verdad: la cuenta se ha vuelto
        # inestable (casi sin perspectiva) o las esquinas no son de una hoja.
        if not (0.15 * side) ** 2 <= f2 <= (8.0 * side) ** 2:
            f2 = None
    if f2 is None:
        # Con dos lados paralelos en la foto (camara inclinada solo hacia
        # delante o solo de lado) las esquinas no dicen la focal: se supone la
        # de la camara de un movil. Sin perspectiva da igual cual sea.
        f2 = (0.85 * side) ** 2
    q2 = ((n2[0] - u0 * n2[2]) ** 2 + (n2[1] - v0 * n2[2]) ** 2) / f2 + n2[2] ** 2
    q3 = ((n3[0] - u0 * n3[2]) ** 2 + (n3[1] - v0 * n3[2]) ** 2) / f2 + n3[2] ** 2
    if q2 <= 0 or q3 <= 0:
        return None
    return math.sqrt(q2 / q3)


def straight_size(image_size: tuple[int, int], quad: Sequence[Point] | None) -> tuple[int, int]:
    """Tamano en pixeles del papel ya enderezado (sin girar), con su proporcion real."""
    if quad is None:
        return image_size
    w, h = image_size
    points = [(x * w, y * h) for x, y in quad]
    tl, tr, br, bl = points
    width = max(_distance(tl, tr), _distance(bl, br))
    height = max(_distance(tl, bl), _distance(tr, br))
    aspect = true_aspect(points, w, h)
    if aspect is not None:
        # La proporcion real, sin perder resolucion en ningun sentido.
        height = max(height, width / aspect)
        width = height * aspect
    return max(1, round(width)), max(1, round(height))


_TURNS = {90: Image.Transpose.ROTATE_270, 180: Image.Transpose.ROTATE_180, 270: Image.Transpose.ROTATE_90}


def straighten(image: Image.Image, quad: Sequence[Point] | None, rotate: int = 0, max_side: int | None = None) -> Image.Image:
    """El papel recortado y enderezado (o la imagen entera), girado ``rotate`` grados en el sentido de las agujas del reloj."""
    width, height = straight_size(image.size, quad)
    factor = min(1.0, max_side / max(width, height)) if max_side else 1.0
    if quad is None:
        out = image.copy() if factor >= 1.0 else image.resize((max(1, round(width * factor)), max(1, round(height * factor))), Image.Resampling.LANCZOS)
    else:
        # Si el resultado va a ser mucho mas pequeno que la foto, primero se
        # reduce la foto: la transformacion toma un pixel de cada tantos y, sin
        # reducir antes, el texto saldria roto.
        step = int(1 / factor) if factor < 0.5 else 1
        source = image.reduce(step) if step > 1 else image
        sx, sy = source.width, source.height
        points = [(x * sx, y * sy) for x, y in quad]
        out_w, out_h = max(1, round(width * factor)), max(1, round(height * factor))
        # Fuera un pelo del contorno (menos de 2 mm en un A4): en el borde queda
        # siempre un hilo de la mesa que, en blanco y negro, seria una raya
        # negra. Se dibuja la hoja con ese margen de mas y se recorta, asi el
        # resultado sigue midiendo lo pedido.
        trim = max(1, round(TRIM * max(out_w, out_h)))
        big_w, big_h = out_w + 2 * trim, out_h + 2 * trim
        out = source.transform(
            (big_w, big_h),
            Image.Transform.PERSPECTIVE,
            perspective_coefficients(points, big_w, big_h),
            resample=Image.Resampling.BICUBIC,
            fillcolor=(255, 255, 255),
        ).crop((trim, trim, trim + out_w, trim + out_h))
    if rotate in _TURNS:
        out = out.transpose(_TURNS[rotate])
    return out


# ----------------------------------------------------------------- mejora


def _background(band: Image.Image) -> Image.Image:
    """Como se veria el papel sin nada escrito: la luz y las sombras de la foto."""
    step = max(1, max(band.size) // 320)
    small = band.reduce(step) if step > 1 else band.copy()
    # El maximo local borra el texto (trazos finos y oscuros); la mediana y el
    # desenfoque dejan solo la variacion lenta de la luz.
    small = small.filter(ImageFilter.MaxFilter(7)).filter(ImageFilter.MedianFilter(9)).filter(ImageFilter.GaussianBlur(3))
    return small.resize(band.size, Image.Resampling.BILINEAR).point(lambda v: max(v, 1))


def flatten(image: Image.Image) -> Image.Image:
    """Sin sombras ni luz desigual: cada canal entre su fondo, y el papel queda blanco y neutro."""
    bands = [
        ImageMath.lambda_eval(lambda a: a["convert"](a["float"](a["band"]) * 255 / a["background"], "L"), band=band, background=_background(band))
        for band in image.split()
    ]
    return bands[0] if len(bands) == 1 else Image.merge(image.mode, bands)


def colorful(image: Image.Image) -> bool:
    """Si hay color de verdad (un sello, una firma en azul, una foto) o todo es gris."""
    hsv = scaled(image, 480).convert("HSV")
    _, saturation, value = hsv.split()
    vivid = ImageChops.multiply(saturation.point(lambda v: 255 if v > 60 else 0), value.point(lambda v: 255 if v > 60 else 0))
    # Con la luz ya igualada, un documento en gris no tiene casi nada saturado;
    # un sello o una firma en azul, unas decimas de su superficie.
    return vivid.histogram()[255] / float(vivid.width * vivid.height) > 0.0008


def _percentile(hist: list[int], fraction: float) -> int:
    total = sum(hist)
    limit = total * fraction
    seen = 0
    for value, count in enumerate(hist):
        seen += count
        if seen >= limit:
            return value
    return 255


def _levels(image: Image.Image, low: int, high: int, gamma: float = 1.0) -> Image.Image:
    span = max(1, high - low)
    lut = [round(255 * min(1.0, max(0.0, (v - low) / span)) ** gamma) for v in range(256)]
    return image.point(lut * len(image.getbands()))


def _otsu(hist: list[int]) -> int:
    total = sum(hist)
    weighted = sum(i * h for i, h in enumerate(hist))
    best, threshold, below, below_sum = -1.0, 128, 0, 0.0
    for t in range(256):
        below += hist[t]
        if below == 0:
            continue
        above = total - below
        if above == 0:
            break
        below_sum += t * hist[t]
        mean_below = below_sum / below
        mean_above = (weighted - below_sum) / above
        spread = below * above * (mean_below - mean_above) ** 2
        if spread > best:
            best, threshold = spread, t
    return threshold


def enhance(image: Image.Image, style: str) -> Image.Image:
    """La pagina como de escaner: papel blanco, texto oscuro y nitido.

    ``auto`` deja el color solo si lo hay de verdad; ``bw`` da el archivo mas
    ligero (texto puro); ``original`` no toca nada.
    """
    if style == "original":
        return image
    flat = flatten(image)
    if style == "auto":
        style = "color" if colorful(flat) else "gray"
    gray = flat.convert("L")
    # Lo mas oscuro pasa a negro y el papel (ya casi blanco) a blanco del todo.
    low = min(110, _percentile(gray.histogram(), 0.01))
    if style == "bw":
        leveled = _levels(gray, low, 228)
        threshold = _otsu(leveled.histogram())
        return leveled.point([255 if v > threshold else 0 for v in range(256)], "1")
    if style == "gray":
        out = _levels(gray, low, 228, 1.25)
    else:
        out = ImageEnhance.Color(_levels(flat, low, 228, 1.25)).enhance(1.15)
    return out.filter(ImageFilter.UnsharpMask(radius=1.0, percent=60, threshold=2))


# ------------------------------------------------------------ en el PDF


def page_size(width: int, height: int, dpi: float = 0.0) -> tuple[float, float]:
    """Tamano de la pagina en puntos.

    Con la resolucion del escaner, el tamano real. En una foto no se sabe:
    si tiene la forma de un A4 o de una carta, ese papel; si no (un tique, una
    tarjeta), lo mas grande que cabe en un A4 sin deformarla.
    """
    if dpi > 0:
        w, h = width * 72.0 / dpi, height * 72.0 / dpi
        s = min(1.0, MAX_PAGE_POINTS / max(w, h))
        return (w * s, h * s)
    portrait = height >= width
    ratio = max(width, height) / min(width, height)
    for paper in (A4, LETTER):
        target = paper[1] / paper[0]
        if abs(ratio - target) / target <= 0.06:
            return paper if portrait else (paper[1], paper[0])
    box = A4 if portrait else (A4[1], A4[0])
    s = min(box[0] / width, box[1] / height)
    return (width * s, height * s)


def pixels_for(page: tuple[float, float], style: str) -> int:
    """Lado mayor en pixeles con que la pagina va al PDF."""
    dpi = DPI_BW if style == "bw" else DPI_COLOR
    return max(16, round(max(page) * dpi / 72.0))


def encode(image: Image.Image) -> bytes:
    """Blanco y negro sin perdida (PNG de un bit, muy ligero); lo demas en JPEG."""
    buf = io.BytesIO()
    if image.mode == "1":
        image.save(buf, "PNG", optimize=True)
    else:
        image.save(buf, "JPEG", quality=80, optimize=True)
    return buf.getvalue()
