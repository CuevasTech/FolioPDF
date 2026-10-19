"""Firma digital con certificado: certificados disponibles, campos, firma y validacion.

Este modulo solo traduce las peticiones de la interfaz (parametros validados,
coordenadas de pantalla, historial del documento). La criptografia y el acceso
a las claves estan en ``folio_engine.sign``, que se carga la primera vez que se
usa: importarlo cuesta en torno a 0,7 s y no debe retrasar el arranque.
"""

from __future__ import annotations

import base64
import logging
import secrets
from datetime import datetime
from typing import Annotated, Any, Callable, Literal

import pymupdf
from pydantic import Field, StringConstraints

from .. import locks
from ..errors import EngineError, invalid, unsupported
from ..geometry import Geom
from ..i18n import _, short_date
from ..imaging import load_image
from ..models import DocParams, PageId, PageParams, Params, PathStr, Rect, Secret
from ..server import Ctx, method
from ..session import DocSession

log = logging.getLogger("folio.engine.ops.signing")

# Huella tal como la devuelve ``sign.certificates``: 40 digitos hexadecimales.
Thumbprint = Annotated[str, StringConstraints(pattern=r"^[0-9A-Fa-f]{40}$")]
# Manejador de ventana para los dialogos de Windows. Llega de fuera: se acota al
# rango de los manejadores de ventana y despues se comprueba que exista.
WindowHandle = Annotated[int, Field(strict=True, ge=1, le=0xFFFFFFFF)]
# Nombre de un campo que ya esta en el PDF: lo puso otro programa, asi que solo
# se limita su longitud y se usa unicamente para compararlo.
ExistingFieldName = Annotated[str, StringConstraints(min_length=1, max_length=500)]
# Nombre de un campo nuevo: letras, cifras, espacios, guiones y subrayados. Sin
# puntos, que en un formulario PDF separan niveles de la jerarquia de campos.
NewFieldName = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[\w \-]{1,60}$")]
Note = Annotated[str, StringConstraints(max_length=200)]
TimestampUrl = Annotated[str, StringConstraints(max_length=2048)]

_MAX_LISTED = 500
_MAX_FIELDS = 2000
_MAX_SIGNATURE_IMAGE = 8 * 1024 * 1024
_MIN_VISIBLE = 1.0  # por debajo de un punto el campo se considera invisible


# ------------------------------------------------------------------ certificados


@method("sign.certificates")
def sign_certificates(ctx: Ctx, p: Params) -> dict[str, Any]:
    """Certificados personales del usuario que tienen clave privada (motor no aislado)."""
    _require_direct(ctx)
    from ..sign import certs

    return {"certificates": certs.store_certificates(_MAX_LISTED)}


class PfxInfoParams(Params):
    path: PathStr
    password: Secret


@method("sign.pfxInfo", PfxInfoParams)
def sign_pfx_info(ctx: Ctx, p: PfxInfoParams) -> dict[str, Any]:
    """Datos del certificado de un archivo .pfx/.p12 (la clave se descarta en el acto)."""
    _require_direct(ctx)
    from ..sign import certs

    return certs.load_pfx(p.path, p.password).info.payload(hardware=False)


def _require_direct(ctx: Ctx) -> None:
    # El motor aislado no puede abrir el almacen de Windows ni archivos de
    # claves: eso lo atiende el intermediario sin pasar por aqui.
    if ctx.engine.host.sandboxed:
        raise unsupported(_("Esta operación no está disponible en el motor aislado."))


# ------------------------------------------------------------------------ campos


