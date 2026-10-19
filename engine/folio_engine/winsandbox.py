"""Aislamiento del motor en Windows: AppContainer, objeto de trabajo y mitigaciones.

El proceso que analiza los PDF (el "aislado", ver ``broker.py``) se ejecuta en
un AppContainer: el mecanismo con el que Windows encierra las aplicaciones de
la Tienda y los navegadores sus procesos de contenido. Sin capacidades
declaradas, un proceso asi:

* no puede abrir los archivos del usuario (documentos, escritorio, perfil...):
  solo su propia carpeta y lo que se le conceda de forma explicita (aqui, leer
  los archivos del propio motor y la carpeta de modelos de la IA);
* no tiene red, ni hacia Internet ni hacia el propio equipo;
* corre con nivel de integridad bajo: no puede tocar otros procesos ni las
  ventanas del usuario.

Por si un fallo de MuPDF diera a un PDF malicioso el control del proceso
(A06:2025, defensa en profundidad), ademas:

* un objeto de trabajo lo mata si muere quien lo lanzo, limita su memoria y le
  cierra el portapapeles y el resto del escritorio;
* no puede crear procesos;
* se activan mitigaciones de Windows al crearlo (ASLR forzado, comprobacion
  estricta de manejadores, sin extensiones del sistema ni fuentes ajenas, sin
  cargar bibliotecas remotas o de nivel bajo) y, ya en marcha, se le prohibe
  generar codigo ejecutable (``lock_down_dynamic_code``).

Todo se hace con la API de Windows mediante ctypes: kernel32, advapi32, userenv
y ole32 son bibliotecas del sistema que Windows carga siempre de System32.
"""

from __future__ import annotations

import _winapi
import ctypes
import ctypes.wintypes as wt
import hashlib
import msvcrt
import os
import re
import shutil
import stat
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable

CONTAINER_NAME = "com.cuevastech.folio.motor"

_P = ctypes.c_void_p

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
_userenv = ctypes.WinDLL("userenv", use_last_error=True)
_ole32 = ctypes.WinDLL("ole32")


def _bind(lib: ctypes.WinDLL, name: str, args: list, result: object) -> None:
    fn = getattr(lib, name)
    fn.argtypes = args
    fn.restype = result


