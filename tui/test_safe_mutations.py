"""Task references and atomic mutations remain safe when Markdown changes."""
import datetime as dt
from dataclasses import replace
from pathlib import Path
import subprocess
import sys

import pytest

from tui import taskman as tm


DAY = dt.date(2026, 9, 6)
ANCHOR = "0123456789abcdef0123456789abcdef"
OTHER = "fedcba9876543210fedcba9876543210"


def marker(anchor=ANCHOR):
    return f"<!-- taskman:id={anchor} -->"


def task_file(root, text=None, name="Tasks/Inbox.md"):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((text or f"- [ ] Original {marker()}\n  Keep note.\n").encode("utf-8"))
    return path


def only(root):
    return tm.load_all(root)[0]


def test_resolve_stable_and_legacy_identifiers_without_writing(tmp_path):
    path = task_file(tmp_path)
    before = path.read_bytes()
    assert tm.resolve_task(tmp_path, ANCHOR).id == "Tasks/Inbox.md:1"
    assert tm.resolve_task(tmp_path, "Tasks/Inbox.md:1").anchor == ANCHOR
    assert path.read_bytes() == before
    assert not (tmp_path / ".taskman").exists()


@pytest.mark.parametrize("identifier", ["", "unknown", "a" * 32, "A" * 32,
    "Tasks/Inbox.md:0", "Tasks/Inbox.md:-1", "Tasks/Inbox.md:1.0", "Tasks/Inbox.md:99",
    "../outside.md:1", "C:/outside.md:1", "Tasks/Inbox.txt:1", "Tasks/Inbox.md:１"])
def test_bad_or_missing_identifiers_do_not_pick_a_task(tmp_path, identifier):
    path = task_file(tmp_path)
    before = path.read_bytes()
    with pytest.raises(ValueError):
        tm.resolve_task(tmp_path, identifier)
    assert path.read_bytes() == before


def test_relative_vault_paths_work(tmp_path, monkeypatch):
    task_file(tmp_path)
    monkeypatch.chdir(tmp_path.parent)
    task = tm.resolve_task(Path(tmp_path.name), ANCHOR)
    tm.set_scheduled(tmp_path.name, task, DAY)
    assert only(tmp_path).scheduled == DAY


@pytest.mark.parametrize("operation", [
    lambda root, task: tm.set_dates(root, task, DAY, DAY),
    lambda root, task: tm.set_priority(root, task, 4),
    lambda root, task: tm.edit_text(root, task, "Changed title"),
    lambda root, task: tm.set_note(root, task, "Changed note"),
    lambda root, task: tm.set_status(root, task, "/"),
    lambda root, task: tm.complete(root, task, DAY),
    lambda root, task: tm.set_project(root, task, "Next"),
])
def test_mutations_follow_stable_id_after_shift_and_move(tmp_path, operation):
    path = task_file(tmp_path)
    task = only(tmp_path)
    path.write_bytes(b"# New heading\r\n\r\n" + path.read_bytes())
    destination = tmp_path / "Moved.md"
    path.rename(destination)
    result = operation(tmp_path, task)
    assert result is task
    assert task.file == "Moved.md" and task.lineno == 3 and task.anchor == ANCHOR
    assert tm.resolve_task(tmp_path, ANCHOR).raw == task.raw
    assert destination.read_bytes().startswith(b"# New heading\r\n\r\n")


@pytest.mark.parametrize("operation", [
    lambda root, task: tm.set_dates(root, task, DAY, DAY),
    lambda root, task: tm.set_priority(root, task, 4),
    lambda root, task: tm.edit_text(root, task, "Our title"),
    lambda root, task: tm.set_note(root, task, "Our note"),
    lambda root, task: tm.set_status(root, task, "/"),
    lambda root, task: tm.complete(root, task, DAY),
    lambda root, task: tm.delete_task(root, task),
    lambda root, task: tm.add_subtask(root, task, "New child"),
    lambda root, task: tm.indent_task(root, task),
])
@pytest.mark.parametrize("change", ["title", "note"])
def test_stale_task_or_note_is_rejected_without_mutating_handle_or_file(tmp_path, operation, change):
    path = task_file(tmp_path)
    task = only(tmp_path)
    snapshot = replace(task)
    content = path.read_text(encoding="utf-8")
    content = content.replace("Original", "External title") if change == "title" else content.replace("Keep note.", "External note.")
    path.write_text(content, encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="changed"):
        operation(tmp_path, task)
    assert path.read_bytes() == before and task == snapshot


