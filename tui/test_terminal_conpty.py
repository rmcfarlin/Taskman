"""Exercise Windows Terminal-style input through real, isolated ConPTY pipes.

The session follows Microsoft's CreatePseudoConsole/STARTUPINFOEX contract:
https://learn.microsoft.com/windows/console/creating-a-pseudoconsole-session
No existing terminal, process, user vault, or network remote is touched.
"""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from tui.test_terminal import ROOT, _git, prepare_probe


def run_conpty_probe(folder: Path, executable: Path | None = None) -> dict:
    """Run the source or optional frozen app in its own Windows pseudoconsole."""
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("GIT_CONFIG_", "TEXTUAL_"))
           and key not in {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
                          "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
                          "GIT_NAMESPACE", "GIT_PREFIX", "PYTHONPATH", "PYTHONHOME", "TASKMAN_VAULT"}}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_TERMINAL_PROMPT="0", GIT_ALLOW_PROTOCOL="file", PYTHONUTF8="1",
               TASKMAN_CONFIG_DIR=str(folder / "preferences"))
    command = [sys.executable, str(Path(__file__).resolve()), "--conpty-probe", str(folder.resolve())]
    if executable is not None:
        command += ["--executable", str(executable.resolve())]
    completed = subprocess.run(command, cwd=ROOT, env=env, capture_output=True,
                               text=True, encoding="utf-8", timeout=100,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    report_path = folder / "conpty-result.json"
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
    assert completed.returncode == 0, f"{report}\n{completed.stdout}\n{completed.stderr}"
    assert report.get("ok"), report
    return report


@pytest.mark.skipif(os.name != "nt" or not shutil.which("git"), reason="Windows ConPTY and Git required")
def test_conpty_win32_input_preserves_save_and_push_chords(tmp_path):
    report = run_conpty_probe(tmp_path / "conpty")
    assert report["driver"] == "CreatePseudoConsole Win32-input protocol"
    assert report["win32_input_requested"]
    assert report["ctrl_s_did_not_push"]
    assert report["editor_saved_without_push"]
    assert report["editor_ctrl_shift_s_did_not_push"]
    assert report["ctrl_shift_s_pushed"]
    assert report["push_success_notification"]
    assert report["app_exit"] == 0


def _conpty_probe(folder: Path, executable: Path | None) -> int:
    from ctypes import wintypes as wt

    class COORD(ctypes.Structure):
        _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]

    class STARTUPINFO(ctypes.Structure):
        _fields_ = [("cb", wt.DWORD), ("lpReserved", wt.LPWSTR), ("lpDesktop", wt.LPWSTR),
                    ("lpTitle", wt.LPWSTR), ("dwX", wt.DWORD), ("dwY", wt.DWORD),
                    ("dwXSize", wt.DWORD), ("dwYSize", wt.DWORD), ("dwXCountChars", wt.DWORD),
                    ("dwYCountChars", wt.DWORD), ("dwFillAttribute", wt.DWORD), ("dwFlags", wt.DWORD),
                    ("wShowWindow", wt.WORD), ("cbReserved2", wt.WORD),
                    ("lpReserved2", ctypes.POINTER(wt.BYTE)), ("hStdInput", wt.HANDLE),
                    ("hStdOutput", wt.HANDLE), ("hStdError", wt.HANDLE)]

    class STARTUPINFOEX(ctypes.Structure):
        _fields_ = [("StartupInfo", STARTUPINFO), ("lpAttributeList", ctypes.c_void_p)]

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [("hProcess", wt.HANDLE), ("hThread", wt.HANDLE),
                    ("dwProcessId", wt.DWORD), ("dwThreadId", wt.DWORD)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "CreatePipe": ([ctypes.POINTER(wt.HANDLE), ctypes.POINTER(wt.HANDLE), ctypes.c_void_p, wt.DWORD], wt.BOOL),
        "CreatePseudoConsole": ([COORD, wt.HANDLE, wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)], ctypes.c_long),
        "ClosePseudoConsole": ([wt.HANDLE], None),
        "CloseHandle": ([wt.HANDLE], wt.BOOL),
        "InitializeProcThreadAttributeList": ([ctypes.c_void_p, wt.DWORD, wt.DWORD, ctypes.POINTER(ctypes.c_size_t)], wt.BOOL),
        "UpdateProcThreadAttribute": ([ctypes.c_void_p, wt.DWORD, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t,
                                       ctypes.c_void_p, ctypes.c_void_p], wt.BOOL),
        "DeleteProcThreadAttributeList": ([ctypes.c_void_p], None),
        "CreateProcessW": ([wt.LPCWSTR, wt.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, wt.BOOL, wt.DWORD,
                            ctypes.c_void_p, wt.LPCWSTR, ctypes.POINTER(STARTUPINFOEX),
                            ctypes.POINTER(PROCESS_INFORMATION)], wt.BOOL),
        "ReadFile": ([wt.HANDLE, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.c_void_p], wt.BOOL),
        "WriteFile": ([wt.HANDLE, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.c_void_p], wt.BOOL),
        "GetExitCodeProcess": ([wt.HANDLE, ctypes.POINTER(wt.DWORD)], wt.BOOL),
        "WaitForSingleObject": ([wt.HANDLE, wt.DWORD], wt.DWORD),
        "TerminateProcess": ([wt.HANDLE, wt.UINT], wt.BOOL),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(kernel, name)
        function.argtypes, function.restype = arguments, result

    def checked(value):
        if not value:
            raise ctypes.WinError(ctypes.get_last_error())
        return value

    report = {"ok": False, "driver": "CreatePseudoConsole Win32-input protocol"}
    folder.mkdir(parents=True, exist_ok=True)
    input_read, input_write, output_read, output_write, console = (wt.HANDLE() for _ in range(5))
    process = PROCESS_INFORMATION()
    attributes = None
    reader = None
    output = bytearray()
    output_lock = threading.Lock()
    reader_errors = []

    def snapshot():
        with output_lock:
            return bytes(output)

    def plain(since=0):
        text = snapshot()[since:].decode("utf-8", errors="replace")
        return re.sub(r"\x1b\][^\x07]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]", "", text)

    def poll():
        if not process.hProcess:
            return None
        code = wt.DWORD()
        checked(kernel.GetExitCodeProcess(process.hProcess, ctypes.byref(code)))
        return None if code.value == 259 else code.value

    def wait_until(predicate, description, timeout=25):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if poll() is not None:
                raise AssertionError(f"App exited early ({poll()}): {description}; output={plain()[-1800:]!r}")
            if reader_errors:
                raise AssertionError(reader_errors)
            if predicate():
                return
            time.sleep(0.05)
        raise AssertionError(f"Timed out: {description}; output={plain()[-1800:]!r}")

    try:
        vault, remote = prepare_probe(folder)
        original_head = _git(vault, "rev-parse", "HEAD")
        checked(kernel.CreatePipe(ctypes.byref(input_read), ctypes.byref(input_write), None, 0))
        checked(kernel.CreatePipe(ctypes.byref(output_read), ctypes.byref(output_write), None, 0))
        status = kernel.CreatePseudoConsole(COORD(120, 38), input_read, output_write, 0, ctypes.byref(console))
        assert status >= 0, f"CreatePseudoConsole HRESULT {status:#x}"
        size = ctypes.c_size_t()
        kernel.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
        attributes = ctypes.create_string_buffer(size.value)
        checked(kernel.InitializeProcThreadAttributeList(attributes, 1, 0, ctypes.byref(size)))
        checked(kernel.UpdateProcThreadAttribute(attributes, 0, 0x00020016, console,
                                                ctypes.sizeof(console), None, None))
        startup = STARTUPINFOEX()
        startup.StartupInfo.cb = ctypes.sizeof(startup)
        # Explicit null standard handles stop CreateProcess from copying this
        # harness's redirected pipes instead of attaching ConPTY's console I/O.
        startup.StartupInfo.dwFlags = 0x100 | 1  # USESTDHANDLES | USESHOWWINDOW
        startup.StartupInfo.wShowWindow = 0
        startup.lpAttributeList = ctypes.cast(attributes, ctypes.c_void_p)
        command = ([str(executable)] if executable else [sys.executable, "-m", "tui"])
        command += ["--vault", str(vault)]
        environment = ctypes.create_unicode_buffer("\0".join(f"{key}={value}" for key, value in sorted(
            os.environ.items(), key=lambda item: item[0].casefold())) + "\0\0")

        def drain():
            buffer, count = ctypes.create_string_buffer(65536), wt.DWORD()
            while True:
                if not kernel.ReadFile(output_read, buffer, len(buffer), ctypes.byref(count), None):
                    error = ctypes.get_last_error()
                    if error not in (6, 109, 995):
                        reader_errors.append(f"ReadFile failed: {error}")
                    break
                if not count.value:
                    break
                with output_lock:
                    output.extend(buffer.raw[:count.value])

        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        checked(kernel.CreateProcessW(None, ctypes.create_unicode_buffer(subprocess.list2cmdline(command)),
                                      None, None, False, 0x00080000 | 0x00000400,
                                      ctypes.cast(environment, ctypes.c_void_p), str(ROOT),
                                      ctypes.byref(startup), ctypes.byref(process)))
        for handle in (input_read, output_write):
            kernel.CloseHandle(handle)
            handle.value = None

        def send(data):
            count = wt.DWORD()
            checked(kernel.WriteFile(input_write, data, len(data), ctypes.byref(count), None))
            assert count.value == len(data)

        def press(character, modifiers=0, virtual_key=None):
            virtual_key = virtual_key if virtual_key is not None else ord(character.upper()) if character.isalpha() else ord(character)
            scan = 31 if virtual_key == 83 else 0
            send("".join(f"\x1b[{virtual_key};{scan};{ord(character)};{down};{modifiers};1_"
                         for down in (1, 0)).encode("ascii"))

        wait_until(lambda: "Raw keyboard test" in plain(), "app ready")
        wait_until(lambda: b"\x1b[?9001h" in snapshot(), "Win32 input mode request")
        report["win32_input_requested"] = True
        offset = len(snapshot())
        press("\x13", 0x08, 0x53)
        wait_until(lambda: "Rescanned vault" in plain(offset), "Ctrl+S acknowledgement")
        assert not _git(remote, "for-each-ref", "--format=%(refname)", "refs/heads")
        assert _git(vault, "rev-parse", "HEAD") == original_head
        report["ctrl_s_did_not_push"] = True
        offset = len(snapshot())
        press("n")
        wait_until(lambda: "Ctrl+S = save" in plain(offset), "note editor")
        for character in "Saved through real ConPTY input":
            press(character)
        offset = len(snapshot())
        press("\x13", 0x08, 0x53)
        path = vault / "Tasks/Inbox.md"
        wait_until(lambda: "Saved through real ConPTY input" in path.read_text(encoding="utf-8"), "editor persisted")
        wait_until(lambda: "Note saved" in plain(offset), "editor closed after Ctrl+S")
        assert not _git(remote, "for-each-ref", "--format=%(refname)", "refs/heads")
        assert _git(vault, "rev-parse", "HEAD") == original_head
        report["editor_saved_without_push"] = True
        offset = len(snapshot())
        press("n")
        wait_until(lambda: "Ctrl+S = save" in plain(offset), "note editor reopened")
        offset = len(snapshot())
        press("\x13", 0x18, 0x53)
        wait_until(lambda: "Note unchanged" in plain(offset), "editor closed after Ctrl+Shift+S")
        assert not _git(remote, "for-each-ref", "--format=%(refname)", "refs/heads")
        assert _git(vault, "rev-parse", "HEAD") == original_head
        report["editor_ctrl_shift_s_did_not_push"] = True
        offset = len(snapshot())
        press("\x13", 0x18, 0x53)
        wait_until(lambda: bool(_git(remote, "for-each-ref", "--format=%(refname)", "refs/heads/main")), "global Ctrl+Shift+S push", 35)
        pushed = _git(remote, "rev-parse", "refs/heads/main")
        assert pushed != original_head and pushed == _git(vault, "rev-parse", "HEAD")
        assert "Saved through real ConPTY input" in _git(remote, "show", f"{pushed}:Tasks/Inbox.md")
        report["ctrl_shift_s_pushed"] = True
        wait_until(lambda: "Pushed to Git remote (commit " in plain(offset), "push success notification")
        report["push_success_notification"] = True
        report["commit"] = pushed
        press("q")
        assert kernel.WaitForSingleObject(process.hProcess, 15000) == 0, "App did not exit"
        report["app_exit"] = poll()
        assert report["app_exit"] == 0
        report["ok"] = True
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
        report["output_tail"] = plain()[-2400:]
    finally:
        if process.hProcess and poll() is None:
            kernel.TerminateProcess(process.hProcess, 1)
            kernel.WaitForSingleObject(process.hProcess, 10000)
        if console:
            kernel.ClosePseudoConsole(console)
        for handle in (input_read, input_write, output_write):
            if handle:
                kernel.CloseHandle(handle)
        if reader:
            reader.join(timeout=5)
        if output_read:
            kernel.CloseHandle(output_read)
        for handle in (process.hThread, process.hProcess):
            if handle:
                kernel.CloseHandle(handle)
        if attributes is not None:
            kernel.DeleteProcThreadAttributeList(attributes)
        (folder / "conpty-output.log").write_bytes(snapshot())
        (folder / "conpty-result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1


if __name__ == "__main__" and "--conpty-probe" in sys.argv:
    destination = Path(sys.argv[sys.argv.index("--conpty-probe") + 1]).resolve()
    binary = Path(sys.argv[sys.argv.index("--executable") + 1]) if "--executable" in sys.argv else None
    raise SystemExit(_conpty_probe(destination, binary))
