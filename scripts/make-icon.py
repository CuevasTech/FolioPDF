"""Genera el icono de la aplicacion (build/icon.ico y build/icon.png)."""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw

SIZE = 1024

# Solo el dibujo de la hoja; el fondo con degradado se compone aparte porque el
# lector de SVG de MuPDF no aplica degradados.
GLYPH = """<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256">
  <path d="M86 58h58a12 12 0 0 1 8.5 3.5l30 30A12 12 0 0 1 186 100v84a16 16 0 0 1-16 16H86a16 16 0 0 1-16-16V74a16 16 0 0 1 16-16z"
        fill="#ffffff" fill-opacity="0.22" stroke="#ffffff" stroke-width="11" stroke-linejoin="round"/>
  <path d="M144 60v26a12 12 0 0 0 12 12h28" fill="none" stroke="#ffffff" stroke-width="11" stroke-linejoin="round"/>
  <path d="M98 134h60M98 160h38" fill="none" stroke="#ffffff" stroke-width="11" stroke-linecap="round"/>
</svg>"""


def main(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    # Degradado diagonal: cuatro pixeles interpolados hasta el tamano final.
    corners = Image.new("RGB", (2, 2))
    corners.putdata([(0x78, 0x84, 0xF0), (0x58, 0x64, 0xDC), (0x58, 0x64, 0xDC), (0x39, 0x43, 0xAC)])
    background = corners.resize((SIZE, SIZE), Image.BILINEAR).convert("RGBA")
    mask = Image.new("L", (SIZE, SIZE), 0)
    margin = SIZE * 12 // 256
    ImageDraw.Draw(mask).rounded_rectangle((margin, margin, SIZE - margin, SIZE - margin), radius=SIZE * 56 // 256, fill=255)
    icon = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    icon.paste(background, (0, 0), mask)

    doc = pymupdf.open(stream=GLYPH.encode("utf-8"), filetype="svg")
    pix = doc[0].get_pixmap(matrix=pymupdf.Matrix(SIZE / 256, SIZE / 256), alpha=True)
    glyph = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGBA").resize((SIZE, SIZE), Image.LANCZOS)
    icon = Image.alpha_composite(icon, glyph)

    icon.resize((512, 512), Image.LANCZOS).save(out_dir / "icon.png")
    icon.resize((256, 256), Image.LANCZOS).save(
        out_dir / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    )
    print("icono generado en", out_dir)


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("build"))
