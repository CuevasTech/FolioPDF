"""Checks the document engine as Folio runs it: sandboxed, through the broker.

    python scripts/engine-check.py [--packaged EXE] [--expect-emulated]
    python scripts/engine-check.py --packaged EXE --full [--store-cert] [--models DIR [--fetch-models]]

Quick check: starts the broker, which launches the sandboxed engine, creates a
blank document and renders its page. Fails if the engine is not sandboxed.

--full also runs every part of the engine built from native code, in the
process where Folio runs it: open, edit and save; text and Word export (Word in
its own sandboxed process); splitting; OCR (Tesseract); password protection;
redaction; forms; signing with a .pfx and validating; photo scanning (OpenCV,
its own sandboxed process). It also checks that the sandbox blocks a file the
user did not choose.

--store-cert also signs with certificates from the Windows store (RSA, ECDSA
and an old CSP key). It creates throwaway certificates in CurrentUser\\My and
always deletes them.

--models DIR also runs the local AI with the basic models (llama.cpp and ONNX
Runtime, in its own sandboxed process): search by meaning, personal data
detection, a question, a translation and page unwarping. --fetch-models first
downloads them from the addresses in src/main/aicatalog.ts, checking size and
SHA-256.

Used by the GitHub build on Windows x64 and on Windows 11 on Arm, where the x64
engine runs under emulation. Needs the engine's virtual environment (engine/.venv).
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))

from folio_engine.protocol import read_frame  # noqa: E402

TIMEOUT = 300.0
AI_TIMEOUT = 1800.0  # loading a model and generating text are slow under emulation
# Throwaway password: it only protects files made and deleted by this check.
PASSWORD = "check-Pass-7391"
ALL_PERMISSIONS = dict(print=True, modify=True, copy=True, annotate=True, form=True, assemble=True,
                       accessibility=True, printHq=True)


class Failure(Exception):
    pass


class EngineFailure(Failure):
    def __init__(self, method: str, code: str | None, message: str | None) -> None:
        super().__init__(f"{method} failed: {code} ({message})")
        self.code = code


def expect(condition: Any, what: str) -> None:
    if not condition:
        raise Failure(what)


class Engine:
    """The broker as Folio sees it: frames over its standard input and output."""

    def __init__(self, command: list[str], cwd: Path) -> None:
        self.process = subprocess.Popen(
            command, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.notes: list[str] = []
        self._errors = threading.Thread(target=self._read_errors, daemon=True)
        self._errors.start()
        self._next = 1
        header, _ = self.frame(TIMEOUT)
        expect(header.get("event") == "ready", "the engine did not start")
        self.hello: dict[str, Any] = header.get("data") or {}

    def _read_errors(self) -> None:
        for line in self.process.stderr:
            self.notes.append(line.decode("utf-8", "replace").rstrip())

    def frame(self, timeout: float) -> tuple[dict[str, Any], bytes]:
        box: dict[str, Any] = {}
        reader = threading.Thread(target=lambda: box.update(frame=read_frame(self.process.stdout)), daemon=True)
        reader.start()
        reader.join(timeout)
        if reader.is_alive() or box.get("frame") is None:
            raise Failure("the engine did not answer")
        return box["frame"]

    def call(self, method: str, binary: bytes = b"", *, timeout: float = TIMEOUT, **params: Any) -> tuple[dict[str, Any], bytes]:
        rid = self._next
        self._next += 1
        body = json.dumps({"id": rid, "method": method, "params": params}).encode()
        self.process.stdin.write(struct.pack(">II", len(body), len(binary)) + body + binary)
        self.process.stdin.flush()
        while True:
            header, payload = self.frame(timeout)
            if header.get("event"):
                continue  # progress
            expect(header.get("id") == rid, f"{method}: answer to another request")
            if not header.get("ok"):
                error = header.get("error") or {}
                raise EngineFailure(method, error.get("code"), error.get("message"))
            return header["result"], payload

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self.process.stdin.close()
        try:
            self.process.wait(30)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(10)
        self._errors.join(10)  # everything the engine wrote, before looking at it


# ================================================================ sample files


def make_pdf(path: Path, pages: int = 3, extra: tuple[str, ...] = ()) -> Path:
    import pymupdf

    doc = pymupdf.open()
    for n in range(pages):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 90), f"Documento de prueba, pagina {n + 1}", fontsize=16)
        for i in range(10):
            page.insert_text((72, 130 + i * 18), f"Linea {i + 1} con algo de texto para buscar: folio {n + 1}.", fontsize=11)
        if n == 0:
            for i, line in enumerate(extra):
                page.insert_text((72, 340 + i * 18), line, fontsize=11)
    doc.save(str(path))
    doc.close()
    return path


def make_scanned_pdf(path: Path) -> Path:
    """A page that is only a picture of text, like a scan."""
    import pymupdf

    source = pymupdf.open()
    page = source.new_page(width=420, height=300)
    page.insert_text((30, 60), "Murcielago", fontsize=28)
    for i, line in enumerate(("The quick brown fox jumps", "over the lazy dog near", "the old stone bridge.")):
        page.insert_text((30, 110 + i * 32), line, fontsize=20)
    png = page.get_pixmap(matrix=pymupdf.Matrix(200 / 72, 200 / 72), alpha=False).tobytes("png")
    source.close()
    out = pymupdf.open()
    scan = out.new_page(width=420, height=300)
    scan.insert_image(scan.rect, stream=png)
    out.save(str(path))
    out.close()
    return path


def make_photo(path: Path) -> Path:
    """A phone photo of a page: tilted, on a darker background, with noise."""
    import cv2
    import numpy as np
    import pymupdf

    source = pymupdf.open()
    page = source.new_page(width=595, height=842)
    page.insert_text((72, 90), "Presupuesto de obra", fontsize=22)
    for i in range(26):
        page.insert_text((72, 140 + i * 22), f"Partida {i + 1}: materiales y mano de obra, importe {i * 37} euros", fontsize=11)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(900 / 595, 900 / 595), alpha=False)
    source.close()
    rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, 3)
    paper = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    width, height = 1200, 1600
    corners = [(0.12, 0.08), (0.88, 0.11), (0.9, 0.93), (0.09, 0.9)]
    src = np.float32([[0, 0], [paper.shape[1], 0], [paper.shape[1], paper.shape[0]], [0, paper.shape[0]]])
    matrix = cv2.getPerspectiveTransform(src, np.float32([[x * width, y * height] for x, y in corners]))
    warped = cv2.warpPerspective(paper, matrix, (width, height))
    inside = cv2.warpPerspective(np.full(paper.shape[:2], 255, np.uint8), matrix, (width, height)) > 0
    rng = np.random.default_rng(7)
    photo = np.clip(np.full((height, width, 3), (60, 90, 130), np.int16) + rng.normal(0, 12, (height, width, 3)), 0, 255).astype(np.uint8)
    photo[inside] = warped[inside]
    cv2.imwrite(str(path), photo, [cv2.IMWRITE_JPEG_QUALITY, 88])
    return path


def make_p12(path: Path) -> Path:
    """A new self-signed signing key and certificate, as PKCS#12. Not added to any store."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Folio Check Temporary"),
                      x509.NameAttribute(NameOID.COUNTRY_NAME, "ES")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=True, key_encipherment=False,
                                     data_encipherment=False, key_agreement=False, key_cert_sign=False,
                                     crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
        .sign(key, hashes.SHA256())
    )
    protection = serialization.BestAvailableEncryption(PASSWORD.encode())
    path.write_bytes(pkcs12.serialize_key_and_certificates(b"check", key, cert, None, protection))
    return path


