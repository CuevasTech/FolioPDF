"""Nombre propuesto para un documento digitalizado, a partir de su texto.

No es IA: reglas. Se busca el tipo de documento (factura, contrato...) por
palabras clave, sobre todo al principio del texto (el titulo), y la primera
fecha reconocible. Con eso se propone, por ejemplo, "Factura 2026-10-03". Si
no se reconoce nada, no se propone nada y la aplicacion usa su nombre de
siempre. El usuario puede cambiarlo antes de guardar.

El texto viene del documento (no es de fiar): las expresiones no tienen
repeticiones anidadas ni sin tope, y solo se mira un trozo acotado del texto
(A05:2025, sin ReDoS).
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date

MAX_TEXT = 20_000
HEAD = 600  # el principio del texto, donde suele estar el titulo

# Tipo de documento: palabras clave (sin tildes, en minusculas; la primera, la espanola)
# y nombre en cada idioma.
_KINDS: list[tuple[tuple[str, ...], dict[str, str]]] = [
    (("factura", "invoice"), {"es": "Factura", "en": "Invoice"}),
    (("presupuesto", "quotation", "quote", "estimate"), {"es": "Presupuesto", "en": "Quote"}),
    (("albaran", "delivery note"), {"es": "Albarán", "en": "Delivery note"}),
    (("recibo", "receipt", "ticket"), {"es": "Recibo", "en": "Receipt"}),
    (("nomina", "payslip", "pay slip", "salary slip"), {"es": "Nómina", "en": "Payslip"}),
    (("contrato", "contract", "agreement"), {"es": "Contrato", "en": "Contract"}),
    (("certificado", "certificate"), {"es": "Certificado", "en": "Certificate"}),
    (("extracto", "bank statement", "account statement"), {"es": "Extracto", "en": "Statement"}),
    (("poliza", "insurance policy"), {"es": "Póliza", "en": "Policy"}),
    (("receta", "prescription"), {"es": "Receta", "en": "Prescription"}),
    (("informe", "report"), {"es": "Informe", "en": "Report"}),
    (("solicitud", "application form"), {"es": "Solicitud", "en": "Application"}),
    (("carta", "letter"), {"es": "Carta", "en": "Letter"}),
]

_MONTHS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7,
    "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7,
    "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}
_MONTH_WORDS = "|".join(sorted(_MONTHS, key=len, reverse=True))

_ISO = re.compile(r"(?<!\d)(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?!\d)")
_NUMERIC = re.compile(r"(?<!\d)(\d{1,2})[-/.](\d{1,2})[-/.](\d{4}|\d{2})(?!\d)")
# "3 de octubre de 2026", "3 October 2026"
_DAY_MONTH = re.compile(rf"(?<!\d)(\d{{1,2}})(?:\s+de)?\s+({_MONTH_WORDS})(?:\s+de)?,?\s+(\d{{4}})(?!\d)")
# "October 3, 2026"
_MONTH_DAY = re.compile(rf"\b({_MONTH_WORDS})\s+(\d{{1,2}}),?\s+(\d{{4}})(?!\d)")


def _fold(text: str) -> str:
    """Minusculas y sin tildes, para comparar palabras clave."""
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def _valid(year: int, month: int, day: int) -> date | None:
    if not 1900 <= year <= 2100:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _word_at(text: str, word: str) -> int:
    """Posicion de ``word`` como palabra completa (o -1)."""
    match = re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", text)
    return match.start() if match else -1


def find_kind(folded: str) -> int | None:
    """Indice en ``_KINDS`` del tipo de documento: el que aparece antes en el titulo."""
    best: tuple[int, int] | None = None
    for scope in (folded[:HEAD], folded):
        for index, (words, _names) in enumerate(_KINDS):
            for word in words:
                at = _word_at(scope, word)
                if at >= 0 and (best is None or at < best[0]):
                    best = (at, index)
        if best is not None:
            return best[1]
    return None


def find_date(folded: str, day_first: bool = True) -> date | None:
    """La primera fecha reconocible del texto (dd/mm/aaaa salvo que ``day_first`` sea falso)."""
    candidates: list[tuple[int, date]] = []
    for m in _ISO.finditer(folded):
        found = _valid(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if found:
            candidates.append((m.start(), found))
    for m in _NUMERIC.finditer(folded):
        a, b, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if year < 100:
            year += 2000
        day, month = (a, b) if day_first or a > 12 else (b, a)
        if month > 12 and day <= 12:  # el orden era el otro
            day, month = month, day
        found = _valid(year, month, day)
        if found:
            candidates.append((m.start(), found))
    for m in _DAY_MONTH.finditer(folded):
        found = _valid(int(m.group(3)), _MONTHS[m.group(2)], int(m.group(1)))
        if found:
            candidates.append((m.start(), found))
    for m in _MONTH_DAY.finditer(folded):
        found = _valid(int(m.group(3)), _MONTHS[m.group(1)], int(m.group(2)))
        if found:
            candidates.append((m.start(), found))
    return min(candidates, key=lambda c: c[0])[1] if candidates else None


def suggest(text: str, lang: str) -> str | None:
    """Nombre propuesto (sin extension) o None si no se reconoce el documento."""
    folded = _fold(text[:MAX_TEXT])
    kind = find_kind(folded)
    if kind is None:
        return None
    words, names = _KINDS[kind]
    # Si el documento esta en espanol (la palabra que lo identifica lo es), las fechas van dia/mes.
    spanish = any(_word_at(folded, w) >= 0 for w in words[:1])
    found = find_date(folded, day_first=spanish or lang == "es")
    name = names.get(lang, names["en"])
    return f"{name} {found.isoformat()}" if found else name
