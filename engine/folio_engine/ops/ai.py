"""IA local: buscar por significado, preguntar, resumir, transformar texto y datos personales.

Todo pasa por ``ctx.engine.host.ai`` (en el motor aislado, el intermediario
decide si se permite; ver aibroker.py). Este proceso no puede cargar NumPy
(ver __main__): los vectores se manejan con ``array``.

El texto de los documentos es no fiable: antes de darselo al modelo se quitan
las frases que parecen ordenes para una IA y las citas de la respuesta se
comprueban con las paginas reales (ver ai_prompts.py). La respuesta se devuelve
como texto plano; la interfaz nunca la interpreta como HTML (OWASP LLM05).
"""

from __future__ import annotations

import re
from array import array
from typing import Annotated, Any, Literal

import pymupdf
from pydantic import Field, StringConstraints

from .. import ai_prompts, redaction
from ..aibroker import unavailable
from ..errors import Cancelled, EngineError
from ..geometry import Geom
from ..i18n import _, language
from ..models import DocParams, PageId, PageParams
from ..server import Ctx, method
from ..session import DocSession
from .annots import _finish, _result, annot_id

_TEXT_FLAGS = pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_MEDIABOX_CLIP | pymupdf.TEXT_DEHYPHENATE
MAX_PAGE_CHARS = 60_000
# Trozos de una frase: en la prueba de concepto, con trozos de pagina el vector se
# diluia ("fianza" junto a "gastos") y la busqueda fallaba; por frases acertaba.
CHUNK_CHARS = 400
MIN_CHUNK = 25  # frases mas cortas (titulos, numeros) se juntan con la siguiente
MAX_CHUNKS = 12_000  # para mas, se indexa el principio del documento
EMBED_BATCH = 32
SMALL_DOC = 12_000  # por debajo, al preguntar se pasa el documento entero
ASK_CONTEXT = 9_000
SUMMARY_PART = 10_000
MAX_SUMMARY_PARTS = 24
# Sin grafica, leer el prompt es lo lento (unos 40 tokens/s): se pasa menos texto
# al preguntar, y un documento largo se resume a partir de sus frases mas
# representativas en una sola pasada en vez de por partes (minutos, no media hora).
ASK_CONTEXT_CPU = 6_000
SUMMARY_CPU = 9_000
SUMMARY_SECTIONS = 12
MAX_CANDIDATES = 2_000
NER_MIN_SCORE = 0.6
POLL_WAIT = 0.3

Question = Annotated[str, StringConstraints(min_length=1, max_length=2_000)]
Snippet = Annotated[str, StringConstraints(min_length=1, max_length=8_000)]


# ------------------------------------------------------------------ utilidades


def _pages_text(session: DocSession) -> list[tuple[str, int, str]]:
    """(id de pagina, numero para el usuario, texto) de cada pagina."""
    out: list[tuple[str, int, str]] = []
    for n, pid in enumerate(session.page_ids, start=1):
        text = session.page(pid).get_text("text", flags=_TEXT_FLAGS)[:MAX_PAGE_CHARS]
        out.append((pid, n, re.sub(r"[ \t]+", " ", text).strip()))
    return out


_SENTENCES = re.compile(r"(?<=[.!?;])\s+|\n+")


def _chunks(pages: list[tuple[str, int, str]]) -> list[tuple[str, int, str]]:
    """Trozos de una frase (de unos 400 caracteres como mucho) que no mezclan paginas."""
    out: list[tuple[str, int, str]] = []
    for pid, number, text in pages:
        pending = ""
        for piece in _SENTENCES.split(text):
            piece = f"{pending} {piece.strip()}".strip()
            pending = ""
            if not piece:
                continue
            if len(piece) < MIN_CHUNK:
                pending = piece
                continue
            while len(piece) > CHUNK_CHARS:
                cut = piece.rfind(" ", 0, CHUNK_CHARS)
                cut = cut if cut > CHUNK_CHARS // 2 else CHUNK_CHARS
                out.append((pid, number, piece[:cut].strip()))
                piece = piece[cut:].strip()
            if piece:
                out.append((pid, number, piece))
        if pending:
            out.append((pid, number, pending))
        if len(out) >= MAX_CHUNKS:
            return out[:MAX_CHUNKS]
    return out


