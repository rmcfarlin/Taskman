"""Durable task identities stay hidden and survive normal Markdown changes."""
import datetime as dt
import re

import pytest

from tui import taskman as tm


ANCHOR = "0123456789abcdef0123456789abcdef"
OTHER = "fedcba9876543210fedcba9876543210"


def _marker(anchor=ANCHOR):
    return f"<!-- taskman:id={anchor} -->"


def _file(root, text, name="Tasks/Inbox.md"):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return path


def _only(root):
    tasks = tm.load_all(root)
    assert len(tasks) == 1
    return tasks[0]


def test_parser_hides_anchor_without_changing_location_id_or_task_metadata():
    line = f"  * [/] Review Ω [[Plan]] {_marker()} 🔼 📅 2026-09-10 #work #project/Alpha"
    task = tm.parse_task_line(line, "Tasks/Inbox.md", 12)
    assert task.anchor == ANCHOR
    assert task.id == "Tasks/Inbox.md:12"
    assert task.description == "Review Ω [[Plan]]"
    assert task.tags == ("work", "project/Alpha")
    assert task.priority == 3 and task.due == dt.date(2026, 9, 10)
    assert "taskman:id" not in task.short()
    rebuilt = tm.build_line(task)
    assert rebuilt.count(_marker()) == 1
    again = tm.parse_task_line(rebuilt)
    assert again.anchor == ANCHOR and again.description == task.description
    assert again.tags == task.tags and again.status == "/"


@pytest.mark.parametrize("invalid", ["A" * 32, "a" * 31, "a" * 33,
                                    "01234567-89ab-cdef-0123-456789abcdef"])
def test_invalid_anchor_comments_remain_literal_text(invalid):
    comment = _marker(invalid)
    task = tm.parse_task_line(f"- [ ] Keep this {comment}")
    assert task.anchor == ""
    assert comment in task.description
    assert comment in tm.build_line(task)


def test_unanchored_reads_and_creation_never_generate_ids(tmp_path):
    path = _file(tmp_path, "# Inbox\n\n- [ ] Existing #work\n")
    before = path.read_bytes()
    assert _only(tmp_path).anchor == ""
    assert path.read_bytes() == before
    added = tm.add_task(tmp_path, "New task")
    assert added.anchor == ""
    assert "taskman:id" not in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("ending", ["\n", "\r\n", ""])
def test_lazy_anchor_preserves_original_bytes_and_returns_fresh_task(tmp_path, ending):
    original = "# Keep spacing\r\n\r\n* [ ] Keep   this #work  " + ending
    path = _file(tmp_path, original)
    task = _only(tmp_path)
    anchored = tm.ensure_task_anchor(tmp_path, task)
    assert re.fullmatch(r"[0-9a-f]{32}", anchored.anchor)
    assert anchored is not task and task.anchor == ""
    assert anchored.id == task.id
    assert anchored.description == "Keep this" and anchored.tags == ("work",)
    expected = original[:len(original) - len(ending)] if ending else original
    expected += _marker(anchored.anchor) + ending
    assert path.read_bytes() == expected.encode("utf-8")
    assert anchored.raw.endswith(_marker(anchored.anchor))
    before = path.read_bytes()
    assert tm.ensure_task_anchor(tmp_path, anchored).anchor == anchored.anchor
    assert path.read_bytes() == before


def test_anchor_created_after_add_can_be_resolved_immediately(tmp_path):
    task = tm.add_task(tmp_path, "Converted from a note #work")
    assert task.raw == "" and task.anchor == ""
    anchored = tm.ensure_task_anchor(tmp_path, task)
    found = tm.find_task_by_anchor(tm.load_all(tmp_path), anchored.anchor)
    assert found.id == task.id and found.description == "Converted from a note"


def test_anchoring_child_returns_current_hierarchy_and_note(tmp_path):
    _file(tmp_path, "- [ ] Parent\n  - [ ] Child\n    Keep this note.\n")
    child = tm.load_all(tmp_path)[1]
    anchored = tm.ensure_task_anchor(tmp_path, child)
    assert anchored.depth == 1 and anchored.parent_lineno == 1
    assert anchored.note == "Keep this note." and anchored.note_end == 3


@pytest.mark.parametrize("changed", ["- [ ] Different task\n", "# Inserted\n- [ ] Original\n", ""])
def test_stale_location_or_raw_line_never_overwrites_current_file(tmp_path, changed):
    path = _file(tmp_path, "- [ ] Original\n")
    old = _only(tmp_path)
    path.write_bytes(changed.encode())
    with pytest.raises(ValueError, match="changed"):
        tm.ensure_task_anchor(tmp_path, old)
    assert path.read_bytes() == changed.encode()


def test_change_during_anchor_preparation_is_detected(tmp_path, monkeypatch):
    path = _file(tmp_path, "- [ ] Original\n")
    task = _only(tmp_path)
    original_loader = tm.load_all

    def edit_during_scan(root):
        tasks = original_loader(root)
        path.write_text("- [ ] External edit\n", encoding="utf-8")
        return tasks

    monkeypatch.setattr(tm, "load_all", edit_during_scan)
    with pytest.raises(ValueError, match="file changed"):
        tm.ensure_task_anchor(tmp_path, task)
    assert path.read_text(encoding="utf-8") == "- [ ] External edit\n"


