"""
Smart App Control compatibility shim for pywin32.
=================================================

On some Windows 11 machines, **Smart App Control** (Enforce mode) blocks the
unsigned pywin32 DLL (`pywintypes313.dll`) from loading. That breaks
`import pywintypes`, which in turn breaks `portalocker`, which `crewai` imports
on startup — so the whole backend fails to import with:

    ImportError: DLL load failed while importing pywintypes:
                 An Application Control policy has blocked this file.

This module provides the *tiny* subset of pywin32 that portalocker's Windows
file-locking backend actually uses, implemented purely with `ctypes` calling
into `kernel32.dll` (which is Microsoft-signed and therefore allowed by Smart
App Control). It does NOT disable any security feature.

Importing this module is a **no-op on normal machines**: if the real pywin32
imports successfully, the shim does nothing. It only registers fake modules in
`sys.modules` when the real pywin32 cannot be loaded.
"""

import sys


def _install() -> None:
    if sys.platform != "win32":
        return

    # If the real pywin32 works, do nothing.
    try:
        import pywintypes  # noqa: F401
        return
    except ImportError:
        pass

    import types
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    LOCKFILE_FAIL_IMMEDIATELY = 0x00000001
    LOCKFILE_EXCLUSIVE_LOCK = 0x00000002
    ERROR_LOCK_VIOLATION = 33
    ERROR_NOT_LOCKED = 158

    class OVERLAPPED(ctypes.Structure):
        # Layout matches the Win32 OVERLAPPED struct closely enough for a
        # zero-initialised, whole-file lock (Offset/OffsetHigh = 0).
        _fields_ = [
            ("Internal", ctypes.c_void_p),
            ("InternalHigh", ctypes.c_void_p),
            ("Offset", wintypes.DWORD),
            ("OffsetHigh", wintypes.DWORD),
            ("hEvent", wintypes.HANDLE),
        ]

    class error(Exception):
        """Mimics pywintypes.error: carries winerror / funcname / strerror."""

        def __init__(self, winerror=0, funcname="", strerror=""):
            self.winerror = winerror
            self.funcname = funcname
            self.strerror = strerror
            super().__init__(winerror, funcname, strerror)

    kernel32.LockFileEx.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
        wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
    ]
    kernel32.LockFileEx.restype = wintypes.BOOL
    kernel32.UnlockFileEx.argtypes = [
        wintypes.HANDLE, wintypes.DWORD,
        wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
    ]
    kernel32.UnlockFileEx.restype = wintypes.BOOL

    def _u32(value: int) -> int:
        return ctypes.c_uint32(value & 0xFFFFFFFF).value

    def LockFileEx(hFile, flags, nbytes_low, nbytes_high, overlapped):
        ok = kernel32.LockFileEx(
            wintypes.HANDLE(hFile), wintypes.DWORD(flags), wintypes.DWORD(0),
            wintypes.DWORD(_u32(nbytes_low)), wintypes.DWORD(_u32(nbytes_high)),
            ctypes.byref(overlapped),
        )
        if not ok:
            err = ctypes.get_last_error()
            raise error(err, "LockFileEx", ctypes.FormatError(err))

    def UnlockFileEx(hFile, nbytes_low, nbytes_high, overlapped):
        ok = kernel32.UnlockFileEx(
            wintypes.HANDLE(hFile), wintypes.DWORD(0),
            wintypes.DWORD(_u32(nbytes_low)), wintypes.DWORD(_u32(nbytes_high)),
            ctypes.byref(overlapped),
        )
        if not ok:
            err = ctypes.get_last_error()
            raise error(err, "UnlockFileEx", ctypes.FormatError(err))

    def __import_pywin32_system_module__(modname, globs):
        # Real pywin32 extension modules (pythoncom, win32api, ...) call this to
        # load their blocked DLLs. Raise ImportError so callers that guard the
        # import (e.g. appdirs) fall back to their non-pywin32 code path —
        # exactly as if pywin32 were not installed at all.
        raise ImportError(
            f"pywin32 extension '{modname}' is unavailable "
            "(DLL blocked by Smart App Control); using ctypes shim instead."
        )

    pywintypes_mod = types.ModuleType("pywintypes")
    pywintypes_mod.error = error
    pywintypes_mod.OVERLAPPED = OVERLAPPED
    pywintypes_mod.__import_pywin32_system_module__ = __import_pywin32_system_module__

    win32con_mod = types.ModuleType("win32con")
    win32con_mod.LOCKFILE_FAIL_IMMEDIATELY = LOCKFILE_FAIL_IMMEDIATELY
    win32con_mod.LOCKFILE_EXCLUSIVE_LOCK = LOCKFILE_EXCLUSIVE_LOCK

    win32file_mod = types.ModuleType("win32file")
    win32file_mod.LockFileEx = LockFileEx
    win32file_mod.UnlockFileEx = UnlockFileEx
    win32file_mod.OVERLAPPED = OVERLAPPED
    win32file_mod.error = error

    winerror_mod = types.ModuleType("winerror")
    winerror_mod.ERROR_LOCK_VIOLATION = ERROR_LOCK_VIOLATION
    winerror_mod.ERROR_NOT_LOCKED = ERROR_NOT_LOCKED

    for name, mod in (
        ("pywintypes", pywintypes_mod),
        ("win32con", win32con_mod),
        ("win32file", win32file_mod),
        ("winerror", winerror_mod),
    ):
        sys.modules[name] = mod

    try:
        print("[OK] pywin32 shim active (Smart App Control workaround via ctypes/kernel32)")
    except Exception:
        pass


_install()
