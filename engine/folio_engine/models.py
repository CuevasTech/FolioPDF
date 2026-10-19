"""Tipos compartidos para validar los parametros de cada operacion.

Aunque quien llama es nuestra propia aplicacion, cada peticion se valida de
nuevo aqui (defensa en profundidad): tipos, rangos y longitudes acotados, y
cualquier campo no declarado se rechaza (lista blanca, OWASP A05:2025).
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class Params(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_max_length=2_000_000)


class NoParams(Params):
    pass


# Identificadores generados por el motor: solo se aceptan con su forma exacta,
# asi nunca pueden usarse para componer rutas ni consultas.
DocId = Annotated[str, StringConstraints(pattern=r"^d[0-9a-f]{16}$")]
PageId = Annotated[str, StringConstraints(pattern=r"^p[0-9]{1,9}$")]
ObjId = Annotated[str, StringConstraints(pattern=r"^[a-z][0-9a-z\-]{1,40}$")]

Coord = Annotated[float, Field(ge=-200_000, le=200_000, allow_inf_nan=False)]
Unit = Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)]
Angle = Annotated[float, Field(ge=-3600, le=3600, allow_inf_nan=False)]
Color = tuple[Unit, Unit, Unit]
Point = tuple[Coord, Coord]
Rect = tuple[Coord, Coord, Coord, Coord]
Quad = tuple[Point, Point, Point, Point]

# Rutas: longitud acotada; la comprobacion de fondo esta en ``paths.py``.
PathStr = Annotated[str, StringConstraints(min_length=1, max_length=32_000)]
ShortText = Annotated[str, StringConstraints(max_length=1_000)]
LongText = Annotated[str, StringConstraints(max_length=200_000)]
Secret = Annotated[str, StringConstraints(max_length=1_024)]


class DocParams(Params):
    doc: DocId


class PageParams(Params):
    doc: DocId
    page: PageId