def _embed(ctx: Ctx, texts: list[str]) -> list[array]:
    vectors: list[array] = []
    for start in range(0, len(texts), EMBED_BATCH):
        ctx.check_cancel()
        batch = [t[:8_000] for t in texts[start : start + EMBED_BATCH]]
        reply, data = ctx.engine.host.ai("embed", {"texts": batch})
        dims = reply.get("dims")
        count = reply.get("count")
        if not isinstance(dims, int) or not isinstance(count, int) or count != len(batch) or len(data) != count * dims * 4:
            raise EngineError("ai_failed", _("La IA no ha podido hacerlo."))
        flat = array("f")
        flat.frombytes(data)
        vectors.extend(flat[i * dims : (i + 1) * dims] for i in range(count))
    return vectors


def _dot(a: array, b: array) -> float:
    return sum(x * y for x, y in zip(a, b))


def _index(ctx: Ctx, session: DocSession) -> tuple[list[tuple[str, int, str]], list[array]]:
    """Trozos del documento y sus vectores; se guardan mientras el documento no cambie."""
    revision = session.revisions[session.cur] if session.revisions else None
    cached = getattr(session, "_ai_index", None)
    if cached is not None and cached[0] is revision:
        return cached[1], cached[2]
    chunks = _chunks(_pages_text(session))
    vectors: list[array] = []
    for start in range(0, len(chunks), EMBED_BATCH * 4):
        ctx.progress(start, max(len(chunks), 1), _("Preparando la búsqueda…"))
        vectors.extend(_embed(ctx, [c[2] for c in chunks[start : start + EMBED_BATCH * 4]]))
    session._ai_index = (revision, chunks, vectors)  # type: ignore[attr-defined]
    return chunks, vectors


_WORD = re.compile(r"\w{3,}", re.UNICODE)


def _keyword_rank(chunks: list[tuple[str, int, str]], query: str) -> list[int]:
    """Sin modelo de vectores: los trozos que comparten mas palabras con la pregunta."""
    words = {w.lower() for w in _WORD.findall(query)}
    scored = []
    for i, (_pid, _n, text) in enumerate(chunks):
        found = {w.lower() for w in _WORD.findall(text)}
        scored.append((len(words & found), -i))
    scored.sort(reverse=True)
    return [-i for score, i in scored if score > 0]


def _tag(session: DocSession) -> str:
    """Codigo de las marcas del documento en el prompt: uno por documento abierto (ver ai_prompts)."""
    tag = getattr(session, "_ai_tag", None)
    if tag is None:
        tag = ai_prompts.new_tag()
        session._ai_tag = tag  # type: ignore[attr-defined]
    return tag


def _gpu(ctx: Ctx) -> bool:
    """Si la IA va con la grafica (si no se sabe, como si fuera)."""
    try:
        reply, _payload = ctx.engine.host.ai("device", {})
    except EngineError as exc:
        if exc.code != "ai_unavailable":
            raise
        return True
    return reply.get("gpu") is not False


def _generate(ctx: Ctx, messages: list[dict[str, str]], *, max_tokens: int, watermark: bool, message: str,
              stream: bool = True, done: float = 0, total: float = 1, reuse: str | None = None) -> str:
    """Pide una respuesta al modelo de lenguaje y la va enviando a la interfaz mientras se escribe.

    ``reuse`` (ai_prompts.doc_end): hasta ahi, el proceso de IA puede aprovechar lo
    que ya leyo en la peticion anterior.
    """
    host = ctx.engine.host
    request: dict[str, Any] = {"messages": messages, "maxTokens": max_tokens, "watermark": watermark}
    if reuse:
        request["reuse"] = reuse
    host.ai("start", request)
    parts: list[str] = []
    reason = ""
    try:
        while True:
            reply, _payload = host.ai("poll", {"wait": POLL_WAIT})
            piece = reply.get("text")
            if isinstance(piece, str) and piece:
                parts.append(piece)
            current = ai_prompts.strip_thinking("".join(parts))
            ctx.progress(done, total, message, text=current if stream else None)
            if reply.get("done"):
                reason = str(reply.get("reason", ""))
                break
    except Cancelled:
        try:
            host.ai("stop", {})
        finally:
            raise
    if reason == "missing":
        raise unavailable()
    if reason == "error":
        raise EngineError("ai_failed", _("La IA no ha podido hacerlo."))
    return ai_prompts.strip_thinking("".join(parts))


