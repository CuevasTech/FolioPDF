"""Freno a los PDF comprimidos con Brotli (CVE-2026-111497).

El MuPDF de Folio (1.28.2) lleva un descompresor Brotli con un fallo de
escritura fuera de limites, arreglado en Brotli 1.2.1 (MuPDF 1.28.6). Folio no
puede pasar aun a esa version: la PyMuPDF que la trae (2.x) mueve el texto que
queda al censurar (ver requirements.in y PLAN-1.0.md). MuPDF solo usa Brotli
para los flujos con el filtro /BrotliDecode o su abreviatura /Br, un formato
muy reciente que casi ningun PDF usa, asi que Folio no abre esos PDF: los
rechaza antes de que MuPDF descomprima nada (A06:2025, componentes con fallos
conocidos; defensa en profundidad sobre el aislamiento del motor).

Dos comprobaciones, porque el filtro puede aparecer de dos maneras:

1. ``check_file``, en los bytes del archivo y antes de abrirlo. Los
   diccionarios de los flujos nunca van comprimidos (tampoco el de la tabla de
   referencias ni el de los flujos de objetos), asi que el nombre del filtro
   esta a la vista. Se reconocen tambien sus escapes (``/#42rotliDecode``) y se
   rechaza cualquier ``/Filter`` indirecto (``/Filter 12 0 R``): su nombre
   podria estar guardado dentro de un flujo comprimido, donde no se ve, y un
   PDF normal no lo escribe asi.
2. ``check_document``, ya abierto y sin descomprimir ningun flujo: el filtro de
   cada flujo, resuelto, por si algo escapo a lo anterior.

Lo que no cubre: una imagen escrita dentro del contenido de una pagina
(``BI ... ID``) con ``/F /Br``, que solo se ve al descomprimir ese contenido.
Ese caso se queda dentro del motor aislado (sin red y sin acceso a tus
archivos), como cualquier otro fallo de MuPDF. Este freno se quita al pasar a
una PyMuPDF con MuPDF 1.28.6 o posterior.
"""

from __future__ import annotations

import re
from pathlib import Path

import pymupdf

from .errors import EngineError
from .i18n import _

# Delimitadores de PDF: donde acaba un nombre (ISO 32000-1, 7.2.2).
_END = rb"(?=[\x00\t\n\x0c\r ()<>\[\]{}/%]|\Z)"


def _name(text: str) -> bytes:
    """Patron de un nombre PDF con cualquiera de sus letras escrita con escape #xx."""
    parts = [b"(?:" + re.escape(ch.encode()) + b"|#(?i:" + f"{ord(ch):02x}".encode() + b"))" for ch in text]
    return b"/" + b"".join(parts)


_RAW = re.compile(
    b"(?:" + _name("BrotliDecode") + b"|" + _name("Br") + b")" + _END
    + b"|" + _name("Filter") + rb"[\x00\t\n\x0c\r ]*\d+[\x00\t\n\x0c\r ]+\d+[\x00\t\n\x0c\r ]+R" + _END
)
# Lo que puede ocupar como mucho una coincidencia (para leer por trozos sin perder ninguna).
_OVERLAP = 512
_CHUNK = 8 * 1024 * 1024
_BROTLI = {"/BrotliDecode", "/Br"}


def refused() -> EngineError:
    return EngineError(
        "unsupported",
        _("Este PDF usa un tipo de compresión (Brotli) que Folio todavía no abre, por seguridad."),
    )


def check_bytes(data: bytes) -> None:
    if _RAW.search(data):
        raise refused()


def check_file(path: Path) -> None:
    """Rechaza el archivo si su texto sin comprimir menciona el filtro o un filtro indirecto."""
    with open(path, "rb") as fh:
        tail = b""
        while True:
            chunk = fh.read(_CHUNK)
            if not chunk:
                return
            window = tail + chunk
            if _RAW.search(window):
                raise refused()
            tail = window[-_OVERLAP:]


def _filter_names(doc: pymupdf.Document, xref: int) -> set[str]:
    kind, value = doc.xref_get_key(xref, "Filter")
    if kind == "xref":
        value = doc.xref_object(int(value.split()[0]), compressed=True)
    elif kind not in ("name", "array"):
        return set()
    return set(re.findall(r"/[^\s/\[\]<>()%{}]+", value))


def check_document(doc: pymupdf.Document) -> None:
    """Ya abierto: ningun flujo puede usar Brotli. No descomprime nada."""
    for xref in range(1, doc.xref_length()):
        try:
            if not doc.xref_is_stream(xref):
                continue
            names = _filter_names(doc, xref)
        except Exception:  # noqa: BLE001 - un objeto roto no es motivo para rechazar
            continue
        if names & _BROTLI:
            raise refused()
