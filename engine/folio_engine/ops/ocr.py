"""Reconocimiento optico de caracteres (OCR): texto invisible sobre paginas escaneadas.

PyMuPDF trae Tesseract integrado; solo hacen falta los datos de idioma, que
``scripts/fetch-tessdata.ps1`` deja en ``resources/tessdata``. Cada pagina se
dibuja tal como se ve, se reconoce su texto y este se anade como un elemento
etiquetado de tipo ``ocr``: una capa de texto invisible del tamano de la pagina.
La imagen original no se toca ni se recomprime, y la capa se puede quitar
despues con ``tools.removeAdded``.
"""

from __future__ import annotations

import ctypes
import logging
import math
import os
import unicodedata
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Any, Iterator, Literal, NamedTuple

import pymupdf
from PIL import Image
from pydantic import Field

from .. import content
from ..errors import unsupported
from ..fonts import split_runs
from ..geometry import Geom
from ..i18n import _
from ..models import DocParams, Params
from ..server import Ctx, method
from .render import render_pixmap
from .stamps import PageList, resolve_pages

log = logging.getLogger("folio.engine.ocr")

# Idiomas admitidos. Lista blanca (OWASP A05:2025): el codigo forma el nombre
# del archivo que abre Tesseract (``<codigo>.traineddata``); sin texto libre no
# hay forma de salir de la carpeta de datos (A01:2025).
LANGUAGES = ("spa", "eng", "cat", "glg", "eus", "por", "fra", "deu", "ita")
Language = Literal["spa", "eng", "cat", "glg", "eus", "por", "fra", "deu", "ita"]

_TEXT_FLAGS = pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_MEDIABOX_CLIP

# Una pagina con mas caracteres de texto real que estos se considera "ya con
# texto" y se salta. El umbral deja pasar las paginas escaneadas que solo
# llevan un sello, una numeracion o una nota breve.
_SKIP_CHARS = 50
# Tipos de elemento anadido cuyo texto no cuenta como texto propio de la pagina.
_STAMP_KINDS = ("text", "watermark", "header", "footer", "pagenum")

# Topes por pagina: acotan memoria y tiempo ante paginas enormes o imagenes
# con ruido que Tesseract convierte en miles de "palabras".
_MAX_PIXELS = 32_000_000
_MAX_SIDE = 30_000
_MIN_SIDE = 16
_MAX_WORDS = 20_000
_MAX_WORD_CHARS = 120
_MAX_INDEXED = 50_000
_MAX_DATADIR = 900  # MuPDF guarda la ruta de los datos en un bufer de 1024 bytes
_CELL = 32.0


class _Word(NamedTuple):
    x0: float
    y0: float
    x1: float
    y1: float
    text: str


# ------------------------------------------------------------ datos de idioma


def _tessdata(ctx: Ctx) -> Path | None:
    """Carpeta de datos de idioma, o ``None`` si no existe.

    Sale de la configuracion del motor (``--resources``), nunca de la peticion:
    la interfaz no puede hacer que Tesseract cargue datos de otra carpeta.
    """
    resources = ctx.engine.resources
    if resources is None:
        return None
    folder = Path(resources) / "tessdata"
    try:
        return folder if folder.is_dir() else None
    except OSError:
        return None


def _installed(folder: Path | None) -> list[str]:
    if folder is None:
        return []
    found = []
    for code in LANGUAGES:
        try:
            if (folder / f"{code}.traineddata").is_file():
                found.append(code)
        except OSError:
            continue
    return found


def _short_path(path: str) -> str | None:
    """Nombre corto (8.3) de una ruta de Windows, si el volumen los tiene."""
    try:
        # Instancia propia de la DLL: no se alteran los prototipos de ctypes que usen otros modulos.
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        get_short = kernel32.GetShortPathNameW
        get_short.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
        get_short.restype = ctypes.c_uint
        buffer = ctypes.create_unicode_buffer(1024)
        length = get_short(path, buffer, len(buffer))
        if 0 < length < len(buffer):
            return buffer.value
    except (OSError, AttributeError, ValueError):
        log.debug("GetShortPathNameW no disponible")
    return None