def open_new(engine: Engine, path: Path, **kwargs: Any) -> str:
    make_pdf(path, **kwargs)
    return engine.call("doc.open", path=str(path))[0]["doc"]


def found(engine: Engine, doc: str, text: str) -> int:
    return engine.call("doc.search", doc=doc, query=text)[0]["total"]


# ================================================================ quick check


def check_blank(engine: Engine, _files: Path) -> str:
    created, _ = engine.call("doc.create")
    _, image = engine.call("page.render", doc=created["doc"], page=created["pages"][0]["id"], scale=1.0)
    expect(len(image) > 1000, "the page was not rendered")
    engine.call("doc.close", doc=created["doc"])
    return f"{len(image)} bytes"


# ================================================================ documents


def check_open_edit_save(engine: Engine, files: Path) -> str:
    import pymupdf

    source = make_pdf(files / "Report.pdf")
    opened, _ = engine.call("doc.open", path=str(source))
    doc = opened["doc"]
    info, pixels = engine.call("page.render", doc=doc, page="p1", scale=1.0)
    expect(len(pixels) == info["w"] * info["h"] * 4, "page render")
    engine.call("obj.addShape", doc=doc, page="p1", shape="rect", rect=[50, 50, 120, 90], stroke=None, fill=[1, 0, 0])
    saved, _ = engine.call("doc.save", doc=doc, path=str(source))  # over the original, replaced atomically
    expect(saved["state"]["dirty"] is False and saved["size"] == source.stat().st_size, "save")
    engine.call("doc.close", doc=doc)
    with pymupdf.open(str(source)) as check:
        expect(check.page_count == 3 and check[0].get_drawings(), "the saved file")
    return f"{info['w']}x{info['h']} px"


