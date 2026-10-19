"""Certificados y claves de firma, sin nada de PDF.

Lo usan el motor y el proceso intermediario (``broker.py``). El intermediario
es quien tiene la clave del usuario cuando el motor esta aislado, y no debe
cargar ningun codigo que analice documentos: por eso esto va aparte de
``signers.py`` (que depende de pyHanko).
"""

from __future__ import annotations

import logging
import re
import unicodedata
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from asn1crypto import pkcs12 as asn1_pkcs12
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa, utils
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID, PublicKeyAlgorithmOID

from ..errors import EngineError, io_error, unsupported
from ..i18n import N_, _, short_date
from ..paths import input_file
from . import winstore

log = logging.getLogger("folio.engine.sign.certs")

# Por debajo de 2048 bits una clave RSA ya no ofrece un margen aceptable
# (NIST SP 800-131A la retira desde 2014): no se firma con ella (A04:2025).
MIN_RSA_BITS = 2048
MAX_PFX_BYTES = 2 * 1024 * 1024
MAX_CHAIN = 12

# Curva -> (etiqueta, resumen). El resumen crece con la curva para no dejarla
# por debajo de su nivel de seguridad (FIPS 186-5); nunca menos que SHA-256.
_CURVES: dict[str, tuple[str, str]] = {
    "secp256r1": ("P-256", "sha256"),
    "secp384r1": ("P-384", "sha384"),
    "secp521r1": ("P-521", "sha512"),
}
HASHES = {"sha256": hashes.SHA256, "sha384": hashes.SHA384, "sha512": hashes.SHA512}
DIGEST_SIZES = {"sha256": 32, "sha384": 48, "sha512": 64}
_KEY_USAGES = (
    ("digital_signature", "digitalSignature"),
    ("content_commitment", "nonRepudiation"),
    ("key_encipherment", "keyEncipherment"),
    ("data_encipherment", "dataEncipherment"),
    ("key_agreement", "keyAgreement"),
    ("key_cert_sign", "keyCertSign"),
    ("crl_sign", "cRLSign"),
)

# Identificadores de persona segun ETSI EN 319 412-1 ("IDCES-12345678Z").
_SEMANTIC_ID = re.compile(r"^(?:IDC|PAS|PNO|TIN|VAT|NTR)[A-Z]{2}-(.+)$")
_SPANISH_ID = re.compile(r"^(?:[0-9]{8}[A-Z]|[KLMXYZ][0-9]{7}[A-Z]|[ABCDEFGHJNPQRSUVW][0-9]{7}[0-9A-J])$")
# Nombre comun del DNI electronico: "APELLIDOS, NOMBRE (FIRMA)".
_DNIE_CN = re.compile(r"^(?P<surname>[^,()]+),\s*(?P<given>[^,()]+?)\s*\((?:FIRMA|AUTENTICACI[OÓ]N)\)$")

# Categorias Unicode que no se dibujan: controles, caracteres de formato (entre
# ellos los que invierten el sentido del texto), sustitutos sueltos, uso
# privado y no asignados.
_HIDDEN = {"Cc", "Cf", "Cs", "Co", "Cn"}
_LINE_BREAKS = {"Zl", "Zp"}

# Se traduce al usarlo: ``_(UNTRUSTED_CHAIN_WARNING)``.
UNTRUSTED_CHAIN_WARNING = N_(
    "Windows no encuentra una autoridad de confianza para este certificado: "
    "quien reciba el documento puede ver la firma como no fiable."
)


def clean_text(value: object, limit: int = 256) -> str:
    """Texto de una sola linea, sin caracteres invisibles, recortado a ``limit``."""
    out: list[str] = []
    for ch in str(value):
        category = unicodedata.category(ch)
        if category in _HIDDEN:
            if ch in "\t\n\r\v\f":
                out.append(" ")
            continue
        out.append(" " if category in _LINE_BREAKS else ch)
    return " ".join("".join(out).split())[:limit]


