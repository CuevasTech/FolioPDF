"""Genera el aviso de componentes de terceros que acompana a la aplicacion.

    engine\\.venv\\Scripts\\python.exe scripts\\third_party_notices.py

Recoge lo que se distribuye dentro del instalador:

* los paquetes de Python del motor (los fijados en engine/requirements.txt),
  con su licencia y los textos de licencia que traen;
* el propio Python y el cargador de PyInstaller, que van dentro del motor
  empaquetado;
* los paquetes de la interfaz que se compilan dentro de la aplicacion (lo que
  importa src/ y sus dependencias);
* los datos de idioma del OCR.

Electron anade sus propias licencias (LICENSE.electron.txt y
LICENSES.chromium.html) al empaquetar. El resultado va a
build/THIRD-PARTY-NOTICES.txt y el instalador lo copia junto a Folio.exe.
"""

from __future__ import annotations

import json
import re
import sys
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "build" / "THIRD-PARTY-NOTICES.txt"
LICENSE_NAMES = ("LICENSE", "LICENCE", "COPYING", "NOTICE", "COPYRIGHT")
# Imports de src/ que no son paquetes de terceros (alias del proyecto y Node). Electron
# se excluye por nombre exacto: un prefijo dejaria fuera electron-updater, que si viaja.
LOCAL = ("@/", "@shared/", "node:")


def _is_local(spec: str) -> bool:
    return spec.startswith(LOCAL) or spec == "electron" or spec.startswith("electron/")
RULE = "=" * 78


