"""Calculos, formatos y validaciones de formularios PDF sin ejecutar JavaScript.

Los formularios hechos con Acrobat guardan sus calculos como JavaScript que
llama a funciones estandar (``AFSimple_Calculate``, ``AFNumber_Format``...).
Ejecutar JavaScript de un PDF ajeno no es aceptable (A05:2025): aqui se
reconocen esas llamadas con expresiones acotadas y se hace lo mismo en Python.
Tambien la "notacion simplificada" de Acrobat (formulas como ``Precio *
Cantidad``), con un analizador propio que solo admite numeros, nombres de
campo, ``+ - * /`` y parentesis. Cualquier otro JavaScript se ignora.

Todo lo que se lee aqui viene del documento: longitudes, numero de campos,
terminos y profundidad de las formulas estan acotados, y ningun patron puede
disparar retrocesos sin fin.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import Any

import pymupdf
from pymupdf import mupdf

from .i18n import _, language

MAX_SCRIPT = 16 * 1024
MAX_FIELDS = 2000
MAX_TERMS = 20_000  # nombres de campo en todas las sumas juntas (acota el trabajo de cada recalculo)
MAX_DEPTH = 40
MAX_TOKENS = 500

_SIMPLE = re.compile(
    r'AFSimple_Calculate\(\s*"(SUM|PRD|AVG|MIN|MAX)"\s*,\s*'
    r'(?:new\s+Array\s*\(([^()]*)\)|\[([^\[\]]*)\]|"((?:[^"\\]|\\.){1,4000})")\s*\)'
)
_QUOTED = re.compile(r'"((?:[^"\\]|\\.){1,200})"')
_SFN = re.compile(r"/\*\*\s*BVCALC\s+(.{1,2000}?)\s+EVCALC\s*\*\*/", re.S)
_NUMBER_FORMAT = re.compile(
    r'AFNumber_Format\(\s*(\d{1,2})\s*,\s*(\d)\s*,\s*(\d)\s*,\s*(\d)\s*,\s*"((?:[^"\\]|\\.){0,20})"\s*,\s*(true|false)\s*\)'
)
_PERCENT_FORMAT = re.compile(r"AFPercent_Format\(\s*(\d{1,2})\s*,\s*(\d)\s*(?:,\s*(?:true|false)\s*)?\)")
_DATE_FORMAT = re.compile(r'AFDate_FormatEx\(\s*"((?:[^"\\]|\\.){1,40})"\s*\)|AFDate_Format\(\s*(\d{1,2})\s*\)')
_NUMERIC_KEYSTROKE = re.compile(r"AF(?:Number|Percent)_Keystroke\(")
_RANGE = re.compile(
    r"AFRange_Validate\(\s*(true|false)\s*,\s*(-?\d{1,15}(?:\.\d{1,10})?)\s*,\s*(true|false)\s*,\s*(-?\d{1,15}(?:\.\d{1,10})?)\s*\)"
)

# Formatos de AFDate_Format(n), en el orden de Acrobat (los mismos que reproduce pdf.js).
_DATE_PRESETS = (
    "m/d", "m/d/yy", "mm/dd/yy", "mm/yy", "d-mmm", "d-mmm-yy", "dd-mmm-yy", "yy-mm-dd",
    "mmm-yy", "mmmm-yy", "mmm d, yyyy", "mmmm d, yyyy", "m/d/yy h:MM tt", "m/d/yy HH:MM",
)
_DATE_TOKEN = re.compile(r"\\.|yyyy|yy|mmmm|mmm|mm|m|dddd|ddd|dd|d|HH|H|hh|h|MM|M|ss|s|tt")
_DATE_PART = {
    "yyyy": "y", "yy": "y", "mmmm": "mn", "mmm": "mn", "mm": "m", "m": "m", "dd": "d", "d": "d",
    "HH": "H", "H": "H", "hh": "H", "h": "H", "MM": "M", "M": "M", "ss": "s", "s": "s",
}
# Los nombres de mes se escriben en ingles, como Acrobat y pdf.js: si se escribieran
# en espanol, otro visor no sabria leer la fecha guardada. Al teclear valen ambos.
_MONTHS_EN = ("january", "february", "march", "april", "may", "june", "july", "august", "september",
              "october", "november", "december")
_MONTHS_ES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
              "octubre", "noviembre", "diciembre")
_DAYS_EN = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


@dataclass(frozen=True)
class NumberFormat:
    decimals: int
    sep_style: int  # 0 "1,234.56"  1 "1234.56"  2 "1.234,56"  3 "1234,56"
    neg_style: int  # 0 "-1234"  1 rojo  2 "(1234)"  3 rojo entre parentesis (el color no se aplica)
    currency: str = ""
    prepend: bool = True
    percent: bool = False


@dataclass(frozen=True)
class Range:
    low: float | None
    high: float | None


@dataclass
class FieldInfo:
    name: str
    xref: int  # objeto del campo (el que lleva /T y /V)
    widgets: list[tuple[int, int]] = field(default_factory=list)  # (indice de pagina, xref del widget)
    calculation: Any = None  # ("SIMPLE", operacion, [nombres]) o ("SFN", arbol)
    number: NumberFormat | None = None
    date: str | None = None  # patron de fecha ("dd/mm/yyyy")
    numeric: bool = False  # solo admite numeros al escribir
    range: Range | None = None


# ------------------------------------------------------------------ lectura


def _int_value(doc: pymupdf.Document, kind: str, value: str) -> int | None:
    if kind == "xref":
        value = doc.xref_object(int(value.split()[0])).strip()
    try:
        return int(value)
    except ValueError:
        return None


def _script(doc: pymupdf.Document, xref: int, key: str) -> str:
    """Texto JavaScript de la accion ``key`` del campo (``AA/C``, ``AA/F``...), o vacio."""
    try:
        kind, value = doc.xref_get_key(xref, f"AA/{key}/JS")
        if kind == "string":
            return value[:MAX_SCRIPT]  # PyMuPDF ya la decodifica (PDFDocEncoding o UTF-16)
        if kind == "xref":
            stream = int(value.split()[0])
            length = _int_value(doc, *doc.xref_get_key(stream, "Length"))
            # Un calculo estandar ocupa unas lineas: un flujo grande no lo es y
            # descomprimirlo solo gastaria memoria (posible bomba de compresion).
            if length is None or length > MAX_SCRIPT:
                return ""
            pdf = pymupdf._as_pdf_document(doc)
            # MuPDF decodifica el texto segun la norma (PDFDocEncoding, UTF-16 o UTF-8 con marca).
            return mupdf.pdf_load_stream_or_string_as_utf8(mupdf.pdf_new_indirect(pdf, stream, 0))[:MAX_SCRIPT]
    except Exception:  # noqa: BLE001 - accion ilegible: como si no la hubiera
        return ""
    return ""


def _unquote(text: str) -> str:
    return re.sub(r"\\(.)", r"\1", text)


def parse_calculation(script: str) -> Any:
    found = _SIMPLE.search(script)
    if found:
        listed = found.group(2) or found.group(3)
        if listed is not None:
            names = [_unquote(n) for n in _QUOTED.findall(listed)]
        else:
            # Tambien se admite una sola cadena con los nombres separados por comas.
            names = [n.strip() for n in _unquote(found.group(4) or "").split(",")]
        names = [n for n in names if n][:MAX_FIELDS]
        return ("SIMPLE", found.group(1), names) if names else None
    found = _SFN.search(script)
    if found:
        try:
            return ("SFN", parse_expression(found.group(1)))
        except ValueError:
            return None
    return None


def parse_format(script: str) -> NumberFormat | None:
    found = _NUMBER_FORMAT.search(script)
    if found:
        decimals, sep, neg, _curr_style, currency, prepend = found.groups()
        return NumberFormat(min(int(decimals), 10), int(sep) % 4, int(neg) % 4, _unquote(currency), prepend == "true")
    found = _PERCENT_FORMAT.search(script)
    if found:
        return NumberFormat(min(int(found.group(1)), 10), int(found.group(2)) % 4, 0, "", True, percent=True)
    return None


def parse_date_format(script: str) -> str | None:
    found = _DATE_FORMAT.search(script)
    if not found:
        return None
    if found.group(1) is not None:
        pattern = _unquote(found.group(1))
    else:
        index = int(found.group(2))
        if index >= len(_DATE_PRESETS):
            return None
        pattern = _DATE_PRESETS[index]
    # Solo si de verdad describe una fecha (dia, mes o ano).
    return pattern if set(_date_parts(pattern)) & {"d", "m", "mn", "y"} else None


def parse_range(script: str) -> Range | None:
    found = _RANGE.search(script)
    if not found:
        return None
    has_low, low, has_high, high = found.groups()
    return Range(float(low) if has_low == "true" else None, float(high) if has_high == "true" else None)


# --------------------------------------------- formulas (notacion simplificada)


def _tokens(text: str) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if ch in "+-*/()":
            out.append(ch)
            i += 1
        elif ch.isdigit() or (ch == "." and i + 1 < len(text) and text[i + 1].isdigit()):
            j = i
            while j < len(text) and (text[j].isdigit() or text[j] == "."):
                j += 1
            out.append(text[i:j])
            i = j
        else:
            # Nombre de campo: todo hasta un espacio u operador; la barra
            # invertida permite incluir esos caracteres en el nombre.
            name: list[str] = []
            while i < len(text) and not text[i].isspace() and text[i] not in "+-*/()":
                if text[i] == "\\" and i + 1 < len(text):
                    name.append(text[i + 1])
                    i += 2
                else:
                    name.append(text[i])
                    i += 1
            out.append("$" + "".join(name))
        if len(out) > MAX_TOKENS:
            raise ValueError("formula demasiado larga")
    return out


def parse_expression(text: str) -> Any:
    """Arbol de una formula: ``("num", x)``, ``("field", nombre)``, ``("neg", a)`` o ``(op, a, b)``."""
    tokens = _tokens(text)
    pos = 0

    def peek() -> str | None:
        return tokens[pos] if pos < len(tokens) else None

    def take() -> str:
        nonlocal pos
        pos += 1
        return tokens[pos - 1]

    def expression(depth: int) -> Any:
        node = term(depth)
        while peek() in ("+", "-"):
            node = (take(), node, term(depth))
        return node

    def term(depth: int) -> Any:
        node = factor(depth)
        while peek() in ("*", "/"):
            node = (take(), node, factor(depth))
        return node

    def factor(depth: int) -> Any:
        if depth > MAX_DEPTH:
            raise ValueError("formula demasiado anidada")
        token = peek()
        if token is None:
            raise ValueError("formula incompleta")
        take()
        if token == "(":
            node = expression(depth + 1)
            if take() != ")":
                raise ValueError("parentesis sin cerrar")
            return node
        if token == "-":
            return ("neg", factor(depth + 1))
        if token == "+":
            return factor(depth + 1)
        if token.startswith("$"):
            return ("field", token[1:])
        try:
            return ("num", float(token))
        except ValueError:
            raise ValueError("numero no valido") from None

    try:
        tree = expression(0)
    except IndexError:
        raise ValueError("formula incompleta") from None
    if pos != len(tokens):
        raise ValueError("sobra algo en la formula")
    return tree


def evaluate(tree: Any, values: dict[str, float]) -> float:
    kind = tree[0]
    if kind == "num":
        return tree[1]
    if kind == "field":
        return values.get(tree[1], 0.0)
    if kind == "neg":
        return -evaluate(tree[1], values)
    left, right = evaluate(tree[1], values), evaluate(tree[2], values)
    if kind == "+":
        return left + right
    if kind == "-":
        return left - right
    if kind == "*":
        return left * right
    return left / right if right else math.nan


def fields_in(tree: Any) -> set[str]:
    if tree[0] == "field":
        return {tree[1]}
    if tree[0] == "num":
        return set()
    return set().union(*(fields_in(child) for child in tree[1:]))


# ------------------------------------------------------------------- numeros


def parse_number(text: str, fmt: NumberFormat | None = None, *, stored: bool = False) -> float | None:
    """Numero de un campo (sin aplicar el % de los campos de porcentaje).

    ``stored``: el valor guardado en el archivo, que Acrobat escribe sin
    formato y con punto decimal ("1234.5"). Si no, lo que escribe una persona:
    se admiten separador de miles, moneda, parentesis de negativo y %. La coma
    es decimal (convencion espanola) salvo que el campo declare el formato
    ingles, en cuyo caso "1,234" son miles.
    """
    raw = str(text or "").strip()[:60]
    if stored:
        try:
            value = float(raw)
            if math.isfinite(value):
                return value
        except ValueError:
            pass  # algunos programas guardan el texto ya formateado: se interpreta como escrito
    cleaned = re.sub(r"[^\d,.\-()]", "", raw)
    if not re.search(r"\d", cleaned):
        return None
    negative = cleaned.startswith("-") or (cleaned.startswith("(") and cleaned.endswith(")"))
    cleaned = re.sub(r"[\-()]", "", cleaned)
    english = fmt is not None and fmt.sep_style in (0, 1)
    if "," in cleaned and "." in cleaned:
        decimal = "," if cleaned.rfind(",") > cleaned.rfind(".") else "."  # el ultimo es el decimal
    elif "," in cleaned:
        tail = cleaned.rsplit(",", 1)[1]
        decimal = "" if english and (cleaned.count(",") > 1 or len(tail) == 3) else ","
    elif "." in cleaned:
        # "2.500" son dos mil quinientos (salvo formato ingles); "2.5", dos y medio.
        groups = cleaned.split(".")
        grouped = all(len(group) == 3 for group in groups[1:])
        decimal = "" if (len(groups) > 2 or (not english and grouped)) else "."
    else:
        decimal = ""
    if decimal:
        cleaned = cleaned.replace("," if decimal == "." else ".", "").replace(decimal, ".")
    else:
        cleaned = cleaned.replace(",", "").replace(".", "")
    try:
        value = float(cleaned)
    except ValueError:
        return None
    if not math.isfinite(value):
        return None
    return -value if negative else value


def raw_text(value: float) -> str:
    """Valor guardado en el campo (sin formato): como lo escribiria Acrobat."""
    if not math.isfinite(value):
        return ""
    text = f"{value:.10f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def format_number(value: float, fmt: NumberFormat | None) -> str:
    if not math.isfinite(value):
        return ""
    if fmt is None:
        return raw_text(value)
    shown = value * 100 if fmt.percent else value
    # Redondeo decimal, el 5 hacia arriba (como Acrobat y como se espera al
    # leer el numero), no el redondeo al par de los numeros binarios. Con
    # precision suficiente para cualquier float: con la de por defecto (28
    # cifras), un importe enorme haria fallar la operacion entera.
    with localcontext() as context:
        context.prec = 400
        rounded = Decimal(repr(abs(shown))).quantize(Decimal(1).scaleb(-fmt.decimals), rounding=ROUND_HALF_UP)
    digits = f"{rounded:,.{fmt.decimals}f}"  # "1,234.56"
    integer, _, fraction = digits.partition(".")
    if fmt.sep_style == 1:
        integer = integer.replace(",", "")
    elif fmt.sep_style == 2:
        integer = integer.replace(",", ".")
    elif fmt.sep_style == 3:
        integer = integer.replace(",", "")
    decimal = "," if fmt.sep_style in (2, 3) else "."
    text = integer + (decimal + fraction if fraction else "")
    if fmt.percent:
        text += "%"
    elif fmt.currency:
        text = fmt.currency + text if fmt.prepend else text + fmt.currency
    if shown < 0 and rounded != 0:
        text = f"({text})" if fmt.neg_style in (2, 3) else "-" + text
    return text


# -------------------------------------------------------------------- fechas


def _date_parts(pattern: str) -> list[str]:
    """Partes del patron en su orden: d, m (mes con numero), mn (con nombre), y, H, M, s."""
    out: list[str] = []
    for token in _DATE_TOKEN.findall(pattern):
        part = _DATE_PART.get(token)
        if part and part not in out:
            out.append(part)
    return out


def _month_named(words: list[str]) -> int | None:
    for word in words:
        if len(word) < 3:
            continue  # "de", "of"...
        for names in (_MONTHS_ES, _MONTHS_EN):
            for index, name in enumerate(names):
                if name.startswith(word) or (len(word) > len(name) and word.startswith(name)):
                    return index + 1
    return None


def parse_date(text: str, pattern: str, today: date | None = None) -> datetime | None:
    """Fecha escrita por una persona, leida en el orden del patron ("5/3/26" con "dd/mm/yyyy")."""
    raw = str(text or "").strip()[:60].lower()
    if not raw:
        return None
    today = today or date.today()
    parts = _date_parts(pattern)
    words = re.findall(r"[^\W\d_]+", raw)
    digits = re.findall(r"\d+", raw)
    if len(digits) > 8 or any(len(d) > 8 for d in digits):
        return None
    month = _month_named(words)
    order: list[str] = []
    for part in parts:
        part = "m" if part == "mn" else part  # el mes con nombre tambien se puede escribir con numero
        if part not in order:
            order.append(part)
    date_order = [p for p in order if p in ("d", "y") or (p == "m" and month is None)]
    time_order = [p for p in order if p in ("H", "M", "s")]
    if len(digits) == 3 and len(digits[0]) == 4 and set(date_order) == {"d", "m", "y"}:
        date_order = ["y", "m", "d"]  # "2026-03-05": fecha ISO, se reconoce por el ano delante
    if len(digits) == 1 and len(digits[0]) in (6, 8) and set(date_order) == {"d", "m", "y"}:
        # "05032026": cifras seguidas, repartidas segun el patron.
        widths = {"d": 2, "m": 2, "y": len(digits[0]) - 4}
        split, at = [], 0
        for p in date_order:
            split.append(digits[0][at : at + widths[p]])
            at += widths[p]
        digits = split
    numbers = [int(d) for d in digits]
    if len(numbers) < len(date_order) and "y" in date_order:
        date_order.remove("y")  # sin ano: el actual, como Acrobat
    if len(numbers) > len(date_order) + len(time_order) or len(numbers) < len(date_order):
        return None
    values = dict(zip(date_order + time_order, numbers))
    year = values.get("y", today.year)
    if year < 100:
        year += 2000 if year < 50 else 1900
    hour = values.get("H", 0)
    if "pm" in words or "p" in words:
        hour = hour + 12 if hour < 12 else hour
    elif ("am" in words or "a" in words) and hour == 12:
        hour = 0
    try:
        return datetime(year, month or values.get("m", 1), values.get("d", 1), hour, values.get("M", 0), values.get("s", 0))
    except (ValueError, OverflowError):
        return None


def format_date(value: datetime, pattern: str) -> str:
    def token(found: re.Match[str]) -> str:
        t = found.group(0)
        if t.startswith("\\"):
            return t[1:]
        month, day = _MONTHS_EN[value.month - 1], _DAYS_EN[value.weekday()]
        hour12 = (value.hour % 12) or 12
        return {
            "yyyy": f"{value.year:04d}", "yy": f"{value.year % 100:02d}",
            "mmmm": month.capitalize(), "mmm": month[:3].capitalize(), "mm": f"{value.month:02d}", "m": str(value.month),
            "dddd": day.capitalize(), "ddd": day[:3].capitalize(), "dd": f"{value.day:02d}", "d": str(value.day),
            "HH": f"{value.hour:02d}", "H": str(value.hour), "hh": f"{hour12:02d}", "h": str(hour12),
            "MM": f"{value.minute:02d}", "M": str(value.minute), "ss": f"{value.second:02d}", "s": str(value.second),
            "tt": "am" if value.hour < 12 else "pm",
        }[t]

    return _DATE_TOKEN.sub(token, pattern)


def date_hint(pattern: str) -> str:
    """Patron para orientar al escribir: "dd/mm/aaaa" en espanol, "dd/mm/yyyy" en ingles."""
    if language() == "en":
        return pattern.replace("\\", "")
    return pattern.replace("y", "a").replace("\\", "")


# ------------------------------------------------- valores escritos y mostrados


def accept(info: FieldInfo, text: str) -> tuple[str, str]:
    """Valor que se guarda y valor que se ve al escribir ``text`` en el campo.

    Lanza ``ValueError`` con el motivo (en el idioma de la aplicacion) si el campo no lo admite.
    """
    if not text.strip():
        return "", ""
    if info.date is not None:
        moment = parse_date(text, info.date)
        if moment is None:
            raise ValueError(_("Fecha no válida. Escríbela así: {pattern}.", pattern=date_hint(info.date)))
        # Se guarda ya con el formato del campo (Acrobat guarda lo tecleado):
        # asi el dato queda completo, sin ambiguedad, y otro visor lo vuelve a
        # leer con el mismo patron.
        shown = format_date(moment, info.date)
        return shown, shown
    if info.number is None and not info.numeric and info.range is None:
        return text, text
    number = parse_number(text, info.number)
    if number is None:
        raise ValueError(_("Este campo solo admite números."))
    if info.number is not None and info.number.percent:
        number /= 100  # en un porcentaje se escriben puntos: "15" es el 15 % (se guarda 0.15)
    if info.range is not None:
        low, high = info.range.low, info.range.high
        if (low is not None and number < low) or (high is not None and number > high):
            if low is not None and high is not None:
                raise ValueError(_("El valor debe estar entre {low} y {high}.", low=raw_text(low), high=raw_text(high)))
            if low is not None:
                raise ValueError(_("El valor debe ser mayor o igual que {low}.", low=raw_text(low)))
            raise ValueError(_("El valor debe ser menor o igual que {high}.", high=raw_text(high)))
    if info.number is None:
        return raw_text(number), raw_text(number)
    return raw_text(number), format_number(number, info.number)


def display(info: FieldInfo, stored: str) -> str:
    """Como se ve un valor guardado (el formato del campo aplicado)."""
    if info.number is None or not stored:
        return stored
    number = parse_number(stored, info.number, stored=True)
    return stored if number is None else format_number(number, info.number)


def edit_text(info: FieldInfo, stored: str) -> str | None:
    """Texto para corregir el valor: sin formato pero con la coma decimal del campo (None si no cambia)."""
    if info.number is None or not stored:
        return None
    number = parse_number(stored, info.number, stored=True)
    if number is None:
        return None
    comma = info.number.sep_style in (2, 3)
    if info.number.percent:
        text = raw_text(number * 100)
        return (text.replace(".", ",") if comma else text) + "%"
    text = raw_text(number)
    return text.replace(".", ",") if comma else text


# ------------------------------------------------------------------- modelo


def field_object(doc: pymupdf.Document, widget_xref: int) -> int:
    """Objeto del campo de un widget: el propio widget si lleva /T, si no su padre."""
    if doc.xref_get_key(widget_xref, "T")[0] != "null":
        return widget_xref
    kind, value = doc.xref_get_key(widget_xref, "Parent")
    return int(value.split()[0]) if kind == "xref" else widget_xref


_SCRIPTED = (pymupdf.PDF_WIDGET_TYPE_TEXT, pymupdf.PDF_WIDGET_TYPE_COMBOBOX)
_VALUED = _SCRIPTED + (
    pymupdf.PDF_WIDGET_TYPE_CHECKBOX, pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON, pymupdf.PDF_WIDGET_TYPE_LISTBOX,
)


def model(doc: pymupdf.Document) -> dict[str, FieldInfo]:
    """Campos con valor del documento, con los calculos, formatos y validaciones que se reconocen.

    Las casillas y opciones cuentan en las sumas con su valor de exportacion
    (como en Acrobat), pero sus acciones no se leen.
    """
    fields: dict[str, FieldInfo] = {}
    if not doc.is_form_pdf:
        return fields
    for index in range(doc.page_count):
        for widget in doc[index].widgets(types=list(_VALUED)):
            name = widget.field_name or ""
            if not name:
                continue
            info = fields.get(name)
            if info is None:
                if len(fields) >= MAX_FIELDS:
                    break
                obj = field_object(doc, widget.xref)
                info = FieldInfo(name, obj)
                if widget.field_type in _SCRIPTED:
                    info.calculation = parse_calculation(_script(doc, obj, "C"))
                    shown = _script(doc, obj, "F")
                    info.number = parse_format(shown)
                    info.date = None if info.number else parse_date_format(shown)
                    info.numeric = bool(_NUMERIC_KEYSTROKE.search(_script(doc, obj, "K")))
                    info.range = parse_range(_script(doc, obj, "V"))
                fields[name] = info
            info.widgets.append((index, widget.xref))
    _expand_groups(fields)
    return fields


def _expand_groups(fields: dict[str, FieldInfo]) -> None:
    """En las sumas, el nombre de un grupo ("precio") vale por todos sus campos ("precio.0", "precio.1"...)."""
    groups: dict[str, list[str]] = {}
    for name in fields:
        parts = name.split(".")
        for size in range(1, len(parts)):
            groups.setdefault(".".join(parts[:size]), []).append(name)
    budget = MAX_TERMS
    for info in fields.values():
        calc = info.calculation
        if calc is None or calc[0] != "SIMPLE":
            continue
        names: list[str] = []
        for name in calc[2]:
            names.extend([name] if name in fields else groups.get(name, []))
        budget -= len(names)
        info.calculation = ("SIMPLE", calc[1], names) if budget >= 0 else None  # sin presupuesto: no se calcula


def calculation_order(doc: pymupdf.Document, fields: dict[str, FieldInfo]) -> list[FieldInfo]:
    """Campos calculados en el orden del formulario (/CO) y, detras, los que falten."""
    by_xref = {info.xref: info for info in fields.values() if info.calculation is not None}
    ordered: list[FieldInfo] = []
    try:
        catalog = doc.pdf_catalog()
        kind, value = doc.xref_get_key(catalog, "AcroForm/CO")
        if kind == "xref":
            kind, value = "array", doc.xref_object(int(value.split()[0]))
        if kind == "array":
            for ref in re.findall(r"(\d+)\s+0\s+R", value)[:MAX_FIELDS]:
                info = by_xref.pop(int(ref), None)
                if info is not None:
                    ordered.append(info)
    except Exception:  # noqa: BLE001 - /CO ilegible: se usa el orden de los campos
        pass
    ordered.extend(by_xref.values())
    return ordered


def stored_text(doc: pymupdf.Document, info: FieldInfo) -> str:
    kind, value = doc.xref_get_key(info.xref, "V")
    return value if kind == "string" else ""


def value_of(doc: pymupdf.Document, info: FieldInfo) -> float:
    kind, value = doc.xref_get_key(info.xref, "V")
    if kind == "name":  # casilla u opcion: su valor de exportacion ("Off" es cero)
        value = value.lstrip("/")
    elif kind != "string":
        return 0.0
    number = parse_number(value, info.number, stored=True)
    return 0.0 if number is None else number


def compute(info: FieldInfo, values: dict[str, float]) -> float:
    calc = info.calculation
    if calc[0] == "SFN":
        return evaluate(calc[1], values)
    _, op, names = calc
    numbers = [values.get(name, 0.0) for name in names]
    if op == "SUM":
        return sum(numbers)
    if op == "PRD":
        result = 1.0
        for number in numbers:
            result *= number
        return result
    if op == "AVG":
        return sum(numbers) / len(numbers) if numbers else 0.0
    if op == "MIN":
        return min(numbers) if numbers else 0.0
    return max(numbers) if numbers else 0.0
