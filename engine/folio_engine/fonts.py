"""Catalogo de fuentes para el texto que anade el usuario.

Se usan las fuentes TrueType instaladas con Windows: la interfaz muestra la
misma familia mientras se escribe y el motor incrusta en el PDF solo los
glifos usados, asi que lo que se ve al editar coincide con lo que se guarda.
"""

from __future__ import annotations

import ctypes
import logging
import os
from functools import lru_cache
from pathlib import Path

import pymupdf

log = logging.getLogger("folio.engine.fonts")

# Familia -> (normal, negrita, cursiva, negrita cursiva). Lista cerrada: el
# nombre de familia que llega de la interfaz solo se usa como clave de este
# diccionario, nunca para componer una ruta (A01:2025, manipulacion de rutas).
FAMILIES: dict[str, tuple[str, str | None, str | None, str | None]] = {
    "Arial": ("arial.ttf", "arialbd.ttf", "ariali.ttf", "arialbi.ttf"),
    "Calibri": ("calibri.ttf", "calibrib.ttf", "calibrii.ttf", "calibriz.ttf"),
    "Times New Roman": ("times.ttf", "timesbd.ttf", "timesi.ttf", "timesbi.ttf"),
    "Georgia": ("georgia.ttf", "georgiab.ttf", "georgiai.ttf", "georgiaz.ttf"),
    "Verdana": ("verdana.ttf", "verdanab.ttf", "verdanai.ttf", "verdanaz.ttf"),
    "Tahoma": ("tahoma.ttf", "tahomabd.ttf", None, None),
    "Trebuchet MS": ("trebuc.ttf", "trebucbd.ttf", "trebucit.ttf", "trebucbi.ttf"),
    "Segoe UI": ("segoeui.ttf", "segoeuib.ttf", "segoeuii.ttf", "segoeuiz.ttf"),
    "Courier New": ("cour.ttf", "courbd.ttf", "couri.ttf", "courbi.ttf"),
    "Consolas": ("consola.ttf", "consolab.ttf", "consolai.ttf", "consolaz.ttf"),
    "Comic Sans MS": ("comic.ttf", "comicbd.ttf", "comici.ttf", "comicz.ttf"),
}
DEFAULT_FAMILY = "Arial"
_FALLBACKS = ("segoeui.ttf", "seguisym.ttf", "arial.ttf")

# Familias que no estan en la carpeta de fuentes de Windows y que entrega la
# aplicacion en memoria (ver fonts.register): Aptos, la de Office, que suele
# estar solo en la cache de Office, donde este proceso aislado no puede leer.
# Lista cerrada, como FAMILIES.
EXTRA_FAMILIES = ("Aptos",)
STYLES = ("regular", "bold", "italic", "boldItalic")
MAX_FONT_BYTES = 8 * 1024 * 1024
_registered: dict[str, list[bytes | None]] = {}


def register(family: str, style: str, data: bytes) -> None:
    """Anade una variante de una familia extra. ``ValueError`` si no es una fuente valida."""
    if family not in EXTRA_FAMILIES or style not in STYLES:
        raise ValueError("familia o estilo no admitidos")
    if not data or len(data) > MAX_FONT_BYTES:
        raise ValueError("tamano de fuente no valido")
    try:
        font = pymupdf.Font(fontbuffer=data)
    except Exception as exc:  # noqa: BLE001 - MuPDF lanza tipos propios
        raise ValueError("no es una fuente") from exc
    if font.glyph_count < 50:
        raise ValueError("fuente incompleta")
    slots = _registered.setdefault(family, [None, None, None, None])
    slots[STYLES.index(style)] = bytes(data)
    get_font.cache_clear()


def known(family: str) -> bool:
    """La familia se puede usar: de Windows (lista cerrada) o extra ya entregada."""
    return family in FAMILIES or bool(_registered.get(family, [None])[0])
# Equivalentes integrados en MuPDF por si faltara el archivo de fuente.
_BUILTIN = {(False, False): "helv", (True, False): "hebo", (False, True): "heit", (True, True): "hebi"}


@lru_cache(maxsize=1)
def font_dir() -> Path:
    """Carpeta de fuentes del sistema, preguntada a Windows y no a una variable de entorno."""
    try:
        buf = ctypes.create_unicode_buffer(260)
        csidl_fonts = 0x0014
        if ctypes.windll.shell32.SHGetFolderPathW(None, csidl_fonts, None, 0, buf) == 0 and buf.value:
            return Path(buf.value)
    except Exception:  # noqa: BLE001
        log.debug("SHGetFolderPathW no disponible")
    return Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Fonts"


def _file_for(family: str, bold: bool, italic: bool) -> Path | None:
    files = FAMILIES.get(family) or FAMILIES[DEFAULT_FAMILY]
    index = (1 if bold else 0) + (2 if italic else 0)
    for candidate in (files[index], files[1] if bold else None, files[2] if italic else None, files[0]):
        if candidate:
            path = font_dir() / candidate
            if path.is_file():
                return path
    return None


@lru_cache(maxsize=64)
def get_font(family: str, bold: bool = False, italic: bool = False) -> pymupdf.Font:
    slots = _registered.get(family)
    if slots and slots[0]:
        index = (1 if bold else 0) + (2 if italic else 0)
        data = slots[index] or (slots[1] if bold else None) or (slots[2] if italic else None) or slots[0]
        try:
            return pymupdf.Font(fontbuffer=data)
        except Exception:  # noqa: BLE001
            log.warning("no se pudo cargar la fuente %s", family)
    path = _file_for(family, bold, italic)
    if path is None and family != DEFAULT_FAMILY:
        path = _file_for(DEFAULT_FAMILY, bold, italic)
    if path is not None:
        try:
            return pymupdf.Font(fontfile=str(path))
        except Exception:  # noqa: BLE001
            log.warning("no se pudo cargar la fuente %s", path.name)
    return pymupdf.Font(_BUILTIN[(bold, italic)])


@lru_cache(maxsize=1)
def fallback_fonts() -> tuple[pymupdf.Font, ...]:
    out = []
    for name in _FALLBACKS:
        path = font_dir() / name
        if path.is_file():
            try:
                out.append(pymupdf.Font(fontfile=str(path)))
            except Exception:  # noqa: BLE001
                continue
    return tuple(out)


def available_families() -> list[dict]:
    out = []
    for family, files in FAMILIES.items():
        present = [bool(f) and (font_dir() / f).is_file() for f in files]
        if present[0]:
            out.append({"family": family, "bold": present[1], "italic": present[2], "boldItalic": present[3]})
    for family, slots in _registered.items():
        if slots[0]:
            out.append({"family": family, "bold": bool(slots[1]), "italic": bool(slots[2]), "boldItalic": bool(slots[3])})
    out.sort(key=lambda item: str(item["family"]).lower())
    if not out:
        out.append({"family": DEFAULT_FAMILY, "bold": True, "italic": True, "boldItalic": True})
    return out


def split_runs(text: str, primary: pymupdf.Font) -> list[tuple[pymupdf.Font, str]]:
    """Divide una linea en tramos por fuente, recurriendo a fuentes de reserva si falta un glifo."""
    runs: list[tuple[pymupdf.Font, str]] = []
    for ch in text:
        font = primary
        if not ch.isspace() and not primary.has_glyph(ord(ch)):
            for candidate in fallback_fonts():
                if candidate.has_glyph(ord(ch)):
                    font = candidate
                    break
            else:
                ch = "?"
        if runs and runs[-1][0] is font:
            runs[-1] = (font, runs[-1][1] + ch)
        else:
            runs.append((font, ch))
    return runs
