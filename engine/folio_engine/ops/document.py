"""Abrir, describir, guardar y cerrar documentos; deshacer y rehacer."""

from __future__ import annotations

import re
import secrets
from pathlib import Path
from typing import Any

import pymupdf

from ..errors import EngineError
from ..geometry import Geom
from ..i18n import _
from ..imaging import images_to_pdf, sniff
from ..models import DocParams, Params, PathStr, Secret, ShortText
from ..server import Ctx, method
from ..session import _PERMISSION_BITS, DocSession

_PDF_DATE = re.compile(r"^D:(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?([Zz+\-])?(\d{2})?'?(\d{2})?")


def pdf_date_to_iso(value: str | None) -> str | None:
    """Convierte una fecha PDF (``D:20240131120000+01'00'``) a ISO 8601."""
    if not value:
        return None
    m = _PDF_DATE.match(value.strip())
    if not m:
        return None
    year, month, day, hour, minute, second, sign, tzh, tzm = m.groups()
    out = f"{year}-{month or '01'}-{day or '01'}T{hour or '00'}:{minute or '00'}:{second or '00'}"
    if sign in ("Z", "z"):
        out += "Z"
    elif sign in ("+", "-") and tzh:
        out += f"{sign}{tzh}:{tzm or '00'}"
    return out


def _has_xfa(doc: pymupdf.Document) -> bool:
    try:
        catalog = doc.pdf_catalog()
        kind, value = doc.xref_get_key(catalog, "AcroForm")
        if kind == "xref":
            kind, _ = doc.xref_get_key(int(value.split()[0]), "XFA")
        elif kind == "dict":
            kind, _ = doc.xref_get_key(catalog, "AcroForm/XFA")
        else:
            return False
        return kind not in ("null", "")
    except Exception:  # noqa: BLE001
        return False


def describe(session: DocSession) -> dict[str, Any]:
    doc = session.doc
    meta = doc.metadata or {}
    return {
        "doc": session.id,
        "pages": session.pages_payload(),
        "state": session.state(),
        "meta": {
            "title": meta.get("title") or "",
            "author": meta.get("author") or "",
            "subject": meta.get("subject") or "",
            "keywords": meta.get("keywords") or "",
            "creator": meta.get("creator") or "",
            "producer": meta.get("producer") or "",
            "created": pdf_date_to_iso(meta.get("creationDate")),
            "modified": pdf_date_to_iso(meta.get("modDate")),
        },
        "info": {
            "format": meta.get("format") or "PDF",
            "encrypted": session.encrypted,
            "owner": session.is_owner,
            "permissions": {name: session.allowed(name) for name in _PERMISSION_BITS},
            "hasForm": bool(doc.is_form_pdf),
            "hasXfa": _has_xfa(doc),
            "repaired": bool(doc.is_repaired),
            "fileSize": session.revisions[session.cur].length,
        },
    }


class OpenParams(Params):
    path: PathStr
    password: Secret | None = None


@method("doc.open", OpenParams)
def doc_open(ctx: Ctx, p: OpenParams) -> dict[str, Any]:
    engine = ctx.engine
    # Copia privada del archivo del usuario (host.fetch valida la ruta): el
    # motor solo trabaja sobre ella y, aislado, es lo unico que puede leer.
    local = engine.work_root / f"entrada-{secrets.token_hex(8)}"
    try:
        fetched = engine.host.fetch(p.path, local)
        # Por contenido y antes de que MuPDF lo vea: lo que no empieza como un
        # PDF ni es una imagen admitida no llega a ningun analizador (A05:2025).
        kind = sniff(local)
        if kind == "pdf":
            session = DocSession.from_fetched(engine.work_root, local, fetched, p.password, engine.host)
        elif kind == "image":
            name = Path(fetched.name).stem + ".pdf"
            session = DocSession.from_bytes(engine.work_root, images_to_pdf([local]), name, engine.host)
        else:
            raise EngineError("invalid_file", _("Este tipo de archivo no es compatible. Abre un PDF o una imagen."))
    finally:
        local.unlink(missing_ok=True)  # un PDF ya se ha movido a la carpeta de su sesion
    engine.sessions[session.id] = session
    return describe(session)


