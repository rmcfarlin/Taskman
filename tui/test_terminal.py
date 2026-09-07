"""Native Windows modifier regressions, including an isolated real console."""
from __future__ import annotations

import ctypes
import datetime as dt
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from textual._xterm_parser import XTermParser
from textual.events import Key

from tui import terminal


ROOT = Path(__file__).resolve().parent.parent


def key_record(*, modifiers=0x18, character="\x13", virtual_key=0x53, down=True):
    if os.name == "nt":
        from textual.drivers import win32
        record = win32.KEY_EVENT_RECORD()
        record.bKeyDown = down
        record.wRepeatCount = 1
        record.wVirtualKeyCode = virtual_key
        record.wVirtualScanCode = 0x1F
        record.uChar.UnicodeChar = character
        record.dwControlKeyState = modifiers
        return record
    return SimpleNamespace(bKeyDown=down, wVirtualKeyCode=virtual_key,
                           uChar=SimpleNamespace(UnicodeChar=character), dwControlKeyState=modifiers)


@pytest.mark.parametrize("control", [0x04, 0x08, 0x0C])
@pytest.mark.parametrize("locks", [0, 0x20, 0x80])
def test_native_shift_ctrl_s_retains_both_modifiers(control, locks):
    record = key_record(modifiers=control | 0x10 | locks)
    # This is the actual character-only conversion in the upstream monitor.
    old = list(XTermParser().feed(record.uChar.UnicodeChar))
    assert [event.key for event in old] == ["ctrl+s"]
    corrected = list(XTermParser().feed(terminal.console_key_text(record)))
    assert [event.key for event in corrected] == ["ctrl+shift+s"]


@pytest.mark.parametrize("record, expected", [
    (key_record(modifiers=0x08), "\x13"),
    (key_record(modifiers=0x04), "\x13"),
    (key_record(modifiers=0x10, character="S"), "S"),
    (key_record(modifiers=0, character="s"), "s"),
    (key_record(modifiers=0x19), "\x13"),  # AltGr/Alt must not publish.
    (key_record(down=False), ""),
    (key_record(virtual_key=0), ""),
    (key_record(virtual_key=0, modifiers=0, character="\x1b"), "\x1b"),
])
def test_unrelated_console_input_is_unchanged(record, expected):
    assert terminal.console_key_text(record) == expected


@pytest.mark.skipif(os.name != "nt", reason="Native Windows console structures")
def test_raw_input_batch_preserves_keyup_vt_and_unicode():
    from textual.drivers import win32

    events = []
    monitor = terminal.WindowsEventMonitor(None, None, threading.Event(), events.append)
    records = []
    for key in [key_record(), key_record(down=False), key_record(modifiers=0x08)]:
        record = win32.INPUT_RECORD()
        record.EventType = 1
        record.Event.KeyEvent = key
        records.append(record)
    for char in "\x1b[115;6u\ud83d\ude00":
        record = win32.INPUT_RECORD()
        record.EventType = 1
        record.Event.KeyEvent = key_record(virtual_key=0, modifiers=0, character=char)
        records.append(record)
    monitor.dispatch_records(records, XTermParser())
    assert [event.key for event in events if isinstance(event, Key)] == [
        "ctrl+shift+s", "ctrl+s", "ctrl+shift+s", "grinning_face",
    ]


@pytest.mark.skipif(os.name != "nt", reason="Native Windows console structures")
@pytest.mark.parametrize("modifiers, expected", [(0x08, "ctrl+s"), (0x18, "ctrl+shift+s")])
def test_vt_flattened_keydown_uses_matching_native_release(modifiers, expected):
    from textual.drivers import win32

    events = []
    monitor = terminal.WindowsEventMonitor(None, None, threading.Event(), events.append)
    parser = XTermParser()
    down = win32.INPUT_RECORD()
    down.EventType = 1
    down.Event.KeyEvent = key_record(virtual_key=0, modifiers=0)
    monitor.dispatch_records([down], parser)
    assert events == []
    up = win32.INPUT_RECORD()
    up.EventType = 1
    up.Event.KeyEvent = key_record(modifiers=modifiers, down=False)
    monitor.dispatch_records([up], parser)
    assert [event.key for event in events] == [expected]


