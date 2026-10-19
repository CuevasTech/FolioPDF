"""Las dos unicas salidas a la red del motor: sello de tiempo y revocacion.

Con el motor aislado (sin red), estas conexiones las hace el intermediario
(``broker.py``) con las mismas funciones: ``post_timestamp`` y
``fetch_revocation``; el motor solo les pasa la peticion ya formada.

El motor no abre puertos y por defecto no se conecta a nada. Solo sale a la red
en dos casos, ambos pedidos expresamente por el usuario:

* **Sello de tiempo**, a la direccion que el usuario ha configurado.
* **Revocacion en linea** (``sign.validate`` con ``online``), a las direcciones
  que declaran los certificados del documento.

El segundo caso es el delicado: las direcciones salen de un PDF ajeno, asi que
un documento malicioso podria usarlas para que este equipo hiciera peticiones a
servicios internos (falsificacion de peticiones, A01:2025). Por eso las
peticiones solo pueden ir a direcciones publicas de Internet: se rechazan las
privadas, las de bucle local y las de enlace local, tanto si vienen escritas
como numero como si salen de resolver un nombre o de una redireccion.

El sello de tiempo pasa por el mismo filtro. Su direccion la escribe el
usuario, pero llega al motor como un parametro mas: si la interfaz estuviera
comprometida podria apuntarla a un servicio interno. Las autoridades de sellado
son servicios publicos, asi que no se pierde nada por exigirlo.

Para ambos casos: tiempo de espera corto, tamano de respuesta acotado, sin
proxy tomado del entorno, sin cookies y con TLS verificado (A04:2025).
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import ssl
from typing import Any
from urllib.parse import urlsplit

import aiohttp
import certifi
from aiohttp.abc import AbstractResolver
from asn1crypto import ocsp, tsp
from multidict import CIMultiDict, CIMultiDictProxy
from pyhanko.sign.timestamps import HTTPTimeStamper, TimestampRequestError
from pyhanko.sign.timestamps.api import TimeStamper
from pyhanko_certvalidator.fetchers.aiohttp_fetchers import AIOHttpFetcherBackend
from pyhanko_certvalidator.fetchers.aiohttp_fetchers.util import LazySession
from yarl import URL

from ..errors import EngineError, invalid
from ..i18n import _

MAX_URL_LENGTH = 2048
TIMESTAMP_TIMEOUT = 10.0  # segundos por peticion; una firma con sello hace dos
MAX_TIMESTAMP_BYTES = 256 * 1024  # un sello real ocupa unos pocos KB
MAX_TIMESTAMP_REQUEST = 8 * 1024  # una peticion de sello: un resumen y poco mas
MAX_OCSP_REQUEST = 16 * 1024
REVOCATION_TIMEOUT = 8.0
MAX_REVOCATION_BYTES = 32 * 1024 * 1024  # hay listas de revocacion de varios MB
_CONNECT_TIMEOUT = 5.0
_TIMESTAMP_REPLY = "application/timestamp-reply"
# Solo lo cambian las pruebas, que usan una autoridad de mentira en 127.0.0.1.
_ALLOW_PRIVATE_TIMESTAMP = False
_ALLOW_PRIVATE_REVOCATION = False
# Prefijos IPv6 que llevan dentro una direccion IPv4 (traduccion NAT64).
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_NAT64_LOCAL = ipaddress.ip_network("64:ff9b:1::/48")


def check_http_url(raw: str) -> str:
    """Valida la direccion del servidor de sello de tiempo.

    La escribe el usuario en la configuracion, pero se comprueba igual que
    cualquier dato de fuera (lista blanca, A05:2025): solo ``http`` o ``https``,
    con nombre de servidor, sin credenciales incrustadas y de longitud acotada.
    """
    bad = invalid(_("La dirección del servidor de sello de tiempo no es válida. Debe empezar por http:// o https://."))
    if not isinstance(raw, str) or not 0 < len(raw) <= MAX_URL_LENGTH:
        raise bad
    # Espacios, controles y barras invertidas hacen que distintos programas
    # entiendan la misma direccion de formas distintas: se rechazan sin mas.
    if any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F or ch == "\\" for ch in raw):
        raise bad
    try:
        parts = urlsplit(raw)
        _port = parts.port  # lanza ValueError si el puerto no es un numero valido
        host = parts.hostname
    except ValueError:
        raise bad from None
    if parts.scheme not in ("http", "https") or not host:
        raise bad
    if parts.username is not None or parts.password is not None:
        raise invalid(_("La dirección del servidor de sello de tiempo no puede llevar usuario ni contraseña."))
    return raw


def _is_public(address: str) -> bool:
    """Indica si una direccion IP es de Internet (ni privada, ni local, ni reservada)."""
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        # Direcciones IPv6 que en realidad llevan a una IPv4: se juzga la de
        # dentro, que es adonde llega la conexion. Sin esto, en una red con
        # NAT64 «64:ff9b::a0a:a0a» pasaria por publica siendo 10.10.10.10.
        embedded = ip.ipv4_mapped or ip.sixtofour  # ::ffff:10.0.0.1 y 2002:0a00:0001::
        if embedded is None and ip in _NAT64:
            embedded = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        if embedded is not None:
            ip = embedded
        elif ip in _NAT64_LOCAL or ip.teredo is not None:
            return False  # NAT64 de uso local y Teredo (tunel hacia un cliente cualquiera)
    return bool(ip.is_global) and not ip.is_multicast


def _require_public(url: URL) -> None:
    if url.scheme not in ("http", "https"):
        raise aiohttp.ClientConnectionError("esquema no permitido")
    host = url.host or ""
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return  # es un nombre: lo filtra _PublicResolver al resolverlo
    if not _is_public(host):
        raise aiohttp.ClientConnectionError("destino no permitido")


class _PublicResolver(AbstractResolver):
    """Resuelve nombres y descarta las direcciones que no sean publicas.

    Se filtra el resultado que de verdad se va a usar para conectar, de modo que
    no sirve de nada que un nombre resuelva primero a una direccion publica y
    luego a una interna.
    """

    def __init__(self) -> None:
        self._inner = aiohttp.ThreadedResolver()

    async def resolve(self, host: str, port: int = 0, family: socket.AddressFamily = socket.AF_INET) -> list[Any]:
        found = await self._inner.resolve(host, port, family)
        allowed = [item for item in found if _is_public(str(item["host"]))]
        if not allowed:
            raise OSError("destino no permitido")
        return allowed

    async def close(self) -> None:
        await self._inner.close()


def _public_trace() -> aiohttp.TraceConfig:
    """Comprueba las direcciones dadas como numero, al empezar y en cada redireccion."""

    async def on_start(_session: Any, _context: Any, params: Any) -> None:
        _require_public(params.url)

    async def on_redirect(_session: Any, _context: Any, params: Any) -> None:
        location = params.response.headers.get("Location")
        if location:
            _require_public(params.url.join(URL(location)))

    config = aiohttp.TraceConfig()
    config.on_request_start.append(on_start)
    config.on_request_redirect.append(on_redirect)
    return config


def _capped_response(limit: int) -> type[aiohttp.ClientResponse]:
    class Capped(aiohttp.ClientResponse):
        async def read(self) -> bytes:
            # Se lee por trozos y se corta al pasar del limite: un servidor no
            # puede agotar la memoria del motor con una respuesta sin fin.
            chunks: list[bytes] = []
            total = 0
            async for chunk in self.content.iter_chunked(64 * 1024):
                total += len(chunk)
                if total > limit:
                    self.close()
                    raise aiohttp.ClientPayloadError("respuesta demasiado grande")
                chunks.append(chunk)
            return b"".join(chunks)

    return Capped


def _tls_context() -> ssl.SSLContext:
    # Contexto por defecto: comprueba el certificado del servidor y su nombre.
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    try:
        # En Windows las raices se descargan segun se van usando y puede faltar
        # alguna legitima; se anade la lista de Mozilla que acompana al motor.
        context.load_verify_locations(cafile=certifi.where())
    except Exception:  # noqa: BLE001 - quedan las raices del sistema
        pass
    return context


def _session(*, public_only: bool, max_bytes: int) -> aiohttp.ClientSession:
    """Sesion HTTP endurecida. Debe crearse dentro del bucle de eventos que la usa."""
    connector = aiohttp.TCPConnector(
        ssl=_tls_context(), resolver=_PublicResolver() if public_only else None, limit=8, force_close=True
    )
    return aiohttp.ClientSession(
        connector=connector,
        trust_env=False,  # sin proxy ni credenciales tomados de variables de entorno
        cookie_jar=aiohttp.DummyCookieJar(),
        auto_decompress=False,  # sin descompresion: una respuesta pequena no puede inflarse a gigas
        headers={"Accept-Encoding": "identity"},
        response_class=_capped_response(max_bytes),
        trace_configs=[_public_trace()] if public_only else [],
    )


async def _post_timestamp(url: str, body: bytes) -> bytes:
    timeout = aiohttp.ClientTimeout(total=TIMESTAMP_TIMEOUT, connect=_CONNECT_TIMEOUT)
    try:
        async with _session(public_only=not _ALLOW_PRIVATE_TIMESTAMP, max_bytes=MAX_TIMESTAMP_BYTES) as session:
            async with session.post(
                url, data=body, headers={"Content-Type": "application/timestamp-query"}, allow_redirects=False,
                timeout=timeout,
            ) as response:
                kind = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                if response.status != 200 or kind != _TIMESTAMP_REPLY:
                    raise TimestampRequestError("respuesta inesperada del servidor de sello de tiempo")
                return await response.read()
    except TimestampRequestError:
        raise
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
        raise TimestampRequestError("no se pudo contactar con el servidor de sello de tiempo") from None


class _CheckedImprint:
    """Exige que el sello recibido sea para el resumen que se envio."""

    async def async_timestamp(self, message_digest: bytes, md_algorithm: str) -> Any:
        try:
            token = await super().async_timestamp(message_digest, md_algorithm)  # type: ignore[misc]
            imprint = token["content"]["encap_content_info"]["content"].parsed["message_imprint"]
            stamped = imprint["hashed_message"].native
            algorithm = imprint["hash_algorithm"]["algorithm"].native
        except TimestampRequestError:
            raise
        except Exception:  # noqa: BLE001 - respuesta que no se deja leer
            raise TimestampRequestError("respuesta no valida del servidor de sello de tiempo") from None
        if stamped != message_digest or algorithm != md_algorithm:
            raise TimestampRequestError("el sello de tiempo recibido no corresponde con la firma")
        return token


class GuardedTimeStamper(_CheckedImprint, HTTPTimeStamper):
    """Cliente de sello de tiempo (RFC 3161) con limites.

    RFC 3161 suele ir sobre HTTP sin cifrar y es correcto que asi sea: lo que
    se envia es solo el resumen de la firma (nada confidencial) y la respuesta
    es un sello firmado por la autoridad, que se comprueba criptograficamente.
    La seguridad no depende del transporte. Aun asi:

    * no se siguen redirecciones (el servidor no puede desviarnos a otro
      esquema ni a otra maquina; la direccion valida es la que puso el usuario);
    * se exige que el sello recibido sea para el resumen que se envio.
    """

    def __init__(self, url: str) -> None:
        super().__init__(check_http_url(url), timeout=TIMESTAMP_TIMEOUT)

    async def async_request_tsa_response(self, req: tsp.TimeStampReq) -> tsp.TimeStampResp:
        return tsp.TimeStampResp.load(await _post_timestamp(self.url, req.dump()))


class BrokeredTimeStamper(_CheckedImprint, TimeStamper):
    """Sello de tiempo pedido a traves del intermediario (el motor aislado no tiene red).

    La direccion de la autoridad no sale de aqui: la conoce el intermediario,
    que la recibio con la peticion de firma del usuario.
    """

    def __init__(self, call: Any) -> None:
        super().__init__()
        self._call = call

    async def async_request_tsa_response(self, req: tsp.TimeStampReq) -> tsp.TimeStampResp:
        loop = asyncio.get_running_loop()
        try:
            _result, body = await loop.run_in_executor(None, self._call, "tsa.post", {}, req.dump())
        except EngineError:
            raise TimestampRequestError("no se pudo contactar con el servidor de sello de tiempo") from None
        try:
            return tsp.TimeStampResp.load(body)
        except Exception:  # noqa: BLE001
            raise TimestampRequestError("respuesta no valida del servidor de sello de tiempo") from None


def post_timestamp(url: str, body: bytes) -> bytes:
    """Lado del intermediario: envia una peticion de sello ya formada y devuelve la respuesta.

    La peticion la compone el motor aislado, asi que se trata como dato no
    fiable: solo sale si es una peticion de sello RFC 3161 bien formada y de
    tamano razonable, y solo hacia la direccion que eligio el usuario.
    """
    check_http_url(url)
    if not 0 < len(body) <= MAX_TIMESTAMP_REQUEST:
        raise invalid(_("Petición de sello de tiempo no válida."))
    try:
        request = tsp.TimeStampReq.load(body)
        _parsed = request.native
        if request.dump(force=True) != body:  # sin bytes de mas ni codificaciones alternativas
            raise ValueError
    except Exception:  # noqa: BLE001
        raise invalid(_("Petición de sello de tiempo no válida.")) from None
    try:
        return asyncio.run(_post_timestamp(url, body))
    except TimestampRequestError:
        raise EngineError("timestamp_failed", _("No se pudo obtener el sello de tiempo del servidor configurado.")) from None


class _PublicSession(LazySession):
    def get_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = _session(public_only=not _ALLOW_PRIVATE_REVOCATION, max_bytes=MAX_REVOCATION_BYTES)
        return self._session


def revocation_backend() -> AIOHttpFetcherBackend:
    """Descarga de informacion de revocacion limitada a direcciones publicas.

    Quien lo pide debe cerrarlo (``await backend.close()``) al terminar.
    """
    return AIOHttpFetcherBackend(session=_PublicSession(), per_request_timeout=REVOCATION_TIMEOUT)  # type: ignore[arg-type]


# ------------------------------------------------- revocacion a traves del intermediario


class _BrokerResponse:
    """Lo que necesitan los clientes de pyHanko de una respuesta HTTP."""

    def __init__(self, url: str, method: str, status: int, content_type: str, body: bytes) -> None:
        self.status = status
        self.headers = CIMultiDictProxy(CIMultiDict({"Content-Type": content_type} if content_type else {}))
        target = URL(url)
        self.request_info = aiohttp.RequestInfo(target, method, CIMultiDictProxy(CIMultiDict()), target)
        self.history: tuple[Any, ...] = ()
        self._body = body

    async def read(self) -> bytes:
        return self._body


class _BrokerRequest:
    def __init__(self, call: Any, method: str, url: Any, data: Any, headers: Any, raise_for_status: bool) -> None:
        self._call = call
        self._method = method
        self._url = str(url)
        self._data = bytes(data) if data is not None else b""
        accept = (headers or {}).get("Accept", "") if isinstance(headers, dict) else ""
        self._accept = accept if isinstance(accept, str) else ""
        self._raise = raise_for_status

    async def __aenter__(self) -> _BrokerResponse:
        params = {"method": self._method, "url": self._url, "accept": self._accept[:512]}
        loop = asyncio.get_running_loop()
        try:
            result, body = await loop.run_in_executor(None, self._call, "net.fetch", params, self._data)
        except EngineError:
            raise aiohttp.ClientConnectionError("no se pudo consultar la revocacion") from None
        status = result.get("status")
        kind = result.get("contentType")
        if not isinstance(status, int) or isinstance(status, bool) or not isinstance(kind, str):
            raise aiohttp.ClientConnectionError("respuesta no valida")
        response = _BrokerResponse(self._url, self._method, status, kind, body)
        if self._raise and status >= 400:
            raise aiohttp.ClientResponseError(response.request_info, (), status=status, message="error del servidor")
        return response

    async def __aexit__(self, *_exc: Any) -> bool:
        return False


class _BrokerHttp:
    """Sustituto de la sesion de aiohttp: cada peticion la hace el intermediario."""

    def __init__(self, call: Any) -> None:
        self._call = call

    def get(self, url: Any, *, headers: Any = None, raise_for_status: bool = False, **_ignored: Any) -> _BrokerRequest:
        return _BrokerRequest(self._call, "GET", url, None, headers, raise_for_status)

    def post(
        self, url: Any, *, headers: Any = None, data: Any = None, raise_for_status: bool = False, **_ignored: Any
    ) -> _BrokerRequest:
        return _BrokerRequest(self._call, "POST", url, data, headers, raise_for_status)

    async def close(self) -> None:
        return None


class _BrokerSession(LazySession):
    def __init__(self, call: Any) -> None:
        super().__init__()
        self._call = call

    def get_session(self) -> Any:
        if self._session is None:
            self._session = _BrokerHttp(self._call)
        return self._session


def brokered_revocation_backend(call: Any) -> AIOHttpFetcherBackend:
    """Igual que ``revocation_backend`` pero sin red propia: pregunta el intermediario."""
    return AIOHttpFetcherBackend(session=_BrokerSession(call), per_request_timeout=REVOCATION_TIMEOUT)  # type: ignore[arg-type]


def fetch_revocation(method: str, url: str, body: bytes, accept: str) -> tuple[int, str, bytes]:
    """Lado del intermediario: una consulta de revocacion (CRL, OCSP o certificado emisor).

    Las direcciones salen de un PDF ajeno: solo direcciones publicas (ver la
    cabecera del modulo), solo GET sin cuerpo o POST con una peticion OCSP bien
    formada, y con tiempo y tamano acotados.
    """
    bad = invalid(_("Dirección de consulta de revocación no válida."))
    try:
        check_http_url(url)
    except EngineError:
        raise bad from None
    if method == "GET":
        if body:
            raise bad
        content_type = None
    elif method == "POST":
        if not 0 < len(body) <= MAX_OCSP_REQUEST:
            raise bad
        try:
            request = ocsp.OCSPRequest.load(body)
            _parsed = request.native
            if request.dump(force=True) != body:
                raise ValueError
        except Exception:  # noqa: BLE001
            raise bad from None
        content_type = "application/ocsp-request"
    else:
        raise bad
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in accept) or len(accept) > 512:
        raise bad

    async def run() -> tuple[int, str, bytes]:
        timeout = aiohttp.ClientTimeout(total=REVOCATION_TIMEOUT, connect=_CONNECT_TIMEOUT)
        headers = {"Accept": accept or "*/*", "User-Agent": "Folio"}
        if content_type:
            headers["Content-Type"] = content_type
        async with _session(public_only=not _ALLOW_PRIVATE_REVOCATION, max_bytes=MAX_REVOCATION_BYTES) as session:
            async with session.request(
                method, url, data=body if content_type else None, headers=headers, timeout=timeout, max_redirects=5
            ) as response:
                data = await response.read()
                kind = (response.headers.get("Content-Type") or "").strip()[:200]
                return response.status, kind, data

    try:
        return asyncio.run(run())
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
        raise EngineError("net_failed", _("No se pudo consultar la revocación del certificado.")) from None
