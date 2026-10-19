"""Almacen de certificados de Windows a traves de la API del sistema (ctypes).

Es el unico punto del motor que toca claves privadas del usuario. Decisiones
de seguridad y su porque:

* **La clave privada no sale del almacen.** No se exporta ni se copia: se pide
  a Windows un manejador de la clave (``CryptAcquireCertificatePrivateKey``) y
  es el sistema quien firma el resumen (``NCryptSignHash``). El material de la
  clave nunca entra en la memoria de este proceso y sigue amparado por lo que
  el usuario tenga configurado: proteccion fuerte, PIN de tarjeta, TPM...
  (A04:2025).
* **No se silencia a Windows.** No se pasa ``NCRYPT_SILENT_FLAG`` ni
  ``CRYPT_ACQUIRE_SILENT_FLAG``: si la clave exige consentimiento o PIN, el
  dialogo lo muestra el sistema y el motor ni ve ni pide ese secreto.
* **Solo lectura.** Todos los almacenes se abren con
  ``CERT_STORE_READONLY_FLAG`` y el modulo no declara ninguna funcion capaz de
  anadir, quitar o modificar certificados ni de cambiar la confianza del
  sistema.
* **Bibliotecas solo de System32.** Evita que una DLL plantada en otra carpeta
  suplante a la del sistema (A08:2025).
* **Tipos declarados.** Cada funcion lleva ``argtypes``/``restype``: sin ellos
  ctypes trata punteros y manejadores como enteros de 32 bits y en Windows de
  64 bits los trunca.
* **Manejadores liberados en ``finally``**, tambien cuando algo falla.
* **Nada identificable en el log.** Solo codigos de estado del sistema; nunca
  nombres, huellas ni contenedores de claves (A09:2025).
"""

from __future__ import annotations

import ctypes
import hashlib
import logging
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Iterator

from ..errors import EngineError, denied, not_found, unsupported
from ..i18n import _

log = logging.getLogger("folio.engine.sign.winstore")

# ------------------------------------------------------------------- constantes

_LOAD_LIBRARY_SEARCH_SYSTEM32 = 0x00000800

_CERT_STORE_PROV_SYSTEM_W = 10
_CERT_SYSTEM_STORE_CURRENT_USER = 0x00010000
_CERT_STORE_READONLY_FLAG = 0x00008000
_CERT_STORE_OPEN_EXISTING_FLAG = 0x00004000
_STORE_FLAGS = _CERT_SYSTEM_STORE_CURRENT_USER | _CERT_STORE_READONLY_FLAG | _CERT_STORE_OPEN_EXISTING_FLAG

_CERT_KEY_PROV_INFO_PROP_ID = 2

# Se prefiere CNG (puede firmar con SHA-2 aunque la clave este en un proveedor
# antiguo de Microsoft) y se asocia el dialogo a una ventana si se indica.
# Deliberadamente NO se usa CRYPT_ACQUIRE_SILENT_FLAG (0x40).
_CRYPT_ACQUIRE_WINDOW_HANDLE_FLAG = 0x00000080
_CRYPT_ACQUIRE_PREFER_NCRYPT_KEY_FLAG = 0x00020000
_CERT_NCRYPT_KEY_SPEC = 0xFFFFFFFF
_AT_KEYEXCHANGE = 1
_AT_SIGNATURE = 2

_NCRYPT_PAD_PKCS1_FLAG = 0x00000002  # sin NCRYPT_SILENT_FLAG (0x40)
_NCRYPT_WINDOW_HANDLE_PROPERTY = "HWND Handle"

_HP_HASHVAL = 0x0002

# Construccion de la cadena sin salir a la red: solo almacenes y cache locales.
_CERT_CHAIN_CACHE_ONLY_URL_RETRIEVAL = 0x00000004
_CERT_CHAIN_DISABLE_AUTH_ROOT_AUTO_UPDATE = 0x00000100
_CHAIN_FLAGS = _CERT_CHAIN_CACHE_ONLY_URL_RETRIEVAL | _CERT_CHAIN_DISABLE_AUTH_ROOT_AUTO_UPDATE

# Bits de CERT_TRUST_STATUS.dwErrorStatus que indican que la cadena no acaba en
# una raiz en la que Windows confie (firma invalida, raiz desconocida, ciclo,
# cadena incompleta o desconfianza explicita).
CHAIN_NOT_TRUSTED_MASK = 0x00000008 | 0x00000020 | 0x00000080 | 0x00010000 | 0x04000000

