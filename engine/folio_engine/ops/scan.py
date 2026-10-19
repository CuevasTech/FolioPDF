"""Digitalizar documentos: fotos de papeles o paginas de un escaner hechas PDF.

La interfaz recorre estas operaciones:

1. ``scan.add``: las imagenes (fotos o lo que acaba de leer el escaner) entran
   en un trabajo de digitalizacion. De cada una se guarda una copia de trabajo
   y, si son fotos, se buscan las esquinas del papel (scanworker.py, en un
   proceso aparte).
2. ``scan.preview`` y ``scan.update``: vista previa de cada pagina y ajustes
   (esquinas a mano, giro).
3. ``scan.build``: el PDF, con el acabado elegido y, si se pide, el texto
   reconocido (OCR). Se abre como documento nuevo sin guardar.
4. ``scan.discard``: tirar el trabajo (``scan.build`` tambien lo tira).

Las imagenes no salen de la carpeta privada del motor y se borran al acabar.
"""

from __future__ import annotations

import io
import json
import logging
import re
import secrets
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Literal

import pymupdf
from PIL import Image
from pydantic import Field, StringConstraints

from .. import naming, scanimage, scanworker
from ..errors import EngineError, invalid, not_found
from ..i18n import _, language
from ..imaging import MAX_IMAGE_BYTES, sniff
from ..models import Params, PathStr
from ..server import Ctx, Reply, method
from ..session import DocSession
from .document import describe
from .ocr import Language
from .pages import _Inputs, _stamp

log = logging.getLogger("folio.engine.scan")

# Topes: ningun trabajo puede crecer sin limite (A06:2025).
MAX_JOBS = 4
MAX_JOB_PAGES = 300
MAX_RESULT_BYTES = 1024 * 1024

JobId = Annotated[str, StringConstraints(pattern=r"^j[0-9a-f]{16}$")]
ScanPageId = Annotated[str, StringConstraints(pattern=r"^s[0-9]{1,4}$")]
Style = Literal["auto", "color", "gray", "bw", "original"]
Coord = Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)]
Corner = Annotated[list[Coord], Field(min_length=2, max_length=2)]
Quad = Annotated[list[Corner], Field(min_length=4, max_length=4)]


@dataclass
class ScanPage:
    id: str
    work: Path  # imagen de trabajo
    small: Path  # copia reducida: vistas previas y ajuste de las esquinas
    size: tuple[int, int]
    dpi: float  # la del escaner; 0 en una foto (no se sabe su tamano real)
    found: list[list[float]] | None = None  # lo que encontro la busqueda del papel
    quad: list[list[float]] | None = None  # lo que se aplica (None: la imagen entera)
    rotate: int = 0

    def describe(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "quad": self.quad,
            "found": self.found is not None,
            "rotate": self.rotate,
            "width": self.size[0],
            "height": self.size[1],
        }


@dataclass
class ScanJob:
    id: str
    folder: Path
    pages: dict[str, ScanPage] = field(default_factory=dict)
    counter: int = 0

    def discard(self) -> None:
        shutil.rmtree(self.folder, ignore_errors=True)


def _jobs(ctx: Ctx) -> dict[str, ScanJob]:
    return ctx.engine.scans


def _job(ctx: Ctx, job_id: str) -> ScanJob:
    job = _jobs(ctx).get(job_id)
    if job is None:
        raise not_found(_("Esta digitalización ya no está abierta."))
    return job


def _page(job: ScanJob, page_id: str) -> ScanPage:
    page = job.pages.get(page_id)
    if page is None:
        raise not_found(_("Esa página ya no está en la digitalización."))
    return page


def _new_job(ctx: Ctx) -> ScanJob:
    jobs = _jobs(ctx)
    while len(jobs) >= MAX_JOBS:  # el mas antiguo, olvidado (la interfaz ya no lo usa)
        jobs.pop(next(iter(jobs))).discard()
    job_id = f"j{secrets.token_hex(8)}"
    folder = ctx.engine.work_root / job_id
    folder.mkdir()
    job = ScanJob(job_id, folder)
    jobs[job_id] = job
    return job