_bind(_userenv, "CreateAppContainerProfile", [wt.LPCWSTR, wt.LPCWSTR, wt.LPCWSTR, _P, wt.DWORD, ctypes.POINTER(_P)], ctypes.c_long)
_bind(_userenv, "DeriveAppContainerSidFromAppContainerName", [wt.LPCWSTR, ctypes.POINTER(_P)], ctypes.c_long)
_bind(_userenv, "DeleteAppContainerProfile", [wt.LPCWSTR], ctypes.c_long)
_bind(_userenv, "GetAppContainerFolderPath", [wt.LPCWSTR, ctypes.POINTER(_P)], ctypes.c_long)
_bind(_advapi32, "ConvertSidToStringSidW", [_P, ctypes.POINTER(_P)], wt.BOOL)
_bind(_advapi32, "FreeSid", [_P], _P)
_bind(_advapi32, "EqualSid", [_P, _P], wt.BOOL)
_bind(_advapi32, "OpenProcessToken", [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)], wt.BOOL)
_bind(_advapi32, "GetTokenInformation", [wt.HANDLE, ctypes.c_int, _P, wt.DWORD, ctypes.POINTER(wt.DWORD)], wt.BOOL)
_bind(_advapi32, "GetNamedSecurityInfoW", [wt.LPCWSTR, ctypes.c_int, wt.DWORD, _P, _P, ctypes.POINTER(_P), _P, ctypes.POINTER(_P)], wt.DWORD)
_bind(_advapi32, "SetNamedSecurityInfoW", [wt.LPWSTR, ctypes.c_int, wt.DWORD, _P, _P, _P, _P], wt.DWORD)
_bind(_advapi32, "SetEntriesInAclW", [wt.ULONG, _P, _P, ctypes.POINTER(_P)], wt.DWORD)
_bind(_advapi32, "GetAclInformation", [_P, _P, wt.DWORD, ctypes.c_int], wt.BOOL)
_bind(_advapi32, "GetAce", [_P, wt.DWORD, ctypes.POINTER(_P)], wt.BOOL)
_bind(_kernel32, "LocalFree", [_P], _P)
_bind(_kernel32, "CloseHandle", [wt.HANDLE], wt.BOOL)
_bind(_kernel32, "InitializeProcThreadAttributeList", [_P, wt.DWORD, wt.DWORD, ctypes.POINTER(ctypes.c_size_t)], wt.BOOL)
_bind(_kernel32, "UpdateProcThreadAttribute", [_P, wt.DWORD, ctypes.c_size_t, _P, ctypes.c_size_t, _P, _P], wt.BOOL)
_bind(_kernel32, "DeleteProcThreadAttributeList", [_P], None)
_bind(_kernel32, "CreateProcessW", [wt.LPCWSTR, wt.LPWSTR, _P, _P, wt.BOOL, wt.DWORD, _P, wt.LPCWSTR, _P, _P], wt.BOOL)
_bind(_kernel32, "ResumeThread", [wt.HANDLE], wt.DWORD)
_bind(_kernel32, "TerminateProcess", [wt.HANDLE, wt.UINT], wt.BOOL)
_bind(_kernel32, "WaitForSingleObject", [wt.HANDLE, wt.DWORD], wt.DWORD)
_bind(_kernel32, "GetExitCodeProcess", [wt.HANDLE, ctypes.POINTER(wt.DWORD)], wt.BOOL)
_bind(_kernel32, "CreateJobObjectW", [_P, wt.LPCWSTR], wt.HANDLE)
_bind(_kernel32, "SetInformationJobObject", [wt.HANDLE, ctypes.c_int, _P, wt.DWORD], wt.BOOL)
_bind(_kernel32, "AssignProcessToJobObject", [wt.HANDLE, wt.HANDLE], wt.BOOL)
_bind(_kernel32, "SetProcessMitigationPolicy", [ctypes.c_int, _P, ctypes.c_size_t], wt.BOOL)
_bind(_kernel32, "GetCurrentProcess", [], wt.HANDLE)
_bind(_kernel32, "OpenProcess", [wt.DWORD, wt.BOOL, wt.DWORD], wt.HANDLE)
_bind(_ole32, "CoTaskMemFree", [_P], None)

_ALREADY_EXISTS = 0x800700B7  # HRESULT_FROM_WIN32(ERROR_ALREADY_EXISTS)
_WAIT_TIMEOUT = 0x102
_STILL_ACTIVE = 259
_INFINITE = 0xFFFFFFFF

# Lectura y ejecucion ("RX" de icacls): lo justo para cargar el motor.
_READ_EXECUTE = 0x1200A9
_OBJECT_AND_CONTAINER_INHERIT = 0x3
_SE_FILE_OBJECT = 1
_DACL_SECURITY_INFORMATION = 0x4

_PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002
_PROC_THREAD_ATTRIBUTE_MITIGATION_POLICY = 0x00020007
_PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES = 0x00020009
_PROC_THREAD_ATTRIBUTE_CHILD_PROCESS_POLICY = 0x0002000E
_PROCESS_CREATION_CHILD_PROCESS_RESTRICTED = 0x01

_CREATE_SUSPENDED = 0x00000004
# Sin consola: con ella Windows crearia un proceso conhost.exe para el motor, y
# eso es justo lo que la politica de "sin procesos hijo" impide (fallaria al
# arrancar). La comunicacion va por las canalizaciones de la entrada/salida.
_DETACHED_PROCESS = 0x00000008
_CREATE_UNICODE_ENVIRONMENT = 0x00000400
_EXTENDED_STARTUPINFO_PRESENT = 0x00080000
_STARTF_USESTDHANDLES = 0x00000100

