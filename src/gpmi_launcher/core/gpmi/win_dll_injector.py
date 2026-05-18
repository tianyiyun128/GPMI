"""Windows-only suspended-process DLL injector for GPMI native runtimes."""

from __future__ import annotations

import ctypes as ct
import ctypes.wintypes as wt
import os
import subprocess
from pathlib import Path
from typing import Dict, Iterable, Mapping, Sequence


CREATE_SUSPENDED = 0x00000004
NORMAL_PRIORITY_CLASS = 0x00000020
CREATE_UNICODE_ENVIRONMENT = 0x00000400
MEM_COMMIT = 0x00001000
MEM_RESERVE = 0x00002000
PAGE_READWRITE = 0x04
WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102
INFINITE = 0xFFFFFFFF
STILL_ACTIVE = 259


class STARTUPINFOW(ct.Structure):
    _fields_ = [
        ("cb", wt.DWORD),
        ("lpReserved", wt.LPWSTR),
        ("lpDesktop", wt.LPWSTR),
        ("lpTitle", wt.LPWSTR),
        ("dwX", wt.DWORD),
        ("dwY", wt.DWORD),
        ("dwXSize", wt.DWORD),
        ("dwYSize", wt.DWORD),
        ("dwXCountChars", wt.DWORD),
        ("dwYCountChars", wt.DWORD),
        ("dwFillAttribute", wt.DWORD),
        ("dwFlags", wt.DWORD),
        ("wShowWindow", wt.WORD),
        ("cbReserved2", wt.WORD),
        ("lpReserved2", ct.POINTER(wt.BYTE)),
        ("hStdInput", wt.HANDLE),
        ("hStdOutput", wt.HANDLE),
        ("hStdError", wt.HANDLE),
    ]


class PROCESS_INFORMATION(ct.Structure):
    _fields_ = [
        ("hProcess", wt.HANDLE),
        ("hThread", wt.HANDLE),
        ("dwProcessId", wt.DWORD),
        ("dwThreadId", wt.DWORD),
    ]


class GPMIInjectionError(RuntimeError):
    pass


def _raise_last_error(action: str) -> None:
    error_code = ct.get_last_error()
    message = ct.FormatError(error_code)
    raise GPMIInjectionError(f"{action} failed with Windows error {error_code}: {message}")


def _kernel32():
    if os.name != "nt":
        raise GPMIInjectionError("GPMI native DLL injection is only available on Windows.")

    k32 = ct.WinDLL("kernel32", use_last_error=True)
    k32.CreateProcessW.argtypes = [
        wt.LPCWSTR, wt.LPWSTR, wt.LPVOID, wt.LPVOID, wt.BOOL, wt.DWORD,
        wt.LPVOID, wt.LPCWSTR, ct.POINTER(STARTUPINFOW), ct.POINTER(PROCESS_INFORMATION),
    ]
    k32.CreateProcessW.restype = wt.BOOL
    k32.VirtualAllocEx.argtypes = [wt.HANDLE, wt.LPVOID, ct.c_size_t, wt.DWORD, wt.DWORD]
    k32.VirtualAllocEx.restype = wt.LPVOID
    k32.VirtualFreeEx.argtypes = [wt.HANDLE, wt.LPVOID, ct.c_size_t, wt.DWORD]
    k32.VirtualFreeEx.restype = wt.BOOL
    k32.WriteProcessMemory.argtypes = [wt.HANDLE, wt.LPVOID, wt.LPCVOID, ct.c_size_t, ct.POINTER(ct.c_size_t)]
    k32.WriteProcessMemory.restype = wt.BOOL
    k32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
    k32.GetModuleHandleW.restype = wt.HMODULE
    k32.GetProcAddress.argtypes = [wt.HMODULE, ct.c_char_p]
    k32.GetProcAddress.restype = wt.LPVOID
    k32.CreateRemoteThread.argtypes = [wt.HANDLE, wt.LPVOID, ct.c_size_t, wt.LPVOID, wt.LPVOID, wt.DWORD, ct.POINTER(wt.DWORD)]
    k32.CreateRemoteThread.restype = wt.HANDLE
    k32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
    k32.WaitForSingleObject.restype = wt.DWORD
    k32.GetExitCodeThread.argtypes = [wt.HANDLE, ct.POINTER(wt.DWORD)]
    k32.GetExitCodeThread.restype = wt.BOOL
    k32.ResumeThread.argtypes = [wt.HANDLE]
    k32.ResumeThread.restype = wt.DWORD
    k32.TerminateProcess.argtypes = [wt.HANDLE, wt.UINT]
    k32.TerminateProcess.restype = wt.BOOL
    k32.CloseHandle.argtypes = [wt.HANDLE]
    k32.CloseHandle.restype = wt.BOOL
    return k32


