"""Sistemas de coordenadas de una pagina.

Se manejan tres espacios, todos en puntos tipograficos (1/72 de pulgada):

* **D (visualizacion)**: lo que ve el usuario, con la rotacion de la pagina ya
  aplicada. Origen arriba a la izquierda, eje Y hacia abajo. Es el unico
  espacio que conoce la interfaz.
* **U (sin girar)**: el que usan las funciones de PyMuPDF para anotaciones,
  campos y extraccion de texto. Igual que D pero ignorando /Rotate.
* **PDF**: el del flujo de contenido de la pagina. Origen abajo a la izquierda
  del MediaBox, eje Y hacia arriba.

Las conversiones se calculan aqui a partir de la transformacion real de la
pagina en MuPDF (la misma con la que se dibuja) y no con las propiedades de
PyMuPDF: ``Page.transformation_matrix`` ignora el desplazamiento del CropBox en
paginas giradas, y ``rotation_matrix`` no tiene en cuenta ni /UserUnit ni un
CropBox que se salga del MediaBox. Con esas propiedades lo que se anade a una
pagina asi quedaria desplazado o a otra escala.
"""

from __future__ import annotations

import math
from typing import Iterable, Sequence

import pymupdf
from pymupdf import mupdf


def _page_transform(page: pymupdf.Page) -> pymupdf.Matrix | None:
    """Matriz PDF -> D con la que MuPDF dibuja la pagina (giro, recorte y escala incluidos)."""
    try:
        pdfpage = pymupdf._as_pdf_page(page)
        box = mupdf.FzRect(mupdf.FzRect.Fixed_UNIT)
        ctm = mupdf.FzMatrix()
        mupdf.pdf_page_transform(pdfpage, box, ctm)
        matrix = pymupdf.Matrix(ctm.a, ctm.b, ctm.c, ctm.d, ctm.e, ctm.f)
        if abs(matrix.a * matrix.d - matrix.b * matrix.c) < 1e-9:
            return None
        return matrix
    except Exception:  # noqa: BLE001 - se recurre a las cajas de la pagina
        return None


