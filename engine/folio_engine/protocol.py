"""Tramas del canal entre la aplicacion y el motor.

Cada mensaje es::

    uint32 BE  longitud de la cabecera JSON
    uint32 BE  longitud de la carga binaria (0 si no hay)
    cabecera   JSON en UTF-8
    carga      bytes en bruto (pixeles de una pagina, una imagen, ...)

Se usa JSON y no ``pickle``: deserializar pickle equivale a ejecutar codigo y
aqui no hace falta (OWASP A08:2025).
"""

from __future__ import annotations

import json
import struct
import threading
from typing import Any, BinaryIO

_HEADER = struct.Struct(">II")

# Limites de tamano: un extremo defectuoso no puede hacer que el otro reserve
# memoria sin control a partir de una longitud declarada.
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_BINARY_BYTES = 768 * 1024 * 1024


class ProtocolError(Exception):
    """El flujo no respeta el formato; no se puede seguir leyendo de el."""


def _read_exact(stream: BinaryIO, n: int) -> bytes | None:
    """Lee exactamente ``n`` bytes; ``None`` si el flujo se cierra antes de empezar."""
    chunks: list[bytes] = []
    remaining = n
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            if remaining == n:
                return None
            raise ProtocolError("flujo cortado a mitad de trama")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_frame(stream: BinaryIO) -> tuple[dict[str, Any], bytes] | None:
    """Devuelve ``(cabecera, carga)`` o ``None`` al llegar al final del flujo."""
    raw = _read_exact(stream, _HEADER.size)
    if raw is None:
        return None
    json_len, bin_len = _HEADER.unpack(raw)
    if json_len == 0 or json_len > MAX_JSON_BYTES or bin_len > MAX_BINARY_BYTES:
        raise ProtocolError("longitud de trama fuera de limites")
    body = _read_exact(stream, json_len)
    if body is None:
        raise ProtocolError("flujo cortado a mitad de trama")
    payload = b""
    if bin_len:
        got = _read_exact(stream, bin_len)
        if got is None:
            raise ProtocolError("flujo cortado a mitad de trama")
        payload = got
    try:
        header = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ProtocolError("cabecera no valida") from exc
    if not isinstance(header, dict):
        raise ProtocolError("la cabecera debe ser un objeto")
    return header, payload


class FrameWriter:
    """Escritor de tramas seguro entre hilos (respuestas y avisos de progreso)."""

    def __init__(self, stream: BinaryIO) -> None:
        self._stream = stream
        self._lock = threading.Lock()

    def write(self, header: dict[str, Any], payload: bytes | bytearray | memoryview = b"") -> None:
        body = json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        size = len(payload) if not isinstance(payload, memoryview) else payload.nbytes
        with self._lock:
            self._stream.write(_HEADER.pack(len(body), size))
            self._stream.write(body)
            if size:
                self._stream.write(payload)
            self._stream.flush()