# Mitigaciones al crear el proceso (cada una ocupa 4 bits; 1 = siempre activa).
# Solo las comprobadas con el motor: la de llamadas graficas del nucleo
# (win32k) impide que Python arranque, y la que prohibe generar codigo (ACG)
# rompe la carga de ctypes, asi que esa se activa despues, ya en marcha.
_MITIGATIONS = (
    (1 << 8)  # reubicar siempre las bibliotecas (ASLR aunque no lo pidan)
    | (1 << 12)  # terminar el proceso si se corrompe el monticulo
    | (1 << 16)  # ASLR ascendente
    | (1 << 20)  # ASLR de alta entropia
    | (1 << 24)  # error inmediato ante un manejador no valido
    | (1 << 32)  # sin puntos de extension del sistema (AppInit_DLLs, ganchos)
    | (1 << 48)  # sin fuentes que no sean del sistema (no afecta a MuPDF)
    | (1 << 52)  # sin cargar bibliotecas desde recursos de red
    | (1 << 56)  # sin cargar bibliotecas de nivel de integridad bajo
)

_JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
_JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
_JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x00000400
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_UI_ALL = 0xFF  # escritorio, portapapeles, atomos globales, apagado, ajustes...
_JOB_BASIC_UI_RESTRICTIONS = 4
_JOB_EXTENDED_LIMIT_INFORMATION = 9

_TOKEN_QUERY = 0x0008
_TOKEN_IS_APP_CONTAINER = 29
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_PROCESS_DYNAMIC_CODE_POLICY = 2

_PIPE_BUFFER = 1024 * 1024


class SandboxError(Exception):
    """No se pudo preparar el aislamiento; ``step`` dice en que paso (sin datos del usuario)."""

    def __init__(self, step: str, code: int = 0) -> None:
        super().__init__(f"{step} ({code:#x})" if code else step)
        self.step = step
        self.code = code


class _STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wt.DWORD), ("lpReserved", wt.LPWSTR), ("lpDesktop", wt.LPWSTR), ("lpTitle", wt.LPWSTR),
        ("dwX", wt.DWORD), ("dwY", wt.DWORD), ("dwXSize", wt.DWORD), ("dwYSize", wt.DWORD),
        ("dwXCountChars", wt.DWORD), ("dwYCountChars", wt.DWORD), ("dwFillAttribute", wt.DWORD),
        ("dwFlags", wt.DWORD), ("wShowWindow", wt.WORD), ("cbReserved2", wt.WORD), ("lpReserved2", _P),
        ("hStdInput", wt.HANDLE), ("hStdOutput", wt.HANDLE), ("hStdError", wt.HANDLE),
    ]


class _STARTUPINFOEXW(ctypes.Structure):
    _fields_ = [("StartupInfo", _STARTUPINFOW), ("lpAttributeList", _P)]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", wt.HANDLE), ("hThread", wt.HANDLE), ("dwProcessId", wt.DWORD), ("dwThreadId", wt.DWORD)]


class _SECURITY_CAPABILITIES(ctypes.Structure):
    _fields_ = [("AppContainerSid", _P), ("Capabilities", _P), ("CapabilityCount", wt.DWORD), ("Reserved", wt.DWORD)]


class _ACL_SIZE_INFORMATION(ctypes.Structure):
    _fields_ = [("AceCount", wt.DWORD), ("AclBytesInUse", wt.DWORD), ("AclBytesFree", wt.DWORD)]


class _ACCESS_ALLOWED_ACE(ctypes.Structure):
    _fields_ = [("AceType", wt.BYTE), ("AceFlags", wt.BYTE), ("AceSize", wt.WORD), ("Mask", wt.DWORD), ("SidStart", wt.DWORD)]


class _TRUSTEE_W(ctypes.Structure):
    _fields_ = [
        ("pMultipleTrustee", _P), ("MultipleTrusteeOperation", ctypes.c_int), ("TrusteeForm", ctypes.c_int),
        ("TrusteeType", ctypes.c_int), ("ptstrName", _P),
    ]


