"""Elementos que el usuario anade a una pagina como contenido real del PDF.

Cada elemento (un texto, una imagen, una firma manuscrita, una forma, una marca
de agua...) se guarda como un *Form XObject* mas un flujo de contenido minimo
que lo coloca con una matriz::

    q  /GS gs  a b c d e f cm  /Nombre Do  Q

Mover, escalar o girar el elemento es reescribir esa matriz; borrarlo es quitar
ese flujo. Como es contenido normal de la pagina, se ve e imprime en cualquier
visor. El flujo lleva ademas una etiqueta propia (``/FolioObj``) con los datos
del elemento, de modo que Folio puede volver a seleccionarlo y editarlo aunque
el PDF se haya guardado y reabierto.
"""

from __future__ import annotations

import json
import math
import re
import secrets
from dataclasses import dataclass, field
from typing import Any

import pymupdf
from pymupdf import mupdf

from .errors import EngineError, invalid, not_found
from .geometry import Geom, bounds, box_corners, matrix_operand
from .i18n import _

TAG_KEY = "FolioObj"
TAG_VERSION = 1
MAX_TAG_HEX = 800_000

KINDS = ("text", "image", "signature", "shape", "stamp", "watermark", "header", "footer", "pagenum", "ocr")

_HEX = re.compile(r"^[0-9a-fA-F]*$")
_NAME = re.compile(r"^[A-Za-z0-9_.\-]{1,60}$")
_ID = re.compile(r"^[a-z][0-9a-z\-]{1,40}$")
_PLACEMENT = re.compile(rb"^\s*q\s+(?:/[A-Za-z0-9_.\-]+\s+gs\s+)?(?:[-+0-9.eE]+\s+){6}cm\s+/([A-Za-z0-9_.\-]+)\s+Do\s+Q\s*$")
_DO = re.compile(rb"/([A-Za-z0-9_.\-]+)\s+Do")


@dataclass
class Placed:
    id: str
    kind: str
    stream: int  # xref del flujo de colocacion
    xobject: int  # xref del Form XObject
    name: str  # nombre del recurso en la pagina
    center: tuple[float, float]  # en U
    size: tuple[float, float]
    angle: float  # grados horarios en U
    source: tuple[float, float, float, float]  # caja del XObject en sus coordenadas
    opacity: float = 1.0
    props: dict[str, Any] = field(default_factory=dict)

    def tag(self) -> str:
        data = {
            "v": TAG_VERSION,
            "id": self.id,
            "k": self.kind,
            "x": self.xobject,
            "n": self.name,
            "c": [round(self.center[0], 4), round(self.center[1], 4)],
            "s": [round(self.size[0], 4), round(self.size[1], 4)],
            "a": round(self.angle % 360, 4),
            "src": [round(v, 4) for v in self.source],
            "o": round(self.opacity, 3),
            "p": self.props,
        }
        raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        # En hexadecimal dentro de una cadena literal: no hay nada que escapar ni
        # ambiguedad de codificacion al leerla de vuelta.
        return "(" + raw.hex() + ")"

    def describe(self, geom: Geom) -> dict[str, Any]:
        """Geometria del elemento en el espacio de visualizacion."""
        center = geom.point_d(self.center)
        angle = geom.angle_d(self.angle)
        corners = box_corners(center, self.size, angle)
        return {
            "id": self.id,
            "kind": self.kind,
            "cx": round(center[0], 3),
            "cy": round(center[1], 3),
            "w": round(self.size[0], 3),
            "h": round(self.size[1], 3),
            "angle": round(angle, 3),
            "opacity": self.opacity,
            "bbox": [round(v, 3) for v in bounds(corners)],
            "props": self.props,
        }


def new_object_id() -> str:
    return "o" + secrets.token_hex(6)