@dataclass(frozen=True, repr=False)
class CertificateInfo:
    """Datos publicos de un certificado, ya limpios para mostrarlos."""

    der: bytes
    thumbprint: str
    subject: dict[str, str]
    issuer: dict[str, str]
    not_before: datetime
    not_after: datetime
    key_kind: str  # "rsa", "ec" u "other"
    key_bits: int
    algorithm: str
    digest_name: str | None  # None si con esta clave no se puede firmar aqui
    key_usage: tuple[str, ...]
    signing_usage: bool
    public_key: Any

    def __repr__(self) -> str:
        # Sin datos: si este objeto acabara en un log o en el texto de una
        # excepcion no debe arrastrar nombres ni identificadores (A09:2025).
        return "<CertificateInfo>"

    @property
    def key_supported(self) -> bool:
        return self.digest_name is not None

    @property
    def signature_size(self) -> int:
        """Tamano maximo de la firma, para reservar sitio sin tocar la clave."""
        size = (self.key_bits + 7) // 8
        return size if self.key_kind == "rsa" else 2 * (size + 3) + 3

    @property
    def display_name(self) -> str:
        """Nombre de la persona tal como se escribe en la firma visible."""
        cn = self.subject["cn"]
        dnie = _DNIE_CN.match(cn)
        if dnie:
            return f"{dnie.group('given')} {dnie.group('surname')}".strip()
        if self.subject["givenName"] and self.subject["surname"]:
            return f"{self.subject['givenName']} {self.subject['surname']}"
        serial = _SEMANTIC_ID.sub(r"\1", self.subject["serialNumber"])
        if serial and cn.endswith(" - " + serial):
            cn = cn[: -len(serial) - 3].rstrip()
        return cn or self.subject["organization"]

    @property
    def display_id(self) -> str:
        """Identificador de la persona para la firma visible ("NIF: ..."), o vacio."""
        value = _SEMANTIC_ID.sub(r"\1", self.subject["serialNumber"])
        if not value:
            return ""
        return ("NIF: " if _SPANISH_ID.match(value.upper()) else "ID: ") + value

    def issues(self, now: datetime | None = None) -> list[str]:
        """Motivos por los que este certificado no es apto para firmar (vacio si lo es)."""
        now = now or datetime.now(timezone.utc)
        out: list[str] = []
        if now < self.not_before:
            out.append("notYetValid")
        elif now > self.not_after:
            out.append("expired")
        if self.key_kind == "rsa" and self.key_bits < MIN_RSA_BITS:
            out.append("weakKey")
        elif not self.key_supported:
            out.append("unsupportedKey")
        if not self.signing_usage:
            out.append("noSigningUsage")
        return out

    def payload(self, *, hardware: bool | None, now: datetime | None = None) -> dict[str, Any]:
        issues = self.issues(now)
        return {
            "thumbprint": self.thumbprint,
            "subject": dict(self.subject),
            "issuer": dict(self.issuer),
            "notBefore": self.not_before.isoformat(),
            "notAfter": self.not_after.isoformat(),
            "valid": "expired" not in issues and "notYetValid" not in issues,
            "canSign": not issues,
            "keyUsage": list(self.key_usage),
            "algorithm": self.algorithm,
            "hardware": hardware,
            "issues": issues,
            "displayName": clean_text(self.display_name, 120),
            "displayId": self.display_id,
        }


def _attribute(name: x509.Name, oid: Any) -> str:
    try:
        for attribute in name.get_attributes_for_oid(oid):
            if isinstance(attribute.value, str):
                return clean_text(attribute.value)
    except Exception:  # noqa: BLE001 - un atributo raro se trata como ausente
        pass
    return ""


def _email(cert: x509.Certificate) -> str:
    found = _attribute(cert.subject, NameOID.EMAIL_ADDRESS)
    if found:
        return found
    try:
        names = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        for value in names.get_values_for_type(x509.RFC822Name):
            return clean_text(value)
    except Exception:  # noqa: BLE001 - sin nombre alternativo o extension ilegible
        pass
    return ""


def _key_usage(cert: x509.Certificate) -> tuple[tuple[str, ...], bool]:
    """Usos declarados y si entre ellos esta firmar (firma digital o no repudio)."""
    try:
        usage = cert.extensions.get_extension_for_class(x509.KeyUsage).value
    except x509.ExtensionNotFound:
        # Sin la extension el certificado no restringe para que sirve la clave
        # (RFC 5280, 4.2.1.3).
        return (), True
    except Exception:  # noqa: BLE001 - extensiones ilegibles: no se presume que pueda firmar
        return (), False
    names = tuple(label for attribute, label in _KEY_USAGES if getattr(usage, attribute, False))
    return names, bool(usage.digital_signature or usage.content_commitment)


def _key(cert: x509.Certificate) -> tuple[str, int, str, str | None, Any]:
    try:
        public = cert.public_key()
    except Exception:  # noqa: BLE001 - tipo de clave que la libreria no conoce
        return "other", 0, _("Clave no compatible"), None, None
    if isinstance(public, rsa.RSAPublicKey):
        bits = public.key_size
        # Una clave declarada "solo RSA-PSS" no puede usarse con PKCS#1 v1.5.
        plain = cert.public_key_algorithm_oid == PublicKeyAlgorithmOID.RSAES_PKCS1_v1_5
        if not plain:
            return "other", bits, f"RSA-PSS {bits}", None, public
        return "rsa", bits, f"RSA {bits}", ("sha256" if bits >= MIN_RSA_BITS else None), public
    if isinstance(public, ec.EllipticCurvePublicKey):
        label, digest = _CURVES.get(public.curve.name, (public.curve.name, None))
        return "ec", public.curve.key_size, f"ECDSA {label}", digest, public
    return "other", 0, type(public).__name__.replace("PublicKey", "").replace("_", "") or _("Clave no compatible"), None, public