def check_export_split(engine: Engine, files: Path) -> str:
    doc = open_new(engine, files / "Export.pdf")
    engine.call("tools.exportText", doc=doc, path=str(files / "text.txt"))
    expect("folio" in (files / "text.txt").read_text(encoding="utf-8"), "text export")
    pieces = files / "pieces"
    pieces.mkdir()
    split, _ = engine.call("pages.split", doc=doc, dir=str(pieces), mode="each")
    expect(len(split["files"]) == 3 and len(list(pieces.iterdir())) == 3, "split into pages")
    engine.call("doc.close", doc=doc)
    return "3 pages"


def check_word(engine: Engine, files: Path) -> str:
    doc = open_new(engine, files / "Word.pdf")
    target = files / "report.docx"
    engine.call("tools.exportWord", doc=doc, path=str(target))
    engine.call("doc.close", doc=doc)
    expect(target.read_bytes()[:2] == b"PK", "Word file")
    return f"{target.stat().st_size} bytes"


def check_ocr(engine: Engine, files: Path) -> str:
    status, _ = engine.call("tools.ocrStatus")
    expect(status.get("available") and {"spa", "eng"} <= set(status.get("languages", [])), "OCR language data")
    doc = engine.call("doc.open", path=str(make_scanned_pdf(files / "Scan.pdf")))[0]["doc"]
    expect(found(engine, doc, "Murcielago") == 0, "the page should be only a picture")
    out, _ = engine.call("tools.ocr", doc=doc)
    expect(out["recognized"] == 1 and out["words"] >= 10, f"recognition ({out.get('words')} words)")
    expect(found(engine, doc, "Murcielago") == 1, "recognized word")
    engine.call("doc.close", doc=doc)
    return f"{out['words']} words"


def check_protect(engine: Engine, files: Path) -> str:
    import pymupdf

    doc = open_new(engine, files / "Protect.pdf")
    engine.call("tools.protect", doc=doc, userPassword=PASSWORD, ownerPassword=PASSWORD + "-owner", permissions=ALL_PERMISSIONS)
    target = files / "protected.pdf"
    engine.call("doc.save", doc=doc, path=str(target))
    engine.call("doc.close", doc=doc)
    try:
        engine.call("doc.open", path=str(target))
        raise Failure("opened without its password")
    except EngineFailure as exc:
        expect(exc.code == "needs_password", f"asks for the password ({exc.code})")
    again = engine.call("doc.open", path=str(target), password=PASSWORD)[0]
    expect(len(again["pages"]) == 3, "opens with its password")
    engine.call("doc.close", doc=again["doc"])
    with pymupdf.open(str(target)) as check:
        expect(check.needs_pass and check.authenticate(PASSWORD), "the saved file needs its password")
        method = (check.metadata or {}).get("encryption") or ""
    expect("AES" in method, f"encryption ({method})")
    return method


