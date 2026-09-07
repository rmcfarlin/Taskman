"""Launch the actual CLI and verify failures remain diagnosable."""

from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from tui import __main__ as entrypoint
from tui.diagnostics import record_error


ROOT = Path(__file__).resolve().parent.parent


def cli(*args: str, direct: bool = False) -> subprocess.CompletedProcess:
    target = [str(ROOT / "tui" / "__main__.py")] if direct else ["-m", "tui"]
    # -S removes all site packages, reproducing the user's stock-Python launch.
    return subprocess.run([sys.executable, "-S", *target, *args], cwd=ROOT,
                          capture_output=True, text=True, encoding="utf-8", timeout=30)


@pytest.mark.parametrize("direct", [False, True])
def test_plain_commands_do_not_require_textual(tmp_path, direct):
    (tmp_path / "Inbox.md").write_text("- [ ] Call Amy\n", encoding="utf-8")
    result = cli("--vault", str(tmp_path), "--plain=all", direct=direct)
    assert result.returncode == 0, result.stderr
    assert "Call Amy" in result.stdout
    result = cli("--vault", str(tmp_path), "--check", direct=direct)
    assert result.returncode == 0, result.stderr
    assert "tasks: 1" in result.stdout


def test_help_works_without_ui_dependencies():
    result = cli("--help")
    assert result.returncode == 0, result.stderr
    assert "--plain" in result.stdout


def test_missing_dependency_reports_real_interpreter_and_repair():
    result = cli()
    assert result.returncode == 1
    assert "textual is not installed" in result.stderr or "rich is not installed" in result.stderr
    assert sys.executable in result.stderr
    assert "install.ps1" in result.stderr
    assert "No module named 'taskman'" not in result.stderr


def test_ui_exit_code_and_options_are_preserved(monkeypatch, tmp_path):
    calls = []
    fake_app = SimpleNamespace(THEMES=[], run=lambda *args: calls.append(args) or 7)
    monkeypatch.setitem(sys.modules, "tui.app", fake_app)
    assert entrypoint.main(["--theme=taskman-teal", "--vault", str(tmp_path)]) == 7
    assert calls == [(str(tmp_path), "taskman-teal")]


def test_unknown_ui_arguments_do_not_silently_launch():
    result = cli("--mistyped-option")
    assert result.returncode == 2
    assert "unrecognized arguments" in result.stderr
    assert "not installed" not in result.stderr


@pytest.mark.parametrize("arguments", [
    ("--due", "today"), ("--scheduled", "today"),
    ("--under", "Tasks/Inbox.md:1"), ("--project", "Finance"),
    ("--search", "term"), ("--id",),
])
def test_incomplete_cli_commands_do_not_open_the_ui(arguments):
    result = cli(*arguments)
    assert result.returncode == 2
    assert "not installed" not in result.stderr
    assert "Taskman:" in result.stderr


def test_diagnostic_retains_error_without_capturing_locals_or_task_context(tmp_path):
    private_task_text = "customer task contents must not be captured"
    try:
        raise AttributeError("'Text' object has no attribute 'translate'")
    except AttributeError as exc:
        path = record_error(exc, log_dir=tmp_path,
                            context={"screen": "TaskScreen", "task": private_task_text})
    assert path is not None and path.parent == tmp_path
    text = path.read_text(encoding="utf-8")
    assert "AttributeError" in text and "translate" in text
    assert "test_diagnostic_retains_error" in text
    assert "screen: TaskScreen" in text
    assert "Textual:" in text and "Python:" in text
    assert private_task_text not in text


def test_diagnostic_logging_failure_never_replaces_original_error(tmp_path):
    existing_file = tmp_path / "not-a-directory"
    existing_file.write_text("unchanged", encoding="utf-8")
    assert record_error(RuntimeError("original failure"), log_dir=existing_file) is None
    assert existing_file.read_text(encoding="utf-8") == "unchanged"


def test_fatal_textual_failure_produces_log_and_nonzero_process_exit(tmp_path):
    code = """
from functools import partial
from pathlib import Path
import sys
from tui import app as runtime
from tui.diagnostics import record_error

class BrokenApp(runtime.TaskApp):
    def on_mount(self):
        raise RuntimeError('deliberate startup failure for launcher test')

    def run(self, *args, **kwargs):
        return super().run(*args, headless=True, **kwargs)

runtime.TaskApp = BrokenApp
runtime.record_error = partial(record_error, log_dir=Path(sys.argv[2]))
raise SystemExit(runtime.run(sys.argv[1]))
"""
    log_dir = tmp_path / "logs"
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path), str(log_dir)],
                            cwd=ROOT, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=40)
    assert result.returncode == 1, result.stdout + result.stderr
    reports = list(log_dir.glob("*.log"))
    assert len(reports) == 1
    assert "deliberate startup failure" in reports[0].read_text(encoding="utf-8")
    assert str(reports[0]) in result.stderr


@pytest.mark.skipif(sys.platform != "win32" or not shutil.which("powershell.exe"),
                    reason="Windows launcher integration")
def test_windows_launcher_uses_installed_environment_and_keeps_error_status(tmp_path, monkeypatch):
    # Exercise the launcher from a downloaded source folder with no .venv.
    # The real wheel installer is independently exercised by smoke_distribution.
    import os
    downloaded = tmp_path / "Downloaded source"
    (downloaded / "scripts").mkdir(parents=True)
    launcher = downloaded / "scripts" / "start-taskman.ps1"
    shutil.copy2(ROOT / "scripts" / "start-taskman.ps1", launcher)
    local_data = tmp_path / "User apps"
    installed = local_data / "Taskman" / "app" / "venv"
    (installed / "Scripts").mkdir(parents=True)
    python = installed / "Scripts" / "python.exe"
    shutil.copy2(sys.executable, python)
    (installed / "pyvenv.cfg").write_text(f"home = {sys.base_prefix}\ninclude-system-site-packages = true\n", encoding="utf-8")
    monkeypatch.setenv("LOCALAPPDATA", str(local_data))
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(ROOT), *sys.path]))
    monkeypatch.setenv("TASKMAN_CONFIG_DIR", str(tmp_path / "settings"))
    vault = tmp_path / "My tasks"
    vault.mkdir()
    command = ["powershell.exe", "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
               "-File", str(launcher), "-NoPause"]
    result = subprocess.run([*command, "-RuntimeCheck"], cwd=tmp_path, capture_output=True,
                            text=True, timeout=40)
    assert result.returncode == 0, result.stderr + result.stdout
    assert str(python) in result.stdout
    result = subprocess.run([*command, "--mistyped-option"], cwd=tmp_path,
                            capture_output=True, text=True, timeout=40)
    assert result.returncode == 2, result.stderr + result.stdout
    assert "unrecognized arguments" in result.stderr
    assert "exited with code 2" in result.stdout
    result = subprocess.run([*command, "--vault", str(vault), "--check"], cwd=tmp_path,
                            capture_output=True, text=True, timeout=40)
    assert result.returncode == 0, result.stderr + result.stdout
    assert "tasks: 0" in result.stdout
    assert "runtime ready" not in result.stdout
