"""Carga segura de imagenes y conversion de imagenes a paginas PDF."""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pymupdf
from PIL import Image, ImageOps, UnidentifiedImageError
from pymupdf import mupdf

from .errors import EngineError, invalid
from .i18n import _

log = logging.getLogger("folio.engine.imaging")

A4 = (595.276, 841.890)
LETTER = (612.0, 792.0)
A3 = (841.890, 1190.551)

# Lista blanca de formatos de imagen aceptados (por contenido, no por extension).
_FORMATS = {"PNG", "JPEG", "BMP", "GIF", "TIFF", "WEBP", "MPO"}

# Limite de pixeles: una imagen pequena en disco puede descomprimirse a varios
# gigas ("bomba de descompresion"). Pillow lanza un error al superar el doble.
Image.MAX_IMAGE_PIXELS = 120_000_000
MAX_IMAGE_BYTES = 200 * 1024 * 1024


@dataclass(frozen=True)
class LoadedImage:
    data: bytes  # PNG o JPEG listo para incrustar
    width: int
    height: int
    dpi: tuple[float, float]
    has_alpha: bool


def load_image(source: bytes | Path) -> LoadedImage:
    """Valida y normaliza una imagen para incrustarla en un PDF."""
    if isinstance(source, Path):
        if source.stat().st_size > MAX_IMAGE_BYTES:
            raise invalid(_("La imagen es demasiado grande."))
        raw = source.read_bytes()
    else:
        raw = bytes(source)
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise invalid(_("La imagen está vacía o es demasiado grande."))
    try:
        with Image.open(io.BytesIO(raw)) as probe:
            fmt = probe.format or ""
            if fmt not in _FORMATS:
                raise EngineError("invalid_file", _("Formato de imagen no compatible."))
            probe.load()  # fuerza la descodificacion: detecta archivos truncados o maliciosos
            dpi_info = probe.info.get("dpi") or (0, 0)
            exif_rotated = _exif_orientation(probe) not in (0, 1)
            image = ImageOps.exif_transpose(probe) if exif_rotated else probe.copy()
    except EngineError:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError, SyntaxError) as exc:
        raise EngineError("invalid_file", _("No se pudo leer la imagen.")) from exc

    width, height = image.size
    if width < 1 or height < 1:
        raise EngineError("invalid_file", _("La imagen no tiene dimensiones válidas."))
    try:
        dpi = (float(dpi_info[0]), float(dpi_info[1]))
    except (TypeError, ValueError, IndexError):
        dpi = (0.0, 0.0)
    if not (20 <= dpi[0] <= 4800 and 20 <= dpi[1] <= 4800):
        dpi = (0.0, 0.0)

    has_alpha = image.mode in ("RGBA", "LA", "PA") or (image.mode == "P" and "transparency" in image.info)
    # Un JPEG sin rotacion EXIF se incrusta sin recomprimir (perderia calidad),
    # pero NO tal cual: una foto lleva dentro la posicion GPS, la fecha, el
    # modelo de camara y a veces una miniatura de la imagen antes de recortarla.
    # Nada de eso debe acabar en el PDF sin que el usuario lo sepa (A01:2025).
    clean = strip_jpeg_metadata(raw) if fmt == "JPEG" and not exif_rotated and image.mode in ("RGB", "L", "CMYK") else None
    if clean is not None:
        data = clean
    elif fmt in ("JPEG", "MPO") and not has_alpha:
        # Recodificar con Pillow tampoco escribe metadatos.
        buf = io.BytesIO()
        image.convert("RGB").save(buf, "JPEG", quality=92, optimize=True)
        data = buf.getvalue()
    else:
        buf = io.BytesIO()
        if image.mode not in ("RGB", "RGBA", "L", "LA"):
            image = image.convert("RGBA" if has_alpha else "RGB")
        image.save(buf, "PNG", optimize=False)
        data = buf.getvalue()
    return LoadedImage(data, width, height, dpi, has_alpha)