def check_redaction(engine: Engine, files: Path) -> str:
    import pymupdf

    secret = "4471-SECRETO"
    doc = open_new(engine, files / "Redact.pdf", extra=(f"Codigo de acceso: {secret}",))
    marked, _ = engine.call("redact.find", doc=doc, query=secret)
    expect(marked["total"] == 1, "find the text to redact")
    engine.call("redact.apply", doc=doc)
    expect(found(engine, doc, secret) == 0, "the text is gone")
    target = files / "redacted.pdf"
    engine.call("doc.save", doc=doc, path=str(target))
    engine.call("doc.close", doc=doc)
    with pymupdf.open(str(target)) as check:
        expect(secret not in check[0].get_text() and "folio 1" in check[0].get_text(), "the saved file")
    return "removed"


def check_forms(engine: Engine, files: Path) -> str:
    import pymupdf

    doc = open_new(engine, files / "Form.pdf")
    out, _ = engine.call("form.addFields", doc=doc, page="p1", fields=[{"type": "text", "rect": [300, 40, 520, 62], "name": "nombre"}])
    engine.call("form.setValue", doc=doc, page="p1", id=out["widgets"][0]["id"], value="Folio check")
    target = files / "form-filled.pdf"
    engine.call("doc.save", doc=doc, path=str(target))
    engine.call("doc.close", doc=doc)
    with pymupdf.open(str(target)) as check:
        values = {w.field_name: w.field_value for w in check[0].widgets()}
    expect(values.get("nombre") == "Folio check", f"the field value ({values})")
    return "filled"


def sign_and_validate(engine: Engine, files: Path, name: str, source: dict[str, Any]) -> None:
    doc = open_new(engine, files / f"{name}.pdf")
    signed, _ = engine.call("sign.sign", doc=doc, source=source)
    expect(signed["signature"]["field"], "signature field")
    checked, _ = engine.call("sign.validate", doc=doc)
    expect(checked["signatures"] and checked["signatures"][0]["intact"] is True, "the signature is intact")
    engine.call("doc.close", doc=doc)


def check_sign_pfx(engine: Engine, files: Path) -> str:
    pfx = make_p12(files / "check.p12")
    info, _ = engine.call("sign.pfxInfo", path=str(pfx), password=PASSWORD)
    expect(info["subject"]["cn"] == "Folio Check Temporary", "certificate details")
    sign_and_validate(engine, files, "Signed-pfx", {"type": "pfx", "path": str(pfx), "password": PASSWORD})
    return "signed and intact"


def check_scan(engine: Engine, files: Path) -> str:
    photo = make_photo(files / "photo.jpg")
    added, _ = engine.call("scan.add", paths=[str(photo)])
    expect(added["detected"] is True, "the paper was found in the photo")
    info, jpeg = engine.call("scan.preview", job=added["job"], page="s1", size=300)
    expect(jpeg[:2] == b"\xff\xd8" and max(info["w"], info["h"]) == 300, "preview")
    built, _ = engine.call("scan.build", job=added["job"], pages=["s1"], style="gray")
    expect(len(built["pages"]) == 1, "the page in a PDF")
    engine.call("doc.close", doc=built["doc"])
    return "paper found, PDF built"


def check_sandbox_blocks(_engine: Engine, files: Path) -> str:
    """A file the user did not choose: a sandboxed process may not read it, even if it tries."""
    from folio_engine import winsandbox

    secret = make_pdf(files / "not-chosen.pdf", pages=1)
    probe = (
        "import sys\n"
        "try:\n"
        "    open(sys.argv[1], 'rb').read(4)\n"
        "    print('READ')\n"
        "except OSError:\n"
        "    print('DENIED')\n"
    )
    base = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
    container = winsandbox.open_container()
    try:
        job = winsandbox.Job(memory_limit=None)
        run = container.folder / ("c" * 16)
        run.mkdir(exist_ok=True)
        try:
            env = {name: os.environ[name] for name in ("SystemRoot", "LOCALAPPDATA", "USERPROFILE")}
            env.update(TEMP=str(run), TMP=str(run))
            child = winsandbox.spawn([str(base), "-I", "-c", probe, str(secret)], cwd=str(run), env=env, container=container, job=job)
            child.stdin.close()
            output = child.stdout.read().decode("ascii", "replace").strip()
            child.wait(60)
            child.close()
        finally:
            job.close()
            with contextlib.suppress(OSError):
                run.rmdir()
    finally:
        container.close()
    expect(output == "DENIED", f"the sandboxed process read the file ({output})")
    return "denied"