def describe(der: bytes) -> CertificateInfo:
    """Lee un certificado X.509 (dato no fiable) con un analizador estricto."""
    try:
        cert = x509.load_der_x509_certificate(der)
        not_before, not_after = cert.not_valid_before_utc, cert.not_valid_after_utc
    except Exception:  # noqa: BLE001
        raise EngineError("invalid_file", _("El certificado no se puede leer.")) from None
    kind, bits, algorithm, digest, public = _key(cert)
    usages, signing = _key_usage(cert)
    with warnings.catch_warnings():
        # La biblioteca avisa de nombres mas largos que el limite del estandar
        # (los hay en certificados reales); el texto ya se limpia y se recorta.
        warnings.simplefilter("ignore", UserWarning)
        subject = {
            "cn": _attribute(cert.subject, NameOID.COMMON_NAME),
            "givenName": _attribute(cert.subject, NameOID.GIVEN_NAME),
            "surname": _attribute(cert.subject, NameOID.SURNAME),
            "serialNumber": _attribute(cert.subject, NameOID.SERIAL_NUMBER),
            "organization": _attribute(cert.subject, NameOID.ORGANIZATION_NAME),
            "email": _email(cert),
        }
        issuer = {
            "cn": _attribute(cert.issuer, NameOID.COMMON_NAME),
            "organization": _attribute(cert.issuer, NameOID.ORGANIZATION_NAME),
        }
    return CertificateInfo(
        der, winstore.thumbprint_of(der), subject, issuer, not_before, not_after,
        kind, bits, algorithm, digest, usages, signing, public,
    )


def _day(moment: datetime) -> str:
    return short_date(moment.astimezone())


def ensure_signable(info: CertificateInfo, now: datetime | None = None) -> list[str]:
    """Rechaza los certificados con los que no se debe firmar; devuelve los avisos del resto."""
    issues = info.issues(now)
    if "notYetValid" in issues:
        raise EngineError(
            "cert_not_yet_valid",
            _("Este certificado todavía no es válido: lo será a partir del {date}.", date=_day(info.not_before)),
        )
    if "expired" in issues:
        raise EngineError(
            "cert_expired", _("Este certificado caducó el {date} y ya no sirve para firmar.", date=_day(info.not_after))
        )
    if "weakKey" in issues:
        raise EngineError(
            "weak_key",
            _(
                "La clave RSA de este certificado tiene {bits} bits. "
                "Por seguridad no se firma con claves de menos de {minimum} bits.",
                bits=info.key_bits,
                minimum=MIN_RSA_BITS,
            ),
        )
    if "unsupportedKey" in issues:
        raise unsupported(_("El tipo de clave de este certificado no es compatible con la firma de documentos."))
    warnings: list[str] = []
    if "noSigningUsage" in issues:
        warnings.append(
            _(
                "El certificado no declara el uso de firma digital ni de no repudio: "
                "otros programas pueden no dar por buena esta firma."
            )
        )
    return warnings


def sign_with_key(key: Any, info: CertificateInfo, digest: bytes) -> bytes:
    """Firma un resumen ya calculado con una clave en memoria (PKCS#1 v1.5 o ECDSA en DER)."""
    if info.digest_name is None:
        raise unsupported(_("El tipo de clave de este certificado no es compatible con la firma de documentos."))
    prehashed = utils.Prehashed(HASHES[info.digest_name]())
    if info.key_kind == "rsa":
        return key.sign(digest, padding.PKCS1v15(), prehashed)
    return key.sign(digest, ec.ECDSA(prehashed))


def ec_raw_to_der(raw: bytes) -> bytes:
    """Windows devuelve la firma ECDSA como r y s seguidos; el PDF la espera en DER."""
    half = len(raw) // 2
    if half == 0 or len(raw) != 2 * half:
        raise EngineError("sign_failed", _("Windows devolvió una firma con un formato inesperado."))
    return utils.encode_dss_signature(int.from_bytes(raw[:half], "big"), int.from_bytes(raw[half:], "big"))


# ------------------------------------------------------------------------- PKCS#12