@pytest.mark.skipif(os.name != "nt", reason="Native Windows console structures")
def test_repeated_chords_without_modifier_records_do_not_reuse_stale_state():
    from textual.drivers import win32

    events = []
    monitor = terminal.WindowsEventMonitor(None, None, threading.Event(), events.append)
    parser = XTermParser()
    for modifiers in (0x08, 0x18, 0x08):
        for key in (key_record(virtual_key=0, modifiers=0),
                    key_record(modifiers=modifiers, down=False)):
            record = win32.INPUT_RECORD()
            record.EventType = 1
            record.Event.KeyEvent = key
            monitor.dispatch_records([record], parser)
    assert [event.key for event in events] == ["ctrl+s", "ctrl+shift+s", "ctrl+s"]


@pytest.mark.skipif(os.name != "nt", reason="Native Windows console structures")
@pytest.mark.parametrize("shifted", [False, True])
def test_native_modifier_snapshot_dispatches_on_keydown_without_timeout(shifted):
    from textual.drivers import win32

    events = []
    monitor = terminal.WindowsEventMonitor(None, None, threading.Event(), events.append)
    parser = XTermParser()
    keys = [key_record(virtual_key=0x11, character="\x00", modifiers=0x08)]
    if shifted:
        keys.append(key_record(virtual_key=0x10, character="\x00", modifiers=0x18))
    keys.append(key_record(virtual_key=0, modifiers=0))
    for key in keys:
        record = win32.INPUT_RECORD()
        record.EventType = 1
        record.Event.KeyEvent = key
        monitor.dispatch_records([record], parser)
    assert [event.key for event in events] == ["ctrl+shift+s" if shifted else "ctrl+s"]
    monitor._pending_save_deadline = 0
    monitor.dispatch_records([], parser)
    assert len(events) == 1


@pytest.mark.skipif(os.name != "nt", reason="Native Windows console structures")
@pytest.mark.parametrize("presses_reported", [False, True])
@pytest.mark.parametrize("release_order", ["s_first", "shift_first", "control_first"])
def test_save_modifiers_survive_release_order_and_split_batches(presses_reported, release_order):
    from textual.drivers import win32

    events = []
    monitor = terminal.WindowsEventMonitor(None, None, threading.Event(), events.append)
    parser = XTermParser()

    def dispatch(key):
        record = win32.INPUT_RECORD()
        record.EventType = 1
        record.Event.KeyEvent = key
        monitor.dispatch_records([record], parser)

    if presses_reported:
        dispatch(key_record(virtual_key=0x11, character="\x00", modifiers=0x08))
        dispatch(key_record(virtual_key=0x10, character="\x00", modifiers=0x18))
    dispatch(key_record(virtual_key=0, modifiers=0))
    assert bool(events) is presses_reported
    if release_order == "shift_first":
        dispatch(key_record(virtual_key=0x10, character="\x00", modifiers=0x08, down=False))
        dispatch(key_record(modifiers=0x08, down=False))
    elif release_order == "control_first":
        dispatch(key_record(virtual_key=0x11, character="\x00", modifiers=0x10, down=False))
        dispatch(key_record(modifiers=0x10, down=False))
    else:
        dispatch(key_record(modifiers=0x18, down=False))
    assert [event.key for event in events] == ["ctrl+shift+s"]


@pytest.mark.skipif(os.name != "nt", reason="Native Windows console structures")
def test_vt_only_save_falls_back_locally_and_late_release_cannot_push():
    from textual.drivers import win32

    events = []
    monitor = terminal.WindowsEventMonitor(None, None, threading.Event(), events.append)
    parser = XTermParser()
    down = win32.INPUT_RECORD()
    down.EventType = 1
    down.Event.KeyEvent = key_record(virtual_key=0, modifiers=0)
    monitor.dispatch_records([down], parser)
    assert events == []
    monitor._pending_save_deadline = 0
    monitor.dispatch_records([], parser)
    assert [event.key for event in events] == ["ctrl+s"]
    up = win32.INPUT_RECORD()
    up.EventType = 1
    up.Event.KeyEvent = key_record(modifiers=0x18, down=False)
    monitor.dispatch_records([up], parser)
    assert [event.key for event in events] == ["ctrl+s"]


@pytest.mark.skipif(os.name != "nt", reason="Windows driver selection")
def test_app_selects_corrected_windows_driver_and_preserves_explicit_drivers(tmp_path):
    from textual.drivers.windows_driver import WindowsDriver
    from tui.app import TaskApp

    assert TaskApp(tmp_path).driver_class is terminal.TaskmanWindowsDriver

    class CustomDriver(WindowsDriver):
        pass

    assert terminal.preserve_windows_modifiers(CustomDriver) is CustomDriver


def _git(root, *arguments):
    completed = subprocess.run([shutil.which("git"), "-C", str(root), *arguments],
                               capture_output=True, text=True, encoding="utf-8", timeout=20)
    if completed.returncode:
        raise AssertionError(completed.stderr)
    return completed.stdout.strip()