_CRYPT_E_NOT_FOUND = 0x80092004

# Resumenes admitidos: nombre -> (identificador CNG, ALG_ID del API antiguo, bytes).
# SHA-256 como minimo; MD5 y SHA-1 no figuran y por tanto no se pueden pedir.
_HASHES: dict[str, tuple[str, int, int]] = {
    "sha256": ("SHA256", 0x0000800C, 32),
    "sha384": ("SHA384", 0x0000800D, 48),
    "sha512": ("SHA512", 0x0000800E, 64),
}

# Limites defensivos al leer almacenes (su contenido lo deciden terceros).
_MAX_CERTS_PER_STORE = 5000
_MAX_CERT_BYTES = 256 * 1024
_MAX_PROPERTY_BYTES = 64 * 1024
_MAX_SIGNATURE_BYTES = 2048  # holgado: una firma RSA de 16384 bits ocupa 2048
_MAX_CHAIN_ELEMENTS = 16

# Codigos de estado que se traducen a un mensaje concreto para el usuario.
_STATUS_CANCELLED = {0x8010006E, 0x800704C7, 0x80090036, 1223}
_STATUS_WRONG_PIN = {0x8010006B}
_STATUS_PIN_BLOCKED = {0x8010006C}
_STATUS_NO_DEVICE = {0x8010000C, 0x80100069, 0x8010002E, 0x80100017, 0x8010001D, 0x80100009}
_STATUS_NO_KEY = {0x80090016, 0x8009000D, 0x80092004, 0x8009200B}
_STATUS_DENIED = {0x80090010, 0x80070005, 5}
_STATUS_UNSUPPORTED = {0x80090008, 0x80090029}
# Fallos que solo pueden deberse a la ventana indicada: se reintenta sin ella.
_STATUS_BAD_WINDOW = {87, 1400, 0x80070057, 0x80070578, 0x80090009, 0x80090027}

# Proveedores de Microsoft cuyo tipo de almacenamiento se conoce. Para los demas
# no se afirma nada (``None``): abrir el proveedor solo para preguntarlo
# cargaria codigo de terceros durante un simple listado.
_HARDWARE_PROVIDERS = {
    "microsoft smart card key storage provider",
    "microsoft base smart card crypto provider",
    "microsoft platform crypto provider",
}
_SOFTWARE_PROVIDERS = {
    "microsoft software key storage provider",
    "microsoft base cryptographic provider v1.0",
    "microsoft enhanced cryptographic provider v1.0",
    "microsoft strong cryptographic provider",
    "microsoft enhanced rsa and aes cryptographic provider",
    "microsoft enhanced rsa and aes cryptographic provider (prototype)",
    "microsoft rsa schannel cryptographic provider",
    "microsoft base dss cryptographic provider",
    "microsoft base dss and diffie-hellman cryptographic provider",
    "microsoft enhanced dss and diffie-hellman cryptographic provider",
    "microsoft dh schannel cryptographic provider",
}


# ------------------------------------------------------------------ estructuras


class _CERT_CONTEXT(ctypes.Structure):
    _fields_ = [
        ("dwCertEncodingType", ctypes.c_uint32),
        ("pbCertEncoded", ctypes.c_void_p),
        ("cbCertEncoded", ctypes.c_uint32),
        ("pCertInfo", ctypes.c_void_p),
        ("hCertStore", ctypes.c_void_p),
    ]


class _CRYPT_KEY_PROV_INFO(ctypes.Structure):
    _fields_ = [
        ("pwszContainerName", ctypes.c_void_p),  # no se lee: identifica la clave del usuario
        ("pwszProvName", ctypes.c_wchar_p),
        ("dwProvType", ctypes.c_uint32),
        ("dwFlags", ctypes.c_uint32),
        ("cProvParam", ctypes.c_uint32),
        ("rgProvParam", ctypes.c_void_p),
        ("dwKeySpec", ctypes.c_uint32),
    ]


class _CERT_ENHKEY_USAGE(ctypes.Structure):
    _fields_ = [
        ("cUsageIdentifier", ctypes.c_uint32),
        ("rgpszUsageIdentifier", ctypes.c_void_p),
    ]