@dataclass(frozen=True, repr=False)
class PfxContents:
    info: CertificateInfo
    key: Any
    chain: tuple[bytes, ...]

    def __repr__(self) -> str:
        return "<PfxContents>"


def load_pfx(raw_path: str, password: str) -> PfxContents:
    """Abre un archivo .pfx/.p12.

    La contrasena solo se usa para descifrar: no se guarda ni aparece en logs,
    mensajes o excepciones (las de la libreria se descartan con ``from None`` o
    sin encadenar). Python no permite borrar de la memoria una cadena
    inmutable; la copia en bytes de aqui si se sobrescribe al terminar.
    """
    path = input_file(raw_path, max_bytes=MAX_PFX_BYTES)
    try:
        with open(path, "rb") as handle:
            data = handle.read(MAX_PFX_BYTES + 1)
    except OSError:
        raise io_error(_("No se pudo leer el archivo del certificado.")) from None
    if len(data) > MAX_PFX_BYTES:  # pudo crecer entre la comprobacion y la lectura
        raise io_error(_("El archivo es demasiado grande."))

    # Primero se mira si tiene forma de PKCS#12. Asi "contrasena incorrecta" solo
    # se dice cuando de verdad lo es, y no ante cualquier archivo.
    try:
        outer = asn1_pkcs12.Pfx.load(data)
        shape_ok = outer["version"].native == "v3" and outer["auth_safe"]["content_type"].native in (
            "data", "signed_data",
        )
    except Exception:  # noqa: BLE001
        shape_ok = False
    if not shape_ok:
        raise EngineError("invalid_file", _("El archivo no es un certificado .pfx o .p12 válido."))

    secret = bytearray(password.encode("utf-8"))
    loaded = None
    try:
        # Un archivo "sin contrasena" puede estar sin cifrar o cifrado con la
        # contrasena vacia: se prueban las dos formas.
        for attempt in ([secret] if secret else [None, secret]):
            try:
                loaded = pkcs12.load_pkcs12(data, attempt)
                break
            except Exception:  # noqa: BLE001 - el motivo no se conserva: podria aludir a la contrasena
                continue
    finally:
        for index in range(len(secret)):
            secret[index] = 0
    if loaded is None:
        raise EngineError("wrong_password", _("La contraseña del archivo de certificado no es correcta."))
    if loaded.key is None or loaded.cert is None:
        raise EngineError("invalid_file", _("El archivo no contiene un certificado con su clave privada."))

    try:
        der = loaded.cert.certificate.public_bytes(serialization.Encoding.DER)
        chain = tuple(
            item.certificate.public_bytes(serialization.Encoding.DER) for item in loaded.additional_certs[:MAX_CHAIN]
        )
        spki = serialization.PublicFormat.SubjectPublicKeyInfo
        matches = loaded.key.public_key().public_bytes(serialization.Encoding.DER, spki) == loaded.cert.certificate.public_key().public_bytes(serialization.Encoding.DER, spki)
    except Exception:  # noqa: BLE001
        raise EngineError("invalid_file", _("El certificado del archivo no se puede leer.")) from None
    if not matches:
        raise EngineError("invalid_file", _("La clave del archivo no corresponde con su certificado."))
    return PfxContents(describe(der), loaded.key, chain)


def store_certificates(limit: int) -> list[dict[str, Any]]:
    """Certificados personales del usuario con clave privada, listos para mostrar.

    Primero los vigentes y aptos para firmar; a igual nombre, el que caduca mas
    tarde. Un certificado ilegible se omite: no impide ver los demas.
    """
    now = datetime.now(timezone.utc)
    found = []
    for item in winstore.list_certificates():
        try:
            info = describe(item.der)
        except EngineError:
            continue
        found.append((info, item.hardware))
    payloads = [info.payload(hardware=hardware, now=now) for info, hardware in found]
    order = sorted(
        range(len(found)),
        key=lambda i: (
            not payloads[i]["valid"], not payloads[i]["canSign"], payloads[i]["displayName"].casefold(),
            -found[i][0].not_after.timestamp(),
        ),
    )
    return [payloads[i] for i in order[:limit]]


def pfx_key(raw_path: str, password: str) -> tuple[PfxContents, list[str]]:
    """Clave de un .pfx/.p12 apta para firmar, con los avisos sobre su certificado."""
    contents = load_pfx(raw_path, password)
    warnings = ensure_signable(contents.info)
    if not isinstance(contents.key, (rsa.RSAPrivateKey, ec.EllipticCurvePrivateKey)):
        raise unsupported(_("El tipo de clave de este certificado no es compatible con la firma de documentos."))
    return contents, warnings
