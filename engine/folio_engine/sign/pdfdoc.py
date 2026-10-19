"""Cambios en el PDF hechos con pyHanko: campos de firma y firma.

Todo se escribe como actualizacion incremental: los bytes que ya habia quedan
intactos y lo nuevo se anade al final. Es lo que permite contrafirmar un
documento sin invalidar las firmas que ya tiene.
"""

from __future__ import annotations

import asyncio
import io
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from pyhanko.pdf_utils import generic, layout
from pyhanko.pdf_utils.crypt import StandardSecurityHandler
from pyhanko.pdf_utils.crypt.api import AuthStatus
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.pdf_utils.metadata import model as metadata_model
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.sign.fields import (
    FieldMDPAction,
    FieldMDPSpec,
    MDPPerm,
    SigFieldSpec,
    SigSeedSubFilter,
    append_signature_field,
    enumerate_sig_fields,
)
from pyhanko.sign.general import SigningError
from pyhanko.sign.signers import PdfSignatureMetadata, PdfSigner
from pyhanko.sign.timestamps import TimestampRequestError
from pyhanko.sign.validation import read_certification_data
from pyhanko.stamp import StaticStampStyle

from ..errors import EngineError, invalid, locked, not_found, unsupported
from ..i18n import _

log = logging.getLogger("folio.engine.sign.pdfdoc")

# pyHanko anade "pyHanko <version>" al productor de cada PDF que escribe: la
# version exacta de una libreria ayuda a saber que fallos conocidos le afectan
# (A02:2025, no dar informacion de mas). Queda el nombre de la aplicacion, como
# al guardar.
if isinstance(getattr(metadata_model, "VENDOR", None), str):
    metadata_model.VENDOR = "Folio"

_MAX_FIELDS = 20_000
_MAX_DEPTH = 32

# La apariencia que se entrega a pyHanko ya tiene el tamano del campo: se coloca
# tal cual, sin margenes ni escalado.
_AS_IS = layout.SimpleBoxLayoutRule(
    x_align=layout.AxisAlignment.ALIGN_MIN,
    y_align=layout.AxisAlignment.ALIGN_MIN,
    margins=layout.Margins(0, 0, 0, 0),
    inner_content_scaling=layout.InnerScaling.NO_SCALING,
)


class Unreadable(Exception):
    """pyHanko no puede leer el PDF tal como esta (interno; no llega a la interfaz)."""


class _Reader(PdfFileReader):
    """Lector de pyHanko que admite el diccionario de cifrado escrito en el propio trailer.

    La norma lo permite y es como lo escribe MuPDF (y por tanto Folio al
    proteger un documento), pero el modo estricto de pyHanko lo rechaza. Solo se
    relaja ese punto: todo lo demas se sigue leyendo en modo estricto.
    """

    @property
    def encrypt_dict(self) -> Any:
        try:
            entry = self.trailer.raw_get("/Encrypt")
        except KeyError:
            return None
        if isinstance(entry, generic.DictionaryObject):
            return entry
        return super().encrypt_dict


def _writer_for(reader: PdfFileReader) -> IncrementalPdfFileWriter:
    writer = IncrementalPdfFileWriter.from_reader(reader)
    if writer.security_handler is not None:
        entry = reader.trailer.raw_get("/Encrypt")
        if not isinstance(entry, generic.IndirectObject):
            # pyHanko solo sabe ampliar un PDF cifrado si su diccionario de
            # cifrado es un objeto aparte; con el de MuPDF (escrito en el
            # trailer) falla al escribir. Se anade ese mismo diccionario, sin
            # cambiar nada, como objeto de la actualizacion: el cifrado y las
            # contrasenas siguen siendo exactamente los del documento.
            writer._encrypt = writer.add_object(generic.DictionaryObject(entry))
    return writer


