"""Punto de entrada del motor: ``python -m folio_engine --workdir <carpeta>``.

Modos (ver broker.py):

* sin indicar nada: proceso **intermediario**. Lanza el motor aislado y es lo
  que arranca la aplicacion.
* ``--worker``: el **motor aislado**. Lo lanza el intermediario dentro de un
  AppContainer; todo lo de fuera se lo pide a el.
* ``--direct``: el motor sin aislar, en un solo proceso (diagnostico).
* ``--word-worker``: conversion a Word en un proceso aparte (wordworker.py).
* ``--scan-worker``: busqueda del papel en fotos, en un proceso aparte (scanworker.py).
* ``--ai-worker``: IA local (modelos de lenguaje y pequenos), en un proceso aparte (aiworker.py).
* ``--ai-detect``: dice si un texto lleva la marca de agua de la IA de Folio.
* ``--ai-gpu``: escribe que grafica usaria la IA (proceso aislado de usar y tirar).
* ``--remove-sandbox``: borra el contenedor (lo usa el desinstalador).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path


def _claim_stdio():
    """Reserva la salida estandar real para las tramas del protocolo.

    Cualquier ``print`` (propio o de una libreria) corromperia el flujo binario,
    asi que se duplican los descriptores originales y el descriptor 1 pasa a
    apuntar a stderr.
    """
    in_fd = os.dup(0)
    out_fd = os.dup(1)
    if os.name == "nt":
        import msvcrt

        msvcrt.setmode(in_fd, os.O_BINARY)
        msvcrt.setmode(out_fd, os.O_BINARY)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    if os.name == "nt":
        _point_std_output_at_stderr()
    rfile = os.fdopen(in_fd, "rb", buffering=1024 * 1024)
    wfile = os.fdopen(out_fd, "wb", buffering=1024 * 1024)
    return rfile, wfile


def _point_std_output_at_stderr() -> None:
    """La salida estandar de Windows (``GetStdHandle``) pasa a ser la de errores.

    ``os.dup2`` cierra el manejador original del descriptor 1, pero en el motor
    empaquetado (PyInstaller) Windows seguia dando ese manejador ya cerrado a
    las bibliotecas nativas que escriben en la salida estandar. Si para
    entonces el numero se habia reutilizado para otra cosa, escribian en ella:
    asi fallaba al azar la carga de Vulkan (error 1114) en el proceso de IA. Un
    manejador colgado es ademas un riesgo de integridad (podria acabar
    escribiendo en un archivo del usuario), asi que se corrige siempre.
    """
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.SetStdHandle.argtypes = [wintypes.DWORD, wintypes.HANDLE]
    kernel32.SetStdHandle.restype = wintypes.BOOL
    std_output_handle = wintypes.DWORD(-11 & 0xFFFFFFFF)  # STD_OUTPUT_HANDLE
    if not kernel32.SetStdHandle(std_output_handle, msvcrt.get_osfhandle(1)):
        raise OSError(ctypes.get_last_error(), "SetStdHandle")


def _tidy_container(own_base: Path) -> int:
    """Ordena el resto de la carpeta del contenedor, que comparten todos los perfiles.

    De cada otro perfil (y de las ejecuciones guardadas sin carpeta de perfil,
    como hacian versiones anteriores) se aparta lo recuperable igual que en el
    propio, se borra lo caducado y, si no queda nada, la carpeta: los perfiles
    temporales (pruebas) no dejan restos. Lo de otro perfil en marcha no se
    toca (su marca de uso esta bloqueada).
    """
    from . import recovery, winsandbox

    removed = 0
    root = own_base.parent
    for base in [*winsandbox.other_profiles(own_base), root]:
        area = base / recovery.AREA
        removed += winsandbox.clean_stale_dirs(base, before_delete=lambda run, area=area: recovery.harvest(run, area))
        recovery.prune(area)
        if base != root:
            for leftover in (area, base):
                try:
                    leftover.rmdir()  # solo si esta vacia
                except OSError:
                    pass
    return removed


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else list(argv)
    if arguments[:1] == ["--word-worker"]:
        # Se atiende antes que nada: no arranca el servidor ni toca el protocolo.
        from .wordworker import main as word_worker

        return word_worker(arguments[1:])
    if arguments[:1] == ["--scan-worker"]:
        # Igual que el anterior.
        from .scanworker import main as scan_worker

        return scan_worker(arguments[1:])
    if arguments[:1] == ["--ai-worker"]:
        # La salida estandar se reserva antes de cargar nada: las bibliotecas
        # no pueden escribir en ella.
        rfile, wfile = _claim_stdio()
        from .aiworker import main as ai_worker

        return ai_worker(arguments[1:], rfile, wfile)
    if arguments[:1] == ["--ai-gpu"]:
        # Solo dice que grafica usaria la IA y termina. El intermediario lo lanza
        # aislado, como la IA: los controladores de video (Vulkan) nunca se cargan
        # en el proceso sin aislar (ver aibroker.AiService.configure).
        from .aiworker import gpu_main

        return gpu_main()
    if arguments[:1] == ["--ai-detect"]:
        from .aiworker import detect_main

        return detect_main(arguments[1:])
    if arguments[:1] == ["--remove-sandbox"]:
        from . import winsandbox

        return 0 if winsandbox.delete_container() else 1

    parser = argparse.ArgumentParser(prog="folio-engine", add_help=False)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--worker", action="store_true")
    mode.add_argument("--direct", action="store_true")
    parser.add_argument("--no-sandbox", action="store_true")
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--resources", default=None)
    # Carpeta de los modelos de IA que descargo la aplicacion (solo la usa el intermediario).
    parser.add_argument("--models", default=None)
    parser.add_argument("--log-level", default="info", choices=("debug", "info", "warning", "error"))
    args = parser.parse_args(arguments)

    if args.worker:
        # Lo primero, antes de cargar nada que analice documentos: desde aqui
        # este proceso no puede crear ni modificar codigo ejecutable (ACG). Un
        # fallo de memoria explotado por un PDF ya no puede inyectar codigo
        # propio. ctypes, que lo necesita al cargarse, ya esta cargado.
        from . import winsandbox

        locked = winsandbox.lock_down_dynamic_code()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        stream=sys.stderr,
        format="%(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("folio.engine")

    work_root = Path(args.workdir)
    if not work_root.is_absolute() or not work_root.is_dir():
        log.error("la carpeta de trabajo no existe o no es absoluta")
        return 2
    resources = Path(args.resources) if args.resources else None
    if resources is not None and not resources.is_dir():
        resources = None

    rfile, wfile = _claim_stdio()

    if not (args.worker or args.direct):
        from .broker import run

        models = Path(args.models) if args.models else None
        if models is not None and not models.is_absolute():
            models = None
        return run(rfile, wfile, work_root, resources, sandbox=not args.no_sandbox, models=models)

    if args.worker:
        from . import recovery, winsandbox

        if not locked:
            if winsandbox.running_emulated():
                log.info("motor emulado en Windows ARM: sin prohibicion de generar codigo (el emulador la necesita)")
            else:
                log.warning("no se pudo prohibir la generacion de codigo en el motor aislado")

        # Ejecuciones anteriores que terminaron mal: lo que tenia cambios sin
        # guardar se aparta para ofrecer recuperarlo; el resto se borra.
        area = recovery.area_for(work_root)
        removed = winsandbox.clean_stale_runs(work_root, before_delete=lambda run: recovery.harvest(run, area))
        recovery.prune(area)
        if work_root.parent.name.startswith(winsandbox.PROFILE_PREFIX):
            removed += _tidy_container(work_root.parent)
        if removed:
            log.info("borradas %d carpetas de trabajo de ejecuciones anteriores", removed)

    # En Windows, cargar NumPy (y con el OpenCV) en este proceso se cuelga para
    # siempre si hay un hilo esperando en la entrada estandar, como hace el
    # motor. Por eso la conversion a Word y la busqueda del papel van en
    # procesos aparte (wordworker.py, scanworker.py). Si una biblioteca
    # intentara importarlos aqui, mejor un error inmediato que un motor
    # colgado (A10:2025).
    for blocked in ("numpy", "cv2"):
        sys.modules.setdefault(blocked, None)  # type: ignore[arg-type]

    import pymupdf

    from . import __version__, ops  # noqa: F401 - importar ops registra las operaciones
    from .host import BrokeredHost
    from .server import Engine, Server

    # MuPDF escribe sus avisos en stderr por defecto; se silencian para no
    # volcar al log fragmentos del contenido de los documentos.
    pymupdf.TOOLS.mupdf_display_errors(False)
    pymupdf.TOOLS.mupdf_display_warnings(False)

    engine = Engine(work_root, resources)
    server = Server(rfile, wfile, engine)
    if args.worker:
        engine.host = BrokeredHost(server.call_host)
    try:
        server.serve({"version": __version__, "pymupdf": pymupdf.__version__, "pid": os.getpid()})
    finally:
        # Al salir normalmente la aplicacion ya ha cerrado cada documento. Si
        # queda alguno abierto es que se ha ido de golpe: lo que tenga cambios
        # sin guardar se conserva para ofrecer recuperarlo.
        engine.close_all(keep_unsaved=args.worker)
        try:
            wfile.flush()
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
