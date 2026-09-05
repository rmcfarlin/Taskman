"""Release checks reject stale artifacts and incomplete license redistribution."""
from __future__ import annotations

import importlib.util
import io
from pathlib import Path
import tarfile
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
VERSION = "9.8.7"


@pytest.fixture
def smoke():
    spec = importlib.util.spec_from_file_location(
        "taskman_distribution_smoke", ROOT / "scripts" / "smoke_distribution.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_archive(path: Path, members: dict[str, bytes]) -> None:
    if path.name.endswith(".tar.gz"):
        with tarfile.open(path, "w:gz") as archive:
            for name, content in members.items():
                entry = tarfile.TarInfo(name)
                entry.size = len(content)
                archive.addfile(entry, io.BytesIO(content))
    else:
        with zipfile.ZipFile(path, "w") as archive:
            for name, content in members.items():
                archive.writestr(name, content)


@pytest.fixture
def release(tmp_path):
    documents = ("README.md", "LICENSE", "CONTRIBUTING.md", "SECURITY.md",
                 "docs/DEVELOPMENT.md")
    for name in documents:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"checked-in document: " + name.encode())
    dist = tmp_path / "dist"
    dist.mkdir()
    metadata_root = f"taskman_vault-{VERSION}.dist-info"
    members = {
        "wheel": {
            "tui/__init__.py": f'__version__ = "{VERSION}"'.encode(),
            f"{metadata_root}/METADATA": (
                "Metadata-Version: 2.4\nName: taskman-vault\n"
                f"Version: {VERSION}\nLicense-Expression: MIT\nLicense-File: LICENSE\n"
            ).encode(),
            f"{metadata_root}/licenses/LICENSE": (tmp_path / "LICENSE").read_bytes(),
        },
        "sdist": {f"taskman_vault-{VERSION}/{name}": (tmp_path / name).read_bytes()
                  for name in documents},
        "source": {f"taskman-{VERSION}/{name}": (tmp_path / name).read_bytes()
                   for name in documents},
        "standalone": {f"taskman/{name}": (tmp_path / name).read_bytes()
                       for name in ("README.md", "LICENSE", "SECURITY.md")},
    }
    paths = {
        "wheel": dist / f"taskman_vault-{VERSION}-py3-none-any.whl",
        "sdist": dist / f"taskman_vault-{VERSION}.tar.gz",
        "source": dist / f"taskman-{VERSION}-source.zip",
        "standalone": dist / f"taskman-{VERSION}-windows-x64.zip",
    }
    for kind, path in paths.items():
        _write_archive(path, members[kind])
    return tmp_path, paths, members


@pytest.mark.parametrize("system,machine,filename", [
    ("Windows", "AMD64", "windows-x64"),
    ("Linux", "x86_64", "linux-x64"),
    ("Darwin", "arm64", "macos-arm64"),
    ("Linux", "aarch64", "linux-arm64"),
])
def test_selects_current_release_for_host_despite_other_downloads(smoke, release,
                                                               system, machine, filename):
    root, paths, _ = release
    dist = root / "dist"
    expected_native = dist / f"taskman-{VERSION}-{filename}.zip"
    expected_native.touch(exist_ok=True)
    for stale in ("taskman_vault-1.0.0-py3-none-any.whl", "taskman-1.0.0-windows-x64.zip",
                  f"taskman-{VERSION}-windows-arm64.zip", f"taskman-{VERSION}-linux-riscv64.zip"):
        (dist / stale).touch()
    selected = smoke.select_assets(dist, VERSION, system=system, machine=machine)
    assert selected == {**paths, "standalone": expected_native}


@pytest.mark.parametrize("missing,replacement", [
    ("wheel", "taskman_vault-1.0.0-py3-none-any.whl"),
    ("standalone", f"taskman-{VERSION}-linux-x64.zip"),
    ("standalone", f"taskman-{VERSION}-windows-arm64.zip"),
    ("standalone", "taskman-1.0.0-windows-x64.zip"),
])
def test_rejects_missing_version_or_host_asset(smoke, release, missing, replacement):
    root, paths, _ = release
    paths[missing].rename(root / "dist" / replacement)
    with pytest.raises(RuntimeError, match=paths[missing].name):
        smoke.select_assets(root / "dist", VERSION, system="Windows", machine="AMD64")


def test_verifies_all_distribution_documents_and_metadata(smoke, release):
    root, paths, _ = release
    smoke.verify_assets(paths, VERSION, root)


@pytest.mark.parametrize("kind", ["wheel", "sdist", "source", "standalone"])
@pytest.mark.parametrize("change", ["missing", "different"])
def test_rejects_missing_or_changed_license_in_each_format(smoke, release, kind, change):
    root, paths, members = release
    name = next(name for name in members[kind] if name.endswith("/LICENSE"))
    if change == "missing":
        del members[kind][name]
    else:
        members[kind][name] = b"an unrelated license"
    _write_archive(paths[kind], members[kind])
    with pytest.raises(RuntimeError, match="LICENSE"):
        smoke.verify_assets(paths, VERSION, root)


@pytest.mark.parametrize("kind,document", [
    ("sdist", "CONTRIBUTING.md"), ("source", "SECURITY.md"),
    ("standalone", "SECURITY.md"),
])
def test_rejects_missing_distribution_guidance(smoke, release, kind, document):
    root, paths, members = release
    name = next(name for name in members[kind] if name.endswith("/" + document))
    del members[kind][name]
    _write_archive(paths[kind], members[kind])
    with pytest.raises(RuntimeError, match=document):
        smoke.verify_assets(paths, VERSION, root)


@pytest.mark.parametrize("old,new,field", [
    (f"Version: {VERSION}", "Version: 1.0.0", "Version"),
    ("License-Expression: MIT", "License-Expression: Apache-2.0", "License-Expression"),
    ("License-Expression: MIT\n", "", "License-Expression"),
    ("License-File: LICENSE\n", "", "License-File"),
])
def test_rejects_wrong_or_missing_wheel_metadata(smoke, release, old, new, field):
    root, paths, members = release
    name = f"taskman_vault-{VERSION}.dist-info/METADATA"
    members["wheel"][name] = members["wheel"][name].replace(old.encode(), new.encode())
    _write_archive(paths["wheel"], members["wheel"])
    with pytest.raises(RuntimeError, match=field):
        smoke.verify_assets(paths, VERSION, root)


def test_cli_version_check_rejects_a_version_with_the_same_prefix(smoke, tmp_path, monkeypatch):
    monkeypatch.setattr(smoke, "__version__", VERSION)
    monkeypatch.setattr(smoke, "call", lambda *args: f"Taskman {VERSION}0\n")
    with pytest.raises(AssertionError):
        smoke.exercise(["taskman"], tmp_path, {})