def _forget_job(ctx: Ctx, job: ScanJob) -> None:
    _jobs(ctx).pop(job.id, None)
    job.discard()


def _keep(job: ScanJob, image: Image.Image, dpi: float) -> ScanPage:
    """Guarda la imagen (ya validada y limpia) en el trabajo como pagina nueva."""
    job.counter += 1
    page_id = f"s{job.counter}"
    work = job.folder / f"{page_id}.jpg"
    small = job.folder / f"{page_id}-small.jpg"
    # Pillow no copia los metadatos de la foto (posicion GPS, camara...) al
    # guardarla de nuevo: no llegan al PDF (A01:2025).
    image.save(work, "JPEG", quality=94)
    scanimage.scaled(image, scanimage.PREVIEW_SIDE).save(small, "JPEG", quality=90)
    page = ScanPage(page_id, work, small, image.size, dpi)
    job.pages[page_id] = page
    return page


def _open_own(path: Path) -> Image.Image:
    """Una imagen que guardo el propio motor en el trabajo."""
    with Image.open(path) as image:
        return image.convert("RGB")


def _detect(ctx: Ctx, job: ScanJob, pages: list[ScanPage]) -> bool:
    """Busca el papel en cada foto (en un proceso aparte). False si no se ha podido buscar."""
    ctx.progress(0, 0, _("Buscando los bordes del papel…"))
    token = secrets.token_hex(6)
    request = job.folder / f"detect-{token}.json"
    result = job.folder / f"detect-{token}.out.json"
    copies = []
    try:
        names = []
        for n, page in enumerate(pages):
            name = f"det-{n}.png"
            copy = job.folder / name
            scanimage.scaled(_open_own(page.small), scanimage.DETECT_SIDE).save(copy, "PNG")
            copies.append(copy)
            names.append(name)
        request.write_text(json.dumps({"images": names}), encoding="utf-8")
        code = ctx.engine.host.detect_documents(ctx.engine.work_root, request, result, ctx.check_cancel)
        if code != scanworker.EXIT_OK or not result.is_file() or result.stat().st_size > MAX_RESULT_BYTES:
            log.warning("no se pudieron buscar los bordes del papel (codigo %s)", code)
            return False
        # Lo que devuelve el proceso auxiliar se comprueba como cualquier dato de fuera.
        answer = json.loads(result.read_text(encoding="utf-8"))
        found = answer.get("results") if isinstance(answer, dict) else None
        if not isinstance(found, list) or len(found) != len(pages):
            return False
        for page, item in zip(pages, found):
            quad = item.get("quad") if isinstance(item, dict) else None
            try:
                if quad is not None:
                    scanimage.check_quad(quad)
                    page.found = page.quad = [[float(x), float(y)] for x, y in quad]
            except (EngineError, TypeError, ValueError):
                page.found = page.quad = None
        return True
    except (OSError, ValueError) as exc:
        log.warning("no se pudieron buscar los bordes del papel (%s)", type(exc).__name__)
        return False
    finally:
        for leftover in (request, result, *copies):
            try:
                leftover.unlink(missing_ok=True)
            except OSError:
                log.warning("no se pudo borrar un temporal de la digitalizacion")


# ----------------------------------------------------------------- operaciones


class AddParams(Params):
    job: JobId | None = None
    paths: Annotated[list[PathStr], Field(min_length=1, max_length=100)]
    # Fotos: se busca el papel. Del escaner llega ya recto y a su tamano.
    detect: bool = True
    dpi: Annotated[int, Field(ge=0, le=1200)] = 0