def test_failed_atomic_anchor_write_preserves_file_and_cleans_temporary_file(tmp_path, monkeypatch):
    path = _file(tmp_path, "- [ ] Original\n")
    task = _only(tmp_path)
    before = path.read_bytes()

    def fail_replace(source, destination):
        raise OSError("Simulated replacement failure")

    monkeypatch.setattr(tm.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replacement failure"):
        tm.ensure_task_anchor(tmp_path, task)
    assert path.read_bytes() == before and task.anchor == ""
    assert list(path.parent.iterdir()) == [path]


def test_anchor_cannot_write_outside_vault(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside.md"
    outside.write_text("- [ ] Outside\n", encoding="utf-8")
    task = tm.parse_task_line("- [ ] Outside", f"../{outside.name}", 1)
    with pytest.raises(ValueError, match="inside the vault"):
        tm.ensure_task_anchor(tmp_path, task)
    assert outside.read_text(encoding="utf-8") == "- [ ] Outside\n"


def test_duplicate_anchors_never_choose_an_arbitrary_task(tmp_path):
    first = _file(tmp_path, f"- [ ] First {_marker()}\n")
    second = _file(tmp_path, f"- [ ] Second {_marker()}\n", "Projects/Other.md")
    tasks = tm.load_all(tmp_path)
    before = (first.read_bytes(), second.read_bytes())
    with pytest.raises(ValueError, match="Duplicate task anchor"):
        tm.find_task_by_anchor(iter(tasks), ANCHOR)
    with pytest.raises(ValueError, match="Duplicate task anchor"):
        tm.ensure_task_anchor(tmp_path, tasks[0])
    assert (first.read_bytes(), second.read_bytes()) == before
    assert tm.find_task_by_anchor(tasks, "") is None
    assert tm.find_task_by_anchor(tasks, OTHER) is None


def test_conflicting_markers_on_one_line_are_not_silently_resolved_or_removed(tmp_path):
    path = _file(tmp_path, f"- [ ] Ambiguous {_marker()} {_marker(OTHER)}\n")
    task = _only(tmp_path)
    assert task.anchor == ""
    assert _marker() in tm.build_line(task) and _marker(OTHER) in tm.build_line(task)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="Conflicting task anchors"):
        tm.find_task_by_anchor([task], ANCHOR)
    with pytest.raises(ValueError, match="conflicting anchors"):
        tm.ensure_task_anchor(tmp_path, task)
    assert path.read_bytes() == before


@pytest.mark.parametrize("operation", [
    lambda root, task: tm.edit_text(root, task, "Edited title"),
    lambda root, task: tm.edit_text(root, task, f"Edited title {_marker(OTHER)}"),
    lambda root, task: tm.set_status(root, task, "/"),
    lambda root, task: tm.set_status(root, task, "x", dt.date(2026, 9, 5)),
    lambda root, task: tm.set_due(root, task, dt.date(2026, 9, 10)),
    lambda root, task: tm.set_priority(root, task, 4),
    lambda root, task: tm.set_project(root, task, "Beta"),
    lambda root, task: tm.link_project(root, task, "Beta"),
    lambda root, task: tm.link_note(root, task, "Related note"),
    lambda root, task: tm.set_note(root, task, "Longer note\nSecond line"),
    lambda root, task: tm.toggle(root, task, dt.date(2026, 9, 5)),
])
def test_normal_mutations_preserve_anchor(tmp_path, operation):
    path = _file(tmp_path, f"- [ ] Original #work {_marker()}\n  Existing note\n")
    task = _only(tmp_path)
    operation(tmp_path, task)
    found = tm.find_task_by_anchor(tm.load_all(tmp_path), ANCHOR)
    assert found is not None
    assert found.anchor == ANCHOR and "taskman:id" not in found.description
    assert path.read_text(encoding="utf-8").count(_marker()) == 1
    assert _marker(OTHER) not in path.read_text(encoding="utf-8")


def test_hierarchy_changes_preserve_parent_child_anchors(tmp_path):
    _file(tmp_path, f"- [ ] Previous\n- [ ] Branch {_marker()}\n"
          f"  - [ ] Child {_marker(OTHER)}\n    Child note\n")
    task = tm.find_task_by_anchor(tm.load_all(tmp_path), ANCHOR)
    assert tm.indent_task(tmp_path, task) is not None
    tasks = tm.load_all(tmp_path)
    branch = tm.find_task_by_anchor(tasks, ANCHOR)
    child = tm.find_task_by_anchor(tasks, OTHER)
    assert branch.depth == 1 and child.depth == 2 and child.note == "Child note"
    tm.toggle(tmp_path, branch, dt.date(2026, 9, 5))
    tasks = tm.load_all(tmp_path)
    assert tm.find_task_by_anchor(tasks, ANCHOR).done
    assert tm.find_task_by_anchor(tasks, OTHER).done
    assert tm.outdent_task(tmp_path, tm.find_task_by_anchor(tasks, ANCHOR)) is not None
    tasks = tm.load_all(tmp_path)
    assert tm.find_task_by_anchor(tasks, ANCHOR).depth == 0
    assert tm.find_task_by_anchor(tasks, OTHER).depth == 1


def test_anchor_resolves_after_line_shift_and_file_move(tmp_path):
    path = _file(tmp_path, f"- [ ] Stable task {_marker()}\n  Its note\n")
    original = _only(tmp_path)
    path.write_text("# Added heading\n\n" + path.read_text(encoding="utf-8"), encoding="utf-8")
    destination = tmp_path / "Projects" / "Moved.md"
    destination.parent.mkdir()
    path.rename(destination)
    moved = tm.find_task_by_anchor(tm.load_all(tmp_path), ANCHOR)
    assert moved.file == "Projects/Moved.md" and moved.lineno == 3
    assert moved.id != original.id and moved.note == "Its note"
    assert tm.ensure_task_anchor(tmp_path, moved).anchor == ANCHOR
