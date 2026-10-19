"""Formularios: rellenar campos, prepararlos (crear, editar, borrar) y detectarlos."""

from __future__ import annotations

import math
import re
import unicodedata
from typing import Annotated, Any, Callable, Literal

import pymupdf
from pydantic import Field, StringConstraints
from pymupdf import mupdf

from .. import formcalc
from ..errors import EngineError, invalid, locked, not_found
from ..geometry import Geom
from ..i18n import N_, _
from ..models import Color, DocParams, PageId, PageParams, Params, Point, Rect
from ..server import Ctx, method
from ..session import DocSession

WidgetId = Annotated[str, StringConstraints(pattern=r"^w[0-9]{1,9}$")]
FieldName = Annotated[str, StringConstraints(min_length=1, max_length=120)]
OptionText = Annotated[str, StringConstraints(min_length=1, max_length=200)]
FieldType = Literal["text", "checkbox", "radio", "combo", "list"]

_TYPE_NAMES = {
    pymupdf.PDF_WIDGET_TYPE_TEXT: "text",
    pymupdf.PDF_WIDGET_TYPE_CHECKBOX: "checkbox",
    pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON: "radio",
    pymupdf.PDF_WIDGET_TYPE_COMBOBOX: "combo",
    pymupdf.PDF_WIDGET_TYPE_LISTBOX: "list",
    pymupdf.PDF_WIDGET_TYPE_BUTTON: "button",
    pymupdf.PDF_WIDGET_TYPE_SIGNATURE: "signature",
}
_TYPE_CODES = {name: code for code, name in _TYPE_NAMES.items()}
# Nombres de los campos nuevos ("Texto1", "Casilla1"...): se escriben en el PDF, en el idioma de la aplicacion.
_DEFAULT_NAMES = {
    "text": N_("Texto"),
    "checkbox": N_("Casilla"),
    "radio": N_("Grupo"),
    "combo": N_("Desplegable"),
    "list": N_("Lista"),
}
_FONTS = {"sans": "Helv", "serif": "TiRo", "mono": "Cour"}
_FONT_KEYS = {"helv": "sans", "tiro": "serif", "cour": "mono"}
_REF = re.compile(r"(\d+)\s+0\s+R")
_NAME_TOKEN = re.compile(r"[^A-Za-z0-9_\-]")
_RADIO_FLAGS = pymupdf.PDF_BTN_FIELD_IS_RADIO | pymupdf.PDF_BTN_FIELD_IS_NO_TOGGLE_TO_OFF
_BORDER = (0.55, 0.58, 0.66)


def widget_id(widget: pymupdf.Widget) -> str:
    return f"w{widget.xref}"


def find_widget(page: pymupdf.Page, ident: str) -> pymupdf.Widget:
    xref = int(ident[1:])
    for widget in page.widgets():
        if widget.xref == xref:
            return widget
    raise not_found(_("El campo ya no existe."))