def open_reader(data: bytes, password: str | None) -> tuple[PdfFileReader, bool]:
    """Abre el PDF para leerlo o ampliarlo. Devuelve ``(lector, tolerante)``.

    Primero en modo estricto. Solo si asi no se puede se reintenta en modo
    tolerante, y quien llama lo sabe (``tolerante``) para avisar: un archivo
    que hay que leer con manga ancha es justo donde se esconden las
    manipulaciones de firmas.
    """
    lenient = False
    try:
        reader: PdfFileReader = _Reader(io.BytesIO(data), strict=True)
        if reader.xrefs.hybrid_xrefs_present:
            raise ValueError("referencias cruzadas hibridas")
        encrypted = reader.encrypted  # se consulta ya: en modo estricto puede fallar aqui
    except Exception:  # noqa: BLE001 - PDF ajeno: cualquier fallo de lectura
        try:
            reader = _Reader(io.BytesIO(data), strict=False)
            encrypted = reader.encrypted
            lenient = True
        except Exception:  # noqa: BLE001
            raise Unreadable() from None
    if encrypted:
        if not isinstance(reader.security_handler, StandardSecurityHandler):
            raise unsupported(_("Este PDF está cifrado con certificado y no se puede firmar ni validar desde aquí."))
        try:
            result = reader.decrypt(password or "")
        except Exception:  # noqa: BLE001
            raise Unreadable() from None
        if result.status == AuthStatus.FAILED:
            raise unsupported(_("No se pudo abrir el cifrado de este PDF para trabajar con sus firmas."))
    return reader, lenient


def field_names(reader: PdfFileReader) -> set[str]:
    """Nombres completos de todos los campos del formulario (recorrido acotado)."""
    names: set[str] = set()
    try:
        fields = reader.root["/AcroForm"]["/Fields"]
    except Exception:  # noqa: BLE001 - sin formulario
        return names
    budget = [_MAX_FIELDS]

    def walk(items: Any, prefix: str, seen: frozenset[Any], depth: int) -> None:
        if depth > _MAX_DEPTH or not isinstance(items, generic.ArrayObject):
            return
        for ref in items:
            if budget[0] <= 0:
                return
            budget[0] -= 1
            if not isinstance(ref, generic.IndirectObject) or ref.reference in seen:
                continue
            try:
                field = ref.get_object()
            except Exception:  # noqa: BLE001
                continue
            if not isinstance(field, generic.DictionaryObject):
                continue
            # Siempre con ``[]`` y nunca con ``.get()``: en los diccionarios de
            # pyHanko solo ``[]`` descifra las cadenas y resuelve las referencias.
            # Con ``.get()`` el nombre de un campo de un PDF cifrado sale como un
            # objeto opaco y la comprobacion de nombres repetidos queda ciega.
            name = prefix
            try:
                partial = field["/T"]
            except Exception:  # noqa: BLE001 - sin nombre propio (o ilegible): no aporta nombre
                partial = None
            if isinstance(partial, str):
                name = f"{prefix}.{partial}" if prefix else str(partial)
                names.add(name)
            try:
                kids = field["/Kids"]
            except Exception:  # noqa: BLE001 - sin hijos
                kids = None
            if kids is not None:
                walk(kids, name, seen | {ref.reference}, depth + 1)

    walk(fields, "", frozenset(), 0)
    return names


def unique_field_name(taken: set[str]) -> str:
    # Nombre del campo nuevo ("Firma1"...): queda en el PDF, en el idioma de la aplicacion.
    number = 1
    while _("Firma{number}", number=number) in taken:
        number += 1
    return _("Firma{number}", number=number)


def _has_empty_signature_field(reader: PdfFileReader, name: str) -> bool:
    try:
        return next(enumerate_sig_fields(reader, filled_status=False, with_name=name), None) is not None
    except Exception:  # noqa: BLE001 - existe pero no es de firma, o el formulario esta roto
        return False


def has_signatures(reader: PdfFileReader) -> bool:
    try:
        return next(enumerate_sig_fields(reader, filled_status=True), None) is not None
    except Exception:  # noqa: BLE001
        return False


def _ensure_changes_allowed(reader: PdfFileReader) -> None:
    try:
        certification = read_certification_data(reader)
    except Exception:  # noqa: BLE001
        certification = None
    if certification is not None and certification.permission == MDPPerm.NO_CHANGES:
        raise locked(_("El documento está bloqueado por su firma: no admite más firmas ni campos."))
    # Una firma con bloqueo de campo /P 1 (PDF 2.0) tambien cierra el documento.
    try:
        for _name, _value, field_ref in enumerate_sig_fields(reader, filled_status=True):
            lock = field_ref.get_object().get("/Lock")
            if lock is not None and int(lock.get_object().get("/P", 0)) == MDPPerm.NO_CHANGES.value:
                raise locked(_("El documento está bloqueado por su firma: no admite más firmas ni campos."))
    except EngineError:
        raise
    except Exception:  # noqa: BLE001 - campo ilegible: lo comprueba despues la validacion
        pass


