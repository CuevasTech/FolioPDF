"""Recuperacion de documentos tras un cierre inesperado.

La copia de trabajo de cada documento esta siempre en el disco con el ultimo
cambio confirmado (ver session.py). Para poder recuperarla hace falta saber de
que documento era: tras cada operacion se deja en su carpeta una ficha pequena
(``recuperar.json``) con el nombre, si tenia cambios sin guardar y hasta donde
llega el estado actual dentro del archivo de trabajo. Nunca la contrasena.

Al arrancar, el motor aparta a la zona de recuperacion las copias con cambios
sin guardar de las ejecuciones que terminaron mal, antes de borrar el resto, y
la aplicacion ofrece recuperarlas o descartarlas. Lo que nadie recupera se
borra a los siete dias: son copias de documentos del usuario.

Todo lo que se lee aqui lo escribio un motor que procesa PDF ajenos: se trata
como dato no fiable (tamano acotado, campos validados, nombres de archivo con
forma fija y sin enlaces).
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import stat
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("folio.engine.recovery")

NOTE = "recuperar.json"
AREA = "recuperacion"
KEEP_DAYS = 7
_MAX_NOTE = 64 * 1024
_SESSION = re.compile(r"^d[0-9a-f]{16}$")
_WORK_FILE = re.compile(r"^w[0-9]{1,6}\.pdf$")


def area_for(work_root: Path) -> Path:
    """Zona de recuperacion: junto a las carpetas de trabajo de cada ejecucion."""
    return work_root.parent / AREA


def note(session: Any) -> None:
    """Deja (o actualiza) la ficha de recuperacion de una sesion. Escritura atomica."""
    revision = session.revisions[session.cur]
    payload = {
        "v": 1,
        "name": str(session.name)[:300],
        "path": str(session.source)[:4000] if session.source else None,
        "dirty": bool(session.dirty),
        "file": revision.file.name,
        "length": int(revision.length),
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "encrypted": bool(session.encrypted),
    }
    target = session.work_dir / NOTE
    tmp = session.work_dir / (NOTE + ".tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, target)
    except OSError:
        log.warning("no se pudo actualizar la ficha de recuperacion")


def _plain(path: Path, *, directory: bool) -> bool:
    """Existe y es un archivo o carpeta de verdad (sin enlaces ni uniones)."""
    try:
        info = os.lstat(path)
    except OSError:
        return False
    if getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
        return False
    return stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)


@dataclass(frozen=True)
class Entry:
    id: str
    name: str
    path: str | None
    time: str
    encrypted: bool
    file: Path
    length: int

    def payload(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "path": self.path, "time": self.time, "encrypted": self.encrypted}


def _read_note(folder: Path) -> Entry | None:
    """Ficha valida de ``folder`` (carpeta de una sesion), o ``None``."""
    note_path = folder / NOTE
    if not _plain(note_path, directory=False):
        return None
    try:
        raw = note_path.read_bytes()[: _MAX_NOTE + 1]
        if len(raw) > _MAX_NOTE:
            return None
        data = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("v") != 1:
        return None
    name, path, file, length = data.get("name"), data.get("path"), data.get("file"), data.get("length")
    when, encrypted = data.get("time"), data.get("encrypted")
    if not isinstance(name, str) or not 0 < len(name) <= 300:
        return None
    if path is not None and (not isinstance(path, str) or len(path) > 4000):
        return None
    # El archivo solo puede ser una copia de trabajo de la propia carpeta.
    if not isinstance(file, str) or not _WORK_FILE.match(file):
        return None
    if not isinstance(length, int) or isinstance(length, bool) or length <= 0:
        return None
    if not isinstance(when, str) or len(when) > 40 or not isinstance(encrypted, bool):
        return None
    work = folder / file
    if not _plain(work, directory=False):
        return None
    try:
        if work.stat().st_size < length:
            return None
    except OSError:
        return None
    if data.get("dirty") is not True:
        return None
    return Entry(folder.name, name, path, when, encrypted, work, length)


def harvest(run: Path, area: Path) -> int:
    """Aparta a ``area`` las copias con cambios sin guardar de una ejecucion abandonada."""
    moved = 0
    try:
        sessions = [Path(entry.path) for entry in os.scandir(run) if _SESSION.match(entry.name)]
    except OSError:
        return 0
    for folder in sessions:
        if not _plain(folder, directory=True):
            continue
        entry = _read_note(folder)
        if entry is None:
            continue
        try:
            area.mkdir(exist_ok=True)
            if not _plain(area, directory=True):
                return moved
            target = area / folder.name
            target.mkdir()
            os.replace(entry.file, target / entry.file.name)
            os.replace(folder / NOTE, target / NOTE)
            moved += 1
        except OSError:
            log.warning("no se pudo apartar un documento para recuperarlo")
    return moved


def prune(area: Path, *, keep_days: int = KEEP_DAYS) -> None:
    """Borra lo que lleva mas de ``keep_days`` dias sin recuperarse, y lo que no es valido."""
    if not _plain(area, directory=True):
        return
    limit = time.time() - keep_days * 86400
    for entry in list(os.scandir(area)):
        folder = Path(entry.path)
        try:
            stale = entry.stat(follow_symlinks=False).st_mtime < limit
        except OSError:
            stale = True
        if not _SESSION.match(entry.name) or not _plain(folder, directory=True) or stale or _read_note(folder) is None:
            if _plain(folder, directory=True):
                shutil.rmtree(folder, ignore_errors=True)
            else:
                try:
                    folder.unlink()  # un enlace o un archivo suelto: se quita el enlace, no su destino
                except OSError:
                    pass


def entries(area: Path) -> list[Entry]:
    if not _plain(area, directory=True):
        return []
    found = []
    for entry in os.scandir(area):
        if _SESSION.match(entry.name) and _plain(Path(entry.path), directory=True):
            item = _read_note(Path(entry.path))
            if item is not None:
                found.append(item)
    return sorted(found, key=lambda item: item.time, reverse=True)


def find(area: Path, sid: str) -> Entry | None:
    if not _SESSION.match(sid):
        return None
    folder = area / sid
    return _read_note(folder) if _plain(folder, directory=True) else None


def discard(area: Path, sid: str) -> None:
    if _SESSION.match(sid) and _plain(area / sid, directory=True):
        shutil.rmtree(area / sid, ignore_errors=True)