def _command_line(exe_path: Path, args: Sequence[str]) -> str:
    return subprocess.list2cmdline([str(exe_path), *[str(arg) for arg in args]])


def _make_environment_block(overrides: Mapping[str, str] | None) -> ct.Array:
    env: Dict[str, str] = dict(os.environ)
    if overrides:
        for key, value in overrides.items():
            if value is None:
                env.pop(key, None)
            else:
                env[str(key)] = str(value)
    items = [f"{key}={value}" for key, value in sorted(env.items(), key=lambda kv: kv[0].upper())]
    return ct.create_unicode_buffer("\0".join(items) + "\0\0")


def _inject_load_library_w(k32, process_handle: wt.HANDLE, dll_path: Path, timeout_ms: int) -> None:
    dll_buffer = ct.create_unicode_buffer(str(dll_path.resolve()))
    byte_count = ct.sizeof(dll_buffer)
    remote_memory = k32.VirtualAllocEx(process_handle, None, byte_count, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE)
    if not remote_memory:
        _raise_last_error("VirtualAllocEx")

    remote_thread = None
    try:
        written = ct.c_size_t(0)
        if not k32.WriteProcessMemory(process_handle, remote_memory, dll_buffer, byte_count, ct.byref(written)):
            _raise_last_error("WriteProcessMemory")
        if written.value != byte_count:
            raise GPMIInjectionError(f"WriteProcessMemory wrote {written.value} of {byte_count} bytes")

        kernel32_module = k32.GetModuleHandleW("kernel32.dll")
        if not kernel32_module:
            _raise_last_error("GetModuleHandleW(kernel32.dll)")
        load_library_w = k32.GetProcAddress(kernel32_module, b"LoadLibraryW")
        if not load_library_w:
            _raise_last_error("GetProcAddress(LoadLibraryW)")

        thread_id = wt.DWORD(0)
        remote_thread = k32.CreateRemoteThread(process_handle, None, 0, load_library_w, remote_memory, 0, ct.byref(thread_id))
        if not remote_thread:
            _raise_last_error("CreateRemoteThread")

        wait_result = k32.WaitForSingleObject(remote_thread, timeout_ms if timeout_ms > 0 else INFINITE)
        if wait_result == WAIT_TIMEOUT:
            raise GPMIInjectionError(f"Timed out while injecting {dll_path.name}")
        if wait_result != WAIT_OBJECT_0:
            raise GPMIInjectionError(f"WaitForSingleObject returned unexpected code {wait_result}")

        exit_code = wt.DWORD(0)
        if not k32.GetExitCodeThread(remote_thread, ct.byref(exit_code)):
            _raise_last_error("GetExitCodeThread")
        if exit_code.value == 0 or exit_code.value == STILL_ACTIVE:
            raise GPMIInjectionError(f"LoadLibraryW failed for {dll_path}")
    finally:
        if remote_thread:
            k32.CloseHandle(remote_thread)
        k32.VirtualFreeEx(process_handle, remote_memory, 0, 0x8000)


def start_suspended_and_inject_dll(
    exe_path: Path,
    args: Iterable[str],
    work_dir: str | Path | None,
    dll_path: Path,
    timeout_seconds: int = 30,
    env_overrides: Mapping[str, str] | None = None,
) -> int:
    exe_path = Path(exe_path).resolve()
    dll_path = Path(dll_path).resolve()
    if not exe_path.is_file():
        raise GPMIInjectionError(f"Game executable not found: {exe_path}")
    if not dll_path.is_file():
        raise GPMIInjectionError(f"DLL to inject not found: {dll_path}")

    k32 = _kernel32()
    startup = STARTUPINFOW()
    startup.cb = ct.sizeof(STARTUPINFOW)
    proc_info = PROCESS_INFORMATION()
    cmd = ct.create_unicode_buffer(_command_line(exe_path, list(args)))
    cwd = str(Path(work_dir).resolve()) if work_dir else str(exe_path.parent)
    env_block = _make_environment_block(env_overrides)

    ok = k32.CreateProcessW(
        str(exe_path), cmd, None, None, False,
        CREATE_SUSPENDED | NORMAL_PRIORITY_CLASS | CREATE_UNICODE_ENVIRONMENT,
        env_block, cwd, ct.byref(startup), ct.byref(proc_info),
    )
    if not ok:
        _raise_last_error("CreateProcessW")

    resumed = False
    try:
        _inject_load_library_w(k32, proc_info.hProcess, dll_path, max(1, int(timeout_seconds)) * 1000)
        if k32.ResumeThread(proc_info.hThread) == 0xFFFFFFFF:
            _raise_last_error("ResumeThread")
        resumed = True
        return int(proc_info.dwProcessId)
    finally:
        if not resumed:
            k32.TerminateProcess(proc_info.hProcess, 1)
        k32.CloseHandle(proc_info.hThread)
        k32.CloseHandle(proc_info.hProcess)
