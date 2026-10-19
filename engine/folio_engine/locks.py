"""Bloqueos que imponen las firmas de un documento.

Firmar un PDF no impide cambiar sus bytes: lo que hace es sellarlos, de modo
que cualquier cambio posterior se detecta. Ademas, quien firma puede declarar
que cambios admite despues:

* Certificacion (DocMDP, la primera firma): /P 1 ningun cambio; /P 2 rellenar
  formularios y firmar; /P 3 ademas, anotar.
* Bloqueo de campo (/Lock de un campo de firma, que entra en vigor al firmarlo):
  deja fijos algunos campos (/Action /All, /Include o /Exclude y /Fields) y,
  en PDF 2.0, tambien puede limitar el documento entero con /P.

Folio respeta esas declaraciones como Acrobat: no deja hacer lo que el firmante
no permitio. Todo esto se lee de un documento ajeno: si algo no se entiende,
se ignora esa entrada (no se inventa un bloqueo ni se quita uno valido).
"""

from __future__ import annotations

from dataclasses import dataclass

import pymupdf
from pymupdf import mupdf

# Nivel de bloqueo: lo que se puede seguir haciendo en el documento.
NO_CHANGES, FORMS, ANNOTATIONS = 1, 2, 3
# Acciones de ``DocSession.require`` que admite cada nivel. Imprimir y copiar
# no cambian el documento: valen siempre.
ALLOWED = {
    NO_CHANGES: frozenset({"print", "printHq", "copy", "accessibility"}),
    FORMS: frozenset({"print", "printHq", "copy", "accessibility", "form", "sign"}),
    ANNOTATIONS: frozenset({"print", "printHq", "copy", "accessibility", "form", "sign", "annotate"}),
}
_MAX_FIELDS = 5000  # nombres en una lista /Fields (dato ajeno: acotado)


@dataclass(frozen=True)
class Locks:
    level: int | None = None  # None: el documento no tiene bloqueo general
    # Campos fijados por firmas: (accion, nombres) con accion "All", "Include" o "Exclude".
    fields: tuple[tuple[str, frozenset[str]], ...] = ()

    def allows(self, action: str) -> bool:
        return self.level is None or action in ALLOWED[self.level]

    def field_locked(self, name: str) -> bool:
        for action, names in self.fields:
            if action == "All" or (action == "Include" and name in names) or (action == "Exclude" and name not in names):
                return True
        return False

    def payload(self) -> dict[str, object]:
        return {"level": self.level, "fields": bool(self.fields)}


NONE = Locks()


def _name(obj: mupdf.PdfObj) -> str:
    return mupdf.pdf_to_name(obj) if mupdf.pdf_is_name(obj) else ""


def _level(value: mupdf.PdfObj, default: int | None) -> int | None:
    """Valor /P valido (1, 2 o 3); cualquier otra cosa es el valor por defecto."""
    if mupdf.pdf_is_int(value):
        number = mupdf.pdf_to_int(value)
        if number in (NO_CHANGES, FORMS, ANNOTATIONS):
            return number
    return default


def _certification(pdf: mupdf.PdfDocument) -> int | None:
    """Nivel /P de la firma de certificacion, o None si el documento no esta certificado."""
    perms = mupdf.pdf_dict_gets(mupdf.pdf_dict_gets(mupdf.pdf_trailer(pdf), "Root"), "Perms")
    signature = mupdf.pdf_dict_gets(perms, "DocMDP")
    if not mupdf.pdf_is_dict(signature):
        return None
    references = mupdf.pdf_dict_gets(signature, "Reference")
    for index in range(min(mupdf.pdf_array_len(references), 50)):
        reference = mupdf.pdf_array_get(references, index)
        if _name(mupdf.pdf_dict_gets(reference, "TransformMethod")) == "DocMDP":
            # Sin /P, la norma dice 2 (rellenar formularios y firmar).
            return _level(mupdf.pdf_dict_gets(mupdf.pdf_dict_gets(reference, "TransformParams"), "P"), FORMS)
    return None


def _field_dict(pdf: mupdf.PdfDocument, xref: int) -> mupdf.PdfObj:
    """Diccionario del campo de un widget (el propio widget si lleva /T, si no su padre)."""
    widget = mupdf.pdf_load_object(pdf, xref)
    if mupdf.pdf_is_dict(mupdf.pdf_dict_gets(widget, "Lock")) or mupdf.pdf_dict_gets(widget, "T").m_internal:
        return widget
    parent = mupdf.pdf_dict_gets(widget, "Parent")
    return parent if mupdf.pdf_is_dict(parent) else widget


def read(doc: pymupdf.Document) -> Locks:
    """Bloqueos vigentes en el documento abierto (sin firmas, ninguno)."""
    try:
        pdf = pymupdf._as_pdf_document(doc)
        level = _certification(pdf)
        fields: list[tuple[str, frozenset[str]]] = []
        for page in doc:
            for widget in page.widgets(types=[pymupdf.PDF_WIDGET_TYPE_SIGNATURE]):
                if not widget.is_signed:
                    continue  # un bloqueo de campo entra en vigor al firmarlo
                lock = mupdf.pdf_dict_gets(_field_dict(pdf, widget.xref), "Lock")
                if not mupdf.pdf_is_dict(lock):
                    continue
                action = _name(mupdf.pdf_dict_gets(lock, "Action"))
                if action in ("All", "Include", "Exclude"):
                    listed = mupdf.pdf_dict_gets(lock, "Fields")
                    names = frozenset(
                        mupdf.pdf_to_text_string(mupdf.pdf_array_get(listed, i))
                        for i in range(min(mupdf.pdf_array_len(listed), _MAX_FIELDS))
                    )
                    fields.append((action, names))
                locked = _level(mupdf.pdf_dict_gets(lock, "P"), None)
                if locked is not None:
                    level = locked if level is None else min(level, locked)
        return Locks(level, tuple(fields))
    except Exception:  # noqa: BLE001 - estructura ilegible: sin bloqueos que hacer cumplir
        return NONE