class _CERT_USAGE_MATCH(ctypes.Structure):
    _fields_ = [
        ("dwType", ctypes.c_uint32),
        ("Usage", _CERT_ENHKEY_USAGE),
    ]


class _CERT_CHAIN_PARA(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint32),
        ("RequestedUsage", _CERT_USAGE_MATCH),
    ]


class _CERT_TRUST_STATUS(ctypes.Structure):
    _fields_ = [
        ("dwErrorStatus", ctypes.c_uint32),
        ("dwInfoStatus", ctypes.c_uint32),
    ]


# De las tres estructuras siguientes solo se declara el principio (los campos
# que se leen). Las reserva y libera Windows; aqui nunca se crean ni se copian.
class _CERT_CHAIN_ELEMENT(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint32),
        ("pCertContext", ctypes.POINTER(_CERT_CONTEXT)),
    ]


class _CERT_SIMPLE_CHAIN(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint32),
        ("TrustStatus", _CERT_TRUST_STATUS),
        ("cElement", ctypes.c_uint32),
        ("rgpElement", ctypes.POINTER(ctypes.POINTER(_CERT_CHAIN_ELEMENT))),
    ]


class _CERT_CHAIN_CONTEXT(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint32),
        ("TrustStatus", _CERT_TRUST_STATUS),
        ("cChain", ctypes.c_uint32),
        ("rgpChain", ctypes.POINTER(ctypes.POINTER(_CERT_SIMPLE_CHAIN))),
    ]


class _BCRYPT_PKCS1_PADDING_INFO(ctypes.Structure):
    _fields_ = [("pszAlgId", ctypes.c_wchar_p)]


_PCCERT_CONTEXT = ctypes.POINTER(_CERT_CONTEXT)
_PCCERT_CHAIN_CONTEXT = ctypes.POINTER(_CERT_CHAIN_CONTEXT)
_HANDLE = ctypes.c_size_t  # HCRYPTPROV, HCRYPTHASH y NCRYPT_KEY_HANDLE son ULONG_PTR
_DWORD = ctypes.c_uint32
_BOOL = ctypes.c_int
_STATUS = ctypes.c_int32  # SECURITY_STATUS


# -------------------------------------------------------------------- bibliotecas