def _usable_datadir(folder: Path) -> str | None:
    """Ruta de los datos tal como puede abrirla Tesseract, o ``None`` si no hay ninguna.

    El Tesseract integrado abre los archivos con ``fopen``, que en Windows
    interpreta la ruta en la pagina de codigos del sistema: una carpeta con
    tildes o enes (el nombre de usuario, por ejemplo) no se encuentra. Si la
    ruta no es ASCII se prueba su nombre corto, que si lo es.
    """
    text = str(folder)
    if text.isascii() and len(text) <= _MAX_DATADIR:
        return text
    short = _short_path(text)
    if short and short.isascii() and len(short) <= _MAX_DATADIR:
        return short
    return None


@contextmanager
def _inside(folder: Path | None) -> Iterator[None]:
    """Ejecuta el bloque con ``folder`` como directorio actual (``None`` = sin cambios).

    Ultimo recurso para rutas no ASCII sin nombre corto: desde dentro de la
    carpeta, Tesseract recibe "." y no depende de como se escriba la ruta. El
    directorio actual es global al proceso: el cambio dura solo el
    reconocimiento (el motor ejecuta una operacion cada vez) y se restaura
    siempre. La carpeta es la de recursos de la aplicacion, no una del usuario.
    """
    if folder is None:
        yield
        return
    try:
        previous: str | None = os.getcwd()
    except OSError:
        previous = None
    os.chdir(folder)
    try:
        yield
    finally:
        if previous is not None:
            try:
                os.chdir(previous)
            except OSError:
                log.warning("no se pudo restaurar el directorio de trabajo")


def _require_data(ctx: Ctx, languages: list[str]) -> Path:
    folder = _tessdata(ctx)
    installed = _installed(folder)
    missing = [code for code in languages if code not in installed]
    if folder is None or missing:
        raise unsupported(
            _("Faltan los datos de idioma del OCR. Vuelve a instalar la aplicación para recuperarlos."),
            missing=missing,
        )
    return folder


@method("tools.ocrStatus")
def tools_ocr_status(ctx: Ctx, p: Params) -> dict[str, Any]:
    """Indica si se puede reconocer texto y en que idiomas."""
    languages = _installed(_tessdata(ctx))
    return {"available": bool(languages), "languages": languages}


# ------------------------------------------------------------- reconocimiento


def _render_scale(size: tuple[float, float], dpi: int) -> float | None:
    """Escala de dibujo para ``dpi`` puntos por pulgada, reducida si la pagina es enorme."""
    width, height = size
    if not (width > 0 and height > 0):
        return None
    scale = min(dpi / 72, math.sqrt(_MAX_PIXELS / (width * height)), _MAX_SIDE / max(width, height))
    if min(width, height) * scale < _MIN_SIDE:
        return None
    return scale


def _clean_word(text: str) -> str:
    """Texto de una palabra reconocida, sin caracteres de control ni sustitutos sueltos.

    Lo reconocido sale del contenido del documento, es decir, es un dato no
    fiable: se limpia y se acota antes de escribirlo en el PDF.
    """
    kept = "".join(ch for ch in text if not ch.isspace() and unicodedata.category(ch)[0] != "C")
    return kept[:_MAX_WORD_CHARS]


