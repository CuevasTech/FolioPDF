"""Firmantes: quien hace la operacion criptografica de una firma PAdES.

Hay tres origenes de clave y todos pasan por el mismo codigo de comprobacion:

* ``StoreSigner``: la clave esta en el almacen de Windows y firma el sistema
  (ver ``winstore``); este proceso no llega a ver la clave.
* ``MemorySigner``: la clave viene de un archivo .pfx/.p12 y vive en memoria
  solo mientras dura la operacion.
* ``BrokeredSigner``: el motor esta aislado y la clave (de cualquiera de los
  dos origenes) la tiene el proceso intermediario, que firma el resumen que
  se le pide durante la operacion de firma en curso y nada mas.

En todos los casos la firma recien hecha se comprueba con la clave publica del
certificado antes de darla por buena: si no corresponde, no se firma nada
(A08:2025, no dar por integro lo que no se ha verificado).

Los datos de certificados y la carga de .pfx estan en ``certs.py`` (sin
dependencias de PDF); aqui se reexportan para quien los usaba desde este modulo.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Iterable

from asn1crypto import algos
from asn1crypto import x509 as asn1_x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ec, padding, utils
from pyhanko.sign.general import SigningError
from pyhanko.sign.signers.pdf_cms import Signer
from pyhanko_certvalidator.registry import SimpleCertificateStore

from ..errors import EngineError, unsupported
from ..i18n import _
from .certs import (  # noqa: F401 - reexportados
    HASHES,
    MAX_CHAIN,
    MAX_PFX_BYTES,
    MIN_RSA_BITS,
    UNTRUSTED_CHAIN_WARNING,
    CertificateInfo,
    PfxContents,
    describe,
    ec_raw_to_der,
    ensure_signable,
    load_pfx,
    pfx_key,
    sign_with_key,
)

log = logging.getLogger("folio.engine.sign.signers")


class _CertificateSigner(Signer):
    """Firma RSA PKCS#1 v1.5 o ECDSA con SHA-2 y verifica el resultado.

    PKCS#1 v1.5 (y no PSS) porque es lo que aceptan todos los validadores de
    PDF y todas las tarjetas; como esquema de firma sigue siendo seguro. El
    mecanismo queda fijado al crear el firmante: nada de lo que venga despues
    (el PDF, un campo con "valores semilla") puede rebajarlo a SHA-1.
    """

    def __init__(self, info: CertificateInfo, chain: Iterable[bytes] = ()) -> None:
        if info.digest_name is None:
            raise unsupported(_("El tipo de clave de este certificado no es compatible con la firma de documentos."))
        self.info = info
        self.digest_name: str = info.digest_name
        registry = SimpleCertificateStore()
        for der in list(chain)[:MAX_CHAIN]:
            try:
                registry.register(asn1_x509.Certificate.load(der))
            except Exception:  # noqa: BLE001 - un emisor ilegible no impide firmar; solo no se incrusta
                continue
        mechanism = f"{self.digest_name}_{'rsa' if info.key_kind == 'rsa' else 'ecdsa'}"
        super().__init__(
            prefer_pss=False,
            embed_roots=True,
            signature_mechanism=algos.SignedDigestAlgorithm({"algorithm": mechanism}),
            signing_cert=asn1_x509.Certificate.load(info.der),
            cert_registry=registry,
        )

    @property
    def subject_name(self) -> str:
        return self.info.display_name

    def _sign_digest(self, digest: bytes) -> bytes:
        raise NotImplementedError

    def _verify(self, signature: bytes, digest: bytes) -> None:
        prehashed = utils.Prehashed(HASHES[self.digest_name]())
        try:
            if self.info.key_kind == "rsa":
                self.info.public_key.verify(signature, digest, padding.PKCS1v15(), prehashed)
            else:
                self.info.public_key.verify(signature, digest, ec.ECDSA(prehashed))
        except InvalidSignature:
            log.error("la firma obtenida no corresponde con el certificado elegido")
            raise EngineError(
                "sign_failed", _("La firma obtenida no corresponde con el certificado elegido. No se ha firmado nada.")
            ) from None

    async def async_sign_raw(self, data: bytes, digest_algorithm: str, dry_run: bool = False) -> bytes:
        if digest_algorithm.lower() != self.digest_name:
            raise SigningError(_("Algoritmo de resumen inesperado."))
        if dry_run:
            # pyHanko pide antes una firma "de prueba" solo para reservar sitio
            # en el PDF. No se toca la clave: asi el usuario ve un unico dialogo
            # de PIN, el de la firma de verdad.
            return bytes(self.info.signature_size)
        digest = hashlib.new(self.digest_name, data).digest()
        signature = self._sign_digest(digest)
        self._verify(signature, digest)
        return signature


class StoreSigner(_CertificateSigner):
    """Firma con una clave del almacen de Windows, sin sacarla de el."""

    def __init__(self, certificate: Any, info: CertificateInfo, hwnd: int | None = None) -> None:
        super().__init__(info, certificate.chain)
        self._thumbprint = certificate.thumbprint
        self._hwnd = hwnd

    def _sign_digest(self, digest: bytes) -> bytes:
        from . import winstore

        raw = winstore.sign_digest(
            self._thumbprint, digest, hash_name=self.digest_name, key_kind=self.info.key_kind, hwnd=self._hwnd
        )
        return raw if self.info.key_kind != "ec" else ec_raw_to_der(raw)


class MemorySigner(_CertificateSigner):
    """Firma con una clave cargada de un archivo .pfx/.p12."""

    def __init__(self, key: Any, info: CertificateInfo, chain: Iterable[bytes] = ()) -> None:
        super().__init__(info, chain)
        self._key = key

    def _sign_digest(self, digest: bytes) -> bytes:
        if self._key is None:
            raise EngineError("internal", _("La clave del certificado ya no está disponible."))
        return sign_with_key(self._key, self.info, digest)

    def forget(self) -> None:
        """Suelta la clave en cuanto deja de hacer falta."""
        self._key = None


class BrokeredSigner(_CertificateSigner):
    """Firma pidiendo el resumen firmado al proceso intermediario (motor aislado)."""

    def __init__(self, host: Any, info: CertificateInfo, chain: Iterable[bytes] = ()) -> None:
        super().__init__(info, chain)
        self._host = host

    def _sign_digest(self, digest: bytes) -> bytes:
        return self._host.sign_digest(digest, self.digest_name)


def store_signer(thumbprint: str, hwnd: int | None) -> tuple[StoreSigner, list[str]]:
    """Firmante para un certificado del almacen de Windows y avisos sobre el."""
    from . import winstore

    certificate = winstore.load_certificate(thumbprint)
    info = describe(certificate.der)
    warnings = ensure_signable(info)
    if certificate.chain_errors is not None and certificate.chain_errors & winstore.CHAIN_NOT_TRUSTED_MASK:
        warnings.append(_(UNTRUSTED_CHAIN_WARNING))
    return StoreSigner(certificate, info, hwnd), warnings


def pfx_signer(raw_path: str, password: str) -> tuple[MemorySigner, list[str]]:
    """Firmante para un archivo .pfx/.p12 y avisos sobre su certificado."""
    contents, warnings = pfx_key(raw_path, password)
    return MemorySigner(contents.key, contents.info, contents.chain), warnings


def brokered_signer(host: Any, certificate: bytes, chain: Iterable[bytes], untrusted_chain: bool) -> tuple[BrokeredSigner, list[str]]:
    """Firmante cuya clave tiene el intermediario; el certificado se vuelve a comprobar aqui."""
    info = describe(certificate)
    warnings = ensure_signable(info)
    if untrusted_chain:
        warnings.append(_(UNTRUSTED_CHAIN_WARNING))
    return BrokeredSigner(host, info, chain), warnings
