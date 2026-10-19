"""Documentos que se pueden recuperar tras un cierre inesperado (ver ``folio_engine.recovery``)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import recovery
from ..errors import io_error, not_found
from ..i18n import _
from ..models import DocId, Params, Secret
from ..server import Ctx, method
from ..session import DocSession
from .document import describe

_MAX_RECOVERED = 4 * 1024**3


class RecoveryParams(Params):
    id: DocId


class RecoverParams(RecoveryParams):
    password: Secret | None = None


def _area(ctx: Ctx) -> Path:
    return recovery.area_for(ctx.engine.work_root)


@method("recovery.list")
def recovery_list(ctx: Ctx, p: Params) -> dict[str, Any]:
    """Documentos con cambios sin guardar de una ejecucion que termino de golpe."""
    return {"documents": [entry.payload() for entry in recovery.entries(_area(ctx))]}


def _recovered_name(name: str) -> str:
    stem = name[:-4] if name.lower().endswith(".pdf") else name
    return _("{stem} (recuperado).pdf", stem=stem)


@method("recovery.open", RecoverParams)
def recovery_open(ctx: Ctx, p: RecoverParams) -> dict[str, Any]:
    """Abre la copia recuperada como documento nuevo, sin archivo propio: se guarda con «Guardar como»."""
    area = _area(ctx)
    entry = recovery.find(area, p.id)
    if entry is None:
        raise not_found(_("Ese documento ya no se puede recuperar."))
    if entry.length > _MAX_RECOVERED:
        raise io_error(_("El documento recuperado es demasiado grande."))
    try:
        with open(entry.file, "rb") as handle:
            data = handle.read(entry.length)  # solo hasta el ultimo estado confirmado
    except OSError as exc:
        raise io_error(_("No se pudo leer el documento recuperado.")) from exc
    session = DocSession.from_bytes(ctx.engine.work_root, data, _recovered_name(entry.name), ctx.engine.host, p.password)
    ctx.engine.sessions[session.id] = session
    # Ya tiene su propia copia de trabajo (con su propia ficha de recuperacion).
    recovery.discard(area, p.id)
    return describe(session)


@method("recovery.discard", RecoveryParams)
def recovery_discard(ctx: Ctx, p: RecoveryParams) -> dict[str, Any]:
    recovery.discard(_area(ctx), p.id)
    return {}
