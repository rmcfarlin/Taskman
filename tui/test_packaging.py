"""Release boundaries: runtime metadata and source allowlists exclude vaults."""
from __future__ import annotations

import importlib.util
import ast
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def _builder():
    spec = importlib.util.spec_from_file_location("taskman_build_release", ROOT / "scripts" / "build_release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_source_allowlist_rejects_personal_vault_and_build_outputs(tmp_path):
    builder = _builder()
    files = ["README.md", "pyproject.toml", "tui/app.py", "tui/test_app.py",
             "scripts/install.ps1", "docs/DEVELOPMENT.md", ".github/workflows/release.yml",
             "Tasks/Inbox.md", "Projects/Private.md", "Notes/secrets.md", "tui/theme.txt",
             "tui/logs/error.log", "artifacts/private.txt", ".env", "build/token.py"]
    for name in files:
        destination = tmp_path / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("do not leak", encoding="utf-8")
    allowed = {p.relative_to(tmp_path).as_posix() for p in builder.source_files(tmp_path)}
    assert allowed == {"README.md", "pyproject.toml", "tui/app.py", "tui/test_app.py",
                       "scripts/install.ps1", "docs/DEVELOPMENT.md", ".github/workflows/release.yml"}


def test_source_archive_contents_exactly_match_allowlist(tmp_path, monkeypatch):
    builder = _builder()
    archive_path = builder.source_zip(tmp_path)
    with zipfile.ZipFile(archive_path) as archive:
        members = archive.namelist()
    prefix = f"taskman-{builder.__version__}/"
    assert set(members) == {prefix + p.relative_to(ROOT).as_posix() for p in builder.source_files()}
    assert all(".." not in Path(name).parts for name in members)


def test_runtime_build_includes_required_modules_and_excludes_tests():
    tree = ast.parse((ROOT / "setup.py").read_text(encoding="utf-8"))
    assignment = next(node for node in tree.body if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == "RUNTIME_MODULES"
                              for target in node.targets))
    runtime = ast.literal_eval(assignment.value)
    assert {"__init__", "__main__", "app", "vaults", "settings", "vault_screen"} <= runtime
    assert all(not module.startswith("test_") for module in runtime)
    assert "visual_check" not in runtime and "check" not in runtime
    assert all((ROOT / "tui" / f"{module}.py").is_file() for module in runtime)