def _widgets(session: DocSession) -> list[tuple[str, int, pymupdf.Rect, bool]]:
    """Campos de firma con representacion en alguna pagina: ``(nombre, pagina, caja U, firmado)``."""
    cached = session.cache.get("sign.widgets")
    if cached is not None:
        return cached
    out: list[tuple[str, int, pymupdf.Rect, bool]] = []
    doc = session.doc
    if doc.is_form_pdf:
        for index in range(len(session.page_ids)):
            try:
                widgets = list(doc[index].widgets(types=[pymupdf.PDF_WIDGET_TYPE_SIGNATURE]))
            except Exception:  # noqa: BLE001 - formulario estropeado en esa pagina
                continue
            for widget in widgets:
                name = widget.field_name or ""
                if not name or len(name) > 500:
                    continue
                out.append((name, index, pymupdf.Rect(widget.rect), bool(widget.is_signed)))
                if len(out) >= _MAX_FIELDS:
                    break
            if len(out) >= _MAX_FIELDS:
                break
    session.cache["sign.widgets"] = out  # la cache se vacia sola cada vez que cambia el documento
    return out


def _visible(rect: pymupdf.Rect) -> bool:
    return rect.is_valid and rect.width >= _MIN_VISIBLE and rect.height >= _MIN_VISIBLE


def _field_payload(session: DocSession, item: tuple[str, int, pymupdf.Rect, bool]) -> dict[str, Any]:
    name, index, rect, signed = item
    shown = Geom(session.doc[index]).api_rect_d(rect) if _visible(rect) else None  # rect viene de Widget.rect
    return {"name": name, "page": session.page_ids[index], "rect": shown, "signed": signed}


@method("sign.fields", DocParams)
def sign_fields(ctx: Ctx, p: DocParams) -> dict[str, Any]:
    session = ctx.engine.session(p.doc)
    return {"fields": [_field_payload(session, item) for item in _widgets(session)]}


def _target_box(page: pymupdf.Page, rect: Rect) -> tuple[tuple[float, float, float, float], tuple[float, float]]:
    """Caja en coordenadas del PDF y tamano en pantalla de una zona dada en coordenadas de visualizacion."""
    from ..sign.appearance import MIN_SIDE

    geom = Geom(page)
    wanted = pymupdf.Rect(rect).normalize() & pymupdf.Rect(0, 0, geom.size_d[0], geom.size_d[1])
    if wanted.is_empty or wanted.width < MIN_SIDE or wanted.height < MIN_SIDE:
        raise invalid(_("La zona de la firma es demasiado pequeña o queda fuera de la página."))
    box = (geom.rect_u((wanted.x0, wanted.y0, wanted.x1, wanted.y1)) * geom.u2pdf).normalize()
    return (box.x0, box.y0, box.x1, box.y1), (wanted.width, wanted.height)


def _preflight(session: DocSession, data: bytes) -> None:
    """Comprueba que el motor podra reabrir el resultado antes de anadirlo al archivo de trabajo."""
    problem = EngineError("internal", _("El resultado de la operación no es un PDF utilizable. No se ha cambiado nada."))
    try:
        probe = pymupdf.open(stream=data, filetype="pdf")
    except Exception:  # noqa: BLE001
        raise problem from None
    try:
        # needs_pass se lee una sola vez y antes de autenticar (ver session.py).
        locked = bool(probe.needs_pass)
        if locked and not probe.authenticate(session.password or ""):
            raise problem
        if probe.page_count != len(session.page_ids):
            raise problem
    finally:
        probe.close()


def _append(
    session: DocSession, label: str, base: bytes, result: bytes, *, dirty: list[str], seals: bool
) -> None:
    """Confirma como revision lo que ``result`` anade a ``base``.

    Solo se acepta un resultado que empiece exactamente por los bytes que ya
    habia: es la garantia de que las firmas anteriores siguen intactas.
    """
    if len(result) <= len(base) or memoryview(result)[: len(base)] != base:
        raise EngineError("internal", _("La operación habría alterado el contenido ya firmado. No se ha cambiado nada."))
    _preflight(session, result)
    revision = session.revisions[session.cur]
    count = len(session.revisions)
    try:
        session.append_revision(label, bytes(memoryview(result)[len(base):]), dirty=dirty, seals=seals)
    except BaseException:
        # Fallo al escribir (por ejemplo, disco lleno): se deja el archivo de
        # trabajo como estaba para que el documento siga siendo utilizable.
        log.error("no se pudo confirmar la revision en el archivo de trabajo")
        if len(session.revisions) == count:
            try:
                with open(revision.file, "r+b") as handle:
                    handle.truncate(revision.length)
                session.rollback()
            except Exception:  # noqa: BLE001
                log.error("no se pudo restaurar el archivo de trabajo")
        raise