def prepare_probe(folder: Path) -> tuple[Path, Path]:
    """Prepare only disposable local files and a local bare remote."""
    folder.mkdir(parents=True, exist_ok=True)
    vault, remote = folder / "vault", folder / "local-remote.git"
    vault.mkdir()
    remote.mkdir()
    _git(vault, "init", "--initial-branch=main")
    _git(remote, "init", "--bare", "--initial-branch=main")
    _git(vault, "config", "user.name", "Taskman Console Test")
    _git(vault, "config", "user.email", "console@example.invalid")
    _git(vault, "remote", "add", "origin", str(remote))
    (vault / "Tasks").mkdir()
    (vault / "Tasks/Inbox.md").write_text(
        f"- [ ] Raw keyboard test 📅 {dt.date.today().isoformat()}\n", encoding="utf-8")
    _git(vault, "add", ".")
    _git(vault, "commit", "-m", "Initial local fixture")
    return vault, remote


def run_console_probe(folder: Path, executable: Path | None = None) -> dict:
    """Launch the source or bundled app in a hidden console and inject raw input.

    This function is also reused by the distribution smoke: all Git traffic is
    confined to the local bare fixture, with preferences isolated in ``folder``.
    """
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("GIT_CONFIG_", "TEXTUAL_"))
           and key not in {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
                          "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
                          "GIT_NAMESPACE", "GIT_PREFIX"}}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_TERMINAL_PROMPT="0", GIT_ALLOW_PROTOCOL="file",
               TASKMAN_CONFIG_DIR=str(folder / "preferences"))
    env.pop("TASKMAN_VAULT", None)
    command = [sys.executable, str(Path(__file__).resolve()), "--native-console-probe", str(folder)]
    if executable is not None:
        command.extend(["--executable", str(executable.resolve())])
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    completed = subprocess.run(command, cwd=ROOT, env=env, startupinfo=startup,
                               creationflags=subprocess.CREATE_NEW_CONSOLE,
                               capture_output=True, text=True, encoding="utf-8", timeout=90)
    report_path = folder / "console-result.json"
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
    assert completed.returncode == 0, f"{report}\n{completed.stdout}\n{completed.stderr}"
    assert report.get("ok"), report
    return report


@pytest.mark.skipif(os.name != "nt" or not shutil.which("git"), reason="Windows console and Git required")
def test_actual_console_input_saves_editor_then_pushes_to_local_remote(tmp_path):
    report = run_console_probe(tmp_path / "console")
    assert report["driver"] == "native ReadConsoleInputW"
    assert report["ctrl_s_did_not_push"]
    assert report["editor_saved_without_push"]
    assert report["editor_ctrl_shift_s_did_not_push"]
    assert report["ctrl_shift_s_pushed"]
    assert report["native_modifier_records_sent"]
    assert report["app_exit"] == 0