class _Api:
    """Funciones del sistema ya declaradas. Se crea una sola vez, al primer uso."""

    def __init__(self) -> None:
        def load(name: str) -> Any:
            return ctypes.WinDLL(name, use_last_error=True, winmode=_LOAD_LIBRARY_SEARCH_SYSTEM32)  # type: ignore[attr-defined]

        crypt32, ncrypt, advapi32, user32 = load("crypt32.dll"), load("ncrypt.dll"), load("advapi32.dll"), load("user32.dll")

        def declare(lib: Any, name: str, restype: Any, *argtypes: Any) -> Any:
            fn = getattr(lib, name)
            fn.argtypes = list(argtypes)
            fn.restype = restype
            return fn

        void_p, wstr, dword_p = ctypes.c_void_p, ctypes.c_wchar_p, ctypes.POINTER(_DWORD)

        # --- almacenes y certificados (todo consulta; nada escribe)
        self.CertOpenStore = declare(crypt32, "CertOpenStore", void_p, void_p, _DWORD, void_p, _DWORD, wstr)
        self.CertCloseStore = declare(crypt32, "CertCloseStore", _BOOL, void_p, _DWORD)
        self.CertEnumCertificatesInStore = declare(
            crypt32, "CertEnumCertificatesInStore", _PCCERT_CONTEXT, void_p, _PCCERT_CONTEXT
        )
        self.CertDuplicateCertificateContext = declare(
            crypt32, "CertDuplicateCertificateContext", _PCCERT_CONTEXT, _PCCERT_CONTEXT
        )
        self.CertFreeCertificateContext = declare(crypt32, "CertFreeCertificateContext", _BOOL, _PCCERT_CONTEXT)
        self.CertGetCertificateContextProperty = declare(
            crypt32, "CertGetCertificateContextProperty", _BOOL, _PCCERT_CONTEXT, _DWORD, void_p, dword_p
        )
        self.CertGetEnhancedKeyUsage = declare(
            crypt32, "CertGetEnhancedKeyUsage", _BOOL, _PCCERT_CONTEXT, _DWORD, void_p, dword_p
        )
        self.CertGetCertificateChain = declare(
            crypt32, "CertGetCertificateChain", _BOOL, void_p, _PCCERT_CONTEXT, void_p, void_p,
            ctypes.POINTER(_CERT_CHAIN_PARA), _DWORD, void_p, ctypes.POINTER(_PCCERT_CHAIN_CONTEXT),
        )
        self.CertFreeCertificateChain = declare(crypt32, "CertFreeCertificateChain", None, _PCCERT_CHAIN_CONTEXT)

        # --- clave privada: se obtiene un manejador, nunca la clave
        self.CryptAcquireCertificatePrivateKey = declare(
            crypt32, "CryptAcquireCertificatePrivateKey", _BOOL, _PCCERT_CONTEXT, _DWORD, void_p,
            ctypes.POINTER(_HANDLE), dword_p, ctypes.POINTER(_BOOL),
        )
        self.NCryptSignHash = declare(
            ncrypt, "NCryptSignHash", _STATUS, _HANDLE, void_p, void_p, _DWORD, void_p, _DWORD, dword_p, _DWORD
        )
        self.NCryptSetProperty = declare(ncrypt, "NCryptSetProperty", _STATUS, _HANDLE, wstr, void_p, _DWORD, _DWORD)
        self.NCryptFreeObject = declare(ncrypt, "NCryptFreeObject", _STATUS, _HANDLE)

        # --- proveedores antiguos (CSP) que Windows no puede abrir con CNG
        self.CryptCreateHash = declare(
            advapi32, "CryptCreateHash", _BOOL, _HANDLE, _DWORD, _HANDLE, _DWORD, ctypes.POINTER(_HANDLE)
        )
        self.CryptSetHashParam = declare(advapi32, "CryptSetHashParam", _BOOL, _HANDLE, _DWORD, void_p, _DWORD)
        self.CryptSignHashW = declare(advapi32, "CryptSignHashW", _BOOL, _HANDLE, _DWORD, wstr, _DWORD, void_p, dword_p)
        self.CryptDestroyHash = declare(advapi32, "CryptDestroyHash", _BOOL, _HANDLE)
        self.CryptReleaseContext = declare(advapi32, "CryptReleaseContext", _BOOL, _HANDLE, _DWORD)

        self.IsWindow = declare(user32, "IsWindow", _BOOL, void_p)


def available() -> bool:
    return sys.platform == "win32"


@lru_cache(maxsize=1)
def _api() -> _Api:
    if not available():
        raise unsupported(_("El almacén de certificados de Windows solo está disponible en Windows."))
    return _Api()


def _last_error() -> int:
    return ctypes.get_last_error() & 0xFFFFFFFF


# -------------------------------------------------------------------- resultados


@dataclass(frozen=True, repr=False)
class StoreCertificate:
    """Certificado leido del almacen (solo datos publicos)."""

    der: bytes
    thumbprint: str
    provider: str | None = None
    hardware: bool | None = None
    chain: tuple[bytes, ...] = ()  # emisores, del mas cercano a la raiz; sin el propio certificado
    chain_errors: int | None = None  # CERT_TRUST_STATUS.dwErrorStatus; None si no se calculo

    def __repr__(self) -> str:
        # Sin huella ni contenido: este objeto no debe poder identificar a nadie
        # si alguna vez acaba en un log o en el texto de una excepcion (A09:2025).
        return "<StoreCertificate>"


@dataclass(frozen=True, repr=False)
class TrustMaterial:
    """Certificados en los que confia Windows, para validar firmas ajenas."""

    roots: tuple[bytes, ...]
    intermediates: tuple[bytes, ...]

    def __repr__(self) -> str:
        return "<TrustMaterial>"


def thumbprint_of(der: bytes) -> str:
    """Huella SHA-1 en hexadecimal, la misma que muestra Windows.

    Es solo un identificador para elegir certificado (el usuario la reconoce en
    el administrador de certificados), no un control de seguridad: quien firma
    es la clave que Windows asocia a ese certificado y la firma se comprueba
    despues con su clave publica. Por eso SHA-1 es aceptable aqui (A04:2025).
    """
    return hashlib.sha1(der, usedforsecurity=False).hexdigest().upper()


# ----------------------------------------------------------------------- almacenes


