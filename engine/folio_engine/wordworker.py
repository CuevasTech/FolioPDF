"""Conversion a Word en un proceso aparte.

Se lanza como ``python -m folio_engine --word-worker <origen.pdf> <destino.docx>``
(o el ejecutable empaquetado con los mismos argumentos). Va en su propio
proceso, y no dentro del motor, por dos motivos:

* Aislamiento. pdf2docx arrastra OpenCV y NumPy y su autor ya no la mantiene de
  forma activa (A03:2025, componentes de terceros). En un proceso aparte, un
  fallo o un bloqueo de esas bibliotecas no tumba el motor, la conversion se
  puede cancelar matando el proceso y su memoria se libera al terminar.
* En Windows, cargar NumPy dentro del motor se queda bloqueado para siempre: el
  motor tiene un hilo esperando peticiones en la entrada estandar y la
  inicializacion de esas bibliotecas consulta ese mismo descriptor.

La contrasena del PDF, si la tiene, llega por la entrada estandar: en la linea
de ordenes la veria cualquier proceso del equipo.
"""

from __future__ import annotations

import logging
import os
import sys

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_UNAVAILABLE = 3
EXIT_FAILED = 4

_MAX_SECRET = 4096


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        return EXIT_USAGE
    source, dest = argv
    # Las rutas las fija el motor (su carpeta privada de trabajo); aun asi se
    # comprueba lo minimo antes de pasarselas a una biblioteca de terceros.
    if not (os.path.isabs(source) and os.path.isabs(dest)):
        return EXIT_USAGE
    if not (source.lower().endswith(".pdf") and dest.lower().endswith(".docx") and os.path.isfile(source)):
        return EXIT_USAGE
    try:
        # Se lee entera ANTES de cargar ninguna biblioteca (ver el comentario
        # del modulo): despues nadie vuelve a tocar la entrada estandar.
        secret = sys.stdin.buffer.read(_MAX_SECRET)
    except (OSError, ValueError, AttributeError):
        secret = b""
    password = secret.decode("utf-8", "replace") or None

    # Nada de lo que escriban las bibliotecas debe salir de aqui: sus mensajes
    # citan el nombre del archivo y a veces contenido del documento (A09:2025).
    sink = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115 - vive lo que el proceso
    sys.stdout = sys.stderr = sink
    logging.disable(logging.CRITICAL)

    try:
        # SystemExit incluido: la biblioteca lo lanza si no le gusta la version
        # de PyMuPDF.
        from pdf2docx import Converter
    except (Exception, SystemExit):  # noqa: BLE001
        return EXIT_UNAVAILABLE
    try:
        converter = Converter(source, password=password)
        try:
            settings = converter.default_settings
            # Un fallo en una pagina detiene la conversion: mejor un error claro
            # que un Word al que le faltan paginas sin avisar (A10:2025). Sin
            # procesos auxiliares: todo ocurre dentro de este.
            settings.update(ignore_page_error=False, multi_processing=False, debug=False)
            converter.load_pages()
            converter.parse_document(**settings)
            converter.parse_pages(**settings)
            converter.make_docx(dest, **settings)
        finally:
            converter.close()
    except (Exception, SystemExit):  # noqa: BLE001 - errores propios de la biblioteca
        return EXIT_FAILED
    return EXIT_OK if os.path.isfile(dest) and os.path.getsize(dest) > 0 else EXIT_FAILED
