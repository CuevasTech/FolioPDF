"""Busca el documento en fotos de papeles, en un proceso aparte.

Se lanza como ``python -m folio_engine --scan-worker <peticion.json> <resultado.json>``
(o el ejecutable empaquetado con los mismos argumentos). Usa OpenCV y NumPy,
que no pueden cargarse dentro del motor (ver wordworker.py), y va aislado igual
que la conversion a Word: con el motor aislado lo lanza el intermediario dentro
del mismo AppContainer.

La peticion la escribe el motor en su carpeta privada: la lista de imagenes que
hay junto a ella (PNG reducidas que el propio motor ha descodificado y vuelto a
guardar: aqui nunca llega el archivo original del usuario). El resultado dice,
para cada una, donde estan las cuatro esquinas del papel en coordenadas
relativas (0..1, en el orden superior izquierda, superior derecha, inferior
derecha, inferior izquierda) o ``null`` si no se encuentra con seguridad.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import sys
from typing import Any

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_UNAVAILABLE = 3
EXIT_FAILED = 4

_NAME = re.compile(r"^det-\d{1,4}\.png$")
MAX_IMAGES = 500
MAX_REQUEST = 256 * 1024
MAX_IMAGE_BYTES = 40 * 1024 * 1024
# Lado mayor con el que se busca el papel: de sobra para encontrar sus bordes y rapido.
WORK_SIZE = 720
# El papel ocupa al menos esta parte de la foto: lo mas pequeno suele ser otra cosa.
MIN_AREA = 0.18
# Si ocupa casi toda la foto no hay fondo que quitar: mejor la foto entera.
MAX_AREA = 0.985
# Diferencia minima de color entre el papel y lo que lo rodea (en Lab de OpenCV).
MIN_CONTRAST = 18.0


def _libs() -> tuple[Any, Any]:
    import cv2
    import numpy as np

    return cv2, np


def _order(points: Any) -> Any:
    """Las cuatro esquinas en el orden de las agujas del reloj, empezando por la superior izquierda."""
    _, np = _libs()
    pts = np.asarray(points, dtype=np.float64).reshape(4, 2)
    center = pts.mean(axis=0)
    # Con el eje y hacia abajo, el angulo crece en el sentido de las agujas del reloj.
    pts = pts[np.argsort(np.arctan2(pts[:, 1] - center[1], pts[:, 0] - center[0]))]
    return np.roll(pts, -int(np.argmin(pts.sum(axis=1))), axis=0)


def _contrast(quad: Any, lab: Any) -> float:
    """Diferencia entre el color justo dentro del borde y el de justo fuera.

    Con la mediana, que no se deja llevar por el texto: el borde de un papel
    sobre una mesa separa colores distintos; el contorno de un bloque de texto
    (lo que se encontraria en un escaneo o en una foto sin fondo) tiene papel
    blanco a los dos lados.
    """
    cv2, np = _libs()
    height, width = lab.shape[:2]
    mask = np.zeros((height, width), np.uint8)
    cv2.fillConvexPoly(mask, np.round(quad).astype(np.int32), 255)
    size = max(3, round(0.025 * max(height, width)))
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (size, size))
    outer = cv2.subtract(cv2.dilate(mask, kernel), mask)
    inner = cv2.subtract(mask, cv2.erode(mask, kernel))
    if cv2.countNonZero(outer) < 50 or cv2.countNonZero(inner) < 50:
        return 0.0
    inside = np.median(lab[inner > 0], axis=0).astype(np.float64)
    outside = np.median(lab[outer > 0], axis=0).astype(np.float64)
    return float(np.linalg.norm(inside - outside))


def _score(quad: Any, width: int, height: int, lab: Any) -> float:
    """Lo grande que es el cuadrilatero si parece una hoja de papel; 0 si no lo parece."""
    cv2, np = _libs()
    area = abs(float(cv2.contourArea(quad.astype(np.float32))))
    share = area / float(width * height)
    if not MIN_AREA <= share <= MAX_AREA:
        return 0.0
    if not cv2.isContourConvex(quad.astype(np.float32).reshape(-1, 1, 2)):
        return 0.0
    sides = [float(np.linalg.norm(quad[i] - quad[(i + 1) % 4])) for i in range(4)]
    if min(sides) < 8:
        return 0.0
    # Una hoja vista con algo de perspectiva: esquinas no muy lejos de los 90
    # grados y lados opuestos parecidos.
    for i in range(4):
        before, here, after = quad[i - 1], quad[i], quad[(i + 1) % 4]
        a, b = before - here, after - here
        cosine = float(np.dot(a, b)) / (float(np.linalg.norm(a) * np.linalg.norm(b)) + 1e-9)
        if not 50.0 <= math.degrees(math.acos(max(-1.0, min(1.0, cosine)))) <= 130.0:
            return 0.0
    if min(sides[0], sides[2]) / max(sides[0], sides[2]) < 0.55 or min(sides[1], sides[3]) / max(sides[1], sides[3]) < 0.55:
        return 0.0
    # El propio marco de la foto no es un papel.
    margin_x, margin_y = 0.012 * width, 0.012 * height
    framed = sum(
        1 for x, y in quad if (x < margin_x or x > width - margin_x) and (y < margin_y or y > height - margin_y)
    )
    if framed >= 3 or _contrast(quad, lab) < MIN_CONTRAST:
        return 0.0
    return share


def _quads(binary: Any, lab: Any, *, retrieval: int, box_fallback: bool) -> list[tuple[float, Any]]:
    """Cuadrilateros candidatos en una imagen en blanco y negro (bordes o zona del papel)."""
    cv2, _ = _libs()
    height, width = binary.shape[:2]
    found: list[tuple[float, Any]] = []
    contours, _ = cv2.findContours(binary, retrieval, cv2.CHAIN_APPROX_SIMPLE)
    total = float(width * height)
    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:8]:
        hull = cv2.convexHull(contour)
        if cv2.contourArea(hull) < MIN_AREA * total:
            break
        perimeter = cv2.arcLength(hull, True)
        quad = None
        for tolerance in (0.015, 0.02, 0.03, 0.04, 0.05, 0.065, 0.08):
            approx = cv2.approxPolyDP(hull, tolerance * perimeter, True)
            if len(approx) == 4:
                quad = approx.reshape(4, 2)
                break
            if len(approx) < 4:
                break
        if quad is None and box_fallback:
            # Esquinas redondeadas o dobladas: el rectangulo que mejor la envuelve.
            quad = cv2.boxPoints(cv2.minAreaRect(hull))
        if quad is None:
            continue
        quad = _order(quad)
        score = _score(quad, width, height, lab)
        if score > 0:
            found.append((score, quad))
    return found


def find_document(image: Any) -> list[list[float]] | None:
    """Esquinas del papel en una imagen BGR de OpenCV, relativas a su tamano; None si no se ve claro."""
    cv2, np = _libs()
    height, width = image.shape[:2]
    scale = min(1.0, WORK_SIZE / max(height, width))
    if scale < 1.0:
        image = cv2.resize(image, (max(1, round(width * scale)), max(1, round(height * scale))), interpolation=cv2.INTER_AREA)
    height, width = image.shape[:2]
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    lab = cv2.cvtColor(cv2.GaussianBlur(image, (3, 3), 0), cv2.COLOR_BGR2LAB)
    candidates: list[tuple[float, Any]] = []

    # 1. Bordes: el contorno del papel contra lo que hay detras, con varios
    #    umbrales (fotos con poco contraste y con mucho).
    gray = cv2.GaussianBlur(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    median = float(np.median(gray))
    for low, high in ((0.66 * median, 1.33 * median), (20.0, 60.0), (50.0, 150.0)):
        low = max(0.0, low)
        edges = cv2.Canny(gray, int(low), int(min(255.0, max(low + 10.0, high))))
        edges = cv2.dilate(edges, kernel, iterations=1)
        edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel, iterations=2)
        candidates += _quads(edges, lab, retrieval=cv2.RETR_LIST, box_fallback=False)

    # 2. Zona del papel: claro y poco saturado frente a una mesa o un fondo de color.
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    paper = cv2.GaussianBlur(cv2.subtract(hsv[:, :, 2], hsv[:, :, 1]), (7, 7), 0)
    _, mask = cv2.threshold(paper, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=2)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=3)
    candidates += _quads(mask, lab, retrieval=cv2.RETR_EXTERNAL, box_fallback=True)

    if not candidates:
        return None
    _, quad = max(candidates, key=lambda item: item[0])
    return [[round(min(1.0, max(0.0, float(x) / width)), 5), round(min(1.0, max(0.0, float(y) / height)), 5)] for x, y in quad]


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        return EXIT_USAGE
    source, dest = argv
    # Las rutas las fija el motor (su carpeta privada de trabajo); aun asi se
    # comprueba lo minimo antes de abrir nada (A01:2025).
    if not (os.path.isabs(source) and os.path.isabs(dest)):
        return EXIT_USAGE
    if not (source.lower().endswith(".json") and dest.lower().endswith(".json") and os.path.isfile(source)):
        return EXIT_USAGE
    folder = os.path.dirname(source)
    if os.path.dirname(dest) != folder:
        return EXIT_USAGE
    try:
        with open(source, "rb") as fh:
            raw = fh.read(MAX_REQUEST + 1)
        request = json.loads(raw.decode("utf-8")) if len(raw) <= MAX_REQUEST else None
    except (OSError, ValueError):
        return EXIT_USAGE
    names = request.get("images") if isinstance(request, dict) else None
    # Solo nombres de forma fija dentro de la misma carpeta: nada de rutas (A01:2025).
    if not isinstance(names, list) or not 0 < len(names) <= MAX_IMAGES:
        return EXIT_USAGE
    if not all(isinstance(name, str) and _NAME.match(name) for name in names):
        return EXIT_USAGE

    # Nada de lo que escriban las bibliotecas debe salir de aqui (A09:2025).
    sink = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115 - vive lo que el proceso
    sys.stdout = sys.stderr = sink
    logging.disable(logging.CRITICAL)
    try:
        cv2, np = _libs()
        cv2.setNumThreads(2)
    except Exception:  # noqa: BLE001
        return EXIT_UNAVAILABLE

    results: list[dict[str, Any]] = []
    for name in names:
        quad = None
        try:
            with open(os.path.join(folder, name), "rb") as fh:
                data = fh.read(MAX_IMAGE_BYTES + 1)
            if len(data) <= MAX_IMAGE_BYTES:
                image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
                if image is not None and image.size:
                    quad = find_document(image)
        except Exception:  # noqa: BLE001 - una imagen rara no impide las demas
            quad = None
        results.append({"quad": quad})
    try:
        tmp = dest + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"results": results}, fh)
        os.replace(tmp, dest)
    except OSError:
        return EXIT_FAILED
    return EXIT_OK
