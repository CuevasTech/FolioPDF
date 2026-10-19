"""Errores controlados del motor.

Un ``EngineError`` lleva un codigo estable (para que la interfaz decida que
hacer) y un mensaje pensado para mostrarse al usuario. Cualquier otra excepcion
se considera interna: se registra con detalle en el log y hacia fuera solo sale
un mensaje generico (OWASP A10:2025, no filtrar detalles internos).
"""

from __future__ import annotations

from typing import Any

from .i18n import _


class EngineError(Exception):
    def __init__(self, code: str, message: str, **detail: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.detail:
            out["detail"] = self.detail
        return out


def invalid(message: str | None = None, **detail: Any) -> EngineError:
    # El texto por defecto se traduce al llamar, no al cargar el modulo.
    return EngineError("invalid_params", _("Parámetros no válidos.") if message is None else message, **detail)


def not_found(message: str | None = None, **detail: Any) -> EngineError:
    return EngineError("not_found", _("No se encontró el elemento.") if message is None else message, **detail)


def unsupported(message: str, **detail: Any) -> EngineError:
    return EngineError("unsupported", message, **detail)


def io_error(message: str, **detail: Any) -> EngineError:
    return EngineError("io_error", message, **detail)


def denied(message: str, **detail: Any) -> EngineError:
    return EngineError("permission_denied", message, **detail)


def locked(message: str, **detail: Any) -> EngineError:
    """Lo impide la firma del documento (no una contrasena: no hay nada que desbloquear)."""
    return EngineError("locked", message, **detail)


class Cancelled(EngineError):
    def __init__(self) -> None:
        super().__init__("cancelled", _("Operación cancelada."))