def _native_console_probe(folder: Path, executable: Path | None) -> int:
    """Child-process harness; its console never attaches to a user's terminal."""
    from ctypes import wintypes
    from textual.drivers import win32

    folder.mkdir(parents=True, exist_ok=True)
    report = {"ok": False, "driver": "native ReadConsoleInputW"}
    process = None
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    write_input = kernel.WriteConsoleInputW
    write_input.argtypes = [wintypes.HANDLE, ctypes.POINTER(win32.INPUT_RECORD),
                           wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    write_input.restype = wintypes.BOOL
    read_output = kernel.ReadConsoleOutputCharacterW
    read_output.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD,
                           win32.COORD, ctypes.POINTER(wintypes.DWORD)]
    read_output.restype = wintypes.BOOL
    import msvcrt

    def screen() -> str:
        with open("CONOUT$", "w", encoding="utf-8") as output:
            buffer = ctypes.create_unicode_buffer(32768)
            read = wintypes.DWORD()
            if not read_output(msvcrt.get_osfhandle(output.fileno()), buffer, 32767,
                               win32.COORD(0, 0), ctypes.byref(read)):
                raise ctypes.WinError(ctypes.get_last_error())
            return buffer[:read.value]

    def wait_until(predicate, description: str, timeout=25):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if process is not None and process.poll() is not None:
                raise AssertionError(f"App exited early ({process.returncode}): {description}")
            if predicate():
                return
            time.sleep(0.05)
        raise AssertionError(f"Timed out: {description}; screen={screen()[:1200]!r}")

    try:
        vault, remote = prepare_probe(folder)
        original_head = _git(vault, "rev-parse", "HEAD")
        app_command = ([str(executable)] if executable else [sys.executable, "-m", "tui"])
        app_command.extend(["--vault", str(vault)])
        with open("CONIN$", "r", encoding="utf-8") as console_in, \
             open("CONOUT$", "w", encoding="utf-8") as console_out, \
             (folder / "app-stderr.log").open("w", encoding="utf-8") as errors:
            process = subprocess.Popen(app_command, cwd=ROOT, stdin=console_in,
                                       stdout=console_out, stderr=errors)
            input_handle = msvcrt.get_osfhandle(console_in.fileno())

            def press(character: str, modifiers=0, virtual_key=None, native_modifiers=False):
                if virtual_key is None:
                    virtual_key = ord(character.upper()) if character.isalpha() else 0
                keys = []
                if native_modifiers:
                    keys.extend([key_record(virtual_key=0x11, character="\x00", modifiers=0x08),
                                 key_record(virtual_key=0x10, character="\x00", modifiers=0x18)])
                keys.extend(key_record(modifiers=modifiers, character=character,
                                       virtual_key=virtual_key, down=down) for down in (True, False))
                if native_modifiers:
                    keys.extend([key_record(virtual_key=0x10, character="\x00", modifiers=0x08, down=False),
                                 key_record(virtual_key=0x11, character="\x00", modifiers=0, down=False)])
                records = (win32.INPUT_RECORD * len(keys))()
                for index, key in enumerate(keys):
                    records[index].EventType = 1
                    records[index].Event.KeyEvent = key
                count = wintypes.DWORD()
                if not write_input(input_handle, records, len(records), ctypes.byref(count)) or count.value != len(records):
                    raise ctypes.WinError(ctypes.get_last_error())

            wait_until(lambda: "Taskman" in screen() and "Raw keyboard test" in screen(), "app ready")
            press("\x13", 0x08, 0x53)
            wait_until(lambda: "All changes saved to Markdown" in screen(), "plain Ctrl+S acknowledgement")
            remote_refs = _git(remote, "for-each-ref", "--format=%(refname)", "refs/heads")
            assert not remote_refs and _git(vault, "rev-parse", "HEAD") == original_head
            report["ctrl_s_did_not_push"] = True
            press("n")
            wait_until(lambda: "Ctrl+S" in screen() and "Esc" in screen(), "note editor")
            for character in "Saved through real console input":
                press(character)
            press("\x13", 0x08, 0x53)
            path = vault / "Tasks/Inbox.md"
            wait_until(lambda: "Saved through real console input" in path.read_text(encoding="utf-8"), "editor saved")
            wait_until(lambda: "Ctrl+S = save" not in screen(), "editor closed")
            assert not _git(remote, "for-each-ref", "--format=%(refname)", "refs/heads")
            assert _git(vault, "rev-parse", "HEAD") == original_head
            report["editor_saved_without_push"] = True
            press("n")
            wait_until(lambda: "Ctrl+S = save" in screen(), "note editor reopened")
            press("\x13", 0x18, 0x53)
            wait_until(lambda: "Ctrl+S = save" not in screen(), "shift-save closed editor")
            assert not _git(remote, "for-each-ref", "--format=%(refname)", "refs/heads")
            assert _git(vault, "rev-parse", "HEAD") == original_head
            report["editor_ctrl_shift_s_did_not_push"] = True
            press("\x13", 0x18, 0x53, native_modifiers=True)
            report["native_modifier_records_sent"] = True
            wait_until(lambda: bool(_git(remote, "for-each-ref", "--format=%(refname)", "refs/heads/main")), "Ctrl+Shift+S remote update", 35)
            pushed = _git(remote, "rev-parse", "refs/heads/main")
            assert pushed != original_head
            assert "Saved through real console input" in _git(remote, "show", f"{pushed}:Tasks/Inbox.md")
            report["ctrl_shift_s_pushed"] = True
            report["commit"] = pushed
            press("q")
            report["app_exit"] = process.wait(timeout=15)
            assert report["app_exit"] == 0
        report["ok"] = True
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
        try:
            report["screen"] = screen()[:1800]
        except Exception:
            pass
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        (folder / "console-result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1


if __name__ == "__main__" and "--native-console-probe" in sys.argv:
    # Running this source test file directly is supported only for its isolated
    # native-console child, never as a Taskman entry point.
    destination = Path(sys.argv[sys.argv.index("--native-console-probe") + 1]).resolve()
    binary = Path(sys.argv[sys.argv.index("--executable") + 1]) if "--executable" in sys.argv else None
    raise SystemExit(_native_console_probe(destination, binary))
