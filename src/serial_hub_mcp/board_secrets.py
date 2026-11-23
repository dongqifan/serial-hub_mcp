"""Current-user DPAPI for GUI profile passwords; no plaintext storage fallback."""
from __future__ import annotations

import base64
import ctypes
import sys
from ctypes import wintypes


class _Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.c_void_p)]


# Version and provider GUID in a serialized CryptProtectData blob. Check the
# envelope without having to decrypt every board when listing/saving profiles.
_HEADER = bytes.fromhex('01000000 d08c9ddf0115d1118c7a00c04fc297eb')


def _decode(cipher: str) -> bytes:
    try:
        data = base64.b64decode(cipher, validate=True)
    except (TypeError, ValueError):
        data = b''
    if not data.startswith(_HEADER) or len(data) <= len(_HEADER):
        raise ValueError('密码不是有效的 Windows 加密数据。')
    return data


def validate_secret(cipher: str) -> None:
    if not isinstance(cipher, str):
        raise ValueError('密码存储字段无效。')
    if cipher:
        _decode(cipher)


def _dpapi(data: bytes, *, encrypt: bool) -> bytes:
    if sys.platform != 'win32':
        raise ValueError('密码保护仅支持 Windows 当前用户；未保存明文密码。')
    # Specify pointer-sized argument and return types (especially LocalFree on Win64).
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    call = crypt.CryptProtectData if encrypt else crypt.CryptUnprotectData
    ptr = ctypes.POINTER(_Blob)
    call.argtypes = [ptr, ctypes.c_void_p, ptr, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ptr]
    call.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    buf = ctypes.create_string_buffer(data)
    source, result = _Blob(len(data), ctypes.addressof(buf)), _Blob()
    try:
        # CRYPTPROTECT_UI_FORBIDDEN; no LOCAL_MACHINE flag, no UI on decrypt errors.
        if not call(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
            raise ValueError('Windows 无法保护密码。' if encrypt else '当前 Windows 用户无法解密此密码。')
        return ctypes.string_at(result.data, result.size)
    finally:
        ctypes.memset(ctypes.addressof(buf), 0, len(buf))
        if result.data:
            ctypes.memset(result.data, 0, result.size)
            kernel.LocalFree(result.data)


def protect(password: str) -> str:
    if not isinstance(password, str):
        raise ValueError('密码必须为文字。')
    if not password:
        return ''
    try:
        raw = password.encode('utf-8')
    except UnicodeError:
        raise ValueError('密码含有无效字符。') from None
    return base64.b64encode(_dpapi(raw, encrypt=True)).decode('ascii')


def reveal(cipher: str) -> str:
    if cipher == '':
        return ''
    try:
        return _dpapi(_decode(cipher), encrypt=False).decode('utf-8')
    except UnicodeError:
        raise ValueError('无法读取密码。') from None
