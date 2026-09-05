"""Install and exercise release assets away from the source checkout."""
from __future__ import annotations

import argparse
from email.parser import BytesParser
import os
from pathlib import Path
import platform
import subprocess
import sys
import tarfile
import tempfile
import venv
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tui import __version__


def select_assets(dist: Path, version: str, *, system: str | None = None,
                  machine: str | None = None) -> dict[str, Path]:
    """Require this release's files, including the native build for this host."""
    system = system or platform.system()
    systems = {"Windows": "windows", "Darwin": "macos", "Linux": "linux"}
    if system not in systems:
        raise RuntimeError(f"Unsupported standalone platform: {system}")
    machine = (machine or platform.machine()).lower()
    arch = {"amd64": "x64", "x86_64": "x64", "aarch64": "arm64"}.get(machine, machine)
    assets = {
        "wheel": dist / f"taskman_vault-{version}-py3-none-any.whl",
        "sdist": dist / f"taskman_vault-{version}.tar.gz",
        "source": dist / f"taskman-{version}-source.zip",
        "standalone": dist / f"taskman-{version}-{systems[system]}-{arch}.zip",
    }
    missing = [path.name for path in assets.values() if not path.is_file()]
    if missing:
        raise RuntimeError("Missing current release assets: " + ", ".join(missing)
                           + "; build with --standalone on this platform")
    return assets


def _zip_member(archive: zipfile.ZipFile, name: str) -> bytes:
    try:
        return archive.read(name)
    except KeyError as error:
        raise RuntimeError(f"{archive.filename}: missing {name}") from error


def _check_document(content: bytes, source: Path, archive: Path, name: str) -> None:
    if content != source.read_bytes():
        raise RuntimeError(f"{archive.name}: {name} differs from the checkout")


def verify_assets(assets: dict[str, Path], version: str, root: Path) -> None:
    """Verify release metadata and redistributable documents before execution."""
    wheel = assets["wheel"]
    metadata_root = f"taskman_vault-{version}.dist-info"
    with zipfile.ZipFile(wheel) as archive:
        members = archive.namelist()
        if any("test_" in p or p.startswith(("Tasks/", "Projects/", "artifacts/")) for p in members):
            raise RuntimeError(f"{wheel.name}: unexpected test or vault files")
        if not all(p.startswith("tui/") or p.startswith(metadata_root + "/") for p in members):
            raise RuntimeError(f"{wheel.name}: unexpected files outside the runtime package")
        metadata = BytesParser().parsebytes(_zip_member(archive, f"{metadata_root}/METADATA"))
        for field, expected in (("Name", "taskman-vault"), ("Version", version),
                                ("License-Expression", "MIT")):
            if metadata.get(field) != expected:
                raise RuntimeError(f"{wheel.name}: expected {field}: {expected}")
        if "LICENSE" not in metadata.get_all("License-File", []):
            raise RuntimeError(f"{wheel.name}: metadata must declare License-File: LICENSE")
        name = f"{metadata_root}/licenses/LICENSE"
        _check_document(_zip_member(archive, name), root / "LICENSE", wheel, name)

    source_documents = ("README.md", "LICENSE", "CONTRIBUTING.md", "SECURITY.md",
                        "docs/DEVELOPMENT.md", "docs/RELEASE_NOTES.md")
    with tarfile.open(assets["sdist"], "r:gz") as archive:
        for document in source_documents:
            name = f"taskman_vault-{version}/{document}"
            try:
                member = archive.getmember(name)
            except KeyError as error:
                raise RuntimeError(f"{assets['sdist'].name}: missing {name}") from error
            if not member.isfile():
                raise RuntimeError(f"{assets['sdist'].name}: {name} must be a regular file")
            with archive.extractfile(member) as source:
                _check_document(source.read(), root / document, assets["sdist"], name)
    for kind, prefix, documents in (
        ("source", f"taskman-{version}", source_documents),
        ("standalone", "taskman", ("README.md", "LICENSE", "SECURITY.md", "docs/RELEASE_NOTES.md")),
    ):
        with zipfile.ZipFile(assets[kind]) as archive:
            for document in documents:
                archived_name = Path(document).name if kind == "standalone" else document
                name = f"{prefix}/{archived_name}"
                _check_document(_zip_member(archive, name), root / document, assets[kind], name)


def call(command: list[str], cwd: Path, env: dict[str, str]) -> str:
    process = subprocess.run(command, cwd=cwd, env=env, text=True,
                             encoding="utf-8", errors="replace", capture_output=True, timeout=180)
    if process.returncode:
        raise RuntimeError(f"{command[0]} exited {process.returncode}\n{process.stdout}\n{process.stderr}")
    return process.stdout


def exercise(command: list[str], work: Path, env: dict[str, str]) -> None:
    folder = work / "My tasks \u00e9 \u65e5\u672c"
    folder.mkdir(parents=True, exist_ok=True)
    assert call([*command, "--version"], work, env).strip() == f"Taskman {__version__}"
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
    assets = select_assets(ROOT / "dist", __version__)
    verify_assets(assets, __version__, ROOT)
    print("DISTRIBUTION_CONTENTS_OK", __version__)
    work.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env.pop("TASKMAN_VAULT", None)
    env["TASKMAN_CONFIG_DIR"] = str(work / "profile")
    env["PYTHONUTF8"] = "1"
    wheel = assets["wheel"]
    install = work / "installed app"
    venv.EnvBuilder(with_pip=True).create(install)
    python = install / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    call([str(python), "-m", "pip", "install", str(wheel)], work, env)
    executable = install / ("Scripts/taskman.exe" if os.name == "nt" else "bin/taskman")
    exercise([str(executable)], work / "wheel", env)
    call([str(python), "-c", "import tui.app; print(tui.app.__file__)"], work, env)
    print("WHEEL_SMOKE_OK", executable)
    unpacked = work / "portable app"
    with zipfile.ZipFile(assets["standalone"]) as bundle:
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
