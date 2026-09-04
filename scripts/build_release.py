"""Build clean source / wheel / standalone downloads from explicit file lists."""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tui import __version__

SOURCE_ROOT_FILES = ("README.md", "pyproject.toml", "setup.py", "MANIFEST.in", ".gitignore")
SOURCE_ASSET_FILES = ("assets/taskman.png", "assets/taskman.ico")
SOURCE_PATTERNS = {
    "tui": ("*.py", "requirements.txt"),
    "scripts": ("*.py", "*.ps1", "*.cmd", "*.sh"),
    "docs": ("*.md",),
    ".github/workflows": ("*.yml",),
}


def source_files(root: Path = ROOT) -> list[Path]:
    """No recursion through arbitrary folders: personal notes never qualify."""
    files = [root / name for name in SOURCE_ROOT_FILES if (root / name).is_file()]
    files.extend(root / name for name in SOURCE_ASSET_FILES
                 if (root / name).is_file() and not (root / name).is_symlink())
    for folder, patterns in SOURCE_PATTERNS.items():
        for pattern in patterns:
            files.extend(p for p in (root / folder).glob(pattern)
                         if p.is_file() and not p.is_symlink())
    return sorted(set(files))


def run(*args: str) -> None:
    subprocess.run(args, cwd=ROOT, check=True)


def source_zip(output: Path) -> Path:
    path = output / f"taskman-{__version__}-source.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for source in source_files():
            archive.write(source, f"taskman-{__version__}/{source.relative_to(ROOT).as_posix()}")
    return path


def standalone(output: Path) -> Path:
    # onedir avoids unpacking the Python runtime on every launch and makes the
    # application folder portable. There is no external Python dependency.
    build_root = ROOT / "build" / "standalone"
    icon_args = ("--icon", str(ROOT / "assets" / "taskman.ico")) if os.name == "nt" else ()
    run(sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir",
        "--console", "--name", "taskman", "--paths", str(ROOT), *icon_args,
        "--collect-all", "textual", "--collect-all", "rich",
        "--distpath", str(build_root / "dist"), "--workpath", str(build_root / "work"),
        "--specpath", str(build_root), str(ROOT / "scripts" / "frozen_entry.py"))
    bundle = build_root / "dist" / "taskman"
    shutil.copy2(ROOT / "README.md", bundle / "README.md")
    for name in SOURCE_ASSET_FILES:
        source = ROOT / name
        shutil.copy2(source, bundle / source.name)
    if os.name == "nt":
        (bundle / "Taskman.cmd").write_text(
            '@echo off\r\n"%~dp0taskman.exe" %*\r\nset "taskmanExit=%errorlevel%"\r\n'
            'if not "%taskmanExit%"=="0" pause\r\nexit /b %taskmanExit%\r\n',
            encoding="ascii")
    system = {"Windows": "windows", "Darwin": "macos", "Linux": "linux"}[platform.system()]
    machine = platform.machine().lower()
    arch = {"amd64": "x64", "x86_64": "x64", "aarch64": "arm64"}.get(machine, machine)
    path = output / f"taskman-{__version__}-{system}-{arch}.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for source in sorted(bundle.rglob("*")):
            if source.is_file():
                archive.write(source, f"taskman/{source.relative_to(bundle).as_posix()}")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--standalone", action="store_true", help="also freeze a self-contained runtime")
    args = parser.parse_args()
    output = ROOT / "dist"
    output.mkdir(exist_ok=True)
    run(sys.executable, "-m", "build", "--wheel", "--sdist", "--outdir", str(output))
    made = [source_zip(output)]
    if args.standalone:
        made.append(standalone(output))
    files = sorted(p for p in output.iterdir() if p.is_file() and p.name != "SHA256SUMS.txt")
    (output / "SHA256SUMS.txt").write_text("".join(
        f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in files), encoding="utf-8")
    for path in made:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
