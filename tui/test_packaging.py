"""Release boundaries: runtime metadata and source allowlists exclude vaults."""
from __future__ import annotations

import importlib.util
import ast
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _builder():
    spec = importlib.util.spec_from_file_location("taskman_build_release", ROOT / "scripts" / "build_release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_source_allowlist_rejects_personal_vault_and_build_outputs(tmp_path):
    builder = _builder()
    files = ["README.md", "LICENSE", "CONTRIBUTING.md", "SECURITY.md",
             "pyproject.toml", "tui/app.py", "tui/test_app.py",
             "scripts/install.ps1", "docs/DEVELOPMENT.md", ".github/workflows/release.yml",
             "Tasks/Inbox.md", "Projects/Private.md", "Notes/secrets.md", "tui/theme.txt",
             "tui/logs/error.log", "artifacts/private.txt", ".env", "build/token.py",
             "assets/taskman.png", "assets/taskman.ico", "assets/private.png",
             "assets/drafts/taskman.png", "assets/private.md"]
    for name in files:
        destination = tmp_path / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("do not leak", encoding="utf-8")
    allowed = {p.relative_to(tmp_path).as_posix() for p in builder.source_files(tmp_path)}
    assert allowed == {"README.md", "LICENSE", "CONTRIBUTING.md", "SECURITY.md",
                       "pyproject.toml", "tui/app.py", "tui/test_app.py",
                       "scripts/install.ps1", "docs/DEVELOPMENT.md", ".github/workflows/release.yml",
                       "assets/taskman.png", "assets/taskman.ico"}


def test_source_archive_contents_exactly_match_allowlist(tmp_path, monkeypatch):
    builder = _builder()
    archive_path = builder.source_zip(tmp_path)
    with zipfile.ZipFile(archive_path) as archive:
        members = archive.namelist()
        for name in ("LICENSE", "CONTRIBUTING.md", "SECURITY.md"):
            assert archive.read(f"taskman-{builder.__version__}/{name}") == (ROOT / name).read_bytes()
    prefix = f"taskman-{builder.__version__}/"
    assert set(members) == {prefix + p.relative_to(ROOT).as_posix() for p in builder.source_files()}
    assert all(".." not in Path(name).parts for name in members)


def test_runtime_build_includes_required_modules_and_excludes_tests():
    tree = ast.parse((ROOT / "setup.py").read_text(encoding="utf-8"))
    assignment = next(node for node in tree.body if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == "RUNTIME_MODULES"
                              for target in node.targets))
    runtime = ast.literal_eval(assignment.value)
    assert {"__init__", "__main__", "app", "vaults", "settings", "vault_screen",
            "notes", "notes_ui", "notes_actions", "note_files", "note_templates", "notes_dialogs",
            "cli", "git_sync", "terminal", "recurrence"} <= runtime
    assert all(not module.startswith("test_") for module in runtime)
    assert "visual_check" not in runtime and "check" not in runtime
    assert all((ROOT / "tui" / f"{module}.py").is_file() for module in runtime)


@pytest.mark.parametrize("system,os_name", [("Windows", "nt"), ("Linux", "posix"),
                                          ("Darwin", "posix")])
def test_standalone_packages_artwork_and_embeds_icon_only_on_windows(tmp_path, monkeypatch,
                                                                  system, os_name):
    builder = _builder()
    monkeypatch.setattr(builder, "ROOT", tmp_path)
    monkeypatch.setattr(builder, "os", SimpleNamespace(name=os_name))
    monkeypatch.setattr(builder.platform, "system", lambda: system)
    monkeypatch.setattr(builder.platform, "machine", lambda: "AMD64")
    (tmp_path / "README.md").write_text("Taskman", encoding="utf-8")
    for name in ("LICENSE", "SECURITY.md"):
        (tmp_path / name).write_text("distribution document: " + name, encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "RELEASE_NOTES.md").write_text("Current release notes", encoding="utf-8")
    (tmp_path / "assets").mkdir()
    for name in ("taskman.png", "taskman.ico"):
        (tmp_path / "assets" / name).write_bytes(b"artwork:" + name.encode())
    commands = []

    def build_bundle(*args):
        commands.append(args)
        bundle = tmp_path / "build" / "standalone" / "dist" / "taskman"
        bundle.mkdir(parents=True)
        (bundle / ("taskman.exe" if os_name == "nt" else "taskman")).write_bytes(b"executable")

    monkeypatch.setattr(builder, "run", build_bundle)
    output = tmp_path / "dist"
    output.mkdir()
    archive_path = builder.standalone(output)
    assert len(commands) == 1 and "--console" in commands[0]
    if os_name == "nt":
        icon_position = commands[0].index("--icon")
        assert commands[0][icon_position + 1] == str(tmp_path / "assets" / "taskman.ico")
    else:
        assert "--icon" not in commands[0]
    with zipfile.ZipFile(archive_path) as archive:
        import json
        manifest = json.loads(archive.read("taskman/.taskman-install.json"))
        assert manifest["app_id"] == "taskman-vault"
        assert manifest["version"] == builder.__version__
        assert set(manifest["files"]) == {name.removeprefix("taskman/") for name in archive.namelist() if name != "taskman/.taskman-install.json"}
        assert archive.read("taskman/RELEASE_NOTES.md") == (tmp_path / "docs/RELEASE_NOTES.md").read_bytes()
        for name in ("LICENSE", "SECURITY.md"):
            assert archive.read(f"taskman/{name}") == (tmp_path / name).read_bytes()
        for name in ("taskman.png", "taskman.ico"):
            assert archive.read(f"taskman/{name}") == (tmp_path / "assets" / name).read_bytes()
