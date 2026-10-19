"""Que objetos de contenido dibuja cada pagina de un PDF.

Un PDF separa lo que una pagina *tiene a mano* (su diccionario /Resources) de
lo que de verdad *dibuja* (lo que nombran sus flujos de contenido). Muchos
programas (FPDF, TCPDF, jsPDF...) dan a todas las paginas un unico /Resources
con las imagenes de todo el documento, y otros lo cuelgan del nodo /Pages para
que se herede. De ahi sale una fuga clasica: se censura la imagen de una
pagina, se borra una pagina o se extrae otra, y la imagen original sigue en el
archivo porque el diccionario compartido la sigue nombrando, aunque ya nadie la
pinte. Quitar lo inalcanzable no basta: sigue siendo alcanzable.

Este modulo responde a dos preguntas sobre un documento abierto:

* que objetos de contenido (flujos de pagina, imagenes, Form XObject, patrones,
  glifos Type3, grupos de mascara, apariencias de anotacion) tiene *a mano* una
  pagina y cuales *usa*, y
* de un conjunto de objetos que una operacion ha retirado, cuales siguen
  colgando de algun sitio sin que ninguna pagina los dibuje. Esos se anulan
  (``release_undrawn``): la referencia que quede apunta a ``null``.

"Usa" se decide por los nombres que aparecen en el flujo de contenido. La
busqueda peca siempre del mismo lado: ante un flujo ilegible o una estructura
inesperada, el recurso cuenta como usado; y si el documento anida su contenido
mas de lo que aqui se recorre, no se anula nada (``TooDeep``). Asi un error
puede dejar de anular algo, pero nunca quita a una pagina lo que dibuja. No se
reescribe ningun flujo ni se renumera ningun objeto.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Iterable

import pymupdf
from pymupdf import mupdf

# Niveles de anidamiento (formulario dentro de formulario, patron, fuente...)
# que se recorren. Un documento real no pasa de unos pocos.
MAX_DEPTH = 24

# Un nombre en un flujo de contenido: la barra y todo lo que no sea blanco ni
# delimitador. Tambien "encuentra" nombres dentro de cadenas, comentarios o
# datos binarios; eso solo anade usos de mas, que es el lado seguro.
_NAME = re.compile(rb"/([^\x00\t\n\x0c\r ()<>\[\]{}/%]*)")
_ESCAPE = re.compile(rb"#([0-9A-Fa-f]{2})")
_BIG_STREAM = 8 * 1024 * 1024

_N = pymupdf.PDF_NAME
_get = mupdf.pdf_dict_gets
_is_dict, _is_array, _is_stream = mupdf.pdf_is_dict, mupdf.pdf_is_array, mupdf.pdf_is_stream
_is_indirect, _to_num = mupdf.pdf_is_indirect, mupdf.pdf_to_num


class TooDeep(Exception):
    """El contenido esta anidado mas de lo que se recorre: no se puede saber que se dibuja."""


def _pdf(doc: pymupdf.Document) -> Any:
    return pymupdf._as_pdf_document(doc)


def _ref(obj: Any) -> int:
    """Numero del objeto si es una referencia indirecta; si no, 0."""
    return _to_num(obj) if getattr(obj, "m_internal", None) and _is_indirect(obj) else 0


def _name(obj: Any) -> str:
    return mupdf.pdf_to_name(obj) if getattr(obj, "m_internal", None) and mupdf.pdf_is_name(obj) else ""


def reachable_objects(doc: pymupdf.Document) -> set[int]:
    """Numeros de los objetos alcanzables desde el ``trailer``."""
    resolve = mupdf.pdf_resolve_indirect
    dict_len, dict_val = mupdf.pdf_dict_len, mupdf.pdf_dict_get_val
    array_len, array_get = mupdf.pdf_array_len, mupdf.pdf_array_get
    seen: set[int] = set()
    # Pila explicita: un PDF hostil con estructuras muy profundas no puede
    # agotar la pila de llamadas de Python.
    stack = [mupdf.pdf_trailer(_pdf(doc))]
    while stack:
        obj = stack.pop()
        if _is_indirect(obj):
            num = _to_num(obj)
            if num in seen:
                continue
            seen.add(num)
            obj = resolve(obj)
        if _is_dict(obj):
            for i in range(dict_len(obj)):
                stack.append(dict_val(obj, i))
        elif _is_array(obj):
            for i in range(array_len(obj)):
                stack.append(array_get(obj, i))
    return seen


def stream_names(doc: pymupdf.Document, xref: int) -> frozenset[bytes] | None:
    """Nombres que aparecen en un flujo de contenido. ``None`` si no se puede leer (todo cuenta como usado)."""
    try:
        data = doc.xref_stream(xref)
    except Exception:  # noqa: BLE001 - flujo ilegible
        return None
    if data is None:
        return None
    if len(data) > _BIG_STREAM:  # sin montar una lista con millones de apariciones
        found = {match.group(1) for match in _NAME.finditer(data)}
    else:
        found = set(_NAME.findall(data))
    for raw in [name for name in found if b"#" in name]:
        # Un nombre puede venir con escapes (#41 es "A"): vale tambien resuelto.
        found.add(_ESCAPE.sub(lambda m: bytes([int(m.group(1), 16)]), raw))
    return frozenset(found)


def _key(obj: Any) -> bytes | None:
    """Bytes del nombre de una entrada de recursos. ``None``: no se puede comparar con seguridad (cuenta como usada)."""
    try:
        # La biblioteca entrega el nombre como texto, con los bytes que no son
        # UTF-8 valido "escapados": asi se recuperan exactamente los originales.
        return mupdf.pdf_to_name(obj).encode("utf-8", "surrogateescape")
    except Exception:  # noqa: BLE001
        return None


class ContentMap:
    """Objetos de contenido de las paginas de un documento, con memoria para lo que comparten.

    Vale mientras no se modifique el documento: despues hay que crear otro.
    ``truncated`` se pone a verdadero si alguna consulta tuvo que cortar por
    profundidad; sus resultados estan entonces incompletos y no deben usarse
    para decidir que se elimina.
    """

    def __init__(self, doc: pymupdf.Document) -> None:
        self.doc = doc
        self.pdf = _pdf(doc)
        self.truncated = False
        self._names: dict[int, frozenset[bytes] | None] = {}
        self._owners: dict[tuple[int, bool], frozenset[int]] = {}
        self._listed: dict[int, frozenset[int]] = {}
        self._cut = False  # hubo un corte (ciclo o profundidad) bajo lo que se esta calculando

    # ---------------------------------------------------------------- consulta

    def page(self, page_obj: Any, *, used_only: bool) -> frozenset[int]:
        """Objetos de contenido de una pagina: todos los que tiene a mano o solo los que dibuja."""
        out: set[int] = set()
        streams = self._content_streams(page_obj)
        out.update(streams)
        resources = mupdf.pdf_dict_get_inheritable(page_obj, _N("Resources"))
        if used_only:
            self._resources(resources, self._union(streams), out, 0, set(), True)
        else:
            shared = _ref(resources)
            if shared and shared in self._listed:
                out.update(self._listed[shared])
            else:
                listed: set[int] = set()
                self._cut = False
                self._resources(resources, None, listed, 0, set(), False)
                if shared and not self._cut:
                    self._listed[shared] = frozenset(listed)
                out.update(listed)
        self._annotations(page_obj, out, used_only)
        return frozenset(out)

    def stream(self, xref: int, *, used_only: bool) -> frozenset[int]:
        """Un flujo de contenido con recursos propios (un Form XObject, por ejemplo) y todo lo que arrastra."""
        return self._owner(mupdf.pdf_new_indirect(self.pdf, xref, 0), None, used_only, 0, set())

    # ------------------------------------------------------------ recorrido

    def _content_streams(self, page_obj: Any) -> list[int]:
        contents = _get(page_obj, "Contents")
        if _is_array(contents):
            found = [_to_num(mupdf.pdf_array_get(contents, i)) for i in range(mupdf.pdf_array_len(contents))]
        elif getattr(contents, "m_internal", None):
            found = [_to_num(contents)]
        else:
            found = []
        return [xref for xref in found if xref > 0]

    def _stream_names(self, xref: int) -> frozenset[bytes] | None:
        if xref not in self._names:
            self._names[xref] = stream_names(self.doc, xref)
        return self._names[xref]

    def _union(self, streams: Iterable[int]) -> frozenset[bytes] | None:
        names: set[bytes] = set()
        for xref in streams:
            found = self._stream_names(xref)
            if found is None:
                return None  # un flujo ilegible: no se puede descartar ningun uso
            names |= found
        return frozenset(names)

    def _entries(self, container: Any, names: frozenset[bytes] | None) -> Iterable[Any]:
        """Valores de un subdiccionario de recursos cuyo nombre se usa (todos si ``names`` es ``None``)."""
        if not _is_dict(container):
            return
        for i in range(mupdf.pdf_dict_len(container)):
            if names is not None:
                key = _key(mupdf.pdf_dict_get_key(container, i))
                if key is not None and key not in names:
                    continue
            yield mupdf.pdf_dict_get_val(container, i)

    def _resources(
        self, res: Any, names: frozenset[bytes] | None, out: set[int], depth: int, stack: set[int], used_only: bool
    ) -> None:
        if res is None or not _is_dict(res):
            return
        if depth > MAX_DEPTH:
            self._cut = self.truncated = True
            return
        for obj in self._entries(_get(res, "XObject"), names):
            self._xobject(obj, res, out, depth, stack, used_only)
        for obj in self._entries(_get(res, "Pattern"), names):
            self._pattern(obj, res, out, depth, stack, used_only)
        for obj in self._entries(_get(res, "Shading"), names):
            self._stream(obj, out)
        for obj in self._entries(_get(res, "ExtGState"), names):
            self._gstate(obj, res, out, depth, stack, used_only)
        for obj in self._entries(_get(res, "Font"), names):
            self._font(obj, res, out, depth, stack, used_only)

    def _stream(self, obj: Any, out: set[int]) -> None:
        num = _ref(obj)
        if num and _is_stream(obj):
            out.add(num)

    def _xobject(self, obj: Any, res: Any, out: set[int], depth: int, stack: set[int], used_only: bool) -> None:
        num = _ref(obj)
        if not num or not _is_dict(obj):
            return
        subtype = _name(_get(obj, "Subtype"))
        if subtype == "Form":
            out.update(self._owner(obj, res, used_only, depth, stack))
            return
        out.add(num)
        if subtype == "Image":
            # Las mascaras llevan informacion propia (en los escaneados con
            # capas, el texto esta en la mascara).
            for key in ("SMask", "Mask"):
                self._stream(_get(obj, key), out)
            alternates = _get(obj, "Alternates")
            if _is_array(alternates):
                for i in range(mupdf.pdf_array_len(alternates)):
                    self._stream(_get(mupdf.pdf_array_get(alternates, i), "Image"), out)

    def _pattern(self, obj: Any, res: Any, out: set[int], depth: int, stack: set[int], used_only: bool) -> None:
        if not _is_dict(obj):
            return
        if _is_stream(obj):  # patron de mosaico: un flujo de contenido con sus recursos
            out.update(self._owner(obj, res, used_only, depth, stack))
            return
        self._stream(_get(obj, "Shading"), out)
        self._gstate(_get(obj, "ExtGState"), res, out, depth, stack, used_only)

    def _gstate(self, obj: Any, res: Any, out: set[int], depth: int, stack: set[int], used_only: bool) -> None:
        if not _is_dict(obj):
            return
        mask = _get(obj, "SMask")
        if _is_dict(mask):
            group = _get(mask, "G")
            if _is_dict(group):
                out.update(self._owner(group, res, used_only, depth, stack))
        font = _get(obj, "Font")
        if _is_array(font) and mupdf.pdf_array_len(font) > 0:
            self._font(mupdf.pdf_array_get(font, 0), res, out, depth, stack, used_only)

    def _font(self, obj: Any, res: Any, out: set[int], depth: int, stack: set[int], used_only: bool) -> None:
        if not _is_dict(obj) or _name(_get(obj, "Subtype")) != "Type3":
            return
        procs = _get(obj, "CharProcs")
        if not _is_dict(procs):
            return
        num = _ref(obj)
        if num and num in stack:
            self._cut = True  # ciclo: ya se esta recorriendo
            return
        streams = []
        for i in range(mupdf.pdf_dict_len(procs)):
            proc = _ref(mupdf.pdf_dict_get_val(procs, i))
            if proc:
                streams.append(proc)
        out.update(streams)
        own = _get(obj, "Resources")
        if num:
            stack.add(num)
        # Sin recursos propios, los glifos usan los de la pagina donde se dibujan.
        names = self._union(streams) if used_only else None
        self._resources(own if _is_dict(own) else res, names, out, depth + 1, stack, used_only)
        if num:
            stack.discard(num)

    def _owner(self, obj: Any, parent_res: Any, used_only: bool, depth: int, stack: set[int]) -> frozenset[int]:
        """Un flujo de contenido con recursos (Form XObject, patron de mosaico, grupo de mascara) y lo que arrastra."""
        num = _ref(obj)
        own = _get(obj, "Resources")
        has_own = _is_dict(own)
        memo = (num, used_only)
        if num and has_own and memo in self._owners:
            return self._owners[memo]
        if num and num in stack:
            self._cut = True  # ciclo: el objeto ya esta contado mas arriba
            return frozenset((num,))
        out: set[int] = set()
        if num:
            out.add(num)
            stack.add(num)
        names = (self._stream_names(num) if num else None) if used_only else None
        outer, self._cut = self._cut, False
        # Sin recursos propios (forma antigua, aun valida), usa los de quien lo dibuja.
        self._resources(own if has_own else parent_res, names, out, depth + 1, stack, used_only)
        complete = not self._cut
        self._cut = self._cut or outer
        if num:
            stack.discard(num)
        result = frozenset(out)
        if num and has_own and complete:
            self._owners[memo] = result
        return result

    def _annotations(self, page_obj: Any, out: set[int], used_only: bool) -> None:
        """Las apariencias de las anotaciones y campos tambien son contenido que la pagina muestra."""
        annots = _get(page_obj, "Annots")
        if not _is_array(annots):
            return
        for i in range(mupdf.pdf_array_len(annots)):
            annot = mupdf.pdf_array_get(annots, i)
            if not _is_dict(annot):
                continue
            looks = _get(annot, "AP")
            if _is_dict(looks):
                for j in range(mupdf.pdf_dict_len(looks)):
                    self._appearance(mupdf.pdf_dict_get_val(looks, j), out, used_only)
            marks = _get(annot, "MK")
            if _is_dict(marks):
                for key in ("I", "RI", "IX"):
                    self._appearance(_get(marks, key), out, used_only)

    def _appearance(self, obj: Any, out: set[int], used_only: bool) -> None:
        if _is_stream(obj):
            out.update(self._owner(obj, None, used_only, 0, set()))
        elif _is_dict(obj):  # una apariencia por estado (casillas, botones de opcion)
            for i in range(mupdf.pdf_dict_len(obj)):
                value = mupdf.pdf_dict_get_val(obj, i)
                if _is_stream(value):
                    out.update(self._owner(value, None, used_only, 0, set()))


def drawn_by_pages(
    doc: pymupdf.Document, objects: Iterable[int], *, tick: Callable[[], None] | None = None
) -> dict[int, list[int]]:
    """De ``objects``, cuales dibuja alguna pagina del documento: ``{xref: [indices de pagina]}``.

    Lanza ``TooDeep`` si no se ha podido recorrer todo el contenido: entonces
    no se sabe si los que faltan se dibujan o no.
    """
    wanted = set(objects)
    drawn: dict[int, list[int]] = {}
    if not wanted:
        return drawn
    pdf = _pdf(doc)
    cmap = ContentMap(doc)
    # Solo lectura: MuPDF descarta su indice de paginas en cuanto se modifica
    # un objeto, y buscar cada pagina volveria a recorrer el arbol entero.
    for index in range(doc.page_count):
        if tick is not None and index % 64 == 0:
            tick()
        page_obj = mupdf.pdf_lookup_page_obj(pdf, index)
        candidates = wanted & cmap.page(page_obj, used_only=False)
        if candidates:
            for xref in candidates & cmap.page(page_obj, used_only=True):
                drawn.setdefault(xref, []).append(index)
        if cmap.truncated:
            raise TooDeep
    return drawn


def release_undrawn(
    doc: pymupdf.Document, retired: Iterable[int], *, tick: Callable[[], None] | None = None
) -> dict[int, list[int]]:
    """Anula los objetos retirados que siguen colgando de algun sitio sin que ninguna pagina los dibuje.

    ``retired`` son objetos de contenido que una operacion ha dejado de usar
    (la imagen original de una zona censurada, el contenido de una pagina
    borrada). Los que ya nadie referencia no hacen falta aqui: los libera la
    purga de huerfanos. Los que siguen alcanzables se reparten en dos:

    * alguna pagina los dibuja de verdad: se conservan y se devuelven, con las
      paginas que los dibujan, para que quien llama pueda avisar;
    * solo figuran en un diccionario de recursos (o en otra estructura) sin que
      nadie los pinte: se sustituyen por ``null``. Su contenido deja de estar
      en el documento y la referencia que quede ya no lleva a nada.

    Lanza ``TooDeep`` (sin haber cambiado nada) si no se puede saber que
    dibujan las paginas.
    """
    pending = set(retired)
    if not pending:
        return {}
    leaked = pending & reachable_objects(doc)
    if not leaked:
        return {}
    drawn = drawn_by_pages(doc, leaked, tick=tick)
    for xref in sorted(leaked.difference(drawn)):
        doc.update_object(xref, "null")
    return drawn


def release_unlisted(doc: pymupdf.Document, pages: Iterable[Any], *, floor: int = 0) -> int:
    """Anula lo que las paginas dadas tienen a mano pero ninguna de ellas dibuja.

    Es para paginas recien copiadas a un documento (extraer, dividir, insertar
    solo algunas): su diccionario de recursos puede nombrar las imagenes de
    todas las paginas del original, que viajarian dentro del archivo sin verse.
    Solo se tocan objetos con numero ``floor`` o mayor (los recien creados).
    Lanza ``TooDeep`` (sin haber cambiado nada) si no se puede saber que
    dibujan.
    """
    cmap = ContentMap(doc)
    listed: set[int] = set()
    used: set[int] = set()
    for page_obj in pages:  # solo lectura; las modificaciones van despues
        listed |= cmap.page(page_obj, used_only=False)
        used |= cmap.page(page_obj, used_only=True)
    if cmap.truncated:
        raise TooDeep
    spare = sorted(xref for xref in listed - used if xref >= floor)
    for xref in spare:
        doc.update_object(xref, "null")
    return len(spare)