def _by_page(chunks: list[tuple[str, int, str]], picked: set[int]) -> list[tuple[str, int, str]]:
    """Los trozos elegidos, juntos por pagina y en el orden del documento."""
    merged: dict[tuple[str, int], list[str]] = {}
    for j in sorted(picked):
        merged.setdefault((chunks[j][0], chunks[j][1]), []).append(chunks[j][2])
    return [(pid, n, " ".join(parts)) for (pid, n), parts in merged.items()]


def _representative(ctx: Ctx, session: DocSession, pages: list[tuple[str, int, str]]) -> list[tuple[str, int, str]]:
    """Las frases mas representativas de cada parte del documento, hasta SUMMARY_CPU caracteres.

    El documento se divide en partes seguidas y de cada una se eligen las frases
    mas cercanas a su centro (la media de sus vectores): asi el resumen abarca el
    documento entero. Sin modelo de vectores, frases repartidas a lo largo de cada parte.
    """
    try:
        chunks, vectors = _index(ctx, session)
    except EngineError as exc:
        if exc.code != "ai_unavailable":
            raise
        chunks, vectors = _chunks(pages), []
    if not chunks:
        return []
    sections = min(SUMMARY_SECTIONS, len(chunks))
    share = SUMMARY_CPU // sections
    picked: set[int] = set()
    for k in range(sections):
        members = list(range(k * len(chunks) // sections, (k + 1) * len(chunks) // sections))
        if vectors:
            dims = len(vectors[members[0]])
            center = array("f", [0.0] * dims)
            for i in members:
                for d, x in enumerate(vectors[i]):
                    center[d] += x
            order = sorted(members, key=lambda i: _dot(vectors[i], center), reverse=True)
        else:
            step = max(1, len(members) // 4)
            order = members[::step] + [i for i in members if (i - members[0]) % step]
        used = 0
        for i in order:
            if used + len(chunks[i][2]) > share and used:
                continue
            picked.add(i)
            used += len(chunks[i][2])
            if used >= share * 0.9:
                break
    return _by_page(chunks, picked)


def _fragments(items: list[tuple[str, int, str]], lang: str) -> tuple[list[ai_prompts.Fragment], int]:
    out: list[ai_prompts.Fragment] = []
    hits = 0
    for _pid, number, text in items:
        clean, found = ai_prompts.filter_injections(text, lang)
        hits += found
        out.append(ai_prompts.Fragment(number, clean))
    return out, hits


# -------------------------------------------------------------- buscar


class SearchParams(DocParams):
    query: Question
    k: Annotated[int, Field(ge=1, le=20)] = 8


@method("ai.search", SearchParams)
def ai_search(ctx: Ctx, p: SearchParams) -> dict[str, Any]:
    """Los trozos del documento que mas se parecen en significado a lo buscado."""
    session = ctx.engine.session(p.doc)
    chunks, vectors = _index(ctx, session)
    if not chunks:
        return {"results": []}
    query = _embed(ctx, [p.query])[0]
    ranked = sorted(range(len(chunks)), key=lambda i: _dot(vectors[i], query), reverse=True)[: p.k]
    results = []
    for i in ranked:
        pid, number, text = chunks[i]
        page = session.page(pid)
        geom = Geom(page)
        # Para resaltar: las primeras palabras del trozo tal como estan en la pagina.
        phrase = " ".join(text.split()[:8])
        rects = [geom.rect_d(r) for r in redaction.search_page(page, phrase)[:4]] if phrase else []
        results.append({"page": pid, "number": number, "text": text[:400], "score": round(_dot(vectors[i], query), 4), "rects": rects})
    return {"results": results}


# ------------------------------------------------------------- preguntar


class AskParams(DocParams):
    question: Question


@method("ai.ask", AskParams)
def ai_ask(ctx: Ctx, p: AskParams) -> dict[str, Any]:
    session = ctx.engine.session(p.doc)
    lang = language()
    pages = _pages_text(session)
    total_chars = sum(len(t) for _pid, _n, t in pages)
    if total_chars <= SMALL_DOC:
        chosen = [(pid, n, t) for pid, n, t in pages if t]
    else:
        budget = ASK_CONTEXT if _gpu(ctx) else ASK_CONTEXT_CPU
        chunks: list[tuple[str, int, str]]
        try:
            chunks, vectors = _index(ctx, session)
            query = _embed(ctx, [p.question])[0]
            order = sorted(range(len(chunks)), key=lambda i: _dot(vectors[i], query), reverse=True)
        except EngineError as exc:
            if exc.code != "ai_unavailable":
                raise
            chunks = _chunks(pages)
            order = _keyword_rank(chunks, p.question) or list(range(len(chunks)))
        # Las frases mas parecidas y sus vecinas de la misma pagina (dan el contexto).
        picked: set[int] = set()
        used = 0
        for i in order:
            for j in (i, i - 1, i + 1):
                if 0 <= j < len(chunks) and j not in picked and chunks[j][0] == chunks[i][0]:
                    if used + len(chunks[j][2]) > budget:
                        break
                    picked.add(j)
                    used += len(chunks[j][2])
            if used >= budget * 0.95:
                break
        chosen = _by_page(chunks, picked)
    if not chosen:
        return {"text": _("El documento no tiene texto que la IA pueda leer. Prueba antes con «Reconocer texto (OCR)»."), "pages": [], "injection": 0}
    fragments, hits = _fragments(chosen, lang)
    tag = _tag(session)
    text = _generate(ctx, ai_prompts.ask(lang, fragments, p.question, tag), max_tokens=700, watermark=True,
                     message=_("Pensando…"), reuse=ai_prompts.doc_end(tag))
    allowed = {f.page for f in fragments}
    text, cited, removed = ai_prompts.check_citations(text, allowed)
    numbers = {n: pid for pid, n, _t in pages}
    return {"text": text, "pages": [{"number": n, "page": numbers[n]} for n in cited if n in numbers], "injection": hits, "removedCitations": removed}


# --------------------------------------------------------------- resumir


class SummaryParams(DocParams):
    length: Literal["short", "long"] = "short"


@method("ai.summarize", SummaryParams)
def ai_summarize(ctx: Ctx, p: SummaryParams) -> dict[str, Any]:
    session = ctx.engine.session(p.doc)
    lang = language()
    pages = [item for item in _pages_text(session) if item[2]]
    if not pages:
        return {"text": _("El documento no tiene texto que la IA pueda leer. Prueba antes con «Reconocer texto (OCR)»."), "pages": [], "injection": 0}
    # Partes de unos 10 000 caracteres (paginas completas siempre que se pueda).
    parts: list[list[tuple[str, int, str]]] = [[]]
    size = 0
    for item in pages:
        text = item[2][:SUMMARY_PART]
        if parts[-1] and size + len(text) > SUMMARY_PART:
            parts.append([])
            size = 0
        parts[-1].append((item[0], item[1], text))
        size += len(text)
    partial = parts[:MAX_SUMMARY_PARTS] if len(parts) > MAX_SUMMARY_PARTS else parts
    covered = partial[-1][-1][1]
    hits = 0
    condensed = len(parts) > 1 and not _gpu(ctx)
    if condensed:
        partial = [_representative(ctx, session, pages)]
        covered = len(session.page_ids)  # abarca todo el documento, por extractos
    if len(partial) == 1:
        fragments, hits = _fragments(partial[0], lang)
        tag = _tag(session)
        text = _generate(ctx, ai_prompts.summary(lang, fragments, p.length, tag), max_tokens=900 if p.length == "long" else 450,
                         watermark=True, message=_("Resumiendo…"), reuse=ai_prompts.doc_end(tag))
    else:
        summaries: list[str] = []
        for n, part in enumerate(partial):
            fragments, found = _fragments(part, lang)
            hits += found
            message = _("Resumiendo las páginas {first}–{last}…", first=part[0][1], last=part[-1][1])
            summaries.append(_generate(ctx, ai_prompts.summary(lang, fragments, "part"), max_tokens=350, watermark=False,
                                       message=message, stream=False, done=n, total=len(partial) + 1))
        text = _generate(ctx, ai_prompts.merge(lang, summaries), max_tokens=900 if p.length == "long" else 500,
                         watermark=True, message=_("Uniendo el resumen…"), done=len(partial), total=len(partial) + 1)
    allowed = {n for part in partial for _pid, n, _t in part}
    text, cited, removed = ai_prompts.check_citations(text, allowed)
    numbers = {n: pid for pid, n, _t in pages}
    return {
        "text": text,
        "pages": [{"number": n, "page": numbers[n]} for n in cited if n in numbers],
        "injection": hits,
        "removedCitations": removed,
        "coveredPages": covered,
        "totalPages": len(session.page_ids),
        "condensed": condensed,
    }


# --------------------------------------------------- traducir y reescribir


class TransformBody(DocParams):
    text: Snippet
    action: Literal["translate", "rewrite", "formal", "simple", "shorter", "fix"]
    target: Literal["es", "en"] = "en"


@method("ai.transform", TransformBody)
def ai_transform(ctx: Ctx, p: TransformBody) -> dict[str, Any]:
    """Texto que el usuario ha seleccionado o escrito: traducirlo, reescribirlo o corregirlo."""
    ctx.engine.session(p.doc)  # el documento existe (la operacion va asociada a el)
    lang = language()
    clean, hits = ai_prompts.filter_injections(p.text, lang)
    messages = ai_prompts.transform(lang, p.action, clean, p.target)
    text = _generate(ctx, messages, max_tokens=min(2048, 200 + len(p.text) // 2), watermark=p.action in ai_prompts.WATERMARKED,
                     message=_("Escribiendo…"))
    return {"text": text, "injection": hits, "marked": p.action in ai_prompts.WATERMARKED}


# ------------------------------------------------- guardar como nota


class NoteParams(PageParams):
    text: Annotated[str, StringConstraints(min_length=1, max_length=20_000)]
    point: tuple[float, float] | None = None


@method("ai.addNote", NoteParams)
def ai_add_note(ctx: Ctx, p: NoteParams) -> dict[str, Any]:
    """Guarda un resultado de la IA como nota del PDF, marcada como generada por IA.

    Marca legible por maquina (Reglamento de IA, art. 50.2): el asunto de la
    nota y una clave propia en su diccionario (/FolioAI) dicen que el contenido
    lo genero la IA de Folio y cuando. El texto, ademas, lleva la marca de agua
    estadistica si salio de un resumen o una respuesta.
    """
    session = ctx.engine.session(p.doc)
    session.require("annotate")
    with session.edit(_("Añadir nota de la IA")) as tx:
        page = session.page(p.page)
        geom = Geom(page)
        point = geom.point_u(p.point if p.point is not None else (24.0, 24.0))
        annot = page.add_text_annot(point, p.text, icon="Comment")
        _finish(annot, stroke=(0.31, 0.36, 0.84), fill=None, width=None, opacity=1.0, contents=p.text,
                author=_("IA de Folio"))
        annot.set_info(subject=_("Generado con IA (Folio)"))
        annot.update()
        stamp = pymupdf.get_pdf_now()
        session.doc.xref_set_key(annot.xref, "FolioAI", f"<< /Generator (Folio) /Type /AIGeneratedContent /Date ({stamp}) >>")
        ident = annot_id(annot)
        tx.touch(p.page)
    return _result(session, p.page, ident)


# ------------------------------------------------- datos personales


class CandidatesParams(DocParams):
    pages: Annotated[list[PageId], Field(min_length=1, max_length=10_000)] | None = None
    useAi: bool = True


# Entidades del modelo que son datos personales, y como se agrupan.
_NER_KIND = {
    "GIVEN_NAME": "name", "SURNAME": "name",
    "STREET_ADDRESS": "address", "STREET_NAME": "address", "BUILDING_NUMBER": "address",
    "SECONDARY_ADDRESS": "address", "ZIP_CODE": "address",
    "DATE_OF_BIRTH": "birthdate",
    "GOVERNMENT_ID": "idnumber", "PASSPORT": "idnumber", "DRIVERS_LICENSE": "idnumber", "TAX_ID": "idnumber", "SSN": "idnumber",
    "IBAN": "iban", "ACCOUNT_NUMBER": "bank", "ROUTING_NUMBER": "bank", "SWIFT_BIC": "bank",
    "CREDIT_DEBIT_CARD": "card", "CVV": "card", "PIN": "secret", "PASSWORD": "secret", "API_KEY": "secret",
    "EMAIL": "email", "PHONE": "phone", "FAX_NUMBER": "phone",
    "LICENSE_PLATE": "plate", "MEDICAL_RECORD_NUMBER": "medical",
    "CUSTOMER_ID": "account", "EMPLOYEE_ID": "account", "USERNAME": "account",
}
_CITY = "CITY"


def _lines(page: pymupdf.Page) -> list[list[list[tuple[str, Any]]]]:
    """Bloques -> lineas -> caracteres (letra, caja) de la pagina."""
    textpage = page.get_textpage(flags=pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_MEDIABOX_CLIP)
    try:
        raw = textpage.extractRAWDICT()
    finally:
        del textpage
    blocks = []
    for block in raw.get("blocks", ()):
        if block.get("type", 0) != 0:
            continue
        lines = []
        for line in block.get("lines", ()):
            chars = [(c["c"], c["bbox"]) for span in line.get("spans", ()) for c in span.get("chars", ()) if len(c.get("c", "")) == 1]
            if chars:
                lines.append(chars[:20_000])
        if lines:
            blocks.append(lines)
    return blocks


def _rows(boxes: list[Any]) -> list[tuple[float, float, float, float]]:
    """Una caja por linea (los caracteres de una misma linea se unen), en U."""
    rows: list[list[Any]] = []
    for box in boxes:
        if rows and abs(rows[-1][-1][1] - box[1]) < 2 and abs(rows[-1][-1][3] - box[3]) < 2:
            rows[-1].append(box)
        else:
            rows.append([box])
    return [(min(b[0] for b in row), min(b[1] for b in row), max(b[2] for b in row), max(b[3] for b in row)) for row in rows]


def _overlaps(rows: list[tuple[float, float, float, float]], taken: list[tuple[float, float, float, float]]) -> bool:
    return any(r[0] < t[2] and t[0] < r[2] and r[1] < t[3] and t[1] < r[3] for r in rows for t in taken)


@method("redact.candidates", CandidatesParams)
def redact_candidates(ctx: Ctx, p: CandidatesParams) -> dict[str, Any]:
    """Datos personales que conviene censurar: no marca nada, solo los propone.

    Primero las reglas (DNI, NIE, IBAN, tarjetas, correos y telefonos, con su
    digito de control). Con la IA activada, ademas el modelo de entidades:
    nombres, direcciones, fechas de nacimiento, numeros de documentos...
    """
    session = ctx.engine.session(p.doc)
    ids = list(dict.fromkeys(p.pages)) if p.pages is not None else list(session.page_ids)
    for pid in ids:
        session.page_index(pid)
    candidates: list[dict[str, Any]] = []
    used_ai = p.useAi
    truncated = False
    for n, pid in enumerate(ids):
        ctx.progress(n, len(ids), _("Buscando datos personales…"))
        page = session.page(pid)
        geom = Geom(page)
        blocks = _lines(page)
        taken: list[tuple[float, float, float, float]] = []
        # 1. Reglas, linea a linea (como redact.find).
        for lines in blocks:
            for chars in lines:
                text = "".join(c for c, _b in chars)
                for kind, finder in redaction.FINDERS.items():
                    for start, end in finder(text):
                        boxes = [chars[i][1] for i in range(start, end) if not text[i].isspace()]
                        if not boxes:
                            continue
                        rows = _rows(boxes)
                        candidates.append({"page": pid, "kind": kind, "text": text[start:end].strip()[:200],
                                           "rects": [geom.rect_d(r) for r in rows], "source": "rule", "score": 1.0})
                        taken.extend(rows)
        # 2. El modelo de entidades, bloque a bloque (necesita el contexto de la frase).
        if used_ai:
            texts: list[str] = []
            maps: list[list[Any]] = []
            for lines in blocks:
                text_parts: list[str] = []
                mapping: list[Any] = []
                for chars in lines:
                    if text_parts:
                        text_parts.append(" ")
                        mapping.append(None)
                    text_parts.extend(c for c, _b in chars)
                    mapping.extend(b for _c, b in chars)
                block_text = "".join(text_parts)[:8_000]
                if block_text.strip():
                    texts.append(block_text)
                    maps.append(mapping[:8_000])
            try:
                found: list[Any] = []
                for start in range(0, len(texts), 32):
                    reply, _data = ctx.engine.host.ai("ner", {"texts": texts[start : start + 32]})
                    entities = reply.get("entities")
                    if not isinstance(entities, list):
                        raise EngineError("ai_failed", _("La IA no ha podido hacerlo."))
                    found.extend(entities)
            except EngineError as exc:
                if exc.code not in ("ai_unavailable",):
                    raise
                used_ai = False
                found = []
            for text, mapping, spans in zip(texts, maps, found):
                groups: list[list[Any]] = []
                for span in spans if isinstance(spans, list) else []:
                    if not (isinstance(span, list) and len(span) == 4):
                        continue
                    label, s, e, score = span
                    if not (isinstance(s, int) and isinstance(e, int) and 0 <= s < e <= len(text)):
                        continue
                    kind = _NER_KIND.get(label) or ("city" if label == _CITY else None)
                    if kind is None or not isinstance(score, (int, float)) or score < NER_MIN_SCORE:
                        continue
                    # Partes contiguas de un nombre o de una direccion se juntan (la ciudad solo junto a una direccion).
                    if groups and s - groups[-1][2] <= 3 and (groups[-1][0] == kind or (groups[-1][0] == "address" and kind == "city")):
                        groups[-1][2] = e
                        groups[-1][3] = min(groups[-1][3], score)
                    elif kind != "city":
                        groups.append([kind, s, e, float(score)])
                for kind, s, e, score in groups:
                    boxes = [mapping[i] for i in range(s, e) if i < len(mapping) and mapping[i] is not None and not text[i].isspace()]
                    if not boxes:
                        continue
                    rows = _rows(boxes)
                    # Lo que ya encontraron las reglas no se repite (se compara linea a linea).
                    if _overlaps(rows, taken):
                        continue
                    candidates.append({"page": pid, "kind": kind, "text": text[s:e].strip()[:200],
                                       "rects": [geom.rect_d(r) for r in rows], "source": "ai", "score": round(score, 3)})
        if len(candidates) >= MAX_CANDIDATES:
            candidates = candidates[:MAX_CANDIDATES]
            truncated = True
            break
    for i, item in enumerate(candidates, start=1):
        item["id"] = f"c{i}"
    return {"candidates": candidates, "ai": used_ai, "truncated": truncated}