@method("scan.add", AddParams)
def scan_add(ctx: Ctx, p: AddParams) -> dict[str, Any]:
    """Anade imagenes a una digitalizacion (nueva si no se indica)."""
    job = _job(ctx, p.job) if p.job else _new_job(ctx)
    if len(job.pages) + len(p.paths) > MAX_JOB_PAGES:
        raise invalid(_("Como mucho {count} páginas por documento.", count=MAX_JOB_PAGES))
    added: list[ScanPage] = []
    try:
        with _Inputs(ctx, MAX_IMAGE_BYTES) as inputs:
            # Todas las rutas se validan (y se copian) antes de empezar.
            for n, raw in enumerate(p.paths):
                inputs.add(raw, path=raw, index=n)
            for n, (local, fetched) in enumerate(inputs.files):
                ctx.progress(
                    n, len(inputs.files),
                    _("Preparando las imágenes ({done} de {total})…", done=n + 1, total=len(inputs.files)),
                )
                detail = {"path": p.paths[n], "index": n}
                if sniff(local) != "image":
                    raise EngineError(
                        "invalid_file", _("«{name}» no es una imagen compatible.", name=fetched.name), **detail
                    )
                try:
                    image = scanimage.open_photo(local)
                except EngineError as exc:
                    raise EngineError(
                        exc.code, _("«{name}»: {message}", name=fetched.name, message=exc.message), **detail
                    ) from exc
                added.append(_keep(job, image, p.dpi))
        found = _detect(ctx, job, added) if p.detect and added else True
    except BaseException:
        # Todo o nada: si una imagen falla no queda media tanda dentro.
        for page in added:
            job.pages.pop(page.id, None)
            for path in (page.work, page.small):
                path.unlink(missing_ok=True)
        if not job.pages and p.job is None:
            _forget_job(ctx, job)
        raise
    return {"job": job.id, "pages": [page.describe() for page in added], "detected": found}


class PreviewParams(Params):
    job: JobId
    page: ScanPageId
    # result: como quedara la pagina; source: la foto entera (para ajustar las esquinas).
    view: Literal["result", "source"] = "result"
    style: Style = "auto"
    size: Annotated[int, Field(ge=64, le=1600)] = 480
    unwarp: bool = False  # aplanar paginas curvadas con la IA (si esta activada)


@method("scan.preview", PreviewParams)
def scan_preview(ctx: Ctx, p: PreviewParams) -> Reply:
    """Vista previa de una pagina en JPEG."""
    page = _page(_job(ctx, p.job), p.page)
    image = _open_own(page.small)
    if p.view == "source":
        out = scanimage.scaled(image, p.size)
    else:
        straight = scanimage.straighten(image, page.quad, page.rotate, p.size)
        out = scanimage.enhance(_unwarp(ctx, straight) if p.unwarp else straight, p.style)
    if out.mode == "1":
        out = out.convert("L")
    buf = io.BytesIO()
    out.save(buf, "JPEG", quality=85)
    return Reply({"w": out.width, "h": out.height}, buf.getvalue())


class UpdateParams(Params):
    job: JobId
    page: ScanPageId
    quad: Quad | None = None
    whole: bool = False  # la imagen entera, sin recortar
    redetect: bool = False  # volver a lo que encontro la busqueda
    rotate: Literal[0, 90, 180, 270] | None = None


@method("scan.update", UpdateParams)
def scan_update(ctx: Ctx, p: UpdateParams) -> dict[str, Any]:
    """Ajusta el recorte o el giro de una pagina."""
    page = _page(_job(ctx, p.job), p.page)
    if p.whole:
        page.quad = None
    elif p.redetect:
        page.quad = page.found
    elif p.quad is not None:
        scanimage.check_quad(p.quad)
        page.quad = [[float(x), float(y)] for x, y in p.quad]
    if p.rotate is not None:
        page.rotate = p.rotate
    return page.describe()


class BuildParams(Params):
    job: JobId
    pages: Annotated[list[ScanPageId], Field(min_length=1, max_length=MAX_JOB_PAGES)]
    style: Style = "auto"
    ocr: bool = False
    languages: Annotated[tuple[Language, ...], Field(min_length=1, max_length=3)] = ("spa", "eng")
    unwarp: bool = False