def _recognize(
    pix: pymupdf.Pixmap, scale: float, language: str, folder: Path, datadir: str | None, page: tuple[float, float]
) -> list[_Word]:
    """Palabras reconocidas en la imagen, con su caja en el espacio de visualizacion."""
    resolution = max(1, round(scale * 72))
    pix.set_dpi(resolution, resolution)  # Tesseract ajusta su analisis a la resolucion real
    try:
        # ``tessdata`` y ``language`` van siempre con valor: con ``tessdata``
        # vacio PyMuPDF buscaria Tesseract ejecutando ``tesseract --list-langs``
        # con shell=True y haria caso de TESSDATA_PREFIX, con lo que cargaria
        # datos (o lanzaria un programa) ajenos a la aplicacion (OWASP A03:2025
        # y A05:2025).
        with _inside(folder if datadir is None else None):
            data = pix.pdfocr_tobytes(compress=True, language=language, tessdata=datadir or ".")
    except MemoryError:
        raise
    except Exception as exc:  # noqa: BLE001 - MuPDF lanza tipos propios
        # Al log va solo el tipo del fallo; al usuario, un mensaje generico (A09:2025 y A10:2025).
        log.error("el reconocimiento de texto ha fallado (%s)", type(exc).__name__)
        raise unsupported(_("No se pudo iniciar el reconocimiento de texto (OCR).")) from exc

    result = pymupdf.open(stream=data, filetype="pdf")
    try:
        ocr_page = result[0]
        rect = ocr_page.rect
        if rect.width <= 0 or rect.height <= 0:
            return []
        # Puntos de la pagina reconocida -> pixeles -> puntos de visualizacion.
        kx = pix.width / rect.width / scale
        ky = pix.height / rect.height / scale
        raw = ocr_page.get_text("words")
    finally:
        result.close()

    words: list[_Word] = []
    for item in raw[:_MAX_WORDS]:
        text = _clean_word(str(item[4]))
        x0 = max(0.0, item[0] * kx + pix.x / scale)
        y0 = max(0.0, item[1] * ky + pix.y / scale)
        x1 = min(page[0], item[2] * kx + pix.x / scale)
        y1 = min(page[1], item[3] * ky + pix.y / scale)
        if text and all(map(math.isfinite, (x0, y0, x1, y1))) and x1 - x0 >= 0.2 and y1 - y0 >= 0.5:
            words.append(_Word(x0, y0, x1, y1, text))
    return words


def _own_text_chars(doc: pymupdf.Document, page: pymupdf.Page) -> int:
    """Caracteres de texto real de la pagina, sin contar lo que anadio Folio encima.

    Una numeracion o un encabezado puestos sobre una pagina escaneada no la
    convierten en una pagina "con texto". La capa de un OCR anterior si cuenta.
    """
    total = sum(1 for ch in page.get_text("text", flags=_TEXT_FLAGS) if not ch.isspace())
    if total <= _SKIP_CHARS:
        return total
    stamped = 0
    for placed in content.list_objects(doc, page):
        if placed.kind not in _STAMP_KINDS:
            continue
        # Las propiedades vienen de la etiqueta guardada en el PDF: son datos no
        # fiables y se leen con cuidado. Mentir aqui solo consigue que se
        # reconozca una pagina de mas, y el filtro de duplicados la cubre.
        text, copies = placed.props.get("text"), placed.props.get("copies", 1)
        if isinstance(text, str):
            if isinstance(copies, bool) or not isinstance(copies, int) or not 1 <= copies <= 10_000:
                copies = 1
            stamped += copies * sum(1 for ch in text if not ch.isspace())
    return max(0, total - stamped)


# Una letra de texto real: su caja en D y el caracter.
_Glyph = tuple[float, float, float, float, str]