def _license_of(dist: metadata.Distribution) -> str:
    meta = dist.metadata
    expression = meta.get("License-Expression")
    if expression:
        return expression.strip()
    declared = (meta.get("License") or "").strip()
    if declared and len(declared) <= 80 and "\n" not in declared:
        return declared
    classifiers = [c.split("::")[-1].strip() for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
    return ", ".join(classifiers) or "ver el texto de licencia"


def _license_texts(dist: metadata.Distribution) -> list[tuple[str, str]]:
    out = []
    for item in dist.files or []:
        parts = item.parts
        upper = item.name.upper()
        in_info = parts[0].endswith(".dist-info")
        # Algunos paquetes (onnxruntime) traen la licencia y los avisos en su carpeta, no en dist-info.
        top_level = len(parts) == 2 and (upper.startswith(LICENSE_NAMES) or upper.startswith("THIRDPARTYNOTICES"))
        if (in_info and ("licenses" in parts or upper.startswith(LICENSE_NAMES))) or top_level:
            try:
                text = Path(str(item.locate())).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            out.append((item.name, text.strip()))
    return out


def python_packages() -> list[tuple[str, str, str, list[tuple[str, str]]]]:
    text = (ROOT / "engine" / "requirements.txt").read_text("utf-8")
    pins = re.findall(r"(?m)^([A-Za-z0-9_.\-]+)==([^\s\\]+)", text)
    # Fijados por direccion (nombre @ url, con su hash): la version es la instalada.
    pins += [(name, metadata.version(name)) for name in re.findall(r"(?m)^([A-Za-z0-9_.\-]+) @ \S+", text)]
    out = []
    for name, version in sorted(pins, key=lambda p: p[0].lower()):
        dist = metadata.distribution(name)
        if dist.version != version:
            raise SystemExit(f"{name}: instalado {dist.version}, fijado {version}. Instala engine/requirements.txt antes.")
        out.append((dist.metadata["Name"], version, _license_of(dist), _license_texts(dist)))
    return out


def _package_dir(name: str, parent: Path | None) -> Path:
    if parent is not None and (parent / "node_modules" / name / "package.json").exists():
        return parent / "node_modules" / name
    return ROOT / "node_modules" / name


def npm_packages() -> list[tuple[str, str, str, list[tuple[str, str]]]]:
    roots: set[str] = set()
    pattern = re.compile(r"""(?:from|import)\s+['"]([^'".][^'"]*)['"]""")
    for source in (ROOT / "src").rglob("*.ts*"):
        for spec in pattern.findall(source.read_text("utf-8")):
            if _is_local(spec):
                continue
            match = re.match(r"(@[^/]+/[^/]+|[^/]+)", spec)
            if match:
                roots.add(match.group(1))
    seen: dict[str, Path] = {}
    stack: list[tuple[str, Path | None]] = [(name, None) for name in sorted(roots)]
    while stack:
        name, parent = stack.pop()
        if name in seen:
            continue
        folder = _package_dir(name, parent)
        data = json.loads((folder / "package.json").read_text("utf-8"))
        seen[name] = folder
        for dependency in data.get("dependencies") or {}:
            stack.append((dependency, folder))
    out = []
    for name in sorted(seen):
        folder = seen[name]
        data = json.loads((folder / "package.json").read_text("utf-8"))
        license_id = data.get("license") if isinstance(data.get("license"), str) else "ver el texto de licencia"
        texts = []
        for item in sorted(folder.iterdir()):
            if item.is_file() and item.name.upper().startswith(LICENSE_NAMES):
                texts.append((item.name, item.read_text(encoding="utf-8", errors="replace").strip()))
        out.append((name, str(data.get("version", "")), license_id, texts))
    return out


def runtime_parts() -> list[tuple[str, str, str, list[tuple[str, str]]]]:
    """Lo que va en el motor ademas de los paquetes: Python, el cargador de PyInstaller y llama.cpp."""
    out = []
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    texts = [("LICENSE.txt", python_license.read_text("utf-8", errors="replace").strip())] if python_license.exists() else []
    out.append(("Python", sys.version.split()[0], "PSF-2.0", texts))
    try:
        dist = metadata.distribution("pyinstaller")
        out.append(("PyInstaller (cargador)", dist.version, "GPL-2.0-or-later con excepcion para el cargador",
                    _license_texts(dist)))
    except metadata.PackageNotFoundError:
        pass
    # llama-cpp-python lleva compilado llama.cpp (llama.dll y ggml*.dll) sin su licencia.
    out.append(("llama.cpp y ggml (dentro de llama-cpp-python)", "b11312", "MIT", [("LICENSE", LLAMA_CPP_LICENSE)]))
    return out


LLAMA_CPP_LICENSE = """MIT License

Copyright (c) 2023-2026 The ggml authors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE."""


def _section(title: str, items: list[tuple[str, str, str, list[tuple[str, str]]]]) -> list[str]:
    lines = [RULE, title, RULE, ""]
    for name, version, license_id, _texts in items:
        lines.append(f"  {name} {version} - {license_id}")
    lines.append("")
    for name, version, license_id, texts in items:
        lines += ["-" * 78, f"{name} {version}", f"Licencia: {license_id}", "-" * 78]
        if not texts:
            lines.append("(El paquete no incluye un archivo de licencia aparte; su licencia es la indicada.)")
        for file_name, text in texts:
            lines += [f"[{file_name}]", "", text, ""]
        lines.append("")
    return lines


def main() -> int:
    lines = [
        "Folio - Componentes de terceros",
        "",
        "Folio incluye los componentes siguientes, cada uno con su licencia. Los textos",
        "de licencia van a continuacion, tal como los distribuye cada proyecto.",
        "",
        "PyMuPDF y MuPDF se distribuyen bajo la licencia GNU AGPL-3.0 (o una licencia",
        "comercial de Artifex). Su codigo fuente esta en https://github.com/pymupdf/PyMuPDF",
        "y https://mupdf.com. Electron y Chromium incluyen sus licencias en",
        "LICENSE.electron.txt y LICENSES.chromium.html, en la carpeta de la aplicacion.",
        "",
        "Datos de idioma del OCR (eng, spa y osd de tessdata_fast,",
        "https://github.com/tesseract-ocr/tessdata_fast): Apache-2.0.",
        "",
    ]
    lines += _section("Motor: entorno de ejecucion", runtime_parts())
    lines += _section("Motor: paquetes de Python", python_packages())
    lines += _section("Interfaz: paquetes compilados dentro de la aplicacion", npm_packages())
    # Saltos LF en cualquier sistema: regenerarlo sin cambios no deja diferencias en git.
    OUT.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8", newline="\n")
    print(f"{OUT.relative_to(ROOT)}: {OUT.stat().st_size // 1024} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