class Geom:
    __slots__ = ("rotation", "u2d", "d2u", "u2pdf", "size_d", "size_u", "api2d", "d2api")

    def __init__(self, page: pymupdf.Page) -> None:
        # Las propiedades de PyMuPDF que leen o cambian una caja ya existente
        # (Annot.rect, Annot.vertices, Annot.set_rect, Widget.rect) giran con sus
        # propias matrices. En paginas normales son identicas a las de abajo;
        # con /UserUnit o un CropBox fuera del MediaBox, no. Esos valores se
        # convierten siempre con las suyas (metodos ``api_*``).
        self.api2d = pymupdf.Matrix(page.rotation_matrix)
        self.d2api = pymupdf.Matrix(page.derotation_matrix)
        rotation = page.rotation % 360
        self.rotation = rotation
        rect = page.rect  # tamano real en pantalla
        self.size_d = (rect.width, rect.height)
        wu, hu = (rect.width, rect.height) if rotation in (0, 180) else (rect.height, rect.width)
        self.size_u = (wu, hu)
        if rotation == 90:
            self.u2d = pymupdf.Matrix(0, 1, -1, 0, hu, 0)
        elif rotation == 180:
            self.u2d = pymupdf.Matrix(-1, 0, 0, -1, wu, hu)
        elif rotation == 270:
            self.u2d = pymupdf.Matrix(0, -1, 1, 0, 0, wu)
        else:
            self.u2d = pymupdf.Matrix(1, 0, 0, 1, 0, 0)
        self.d2u = ~self.u2d
        pdf2d = _page_transform(page)
        if pdf2d is not None:
            self.u2pdf = self.u2d * ~pdf2d
        else:
            cropbox = page.cropbox
            mediabox = page.mediabox
            # PyMuPDF expone el CropBox con la Y ya invertida respecto al borde
            # superior del MediaBox; se deshace para obtener coordenadas PDF.
            self.u2pdf = pymupdf.Matrix(1, 0, 0, -1, cropbox.x0, mediabox.y1 - cropbox.y0)

    # ---- D -> U
    def point_u(self, p: Sequence[float]) -> pymupdf.Point:
        return pymupdf.Point(p[0], p[1]) * self.d2u

    def rect_u(self, r: Sequence[float]) -> pymupdf.Rect:
        return (pymupdf.Rect(r[0], r[1], r[2], r[3]).normalize() * self.d2u).normalize()

    def quad_u(self, q: Sequence[Sequence[float]]) -> pymupdf.Quad:
        return pymupdf.Quad(*(pymupdf.Point(p[0], p[1]) * self.d2u for p in q))

    def angle_u(self, angle_d: float) -> float:
        """Angulo (horario) de un elemento en U a partir de su angulo en D."""
        return (angle_d - self.rotation) % 360

    # ---- U -> D
    def point_d(self, p: Sequence[float]) -> list[float]:
        q = pymupdf.Point(p[0], p[1]) * self.u2d
        return [round(q.x, 3), round(q.y, 3)]

    def rect_d(self, r: Sequence[float]) -> list[float]:
        out = (pymupdf.Rect(r[0], r[1], r[2], r[3]) * self.u2d).normalize()
        return [round(out.x0, 3), round(out.y0, 3), round(out.x1, 3), round(out.y1, 3)]

    def quad_d(self, q: pymupdf.Quad | Sequence[Sequence[float]]) -> list[list[float]]:
        points = (q.ul, q.ur, q.ll, q.lr) if isinstance(q, pymupdf.Quad) else q
        return [self.point_d(p) for p in points]

    def angle_d(self, angle_u: float) -> float:
        return (angle_u + self.rotation) % 360

    def vector_d(self, v: Sequence[float]) -> list[float]:
        """Gira un vector de direccion de U a D (sin traslacion)."""
        m = self.u2d
        return [round(v[0] * m.a + v[1] * m.c, 6), round(v[0] * m.b + v[1] * m.d, 6)]

    # ---- valores de las propiedades de PyMuPDF (ver el comentario de __init__)
    def api_rect_d(self, r: Sequence[float]) -> list[float]:
        """Caja leida de ``Annot.rect`` o ``Widget.rect``, en D."""
        out = (pymupdf.Rect(r[0], r[1], r[2], r[3]) * self.api2d).normalize()
        return [round(out.x0, 3), round(out.y0, 3), round(out.x1, 3), round(out.y1, 3)]

    def api_point_d(self, p: Sequence[float]) -> list[float]:
        """Punto leido de ``Annot.vertices``, en D."""
        q = pymupdf.Point(p[0], p[1]) * self.api2d
        return [round(q.x, 3), round(q.y, 3)]

    def api_rect_u(self, r: Sequence[float]) -> pymupdf.Rect:
        """Caja en D lista para ``Annot.set_rect`` o ``Widget.rect``."""
        return (pymupdf.Rect(r[0], r[1], r[2], r[3]).normalize() * self.d2api).normalize()

    def api_point_u(self, p: Sequence[float]) -> pymupdf.Point:
        return pymupdf.Point(p[0], p[1]) * self.d2api

    def api_to_u(self, r: Sequence[float]) -> pymupdf.Rect:
        """Caja leida de una propiedad de PyMuPDF, llevada al espacio U de este modulo."""
        return (pymupdf.Rect(r[0], r[1], r[2], r[3]) * self.api2d * self.d2u).normalize()

    # ---- colocacion de contenido
    def placement(
        self,
        source_box: Sequence[float],
        center_u: Sequence[float],
        size: tuple[float, float],
        angle_u: float,
    ) -> pymupdf.Matrix:
        """Matriz ``cm`` que lleva un XObject a una caja de la pagina.

        ``source_box`` es la caja del XObject en sus propias coordenadas (Y hacia
        arriba). La caja de destino tiene tamano ``size`` y centro ``center_u``
        en U, y esta girada ``angle_u`` grados en sentido horario.
        """
        sx0, sy0, sx1, sy1 = source_box
        sw, sh = sx1 - sx0, sy1 - sy0
        w, h = size
        # coordenadas del XObject (Y hacia arriba) -> caja local (Y hacia abajo)
        to_local = pymupdf.Matrix(w / sw, 0, 0, -h / sh, -sx0 * w / sw, h + sy0 * h / sh)
        to_center = pymupdf.Matrix(1, 0, 0, 1, -w / 2, -h / 2)
        rad = math.radians(angle_u)
        cos, sin = math.cos(rad), math.sin(rad)
        rotate = pymupdf.Matrix(cos, sin, -sin, cos, 0, 0)  # horario con Y hacia abajo
        to_page = pymupdf.Matrix(1, 0, 0, 1, center_u[0], center_u[1])
        return to_local * to_center * rotate * to_page * self.u2pdf


def box_corners(center: Sequence[float], size: tuple[float, float], angle: float) -> list[list[float]]:
    """Vertices (sup-izq, sup-der, inf-der, inf-izq) de una caja girada ``angle`` grados horarios."""
    w, h = size
    rad = math.radians(angle)
    cos, sin = math.cos(rad), math.sin(rad)
    out = []
    for dx, dy in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)):
        out.append([center[0] + dx * cos - dy * sin, center[1] + dx * sin + dy * cos])
    return out


def bounds(points: Iterable[Sequence[float]]) -> list[float]:
    xs, ys = zip(*((p[0], p[1]) for p in points))
    return [min(xs), min(ys), max(xs), max(ys)]


def fmt(value: float) -> str:
    """Numero para un flujo de contenido PDF: sin notacion cientifica ni ceros sobrantes."""
    text = f"{value:.5f}".rstrip("0").rstrip(".")
    return text if text and text != "-0" else "0"


def matrix_operand(m: pymupdf.Matrix) -> str:
    return " ".join(fmt(v) for v in (m.a, m.b, m.c, m.d, m.e, m.f))