class _EXPLICIT_ACCESS_W(ctypes.Structure):
    _fields_ = [("grfAccessPermissions", wt.DWORD), ("grfAccessMode", ctypes.c_int), ("grfInheritance", wt.DWORD), ("Trustee", _TRUSTEE_W)]


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in ("reads", "writes", "others", "read_bytes", "write_bytes", "other_bytes")]


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wt.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t), ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wt.DWORD), ("Affinity", ctypes.c_size_t), ("PriorityClass", wt.DWORD),
        ("SchedulingClass", wt.DWORD),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION), ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", wt.DWORD), ("dwMemoryLoad", wt.DWORD), ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


_bind(_kernel32, "GlobalMemoryStatusEx", [ctypes.POINTER(_MEMORYSTATUSEX)], wt.BOOL)


# --------------------------------------------------------------------- contenedor


@dataclass(frozen=True)
class Container:
    """Perfil de AppContainer del motor: su identidad (SID) y su carpeta privada."""

    sid: int  # puntero al SID; vive lo que el proceso (se libera en ``close``)
    sid_string: str
    folder: Path

    def close(self) -> None:
        _advapi32.FreeSid(self.sid)


def open_container(name: str = CONTAINER_NAME) -> Container:
    """Crea el perfil del contenedor la primera vez y lo reutiliza despues."""
    sid = _P()
    hr = _userenv.CreateAppContainerProfile(name, "Folio", "Motor de documentos de Folio", None, 0, ctypes.byref(sid))
    if (hr & 0xFFFFFFFF) == _ALREADY_EXISTS:
        hr = _userenv.DeriveAppContainerSidFromAppContainerName(name, ctypes.byref(sid))
    if hr < 0 or not sid.value:
        raise SandboxError("perfil", hr & 0xFFFFFFFF)
    try:
        text = _P()
        if not _advapi32.ConvertSidToStringSidW(sid, ctypes.byref(text)):
            raise SandboxError("sid", ctypes.get_last_error())
        try:
            sid_string = ctypes.wstring_at(text.value)
        finally:
            _kernel32.LocalFree(text)
        where = _P()
        hr = _userenv.GetAppContainerFolderPath(sid_string, ctypes.byref(where))
        if hr < 0 or not where.value:
            raise SandboxError("carpeta", hr & 0xFFFFFFFF)
        try:
            folder = Path(ctypes.wstring_at(where.value))
        finally:
            _ole32.CoTaskMemFree(where)
        if not folder.is_dir():
            raise SandboxError("carpeta")
    except BaseException:
        _advapi32.FreeSid(sid)
        raise
    return Container(sid.value, sid_string, folder)


def delete_container(name: str = CONTAINER_NAME) -> bool:
    """Borra el perfil y su carpeta (al desinstalar)."""
    return _userenv.DeleteAppContainerProfile(name) >= 0


def _has_read_access(path: Path, sid: int) -> bool:
    dacl = _P()
    descriptor = _P()
    error = _advapi32.GetNamedSecurityInfoW(
        str(path), _SE_FILE_OBJECT, _DACL_SECURITY_INFORMATION, None, None, ctypes.byref(dacl), None, ctypes.byref(descriptor)
    )
    if error:
        raise SandboxError("leer permisos", error)
    try:
        if not dacl.value:
            return True  # sin lista de control: acceso libre
        size = _ACL_SIZE_INFORMATION()
        if not _advapi32.GetAclInformation(dacl, ctypes.byref(size), ctypes.sizeof(size), 2):
            raise SandboxError("leer permisos", ctypes.get_last_error())
        for index in range(size.AceCount):
            ace = _P()
            if not _advapi32.GetAce(dacl, index, ctypes.byref(ace)):
                continue
            entry = _ACCESS_ALLOWED_ACE.from_address(ace.value)
            if entry.AceType != 0:  # solo entradas que conceden
                continue
            if entry.AceFlags & _OBJECT_AND_CONTAINER_INHERIT != _OBJECT_AND_CONTAINER_INHERIT:
                continue
            if entry.Mask & _READ_EXECUTE != _READ_EXECUTE:
                continue
            if _advapi32.EqualSid(ace.value + _ACCESS_ALLOWED_ACE.SidStart.offset, sid):
                return True
        return False
    finally:
        _kernel32.LocalFree(descriptor)


