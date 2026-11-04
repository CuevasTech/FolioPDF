"""Quick check of the document engine as Folio runs it (sandboxed).

    python scripts/engine-check.py                         # engine from source (engine/)
    python scripts/engine-check.py --packaged PATH.exe     # packaged engine

Starts the broker, which launches the sandboxed engine, creates a blank
document, renders its page and closes it. Fails if the engine is not
sandboxed. Used by the GitHub build, also on Windows on Arm, where the x64
engine runs under emulation.
"""

from __future__ import annotations

import argparse
import json
import struct
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))

from folio_engine.protocol import read_frame  # noqa: E402

TIMEOUT = 180.0


def frame(process: subprocess.Popen) -> tuple[dict, bytes]:
    box: dict = {}
    reader = threading.Thread(target=lambda: box.update(frame=read_frame(process.stdout)), daemon=True)
    reader.start()
    reader.join(TIMEOUT)
    if reader.is_alive() or box.get("frame") is None:
        raise SystemExit("ERROR: the engine did not answer")
    return box["frame"]


def call(process: subprocess.Popen, rid: int, method: str, **params) -> tuple[dict, bytes]:
    body = json.dumps({"id": rid, "method": method, "params": params}).encode()
    process.stdin.write(struct.pack(">II", len(body), 0) + body)
    process.stdin.flush()
    while True:
        header, payload = frame(process)
        if header.get("event"):
            continue
        if not header.get("ok"):
            raise SystemExit(f"ERROR: {method} failed: {header.get('error', {}).get('code')}")
        return header["result"], payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packaged", type=Path, help="packaged engine (folio-engine.exe)")
    parser.add_argument("--expect-emulated", action="store_true", help="fail unless the engine reports x64 emulation on Arm")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="folio-check-") as tmp:
        work = Path(tmp) / "work"
        resources = Path(tmp) / "resources"
        work.mkdir()
        resources.mkdir()
        options = ["--workdir", str(work), "--resources", str(resources)]
        if args.packaged:
            command, cwd = [str(args.packaged.resolve()), *options], args.packaged.resolve().parent
        else:
            # -E -s: like the app, ignore PYTHON* variables and user site packages.
            command, cwd = [sys.executable, "-E", "-s", "-m", "folio_engine", *options], ROOT / "engine"
        process = subprocess.Popen(
            command, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        notes: list[str] = []
        errors = threading.Thread(
            target=lambda: notes.extend(line.decode("utf-8", "replace").strip() for line in process.stderr), daemon=True
        )
        errors.start()
        try:
            header, _ = frame(process)
            if header.get("event") != "ready":
                raise SystemExit("ERROR: no ready event")
            if header.get("data", {}).get("sandbox") is not True:
                raise SystemExit(f"ERROR: the engine is not sandboxed ({header.get('data', {}).get('sandboxIssue')})")
            created, _ = call(process, 1, "doc.create")
            page = created["pages"][0]["id"]
            _, image = call(process, 2, "page.render", doc=created["doc"], page=page, scale=1.0)
            if len(image) < 1000:
                raise SystemExit("ERROR: the page was not rendered")
            call(process, 3, "doc.close", doc=created["doc"])
        finally:
            process.stdin.close()
            try:
                process.wait(30)
            except subprocess.TimeoutExpired:
                process.kill()
            errors.join(10)  # everything the engine wrote, before looking at it
        for line in notes:
            if "emulado" in line or "ERROR" in line:
                print(f"  engine: {line[:200]}")
        if args.expect_emulated and not any("emulado" in line for line in notes):
            raise SystemExit("ERROR: the engine did not detect x64 emulation on Arm")
        print(f"engine: sandboxed, blank page rendered ({len(image)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