@contextmanager
def _open_store(api: _Api, name: str, *, optional: bool = False) -> Iterator[int | None]:
    handle = api.CertOpenStore(_CERT_STORE_PROV_SYSTEM_W, 0, None, _STORE_FLAGS, name)
    if not handle:
        if optional:
            yield None
            return
        log.warning("no se pudo abrir un almacen de certificados (estado 0x%08X)", _last_error())
        raise EngineError("store_unavailable", _("No se pudo abrir el almacén de certificados de Windows."))
    try:
        yield handle
    finally:
        api.CertCloseStore(handle, 0)


def _contexts(api: _Api, store: int) -> Iterator[Any]:
    """Recorre los certificados de un almacen abierto.

    ``CertEnumCertificatesInStore`` libera el contexto anterior en cada paso; si
    el recorrido se corta antes del final, el ultimo se libera en ``finally``.
    Quien quiera conservar un contexto debe duplicarlo.
    """
    context = None
    count = 0
    try:
        while count < _MAX_CERTS_PER_STORE:
            context = api.CertEnumCertificatesInStore(store, context)
            if not context:
                context = None
                return
            count += 1
            yield context
    finally:
        if context:
            api.CertFreeCertificateContext(context)


def _der_of(context: Any) -> bytes | None:
    raw = context.contents
    size = int(raw.cbCertEncoded)
    if not raw.pbCertEncoded or not 0 < size <= _MAX_CERT_BYTES:
        return None
    return ctypes.string_at(raw.pbCertEncoded, size)


def _provider_of(api: _Api, context: Any) -> tuple[bool, str | None]:
    """``(tiene clave asociada, nombre del proveedor)`` sin tocar la clave."""
    size = _DWORD(0)
    if not api.CertGetCertificateContextProperty(context, _CERT_KEY_PROV_INFO_PROP_ID, None, ctypes.byref(size)):
        return False, None
    if not ctypes.sizeof(_CRYPT_KEY_PROV_INFO) <= size.value <= _MAX_PROPERTY_BYTES:
        return False, None
    buffer = ctypes.create_string_buffer(size.value)
    if not api.CertGetCertificateContextProperty(context, _CERT_KEY_PROV_INFO_PROP_ID, buffer, ctypes.byref(size)):
        return False, None
    info = _CRYPT_KEY_PROV_INFO.from_buffer(buffer)
    name = info.pwszProvName  # ctypes copia el texto: no depende de ``buffer`` despues
    return True, (name[:200] if name else None)


def _hardware_hint(provider: str | None) -> bool | None:
    key = (provider or "").strip().lower()
    if key in _HARDWARE_PROVIDERS:
        return True
    if key in _SOFTWARE_PROVIDERS:
        return False
    return None


def list_certificates() -> list[StoreCertificate]:
    """Certificados de ``CurrentUser\\MY`` que tienen clave privada asociada.

    Solo se consulta la propiedad que dice donde esta la clave; la clave no se
    abre, asi que listar nunca hace aparecer un dialogo de PIN.
    """
    api = _api()
    out: list[StoreCertificate] = []
    with _open_store(api, "MY") as store:
        for context in _contexts(api, store):
            has_key, provider = _provider_of(api, context)
            if not has_key:
                continue
            der = _der_of(context)
            if der is None:
                continue
            out.append(StoreCertificate(der, thumbprint_of(der), provider, _hardware_hint(provider)))
    return out


def _find(api: _Api, store: int, thumbprint: str) -> Any:
    """Contexto duplicado del certificado con esa huella; quien llama debe liberarlo."""
    for context in _contexts(api, store):
        der = _der_of(context)
        if der is None or thumbprint_of(der) != thumbprint:
            continue
        has_key, _provider = _provider_of(api, context)
        if not has_key:
            continue
        duplicate = api.CertDuplicateCertificateContext(context)
        if duplicate:
            return duplicate
    raise not_found(_("El certificado ya no está en el almacén de Windows o no tiene clave privada."))


