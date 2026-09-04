"""Product folder behavior against real temporary folders, never personal notes."""

import json
import os
from pathlib import Path

import pytest

from tui import settings, taskman as tm, vaults


def test_settings_roundtrip_unicode_dedup_and_no_package_writes(tmp_path, monkeypatch):
    config = tmp_path / "Preferences É"
    monkeypatch.setenv("TASKMAN_CONFIG_DIR", str(config))
    assert settings.config_dir() == config
    assert settings.read_theme() == "" and settings.recent_vaults() == []
    assert not config.exists()
    first, second = tmp_path / "Work Résumé", tmp_path / "Personal Notes"
    first.mkdir()
    second.mkdir()
    settings.write_theme("taskman-rcm")
    settings.remember_vault(first)
    settings.remember_vault(second)
    settings.remember_vault(first)
    assert settings.recent_vaults() == [first, second]
    assert settings.last_vault() == first
    assert settings.read_theme() == "taskman-rcm"
    assert sorted(p.name for p in config.iterdir()) == ["settings.json"]
    values = json.loads((config / "settings.json").read_text(encoding="utf-8"))
    assert values["recent_vaults"] == [str(first), str(second)]


@pytest.mark.parametrize("content", ["{bad json", "[]", "null", '{"recent_vaults": 12, "theme": false}'])
def test_corrupt_settings_are_recoverable(tmp_path, monkeypatch, content):
    monkeypatch.setenv("TASKMAN_CONFIG_DIR", str(tmp_path))
    (tmp_path / "settings.json").write_text(content, encoding="utf-8")
    assert settings.read_theme() == "" and settings.last_vault() is None
    settings.write_theme("taskman-teal")
    assert settings.read_theme() == "taskman-teal"


def test_settings_failed_atomic_replace_keeps_prior_file(tmp_path, monkeypatch):
    monkeypatch.setenv("TASKMAN_CONFIG_DIR", str(tmp_path))
    settings.write_theme("taskman-rcm")
    original = (tmp_path / "settings.json").read_bytes()

    def refused(*args):
        raise PermissionError("settings destination unavailable")

    monkeypatch.setattr(settings.os, "replace", refused)
    with pytest.raises(PermissionError, match="unavailable"):
        settings.write_theme("taskman-light")
    assert (tmp_path / "settings.json").read_bytes() == original
    assert sorted(p.name for p in tmp_path.iterdir()) == ["settings.json"]


def test_recents_are_bounded_and_skip_unavailable_and_malformed_entries(tmp_path):
    folders = [tmp_path / f"Vault {i}" for i in range(12)]
    for folder in folders:
        folder.mkdir()
        settings.remember_vault(folder)
    assert settings.recent_vaults() == list(reversed(folders[-10:]))
    folders[-1].rmdir()
    assert settings.last_vault() == folders[-2]


def test_plan_setup_and_repeated_setup_preserve_all_existing_bytes(tmp_path):
    root = tmp_path / "Résumé Projects"
    root.mkdir()
    (root / "Tasks").mkdir()
    inbox = root / "Tasks" / "Inbox.md"
    original = b"# My inbox\r\n\r\n- [ ] Keep this task\r\n"
    inbox.write_bytes(original)
    private = root / "Important.data"
    private.write_bytes(b"\x00private binary\xff")
    plan = vaults.plan_vault(root)
    assert "Tasks" not in plan.missing_dirs
    assert "Tasks/Inbox.md" not in plan.missing_files
    assert ".taskman/vault.json" in plan.missing_files
    assert not (root / ".taskman").exists()
    assert vaults.initialize_vault(root) == root
    first = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert vaults.initialize_vault(root) == root
    second = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert first == second
    assert inbox.read_bytes() == original and private.read_bytes() == b"\x00private binary\xff"
    assert vaults.plan_vault(root).missing_dirs == ()
    assert vaults.plan_vault(root).missing_files == ()
    assert not (root / ".git").exists()


def test_setup_can_create_a_missing_root_but_open_requires_existing(tmp_path):
    root = tmp_path / "New parent" / "New vault"
    with pytest.raises(ValueError, match="does not exist"):
        vaults.normalize_folder(root)
    plan = vaults.plan_vault(f'"{root}"')
    assert plan.root == root and not root.parent.exists()
    assert vaults.initialize_vault(root) == root
    assert (root / "Tasks" / "Inbox.md").is_file()


@pytest.mark.parametrize("blocked", ["Notes", "Tasks/Inbox.md", ".taskman/vault.json"])
def test_setup_preflights_all_conflicts_before_creating_anything(tmp_path, blocked):
    root = tmp_path / "Vault"
    root.mkdir()
    target = root / blocked
    target.parent.mkdir(exist_ok=True)
    if target.suffix:
        target.mkdir()
    else:
        target.write_text("preserve", encoding="utf-8")
    before = {p.relative_to(root) for p in root.rglob("*")}
    with pytest.raises(ValueError, match="existing"):
        vaults.initialize_vault(root)
    assert {p.relative_to(root) for p in root.rglob("*")} == before


