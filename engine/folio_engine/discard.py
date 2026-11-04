"""Salida de texto que lo descarta todo, para los procesos auxiliares aislados.

Los procesos de Word y de digitalizacion no dejan salir nada de lo que escriben
sus bibliotecas: los mensajes citan el nombre del archivo y a veces contenido
del documento (A09:2025). Antes se abria el dispositivo NUL, pero en Windows
Server 2025 (y en equipos con ese dispositivo restringido) un proceso dentro de
un AppContainer no puede abrirlo (``PermissionError``) y el proceso terminaba
sin hacer nada. Esto no abre nada del sistema.
"""

from __future__ import annotations

import io


class _DiscardBytes(io.RawIOBase):
    def writable(self) -> bool:
        return True

    def write(self, data) -> int:  # noqa: ANN001 - bytes, bytearray o memoryview
        return len(data)


def text_sink() -> io.TextIOWrapper:
    """Un ``sys.stdout``/``sys.stderr`` valido (con ``buffer`` y ``encoding``) que no guarda nada."""
    return io.TextIOWrapper(io.BufferedWriter(_DiscardBytes()), encoding="utf-8", write_through=True)