def test_legacy_location_shift_is_rejected_instead_of_editing_neighbor(tmp_path):
    path = task_file(tmp_path, "- [ ] Original\n")
    task = only(tmp_path)
    path.write_text("- [ ] New neighbor\n- [ ] Original\n", encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="changed"):
        tm.set_due(tmp_path, task, DAY)
    assert path.read_bytes() == before


@pytest.mark.parametrize("identifier", [ANCHOR, "Tasks/Inbox.md:1"])
def test_duplicate_ids_fail_even_when_resolving_by_location(tmp_path, identifier):
    first = task_file(tmp_path)
    second = task_file(tmp_path, f"- [ ] Copy {marker()}\n", "Copy.md")
    before = (first.read_bytes(), second.read_bytes())
    with pytest.raises(ValueError, match="Duplicate"):
        tm.resolve_task(tmp_path, identifier)
    with pytest.raises(ValueError, match="Duplicate"):
        tm.set_due(tmp_path, only(tmp_path), DAY)
    assert (first.read_bytes(), second.read_bytes()) == before


def test_new_task_and_subtask_have_unique_persistent_ids(tmp_path):
    first = tm.add_task(tmp_path, "Parent")
    second = tm.add_subtask(tmp_path, first, "Child")
    assert first.raw and second.raw and first.anchor != second.anchor
    assert tm.resolve_task(tmp_path, first.anchor).description == "Parent"
    assert tm.resolve_task(tmp_path, second.anchor).parent_lineno == first.lineno


def test_explicit_unused_id_is_preserved_and_duplicates_or_conflicts_fail(tmp_path):
    created = tm.add_task(tmp_path, f"Imported {marker()}")
    assert created.anchor == ANCHOR
    path = tmp_path / created.file
    before = path.read_bytes()
    for text in (f"Duplicate {marker()}", f"Conflict {marker()} {marker(OTHER)}"):
        with pytest.raises(ValueError, match="Duplicate|conflicting"):
            tm.add_task(tmp_path, text)
        with pytest.raises(ValueError, match="Duplicate|conflicting"):
            tm.add_subtask(tmp_path, created, text)
        assert path.read_bytes() == before


def test_dates_save_together_preserving_metadata_notes_and_unrelated_bytes(tmp_path, monkeypatch):
    path = task_file(tmp_path, f"# Keep\r\n- [/] Original 🛫 2026-09-01 #work {marker()}\r\n  Keep note.\n- [ ] Neighbor")
    task = only(tmp_path)
    replacements = []
    original_replace = tm.os.replace

    def count(source, destination):
        replacements.append(destination)
        return original_replace(source, destination)

    monkeypatch.setattr(tm.os, "replace", count)
    tm.set_dates(tmp_path, task, DAY, DAY + dt.timedelta(days=2))
    assert len(replacements) == 1
    assert task.due == DAY and task.scheduled == DAY + dt.timedelta(days=2)
    assert task.status == "/" and task.start == dt.date(2026, 9, 1)
    assert task.tags == ("work",) and task.note == "Keep note."
    assert path.read_bytes().endswith(b"\r\n  Keep note.\n- [ ] Neighbor")
    tm.set_due(tmp_path, task, None)
    assert task.scheduled == DAY + dt.timedelta(days=2)
    tm.set_scheduled(tmp_path, task, None)
    assert task.due is None and task.scheduled is None


def test_invalid_second_date_never_saves_first_date(tmp_path):
    path = task_file(tmp_path)
    task = only(tmp_path)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="Dates"):
        tm.set_dates(tmp_path, task, DAY, "invalid")
    assert task.due is None and path.read_bytes() == before