# ================================================================ Windows store

_POWERSHELL = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
_STORE_PREFIX = "CN=Folio Check Temporary"
# label -> New-SelfSignedCertificate options. Constants only: nothing from outside reaches PowerShell.
_STORE_KEYS = {
    "RSA": "-KeyAlgorithm RSA -KeyLength 2048",
    "ECDSA": "-KeyAlgorithm ECDSA_nistP256",
    "CSP": "-KeyAlgorithm RSA -KeyLength 2048 -KeySpec Signature -Provider 'Microsoft Enhanced RSA and AES Cryptographic Provider'",
}


def _powershell(script: str) -> str:
    # Windows PowerShell 5.1 has the PKI module; PSModulePath from PowerShell 7 would break it.
    env = {key: value for key, value in os.environ.items() if key.upper() != "PSMODULEPATH"}
    done = subprocess.run([str(_POWERSHELL), "-NoProfile", "-NonInteractive", "-Command", script],
                          capture_output=True, text=True, timeout=240, env=env, check=False)
    return done.stdout


def remove_store_certificates() -> int:
    """Deletes the check certificates (and their keys) and returns how many are left."""
    match = "{ $_.Subject -like '" + _STORE_PREFIX + "*' }"
    out = _powershell(
        "$ErrorActionPreference = 'SilentlyContinue';"
        f"Get-ChildItem Cert:\\CurrentUser\\My | Where-Object {match} | ForEach-Object {{ Remove-Item -Path $_.PSPath -DeleteKey }};"
        f"Get-ChildItem Cert:\\CurrentUser\\CA | Where-Object {match} | ForEach-Object {{ Remove-Item -Path $_.PSPath }};"
        "$left = 0;"
        "foreach ($store in (Get-ChildItem Cert:\\CurrentUser | ForEach-Object Name)) {"
        f" $left += @(Get-ChildItem \"Cert:\\CurrentUser\\$store\" | Where-Object {match}).Count }};"
        "\"left=$left\""
    )
    left = re.search(r"left=(\d+)", out)
    return int(left.group(1)) if left else -1


def create_store_certificates() -> dict[str, str]:
    parts = ["$ErrorActionPreference = 'Stop'"]
    for label, options in _STORE_KEYS.items():
        parts.append(
            f"$c = New-SelfSignedCertificate -Type Custom -Subject '{_STORE_PREFIX} {label}, C=ES' "
            f"-KeyUsage DigitalSignature,NonRepudiation {options} -CertStoreLocation Cert:\\CurrentUser\\My "
            f"-NotAfter (Get-Date).AddDays(1); '{label}=' + $c.Thumbprint"
        )
    made = dict(re.findall(r"^(\w+)=([0-9A-F]{40})\s*$", _powershell("; ".join(parts)), flags=re.M))
    expect(set(made) == set(_STORE_KEYS), "could not create the test certificates")
    return made


def check_store(engine: Engine, files: Path, thumbprints: dict[str, str]) -> Callable[[Engine, Path], str]:
    def run(_engine: Engine, _files: Path) -> str:
        listed, _ = engine.call("sign.certificates")
        mine = {c["thumbprint"] for c in listed["certificates"]}
        expect(set(thumbprints.values()) <= mine, "the store certificates are listed")
        for label, thumbprint in thumbprints.items():
            sign_and_validate(engine, files, f"Signed-{label}", {"type": "store", "thumbprint": thumbprint})
        return ", ".join(thumbprints)
    return run


# ================================================================ local AI