def _text_index(page: pymupdf.Page, geom: Geom) -> dict[tuple[int, int], list[_Glyph]]:
    """Letras del texto real que se lee horizontal en pantalla, en una rejilla para buscarlas rapido."""
    cells: dict[tuple[int, int], list[_Glyph]] = {}
    count = 0
    for block in page.get_text("rawdict", flags=_TEXT_FLAGS).get("blocks", []):
        if block.get("type", 0) != 0:
            continue
        for line in block.get("lines", []):
            # El texto inclinado o tumbado (una marca de agua en diagonal) no
            # cuenta: Tesseract no lo lee, asi que no puede haberlo duplicado, y
            # sus cajas taparian el texto escaneado sobre el que pasa.
            if geom.vector_d(line.get("dir", (1.0, 0.0)))[0] < 0.96:
                continue
            for span in line.get("spans", []):
                for char in span.get("chars", []):
                    x0, y0, x1, y1 = geom.rect_d(char["bbox"])
                    columns = range(int(x0 // _CELL), int(x1 // _CELL) + 1)
                    rows = range(int(y0 // _CELL), int(y1 // _CELL) + 1)
                    if len(columns) * len(rows) > 64:
                        continue  # letra gigante: no es texto corrido
                    glyph = (x0, y0, x1, y1, str(char.get("c", "")))
                    for column in columns:
                        for row in rows:
                            cells.setdefault((column, row), []).append(glyph)
                    count += 1
                    if count >= _MAX_INDEXED:
                        return cells
    return cells


def _letters(text: str) -> Counter[str]:
    """Letras y cifras de un texto, sin distinguir mayusculas."""
    return Counter(ch for ch in text.casefold() if ch.isalnum())


def _already_text(word: _Word, cells: dict[tuple[int, int], list[_Glyph]]) -> bool:
    """La palabra reconocida es texto real que ya esta en ese mismo sitio.

    Se da por repetida cuando letras de texto real, en su mismo renglon y de
    altura comparable, cubren al menos la mitad de su ancho y ademas dicen
    (mas o menos) lo mismo. Comparar tambien las letras evita descartar texto
    escaneado solo porque una marca de agua u otro sello pase por encima.
    """
    middle = (word.y0 + word.y1) / 2
    height = word.y1 - word.y0
    row = int(middle // _CELL)
    seen: set[_Glyph] = set()
    covered = 0.0
    under: list[str] = []
    for column in range(int(word.x0 // _CELL), int(word.x1 // _CELL) + 1):
        for glyph in cells.get((column, row), ()):
            if glyph in seen:
                continue
            seen.add(glyph)
            x0, y0, x1, y1, char = glyph
            overlap = min(x1, word.x1) - max(x0, word.x0)
            # La caja de una letra (ascendente a descendente) es algo mayor que
            # la tinta de su renglon, nunca varias veces mayor ni menor.
            if overlap > 0 and y0 <= middle <= y1 and 0.8 * height <= y1 - y0 <= 3.0 * height:
                covered += overlap
                under.append(char)
    if covered < 0.5 * (word.x1 - word.x0):
        return False
    wanted = _letters(word.text)
    if not wanted:
        return True  # solo signos de puntuacion: basta con que coincida el sitio
    # Mas de la mitad de sus letras estan en el texto real que tiene debajo.
    return 2 * sum((wanted & _letters("".join(under))).values()) > sum(wanted.values())


def _group_lines(words: list[_Word]) -> list[list[_Word]]:
    """Agrupa las palabras por renglon: Tesseract da a todas las de un renglon el mismo alto."""
    lines: list[list[_Word]] = []
    for word in words:
        last = lines[-1][-1] if lines else None
        if last is not None and abs(last.y0 - word.y0) < 0.05 and abs(last.y1 - word.y1) < 0.05 and word.x0 >= last.x0:
            lines[-1].append(word)
        else:
            lines.append([word])
    return lines


class _Fit(NamedTuple):
    """Una palabra lista para escribirse: donde empieza, donde acaba y con que tamano de letra."""

    runs: list[tuple[pymupdf.Font, str]]
    start: float
    end: float
    baseline: float
    fontsize: float


def _side_bearings(runs: list[tuple[pymupdf.Font, str]]) -> tuple[float, float]:
    """Blanco que la fuente deja antes de la primera letra y despues de la ultima (para tamano 1)."""
    try:
        first_font, first = runs[0][0], ord(runs[0][1][0])
        last_font, last = runs[-1][0], ord(runs[-1][1][-1])
        left = float(first_font.glyph_bbox(first).x0)
        right = float(last_font.glyph_advance(last)) - float(last_font.glyph_bbox(last).x1)
    except Exception:  # noqa: BLE001 - glifo sin medidas: se ajusta por el avance completo
        return 0.0, 0.0
    if not (math.isfinite(left) and math.isfinite(right)):
        return 0.0, 0.0
    return left, right


def _fit_word(word: _Word, font: pymupdf.Font, middle: float) -> _Fit | None:
    """Tamano de letra con el que la palabra ocupa exactamente el ancho de su caja.

    La caja reconocida abarca la tinta, de la primera letra a la ultima, y el
    avance de una palabra incluye ademas el blanco de sus dos extremos. Se
    descuenta: asi el tamano sale parejo en palabras cortas y largas y cada
    letra cae sobre la suya.
    """
    runs = split_runs(word.text, font)
    advance = sum(run_font.text_length(run_text, fontsize=1) for run_font, run_text in runs)
    if not advance > 0:
        return None
    left, right = _side_bearings(runs)
    inked = advance - left - right
    if inked < 0.25 * advance:
        left, inked = 0.0, advance
    height = word.y1 - word.y0
    # Una raya larga leida como un guion daria una letra descomunal: el tamano
    # se mantiene en proporcion al alto del renglon.
    fontsize = min(max((word.x1 - word.x0) / inked, 0.35 * height), 2.5 * height)
    if not 1.0 <= fontsize <= 1500.0:
        return None
    start = word.x0 - left * fontsize
    # La caja de la letra (ascendente a descendente) queda centrada en la tinta del renglon.
    baseline = (word.y0 + word.y1) / 2 + middle * fontsize
    return _Fit(runs, start, start + advance * fontsize, baseline, fontsize)


def _layer_source(
    size: tuple[float, float], words: list[_Word], font: pymupdf.Font
) -> tuple[pymupdf.Document, int] | None:
    """Pagina fuente con el texto reconocido en modo invisible; ``None`` si no queda nada que escribir.

    Cada palabra se escribe con el tamano de letra que hace coincidir su ancho
    con el de su caja. Entre dos palabras seguidas de un renglon va un espacio
    real, centrado en el hueco, para que al copiar o buscar frases el texto
    salga como se lee.
    """
    doc = pymupdf.open()
    try:
        page = doc.new_page(width=size[0], height=size[1])
        writer = pymupdf.TextWriter(page.rect)
        middle = (float(font.ascender) + float(font.descender)) / 2
        space = float(font.glyph_advance(32))
        written = 0
        for line in _group_lines(words):
            fitted = [fit for fit in (_fit_word(word, font, middle) for word in line) if fit is not None]
            for index, fit in enumerate(fitted):
                x = fit.start
                for run_font, run_text in fit.runs:
                    writer.append((x, fit.baseline), run_text, font=run_font, fontsize=fit.fontsize)
                    x += run_font.text_length(run_text, fontsize=fit.fontsize)
                written += 1
                if index + 1 < len(fitted):
                    gap = fitted[index + 1].start - fit.end
                    if gap < 1.2 * fit.fontsize:  # mas lejos es otra columna: no se une con un espacio
                        at = fit.end + (gap - space * fit.fontsize) / 2
                        writer.append((at, fit.baseline), " ", font=font, fontsize=fit.fontsize)
        if not written:
            doc.close()
            return None
        writer.write_text(page, render_mode=3)  # modo 3: texto invisible
        try:
            doc.subset_fonts()  # solo se incrustan los glifos usados
        except Exception:  # noqa: BLE001 - sin subconjunto el PDF es valido, solo algo mayor
            pass
    except BaseException:
        doc.close()
        raise
    return doc, written


# ------------------------------------------------------------ orientacion

# Una pagina escaneada de lado (o del reves) da un reconocimiento de "palabras"
# sin sentido. Solo entonces se prueba a girarla: los documentos normales no
# pagan ningun coste extra.
_PROBE_DPI = 150
_MIN_TURN_SCORE = 12
_VOWELS = frozenset("aeiouyáéíóúàèìòùäëïöüâêîôû")
# Palabras funcionales frecuentes de los idiomas del OCR. Un texto al reves
# tambien da "palabras" con letras y vocales (la u se lee como n...), pero casi
# nunca estas: son la mejor senal de que la pagina se lee derecha.
_FUNCTION_WORDS = frozenset(
    """
    de la el en los las del al una un por con para que como pero sus su se lo le les mas este esta esto entre
    sin sobre hasta desde donde cuando muy tambien ya hay ser son fue han ni otro otra todo
    the of and to in is it that for was on are with as be this by at from or an have not but which you
    all were they we can there their has been if will would about one
    els amb per una dels als pel mes aquest aquesta
    do da no na os as em um uma ao dos das pelo pela
    le les des et du est qui pour dans sur pas avec une
    der die und den von zu das mit sich des auf ist im dem nicht ein eine
    di il che per della dei sono gli nel alla
    eta da ez bat ere du dira zen baina
    """.split()
)


def _score(words: list[_Word]) -> tuple[int, int]:
    """(palabras funcionales, palabras plausibles: tres letras o mas, solo letras y con vocal)."""
    function = plausible = 0
    for word in words:
        text = word.text.strip(".,;:!?()[]{}«»\"'¡¿-–—").casefold()
        if text in _FUNCTION_WORDS:
            function += 1
        if len(text) >= 3 and text.isalpha() and any(ch in _VOWELS for ch in text):
            plausible += 1
    return function, plausible


def _looks_upright(words: list[_Word]) -> bool:
    function, plausible = _score(words)
    return function >= 3 and function >= 0.12 * len(words) and plausible >= 0.4 * len(words)


def _weight(words: list[_Word]) -> int:
    function, plausible = _score(words)
    return 4 * function + plausible // 2


def _upright_turn(
    session: Any, pid: str, visible: tuple[float, float], language: str, folder: Path, datadir: str | None
) -> int:
    """Giro (90, 180 o 270, en el sentido de las agujas) que deja derecho el texto; 0 si ninguno es claramente mejor."""
    scale = _render_scale(visible, _PROBE_DPI)
    if scale is None:
        return 0
    pix = render_pixmap(session, pid, scale, annots=False, alpha_channel=False)
    try:
        image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    finally:
        del pix
    scores: dict[int, int] = {}
    for turn in (0, 90, 180, 270):
        # PIL gira en sentido antihorario: -turn lo gira en el de las agujas.
        turned = image if turn == 0 else image.rotate(-turn, expand=True)
        probe = pymupdf.Pixmap(pymupdf.csRGB, turned.width, turned.height, turned.tobytes(), False)
        try:
            words = _recognize(probe, scale, language, folder, datadir, (turned.width / scale, turned.height / scale))
        finally:
            del probe
        scores[turn] = _weight(words)
    best = max(scores, key=lambda turn: scores[turn])
    # Solo si se lee claramente mejor: ante la duda, la pagina no se toca.
    if best == 0 or scores[best] < max(_MIN_TURN_SCORE, 2 * scores[0] + 4):
        return 0
    return best


class OcrParams(DocParams):
    pages: PageList | None = None
    languages: Annotated[tuple[Language, ...], Field(min_length=1, max_length=3)] = ("spa", "eng")
    dpi: Annotated[int, Field(ge=150, le=400)] = 300
    skipText: bool = True
    # Enderezar las paginas escaneadas de lado o del reves (cambia su giro).
    straighten: bool = True


@method("tools.ocr", OcrParams)
def tools_ocr(ctx: Ctx, p: OcrParams) -> dict[str, Any]:
    """Anade una capa de texto invisible a las paginas escaneadas."""
    session = ctx.engine.session(p.doc)
    session.require("modify")
    languages = list(dict.fromkeys(p.languages))
    folder = _require_data(ctx, languages)
    datadir = _usable_datadir(folder)
    language = "+".join(languages)
    ids = resolve_pages(session, p.pages)
    # Helvetica integrada en MuPDF: el texto es invisible (la letra da igual) y
    # asi lo que se extrae coincide caracter a caracter con lo reconocido
    # (espacios y guiones incluidos), sin depender de las fuentes del equipo.
    font = pymupdf.Font("helv")
    recognized = skipped = empty = total_words = 0
    straightened: list[str] = []
    # Girar paginas es reorganizar el documento: solo si el autor lo permite.
    may_turn = p.straighten and session.allowed("assemble")
    # Una sola transaccion: un unico paso de deshacer y, si se cancela o algo
    # falla a mitad, la excepcion la revierte entera y no queda ninguna pagina
    # a medias (OWASP A10:2025).
    with session.edit(_("Reconocer texto (OCR)")) as tx:
        for index, pid in enumerate(ids):
            # El aviso lleva solo numeros de pagina, nunca texto del documento (A09:2025).
            message = _("Reconociendo el texto de la página {page} de {total}", page=index + 1, total=len(ids))
            ctx.progress(index, len(ids), message)
            page = session.page(pid)
            geom = Geom(page)
            visible = geom.size_d
            if p.skipText and _own_text_chars(session.doc, page) > _SKIP_CHARS:
                skipped += 1
                continue
            scale = _render_scale(visible, p.dpi)
            if scale is None:
                empty += 1
                continue
            # Sin anotaciones: se reconoce el contenido de la pagina, no los
            # comentarios ni lo escrito en los campos de un formulario.
            pix = render_pixmap(session, pid, scale, annots=False, alpha_channel=False)
            try:
                words = _recognize(pix, scale, language, folder, datadir, visible)
            finally:
                del pix
            if may_turn and not _looks_upright(words):
                turn = _upright_turn(session, pid, visible, language, folder, datadir)
                if turn:
                    page.set_rotation((page.rotation + turn) % 360)
                    # Cambia lo que se ve: fuera la lista de dibujo guardada.
                    session.cache.get("dl", {}).pop((pid, False), None)
                    session.cache.get("dl", {}).pop((pid, True), None)
                    tx.touch(pid)
                    tx.structural = True  # cambia el tamano visible de la pagina
                    straightened.append(pid)
                    page = session.page(pid)
                    geom = Geom(page)
                    visible = geom.size_d
                    scale = _render_scale(visible, p.dpi)
                    if scale is None:
                        empty += 1
                        continue
                    pix = render_pixmap(session, pid, scale, annots=False, alpha_channel=False)
                    try:
                        words = _recognize(pix, scale, language, folder, datadir, visible)
                    finally:
                        del pix
            if not words:
                empty += 1
                continue

            page = session.page(pid)
            # Repetir el OCR sustituye la capa anterior en lugar de apilar otra.
            previous = [placed for placed in content.list_objects(session.doc, page) if placed.kind == "ocr"]
            for placed in previous:
                content.remove(session.doc, page, placed)
            if previous:
                tx.touch(pid)
            # Lo que ya es texto real (un encabezado, una pagina mixta) no se
            # duplica en la capa: solo se anade lo que no se podia seleccionar.
            cells = _text_index(page, geom)
            if cells:
                words = [word for word in words if not _already_text(word, cells)]
            layer = _layer_source(visible, words, font) if words else None
            if layer is None:
                empty += 1
                continue
            source, count = layer
            try:
                center_u = geom.point_u((visible[0] / 2, visible[1] / 2))
                content.place(
                    session.doc, page, source, kind="ocr", center=(center_u.x, center_u.y), size=visible,
                    angle=geom.angle_u(0), props={"lang": language, "dpi": round(scale * 72), "words": count},
                )
            finally:
                source.close()
            tx.touch(pid)
            recognized += 1
            total_words += count
        if not tx.dirty:
            tx.skip = True
    return session.result(
        tx.dirty, structural=bool(straightened), recognized=recognized, skipped=skipped, empty=empty,
        words=total_words, straightened=straightened,
    )