def add_field(
    data: bytes,
    password: str | None,
    *,
    name: str | None,
    page_index: int,
    box: tuple[float, float, float, float],
) -> tuple[str, bytes]:
    """Anade un campo de firma vacio. Devuelve ``(nombre, pdf completo)``."""
    reader, _lenient = open_reader(data, password)
    _ensure_changes_allowed(reader)
    taken = field_names(reader)
    if name is None:
        name = unique_field_name(taken)
    elif name in taken:
        raise invalid(_("Ya existe un campo con ese nombre en el documento."))
    try:
        writer = _writer_for(reader)
        append_signature_field(writer, SigFieldSpec(sig_field_name=name, on_page=page_index, box=box))
        output = io.BytesIO()
        writer.write(output)
    except EngineError:
        raise
    except Exception as exc:  # noqa: BLE001
        # Solo el tipo: el texto de estas excepciones puede citar contenido del PDF.
        log.error("no se pudo anadir el campo de firma (%s)", type(exc).__name__)
        raise unsupported(_("La estructura de este PDF no permite añadirle un campo de firma.")) from None
    return name, output.getvalue()


@dataclass(frozen=True)
class SignPlan:
    when: datetime
    field_name: str | None = None  # None: campo nuevo con nombre generado
    existing: bool = False  # firmar un campo que ya existe en el documento
    page_index: int = 0
    box: tuple[float, float, float, float] | None = None  # caja del campo nuevo, en coordenadas del PDF
    stamp_file: Path | None = None  # apariencia visible (PDF de una pagina)
    reason: str = ""
    location: str = ""
    contact: str = ""
    certify: bool = False
    # Bloquear el documento con esta firma: si es la primera, certificacion sin
    # cambios permitidos; si ya habia firmas, bloqueo del propio campo (PDF 2.0).
    lock: bool = False
    timestamper: Any = None
    # Firma de larga duracion (PAdES B-LTA): contexto con el que se comprueba
    # el certificado y se obtiene su informacion de revocacion para incluirla,
    # y el cliente de red que usa (se cierra al terminar).
    validation_context: Any = None
    revocation_backend: Any = None


@dataclass(frozen=True)
class Signed:
    data: bytes
    field_name: str
    lenient: bool  # el PDF solo se pudo leer en modo tolerante