def strip_jpeg_metadata(data: bytes) -> bytes | None:
    """Devuelve el JPEG sin metadatos y con la imagen intacta (no se recomprime).

    Se quitan EXIF (GPS, fechas, camara, miniatura), XMP, IPTC/Photoshop, los
    comentarios, la miniatura JFIF y cualquier dato que venga despues del final
    de la imagen. Se conservan el perfil de color (ICC) y el segmento de Adobe,
    que hacen falta para que los colores se interpreten bien. ``None`` si la
    estructura no se reconoce: quien llama decide que hacer.
    """
    size = len(data)
    if size < 4 or data[:2] != b"\xff\xd8":
        return None
    out = [b"\xff\xd8"]
    pos = 2
    while True:
        if pos >= size or data[pos] != 0xFF:
            return None
        while pos < size and data[pos] == 0xFF:  # relleno permitido antes de un marcador
            pos += 1
        if pos >= size:
            return None
        marker = data[pos]
        pos += 1
        if marker == 0xD9:  # fin de la imagen: lo que venga detras no se copia
            out.append(b"\xff\xd9")
            break
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:  # marcadores sin contenido
            out.append(bytes((0xFF, marker)))
            continue
        if marker in (0x00, 0xD8) or pos + 2 > size:
            return None
        length = int.from_bytes(data[pos : pos + 2], "big")
        if length < 2 or pos + length > size:
            return None
        payload = data[pos + 2 : pos + length]
        keep: bytes | None = bytes((0xFF, marker)) + data[pos : pos + length]
        if marker == 0xE0:
            if payload.startswith(b"JFIF\x00") and len(payload) >= 14:
                # Cabecera JFIF (lleva la resolucion) sin su miniatura.
                keep = b"\xff\xe0\x00\x10" + payload[:12] + b"\x00\x00"
            else:
                keep = None  # JFXX: miniaturas
        elif marker == 0xE2:
            keep = keep if payload.startswith(b"ICC_PROFILE\x00") else None
        elif marker == 0xEE:
            keep = keep if payload.startswith(b"Adobe") else None
        elif 0xE1 <= marker <= 0xEF or marker == 0xFE:
            keep = None  # EXIF, XMP, IPTC, datos de aplicaciones y comentarios
        if keep is not None:
            out.append(keep)
        pos += length
        if marker == 0xDA:
            # Datos comprimidos: llegan hasta el siguiente marcador de verdad
            # (0xFF00 es un byte de datos y RSTn un punto de reinicio).
            start = pos
            while True:
                found = data.find(b"\xff", pos)
                if found < 0 or found + 1 >= size:
                    return None
                following = data[found + 1]
                if following == 0x00 or 0xD0 <= following <= 0xD7:
                    pos = found + 2
                elif following == 0xFF:
                    pos = found + 1
                else:
                    pos = found
                    break
            out.append(data[start:pos])
    return b"".join(out)


def _same_picture(a: bytes, b: bytes) -> bool:
    """Las dos imagenes se abren y miden lo mismo (comprobacion barata tras limpiar)."""
    try:
        with Image.open(io.BytesIO(a)) as first, Image.open(io.BytesIO(b)) as second:
            return first.size == second.size and first.mode == second.mode
    except Exception:  # noqa: BLE001
        return False


