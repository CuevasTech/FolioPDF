"""Validacion de las firmas de un documento.

Todo lo que se lee aqui viene de fuera: el PDF, las firmas y los certificados
que llevan dentro. De ahi las reglas del modulo:

* Cada firma se valida por separado y cualquier fallo al hacerlo se convierte
  en el resultado "no se pudo validar" de esa firma. Una firma mal formada (a
  proposito o no) no impide ver el estado de las demas ni tumba la operacion, y
  hacia fuera no sale ninguna traza (A10:2025).
* Ante la duda, no se da por buena: un archivo que solo se deja leer en modo
  tolerante, una cobertura rara o una raiz que no se puede comprobar nunca
  acaban en "valida".
* La confianza la decide la lista de autoridades de Windows, que se lee y no
  se modifica. El motor no anade autoridades por su cuenta.
* Sin red salvo que se pida (``online``); entonces solo se consulta la
  revocacion, con limites (ver ``net``).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from asn1crypto import x509 as asn1_x509
from pyhanko.sign.ades.report import AdESIndeterminate
from pyhanko.sign.diff_analysis import ModificationLevel
from pyhanko.sign.fields import enumerate_sig_fields
from pyhanko.sign.validation import (
    EmbeddedPdfSignature,
    SignatureCoverageLevel,
    async_validate_pdf_signature,
    async_validate_pdf_timestamp,
)
from pyhanko.sign.validation.settings import KeyUsageConstraints
from pyhanko_certvalidator import ValidationContext

from ..i18n import _, short_date
from . import certs, pdfdoc

log = logging.getLogger("folio.engine.sign.validation")

MAX_SIGNATURES = 100
ONLINE_BUDGET = 25.0  # segundos, por firma, para consultar la revocacion

_EXPIRED = (
    AdESIndeterminate.OUT_OF_BOUNDS_NO_POE, AdESIndeterminate.OUT_OF_BOUNDS_NOT_REVOKED, AdESIndeterminate.EXPIRED,
)
_REVOKED = (AdESIndeterminate.REVOKED_NO_POE, AdESIndeterminate.REVOKED_CA_NO_POE)
_WEAK = (AdESIndeterminate.CRYPTO_CONSTRAINTS_FAILURE, AdESIndeterminate.CRYPTO_CONSTRAINTS_FAILURE_NO_POE)
_SEVERITY = {"valid": 0, "warning": 1, "invalid": 2}


@dataclass(frozen=True, repr=False)
class Trust:
    """Autoridades en las que confia Windows, ya analizadas."""

    roots: tuple[Any, ...]
    others: tuple[Any, ...]

    def __repr__(self) -> str:
        return "<Trust>"


def _parse(items: tuple[bytes, ...]) -> tuple[Any, ...]:
    out = []
    for der in items:
        try:
            # Se analiza entero ya, con el analizador del validador: un
            # certificado estropeado del almacen se descarta aqui y no rompe
            # despues la validacion de todas las firmas. (No se filtra con
            # ``cryptography``: dejara de aceptar numeros de serie no positivos,
            # que tienen varias raices legitimas de Windows, y una actualizacion
            # les retiraria la confianza sin avisar.)
            certificate = asn1_x509.Certificate.load(der)
            _ = (certificate.native, certificate.issuer_serial, certificate.public_key.sha1, certificate.subject.hashable)
            out.append(certificate)
        except Exception:  # noqa: BLE001
            continue
    return tuple(out)


def load_trust(host: Any) -> Trust:
    """Lee las autoridades de confianza de Windows; si no se puede, no se confia en ninguna.

    Las lee ``host`` (host.py): el motor aislado no tiene acceso al almacen de
    Windows y se las pide al intermediario.
    """
    try:
        material = host.trust_material()
    except Exception as exc:  # noqa: BLE001 - sin lista de confianza se sigue, pero sin fiarse de nadie
        log.warning("no se pudieron leer las autoridades de confianza de Windows (%s)", type(exc).__name__)
        return Trust((), ())
    return Trust(_parse(material.roots), _parse(material.intermediates))


def _context(
    trust: Trust, *, moment: datetime | None = None, backend: Any = None, strict_revocation: bool = False
) -> ValidationContext:
    kwargs: dict[str, Any] = {
        # Lista explicita: nunca se recurre a otra lista de autoridades que no sea la de Windows.
        "trust_roots": list(trust.roots),
        "other_certs": list(trust.others),
        "allow_fetching": backend is not None,
        "revocation_mode": "hard-fail" if strict_revocation else "soft-fail",
    }
    if moment is not None:
        kwargs["moment"] = moment
    if backend is not None:
        kwargs["fetcher_backend"] = backend
    return ValidationContext(**kwargs)


def embedding_context(host: Any, backend: Any) -> ValidationContext:
    """Contexto para incluir en una firma la informacion de revocacion de su cadena (larga duracion).

    Con consulta en linea y exigiendo la revocacion de cada certificado que la
    declare: si no se puede obtener, la firma no se hace (ver pdfdoc.sign).
    """
    return _context(load_trust(host), backend=backend, strict_revocation=True)


def _usage(certificate: Any) -> KeyUsageConstraints:
    """El firmante debe estar autorizado a firmar: firma digital o no repudio."""
    try:
        unrestricted = certificate.key_usage_value is None
    except Exception:  # noqa: BLE001
        unrestricted = False
    # Un certificado sin la extension de uso de clave no restringe nada (RFC 5280).
    return KeyUsageConstraints(key_usage=set() if unrestricted else {"digital_signature", "non_repudiation"})


def _iso(moment: datetime | None) -> str | None:
    try:
        return moment.isoformat() if moment is not None else None
    except Exception:  # noqa: BLE001
        return None


def _when(moment: datetime | None) -> str:
    try:
        return short_date(moment.astimezone()) if moment is not None else _("fecha desconocida")
    except Exception:  # noqa: BLE001
        return _("fecha desconocida")


def _people(certificate: Any) -> tuple[dict[str, str], dict[str, str]]:
    empty = ({"cn": "", "serialNumber": "", "organization": ""}, {"cn": ""})
    try:
        info = certs.describe(certificate.dump())
    except Exception:  # noqa: BLE001 - certificado que no se deja leer: sin nombre, pero se sigue
        return empty
    subject = {key: info.subject[key] for key in ("cn", "serialNumber", "organization")}
    return subject, {"cn": info.issuer["cn"] or info.issuer["organization"]}


def unknown_entry(name: str, kind: str = "signature") -> dict[str, Any]:
    """Resultado de una firma que no se ha podido validar."""
    return {
        "field": name,
        "type": kind,
        "page": None,
        "rect": None,
        "signer": {"cn": "", "serialNumber": "", "organization": ""},
        "issuer": {"cn": ""},
        "signedAt": None,
        "timestamp": None,
        "intact": False,
        "trusted": False,
        "coverage": "partial",
        "modifiedAfter": False,
        "modification": "none",
        "revocationChecked": False,
        "longTerm": False,
        "certify": False,
        "summary": "unknown",
        "messages": [_("No se pudo validar esta firma: su formato no es compatible o está dañado.")],
    }


def _modification(status: Any) -> str:
    level = status.modification_level
    if level in (ModificationLevel.NONE, ModificationLevel.LTA_UPDATES):
        return "none"
    if level == ModificationLevel.FORM_FILLING:
        return "formfill"
    if level == ModificationLevel.ANNOTATIONS:
        return "annotations"
    return "other"  # incluye "no se pudo analizar": no se presume que los cambios sean inocuos


def _is_certification(reader: Any, embedded: EmbeddedPdfSignature) -> bool:
    try:
        reference = reader.root["/Perms"].get_value_as_reference("/DocMDP")
        return reference == embedded.sig_object.container_ref
    except Exception:  # noqa: BLE001
        return False


def _good_timestamp(status: Any) -> Any:
    stamp = getattr(status, "timestamp_validity", None)
    if stamp is not None and stamp.valid and stamp.intact and stamp.trusted:
        return stamp
    return None


# Motivos de revocacion que significan que la clave pudo estar en manos ajenas.
_COMPROMISE = ("key_compromise", "ca_compromise", "aa_compromise")


def _key_compromised(status: Any) -> bool:
    """El certificado se revoco por compromiso de clave (o no se puede saber el motivo)."""
    try:
        reason = status.revocation_details.revocation_reason
        return str(getattr(reason, "native", reason)) in _COMPROMISE
    except Exception:  # noqa: BLE001 - sin motivo legible se supone lo peor
        return True


class _Verdict:
    """Va acumulando mensajes y se queda con la gravedad mas alta."""

    def __init__(self) -> None:
        self.summary = "valid"
        self.messages: list[str] = []

    def add(self, text: str, level: str = "valid") -> None:
        self.messages.append(text)
        if _SEVERITY[level] > _SEVERITY[self.summary]:
            self.summary = level


def _trust_message(status: Any, certificate: Any) -> str:
    problem = status.trust_problem_indic
    if problem in _WEAK:
        return _("La firma o su certificado usan un algoritmo que ya no se considera seguro (por ejemplo SHA-1 o una clave corta).")
    if problem in _EXPIRED:
        try:
            expired = _when(certificate.not_valid_after)
        except Exception:  # noqa: BLE001
            expired = _("fecha desconocida")
        return _(
            "El certificado del firmante caducó el {date}. Sin un sello de tiempo de confianza "
            "no se puede comprobar que se firmara mientras era válido.",
            date=expired,
        )
    try:
        self_signed = certificate.self_signed in ("yes", "maybe")
        restricted = certificate.key_usage_value is not None and not (
            {"digital_signature", "non_repudiation"} & set(certificate.key_usage_value.native)
        )
    except Exception:  # noqa: BLE001
        self_signed = restricted = False
    if self_signed:
        return _("El certificado del firmante está autofirmado: ninguna autoridad de confianza avala su identidad.")
    if restricted:
        return _("El certificado del firmante no está autorizado para firmar documentos.")
    if problem == AdESIndeterminate.NO_CERTIFICATE_CHAIN_FOUND:
        return _("El certificado del firmante no procede de ninguna autoridad en la que confíe este equipo.")
    return _("No se pudo comprobar que el certificado del firmante proceda de una autoridad de confianza.")


def _revocation_archive(reader: Any) -> bool:
    """El documento guarda informacion de revocacion para validarlo a largo plazo (almacen DSS)."""
    try:
        from pyhanko.sign.validation.dss import DocumentSecurityStore

        dss = DocumentSecurityStore.read_dss(reader)
        return bool(getattr(dss, "ocsps", None) or getattr(dss, "crls", None))
    except Exception:  # noqa: BLE001 - sin almacen o ilegible: no se presume que lo tenga
        return False


async def _signature(
    embedded: EmbeddedPdfSignature, reader: Any, trust: Trust, backend: Any, lenient: bool, archive: bool = False
) -> dict[str, Any]:
    certificate = embedded.signer_cert
    usage = _usage(certificate)
    stamp_context = _context(trust)  # el sello de tiempo se valida siempre con lo que hay en el equipo
    online = backend is not None
    revocation_checked = False
    revocation_failed = False

    if online:
        # Primer intento exigiendo la revocacion que declaren los certificados.
        primary = _context(trust, backend=backend, strict_revocation=True)
        try:
            status = await asyncio.wait_for(
                async_validate_pdf_signature(
                    embedded, signer_validation_context=primary, ts_validation_context=stamp_context,
                    key_usage_settings=usage,
                ),
                ONLINE_BUDGET,
            )
        except asyncio.TimeoutError:
            status = None
        if status is not None and status.trusted:
            revocation_checked = bool(primary.crls or primary.ocsps)
        elif status is None or not status.revoked:
            # No se pudo consultar (o hay otro problema): se valida sin red para
            # saber al menos como esta la cadena, y se dice que falta la revocacion.
            revocation_failed = True
            status = None
    else:
        status = None
    if status is None:
        status = await async_validate_pdf_signature(
            embedded, signer_validation_context=_context(trust), ts_validation_context=stamp_context,
            key_usage_settings=usage, skip_diff=False,
        )

    verdict = _Verdict()
    intact = bool(status.intact and status.valid)
    coverage = status.coverage
    covered = coverage is not None and coverage >= SignatureCoverageLevel.ENTIRE_REVISION
    whole = coverage == SignatureCoverageLevel.ENTIRE_FILE
    modification = _modification(status) if covered else "other"
    policy = embedded.docmdp_level

    if not intact:
        verdict.add(_("La firma no es válida: el contenido firmado se ha alterado o la firma está dañada."), "invalid")
    elif not covered:
        verdict.add(
            _("La firma no abarca el documento de la forma habitual: no se puede garantizar que lo que se ve sea lo que se firmó."),
            "invalid",
        )
    elif whole:
        verdict.add(_("El documento no se ha modificado desde que se aplicó esta firma."))
    elif policy is not None and status.docmdp_ok is False:
        verdict.add(_("Después de esta firma el documento se modificó de una forma que la propia firma no permitía."), "invalid")
    elif modification == "none":
        verdict.add(_("La firma es íntegra. Después solo se añadió información para poder validarla más adelante."))
    elif modification == "formfill":
        verdict.add(_("La firma es íntegra. Después se añadieron otras firmas o se rellenaron campos, cambios que esta firma admite."))
    elif modification == "annotations":
        verdict.add(_("La firma es íntegra, pero después se añadieron comentarios o anotaciones al documento."), "warning")
    else:
        verdict.add(
            _(
                "La firma es íntegra, pero el documento se modificó después de firmarlo: "
                "lo que se muestra puede no coincidir con lo que se firmó."
            ),
            "warning",
        )

    trusted = bool(status.trusted)
    stamp = _good_timestamp(status)
    revoked_on = status.revocation_details.revocation_date if status.revoked else None
    if intact and not trusted and stamp is not None:
        # Un sello de tiempo de confianza demuestra cuando existia la firma. Si
        # en esa fecha el certificado era valido, que haya caducado (o se haya
        # revocado) despues no la invalida.
        rescuable = status.trust_problem_indic in _EXPIRED or (
            # Revocado por clave comprometida: un sello anterior a la fecha de
            # revocacion no prueba nada, porque la clave pudo usarla un tercero
            # antes de que el titular lo notara. En ese caso no se rescata.
            revoked_on is not None and stamp.timestamp < revoked_on and not _key_compromised(status)
        )
        if rescuable:
            past = await async_validate_pdf_signature(
                embedded, signer_validation_context=_context(trust, moment=stamp.timestamp),
                ts_validation_context=stamp_context, key_usage_settings=usage, skip_diff=True,
            )
            if past.trusted:
                trusted = True
                if revoked_on is not None:
                    revocation_checked = True
                    verdict.add(
                        _(
                            "El certificado se revocó el {date}, después de la fecha del sello de tiempo: "
                            "la firma se hizo cuando todavía era válido.",
                            date=_when(revoked_on),
                        )
                    )
                else:
                    verdict.add(_("El certificado ha caducado, pero el sello de tiempo demuestra que se firmó mientras era válido."))
    if intact:
        if trusted and revoked_on is None and status.trusted:
            verdict.add(_("La identidad del firmante la avala una autoridad en la que confía este equipo."))
        elif not trusted and revoked_on is not None:
            verdict.add(
                _(
                    "El certificado del firmante fue revocado el {date} "
                    "y no hay prueba de que la firma sea anterior.",
                    date=_when(revoked_on),
                ),
                "invalid",
            )
        elif not trusted:
            verdict.add(_trust_message(status, certificate), "warning")
        if trusted and revoked_on is None:
            if revocation_checked:
                verdict.add(_("Se ha comprobado en línea que el certificado no está revocado."))
            elif revocation_failed:
                verdict.add(_("No se pudo comprobar en línea si el certificado está revocado."), "warning")
            else:
                verdict.add(_("No se ha comprobado si el certificado está revocado (requiere conexión)."))

    raw_stamp = getattr(status, "timestamp_validity", None)
    timestamp = None
    if raw_stamp is not None:
        sound = bool(raw_stamp.valid and raw_stamp.intact)
        timestamp = {"time": _iso(raw_stamp.timestamp), "valid": sound, "trusted": bool(raw_stamp.trusted)}
        if not sound:
            verdict.add(_("El sello de tiempo de la firma no es válido."), "warning")
        elif not raw_stamp.trusted:
            verdict.add(_("Lleva un sello de tiempo, pero de una autoridad en la que este equipo no confía."))
        else:
            verdict.add(_("Lleva un sello de tiempo de confianza del {date}.", date=_when(raw_stamp.timestamp)))
    elif intact:
        verdict.add(_("La fecha de la firma es la del equipo de quien firmó; no lleva sello de tiempo."))
    long_term = bool(archive and intact and raw_stamp is not None)
    if long_term:
        verdict.add(
            _(
                "El documento incluye la información de revocación de los certificados, para poder comprobar la firma "
                "a largo plazo (aunque el certificado caduque)."
            )
        )

    certify = _is_certification(reader, embedded)
    if certify:
        verdict.add(_("Es la firma con la que el autor certificó el documento."))
    if not status.seed_value_ok:
        verdict.add(_("La firma no cumple las condiciones que imponía el campo de firma."), "warning")
    if lenient:
        verdict.add(_("El archivo tiene irregularidades de formato: la validación es menos fiable."), "warning")

    subject, issuer = _people(certificate)
    return {
        "field": embedded.field_name,
        "type": "signature",
        "page": None,
        "rect": None,
        "signer": subject,
        "issuer": issuer,
        "signedAt": _iso(status.signer_reported_dt),
        "timestamp": timestamp,
        "intact": intact,
        "trusted": trusted and intact,
        "coverage": "whole" if whole else "partial",
        "modifiedAfter": modification != "none",
        "modification": modification,
        "revocationChecked": revocation_checked,
        "longTerm": long_term,
        "certify": certify,
        "summary": verdict.summary,
        "messages": verdict.messages,
    }


async def _document_timestamp(embedded: EmbeddedPdfSignature, trust: Trust, lenient: bool) -> dict[str, Any]:
    status = await async_validate_pdf_timestamp(embedded, validation_context=_context(trust))
    verdict = _Verdict()
    intact = bool(status.intact and status.valid)
    coverage = status.coverage
    covered = coverage is not None and coverage >= SignatureCoverageLevel.ENTIRE_REVISION
    whole = coverage == SignatureCoverageLevel.ENTIRE_FILE
    modification = _modification(status) if covered else "other"
    if not intact:
        verdict.add(_("El sello de tiempo del documento no es válido: el contenido se ha alterado o el sello está dañado."), "invalid")
    elif not covered:
        verdict.add(_("El sello de tiempo no abarca el documento de la forma habitual."), "invalid")
    elif modification in ("annotations", "other"):
        verdict.add(_("El documento se modificó después de este sello de tiempo."), "warning")
    else:
        verdict.add(_("Sello de tiempo del documento del {date}.", date=_when(status.timestamp)))
    if intact and not status.trusted:
        verdict.add(_("El sello procede de una autoridad en la que este equipo no confía."), "warning")
    if lenient:
        verdict.add(_("El archivo tiene irregularidades de formato: la validación es menos fiable."), "warning")
    subject, issuer = _people(embedded.signer_cert)
    moment = _iso(status.timestamp)
    return {
        "field": embedded.field_name,
        "type": "timestamp",
        "page": None,
        "rect": None,
        "signer": subject,
        "issuer": issuer,
        "signedAt": moment,
        "timestamp": {"time": moment, "valid": intact, "trusted": bool(status.trusted)},
        "intact": intact,
        "trusted": bool(status.trusted) and intact,
        "coverage": "whole" if whole else "partial",
        "modifiedAfter": modification != "none",
        "modification": modification,
        "revocationChecked": False,
        "longTerm": False,
        "certify": False,
        "summary": verdict.summary,
        "messages": verdict.messages,
    }


def _signed_fields(reader: Any) -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    for name, _value, reference in enumerate_sig_fields(reader, filled_status=True):
        out.append((str(name), reference))
        if len(out) >= MAX_SIGNATURES:
            break
    return out


async def _validate_all(
    reader: Any, lenient: bool, trust: Trust, backend: Any, tick: Callable[[], None] | None
) -> list[dict[str, Any]]:
    results: list[tuple[int, dict[str, Any]]] = []
    archive = _revocation_archive(reader)
    try:
        for name, reference in _signed_fields(reader):
            if tick is not None:
                tick()
            order = 1 << 30
            try:
                embedded = EmbeddedPdfSignature(reader, reference, name)
                order = int(embedded.signed_revision)
                if embedded.sig_object_type == "/DocTimeStamp":
                    entry = await _document_timestamp(embedded, trust, lenient)
                else:
                    entry = await _signature(embedded, reader, trust, backend, lenient, archive)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - firma ajena: cualquier fallo es "no se pudo validar"
                # Solo el tipo de excepcion: su texto puede citar al firmante (A09:2025).
                log.warning("no se pudo validar una firma (%s)", type(exc).__name__)
                entry = unknown_entry(name[:500])
            results.append((order, entry))
    finally:
        if backend is not None:
            try:
                await backend.close()
            except Exception:  # noqa: BLE001
                pass
    results.sort(key=lambda item: item[0])  # en el orden en que se firmo
    return [entry for _order, entry in results]


def validate_document(
    data: bytes,
    password: str | None,
    host: Any,
    *,
    online: bool = False,
    tick: Callable[[], None] | None = None,
) -> list[dict[str, Any]] | None:
    """Estado de cada firma del documento, o ``None`` si el PDF no se deja leer."""
    try:
        reader, lenient = pdfdoc.open_reader(data, password)
    except pdfdoc.Unreadable:
        return None
    trust = load_trust(host)
    # Solo se carga la parte de red si de verdad se va a usar.
    backend = host.revocation_backend() if online else None
    return asyncio.run(_validate_all(reader, lenient, trust, backend, tick))


# ------------------------------------------------- comprobacion tras firmar


@dataclass(frozen=True)
class FreshCheck:
    sound: bool  # integra, criptograficamente valida y cubre todo el archivo
    trusted: bool  # su certificado encadena con una autoridad de confianza de Windows
    stamped: bool  # lleva un sello de tiempo valido para esta firma
    stamp_trusted: bool
    strict: bool  # el resultado se deja leer en modo estricto


def check_new_signature(
    data: bytes, password: str | None, field_name: str, host: Any, *, long_term: bool = False
) -> FreshCheck:
    """Valida la firma recien creada antes de darla por buena.

    Es una red de seguridad de extremo a extremo: si por un fallo nuestro, de
    una libreria o del proveedor de la clave el resultado no fuera una firma
    correcta, se descarta en vez de guardarse (A08:2025).

    Una firma normal debe abarcar el archivo entero. En una de larga duracion
    van despues el almacen de revocacion y el sello del documento: se admite
    solo si el analisis de cambios dice que lo anadido es exactamente eso.
    """
    failed = FreshCheck(False, False, False, False, False)
    try:
        reader, lenient = pdfdoc.open_reader(data, password)
        reference = next(
            (ref for name, _value, ref in enumerate_sig_fields(reader, filled_status=True, with_name=field_name)), None
        )
        if reference is None:
            return failed
        embedded = EmbeddedPdfSignature(reader, reference, field_name)
        context = _context(load_trust(host))
        status = asyncio.run(
            async_validate_pdf_signature(
                embedded, signer_validation_context=context, ts_validation_context=context,
                key_usage_settings=_usage(embedded.signer_cert), skip_diff=not long_term,
            )
        )
    except Exception as exc:  # noqa: BLE001
        log.error("la firma recien creada no se pudo comprobar (%s)", type(exc).__name__)
        return failed
    stamp = status.timestamp_validity
    covered = status.coverage == SignatureCoverageLevel.ENTIRE_FILE or (
        long_term
        and status.coverage == SignatureCoverageLevel.ENTIRE_REVISION
        and status.modification_level in (ModificationLevel.NONE, ModificationLevel.LTA_UPDATES)
    )
    return FreshCheck(
        sound=bool(status.intact and status.valid and covered),
        trusted=bool(status.trusted),
        stamped=bool(stamp is not None and stamp.valid and stamp.intact),
        stamp_trusted=bool(stamp is not None and stamp.trusted),
        strict=not lenient,
    )