def ensure_read_access(path: Path, container: Container) -> bool:
    """Concede al contenedor leer (y ejecutar) ``path`` y lo que contiene.

    Solo lectura: el motor aislado no puede modificar sus propios archivos. Se
    comprueba antes para no recorrer la carpeta en cada arranque (Windows
    propaga el permiso a todo su contenido). Devuelve si hubo que concederlo.
    """
    if _has_read_access(path, container.sid):
        return False
    dacl = _P()
    descriptor = _P()
    error = _advapi32.GetNamedSecurityInfoW(
        str(path), _SE_FILE_OBJECT, _DACL_SECURITY_INFORMATION, None, None, ctypes.byref(dacl), None, ctypes.byref(descriptor)
    )
    if error:
        raise SandboxError("leer permisos", error)
    new_dacl = _P()
    try:
        access = _EXPLICIT_ACCESS_W()
        access.grfAccessPermissions = _READ_EXECUTE
        access.grfAccessMode = 1  # GRANT_ACCESS
        access.grfInheritance = _OBJECT_AND_CONTAINER_INHERIT
        access.Trustee.TrusteeForm = 0  # TRUSTEE_IS_SID
        access.Trustee.TrusteeType = 0  # TRUSTEE_IS_UNKNOWN
        access.Trustee.ptstrName = container.sid
        error = _advapi32.SetEntriesInAclW(1, ctypes.byref(access), dacl, ctypes.byref(new_dacl))
        if error:
            raise SandboxError("preparar permisos", error)
        error = _advapi32.SetNamedSecurityInfoW(str(path), _SE_FILE_OBJECT, _DACL_SECURITY_INFORMATION, None, None, new_dacl, None)
        if error:
            raise SandboxError("conceder permisos", error)
    finally:
        if new_dacl.value:
            _kernel32.LocalFree(new_dacl)
        _kernel32.LocalFree(descriptor)
    return True


# --------------------------------------------------------------- objeto de trabajo


