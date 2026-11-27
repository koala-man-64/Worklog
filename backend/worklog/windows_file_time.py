"""Read a Windows file's native metadata change time without following reparse points."""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from functools import lru_cache

_FILE_READ_ATTRIBUTES = 0x0080
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_FILE_SHARE_DELETE = 0x00000004
_OPEN_EXISTING = 3
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_FILE_TYPE_DISK = 1
_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_FILE_BASIC_INFO_CLASS = 0


class _FileBasicInfo(ctypes.Structure):
    _fields_ = [
        ("CreationTime", ctypes.c_longlong),
        ("LastAccessTime", ctypes.c_longlong),
        ("LastWriteTime", ctypes.c_longlong),
        ("ChangeTime", ctypes.c_longlong),
        ("FileAttributes", wintypes.DWORD),
    ]


@lru_cache(maxsize=1)
def _win32_api():
    """Load and type the Win32 entry points once for repeated stamp checks."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE

    get_file_type = kernel32.GetFileType
    get_file_type.argtypes = (wintypes.HANDLE,)
    get_file_type.restype = wintypes.DWORD

    get_information = kernel32.GetFileInformationByHandleEx
    get_information.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    get_information.restype = wintypes.BOOL

    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    return kernel32, create_file, get_file_type, get_information, close_handle


def change_time_ns(path: str | os.PathLike[str]) -> int | None:
    """Return FILE_BASIC_INFO.ChangeTime in nanoseconds, or None if unavailable.

    The handle requests only FILE_READ_ATTRIBUTES, permits concurrent readers, writers,
    and deletion, and opens the final component without following a reparse point.
    Reparse points, non-disk handles, and nonpositive timestamps fail closed.
    """
    if os.name != "nt":
        return None
    try:
        native_path = os.fspath(path)
        if (
            not isinstance(native_path, str)
            or "\0" in native_path
            or len(native_path) < 3
            or not native_path[0].isalpha()
            or native_path[1] != ":"
            or native_path[2] not in "\\/"
        ):
            return None
        _, create_file, get_file_type, get_information, close_handle = _win32_api()
        handle = create_file(
            native_path,
            _FILE_READ_ATTRIBUTES,
            _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE,
            None,
            _OPEN_EXISTING,
            _FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OPEN_REPARSE_POINT,
            None,
        )
    except (AttributeError, OSError, TypeError, ValueError):
        return None

    if handle is None or handle == ctypes.c_void_p(-1).value:
        return None

    change_ns = None
    closed = False
    try:
        if get_file_type(handle) == _FILE_TYPE_DISK:
            basic = _FileBasicInfo()
            if get_information(
                handle,
                _FILE_BASIC_INFO_CLASS,
                ctypes.byref(basic),
                ctypes.sizeof(basic),
            ):
                attributes = int(basic.FileAttributes)
                if (
                    not attributes & _FILE_ATTRIBUTE_REPARSE_POINT
                    and basic.ChangeTime > 0
                ):
                    # FILETIME values count 100-nanosecond intervals since 1601-01-01 UTC.
                    change_ns = int(basic.ChangeTime) * 100
    except (AttributeError, OSError, TypeError, ValueError, OverflowError):
        change_ns = None
    finally:
        try:
            closed = bool(close_handle(handle))
        except (AttributeError, OSError, TypeError, ValueError):
            closed = False

    return change_ns if closed else None
