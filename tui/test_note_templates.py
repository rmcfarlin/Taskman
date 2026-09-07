"""Editable templates stay separate from notes/tasks and reject stale writes."""
from dataclasses import replace
import datetime as dt
import os
from pathlib import Path

import pytest

from tui import note_templates as templates
from tui import notes, taskman
from tui.history import History, HistoryConflict


def custom(vault, name, body):
    file = vault / templates.TEMPLATE_DIR / name
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(body, encoding="utf-8")
    return file


def test_builtin_discovery_and_instantiation_never_write_files(tmp_path):
    found = templates.list_templates(tmp_path)
    assert found == [templates.MEETING_TEMPLATE]
    meeting = found[0]
    assert meeting.name == "Meeting" and meeting.builtin and meeting.revision is None
    assert meeting.file == ".taskman/templates/notes/Meeting.md"
    body = templates.instantiate(meeting, dt.date(2026, 9, 7))
    assert "Date: 2026-09-07" in body
    for section in ("Attendees", "Discussion", "Decisions", "Actions"):
        assert f"## {section}" in body
    assert list(tmp_path.iterdir()) == []


def test_custom_templates_override_default_case_insensitively_and_sort(tmp_path):
    custom(tmp_path, "meeting.MD", "Our meeting\n")
    custom(tmp_path, "Daily review.md", "Review {{date}}\n")
    custom(tmp_path, "Ignore.txt", "not a template")
    nested = tmp_path / templates.TEMPLATE_DIR / "Nested"
    nested.mkdir()
    (nested / "Ignored.md").write_text("Nested", encoding="utf-8")
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    found = templates.list_templates(tmp_path)
    assert [template.name for template in found] == ["Daily review", "meeting"]
    assert all(not template.builtin and len(template.revision) == 64 for template in found)
    assert found[1].body == "Our meeting\n"
    assert before == {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert not (tmp_path / ".taskman/write.lock").exists()


def test_explicit_builtin_edit_changes_only_future_captures(tmp_path):
    meeting = templates.list_templates(tmp_path)[0]
    old_body = templates.instantiate(meeting, dt.date(2026, 9, 7))
    existing_note = notes.NotesStore(tmp_path).create("First meeting", old_body)
    original = (tmp_path / existing_note.file).read_bytes()
    edited = templates.save_template(tmp_path, meeting, "Date {{date}}\nNew agenda\n")
    assert not edited.builtin and edited.revision
    assert edited.file == meeting.file
    fresh = templates.list_templates(tmp_path)[0]
    assert fresh == edited
    assert templates.instantiate(fresh, dt.date(2026, 9, 8)) == "Date 2026-09-08\nNew agenda\n"
    assert (tmp_path / existing_note.file).read_bytes() == original
    assert meeting.body == old_body.replace("2026-09-07", "{{date}}")


def test_template_edit_history_can_restore_builtin_without_touching_notes(tmp_path):
    meeting = templates.list_templates(tmp_path)[0]
    history = History(tmp_path)
    with history.record("Edit template", [meeting.file]):
        edited = templates.save_template(tmp_path, meeting, "Updated\n")
    assert templates.list_templates(tmp_path) == [edited]
    history.undo()
    assert templates.list_templates(tmp_path) == [meeting]
    history.redo()
    assert templates.list_templates(tmp_path) == [edited]
    (tmp_path / edited.file).write_text("External edit", encoding="utf-8")
    with pytest.raises(HistoryConflict):
        history.undo()
    assert (tmp_path / edited.file).read_text(encoding="utf-8") == "External edit"


@pytest.mark.parametrize("change", ["changed", "removed", "appeared"])
def test_stale_draft_or_unexpected_file_is_never_overwritten(tmp_path, change):
    template = templates.list_templates(tmp_path)[0]
    path = tmp_path / template.file
    if change != "appeared":
        template = templates.save_template(tmp_path, template, "Before")
    if change == "removed":
        path.unlink()
        expected = None
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"External content")
        expected = path.read_bytes()
    with pytest.raises(templates.TemplateConflict):
        templates.save_template(tmp_path, template, "Stale editor")
    assert (path.read_bytes() if path.exists() else None) == expected


def test_case_variant_appearing_after_builtin_discovery_is_not_duplicated(tmp_path):
    template = templates.list_templates(tmp_path)[0]
    path = custom(tmp_path, "MEETING.md", "External")
    with pytest.raises(templates.TemplateConflict):
        templates.save_template(tmp_path, template, "Stale")
    assert path.read_text(encoding="utf-8") == "External"
    assert len(list(path.parent.glob("*.md"))) == 1