@method("scan.build", BuildParams)
def scan_build(ctx: Ctx, p: BuildParams) -> dict[str, Any]:
    """El PDF con las paginas en el orden indicado; se abre como documento nuevo."""
    job = _job(ctx, p.job)
    if len(set(p.pages)) != len(p.pages):
        raise invalid(_("Hay páginas repetidas."))
    pages = [_page(job, page_id) for page_id in p.pages]
    if p.ocr:
        # Antes de preparar nada: si faltan los datos de idioma, se dice ya.
        from .ocr import _require_data

        _require_data(ctx, list(dict.fromkeys(p.languages)))
    out = pymupdf.open()
    try:
        for n, page in enumerate(pages):
            ctx.progress(n, len(pages), _("Preparando la página {page} de {total}…", page=n + 1, total=len(pages)))
            image = _open_own(page.work)
            width, height = scanimage.straight_size(image.size, page.quad)
            if page.rotate in (90, 270):
                width, height = height, width
            size = scanimage.page_size(width, height, page.dpi)
            straight = scanimage.straighten(image, page.quad, page.rotate, scanimage.pixels_for(size, p.style))
            final = scanimage.enhance(_unwarp(ctx, straight) if p.unwarp else straight, p.style)
            del image
            target = out.new_page(width=size[0], height=size[1])
            target.insert_image(target.rect, stream=scanimage.encode(final), keep_proportion=False)
        _stamp(out)
        data = out.tobytes(garbage=3, deflate=True)
    finally:
        out.close()
    session = DocSession.from_bytes(ctx.engine.work_root, data, _("Documento digitalizado.pdf"), ctx.engine.host)
    ctx.engine.sessions[session.id] = session
    if p.ocr:
        from .ocr import OcrParams, tools_ocr

        try:
            tools_ocr(ctx, OcrParams(doc=session.id, languages=p.languages))
        except BaseException:
            # Cancelado o fallido: el documento a medias no se queda abierto.
            ctx.engine.sessions.pop(session.id, None)
            session.close()
            raise
    _forget_job(ctx, job)
    # Nombre propuesto a partir del texto (reglas, sin IA): "Factura 2026-10-03.pdf".
    if p.ocr:
        suggestion = naming.suggest(session.page(session.page_ids[0]).get_text("text"), language())
        if suggestion:
            session.name = f"{_safe_name(suggestion)}.pdf"
    return describe(session)


_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _safe_name(name: str) -> str:
    """Sin caracteres que Windows no admite en un nombre de archivo."""
    return _UNSAFE.sub("-", name).strip(" .")[:120] or "Documento"


def _unwarp(ctx: Ctx, image: Image.Image) -> Image.Image:
    """Aplana una pagina curvada con el modelo de la IA. Sin IA, la deja como esta."""
    rgb = image.convert("RGB")
    side = max(rgb.size)
    if side > 4096:  # el proceso de IA no acepta imagenes mayores
        scale = 4096 / side
        rgb = rgb.resize((max(16, int(rgb.width * scale)), max(16, int(rgb.height * scale))), Image.Resampling.LANCZOS)
    try:
        reply, data = ctx.engine.host.ai("unwarp", {"w": rgb.width, "h": rgb.height}, rgb.tobytes())
    except EngineError as exc:
        if exc.code == "ai_unavailable":
            return image
        raise
    width, height = reply.get("w"), reply.get("h")
    if not isinstance(width, int) or not isinstance(height, int) or len(data) != width * height * 3:
        return image
    out = Image.frombytes("RGB", (width, height), data)
    return out.resize(image.size, Image.Resampling.LANCZOS) if out.size != image.size else out


class JobParams(Params):
    job: JobId


@method("scan.discard", JobParams)
def scan_discard(ctx: Ctx, p: JobParams) -> dict[str, Any]:
    """Tira una digitalizacion sin crear el PDF."""
    job = _jobs(ctx).get(p.job)
    if job is not None:
        _forget_job(ctx, job)
    return {}