def _has_signatures_now(session: DocSession) -> bool:
    """Indica si el documento, tal como esta ahora, lleva alguna firma.

    Se mira el documento abierto y no ``session.signed``: ese indicador no se
    recalcula al deshacer, y aqui decide si se puede reescribir el archivo. Ante
    cualquier duda se responde que si, que es lo que impide reescribir.
    """
    try:
        if session.doc.get_sigflags() > 0:
            return True
        return any(signed for _name, _index, _rect, signed in _widgets(session))
    except Exception:  # noqa: BLE001
        return True


def _on_current(session: DocSession, action: Callable[[bytes], Any], *, compact: bool = False) -> tuple[bytes, Any]:
    """Ejecuta ``action`` sobre los bytes confirmados del documento.

    Con ``compact`` (al firmar), si el archivo de trabajo arrastra estados
    anteriores de esta sesion y no hay firmas validas que conservar (no tiene
    ninguna, o las que tenia ya las rompio un cambio), antes se reescribe
    limpio. Un documento firmado se guarda byte a byte: sin este paso, una
    pagina borrada o un texto quitado antes de firmar seguirian dentro del
    archivo firmado, recuperables cortandolo por una revision anterior.

    Si pyHanko no puede leer el PDF (MuPDF es mas tolerante con archivos
    danados) y no hay firmas que conservar, el documento tambien se reescribe
    y se reintenta una vez.
    """
    from ..sign import pdfdoc

    start = session.cur
    rewritten = False

    def rewrite(label: str) -> None:
        nonlocal rewritten
        with session.edit(label) as tx:
            tx.force_full = True
        rewritten = True

    try:
        if compact and session.history_in_file and not (session.chain_intact and _has_signatures_now(session)):
            rewrite(_("Preparar para firmar"))
        data = session.current_bytes()
        try:
            return data, action(data)
        except pdfdoc.Unreadable:
            if rewritten or (session.chain_intact and _has_signatures_now(session)):
                raise
            rewrite(_("Reparar documento"))
            data = session.current_bytes()
            return data, action(data)
    except BaseException as exc:
        # La reescritura solo tenia sentido para firmar: si no se firma, se deshace.
        if rewritten and session.cur == start + 1:
            session.undo()
        if isinstance(exc, pdfdoc.Unreadable):
            if _has_signatures_now(session):
                raise unsupported(
                    _("La estructura de este PDF no permite añadirle una firma sin dañar las que ya tiene.")
                ) from None
            raise unsupported(_("La estructura de este PDF no permite añadirle una firma.")) from None
        raise


def _guard(operation: str, action: Callable[[], Any]) -> Any:
    """Ningun fallo inesperado sale con su texto: podria citar datos del certificado o del PDF.

    El gestor general del servidor registra la traza completa de lo que no sea
    un ``EngineError``; aqui se evita que eso ocurra con datos personales
    (A09:2025) y se devuelve un error generico (A10:2025).
    """
    try:
        return action()
    except EngineError:
        raise
    except MemoryError:
        raise
    except Exception as exc:  # noqa: BLE001
        log.error("fallo inesperado en %s (%s)", operation, type(exc).__name__)
        raise EngineError("internal", _("Se produjo un error inesperado al procesar la firma.")) from None


class AddFieldParams(PageParams):
    rect: Rect
    name: NewFieldName | None = None