def _chain_of(api: _Api, context: Any) -> tuple[tuple[bytes, ...], int | None]:
    """Emisores del certificado segun Windows y estado de confianza de la cadena.

    Se construye solo con lo que hay en el equipo (almacenes y cache): firmar no
    debe provocar conexiones de red por su cuenta.
    """
    para = _CERT_CHAIN_PARA()
    para.cbSize = ctypes.sizeof(_CERT_CHAIN_PARA)
    chain = _PCCERT_CHAIN_CONTEXT()
    ok = api.CertGetCertificateChain(
        None, context, None, context.contents.hCertStore, ctypes.byref(para), _CHAIN_FLAGS, None, ctypes.byref(chain)
    )
    if not ok or not chain:
        log.warning("no se pudo construir la cadena del certificado (estado 0x%08X)", _last_error())
        return (), None
    try:
        info = chain.contents
        errors = int(info.TrustStatus.dwErrorStatus)
        if info.cChain < 1 or not info.rgpChain or not info.rgpChain[0]:
            return (), errors
        simple = info.rgpChain[0].contents
        issuers: list[bytes] = []
        # El elemento 0 es el propio certificado; el resto, sus emisores.
        for index in range(1, min(int(simple.cElement), _MAX_CHAIN_ELEMENTS)):
            element = simple.rgpElement[index]
            if not element or not element.contents.pCertContext:
                break
            der = _der_of(element.contents.pCertContext)
            if der is not None:
                issuers.append(der)
        return tuple(issuers), errors
    finally:
        api.CertFreeCertificateChain(chain)


def load_certificate(thumbprint: str) -> StoreCertificate:
    """Certificado con clave privada elegido por su huella, con su cadena de emisores."""
    api = _api()
    with _open_store(api, "MY") as store:
        context = _find(api, store, thumbprint)
        try:
            der = _der_of(context)
            if der is None:
                raise not_found(_("El certificado ya no está en el almacén de Windows o no tiene clave privada."))
            _has_key, provider = _provider_of(api, context)
            chain, errors = _chain_of(api, context)
            return StoreCertificate(der, thumbprint_of(der), provider, _hardware_hint(provider), chain, errors)
        finally:
            api.CertFreeCertificateContext(context)


# ----------------------------------------------------------------- confianza


def _no_valid_uses(api: _Api, context: Any) -> bool:
    """Indica si el usuario o el sistema han desactivado todos los usos del certificado.

    Windows permite dejar una raiz en el almacen pero "sin ningun proposito". Si
    no se puede consultar se toma como desactivada: ante la duda no se confia
    (A10:2025, fallar del lado seguro).
    """
    size = _DWORD(0)
    if not api.CertGetEnhancedKeyUsage(context, 0, None, ctypes.byref(size)):
        return True
    if not ctypes.sizeof(_CERT_ENHKEY_USAGE) <= size.value <= _MAX_PROPERTY_BYTES:
        return True
    buffer = ctypes.create_string_buffer(size.value)
    ctypes.set_last_error(0)
    if not api.CertGetEnhancedKeyUsage(context, 0, buffer, ctypes.byref(size)):
        return True
    usage = _CERT_ENHKEY_USAGE.from_buffer(buffer)
    # Lista vacia + CRYPT_E_NOT_FOUND = "vale para todo"; lista vacia sin ese
    # codigo = "no vale para nada".
    return usage.cUsageIdentifier == 0 and _last_error() != _CRYPT_E_NOT_FOUND


def _read_store(api: _Api, name: str, *, optional: bool = False, usable_only: bool = False) -> list[bytes]:
    out: list[bytes] = []
    with _open_store(api, name, optional=optional) as store:
        if store is None:
            return out
        for context in _contexts(api, store):
            der = _der_of(context)
            if der is None:
                continue
            if usable_only and _no_valid_uses(api, context):
                continue
            out.append(der)
    return out


def trust_material() -> TrustMaterial:
    """Raices e intermedios en los que confia Windows para este usuario.

    El motor no mantiene una lista propia de autoridades: usa la del sistema,
    que el usuario y Windows administran, y solo la lee. Se descarta lo que
    Windows marca como "no permitido" y las raices con todos los usos
    desactivados, para no confiar en algo en lo que el sistema ya no confia.
    """
    api = _api()
    blocked = {hashlib.sha256(der).digest() for der in _read_store(api, "Disallowed", optional=True)}

    def allowed(items: list[bytes]) -> tuple[bytes, ...]:
        seen: set[bytes] = set()
        kept: list[bytes] = []
        for der in items:
            digest = hashlib.sha256(der).digest()
            if digest in blocked or digest in seen:
                continue
            seen.add(digest)
            kept.append(der)
        return tuple(kept)

    roots = allowed(_read_store(api, "ROOT", usable_only=True))
    intermediates = allowed(_read_store(api, "CA", optional=True))
    return TrustMaterial(roots, intermediates)