def test_explicit_create_dates_override_inline_values_including_clear(tmp_path):
    task = tm.add_task(tmp_path, "Plan 📅 2026-10-01 ⏳ 2026-10-02", due=None, scheduled=DAY)
    assert task.due is None and task.scheduled == DAY
    child = tm.add_subtask(tmp_path, task, "Child 📅 2026-10-01 ⏳ 2026-10-02", due=DAY, scheduled=None)
    assert child.due == DAY and child.scheduled is None
    inline = tm.add_task(tmp_path, "Inline 📅 2026-10-01 ⏳ 2026-10-02")
    assert inline.due == dt.date(2026, 10, 1) and inline.scheduled == dt.date(2026, 10, 2)


def test_completion_is_idempotent_and_status_picker_uses_same_completion_rules(tmp_path):
    path = task_file(tmp_path, f"- [ ] Parent {marker()}\n  - [ ] Child\n  - [-] Cancelled ❌ 2026-09-01\n")
    task = only(tmp_path)
    tm.complete(tmp_path, task, DAY)
    before = path.read_bytes()
    tm.complete(tmp_path, task, DAY + dt.timedelta(days=1))
    tm.set_status(tmp_path, task, "x", DAY + dt.timedelta(days=2))
    assert path.read_bytes() == before
    assert task.done_date == DAY
    tasks = tm.load_all(tmp_path)
    assert tasks[1].done and tasks[2].cancelled
    # Status picker only changes the selected checkbox, preserving existing UI semantics.
    tm.set_status(tmp_path, task, " ")
    assert task.open and tm.load_all(tmp_path)[1].done
    tm.set_status(tmp_path, task, "x", DAY)
    assert task.done_date == DAY


def test_external_edit_while_staging_is_rejected_and_temp_is_cleaned(tmp_path, monkeypatch):
    path = task_file(tmp_path)
    task = only(tmp_path)
    old_chmod = tm.os.chmod
    external = f"- [ ] External edit {marker()}\n"

    def intervene(filename, mode):
        old_chmod(filename, mode)
        path.write_text(external, encoding="utf-8")

    monkeypatch.setattr(tm.os, "chmod", intervene)
    with pytest.raises(ValueError, match="file changed"):
        tm.set_dates(tmp_path, task, DAY, DAY)
    assert path.read_text(encoding="utf-8") == external
    assert list(path.parent.iterdir()) == [path]
    assert task.due is None


def test_failed_replacement_leaves_original_and_handle_unchanged(tmp_path, monkeypatch):
    path = task_file(tmp_path)
    task = only(tmp_path)
    before = path.read_bytes()

    def fail(*args):
        raise OSError("disk failure")

    monkeypatch.setattr(tm.os, "replace", fail)
    with pytest.raises(OSError, match="disk failure"):
        tm.set_dates(tmp_path, task, DAY, DAY)
    assert path.read_bytes() == before and task.due is None and task.scheduled is None
    assert list(path.parent.iterdir()) == [path]


def test_failed_staging_fsync_closes_and_removes_temporary_file(tmp_path, monkeypatch):
    path = task_file(tmp_path)
    task = only(tmp_path)
    before = path.read_bytes()

    def fail(_fd):
        raise OSError("flush failed")

    monkeypatch.setattr(tm.os, "fsync", fail)
    with pytest.raises(OSError, match="flush failed"):
        tm.set_dates(tmp_path, task, DAY, DAY)
    assert path.read_bytes() == before and task.due is None
    assert list(path.parent.iterdir()) == [path]


def test_writer_lock_is_reentrant_and_coordinates_another_process(tmp_path):
    code = "from tui import taskman as t; import sys; print('ready', flush=True); t.add_task(sys.argv[1], 'Child process')"
    process = None
    try:
        with tm.vault_write_lock(tmp_path):
            with tm.vault_write_lock(tmp_path):
                process = subprocess.Popen([sys.executable, "-c", code, str(tmp_path)],
                    cwd=Path(__file__).resolve().parents[1], stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True)
                assert process.stdout.readline().strip() == "ready"
                tm.add_task(tmp_path, "Parent process")
                with pytest.raises(subprocess.TimeoutExpired):
                    process.wait(timeout=0.15)
        output, errors = process.communicate(timeout=15)
        assert process.returncode == 0, (output, errors)
        assert {task.description for task in tm.load_all(tmp_path)} == {"Parent process", "Child process"}
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.communicate()