class _PinnedTimeSigner(PdfSigner):
    """``PdfSigner`` que usa la hora de firma indicada.

    Es la misma que se escribe en la apariencia visible: lo que el lector ve en
    la pagina coincide con la fecha que lleva la firma.
    """

    def __init__(self, *args: Any, signing_time: datetime, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._signing_time = signing_time

    def init_signing_session(self, pdf_out: Any, existing_fields_only: bool = False) -> Any:
        session = super().init_signing_session(pdf_out, existing_fields_only=existing_fields_only)
        session.system_time = self._signing_time
        return session


def sign(data: bytes, password: str | None, signer: Any, plan: SignPlan) -> Signed:
    """Firma el documento (PAdES) y devuelve el PDF completo resultante."""
    reader, lenient = open_reader(data, password)
    _ensure_changes_allowed(reader)
    signed_before = has_signatures(reader)
    if plan.certify and signed_before:
        raise invalid(_("Solo se puede certificar un documento que todavía no tiene firmas."))
    # Solo la primera firma puede certificar; las siguientes bloquean con su campo.
    certify = plan.certify or (plan.lock and not signed_before)

    name = plan.field_name
    if plan.existing:
        if not name or not _has_empty_signature_field(reader, name):
            raise not_found(_("No hay ningún campo de firma vacío con ese nombre."))
        # Un campo que ya existe solo bloquea si lo traia preparado (/Lock): anadirselo
        # ahora seria cambiar un campo de un documento ya firmado.
        field_spec = None
    else:
        taken = field_names(reader)
        if name is None:
            name = unique_field_name(taken)
        elif name in taken:
            raise invalid(_("Ya existe un campo con ese nombre en el documento."))
        lock_spec: dict[str, Any] = {}
        if plan.lock and signed_before:
            lock_spec = {"field_mdp_spec": FieldMDPSpec(FieldMDPAction.ALL), "doc_mdp_update_value": MDPPerm.NO_CHANGES}
        field_spec = SigFieldSpec(sig_field_name=name, on_page=plan.page_index, box=plan.box, **lock_spec)

    style = None
    if plan.stamp_file is not None:
        style = StaticStampStyle.from_pdf_file(
            str(plan.stamp_file), border_width=0, background_layout=_AS_IS, background_opacity=1.0
        )
    long_term = plan.validation_context is not None
    meta = PdfSignatureMetadata(
        field_name=name,
        md_algorithm=signer.digest_name,  # SHA-256 o superior, fijado por el firmante
        subfilter=SigSeedSubFilter.PADES,
        reason=plan.reason or None,
        location=plan.location or None,
        contact_info=plan.contact or None,
        certify=certify,
        # Bloqueo: ningun cambio. Si no, una certificacion deja rellenar
        # formularios y anadir mas firmas, nada mas.
        docmdp_permissions=MDPPerm.NO_CHANGES if plan.lock else MDPPerm.FILL_FORMS,
        # Larga duracion: se incluye la informacion de revocacion de toda la
        # cadena (almacen de seguridad del documento) y despues un sello de
        # tiempo del documento entero que la protege (PAdES B-LTA).
        embed_validation_info=long_term,
        use_pades_lta=long_term,
        validation_context=plan.validation_context,
    )

    async def run(pdf_signer: PdfSigner, writer: Any) -> Any:
        try:
            return await pdf_signer.async_sign_pdf(writer, existing_fields_only=plan.existing)
        finally:
            if plan.revocation_backend is not None:
                try:
                    await plan.revocation_backend.close()
                except Exception:  # noqa: BLE001
                    pass

    try:
        writer = _writer_for(reader)
        pdf_signer = _PinnedTimeSigner(
            meta, signer, timestamper=plan.timestamper, stamp_style=style, new_field_spec=field_spec,
            signing_time=plan.when,
        )
        output = asyncio.run(run(pdf_signer, writer))
        return Signed(output.getvalue(), name, lenient)
    except EngineError:
        raise
    except TimestampRequestError:
        raise EngineError(
            "timestamp_failed",
            _("No se pudo obtener el sello de tiempo del servidor configurado. El documento no se ha firmado."),
        ) from None
    except SigningError as exc:
        log.error("pyHanko rechazo la firma (%s)", type(exc).__name__)
        if long_term:
            raise _long_term_failed(exc) from None
        raise EngineError(
            "sign_failed", _("No se pudo firmar: el documento o el campo de firma no admiten esta firma.")
        ) from None
    except Exception as exc:  # noqa: BLE001
        # Solo el tipo: el texto puede citar datos del certificado o del PDF (A09:2025).
        log.error("fallo al firmar (%s)", type(exc).__name__)
        if long_term and type(exc).__module__.startswith(("pyhanko_certvalidator", "pyhanko.sign")):
            raise _long_term_failed(exc) from None
        raise unsupported(_("La estructura de este PDF no permite añadirle una firma.")) from None


def _long_term_failed(exc: BaseException) -> EngineError:
    """Por que no se pudo hacer la firma de larga duracion, segun el tipo de error (nunca su texto)."""
    kind = type(exc).__name__
    if kind in ("PathBuildingError", "InvalidCertificateError"):
        message = _(
            "La firma de larga duración necesita un certificado emitido por una autoridad que este equipo reconozca "
            "(como la FNMT): con este certificado no se puede comprobar si sigue vigente. Firma con sello de tiempo, "
            "sin larga duración. No se ha firmado nada."
        )
    elif kind == "RevokedError":
        message = _("Tu certificado (o el del servicio de sello de tiempo) está revocado: no se ha firmado nada.")
    elif kind in ("ExpiredError", "NotYetValidError"):
        message = _(
            "Tu certificado (o el del servicio de sello de tiempo) no está en vigor en este momento: no se ha firmado nada."
        )
    else:
        message = _(
            "No se pudo comprobar en línea que tu certificado (o el del servicio de sello de tiempo) no está revocado, "
            "y una firma de larga duración necesita incluir esa comprobación. Revisa la conexión o firma solo con "
            "sello de tiempo. No se ha firmado nada."
        )
    return EngineError("long_term_failed", message)