def _name_token(text: str, fallback: str) -> str:
    """Convierte un texto libre en un nombre PDF seguro (solo letras, digitos, guion y guion bajo)."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    token = _NAME_TOKEN.sub("_", plain.strip()).strip("_")[:40]
    if not token or token.lower() == "off":
        return fallback
    return token


def _key_name(doc: pymupdf.Document, xref: int, key: str) -> str | None:
    kind, value = doc.xref_get_key(xref, key)
    return value.lstrip("/") if kind == "name" else None


def _refs(text: str) -> list[int]:
    return [int(m) for m in _REF.findall(text or "")]


def _array(xrefs: list[int]) -> str:
    return "[" + " ".join(f"{x} 0 R" for x in xrefs) + "]"


# ----------------------------------------------------------------- AcroForm


def _acroform(doc: pymupdf.Document) -> tuple[int, str] | None:
    """Objeto y prefijo de clave donde vive el diccionario AcroForm."""
    catalog = doc.pdf_catalog()
    kind, value = doc.xref_get_key(catalog, "AcroForm")
    if kind == "xref":
        return int(value.split()[0]), ""
    if kind == "dict":
        return catalog, "AcroForm/"
    return None


def _top_fields(doc: pymupdf.Document) -> tuple[list[int], Callable[[list[int]], None]]:
    """Campos de primer nivel y funcion para reescribir esa lista."""
    where = _acroform(doc)
    if where is None:
        raise EngineError("internal", _("El documento no tiene formulario."))
    xref, prefix = where
    kind, value = doc.xref_get_key(xref, prefix + "Fields")
    if kind == "xref":
        array_xref = int(value.split()[0])
        return _refs(doc.xref_object(array_xref)), lambda refs: doc.update_object(array_xref, _array(refs))
    return _refs(value if kind == "array" else ""), lambda refs: doc.xref_set_key(xref, prefix + "Fields", _array(refs))


def _parent_of(doc: pymupdf.Document, xref: int) -> int | None:
    kind, value = doc.xref_get_key(xref, "Parent")
    return int(value.split()[0]) if kind == "xref" else None


def _radio_groups(doc: pymupdf.Document) -> dict[str, int]:
    """Grupos de opciones existentes: nombre -> objeto padre."""
    out: dict[str, int] = {}
    try:
        fields, _ = _top_fields(doc)
    except EngineError:
        return out
    for xref in fields:
        if doc.xref_get_key(xref, "Kids")[0] not in ("array", "xref"):
            continue
        kind, flags = doc.xref_get_key(xref, "Ff")
        if kind != "int" or not int(flags) & pymupdf.PDF_BTN_FIELD_IS_RADIO:
            continue
        kind, title = doc.xref_get_key(xref, "T")
        if kind == "string":
            out[title] = xref
    return out


def _kids(doc: pymupdf.Document, parent: int) -> list[int]:
    kind, value = doc.xref_get_key(parent, "Kids")
    if kind == "xref":
        return _refs(doc.xref_object(int(value.split()[0])))
    return _refs(value) if kind == "array" else []


def _set_kids(doc: pymupdf.Document, parent: int, kids: list[int]) -> None:
    doc.xref_set_key(parent, "Kids", _array(kids))


def _rename_on_state(doc: pymupdf.Document, xref: int, old: str, new: str) -> None:
    """Cambia el nombre del estado «marcado» en los flujos de apariencia del campo.

    OJO: ``xref_set_key`` de PyMuPDF con una ruta ("AP/N") que atraviesa un
    objeto indirecto no escribe el valor: deja en su lugar el texto de relleno
    "fitz: replace me!" sin avisar. Por eso cada nivel indirecto se escribe en
    su propio objeto.
    """

    def renamed(text: str) -> str:
        return re.sub(rf"/{re.escape(old)}(?![A-Za-z0-9_\-])", f"/{new}", text)

    owner, prefix = xref, "AP/"
    kind, value = doc.xref_get_key(xref, "AP")
    if kind == "xref":
        owner, prefix = int(value.split()[0]), ""
    elif kind != "dict":
        return
    for sub in ("N", "D"):
        kind, value = doc.xref_get_key(owner, prefix + sub)
        if kind == "dict":
            doc.xref_set_key(owner, prefix + sub, renamed(value))
        elif kind == "xref":
            states = int(value.split()[0])
            doc.update_object(states, renamed(doc.xref_object(states, compressed=True)))


def _select_radio(doc: pymupdf.Document, parent: int, chosen: int | None) -> None:
    """Marca una opcion del grupo y desmarca las demas."""
    state = "Off"
    for kid in _kids(doc, parent):
        on = _on_state(doc, kid)
        if kid == chosen and on:
            doc.xref_set_key(kid, "AS", f"/{on}")
            state = on
        else:
            doc.xref_set_key(kid, "AS", "/Off")
    doc.xref_set_key(parent, "V", f"/{state}")


def _on_state(doc: pymupdf.Document, xref: int) -> str | None:
    kind, value = doc.xref_get_key(xref, "AP/N")
    if kind == "xref":
        # Estados en un objeto aparte (lo hacen algunos programas). Si es un
        # flujo, es una apariencia unica y no hay estados que leer.
        states = int(value.split()[0])
        if doc.xref_is_stream(states):
            return None
        kind, value = "dict", doc.xref_object(states, compressed=True)
    if kind != "dict":
        return None
    for name in re.findall(r"/([^\s/<>\[\]()]+)\s+\d+\s+0\s+R", value):
        if name != "Off":
            return name
    return None


# --------------------------------------------------------------- descripcion


def _color(value: Any) -> list[float] | None:
    if not value:
        return None
    seq = list(value)
    if len(seq) == 1:
        return [round(seq[0], 4)] * 3
    if len(seq) == 3:
        return [round(c, 4) for c in seq]
    return None


def _calc_model(session: DocSession) -> dict[str, formcalc.FieldInfo]:
    """Calculos, formatos y validaciones del formulario (se recalcula al cambiar el documento)."""
    cached = session.cache.get("formcalc")
    if cached is None:
        try:
            cached = formcalc.model(session.doc)
        except Exception:  # noqa: BLE001 - formulario estropeado: sin calculos, pero se puede rellenar
            cached = {}
        session.cache["formcalc"] = cached
    return cached


def describe(
    doc: pymupdf.Document, widget: pymupdf.Widget, geom: Geom, calc: dict[str, Any] | None = None
) -> dict[str, Any]:
    kind = _TYPE_NAMES.get(widget.field_type, "other")
    flags = int(widget.field_flags or 0)
    raw = widget.field_value
    value: Any
    export = None
    group = None
    if kind == "checkbox":
        value = raw not in (None, False, "", "Off")
        export = widget.on_state() or "Yes"
    elif kind == "radio":
        export = _on_state(doc, widget.xref) or "Yes"
        value = _key_name(doc, widget.xref, "AS") == export
        group = widget.field_name
    elif kind == "list" and isinstance(raw, (list, tuple)):
        value = [str(v) for v in raw]
    else:
        value = "" if raw is None else str(raw)
    options: list[str] = []
    for item in widget.choice_values or []:
        options.append(str(item[1] if isinstance(item, (list, tuple)) and len(item) > 1 else item))
    kind_q, q = doc.xref_get_key(widget.xref, "Q")
    info = (calc or {}).get(widget.field_name or "") if kind == "text" else None
    shape = None
    if info is not None:
        if info.date is not None:
            shape = "date"
        elif info.number is not None:
            shape = "percent" if info.number.percent else "number"
        elif info.numeric or info.range is not None:
            shape = "number"
    return {
        # Se calcula solo a partir de otros campos (no hace falta escribirlo).
        "calculated": bool(info is not None and info.calculation is not None),
        # Que admite: "number", "percent", "date" o cualquier texto (None).
        "format": shape,
        # Como escribir la fecha ("dd/mm/aaaa").
        "hint": formcalc.date_hint(info.date) if info is not None and info.date else None,
        # Texto para corregir el valor (sin formato y con la coma del campo); None: el propio valor.
        "edit": formcalc.edit_text(info, str(value)) if info is not None else None,
        "id": widget_id(widget),
        "name": widget.field_name or "",
        "label": widget.field_label or "",
        "type": kind,
        "rect": geom.api_rect_d(widget.rect),
        "value": value,
        "options": options,
        "export": export,
        "group": group,
        "readOnly": bool(flags & pymupdf.PDF_FIELD_IS_READ_ONLY),
        "required": bool(flags & pymupdf.PDF_FIELD_IS_REQUIRED),
        "multiline": bool(flags & pymupdf.PDF_TX_FIELD_IS_MULTILINE) if kind == "text" else False,
        "password": bool(flags & pymupdf.PDF_TX_FIELD_IS_PASSWORD) if kind == "text" else False,
        "editable": bool(flags & pymupdf.PDF_CH_FIELD_IS_EDIT) if kind == "combo" else False,
        "multiSelect": bool(flags & pymupdf.PDF_CH_FIELD_IS_MULTI_SELECT) if kind == "list" else False,
        "maxLength": int(widget.text_maxlen or 0),
        "fontSize": round(float(widget.text_fontsize or 0), 2),
        "font": _FONT_KEYS.get(str(widget.text_font or "Helv").lower(), "sans"),
        "align": int(q) if kind_q == "int" and q in ("0", "1", "2") else 0,
        "textColor": _color(widget.text_color) or [0, 0, 0],
        "fill": _color(widget.fill_color),
        "border": _color(widget.border_color),
        "borderWidth": round(float(widget.border_width or 0), 2),
        "signed": bool(widget.is_signed) if kind == "signature" else False,
    }


@method("page.widgets", PageParams)
def page_widgets(ctx: Ctx, p: PageParams) -> dict[str, Any]:
    session = ctx.engine.session(p.doc)
    page = session.page(p.page)
    geom = Geom(page)
    calc = _calc_model(session)
    return {"widgets": [describe(session.doc, w, geom, calc) for w in page.widgets()]}


@method("form.fields", DocParams)
def form_fields(ctx: Ctx, p: DocParams) -> dict[str, Any]:
    """Todos los campos del documento, en orden de lectura dentro de cada pagina."""
    session = ctx.engine.session(p.doc)
    calc = _calc_model(session)
    out = []
    for index, pid in enumerate(session.page_ids):
        ctx.check_cancel()
        page = session.doc[index]
        geom = Geom(page)
        items = [describe(session.doc, w, geom, calc) for w in page.widgets()]
        items.sort(key=lambda f: (round(f["rect"][1] / 8), f["rect"][0]))
        for item in items:
            item["page"] = pid
            out.append(item)
    return {"fields": out}


# ------------------------------------------------------------------ rellenar


# Al RELLENAR solo cambian el valor (/V, /AS) y el dibujo del campo (/AP). No se
# usa Widget.update() de PyMuPDF: reescribe tambien borde, colores y letra (un
# borde de 0 pasa a 1), y en un documento firmado eso hace que la firma aparezca
# como alterada (rellenar esta permitido; cambiar el aspecto, no).
# Widget.update() queda para PREPARAR el formulario.


def _redraw(widget: pymupdf.Widget) -> None:
    annot = widget._annot.this
    mupdf.pdf_dirty_annot(annot)
    mupdf.pdf_update_annot(annot)


def _set_text(doc: pymupdf.Document, widget: pymupdf.Widget, text: str) -> None:
    """Asigna el valor de un campo de texto o de lista desplegable (tambien vacio) y lo redibuja."""
    annot = widget._annot.this
    if widget.field_type in (pymupdf.PDF_WIDGET_TYPE_COMBOBOX, pymupdf.PDF_WIDGET_TYPE_LISTBOX):
        mupdf.pdf_set_choice_field_value(annot, text)
    else:
        mupdf.pdf_set_text_field_value(annot, text)
    _redraw(widget)


def _set_check(doc: pymupdf.Document, widget: pymupdf.Widget, on: bool) -> None:
    """Marca o desmarca una casilla (o una opcion sin grupo) cambiando solo su estado."""
    state = (_on_state(doc, widget.xref) or "Yes") if on else "Off"
    doc.xref_set_key(widget.xref, "AS", f"/{state}")
    doc.xref_set_key(formcalc.field_object(doc, widget.xref), "V", f"/{state}")


def _set_choices(doc: pymupdf.Document, widget: pymupdf.Widget, chosen: list[str], options: list[str]) -> None:
    """Varias opciones elegidas en una lista: /V con todas y, si el campo lo usa, /I con sus posiciones."""
    owner = formcalc.field_object(doc, widget.xref)
    doc.xref_set_key(owner, "V", "[" + " ".join(pymupdf.get_pdf_str(value) for value in chosen) + "]")
    if doc.xref_get_key(owner, "I")[0] != "null":
        doc.xref_set_key(owner, "I", "[" + " ".join(str(options.index(value)) for value in chosen) + "]")
    _redraw(widget)


def _store(session: DocSession, info: formcalc.FieldInfo, stored: str, shown: str) -> set[str]:
    """Escribe un valor en todos los sitios donde se ve el campo. Devuelve sus paginas.

    El campo guarda ``stored`` sin formato ("1234.5", como lo guardan los
    visores que aplican estos formatos) y solo lo que se ve lleva el formato
    ("1.234,50 €"). Varios widgets con el mismo nombre son el mismo campo:
    cambian todos a la vez.
    """
    doc = session.doc
    owners: set[int] = set()
    pages: dict[int, pymupdf.Page] = {}
    for index, xref in info.widgets:
        # La pagina debe seguir viva mientras se usa su widget: si se libera,
        # el widget queda suelto y MuPDF no puede redibujarlo.
        page = pages.setdefault(index, doc[index])
        widget = page.load_widget(xref)
        if widget is not None:
            _set_text(doc, widget, shown)
            owners.add(formcalc.field_object(doc, xref))
    if stored != shown:
        for owner in owners:
            doc.xref_set_key(owner, "V", pymupdf.get_pdf_str(stored))
    return {session.page_ids[index] for index, _xref in info.widgets}


def _recalculate(session: DocSession, fields: dict[str, formcalc.FieldInfo]) -> set[str]:
    """Rehace los campos calculados, en el orden del formulario. Devuelve las paginas que cambian."""
    doc = session.doc
    order = formcalc.calculation_order(doc, fields)
    if not order:
        return set()
    values = {name: formcalc.value_of(doc, info) for name, info in fields.items()}
    touched: set[str] = set()
    for info in order:
        if session.locks.field_locked(info.name):
            continue  # lo dejo fijo una firma: no se recalcula
        result = formcalc.compute(info, values)
        # Una division por cero deja el campo vacio, como en Acrobat.
        stored = formcalc.raw_text(result)
        values[info.name] = result if math.isfinite(result) else 0.0
        if formcalc.stored_text(doc, info) == stored:
            continue
        touched |= _store(session, info, stored, formcalc.format_number(result, info.number))
    return touched


class SetValueParams(PageParams):
    id: WidgetId
    value: Annotated[str, StringConstraints(max_length=100_000)] | bool | list[OptionText]


@method("form.setValue", SetValueParams)
def form_set_value(ctx: Ctx, p: SetValueParams) -> dict[str, Any]:
    session = ctx.engine.session(p.doc)
    session.require("form")
    with session.edit(_("Rellenar campo")) as tx:
        page = session.page(p.page)
        doc = session.doc
        widget = find_widget(page, p.id)
        kind = _TYPE_NAMES.get(widget.field_type, "other")
        flags = int(widget.field_flags or 0)
        if flags & pymupdf.PDF_FIELD_IS_READ_ONLY:
            raise invalid(_("Este campo es de solo lectura."))
        session.require_field(widget.field_name or "")
        touched = {p.page}
        calc = _calc_model(session)
        if kind == "text":
            if not isinstance(p.value, str):
                raise invalid(_("El valor debe ser un texto."))
            text = p.value
            if not flags & pymupdf.PDF_TX_FIELD_IS_MULTILINE:
                text = " ".join(text.splitlines())
            if widget.text_maxlen:
                text = text[: widget.text_maxlen]
            info = calc.get(widget.field_name or "")
            if info is None:
                if (widget.field_value or "") == text:
                    tx.skip = True
                _set_text(doc, widget, text)
            else:
                try:
                    # Formato, numeros, fechas y limites que declare el campo.
                    stored, shown = formcalc.accept(info, text)
                except ValueError as exc:
                    raise invalid(str(exc)) from None
                if formcalc.stored_text(doc, info) == stored:
                    tx.skip = True  # mismo valor: nada que escribir (los calculos se revisan igual)
                else:
                    touched |= _store(session, info, stored, shown)
        elif kind == "checkbox":
            _set_check(doc, widget, bool(p.value))
        elif kind == "radio":
            parent = _parent_of(doc, widget.xref)
            if parent is None:
                # Opcion suelta creada por otro programa sin agrupar: se trata como casilla.
                _set_check(doc, widget, bool(p.value))
            else:
                _select_radio(doc, parent, widget.xref if p.value else None)
                # El resto de opciones del grupo puede estar en otras paginas.
                kids = set(_kids(doc, parent))
                for index, pid in enumerate(session.page_ids):
                    if any(w.xref in kids for w in doc[index].widgets(types=[pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON])):
                        touched.add(pid)
        elif kind in ("combo", "list"):
            options = [str(i[1] if isinstance(i, (list, tuple)) and len(i) > 1 else i) for i in widget.choice_values or []]
            if isinstance(p.value, list):
                chosen: Any = [v for v in p.value if v in options]
                chosen = chosen[0] if len(chosen) == 1 else chosen
            else:
                chosen = str(p.value)
                if chosen and chosen not in options and not flags & pymupdf.PDF_CH_FIELD_IS_EDIT:
                    raise invalid(_("Esa opción no existe en la lista."))
            if isinstance(chosen, str):
                _set_text(doc, widget, chosen)
            else:
                _set_choices(doc, widget, chosen, options)
        else:
            raise invalid(_("Este tipo de campo no se puede rellenar."))
        # Totales y demas campos calculados que dependan de este.
        recalculated = _recalculate(session, calc)
        if recalculated:
            tx.skip = False
            touched |= recalculated
        tx.touch(*touched)
    page = session.page(p.page)
    return session.result(
        sorted(touched), widget=describe(session.doc, find_widget(page, p.id), Geom(page), _calc_model(session))
    )


@method("form.reset", DocParams)
def form_reset(ctx: Ctx, p: DocParams) -> dict[str, Any]:
    session = ctx.engine.session(p.doc)
    session.require("form")
    with session.edit(_("Vaciar formulario")) as tx:
        doc = session.doc
        for index, pid in enumerate(session.page_ids):
            page = doc[index]
            changed = False
            for widget in page.widgets():
                kind = _TYPE_NAMES.get(widget.field_type)
                if kind in ("signature", "button", None):
                    continue
                if session.locks.field_locked(widget.field_name or ""):
                    continue  # los campos que dejo fijos una firma no se vacian
                if kind == "radio":
                    parent = _parent_of(doc, widget.xref)
                    if parent is not None:
                        _select_radio(doc, parent, None)
                        changed = True
                        continue
                if kind in ("checkbox", "radio"):
                    _set_check(doc, widget, False)
                else:
                    _set_text(doc, widget, "")
                changed = True
            if changed:
                tx.touch(pid)
        tx.touch(*_recalculate(session, _calc_model(session)))  # los totales vuelven a cero
        if not tx.dirty:
            tx.skip = True
    return session.result(list(session.page_ids))


@method("form.flatten", DocParams)
def form_flatten(ctx: Ctx, p: DocParams) -> dict[str, Any]:
    """Convierte los campos en contenido fijo: el formulario deja de poder editarse."""
    session = ctx.engine.session(p.doc)
    session.require("modify")
    if session.locks.fields:
        # Acoplar quitaria campos que una firma dejo fijos.
        raise locked(_("Una firma del documento deja fijos algunos campos: el formulario no se puede acoplar."))
    with session.edit(_("Acoplar formulario")) as tx:
        if not session.doc.is_form_pdf:
            tx.skip = True
        else:
            session.doc.bake(annots=False, widgets=True)
            tx.touch(*session.page_ids)
    return session.result(list(session.page_ids))


# ------------------------------------------------------------------ preparar


class FieldProps(Params):
    name: FieldName | None = None
    label: Annotated[str, StringConstraints(max_length=300)] | None = None
    required: bool | None = None
    readOnly: bool | None = None
    multiline: bool | None = None
    password: bool | None = None
    maxLength: Annotated[int, Field(ge=0, le=100_000)] | None = None
    fontSize: Annotated[float, Field(ge=0, le=144, allow_inf_nan=False)] | None = None
    font: Literal["sans", "serif", "mono"] | None = None
    align: Literal[0, 1, 2] | None = None
    textColor: Color | None = None
    fill: Color | None = None
    clearFill: bool = False
    border: Color | None = None
    clearBorder: bool = False
    borderWidth: Annotated[float, Field(ge=0, le=12, allow_inf_nan=False)] | None = None
    options: Annotated[list[OptionText], Field(max_length=500)] | None = None
    #: valor de exportacion de una casilla u opcion
    export: Annotated[str, StringConstraints(max_length=60)] | None = None
    #: nombre del grupo al que pertenece una opcion
    group: FieldName | None = None
    value: Annotated[str, StringConstraints(max_length=100_000)] | bool | None = None


class NewField(FieldProps):
    type: FieldType
    rect: Rect


def _existing_names(doc: pymupdf.Document) -> set[str]:
    names: set[str] = set()
    for page in doc:
        for widget in page.widgets():
            if widget.field_name:
                names.add(widget.field_name)
    return names


def _unique(base: str, taken: set[str]) -> str:
    if base not in taken:
        return base
    stem = re.sub(r"\d+$", "", base) or base
    n = 1
    while f"{stem}{n}" in taken:
        n += 1
    return f"{stem}{n}"


def _apply_flags(widget: pymupdf.Widget, kind: str, props: FieldProps) -> None:
    flags = int(widget.field_flags or 0)

    def toggle(bit: int, value: bool | None) -> None:
        nonlocal flags
        if value is None:
            return
        flags = flags | bit if value else flags & ~bit

    toggle(pymupdf.PDF_FIELD_IS_REQUIRED, props.required)
    toggle(pymupdf.PDF_FIELD_IS_READ_ONLY, props.readOnly)
    if kind == "text":
        toggle(pymupdf.PDF_TX_FIELD_IS_MULTILINE, props.multiline)
        toggle(pymupdf.PDF_TX_FIELD_IS_PASSWORD, props.password)
    widget.field_flags = flags


def _apply_style(widget: pymupdf.Widget, kind: str, props: FieldProps) -> None:
    if props.label is not None:
        widget.field_label = props.label or None
    if props.fontSize is not None:
        widget.text_fontsize = props.fontSize
    if props.font is not None and kind in ("text", "combo", "list"):
        widget.text_font = _FONTS[props.font]
    if props.textColor is not None:
        widget.text_color = props.textColor
    if props.clearFill:
        widget.fill_color = None
    elif props.fill is not None:
        widget.fill_color = props.fill
    if props.clearBorder:
        widget.border_color = None
        widget.border_width = 0
    elif props.border is not None:
        widget.border_color = props.border
        if not widget.border_width:
            widget.border_width = 1
    if props.borderWidth is not None:
        widget.border_width = props.borderWidth
    if props.maxLength is not None and kind == "text":
        widget.text_maxlen = props.maxLength
    if props.options is not None and kind in ("combo", "list"):
        widget.choice_values = list(dict.fromkeys(props.options)) or [_("Opción 1")]


def _join_group(doc: pymupdf.Document, xref: int, group: str, export: str) -> None:
    """Convierte un boton de opcion suelto en miembro de un grupo (un solo valor por grupo)."""
    groups = _radio_groups(doc)
    parent = groups.get(group)
    fields, set_fields = _top_fields(doc)
    if parent is None:
        parent = doc.get_new_xref()
        doc.update_object(parent, f"<</FT/Btn/Ff {_RADIO_FLAGS}/T{pymupdf.get_pdf_str(group)}/Kids[]/V/Off>>")
        fields = [x for x in fields if x != xref] + [parent]
    else:
        fields = [x for x in fields if x != xref]
    set_fields(fields)
    taken = {_on_state(doc, kid) for kid in _kids(doc, parent)}
    token = _name_token(export, _("Opcion"))
    if token in taken:
        n = 2
        while f"{token}{n}" in taken:
            n += 1
        token = f"{token}{n}"
    current = _on_state(doc, xref) or "Yes"
    if current != token:
        _rename_on_state(doc, xref, current, token)
    for key in ("T", "FT", "Ff", "V"):
        doc.xref_set_key(xref, key, "null")
    doc.xref_set_key(xref, "Parent", f"{parent} 0 R")
    doc.xref_set_key(xref, "AS", "/Off")
    _set_kids(doc, parent, [k for k in _kids(doc, parent) if k != xref] + [xref])


def _leave_group(doc: pymupdf.Document, xref: int) -> None:
    """Saca una opcion de su grupo y elimina el grupo si se queda vacio."""
    parent = _parent_of(doc, xref)
    if parent is None:
        return
    kids = [k for k in _kids(doc, parent) if k != xref]
    _set_kids(doc, parent, kids)
    if not kids:
        fields, set_fields = _top_fields(doc)
        set_fields([x for x in fields if x != parent])
    elif _key_name(doc, parent, "V") == _on_state(doc, xref):
        doc.xref_set_key(parent, "V", "/Off")


def _create(doc: pymupdf.Document, page: pymupdf.Page, geom: Geom, spec: NewField, taken: set[str]) -> int:
    rect = geom.api_rect_u(spec.rect)  # Widget.rect: espacio de las propiedades de PyMuPDF
    if rect.width < 4 or rect.height < 4:
        raise invalid(_("El campo es demasiado pequeño."))
    kind = spec.type
    widget = pymupdf.Widget()
    widget.field_type = _TYPE_CODES[kind]
    widget.rect = rect
    if kind == "radio":
        # Nombre provisional unico; al unirse al grupo hereda el del grupo.
        widget.field_name = _unique("__opcion", taken)
    else:
        widget.field_name = _unique(spec.name or f"{_(_DEFAULT_NAMES[kind])}1", taken)
    taken.add(widget.field_name)
    widget.border_color = _BORDER
    widget.border_width = 1
    widget.text_color = (0, 0, 0)
    if kind == "text":
        widget.text_fontsize = 0 if not spec.multiline else 10
        widget.field_value = spec.value if isinstance(spec.value, str) else ""
    elif kind in ("combo", "list"):
        widget.choice_values = list(dict.fromkeys(spec.options or [])) or [_("Opción 1"), _("Opción 2")]
        widget.text_fontsize = 0 if kind == "combo" else 10
        widget.field_value = spec.value if isinstance(spec.value, str) and spec.value in widget.choice_values else (
            widget.choice_values[0] if kind == "combo" else ""
        )
        if kind == "combo":
            widget.field_flags = pymupdf.PDF_CH_FIELD_IS_COMBO
    else:
        widget.field_value = False
    _apply_style(widget, kind, spec)
    _apply_flags(widget, kind, spec)
    # El identificador del objeto creado lo da la anotacion devuelta, no el Widget.
    xref = page.add_widget(widget).xref
    if spec.align is not None and kind in ("text", "combo"):
        doc.xref_set_key(xref, "Q", str(spec.align))
        fresh = next((w for w in page.widgets() if w.xref == xref), None)
        if fresh is not None:
            fresh.update()
    if kind == "radio":
        group = spec.group or _unique(f"{_(_DEFAULT_NAMES['radio'])}1", taken | set(_radio_groups(doc)))
        _join_group(doc, xref, group, spec.export or _("Opcion"))
        if spec.value is True:
            parent = _parent_of(doc, xref)
            if parent is not None:
                _select_radio(doc, parent, xref)
    elif kind == "checkbox":
        if spec.export:
            token = _name_token(spec.export, "Yes")
            current = _on_state(doc, xref) or "Yes"
            if token != current:
                _rename_on_state(doc, xref, current, token)
        if spec.value is True:
            fresh = next((w for w in page.widgets() if w.xref == xref), None)
            if fresh is not None:
                fresh.field_value = True
                fresh.update()
    return xref


class AddFieldsParams(PageParams):
    fields: Annotated[list[NewField], Field(min_length=1, max_length=400)]


@method("form.addFields", AddFieldsParams)
def form_add_fields(ctx: Ctx, p: AddFieldsParams) -> dict[str, Any]:
    """Crea uno o varios campos en una pagina (un solo paso de deshacer)."""
    session = ctx.engine.session(p.doc)
    session.require("modify")
    created: list[int] = []
    label = _("Añadir campo") if len(p.fields) == 1 else _("Añadir {count} campos", count=len(p.fields))
    with session.edit(label) as tx:
        page = session.page(p.page)
        geom = Geom(page)
        taken = _existing_names(session.doc)
        for spec in p.fields:
            created.append(_create(session.doc, page, geom, spec, taken))
        tx.touch(p.page)
    page = session.page(p.page)
    geom = Geom(page)
    wanted = set(created)
    widgets = [describe(session.doc, w, geom) for w in page.widgets() if w.xref in wanted]
    return session.result([p.page], widgets=widgets)


class UpdateFieldParams(PageParams):
    id: WidgetId
    rect: Rect | None = None
    props: FieldProps = FieldProps()


@method("form.updateField", UpdateFieldParams)
def form_update_field(ctx: Ctx, p: UpdateFieldParams) -> dict[str, Any]:
    session = ctx.engine.session(p.doc)
    session.require("modify")
    with session.edit(_("Editar campo")) as tx:
        page = session.page(p.page)
        doc = session.doc
        geom = Geom(page)
        widget = find_widget(page, p.id)
        kind = _TYPE_NAMES.get(widget.field_type, "other")
        if kind in ("other", "button"):
            raise invalid(_("Este tipo de campo no se puede editar."))
        if kind == "signature" and widget.is_signed:
            raise invalid(_("Un campo ya firmado no se puede modificar."))
        session.require_field(widget.field_name or "")
        props = p.props
        xref = widget.xref
        if p.rect is not None:
            rect = geom.api_rect_u(p.rect)
            if rect.width < 4 or rect.height < 4:
                raise invalid(_("El campo es demasiado pequeño."))
            widget.rect = rect
            tx.label = _("Mover campo")
        if props.name is not None and kind != "radio" and props.name != widget.field_name:
            taken = _existing_names(doc) - {widget.field_name}
            if props.name in taken:
                raise invalid(_("Ya existe otro campo con ese nombre."))
            widget.field_name = props.name
        _apply_style(widget, kind, props)
        _apply_flags(widget, kind, props)
        if kind == "text" and isinstance(props.value, str):
            widget.field_value = props.value
        widget.update()
        if props.align is not None and kind in ("text", "combo"):
            doc.xref_set_key(xref, "Q", str(props.align))
            find_widget(page, p.id).update()
        if kind == "radio":
            parent = _parent_of(doc, xref)
            current_group = widget.field_name
            if props.group is not None and props.group != current_group:
                selected = _key_name(doc, xref, "AS") not in (None, "Off")
                _leave_group(doc, xref)
                _join_group(doc, xref, props.group, props.export or _on_state(doc, xref) or _("Opcion"))
                parent = _parent_of(doc, xref)
                if selected and parent is not None:
                    _select_radio(doc, parent, None)
            elif props.export is not None and parent is not None:
                token = _name_token(props.export, _("Opcion"))
                current = _on_state(doc, xref) or "Yes"
                others = {_on_state(doc, k) for k in _kids(doc, parent) if k != xref}
                if token != current and token not in others:
                    was_on = _key_name(doc, xref, "AS") == current
                    _rename_on_state(doc, xref, current, token)
                    if was_on:
                        _select_radio(doc, parent, xref)
        elif kind == "checkbox" and props.export is not None:
            token = _name_token(props.export, "Yes")
            current = _on_state(doc, xref) or "Yes"
            if token != current:
                was_on = _key_name(doc, xref, "AS") == current
                _rename_on_state(doc, xref, current, token)
                if was_on:
                    doc.xref_set_key(xref, "AS", f"/{token}")
                    doc.xref_set_key(xref, "V", f"/{token}")
        tx.touch(p.page)
    page = session.page(p.page)
    return session.result([p.page], widget=describe(session.doc, find_widget(page, p.id), Geom(page)))


class DeleteFieldsParams(PageParams):
    ids: Annotated[list[WidgetId], Field(min_length=1, max_length=2_000)]


@method("form.deleteFields", DeleteFieldsParams)
def form_delete_fields(ctx: Ctx, p: DeleteFieldsParams) -> dict[str, Any]:
    session = ctx.engine.session(p.doc)
    session.require("modify")
    with session.edit(_("Eliminar campo")) as tx:
        page = session.page(p.page)
        doc = session.doc
        wanted = {int(i[1:]) for i in p.ids}
        victims = [w for w in page.widgets() if w.xref in wanted]
        if not victims:
            tx.skip = True
        for widget in victims:
            if widget.field_type == pymupdf.PDF_WIDGET_TYPE_SIGNATURE and widget.is_signed:
                raise invalid(_("No se puede eliminar un campo ya firmado."))
            session.require_field(widget.field_name or "")
            _leave_group(doc, widget.xref)
            page.delete_widget(widget)
        tx.touch(p.page)
    return session.result([p.page])


class DuplicateParams(PageParams):
    ids: Annotated[list[WidgetId], Field(min_length=1, max_length=400)]
    offset: Point = (12.0, 12.0)


@method("form.duplicate", DuplicateParams)
def form_duplicate(ctx: Ctx, p: DuplicateParams) -> dict[str, Any]:
    session = ctx.engine.session(p.doc)
    session.require("modify")
    created: list[int] = []
    with session.edit(_("Duplicar campo")) as tx:
        page = session.page(p.page)
        geom = Geom(page)
        taken = _existing_names(session.doc)
        wanted = {int(i[1:]) for i in p.ids}
        sources = [describe(session.doc, w, geom) for w in page.widgets() if w.xref in wanted]
        for src in sources:
            if src["type"] not in _TYPE_CODES:
                continue
            x0, y0, x1, y1 = src["rect"]
            spec = NewField(
                type=src["type"],
                rect=(x0 + p.offset[0], y0 + p.offset[1], x1 + p.offset[0], y1 + p.offset[1]),
                name=None if src["type"] == "radio" else src["name"],
                label=src["label"] or None,
                required=src["required"], readOnly=src["readOnly"], multiline=src["multiline"],
                maxLength=src["maxLength"] or None, fontSize=src["fontSize"], font=src["font"], align=src["align"],
                textColor=tuple(src["textColor"]), fill=tuple(src["fill"]) if src["fill"] else None,
                border=tuple(src["border"]) if src["border"] else None, clearBorder=src["border"] is None,
                borderWidth=src["borderWidth"], options=src["options"] or None,
                group=src["group"], export=None if src["type"] == "radio" else src["export"],
            )
            created.append(_create(session.doc, page, geom, spec, taken))
        if not created:
            tx.skip = True
        tx.touch(p.page)
    page = session.page(p.page)
    geom = Geom(page)
    widgets = [describe(session.doc, w, geom) for w in page.widgets() if w.xref in set(created)]
    return session.result([p.page], widgets=widgets)


# ------------------------------------------------------------------ deteccion


def _label_for(rect: pymupdf.Rect, words: list[tuple]) -> str:
    """Texto inmediatamente a la izquierda (misma linea) o encima de una zona."""
    same_line = [w for w in words if w[2] <= rect.x0 + 2 and w[1] < rect.y1 and w[3] > rect.y0 - 2 and rect.x0 - w[2] < 220]
    if same_line:
        line_no = max(same_line, key=lambda w: w[2])
        row = [w for w in same_line if abs(w[3] - line_no[3]) < 4]
        row.sort(key=lambda w: w[0])
        return " ".join(w[4] for w in row[-5:]).strip(" :._")
    above = [w for w in words if w[3] <= rect.y0 + 1 and rect.y0 - w[3] < 16 and w[2] > rect.x0 and w[0] < rect.x1]
    above.sort(key=lambda w: w[0])
    return " ".join(w[4] for w in above[:5]).strip(" :._")


def _overlaps_text(rect: pymupdf.Rect, words: list[tuple]) -> bool:
    for w in words:
        if set(w[4]) <= set("_."):
            continue
        if w[0] < rect.x1 - 1 and w[2] > rect.x0 + 1 and w[1] < rect.y1 - 1 and w[3] > rect.y0 + 1:
            return True
    return False


@method("form.autoDetect", PageParams)
def form_auto_detect(ctx: Ctx, p: PageParams) -> dict[str, Any]:
    """Propone campos a partir de las lineas, recuadros y subrayados de la pagina.

    No modifica nada: devuelve sugerencias que la interfaz muestra para que el
    usuario las acepte o descarte.
    """
    session = ctx.engine.session(p.doc)
    page = session.page(p.page)
    geom = Geom(page)
    # Texto y trazos llegan en el espacio sin girar; la deteccion se hace en el
    # de visualizacion, porque «horizontal» significa horizontal tal como se ve.
    to_d = geom.u2d
    words = [(*geom.rect_d(w[:4]), w[4]) for w in page.get_text("words")]
    existing = [pymupdf.Rect(geom.api_rect_d(w.rect)) for w in page.widgets()]
    page_rect = pymupdf.Rect(0, 0, *geom.size_d)
    boxes: list[pymupdf.Rect] = []
    lines: list[pymupdf.Rect] = []
    try:
        drawings = page.get_drawings()
    except Exception:  # noqa: BLE001 - contenido que MuPDF no sabe trazar: sin sugerencias de trazos
        drawings = []
    for drawing in drawings[:6000]:
        for item in drawing.get("items", []):
            if item[0] == "re":
                rect = (pymupdf.Rect(item[1]) * to_d).normalize()
                if rect.height <= 2.5 and rect.width >= 40:
                    lines.append(rect)
                elif rect.width <= 2.5:
                    continue
                elif drawing.get("fill") is None or drawing.get("color") is not None:
                    boxes.append(rect)
            elif item[0] == "l":
                a, b = pymupdf.Point(item[1]) * to_d, pymupdf.Point(item[2]) * to_d
                if abs(a.y - b.y) <= 1 and abs(a.x - b.x) >= 40:
                    lines.append(pymupdf.Rect(min(a.x, b.x), a.y, max(a.x, b.x), a.y + 0.5))

    suggestions: list[dict[str, Any]] = []
    used: list[pymupdf.Rect] = list(existing)

    def free(rect: pymupdf.Rect) -> bool:
        if not page_rect.contains(rect) or rect.width < 6 or rect.height < 6:
            return False
        return not any((rect & other).get_area() > 0.25 * rect.get_area() for other in used if not (rect & other).is_empty)

    def suggest(kind: str, rect: pymupdf.Rect, multiline: bool = False) -> None:
        if not free(rect):
            return
        used.append(rect)
        suggestions.append(
            {
                "type": kind,
                "rect": [round(rect.x0, 2), round(rect.y0, 2), round(rect.x1, 2), round(rect.y1, 2)],
                "label": _label_for(rect, words)[:120],
                "multiline": multiline,
            }
        )

    for rect in boxes:
        if 7 <= rect.width <= 22 and 7 <= rect.height <= 22 and abs(rect.width - rect.height) <= 3:
            suggest("checkbox", rect + (0.5, 0.5, -0.5, -0.5))
        elif rect.width >= 40 and 11 <= rect.height <= 400 and not _overlaps_text(rect, words):
            suggest("text", rect + (1.5, 1.5, -1.5, -1.5), multiline=rect.height > 34)
    for rect in lines:
        # Un subrayado es zona de escritura si encima queda sitio en blanco.
        above = pymupdf.Rect(rect.x0, rect.y0 - 16, rect.x1, rect.y0 - 1)
        if not _overlaps_text(above, words) and not any(b.contains(above) for b in boxes):
            suggest("text", above)
    for w in words:
        if len(w[4]) >= 4 and set(w[4]) <= {"_"}:
            suggest("text", pymupdf.Rect(w[0], w[1] - 2, w[2], w[3]))
    suggestions.sort(key=lambda s: (round(s["rect"][1] / 8), s["rect"][0]))
    return {"suggestions": suggestions[:300]}


class ExportDataParams(DocParams):
    pages: list[PageId] | None = None


@method("form.exportData", ExportDataParams)
def form_export_data(ctx: Ctx, p: ExportDataParams) -> dict[str, Any]:
    """Valores actuales del formulario como pares nombre/valor."""
    session = ctx.engine.session(p.doc)
    data: dict[str, Any] = {}
    for index, pid in enumerate(session.page_ids):
        if p.pages is not None and pid not in p.pages:
            continue
        page = session.doc[index]
        geom = Geom(page)
        for widget in page.widgets():
            info = describe(session.doc, widget, geom)
            if info["type"] in ("signature", "button", "other"):
                continue
            if info["type"] == "radio":
                if info["value"]:
                    data[info["name"]] = info["export"]
                else:
                    data.setdefault(info["name"], "")
            else:
                data[info["name"]] = info["value"]
    return {"data": data}