def test_noop_save_preserves_bytes_mtime_bom_and_newline_style(tmp_path):
    path = custom(tmp_path, "Meeting.md", "unused")
    path.write_bytes(b"\xef\xbb\xbfDate: {{date}}\r\nBody\r\n")
    template = templates.list_templates(tmp_path)[0]
    assert template.body == "Date: {{date}}\nBody\n"
    before, modified = path.read_bytes(), path.stat().st_mtime_ns
    assert templates.save_template(tmp_path, template, template.body) == template
    assert path.read_bytes() == before and path.stat().st_mtime_ns == modified
    updated = templates.save_template(tmp_path, template, "Changed\nBody\n")
    assert updated.body == "Changed\nBody\n"
    assert path.read_bytes() == b"\xef\xbb\xbfChanged\r\nBody\r\n"


def test_external_edit_during_staging_is_preserved_and_temp_removed(tmp_path, monkeypatch):
    template = templates.save_template(tmp_path, templates.MEETING_TEMPLATE, "Original")
    path = tmp_path / template.file
    original_chmod = os.chmod

    def edit_during_stage(target, mode):
        original_chmod(target, mode)
        path.write_bytes(b"External concurrent edit")

    monkeypatch.setattr(templates.os, "chmod", edit_during_stage)
    with pytest.raises(templates.TemplateConflict):
        templates.save_template(tmp_path, template, "My edit")
    assert path.read_bytes() == b"External concurrent edit"
    assert not list(path.parent.glob(".taskman-template-*"))


@pytest.mark.parametrize("file", [
    "Notes/Meeting.md", "../Meeting.md", ".taskman/templates/notes/../Other.md",
    ".taskman/templates/notes/Nested/Meeting.md", ".taskman/templates/notes/CON.md",
    ".taskman/templates/notes/Bad?.md", ".taskman/templates/notes/Meeting.txt",
    "C:/Meeting.md",
])
def test_template_save_rejects_nonportable_or_out_of_scope_paths_without_writes(tmp_path, file):
    with pytest.raises(ValueError):
        templates.save_template(tmp_path, replace(templates.MEETING_TEMPLATE, file=file), "Body")
    assert list(tmp_path.iterdir()) == []


def test_linked_template_is_not_followed(tmp_path):
    target = tmp_path / "Elsewhere.md"
    target.write_text("Outside template directory", encoding="utf-8")
    folder = tmp_path / templates.TEMPLATE_DIR
    folder.mkdir(parents=True)
    try:
        (folder / "Meeting.md").symlink_to(target)
    except OSError:
        pytest.skip("Creating symlinks is unavailable")
    with pytest.raises(ValueError):
        templates.list_templates(tmp_path)
    with pytest.raises(ValueError):
        templates.save_template(tmp_path, templates.MEETING_TEMPLATE, "Unsafe")
    assert target.read_text(encoding="utf-8") == "Outside template directory"


@pytest.mark.parametrize("raw", [b"\xff\xfe", b"bad\x00body"])
def test_invalid_template_text_is_reported_without_rewriting(tmp_path, raw):
    path = custom(tmp_path, "Meeting.md", "unused")
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        templates.list_templates(tmp_path)
    assert path.read_bytes() == raw


def test_instantiation_only_replaces_the_date_token():
    template = replace(templates.MEETING_TEMPLATE, body="{{date}} {{name}} {{date}} {date} $(danger)")
    assert templates.instantiate(template, dt.date(2026, 9, 7)) == (
        "2026-09-07 {{name}} 2026-09-07 {date} $(danger)")


def test_templates_are_not_discovered_as_tasks_or_reference_notes(tmp_path):
    templates.save_template(tmp_path, templates.MEETING_TEMPLATE,
                            "# Reusable\n- [ ] Template action 📅 2026-09-07\n")
    tasks = taskman.Store(tmp_path)
    assert tasks.refresh() == []
    assert notes.NotesStore(tmp_path).refresh() == []


def test_builtin_capture_creates_only_a_reference_note_without_placeholder_tasks(tmp_path):
    template = templates.list_templates(tmp_path)[0]
    body = templates.instantiate(template, dt.date(2026, 9, 7))
    created = notes.NotesStore(tmp_path).create("Meeting", body)
    assert "- Action — owner — due date" in created.body
    assert taskman.Store(tmp_path).refresh() == []
    assert [note.file for note in notes.NotesStore(tmp_path).refresh()] == [created.file]
