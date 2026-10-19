"""Firma digital con certificado: almacen de Windows, firmantes, apariencia y validacion.

Es la parte mas sensible del motor. Las decisiones de seguridad estan
comentadas junto al codigo al que afectan; las que valen para todo el paquete:

* La criptografia la hacen siempre librerias revisadas (pyHanko, cryptography)
  o el propio Windows. Aqui no se implementa ningun algoritmo (A04:2025).
* La clave privada de un certificado del almacen nunca se exporta: firma
  Windows. La de un archivo .pfx vive en memoria solo mientras dura la firma.
* Contrasenas y datos de certificados (nombres, NIF, numeros de serie,
  huellas) no se escriben nunca en el log (A09:2025).
* Los PDF y los certificados que traen dentro son datos no fiables: un fallo
  al leerlos nunca tumba la operacion ni filtra detalles internos (A10:2025).

Este archivo se mantiene ligero a proposito: importar pyHanko cuesta en torno a
0,7 s, asi que los modulos que lo usan se cargan la primera vez que se firma o
se valida y no al arrancar el motor.
"""

from __future__ import annotations

import logging

# asn1crypto anade los tipos de contenido de sello de tiempo ("tst_info") a su
# tabla al cargar ``tsp``, pero la tabla inversa se calcula una sola vez, la
# primera vez que se usa. Si algo (leer un .pfx, por ejemplo) la usa antes, esos
# tipos dejan de reconocerse por nombre y los sellos de tiempo fallan. Se carga
# aqui, antes que cualquier otra cosa del paquete; es un modulo pequeno.
from asn1crypto import ocsp, tsp  # noqa: F401, E402

# pyHanko y su validador de certificados registran en sus "loggers" el asunto
# completo de los certificados (nombre y NIF del firmante) junto con las trazas
# de sus excepciones. Eso no debe escribirse en ningun sitio (A09:2025, no
# registrar datos personales).
#
# Se apagan en origen, con un nivel por encima de CRITICAL: el mensaje ni
# llega a crearse. ``propagate = False`` no basta: solo impide que suba al
# logger raiz, y un manejador enganchado a estos loggers (una herramienta de
# pruebas, un volcado a archivo) lo seguiria recibiendo. El nivel lo heredan
# sus loggers hijos (``pyhanko.sign...``), donde de verdad se emiten.
#
# El motor registra por su cuenta lo necesario: operacion y tipo de fallo.
for _name in ("pyhanko", "pyhanko_certvalidator"):
    _library_log = logging.getLogger(_name)
    _library_log.setLevel(logging.CRITICAL + 1)
    _library_log.addHandler(logging.NullHandler())
    _library_log.propagate = False  # segunda barrera, por si algo cambiara el nivel