# ---------------------------------------------------------------------- firmar


def usable_window(hwnd: int | None) -> int | None:
    """Manejador de ventana aceptable para asociarle los dialogos de Windows.

    Llega de fuera, asi que no se da por bueno: debe ser un entero en el rango
    de los manejadores de ventana y corresponder a una ventana que exista. Si
    no, se ignora: el dialogo saldra igualmente, solo que sin ventana madre.
    """
    if hwnd is None or isinstance(hwnd, bool) or not isinstance(hwnd, int) or not 0 < hwnd <= 0xFFFFFFFF:
        return None
    try:
        return hwnd if _api().IsWindow(hwnd) else None
    except Exception:  # noqa: BLE001 - la ventana es solo una comodidad
        return None


def _key_error(status: int) -> EngineError:
    """Convierte un codigo de Windows en un error para el usuario (sin datos del certificado)."""
    status &= 0xFFFFFFFF
    log.warning("Windows no completo la operacion con la clave (estado 0x%08X)", status)
    if status in _STATUS_CANCELLED:
        return EngineError("cancelled", _("Se ha cancelado el uso de la clave del certificado."))
    if status in _STATUS_WRONG_PIN:
        return EngineError("wrong_pin", _("El PIN no es correcto."))
    if status in _STATUS_PIN_BLOCKED:
        return EngineError("pin_blocked", _("El PIN está bloqueado. Desbloquéalo con el programa de tu tarjeta o dispositivo."))
    if status in _STATUS_NO_DEVICE:
        return EngineError(
            "device_missing", _("No se encuentra la tarjeta o el dispositivo que guarda la clave de este certificado.")
        )
    if status in _STATUS_NO_KEY:
        return EngineError("key_unavailable", _("Windows no encuentra la clave privada de este certificado."))
    if status in _STATUS_DENIED:
        return denied(_("Windows no ha permitido usar la clave de este certificado."))
    if status in _STATUS_UNSUPPORTED:
        return unsupported(
            _(
                "El proveedor que guarda la clave de este certificado no admite firmas con SHA-256. "
                "Vuelve a importar el certificado en Windows para poder firmar con él."
            )
        )
    return EngineError(
        "sign_failed", _("Windows no pudo firmar con la clave de este certificado."), status=f"0x{status:08X}"
    )


def _sign_cng(api: _Api, key: int, digest: bytes, hash_name: str, key_kind: str, window: Any) -> bytes:
    if window is not None:
        # Asocia el dialogo del proveedor (PIN, consentimiento) a la ventana de la
        # aplicacion. Si el proveedor no lo admite se ignora: es solo presentacion.
        api.NCryptSetProperty(key, _NCRYPT_WINDOW_HANDLE_PROPERTY, ctypes.byref(window), ctypes.sizeof(window), 0)
    digest_buffer = (ctypes.c_ubyte * len(digest)).from_buffer_copy(digest)
    signature = (ctypes.c_ubyte * _MAX_SIGNATURE_BYTES)()
    written = _DWORD(0)
    if key_kind == "rsa":
        # PKCS#1 v1.5 con el resumen indicado: Windows compone el relleno, no nosotros.
        padding = _BCRYPT_PKCS1_PADDING_INFO(_HASHES[hash_name][0])
        status = api.NCryptSignHash(
            key, ctypes.byref(padding), digest_buffer, len(digest), signature, len(signature),
            ctypes.byref(written), _NCRYPT_PAD_PKCS1_FLAG,
        )
    else:
        status = api.NCryptSignHash(
            key, None, digest_buffer, len(digest), signature, len(signature), ctypes.byref(written), 0
        )
    if status != 0:
        raise _key_error(status)
    if not 0 < written.value <= len(signature):
        raise EngineError("sign_failed", _("Windows devolvió una firma con un tamaño inesperado."))
    return bytes(signature[: written.value])