@method("doc.describe", DocParams)
def doc_describe(ctx: Ctx, p: DocParams) -> dict[str, Any]:
    return describe(ctx.engine.session(p.doc))


class UnlockParams(DocParams):
    password: Secret


@method("doc.unlock", UnlockParams)
def doc_unlock(ctx: Ctx, p: UnlockParams) -> dict[str, Any]:
    session = ctx.engine.session(p.doc)
    if not session.unlock(p.password):
        raise EngineError("wrong_password", _("Esa no es la contraseña de propietario."))
    return describe(session)


@method("doc.close", DocParams)
def doc_close(ctx: Ctx, p: DocParams) -> dict[str, Any]:
    session = ctx.engine.sessions.pop(p.doc, None)
    if session is not None:
        session.close()
    return {}


class SaveParams(DocParams):
    path: PathStr
    force: bool = False


@method("doc.save", SaveParams)
def doc_save(ctx: Ctx, p: SaveParams) -> dict[str, Any]:
    session = ctx.engine.session(p.doc)
    dest = ctx.engine.host.check_output(p.path, (".pdf",))
    return session.save(dest, force=p.force)


@method("doc.undo", DocParams)
def doc_undo(ctx: Ctx, p: DocParams) -> dict[str, Any]:
    session = ctx.engine.session(p.doc)
    rev = session.undo()
    return session.result(rev.dirty, structural=rev.structural, label=rev.label)


@method("doc.redo", DocParams)
def doc_redo(ctx: Ctx, p: DocParams) -> dict[str, Any]:
    session = ctx.engine.session(p.doc)
    rev = session.redo()
    return session.result(rev.dirty, structural=rev.structural, label=rev.label)


class MetaParams(DocParams):
    title: ShortText = ""
    author: ShortText = ""
    subject: ShortText = ""
    keywords: ShortText = ""


@method("doc.setMetadata", MetaParams)
def doc_set_metadata(ctx: Ctx, p: MetaParams) -> dict[str, Any]:
    session = ctx.engine.session(p.doc)
    session.require("modify")
    with session.edit(_("Editar propiedades")):
        meta = dict(session.doc.metadata or {})
        meta.pop("format", None)
        meta.pop("encryption", None)
        meta.update(title=p.title, author=p.author, subject=p.subject, keywords=p.keywords)
        session.doc.set_metadata(meta)
    return session.result(meta=describe(session)["meta"])


@method("doc.outline", DocParams)
def doc_outline(ctx: Ctx, p: DocParams) -> dict[str, Any]:
    session = ctx.engine.session(p.doc)
    doc = session.doc
    items: list[dict[str, Any]] = []
    try:
        toc = doc.get_toc(simple=False)
    except Exception:  # noqa: BLE001 - marcadores corruptos: se muestra el documento sin ellos
        toc = []
    for entry in toc:
        level, title, page_no = entry[0], entry[1], entry[2]
        dest = entry[3] if len(entry) > 3 and isinstance(entry[3], dict) else {}
        target: str | None = None
        top: float | None = None
        if isinstance(page_no, int) and 1 <= page_no <= len(session.page_ids):
            target = session.page_ids[page_no - 1]
            point = dest.get("to")
            if point is not None:
                try:
                    geom = Geom(doc[page_no - 1])
                    top = max(0.0, geom.point_d((point.x, point.y))[1])
                except Exception:  # noqa: BLE001
                    top = None
        items.append(
            {
                "level": int(level),
                "title": str(title)[:500],
                "page": target,
                "y": top,
                "bold": bool(dest.get("bold")),
                "italic": bool(dest.get("italic")),
            }
        )
    return {"items": items}