def process_memory_limit() -> int:
    """Tope de memoria del proceso aislado: la mitad de la fisica, entre 1,5 y 8 GB."""
    status = _MEMORYSTATUSEX()
    status.dwLength = ctypes.sizeof(status)
    total = status.ullTotalPhys if _kernel32.GlobalMemoryStatusEx(ctypes.byref(status)) else 8 << 30
    return int(max(3 << 29, min(8 << 30, total // 2)))


class Job:
    """Objeto de trabajo: muere con quien lo crea, sin interfaz y con memoria acotada."""

    def __init__(self, *, memory_limit: int | None) -> None:
        handle = _kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise SandboxError("objeto de trabajo", ctypes.get_last_error())
        self.handle = handle
        try:
            limits = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            basic = limits.BasicLimitInformation
            basic.LimitFlags = (
                _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                | _JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION  # sin dialogo de error de Windows
                | _JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            )
            basic.ActiveProcessLimit = 1
            if memory_limit:
                basic.LimitFlags |= _JOB_OBJECT_LIMIT_PROCESS_MEMORY
                limits.ProcessMemoryLimit = memory_limit
            if not _kernel32.SetInformationJobObject(
                handle, _JOB_EXTENDED_LIMIT_INFORMATION, ctypes.byref(limits), ctypes.sizeof(limits)
            ):
                raise SandboxError("limites", ctypes.get_last_error())
            ui = wt.DWORD(_JOB_OBJECT_UI_ALL)
            if not _kernel32.SetInformationJobObject(handle, _JOB_BASIC_UI_RESTRICTIONS, ctypes.byref(ui), 4):
                raise SandboxError("limites de interfaz", ctypes.get_last_error())
        except BaseException:
            self.close()
            raise

    def assign(self, process: int) -> None:
        if not _kernel32.AssignProcessToJobObject(self.handle, process):
            raise SandboxError("asignar al objeto de trabajo", ctypes.get_last_error())

    def close(self) -> None:
        if self.handle:
            _kernel32.CloseHandle(self.handle)  # mata lo que quede dentro
            self.handle = 0


# ------------------------------------------------------------------------ procesos


class Child:
    """Proceso lanzado por ``spawn`` con sus tres canalizaciones."""

    def __init__(self, process: int, pid: int, stdin: BinaryIO, stdout: BinaryIO, stderr: BinaryIO) -> None:
        self._process = process
        self.pid = pid
        self.stdin = stdin
        self.stdout = stdout
        self.stderr = stderr
        self._lock = threading.Lock()

    def poll(self) -> int | None:
        with self._lock:
            if not self._process:
                return -1
            code = wt.DWORD()
            if not _kernel32.GetExitCodeProcess(self._process, ctypes.byref(code)):
                return -1
            return None if code.value == _STILL_ACTIVE and self._running() else int(code.value)

    def _running(self) -> bool:
        return _kernel32.WaitForSingleObject(self._process, 0) == _WAIT_TIMEOUT

    def wait(self, timeout: float | None = None) -> int | None:
        with self._lock:
            process = self._process
        if not process:
            return -1
        ms = _INFINITE if timeout is None else max(0, int(timeout * 1000))
        if _kernel32.WaitForSingleObject(process, ms) == _WAIT_TIMEOUT:
            return None
        return self.poll()

    def kill(self) -> None:
        with self._lock:
            if self._process and self._running():
                _kernel32.TerminateProcess(self._process, 1)

    @property
    def in_appcontainer(self) -> bool:
        with self._lock:
            return bool(self._process) and _token_is_appcontainer(self._process)

    def close(self) -> None:
        for stream in (self.stdin, self.stdout, self.stderr):
            try:
                stream.close()
            except OSError:
                pass
        with self._lock:
            if self._process:
                _kernel32.CloseHandle(self._process)
                self._process = 0


def _token_is_appcontainer(process: int) -> bool:
    token = wt.HANDLE()
    if not _advapi32.OpenProcessToken(process, _TOKEN_QUERY, ctypes.byref(token)):
        return False
    try:
        value, size = wt.DWORD(), wt.DWORD()
        if not _advapi32.GetTokenInformation(token, _TOKEN_IS_APP_CONTAINER, ctypes.byref(value), 4, ctypes.byref(size)):
            return False
        return bool(value.value)
    finally:
        _kernel32.CloseHandle(token)


def process_in_appcontainer(pid: int) -> bool | None:
    """Si el proceso ``pid`` corre en un AppContainer (None si no se puede consultar)."""
    process = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not process:
        return None
    try:
        return _token_is_appcontainer(process)
    finally:
        _kernel32.CloseHandle(process)


def _environment_block(env: dict[str, str]) -> ctypes.Array:
    for key, value in env.items():
        if not key or "=" in key or "\0" in key or "\0" in value:
            raise SandboxError("entorno")
    text = "".join(f"{k}={v}\0" for k, v in sorted(env.items(), key=lambda kv: kv[0].upper())) + "\0"
    return ctypes.create_unicode_buffer(text, len(text) + 1)


def spawn(
    cmd: list[str],
    *,
    cwd: str,
    env: dict[str, str],
    container: Container | None,
    job: Job | None,
    block_children: bool = True,
) -> Child:
    """Lanza ``cmd`` (sin interprete de ordenes) con las restricciones indicadas.

    El proceso nace suspendido y no arranca hasta estar dentro del objeto de
    trabajo: no hay ningun instante en que se ejecute sin sus limites. Solo
    hereda los tres extremos de sus canalizaciones (lista explicita), ningun
    otro manejador de este proceso.
    """
    in_r, in_w = _winapi.CreatePipe(None, _PIPE_BUFFER)
    out_r, out_w = _winapi.CreatePipe(None, _PIPE_BUFFER)
    err_r, err_w = _winapi.CreatePipe(None, 0)
    ours = [in_w, out_r, err_r]
    theirs = [in_r, out_w, err_w]
    attributes = None
    pi = _PROCESS_INFORMATION()
    try:
        for handle in theirs:
            os.set_handle_inheritable(handle, True)
        count = 3 + (1 if container is not None else 0)
        size = ctypes.c_size_t()
        _kernel32.InitializeProcThreadAttributeList(None, count, 0, ctypes.byref(size))
        attributes = ctypes.create_string_buffer(size.value)
        if not _kernel32.InitializeProcThreadAttributeList(attributes, count, 0, ctypes.byref(size)):
            attributes = None
            raise SandboxError("atributos", ctypes.get_last_error())
        handles = (wt.HANDLE * 3)(*theirs)
        mitigations = ctypes.c_ulonglong(_MITIGATIONS)
        child_policy = wt.DWORD(_PROCESS_CREATION_CHILD_PROCESS_RESTRICTED if block_children else 0)
        capabilities = _SECURITY_CAPABILITIES(container.sid if container else None, None, 0, 0)
        updates = [
            (_PROC_THREAD_ATTRIBUTE_HANDLE_LIST, handles, ctypes.sizeof(handles)),
            (_PROC_THREAD_ATTRIBUTE_MITIGATION_POLICY, ctypes.byref(mitigations), 8),
            (_PROC_THREAD_ATTRIBUTE_CHILD_PROCESS_POLICY, ctypes.byref(child_policy), 4),
        ]
        if container is not None:
            updates.append((_PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES, ctypes.byref(capabilities), ctypes.sizeof(capabilities)))
        for attribute, value, length in updates:
            if not _kernel32.UpdateProcThreadAttribute(attributes, 0, attribute, value, length, None, None):
                raise SandboxError("atributos", ctypes.get_last_error())

        startup = _STARTUPINFOEXW()
        startup.StartupInfo.cb = ctypes.sizeof(startup)
        startup.StartupInfo.dwFlags = _STARTF_USESTDHANDLES
        startup.StartupInfo.hStdInput, startup.StartupInfo.hStdOutput, startup.StartupInfo.hStdError = theirs
        startup.lpAttributeList = ctypes.addressof(attributes)
        block = _environment_block(env)
        # list2cmdline aplica las reglas de comillas de Windows: cada argumento
        # llega tal cual, sin que su contenido pueda partirse en otros.
        line = ctypes.create_unicode_buffer(subprocess.list2cmdline(cmd))
        flags = _EXTENDED_STARTUPINFO_PRESENT | _CREATE_UNICODE_ENVIRONMENT | _DETACHED_PROCESS | _CREATE_SUSPENDED
        if not _kernel32.CreateProcessW(cmd[0], line, None, None, True, flags, block, cwd, ctypes.byref(startup), ctypes.byref(pi)):
            raise SandboxError("crear proceso", ctypes.get_last_error())
        try:
            if job is not None:
                job.assign(pi.hProcess)
            if _kernel32.ResumeThread(pi.hThread) == 0xFFFFFFFF:
                raise SandboxError("arrancar proceso", ctypes.get_last_error())
        except BaseException:
            _kernel32.TerminateProcess(pi.hProcess, 1)
            _kernel32.CloseHandle(pi.hProcess)
            raise
        finally:
            _kernel32.CloseHandle(pi.hThread)
    except BaseException:
        for handle in ours:
            _kernel32.CloseHandle(handle)
        raise
    finally:
        if attributes is not None:
            _kernel32.DeleteProcThreadAttributeList(attributes)
        for handle in theirs:
            _kernel32.CloseHandle(handle)
    stdin = os.fdopen(msvcrt.open_osfhandle(in_w, os.O_WRONLY), "wb", buffering=0)
    stdout = os.fdopen(msvcrt.open_osfhandle(out_r, os.O_RDONLY), "rb", buffering=_PIPE_BUFFER)
    stderr = os.fdopen(msvcrt.open_osfhandle(err_r, os.O_RDONLY), "rb", buffering=0)
    return Child(pi.hProcess, pi.dwProcessId, stdin, stdout, stderr)


# ------------------------------------------------------------ carpetas de trabajo

RUN_MARKER = ".en-uso"
_RUN_NAME = re.compile(r"^[0-9a-f]{16}$")
# El contenedor es uno por usuario de Windows; dentro, cada perfil de Folio
# (carpeta de datos) tiene su propia carpeta para sus ejecuciones y lo que se
# puede recuperar: dos perfiles (o las pruebas) no se mezclan.
PROFILE_PREFIX = "perfil-"
_PROFILE_NAME = re.compile(r"^perfil-[0-9a-f]{16}$")


def profile_folder_name(main_workdir: Path) -> str:
    """Nombre de la carpeta del perfil dentro del contenedor (no revela la ruta del perfil)."""
    key = os.path.normcase(os.path.abspath(str(main_workdir))).encode("utf-8")
    return PROFILE_PREFIX + hashlib.sha256(key).hexdigest()[:16]


def other_profiles(own_base: Path) -> list[Path]:
    """Carpetas de los demas perfiles junto a ``own_base`` (solo carpetas de verdad)."""
    try:
        entries = list(os.scandir(own_base.parent))
    except OSError:
        return []
    out = []
    for entry in entries:
        path = Path(entry.path)
        if path != own_base and _PROFILE_NAME.match(entry.name) and plain_directory(path):
            out.append(path)
    return out


def plain_directory(path: Path) -> bool:
    """Es una carpeta de verdad: ni enlace simbolico ni union de directorio."""
    try:
        info = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISDIR(info.st_mode) and not info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT


def _abandoned(run: Path) -> bool:
    """La carpeta de otra ejecucion ya no la usa nadie (su marca no esta bloqueada)."""
    marker = run / RUN_MARKER
    try:
        info = os.lstat(marker)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    if not stat.S_ISREG(info.st_mode) or info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        return False
    try:
        with open(marker, "rb") as handle:
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                return False  # su intermediario sigue vivo
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            return True
    except OSError:
        return False


def clean_stale_runs(own: Path, before_delete: Callable[[Path], object] | None = None) -> int:
    """Borra las carpetas de trabajo de ejecuciones anteriores que no terminaron bien.

    Son copias de documentos del usuario y no deben quedarse en el disco. Lo
    hace el motor aislado al arrancar, con sus propios permisos: si alguna
    carpeta fuera en realidad un enlace hacia los archivos del usuario, el
    contenedor no podria borrar nada alli. Ademas no se siguen enlaces.
    ``before_delete`` puede apartar antes lo recuperable (ver recovery.py).
    """
    return _clean(own.parent, own, before_delete)


def clean_stale_dirs(base: Path, before_delete: Callable[[Path], object] | None = None) -> int:
    """Igual que ``clean_stale_runs`` para una carpeta en la que no corre este motor."""
    return _clean(base, None, before_delete)


def _clean(base: Path, own: Path | None, before_delete: Callable[[Path], object] | None) -> int:
    removed = 0
    try:
        entries = list(os.scandir(base))
    except OSError:
        return 0
    for entry in entries:
        path = Path(entry.path)
        if path == own or not _RUN_NAME.match(entry.name) or not plain_directory(path):
            continue
        if _abandoned(path):
            if before_delete is not None:
                before_delete(path)
            shutil.rmtree(path, ignore_errors=True)
            removed += 1
    return removed


# ------------------------------------------------------------------- en marcha


class _POLICY_FLAGS(ctypes.Structure):
    _fields_ = [("Flags", wt.DWORD)]


def lock_down_dynamic_code() -> bool:
    """Prohibe a este proceso crear o modificar codigo ejecutable (ACG).

    Un atacante que tome el control a traves de un fallo de memoria ya no puede
    escribir codigo propio y ejecutarlo. Es irreversible y debe llamarse
    despues de cargar ctypes (que lo necesita al importarse).
    """
    policy = _POLICY_FLAGS(1)  # ProhibitDynamicCode
    return bool(_kernel32.SetProcessMitigationPolicy(_PROCESS_DYNAMIC_CODE_POLICY, ctypes.byref(policy), ctypes.sizeof(policy)))
