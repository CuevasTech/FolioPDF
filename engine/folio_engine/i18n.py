"""Idioma de los textos que el motor muestra o escribe: espanol (por defecto) o ingles.

Al estilo de gettext, pero sin dependencias: cada texto visible se escribe en
el codigo en espanol dentro de ``_()``, y esa misma cadena es la clave del
catalogo ingles (``i18n_en.py``). Los valores variables van como marcadores
``{nombre}`` y se pasan por nombre: ``_("Hay {count} campos", count=3)``.

El idioma lo fija la aplicacion con la variable de entorno ``FOLIO_LANG``
(``es`` o ``en``) y se lee una sola vez, al cargar este modulo. Cualquier otro
valor, o su ausencia, deja el espanol. El intermediario se lo pasa a los
procesos que lanza (motor aislado y procesos auxiliares) ya validado, sin
abrirles el resto del entorno (ver ``broker.Broker._environment``).

Nada de esto puede hacer fallar una operacion: si falta una traduccion, o si
una traduccion no casa con sus marcadores, se usa el texto espanol.

Seguridad (OWASP A05:2025, inyeccion): ``str.format`` solo se aplica a las
plantillas de este codigo (las cadenas literales de ``_()`` y su traduccion),
nunca a un texto recibido de fuera. Los valores que se insertan no se vuelven a
interpretar, aunque contengan llaves.
"""

from __future__ import annotations

import os
from datetime import date, datetime
from typing import Any

from .i18n_en import CATALOG as ENGLISH

LANGUAGES = ("es", "en")
DEFAULT = "es"
ENV = "FOLIO_LANG"


def _from_environment() -> str:
    raw = os.environ.get(ENV, "")
    value = raw.strip().lower() if isinstance(raw, str) and len(raw) <= 16 else ""
    return value if value in LANGUAGES else DEFAULT


_language = _from_environment()


def language() -> str:
    """Idioma activo: ``"es"`` o ``"en"`` (siempre uno de ``LANGUAGES``)."""
    return _language


def set_language(value: str) -> None:
    """Cambia el idioma activo (lo usan las pruebas; un valor desconocido deja el espanol)."""
    global _language
    _language = value if value in LANGUAGES else DEFAULT


def _(text: str, **values: Any) -> str:
    """Texto ``text`` (en espanol) en el idioma activo, con ``values`` en sus marcadores."""
    chosen = text
    if _language == "en":
        found = ENGLISH.get(text) if isinstance(text, str) else None
        chosen = found if isinstance(found, str) and found else text
    if not values:
        return chosen
    for template in (chosen, text):
        try:
            return template.format(**values)
        except (KeyError, IndexError, ValueError, AttributeError, TypeError):
            continue  # traduccion que no casa con sus marcadores: se prueba el espanol
    return text


def N_(text: str) -> str:  # noqa: N802 - nombre habitual de gettext
    """Marca un texto para traducirlo despues (constantes de modulo): devuelve el texto tal cual.

    Se traduce donde se usa, con ``_(CONSTANTE)``: asi el idioma es el activo en
    ese momento y no el que habia al cargar el modulo.
    """
    return text


# Meses para las fechas largas que el motor escribe en los documentos.
MONTHS = (
    N_("enero"), N_("febrero"), N_("marzo"), N_("abril"), N_("mayo"), N_("junio"),
    N_("julio"), N_("agosto"), N_("septiembre"), N_("octubre"), N_("noviembre"), N_("diciembre"),
)


def short_date(moment: date | datetime) -> str:
    """Fecha corta: ``31/01/2026`` en espanol, ``01/31/2026`` en ingles."""
    return _(
        "{day}/{month}/{year}",
        day=f"{moment.day:02d}",
        month=f"{moment.month:02d}",
        year=f"{moment.year:04d}",
    )


def long_date(moment: date | datetime) -> str:
    """Fecha larga: ``31 de enero de 2026`` en espanol, ``January 31, 2026`` en ingles."""
    return _(
        "{day} de {month} de {year}",
        day=str(moment.day),
        month=_(MONTHS[moment.month - 1]),
        year=str(moment.year),
    )