@method("sign.addField", AddFieldParams)
def sign_add_field(ctx: Ctx, p: AddFieldParams) -> dict[str, Any]:
    """Anade un campo de firma vacio en la zona indicada."""
    session = ctx.engine.session(p.doc)
    # Crear un campo de formulario exige poder modificar y anotar el documento.
    session.require("modify")
    session.require("annotate")

    def run() -> dict[str, Any]:
        from ..sign import pdfdoc

        index = session.page_index(p.page)
        box, _size = _target_box(session.doc[index], p.rect)
        base, (name, result) = _on_current(
            session, lambda data: pdfdoc.add_field(data, session.password, name=p.name, page_index=index, box=box)
        )
        _append(session, _("Añadir campo de firma"), base, result, dirty=[p.page], seals=False)
        item = next((w for w in _widgets(session) if w[0] == name), None)
        field = _field_payload(session, item) if item else {"name": name, "page": p.page, "rect": None, "signed": False}
        return session.result([p.page], field=field)

    return _guard("sign.addField", run)


# ------------------------------------------------------------------------- firmar


class StoreSource(Params):
    type: Literal["store"]
    thumbprint: Thumbprint
    hwnd: WindowHandle | None = None


class PfxSource(Params):
    type: Literal["pfx"]
    path: PathStr
    password: Secret


Base64Der = Annotated[str, StringConstraints(min_length=4, max_length=64_000, pattern=r"^[A-Za-z0-9+/]+={0,2}$")]


class BrokeredSource(Params):
    """Clave en manos del intermediario (motor aislado): solo llegan certificados publicos."""

    type: Literal["brokered"]
    certificate: Base64Der
    chain: Annotated[list[Base64Der], Field(max_length=12)] = []
    untrustedChain: bool = False


class AppearanceOptions(Params):
    name: bool = True
    date: bool = True
    reason: bool = False
    location: bool = False
    id: bool = True
    label: bool = True
    border: bool = False


class SignParams(DocParams):
    source: Annotated[StoreSource | PfxSource | BrokeredSource, Field(discriminator="type")]
    field: ExistingFieldName | None = None
    page: PageId | None = None
    rect: Rect | None = None
    appearance: AppearanceOptions = AppearanceOptions()
    reason: Note | None = None
    location: Note | None = None
    contact: Note | None = None
    timestampUrl: TimestampUrl | None = None
    certify: bool = False
    # Bloquear el documento: despues no admite cambios ni mas firmas (ver pdfdoc.SignPlan).
    lock: bool = False
    # Firma de larga duracion (PAdES B-LTA): exige sello de tiempo y conexion.
    longTerm: bool = False


def _offset(moment: datetime) -> str:
    raw = moment.strftime("%z")  # +0200
    return f"{raw[:3]}:{raw[3:]}" if len(raw) == 5 else raw