def _sign_legacy(api: _Api, provider: int, key_spec: int, digest: bytes, hash_name: str, key_kind: str) -> bytes:
    """Firma con un proveedor antiguo (CSP) que Windows no puede abrir mediante CNG."""
    if key_kind != "rsa" or key_spec not in (_AT_KEYEXCHANGE, _AT_SIGNATURE):
        raise unsupported(_("El proveedor que guarda la clave de este certificado no es compatible."))
    hash_handle = _HANDLE(0)
    if not api.CryptCreateHash(provider, _HASHES[hash_name][1], 0, 0, ctypes.byref(hash_handle)):
        raise _key_error(_last_error())
    try:
        digest_buffer = (ctypes.c_ubyte * len(digest)).from_buffer_copy(digest)
        # Se entrega el resumen ya calculado; el proveedor solo anade el relleno y firma.
        if not api.CryptSetHashParam(hash_handle, _HP_HASHVAL, digest_buffer, 0):
            raise _key_error(_last_error())
        signature = (ctypes.c_ubyte * _MAX_SIGNATURE_BYTES)()
        written = _DWORD(len(signature))
        if not api.CryptSignHashW(hash_handle, key_spec, None, 0, signature, ctypes.byref(written)):
            raise _key_error(_last_error())
        if not 0 < written.value <= len(signature):
            raise EngineError("sign_failed", _("Windows devolvió una firma con un tamaño inesperado."))
        # El API antiguo devuelve la firma con los bytes en orden inverso al de PKCS#1.
        return bytes(signature[: written.value])[::-1]
    finally:
        api.CryptDestroyHash(hash_handle)


def _acquire(api: _Api, context: Any, flags: int, window: Any) -> tuple[bool, _HANDLE, int, bool, int]:
    handle = _HANDLE(0)
    key_spec = _DWORD(0)
    caller_frees = _BOOL(0)
    parameters = None
    if window is not None:
        flags |= _CRYPT_ACQUIRE_WINDOW_HANDLE_FLAG
        parameters = ctypes.byref(window)
    ok = bool(
        api.CryptAcquireCertificatePrivateKey(
            context, flags, parameters, ctypes.byref(handle), ctypes.byref(key_spec), ctypes.byref(caller_frees)
        )
    )
    return ok, handle, int(key_spec.value), bool(caller_frees.value), (0 if ok else _last_error())


def sign_digest(
    thumbprint: str,
    digest: bytes,
    *,
    hash_name: str,
    key_kind: str,
    hwnd: int | None = None,
    _force_legacy: bool = False,
) -> bytes:
    """Firma un resumen ya calculado con la clave privada del certificado indicado.

    Devuelve la firma tal como la produce el algoritmo: PKCS#1 v1.5 para RSA y
    ``r || s`` para ECDSA. La clave se abre justo para esta llamada y su
    manejador se libera al terminar, pase lo que pase.

    ``_force_legacy`` existe solo para las pruebas: obliga a usar el camino de
    los proveedores antiguos (CSP) en lugar de CNG.
    """
    if hash_name not in _HASHES:
        raise unsupported(_("Algoritmo de resumen no admitido."))
    if not isinstance(digest, bytes) or len(digest) != _HASHES[hash_name][2]:
        raise EngineError("internal", _("El resumen que se iba a firmar no tiene el tamaño esperado."))
    if key_kind not in ("rsa", "ec"):
        raise unsupported(_("Este tipo de clave no es compatible."))

    api = _api()
    valid_window = usable_window(hwnd)
    window = ctypes.c_void_p(valid_window) if valid_window else None
    flags = 0 if _force_legacy else _CRYPT_ACQUIRE_PREFER_NCRYPT_KEY_FLAG

    with _open_store(api, "MY") as store:
        context = _find(api, store, thumbprint)
        try:
            ok, handle, key_spec, caller_frees, status = _acquire(api, context, flags, window)
            if not ok and window is not None and status in _STATUS_BAD_WINDOW:
                # La ventana es opcional: si es lo que ha fallado, se sigue sin ella.
                # No se reintenta ante ningun otro error (por ejemplo una
                # cancelacion) para no mostrar dos veces el mismo dialogo.
                window = None
                ok, handle, key_spec, caller_frees, status = _acquire(api, context, flags, None)
            if not ok:
                raise _key_error(status)
            try:
                if key_spec == _CERT_NCRYPT_KEY_SPEC:
                    return _sign_cng(api, handle.value, digest, hash_name, key_kind, window)
                return _sign_legacy(api, handle.value, key_spec, digest, hash_name, key_kind)
            finally:
                if caller_frees:
                    if key_spec == _CERT_NCRYPT_KEY_SPEC:
                        api.NCryptFreeObject(handle.value)
                    else:
                        api.CryptReleaseContext(handle.value, 0)
        finally:
            api.CertFreeCertificateContext(context)
