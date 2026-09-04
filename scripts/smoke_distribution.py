"""Install and exercise release assets away from the source checkout."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import venv
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def call(command: list[str], cwd: Path, env: dict[str, str]) -> str:
    process = subprocess.run(command, cwd=cwd, env=env, text=True,
                             encoding="utf-8", errors="replace", capture_output=True, timeout=180)
    if process.returncode:
        raise RuntimeError(f"{command[0]} exited {process.returncode}\n{process.stdout}\n{process.stderr}")
    return process.stdout


def exercise(command: list[str], work: Path, env: dict[str, str]) -> None:
    folder = work / "My tasks \u00e9 \u65e5\u672c"
    folder.mkdir(parents=True, exist_ok=True)
    assert "2.4.0" in call([*command, "--version"], work, env)
    assert "--vault" in call([*command, "--help"], work, env)
    call([*command, "--init", str(folder)], work, env)
    call([*command, "--vault", str(folder), "--add", "Distribution smoke task"], work, env)
    assert "Distribution smoke task" in call([*command, "--vault", str(folder), "--plain", "all"], work, env)
    assert "tasks: 1" in call([*command, "--vault", str(folder), "--check"], work, env)
    inbox = folder / "Tasks" / "Inbox.md"
    before = inbox.read_bytes()
    call([*command, "--init", str(folder)], work, env)
    assert inbox.read_bytes() == before, "Reinitialization changed the existing inbox"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, help="empty smoke directory outside the source checkout")
    args = parser.parse_args()
    work = (args.work_dir or Path(tempfile.mkdtemp(prefix="taskman-release-"))).resolve()
    if work == ROOT or ROOT in work.parents:
        parser.error("The smoke directory must be outside the source checkout")
    work.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env.pop("TASKMAN_VAULT", None)
    env["TASKMAN_CONFIG_DIR"] = str(work / "profile")
    env["PYTHONUTF8"] = "1"
    wheel = next((ROOT / "dist").glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        members = archive.namelist()
        assert not any("test_" in p or p.startswith(("Tasks/", "Projects/", "artifacts/")) for p in members)
        assert all(p.startswith("tui/") or ".dist-info/" in p for p in members)
    install = work / "installed app"
    venv.EnvBuilder(with_pip=True).create(install)
    python = install / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    call([str(python), "-m", "pip", "install", str(wheel)], work, env)
    executable = install / ("Scripts/taskman.exe" if os.name == "nt" else "bin/taskman")
    exercise([str(executable)], work / "wheel", env)
    call([str(python), "-c", "import tui.app; print(tui.app.__file__)"], work, env)
    print("WHEEL_SMOKE_OK", executable)
    candidates = [p for p in (ROOT / "dist").glob("taskman-*.zip") if not p.name.endswith("-source.zip")]
    if not candidates:
        raise RuntimeError("No standalone archive found; build with --standalone")
    archive = candidates[0]
    unpacked = work / "portable app"
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(unpacked)
        if os.name != "nt":
            for member in bundle.infolist():
                mode = member.external_attr >> 16
                if mode:
                    (unpacked / member.filename).chmod(mode)
    frozen = unpacked / "taskman" / ("taskman.exe" if os.name == "nt" else "taskman")
    frozen_env = env.copy()
    frozen_env["PATH"] = os.environ.get("SystemRoot", "C:\\Windows") + "\\System32" if os.name == "nt" else "/usr/bin:/bin"
    exercise([str(frozen)], work / "frozen", frozen_env)
    print("STANDALONE_SMOKE_OK", frozen)
    print("DISTRIBUTION_SMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