class _HttpsOnly(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        if not newurl.startswith("https://"):
            raise Failure("redirected to an address without HTTPS")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def basic_models() -> list[dict[str, Any]]:
    """The basic AI tier as listed in src/main/aicatalog.ts (the app's only source of model addresses)."""
    text = (ROOT / "src" / "main" / "aicatalog.ts").read_text(encoding="utf-8")
    consts = dict(re.findall(r"^const (\w+) = '([^']+)'$", text, flags=re.M))
    section = text[text.index("export const AI_COMPONENTS"):text.index("export const AI_TIERS")]
    tiers = text[text.index("export const AI_TIERS"):]
    wanted = re.findall(r"'([\w-]+)'", re.search(r"basic: \[([^\]]*)\]", tiers).group(1))
    components = []
    for block in re.findall(r"^  '?[\w-]+'?: \{$(.*?)^  \},?$", section, flags=re.M | re.S):
        cid = re.search(r"id: '([\w-]+)'", block).group(1)
        if cid not in wanted:
            continue
        files = []
        for name, repo, rev, file, size, sha in re.findall(
            r"name: '([a-z]+\.[a-z]+)',\s*url: hf\((\w+), (\w+), '([\w./-]+)'\),\s*size: ([\d_]+),\s*sha256: '([0-9a-f]{64})'", block
        ):
            url = f"https://huggingface.co/{consts[repo]}/resolve/{consts[rev]}/{file}"
            files.append({"name": name, "url": url, "size": int(size.replace("_", "")), "sha256": sha})
        ctx = re.search(r"ctx: ([\d_]+)", block)
        components.append({
            "id": cid, "kind": re.search(r"kind: '(\w+)'", block).group(1),
            "version": re.search(r"version: '([\w.-]+)'", block).group(1), "files": files,
            **({"ctx": int(ctx.group(1).replace("_", ""))} if ctx else {}),
        })
    expect(sorted(c["id"] for c in components) == sorted(wanted) and all(c["files"] for c in components),
           "could not read the AI catalog")
    return components


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(8 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_models(models: Path, components: list[dict[str, Any]]) -> None:
    opener = urllib.request.build_opener(_HttpsOnly)
    for component in components:
        folder = models / component["id"] / component["version"]
        folder.mkdir(parents=True, exist_ok=True)
        for item in component["files"]:
            target = folder / item["name"]
            if target.is_file() and target.stat().st_size == item["size"] and _sha256(target) == item["sha256"]:
                continue
            partial = target.with_suffix(".part")
            digest = hashlib.sha256()
            started = time.monotonic()
            request = urllib.request.Request(item["url"], headers={"User-Agent": "folio-build-check"})
            with opener.open(request, timeout=120) as answer, partial.open("wb") as out:
                while chunk := answer.read(8 << 20):
                    out.write(chunk)
                    digest.update(chunk)
            # Exact size and SHA-256 from the catalog, like the app (supply chain).
            if partial.stat().st_size != item["size"] or digest.hexdigest() != item["sha256"]:
                partial.unlink()
                raise Failure(f"{component['id']}/{item['name']}: size or SHA-256 does not match the catalog")
            partial.replace(target)
            print(f"  got {component['id']}/{item['name']} ({item['size'] >> 20} MB, {time.monotonic() - started:.0f} s)", flush=True)


def ai_configuration(components: list[dict[str, Any]]) -> dict[str, Any]:
    """What the app sends in ``ai.configure`` (src/main/ipc.ts, configureAi)."""
    models = {}
    for c in components:
        models[c["kind"]] = {
            "files": {f["name"].rsplit(".", 1)[0]: f"{c['id']}/{c['version']}/{f['name']}" for f in c["files"]},
            "size": sum(f["size"] for f in c["files"]),
            **({"ctx": c["ctx"]} if "ctx" in c else {}),
        }
    return models


AI_TEXT = (
    "Acta de la reunion del equipo de proyecto.",
    "La proxima reunion sera el martes 14 de enero en la sala Atenea.",
    "Asistentes: Maria Garcia Lopez y Jorge Ruiz Martin.",
    "Contacto: maria.garcia@example.com, telefono 612 345 678.",
    "Direccion: Calle Mayor 12, 28013 Madrid.",
    "El presupuesto aprobado para el trimestre es de 18.500 euros.",
)


def ai_checks(models: list[dict[str, Any]]) -> list[tuple[str, Callable[[Engine, Path], str]]]:
    state: dict[str, str] = {}

    def configure(engine: Engine, files: Path) -> str:
        # With the graphics card wanted: this also runs the GPU probe in its sandboxed process.
        result, _ = engine.call("ai.configure", gpu=True, models=ai_configuration(models))
        expect(result.get("cpuOk") is True, "the processor is not accepted for the AI (AVX2)")
        expect(result.get("kinds") == ["embed", "llm", "ner", "unwarp"], f"models accepted ({result.get('kinds')})")
        state["doc"] = open_new(engine, files / "Minutes.pdf", pages=1, extra=AI_TEXT)
        return f"graphics card: {result.get('gpu') or 'none, processor'}"

    def search(engine: Engine, _files: Path) -> str:
        out, _ = engine.call("ai.search", doc=state["doc"], query="Cuando es la proxima reunion?", timeout=AI_TIMEOUT)
        expect(out["results"], "no results")
        return f"{len(out['results'])} results"

    def personal(engine: Engine, _files: Path) -> str:
        out, _ = engine.call("redact.candidates", doc=state["doc"], useAi=True, timeout=AI_TIMEOUT)
        names = [c["text"] for c in out["candidates"] if c["source"] == "ai" and c["kind"] == "name"]
        expect(out["ai"] is True and names, "no names found by the model")
        return ", ".join(names)

    def ask(engine: Engine, _files: Path) -> str:
        out, _ = engine.call("ai.ask", doc=state["doc"], question="Que dia es la proxima reunion?", timeout=AI_TIMEOUT)
        answer = out["text"].strip()
        expect(any(word in answer.lower() for word in ("martes", "tuesday", "14")), f"unexpected answer: {answer[:200]!r}")
        return answer[:120].replace("\n", " ")

    def translate(engine: Engine, _files: Path) -> str:
        out, _ = engine.call("ai.transform", doc=state["doc"], text="Muchas gracias por su ayuda con el proyecto.",
                             action="translate", target="en", timeout=AI_TIMEOUT)
        text = out["text"].strip()
        expect("thank" in text.lower(), f"unexpected translation: {text[:200]!r}")
        engine.call("doc.close", doc=state["doc"])
        return text[:120].replace("\n", " ")

    def unwarp(engine: Engine, files: Path) -> str:
        # Without the model the page is left as it was, silently: compare both.
        photo = make_photo(files / "photo-ai.jpg")
        pages = []
        for flatten in (False, True):
            added, _ = engine.call("scan.add", paths=[str(photo)])
            built, _ = engine.call("scan.build", job=added["job"], pages=["s1"], style="color", unwarp=flatten, timeout=AI_TIMEOUT)
            pages.append(engine.call("page.render", doc=built["doc"], page=built["pages"][0]["id"], scale=0.5)[1])
            engine.call("doc.close", doc=built["doc"])
        expect(pages[0] != pages[1], "the model did not change the page")
        return "page flattened by the model"

    return [
        ("AI: models accepted", configure),
        ("AI: search by meaning (ONNX Runtime)", search),
        ("AI: personal data model (ONNX Runtime)", personal),
        ("AI: question (llama.cpp)", ask),
        ("AI: translation (llama.cpp)", translate),
        ("AI: page unwarping (ONNX Runtime)", unwarp),
    ]


# ================================================================ main


def run(engine: Engine, files: Path, name: str, check: Callable[[Engine, Path], str]) -> None:
    started = time.monotonic()
    try:
        detail = check(engine, files)
    except Exception as exc:  # noqa: BLE001 - reported with the step name
        time.sleep(1)  # the engine's last messages
        for line in engine.notes[-40:]:
            print(f"  engine: {line[:300]}")
        raise SystemExit(f"FAILED: {name}: {type(exc).__name__}: {exc}") from exc
    print(f"  ok  {name} ({time.monotonic() - started:.1f} s): {detail}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packaged", type=Path, help="packaged engine (folio-engine.exe)")
    parser.add_argument("--expect-emulated", action="store_true", help="fail unless the engine reports x64 emulation on Arm")
    parser.add_argument("--full", action="store_true", help="every native part of the engine")
    parser.add_argument("--store-cert", action="store_true", help="also sign with throwaway Windows store certificates")
    parser.add_argument("--models", type=Path, help="folder with the basic AI models (layout of the app)")
    parser.add_argument("--fetch-models", action="store_true", help="download the basic AI models into --models first")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # AI answers have accents
    if (args.store_cert or args.models) and not args.full:
        parser.error("--store-cert and --models need --full")
    if args.fetch_models and not args.models:
        parser.error("--fetch-models needs --models")

    checks: list[tuple[str, Callable[[Engine, Path], str]]] = [("blank document", check_blank)]
    if args.full:
        checks += [
            ("open, edit and save", check_open_edit_save),
            ("export text and split pages", check_export_split),
            ("export to Word (own sandboxed process)", check_word),
            ("OCR (Tesseract)", check_ocr),
            ("password protection", check_protect),
            ("redaction", check_redaction),
            ("forms", check_forms),
            ("sign with a .pfx and validate", check_sign_pfx),
            ("scan a photo (OpenCV, own sandboxed process)", check_scan),
            ("sandbox blocks files not chosen", check_sandbox_blocks),
        ]
    models_dir = args.models.resolve() if args.models else None
    if models_dir:
        components = basic_models()
        if args.fetch_models:
            fetch_models(models_dir, components)
        checks += ai_checks(components)

    thumbprints: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="folio-check-") as tmp:
        work, files = Path(tmp) / "work", Path(tmp) / "files"
        work.mkdir()
        files.mkdir()
        resources = ROOT / "resources"  # OCR language data (scripts/fetch-tessdata.ps1)
        options = ["--workdir", str(work), "--resources", str(resources)]
        if models_dir:
            options += ["--models", str(models_dir)]
        if args.packaged:
            command, cwd = [str(args.packaged.resolve()), *options], args.packaged.resolve().parent
        else:
            # -E -s: like the app, ignore PYTHON* variables and user site packages.
            command, cwd = [sys.executable, "-E", "-s", "-m", "folio_engine", *options], ROOT / "engine"
        try:
            if args.store_cert:
                remove_store_certificates()  # left over from an interrupted run
                thumbprints = create_store_certificates()
            engine = Engine(command, cwd)
            try:
                from folio_engine import winsandbox

                expect(engine.hello.get("sandbox") is True, f"the engine is not sandboxed ({engine.hello.get('sandboxIssue')})")
                expect(winsandbox.process_in_appcontainer(int(engine.hello.get("pid", 0))) is True, "the engine is not in its AppContainer")
                print(f"engine: sandboxed, PyMuPDF {engine.hello.get('pymupdf')}", flush=True)
                if thumbprints:
                    position = next(i for i, (name, _) in enumerate(checks) if name.startswith("sign with a .pfx")) + 1
                    checks.insert(position, ("sign with Windows store certificates", check_store(engine, files, thumbprints)))
                for name, check in checks:
                    run(engine, files, name, check)
            finally:
                engine.close()
        finally:
            if args.store_cert:
                left = remove_store_certificates()
                if left != 0:
                    print(f"WARNING: {left} test certificates left in the store", flush=True)
    for line in engine.notes:
        if "emulado" in line or "ERROR" in line or "no autorizado" in line:
            print(f"  engine: {line[:200]}")
    if any("no autorizado" in line for line in engine.notes):
        raise SystemExit("ERROR: the sandboxed engine asked for something it was not allowed")
    if args.expect_emulated and not any("emulado" in line for line in engine.notes):
        raise SystemExit("ERROR: the engine did not detect x64 emulation on Arm")
    print(f"engine: {len(checks)} checks passed")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Failure as exc:
        sys.exit(f"FAILED: {exc}")