def test_existing_marker_is_never_rewritten(tmp_path):
    (tmp_path / ".taskman").mkdir()
    marker = tmp_path / ".taskman" / "vault.json"
    marker.write_bytes(b'{"my_future_setting":42}')
    vaults.initialize_vault(tmp_path)
    assert marker.read_bytes() == b'{"my_future_setting":42}'


def test_pasted_paths_support_quotes_environment_and_unicode(tmp_path, monkeypatch):
    root = tmp_path / "Mañana Work"
    root.mkdir()
    monkeypatch.setenv("TASKMAN_TEST_FOLDER", str(root))
    token = "%TASKMAN_TEST_FOLDER%" if os.name == "nt" else "$TASKMAN_TEST_FOLDER"
    assert vaults.normalize_folder(f' "{token}" ') == root
    with pytest.raises(ValueError, match="Enter"):
        vaults.normalize_folder(" ")


def test_discovery_never_falls_back_to_installed_package(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert vaults.discover_vault() is None
    with pytest.raises(ValueError, match="No vault selected"):
        tm.vault_root()
    legacy = tmp_path / "Tasks"
    legacy.mkdir()
    assert vaults.discover_vault() == tmp_path


def test_discovery_precedence_and_invalid_explicit_path(tmp_path, monkeypatch):
    paths = [tmp_path / name for name in ("Recent", "Environment", "Explicit")]
    for path in paths:
        path.mkdir()
    settings.remember_vault(paths[0])
    assert vaults.discover_vault() == paths[0]
    monkeypatch.setenv("TASKMAN_VAULT", str(paths[1]))
    assert vaults.discover_vault() == paths[1]
    assert vaults.discover_vault(paths[2]) == paths[2]
    with pytest.raises(ValueError, match="does not exist"):
        vaults.discover_vault(tmp_path / "Missing")


def test_arbitrary_folders_scan_recursively_and_prune_generated_trees(tmp_path):
    keep = tmp_path / "Work" / "Meeting notes.MD"
    keep.parent.mkdir()
    keep.write_text("- [ ] Real task", encoding="utf-8")
    loose = tmp_path / "Capture.md"
    loose.write_text("- [ ] Capture task", encoding="utf-8")
    docs = tmp_path / "docs" / "Reading.md"
    docs.parent.mkdir()
    docs.write_text("- [ ] Read this", encoding="utf-8")
    for name in ("node_modules", "BUILD", ".git", ".hidden", "Templates", "artifacts", "venv"):
        directory = tmp_path / name / "nested"
        directory.mkdir(parents=True)
        (directory / "Ignored.md").write_text("- [ ] Not a task", encoding="utf-8")
    assert set(tm.iter_markdown_files(tmp_path)) == {keep, loose, docs}
    assert {task.description for task in tm.load_all(tmp_path)} == {"Real task", "Capture task", "Read this"}


def test_legacy_scope_is_preserved_until_explicit_setup(tmp_path):
    (tmp_path / "Tasks").mkdir()
    ordinary = tmp_path / "Work"
    ordinary.mkdir()
    note = ordinary / "Plan.md"
    note.write_text("- [ ] Plan in any folder", encoding="utf-8")
    assert tm.iter_markdown_files(tmp_path) == []
    vaults.initialize_vault(tmp_path)
    assert note in tm.iter_markdown_files(tmp_path)


@pytest.mark.parametrize("relative", ["../escape.md", "Work/../../escape.md", "C:/escape.md", "C:escape.md", "x:stream", "/escape.md"])
def test_core_paths_cannot_escape_vault(tmp_path, relative):
    with pytest.raises(ValueError, match="inside"):
        vaults.vault_path(tmp_path, relative)


@pytest.mark.skipif(os.name != "nt", reason="Windows junction safety")
def test_scan_setup_and_plain_mutations_do_not_follow_windows_junctions(tmp_path):
    import _winapi

    root, outside = tmp_path / "Vault", tmp_path / "Outside"
    root.mkdir()
    outside.mkdir()
    foreign = outside / "Inbox.md"
    original = b"- [ ] Foreign task\n"
    foreign.write_bytes(original)
    linked = root / "Tasks"
    _winapi.CreateJunction(str(outside), str(linked))
    try:
        assert tm.iter_markdown_files(root) == []
        with pytest.raises(ValueError, match="linked"):
            vaults.initialize_vault(root)
        with pytest.raises(ValueError, match="linked"):
            tm.add_task(root, "Must not escape")
        with pytest.raises(ValueError, match="linked"):
            vaults.normalize_folder(linked)
        assert foreign.read_bytes() == original
        assert sorted(p.name for p in root.iterdir()) == ["Tasks"]
    finally:
        linked.rmdir()