def strip_embedded_image_metadata(doc: pymupdf.Document, tick: Callable[[], None] | None = None) -> int:
    """Quita los metadatos de las fotos JPEG que ya estan dentro de un PDF.

    Muchos programas incrustan el JPEG de la camara tal cual, con su EXIF.
    Limpiar los metadatos del documento sin tocar esos flujos dejaria dentro la
    posicion GPS de cada foto. Devuelve cuantas imagenes se han limpiado.
    """
    from .redaction import _used_entries  # importacion tardia: redaction es mas pesado y no depende de imaging

    pdf = pymupdf._as_pdf_document(doc)
    cleaned = 0
    # Solo las entradas que existen: un PDF de unos cientos de bytes puede
    # declarar millones de objetos vacios (A05:2025, agotamiento de recursos).
    for xref, _kind, _entry in _used_entries(doc, tick):
        try:
            if not doc.xref_is_stream(xref) or doc.xref_get_key(xref, "Subtype")[1] != "/Image":
                continue
            # Solo cuando DCT es el unico filtro: el flujo es entonces el JPEG.
            if doc.xref_get_key(xref, "Filter")[1].strip("[] ") != "/DCTDecode":
                continue
            raw = doc.xref_stream_raw(xref)
            if not raw or len(raw) > MAX_IMAGE_BYTES:
                continue
            clean = strip_jpeg_metadata(raw)
            if clean is None or len(clean) >= len(raw) or not _same_picture(raw, clean):
                continue
            # compressed=1: el contenido ya va en el formato que declara el
            # diccionario (/Filter /DCTDecode), que no debe cambiar.
            mupdf.pdf_update_stream(pdf, mupdf.pdf_load_object(pdf, xref), pymupdf.JM_BufferFromBytes(clean), 1)
            cleaned += 1
        except Exception as exc:  # noqa: BLE001 - una imagen rara no impide limpiar las demas
            log.warning("no se pudieron limpiar los metadatos de una imagen (%s)", type(exc).__name__)
    return cleaned


def _exif_orientation(image: Image.Image) -> int:
    try:
        return int(image.getexif().get(0x0112, 1) or 1)
    except Exception:  # noqa: BLE001 - EXIF corrupto: se trata como sin rotacion
        return 1


def natural_size(img: LoadedImage) -> tuple[float, float]:
    """Tamano fisico de la imagen en puntos segun su resolucion (96 ppp si no la declara)."""
    dx = img.dpi[0] or 96.0
    dy = img.dpi[1] or 96.0
    return (img.width * 72.0 / dx, img.height * 72.0 / dy)


def page_size_for(img: LoadedImage, mode: str) -> tuple[float, float]:
    """Tamano de pagina para una imagen segun el modo elegido."""
    landscape = img.width > img.height
    if mode in ("a4", "letter"):
        w, h = A4 if mode == "a4" else LETTER
        return (h, w) if landscape else (w, h)
    w, h = natural_size(img)
    if mode == "image":
        scale = min(1.0, 14_000 / max(w, h))  # limite de tamano de pagina del formato PDF
        return (w * scale, h * scale)
    # "auto": tamano real si es razonable; si no (fotos del movil), se ajusta a un A4.
    fits_a3 = max(w, h) <= A3[1] + 1 and min(w, h) <= A3[0] + 1
    if fits_a3 and max(w, h) >= 72:
        return (w, h)
    box = (A4[1], A4[0]) if landscape else A4
    scale = min(box[0] / w, box[1] / h)
    return (w * scale, h * scale)


def images_to_pdf(sources: list[Path | bytes], *, mode: str = "auto", margin: float = 0.0) -> bytes:
    """Crea un PDF con una pagina por imagen."""
    if not sources:
        raise invalid(_("No se ha indicado ninguna imagen."))
    out = pymupdf.open()
    try:
        for source in sources:
            img = load_image(source)
            pw, ph = page_size_for(img, mode)
            page = out.new_page(width=pw, height=ph)
            box = pymupdf.Rect(margin, margin, pw - margin, ph - margin)
            if box.is_empty or box.width < 10 or box.height < 10:
                box = page.rect
            scale = min(box.width / img.width, box.height / img.height)
            w, h = img.width * scale, img.height * scale
            x0 = box.x0 + (box.width - w) / 2
            y0 = box.y0 + (box.height - h) / 2
            page.insert_image(pymupdf.Rect(x0, y0, x0 + w, y0 + h), stream=img.data, keep_proportion=False)
        return out.tobytes(garbage=3, deflate=True)
    finally:
        out.close()


def sniff(path: Path) -> str:
    """Tipo de archivo por contenido: ``pdf``, ``image`` o ``unknown``."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(1024)
    except OSError as exc:
        raise EngineError("io_error", _("No se pudo leer el archivo.")) from exc
    if b"%PDF-" in head:
        return "pdf"
    try:
        with Image.open(path) as probe:
            return "image" if (probe.format or "") in _FORMATS else "unknown"
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError, SyntaxError):
        return "unknown"