@method("sign.sign", SignParams)
def sign_sign(ctx: Ctx, p: SignParams) -> dict[str, Any]:
    """Firma el documento con un certificado (PAdES, SHA-256 o superior)."""
    session = ctx.engine.session(p.doc)
    if p.field is not None and p.rect is not None:
        raise invalid(_("Indica un campo de firma existente o una zona nueva, no las dos cosas."))
    if p.rect is not None and p.page is None:
        raise invalid(_("Falta indicar la página donde colocar la firma."))
    if session.protection is not None or session.remove_protection:
        # Al guardar con un cambio de proteccion pendiente el PDF se reescribe
        # entero y la firma recien hecha quedaria rota.
        raise invalid(_("Hay un cambio de protección pendiente. Guarda el documento y fírmalo después."))
    # Una certificacion que deja rellenar y firmar admite firmar (tambien en un
    # campo nuevo); la proteccion con contrasena se comprueba como siempre.
    session.require_unlocked("sign")
    if p.field is not None:
        session.require_permission("form")  # rellenar un campo de firma ya existente
    else:
        session.require_permission("modify")
        session.require_permission("annotate")

    def run() -> dict[str, Any]:
        from ..sign import appearance, pdfdoc, signers, validation
        from ..sign.appearance import clean_text

        # --- donde va la firma
        page_id: str | None = None
        size: tuple[float, float] | None = None
        box = None
        index = 0
        if p.field is not None:
            item = next((w for w in _widgets(session) if w[0] == p.field), None)
            if item is not None:
                if item[3]:
                    raise invalid(_("Ese campo ya está firmado."))
                index = item[1]
                if _visible(item[2]):
                    page_id = session.page_ids[index]
                    rotated = session.doc[index].rotation % 180 == 90
                    size = (item[2].height, item[2].width) if rotated else (item[2].width, item[2].height)
        elif p.rect is not None and p.page is not None:
            index = session.page_index(p.page)
            box, size = _target_box(session.doc[index], p.rect)
            page_id = p.page
        elif p.page is not None:
            index = session.page_index(p.page)

        host = ctx.engine.host
        # --- sello de tiempo: la direccion se valida antes de cargar ninguna clave
        timestamper = None
        if p.timestampUrl and p.timestampUrl.strip():
            timestamper = host.timestamper(p.timestampUrl.strip())
        if p.longTerm and timestamper is None:
            raise invalid(_("La firma de larga duración necesita un sello de tiempo: configura un servidor en Ajustes."))

        # --- con que certificado (antes de tocar nada: puede rechazarse)
        ctx.check_cancel()
        if isinstance(p.source, BrokeredSource) != host.sandboxed:
            # La clave la tiene el intermediario si y solo si el motor esta aislado.
            raise invalid(_("El origen del certificado no es válido."))
        if isinstance(p.source, BrokeredSource):
            try:
                certificate = base64.b64decode(p.source.certificate, validate=True)
                chain = [base64.b64decode(item, validate=True) for item in p.source.chain]
            except ValueError:
                raise invalid(_("El certificado no es válido.")) from None
            signer, warnings = signers.brokered_signer(host, certificate, chain, p.source.untrustedChain)
        elif isinstance(p.source, StoreSource):
            signer, warnings = signers.store_signer(p.source.thumbprint.upper(), p.source.hwnd)
        else:
            signer, warnings = signers.pfx_signer(p.source.path, p.source.password)
        stamp_file = None
        try:
            info = signer.info
            when = datetime.now().astimezone().replace(microsecond=0)
            reason = clean_text(p.reason or "", 200)
            location = clean_text(p.location or "", 200)
            contact = clean_text(p.contact or "", 200)

            # --- apariencia visible
            drawable = size is not None and min(size) >= appearance.MIN_SIDE
            if drawable and size is not None:
                image = None
                if ctx.binary:
                    if len(ctx.binary) > _MAX_SIGNATURE_IMAGE:
                        raise invalid(_("La imagen de la firma es demasiado grande."))
                    image = load_image(ctx.binary)
                options = p.appearance
                texts = {
                    "name": info.display_name if options.name else "",
                    "identifier": info.display_id if options.id else "",
                    "date": (short_date(when) + when.strftime(" %H:%M:%S ") + _offset(when)) if options.date else "",
                    "reason": reason if options.reason else "",
                    "location": location if options.location else "",
                }
                has_text = any(texts.values())
                if not has_text and image is None and not options.border:
                    raise invalid(
                        _("La firma visible no tiene nada que mostrar: activa algún dato o añade tu firma manuscrita.")
                    )
                # El rotulo "Firmado digitalmente por" solo tiene sentido si le sigue algun dato.
                lines = appearance.text_lines(label=options.label and has_text, **texts)
                pdf = appearance.build(
                    appearance.Appearance(size[0], size[1], session.doc[index].rotation, lines, image, options.border)
                )
                # Temporal dentro de la carpeta privada del documento, con nombre
                # no predecible, y borrado en ``finally`` pase lo que pase.
                stamp_file = session.work_dir / f"firma-{secrets.token_hex(8)}.pdf"
                stamp_file.write_bytes(pdf)

            context = backend = None
            if p.longTerm:
                # Se exige la revocacion de toda la cadena: una firma "de larga
                # duracion" sin ella daria una falsa seguridad.
                backend = host.revocation_backend()
                context = validation.embedding_context(host, backend)
            plan = pdfdoc.SignPlan(
                when=when, field_name=p.field, existing=p.field is not None, page_index=index, box=box,
                stamp_file=stamp_file, reason=reason, location=location, contact=contact, certify=p.certify,
                lock=p.lock, timestamper=timestamper, validation_context=context, revocation_backend=backend,
            )
            ctx.check_cancel()
            before = session.cur
            base, signed = _on_current(
                session, lambda data: pdfdoc.sign(data, session.password, signer, plan), compact=True
            )
            prepared = session.cur == before + 1  # se reescribio el documento para poder firmarlo

            # --- comprobar lo firmado antes de darlo por bueno
            check = validation.check_new_signature(
                signed.data, session.password, signed.field_name, host, long_term=p.longTerm
            )
            problem: EngineError | None = None
            if not check.sound:
                problem = EngineError(
                    "sign_failed", _("La firma creada no ha superado la comprobación. No se ha cambiado el documento.")
                )
            elif timestamper is not None and not check.stamped:
                problem = EngineError(
                    "timestamp_failed",
                    _("El sello de tiempo recibido no es válido para esta firma. No se ha cambiado el documento."),
                )
            if problem is not None:
                # La reescritura previa solo tenia sentido para firmar.
                if prepared and session.cur == before + 1:
                    session.undo()
                raise problem
            if not check.trusted:
                warnings.append(
                    _(
                        "Este equipo no reconoce como de confianza a la autoridad que emitió el certificado: "
                        "quien reciba el documento puede ver la firma como no fiable."
                    )
                )
            if timestamper is not None and not check.stamp_trusted:
                warnings.append(_("El sello de tiempo procede de una autoridad que este equipo no reconoce como de confianza."))
            if signed.lenient or not check.strict:
                warnings.append(_("El PDF tiene irregularidades de formato; algunos validadores pueden mostrar avisos."))

            changed = [page_id] if page_id is not None and drawable else []
            try:
                _append(session, _("Firmar con certificado"), base, signed.data, dirty=changed, seals=True)
            except BaseException:
                if prepared and session.cur == before + 1:
                    session.undo()
                raise
            return session.result(
                changed,
                signature={
                    "field": signed.field_name,
                    "signer": {"cn": info.subject["cn"], "serialNumber": info.subject["serialNumber"]},
                    "signedAt": when.isoformat(),
                    "timestamped": timestamper is not None,
                    "longTerm": p.longTerm,
                    # Si se pidio bloquear y no se pudo (un campo ya existente, sin
                    # bloqueo preparado, en un documento que ya tenia firmas).
                    "locked": session.locks.level == locks.NO_CHANGES,
                    "warnings": warnings,
                },
            )
        finally:
            if stamp_file is not None:
                stamp_file.unlink(missing_ok=True)
            forget = getattr(signer, "forget", None)
            if forget is not None:
                forget()  # la clave de un .pfx no se conserva tras la operacion

    return _guard("sign.sign", run)


# ------------------------------------------------------------------------ validar


class ValidateParams(DocParams):
    online: bool = False


@method("sign.validate", ValidateParams)
def sign_validate(ctx: Ctx, p: ValidateParams) -> dict[str, Any]:
    """Estado de cada firma del documento. Sin red salvo que se pida ``online``."""
    session = ctx.engine.session(p.doc)

    def run() -> dict[str, Any]:
        from ..sign import validation

        placed = {name: (index, rect) for name, index, rect, _signed in _widgets(session)}
        results = validation.validate_document(
            session.current_bytes(), session.password, ctx.engine.host, online=p.online, tick=ctx.check_cancel
        )
        if results is None:
            # El PDF no se deja leer para validarlo: cada firma que se ve queda como "no se pudo validar".
            results = [validation.unknown_entry(name) for name, _i, _r, signed in _widgets(session) if signed]
        for entry in results:
            where = placed.get(entry["field"])
            if where is not None:
                index, rect = where
                entry["page"] = session.page_ids[index]
                entry["rect"] = Geom(session.doc[index]).rect_d(rect) if _visible(rect) else None
        return {"signatures": results}

    return _guard("sign.validate", run)