def _finite(value: Any, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError
    number = float(value)
    if not math.isfinite(number) or not low <= number <= high:
        raise ValueError
    return number


def _resource_xobjects(page: pymupdf.Page) -> dict[str, int]:
    """XObject de los recursos de la pagina: nombre -> numero de objeto."""
    out: dict[str, int] = {}
    try:
        pdfpage = pymupdf._as_pdf_page(page)
        resources = mupdf.pdf_dict_get_inheritable(pdfpage.obj(), pymupdf.PDF_NAME("Resources"))
        xobjects = mupdf.pdf_dict_get(resources, pymupdf.PDF_NAME("XObject"))
        if mupdf.pdf_is_dict(xobjects):
            for i in range(mupdf.pdf_dict_len(xobjects)):
                value = mupdf.pdf_dict_get_val(xobjects, i)
                if mupdf.pdf_is_indirect(value):
                    out[mupdf.pdf_to_name(mupdf.pdf_dict_get_key(xobjects, i))] = mupdf.pdf_to_num(value)
    except Exception:  # noqa: BLE001 - recursos malformados: la pagina no tiene elementos reconocibles
        return {}
    return out


def _parse_tag(doc: pymupdf.Document, stream_xref: int, xobjects: dict[str, int]) -> Placed | None:
    """Lee la etiqueta de un flujo de contenido.

    La etiqueta viene dentro del PDF, es decir, es un dato no fiable: se valida
    campo a campo y, si algo no cuadra, el flujo se trata como contenido normal.

    El XObject del elemento se localiza por su nombre en los recursos de la
    pagina y no por el numero de objeto anotado en la etiqueta: al guardar con
    limpieza los objetos se renumeran y ese numero deja de ser valido.
    """
    try:
        kind, value = doc.xref_get_key(stream_xref, TAG_KEY)
    except Exception:  # noqa: BLE001
        return None
    if kind != "string" or not value or len(value) > MAX_TAG_HEX or not _HEX.match(value):
        return None
    try:
        data = json.loads(bytes.fromhex(value).decode("utf-8"))
        if not isinstance(data, dict) or data.get("v") != TAG_VERSION:
            return None
        obj_id, obj_kind, name = data["id"], data["k"], data["n"]
        if not (isinstance(obj_id, str) and _ID.match(obj_id)):
            return None
        if obj_kind not in KINDS or not (isinstance(name, str) and _NAME.match(name)):
            return None
        xobject = xobjects.get(name)
        if xobject is None:
            return None
        center = (_finite(data["c"][0], -1e6, 1e6), _finite(data["c"][1], -1e6, 1e6))
        size = (_finite(data["s"][0], 0.01, 1e6), _finite(data["s"][1], 0.01, 1e6))
        angle = _finite(data["a"], -3600, 3600)
        source = tuple(_finite(v, -1e6, 1e6) for v in data["src"])
        if len(source) != 4 or source[2] - source[0] <= 0 or source[3] - source[1] <= 0:
            return None
        opacity = _finite(data.get("o", 1.0), 0.0, 1.0)
        props = data.get("p", {})
        if not isinstance(props, dict):
            return None
        # El flujo debe ser exactamente una colocacion del XObject que dice la etiqueta.
        match = _PLACEMENT.match(doc.xref_stream(stream_xref) or b"")
        if not match or match.group(1).decode("ascii") != name:
            return None
    except (KeyError, IndexError, TypeError, ValueError, RecursionError):
        # RecursionError: una etiqueta hostil con miles de niveles anidados no
        # debe tumbar la operacion; se trata como contenido normal.
        return None
    return Placed(obj_id, obj_kind, stream_xref, xobject, name, center, size, angle, source, opacity, props)  # type: ignore[arg-type]


def list_objects(doc: pymupdf.Document, page: pymupdf.Page) -> list[Placed]:
    out = []
    xobjects: dict[str, int] | None = None
    for xref in page.get_contents():
        # Solo se miran los recursos si algun flujo lleva etiqueta.
        try:
            if doc.xref_get_key(xref, TAG_KEY)[0] != "string":
                continue
        except Exception:  # noqa: BLE001
            continue
        if xobjects is None:
            xobjects = _resource_xobjects(page)
        placed = _parse_tag(doc, xref, xobjects)
        if placed is not None:
            out.append(placed)
    return out


def find_object(doc: pymupdf.Document, page: pymupdf.Page, obj_id: str) -> Placed:
    for placed in list_objects(doc, page):
        if placed.id == obj_id:
            return placed
    raise not_found(_("El elemento ya no existe en la página."))


def _set_contents(doc: pymupdf.Document, page: pymupdf.Page, xrefs: list[int]) -> None:
    doc.xref_set_key(page.xref, "Contents", "[" + " ".join(f"{x} 0 R" for x in xrefs) + "]")


def _gstate(page: pymupdf.Page, opacity: float) -> str | None:
    if opacity >= 0.995:
        return None
    return page._set_opacity(CA=max(0.0, opacity), ca=max(0.0, opacity))


def _write_placement(doc: pymupdf.Document, page: pymupdf.Page, placed: Placed) -> None:
    geom = Geom(page)
    matrix = geom.placement(placed.source, placed.center, placed.size, placed.angle)
    parts = ["q"]
    gstate = _gstate(page, placed.opacity)
    if gstate:
        parts.append(f"/{gstate} gs")
    parts.append(f"{matrix_operand(matrix)} cm")
    parts.append(f"/{placed.name} Do")
    parts.append("Q")
    doc.update_stream(placed.stream, ("\n".join(parts) + "\n").encode("ascii"))
    doc.xref_set_key(placed.stream, TAG_KEY, placed.tag())


def _source_box(doc: pymupdf.Document, xobject: int) -> tuple[float, float, float, float]:
    """Caja que ocupa el XObject una vez aplicada su propia /Matrix."""
    kind, value = doc.xref_get_key(xobject, "BBox")
    numbers = [float(v) for v in re.findall(r"[-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?", value)] if kind == "array" else []
    if len(numbers) != 4:
        raise EngineError("internal", _("No se pudo colocar el elemento."))
    box = pymupdf.Rect(numbers).normalize()
    kind, value = doc.xref_get_key(xobject, "Matrix")
    if kind == "array":
        m = [float(v) for v in re.findall(r"[-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?", value)]
        if len(m) == 6:
            box = (box * pymupdf.Matrix(*m)).normalize()
    if box.width <= 0 or box.height <= 0:
        raise EngineError("internal", _("No se pudo colocar el elemento."))
    return (box.x0, box.y0, box.x1, box.y1)


def place(
    doc: pymupdf.Document,
    page: pymupdf.Page,
    source: pymupdf.Document,
    *,
    kind: str,
    center: tuple[float, float],
    size: tuple[float, float],
    angle: float = 0.0,
    opacity: float = 1.0,
    props: dict[str, Any] | None = None,
    obj_id: str | None = None,
    background: bool = False,
    position: int | None = None,
    pno: int = 0,
) -> Placed:
    """Incorpora una pagina de ``source`` (la primera, o ``pno``) como elemento de ``page``.

    ``center`` y ``angle`` se dan en el espacio sin girar (U). Con
    ``background`` el elemento queda por debajo del contenido existente.
    Colocar varias paginas de un mismo ``source`` comparte entre ellas sus
    recursos (la fuente incrustada), que se copian al documento una sola vez.
    """
    if kind not in KINDS:
        raise invalid(_("Tipo de elemento desconocido."))
    if size[0] <= 0 or size[1] <= 0:
        raise invalid(_("El elemento no tiene tamaño."))
    # Sin esto, un estado grafico sin cerrar del contenido original desplazaria
    # o deformaria lo que se anade despues.
    if not page.is_wrapped:
        page.wrap_contents()
    before = page.get_contents()
    page.show_pdf_page(pymupdf.Rect(0, 0, 16, 16), source, pno, keep_proportion=False, overlay=not background)
    added = [x for x in page.get_contents() if x not in before]
    if len(added) != 1:
        raise EngineError("internal", _("No se pudo colocar el elemento."))
    stream = added[0]
    found = _DO.search(doc.xref_stream(stream) or b"")
    if not found:
        raise EngineError("internal", _("No se pudo colocar el elemento."))
    name = found.group(1).decode("ascii")
    # PyMuPDF crea dos XObject: uno interior con el contenido y otro exterior
    # (el que nombra el flujo) cuya /Matrix ya lleva la colocacion que el
    # eligio. Esa matriz se neutraliza: aqui la posicion la fija solo el "cm".
    xobject = next((x[0] for x in doc.get_page_xobjects(page.number) if x[1] == name and x[2] == 0), None)
    if xobject is None:
        raise EngineError("internal", _("No se pudo colocar el elemento."))
    doc.xref_set_key(xobject, "Matrix", "[1 0 0 1 0 0]")
    placed = Placed(
        id=obj_id or new_object_id(),
        kind=kind,
        stream=stream,
        xobject=xobject,
        name=name,
        center=(float(center[0]), float(center[1])),
        size=(float(size[0]), float(size[1])),
        angle=float(angle) % 360,
        source=_source_box(doc, xobject),
        opacity=float(opacity),
        props=props or {},
    )
    _write_placement(doc, page, placed)
    if position is not None:
        order = [x for x in page.get_contents() if x != stream]
        order.insert(max(0, min(position, len(order))), stream)
        _set_contents(doc, page, order)
    return placed


def swap_source(doc: pymupdf.Document, page: pymupdf.Page, placed: Placed, source: pymupdf.Document, *, pno: int = 0) -> bool:
    """Cambia lo que dibuja un elemento por otra pagina de origen del mismo tamano.

    No se toca nada mas: el flujo de colocacion (su matriz exacta, su posicion
    entre los demas y su etiqueta) sigue siendo el mismo y solo pasa a apuntar
    al contenido nuevo. Devuelve ``False``, sin cambiar nada, si el origen no
    mide lo mismo que el contenido actual.
    """
    before = page.get_contents()
    if placed.stream not in before:
        return False
    page.show_pdf_page(pymupdf.Rect(0, 0, 16, 16), source, pno, keep_proportion=False, overlay=True)
    added = [x for x in page.get_contents() if x not in before]
    found = _DO.search(doc.xref_stream(added[0]) or b"") if len(added) == 1 else None
    name = found.group(1).decode("ascii") if found else ""
    xobject = next((x[0] for x in doc.get_page_xobjects(page.number) if x[1] == name and x[2] == 0), None) if name else None
    # El flujo que ha creado PyMuPDF solo servia para traer el contenido.
    _set_contents(doc, page, before)
    if name:
        _drop_resource(page, name)
    if xobject is None:
        return False
    doc.xref_set_key(xobject, "Matrix", "[1 0 0 1 0 0]")  # como en ``place``: la posicion la fija el "cm"
    box = _source_box(doc, xobject)
    if any(abs(a - b) > 0.02 for a, b in zip(box, placed.source)):
        return False
    pdf = pymupdf._as_pdf_document(doc)
    resources = mupdf.pdf_dict_get(pymupdf._as_pdf_page(page).obj(), pymupdf.PDF_NAME("Resources"))
    xobjects = mupdf.pdf_dict_get(resources, pymupdf.PDF_NAME("XObject"))
    if not mupdf.pdf_is_dict(xobjects):
        return False
    mupdf.pdf_dict_puts(xobjects, placed.name, mupdf.pdf_new_indirect(pdf, xobject, 0))
    placed.xobject = xobject
    return True


def update_placement(
    doc: pymupdf.Document,
    page: pymupdf.Page,
    placed: Placed,
    *,
    center: tuple[float, float] | None = None,
    size: tuple[float, float] | None = None,
    angle: float | None = None,
    opacity: float | None = None,
    props: dict[str, Any] | None = None,
) -> Placed:
    if center is not None:
        placed.center = (float(center[0]), float(center[1]))
    if size is not None:
        if size[0] <= 0 or size[1] <= 0:
            raise invalid(_("El elemento no tiene tamaño."))
        placed.size = (float(size[0]), float(size[1]))
    if angle is not None:
        placed.angle = float(angle) % 360
    if opacity is not None:
        placed.opacity = float(opacity)
    if props is not None:
        placed.props = props
    _write_placement(doc, page, placed)
    return placed


def _drop_resource(page: pymupdf.Page, name: str) -> None:
    """Quita el nombre del XObject de los recursos para que el objeto pueda eliminarse al guardar."""
    try:
        pdfpage = pymupdf._as_pdf_page(page)
        resources = mupdf.pdf_dict_get_inheritable(pdfpage.obj(), pymupdf.PDF_NAME("Resources"))
        xobjects = mupdf.pdf_dict_get(resources, pymupdf.PDF_NAME("XObject"))
        if mupdf.pdf_is_dict(xobjects):
            mupdf.pdf_dict_dels(xobjects, name)
    except Exception:  # noqa: BLE001 - si falla solo queda un recurso sin usar, inofensivo
        pass


def _ensure_resource(doc: pymupdf.Document, page: pymupdf.Page, name: str, xobject: int) -> str:
    """Vuelve a registrar el XObject de un elemento en los recursos de la pagina.

    Devuelve el nombre con el que queda registrado: al reescribir el contenido,
    MuPDF puede renombrar los recursos, y si el nombre original lo ocupa ahora
    otro objeto se elige uno nuevo en vez de pisarlo.
    """
    pdf = pymupdf._as_pdf_document(doc)
    pdfpage = pymupdf._as_pdf_page(page)
    resources = mupdf.pdf_dict_get(pdfpage.obj(), pymupdf.PDF_NAME("Resources"))
    if not mupdf.pdf_is_dict(resources):
        resources = mupdf.pdf_dict_put_dict(pdfpage.obj(), pymupdf.PDF_NAME("Resources"), 2)
    xobjects = mupdf.pdf_dict_get(resources, pymupdf.PDF_NAME("XObject"))
    if not mupdf.pdf_is_dict(xobjects):
        xobjects = mupdf.pdf_dict_put_dict(resources, pymupdf.PDF_NAME("XObject"), 4)

    def taken(candidate: str) -> bool:
        current = mupdf.pdf_dict_gets(xobjects, candidate)
        return bool(getattr(current, "m_internal", None)) and mupdf.pdf_to_num(current) != xobject

    index = 0
    while taken(name):
        name = f"FolioX{index}"
        index += 1
    mupdf.pdf_dict_puts(xobjects, name, mupdf.pdf_new_indirect(pdf, xobject, 0))
    return name


def suspend(doc: pymupdf.Document, page: pymupdf.Page) -> tuple[list[int], list[Placed]]:
    """Aparta los elementos anadidos antes de una operacion que reescribe el contenido de la pagina.

    La redaccion de MuPDF funde todos los flujos en uno y descarta los recursos
    que no ve en uso; sin esto los elementos dejarian de ser editables.
    Devuelve el estado que hay que pasar despues a ``resume``.
    """
    placed = list_objects(doc, page)
    order = page.get_contents()
    if placed:
        tagged = {p.stream for p in placed}
        _set_contents(doc, page, [x for x in order if x not in tagged])
    return order, placed


def resume(doc: pymupdf.Document, page: pymupdf.Page, state: tuple[list[int], list[Placed]]) -> None:
    """Devuelve a la pagina, intactos y en su orden, los elementos apartados con ``suspend``."""
    order, placed = state
    if not placed:
        return
    by_stream = {p.stream: p for p in placed}
    _set_contents(doc, page, page.get_contents() + [x for x in order if x in by_stream])
    for item in placed:
        # Sus recursos (XObject y opacidad) pueden haberse descartado: se registran de nuevo.
        item.name = _ensure_resource(doc, page, item.name, item.xobject)
        _write_placement(doc, page, item)


def remove(doc: pymupdf.Document, page: pymupdf.Page, placed: Placed) -> int:
    """Elimina el elemento; devuelve la posicion que ocupaba entre los flujos de la pagina."""
    order = page.get_contents()
    position = order.index(placed.stream) if placed.stream in order else len(order)
    _set_contents(doc, page, [x for x in order if x != placed.stream])
    _drop_resource(page, placed.name)
    return position


def reorder(doc: pymupdf.Document, page: pymupdf.Page, placed: Placed, to_front: bool) -> None:
    order = [x for x in page.get_contents() if x != placed.stream]
    if to_front:
        order.append(placed.stream)
    else:
        # "Al fondo" entre los elementos anadidos: justo antes del primero de
        # ellos, sin pasar por debajo del contenido original de la pagina.
        tagged = {p.stream for p in list_objects(doc, page)}
        index = next((i for i, x in enumerate(order) if x in tagged), len(order))
        order.insert(index, placed.stream)
    _set_contents(doc, page, order)
