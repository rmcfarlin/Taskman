"""Safety and behavior checks for session-local undo/redo."""

import os
from pathlib import Path

import pytest

from tui.history import History, HistoryConflict


def change(history, path, value, label="Edit", context=None):
    with history.record(label, [path.name], context=context):
        path.write_bytes(value)


def test_exact_bytes_and_context_round_trip(tmp_path):
    path = tmp_path / "inbox.md"
    original = "\ufeff- [ ] Ship it 🔺\r\n  Résumé\r\n".encode("utf-8")
    revised = original.replace(b"[ ]", b"[x]")
    path.write_bytes(original)
    history = History(tmp_path)
    context = {"view": "today", "selection": ("inbox.md", 1)}
    change(history, path, revised, "Complete task", context)
    assert history.can_undo and history.undo_label == "Complete task"
    entry = history.undo()
    assert entry.label == "Complete task" and entry.context is context
    assert path.read_bytes() == original
    assert not history.can_undo and history.redo_label == "Complete task"
    assert history.redo() is entry
    assert path.read_bytes() == revised
    assert not history.can_redo


def test_successive_actions_and_bounded_history(tmp_path):
    path = tmp_path / "inbox.md"
    path.write_bytes(b"0")
    history = History(tmp_path, limit=3)
    for number in range(1, 5):
        change(history, path, str(number).encode(), str(number))
    for number in (3, 2, 1):
        history.undo()
        assert path.read_bytes() == str(number).encode()
    assert history.undo() is None
    for number in (2, 3, 4):
        history.redo()
        assert path.read_bytes() == str(number).encode()
    assert history.redo() is None


def test_noop_preserves_redo_and_branch_edit_clears_it(tmp_path):
    path = tmp_path / "inbox.md"
    path.write_bytes(b"before")
    history = History(tmp_path)
    change(history, path, b"after")
    history.undo()
    change(history, path, b"before", "No change")
    assert not history.can_undo and history.can_redo
    change(history, path, b"branch", "New edit")
    assert not history.can_redo and history.undo_label == "New edit"


def test_creation_deletion_and_new_project_in_one_action(tmp_path):
    inbox = tmp_path / "inbox.md"
    project = tmp_path / "Projects" / "New.md"
    inbox.write_bytes(b"Task to move\r\n")
    history = History(tmp_path)
    with history.record("Move", ["inbox.md", "Projects/New.md"]):
        inbox.unlink()
        project.parent.mkdir()
        project.write_bytes(b"# New\r\nTask moved\r\n")
    history.undo()
    assert inbox.read_bytes() == b"Task to move\r\n" and not project.exists()
    project.parent.rmdir()
    history.redo()
    assert not inbox.exists() and project.read_bytes() == b"# New\r\nTask moved\r\n"


@pytest.mark.parametrize("external", [b"external edit", None])
def test_conflict_in_later_file_never_changes_any_file(tmp_path, external):
    first, second = tmp_path / "first.md", tmp_path / "second.md"
    first.write_bytes(b"first before")
    second.write_bytes(b"second before")
    history = History(tmp_path)
    with history.record("Both", ["first.md", "second.md"]):
        first.write_bytes(b"first after")
        second.write_bytes(b"second after")
    if external is None:
        second.unlink()
    else:
        second.write_bytes(external)
    with pytest.raises(HistoryConflict, match="second.md"):
        history.undo()
    assert first.read_bytes() == b"first after"
    assert history.can_undo and not history.can_redo
    assert (second.read_bytes() if second.exists() else None) == external


def test_redo_refuses_external_edit_and_can_retry_when_restored(tmp_path):
    path = tmp_path / "inbox.md"
    path.write_bytes(b"before")
    history = History(tmp_path)
    change(history, path, b"after")
    history.undo()
    path.write_bytes(b"external")
    with pytest.raises(HistoryConflict):
        history.redo()
    assert path.read_bytes() == b"external" and history.can_redo
    path.write_bytes(b"before")
    history.redo()
    assert path.read_bytes() == b"after"


def test_redo_creation_refuses_external_replacement(tmp_path):
    path = tmp_path / "new.md"
    history = History(tmp_path)
    change(history, path, b"created")
    history.undo()
    path.write_bytes(b"someone else's file")
    with pytest.raises(HistoryConflict):
        history.redo()
    assert path.read_bytes() == b"someone else's file"


def test_unchanged_declared_file_is_not_part_of_undo(tmp_path):
    first, second = tmp_path / "first.md", tmp_path / "second.md"
    first.write_bytes(b"before")
    second.write_bytes(b"unchanged")
    history = History(tmp_path)
    with history.record("One", ["first.md", "first.md", "second.md"]):
        first.write_bytes(b"after")
    second.write_bytes(b"external")
    entry = history.undo()
    assert len(entry.changes) == 1 and first.read_bytes() == b"before"
    assert second.read_bytes() == b"external"


@pytest.mark.parametrize("relative", ["", ".", "..", "../outside.md", "folder/../../x",
                                      "/outside.md", "C:/outside.md", "C:relative.md",
                                      "inbox.md:stream", "trailing./x", ".. /x",
                                      "\\\\server\\share\\file.md"])
def test_unsafe_paths_rejected_before_mutation(tmp_path, relative):
    history = History(tmp_path)
    entered = False
    with pytest.raises(ValueError):
        with history.record("Unsafe", [relative]):
            entered = True
    assert not entered and not history.can_undo


def make_symlink(link, target, directory=False):
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError:
        pytest.skip("Creating symlinks requires OS support or elevated privileges")


def test_symlink_escape_rejected_before_read(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "task.md").write_bytes(b"private")
    make_symlink(vault / "linked", outside, directory=True)
    with pytest.raises(ValueError):
        with History(vault).record("Unsafe", ["linked/task.md"]):
            pytest.fail("Mutation must not run")
    assert (outside / "task.md").read_bytes() == b"private"


def test_file_swapped_for_symlink_causes_conflict(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    path = vault / "inbox.md"
    path.write_bytes(b"before")
    history = History(vault)
    change(history, path, b"after")
    outside = tmp_path / "outside.md"
    outside.write_bytes(b"after")
    path.unlink()
    make_symlink(path, outside)
    with pytest.raises(HistoryConflict):
        history.undo()
    assert outside.read_bytes() == b"after" and path.is_symlink()


@pytest.mark.skipif(os.name != "nt", reason="Windows junction safety")
def test_junction_escape_and_later_parent_swap(tmp_path):
    import _winapi

    vault, outside = tmp_path / "vault", tmp_path / "outside"
    vault.mkdir()
    outside.mkdir()
    (outside / "task.md").write_bytes(b"after")
    linked = vault / "linked"
    _winapi.CreateJunction(str(outside), str(linked))
    try:
        with pytest.raises(ValueError):
            with History(vault).record("Unsafe", ["linked/task.md"]):
                pytest.fail("Mutation must not run")
    finally:
        linked.rmdir()

    linked.mkdir()
    (linked / "task.md").write_bytes(b"before")
    history = History(vault)
    with history.record("Edit", ["linked/task.md"]):
        (linked / "task.md").write_bytes(b"after")
    linked.rename(vault / "original")
    _winapi.CreateJunction(str(outside), str(linked))
    try:
        with pytest.raises(HistoryConflict):
            history.undo()
        assert (outside / "task.md").read_bytes() == b"after"
        assert (vault / "original" / "task.md").read_bytes() == b"after"
    finally:
        linked.rmdir()


@pytest.mark.skipif(os.name != "nt", reason="Windows file-name aliases")
def test_case_aliases_only_record_a_file_once(tmp_path):
    path = tmp_path / "Task.md"
    path.write_bytes(b"before")
    history = History(tmp_path)
    with history.record("Edit", ["Task.md", "task.md"]):
        path.write_bytes(b"after")
    entry = history.undo()
    assert len(entry.changes) == 1
    assert path.read_bytes() == b"before"


def test_external_file_to_directory_change_is_a_conflict(tmp_path):
    path = tmp_path / "task.md"
    path.write_bytes(b"before")
    history = History(tmp_path)
    change(history, path, b"after")
    path.unlink()
    path.mkdir()
    with pytest.raises(HistoryConflict):
        history.undo()
    assert path.is_dir() and history.can_undo


def test_directories_and_nested_record_are_rejected(tmp_path):
    history = History(tmp_path)
    (tmp_path / "directory").mkdir()
    with pytest.raises(ValueError):
        with history.record("Directory", ["directory"]):
            pass
    with history.record("Outer", []):
        with pytest.raises(RuntimeError):
            with history.record("Inner", []):
                pass
        with pytest.raises(RuntimeError):
            history.undo()


def test_errors_propagate_and_staging_failure_keeps_originals(tmp_path, monkeypatch):
    history = History(tmp_path)
    with pytest.raises(OSError, match="Mutation failed"):
        with history.record("Failed", ["missing.md"]):
            raise OSError("Mutation failed")
    assert not history.can_undo
    path = tmp_path / "inbox.md"
    path.write_bytes(b"before")
    change(history, path, b"after")
    def fail_fsync(fd):
        raise OSError("Disk failed")
    monkeypatch.setattr(os, "fsync", fail_fsync)
    with pytest.raises(OSError, match="Disk failed"):
        history.undo()
    assert path.read_bytes() == b"after" and history.can_undo
    assert not list(tmp_path.glob(".taskman-undo-*"))


def test_only_declared_files_are_read(tmp_path, monkeypatch):
    history = History(tmp_path)
    path = tmp_path / "task.md"
    path.write_bytes(b"before")
    (tmp_path / "unrelated.md").write_bytes(b"leave alone")
    original = Path.read_bytes
    reads = []
    def tracked_read(target):
        reads.append(target.name)
        return original(target)
    monkeypatch.setattr(Path, "read_bytes", tracked_read)
    change(history, path, b"after")
    history.undo()
    history.redo()
    assert reads and set(reads) == {"task.md"}


def two_file_edit(vault):
    first, second = vault / "first.md", vault / "second.md"
    first.write_bytes(b"first before")
    second.write_bytes(b"second before")
    history = History(vault)
    with history.record("Both", ["first.md", "second.md"]):
        first.write_bytes(b"first after")
        second.write_bytes(b"second after")
    return history, first, second


def note_rename(vault):
    old, new = vault / "Old.md", vault / "New.md"
    old.write_bytes(b"Note body")
    history = History(vault)
    with history.record("Rename", ["Old.md", "New.md"]):
        old.rename(new)
    return history, old, new


@pytest.mark.parametrize("redo", [False, True])
def test_later_replace_failure_rolls_back_earlier_write_and_keeps_stacks(tmp_path, monkeypatch, redo):
    history, first, second = two_file_edit(tmp_path)
    if redo:
        history.undo()
    original = first.read_bytes(), second.read_bytes()
    original_modified = first.stat().st_mtime_ns, second.stat().st_mtime_ns
    stacks = list(history._undo), list(history._redo)
    replace = os.replace

    def fail_second(source, destination):
        if Path(destination) == second:
            raise OSError("Second replace failed")
        replace(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail_second)
        with pytest.raises(OSError, match="Second replace failed"):
            (history.redo if redo else history.undo)()
    assert (first.read_bytes(), second.read_bytes()) == original
    assert (first.stat().st_mtime_ns, second.stat().st_mtime_ns) == original_modified
    assert (history._undo, history._redo) == stacks
    assert not list(tmp_path.glob(".taskman-undo-*"))
    (history.redo if redo else history.undo)()
    assert first.read_bytes() == (b"first after" if redo else b"first before")


def test_rename_undo_unlink_failure_removes_its_newly_created_file(tmp_path, monkeypatch):
    history, old, new = note_rename(tmp_path)
    unlink = Path.unlink

    def fail_new(path, *args, **kwargs):
        if path == new:
            raise PermissionError("Cannot remove new filename")
        return unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", fail_new)
        with pytest.raises(PermissionError):
            history.undo()
    assert not old.exists() and new.read_bytes() == b"Note body"
    assert history.can_undo and not history.can_redo
    assert not list(tmp_path.glob(".taskman-undo-*"))
    history.undo()
    assert old.read_bytes() == b"Note body" and not new.exists()


def test_rename_redo_creation_failure_restores_its_deleted_file(tmp_path, monkeypatch):
    history, old, new = note_rename(tmp_path)
    history.undo()
    link = os.link

    def fail_new(source, destination, *args, **kwargs):
        if Path(destination) == new:
            raise OSError("Cannot create new filename")
        return link(source, destination, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, "link", fail_new)
        with pytest.raises(OSError, match="Cannot create new filename"):
            history.redo()
    assert old.read_bytes() == b"Note body" and not new.exists()
    assert history.can_redo and not history.can_undo
    assert not list(tmp_path.glob(".taskman-undo-*"))
    history.redo()
    assert new.read_bytes() == b"Note body" and not old.exists()


def test_external_change_during_staging_prevents_every_restore_write(tmp_path, monkeypatch):
    history, first, second = two_file_edit(tmp_path)
    fsync = os.fsync
    changed = False

    def external_edit(fd):
        nonlocal changed
        fsync(fd)
        if not changed:
            changed = True
            second.write_bytes(b"Edited during staging")

    monkeypatch.setattr(os, "fsync", external_edit)
    with pytest.raises(HistoryConflict, match="second.md"):
        history.undo()
    assert first.read_bytes() == b"first after"
    assert second.read_bytes() == b"Edited during staging"
    assert history.can_undo and not history.can_redo
    assert not list(tmp_path.glob(".taskman-undo-*"))


def test_external_change_to_next_target_during_apply_rolls_back_previous_target(tmp_path, monkeypatch):
    history, first, second = two_file_edit(tmp_path)
    replace = os.replace
    changed = False

    def external_edit(source, destination):
        nonlocal changed
        replace(source, destination)
        if Path(destination) == first and not changed:
            changed = True
            second.write_bytes(b"Edited between writes")

    monkeypatch.setattr(os, "replace", external_edit)
    with pytest.raises(HistoryConflict, match="second.md"):
        history.undo()
    assert first.read_bytes() == b"first after"
    assert second.read_bytes() == b"Edited between writes"
    assert history.can_undo and not history.can_redo
    assert not list(tmp_path.glob(".taskman-undo-*"))


@pytest.mark.parametrize("same_bytes", [False, True])
def test_rollback_preserves_external_edit_or_replacement_of_already_applied_file(tmp_path, monkeypatch, same_bytes):
    history, first, second = two_file_edit(tmp_path)
    replace = os.replace
    external = b"first before" if same_bytes else b"External replacement"
    changed = False

    def external_edit(source, destination):
        nonlocal changed
        replace(source, destination)
        if Path(destination) == second and not changed:
            changed = True
            replacement = tmp_path / "external-copy.tmp"
            replacement.write_bytes(external)
            replace(replacement, first)

    monkeypatch.setattr(os, "replace", external_edit)
    with pytest.raises(HistoryConflict, match="Recovery copies"):
        history.undo()
    assert first.read_bytes() == external
    assert second.read_bytes() == b"second after"
    assert history.can_undo and not history.can_redo
    copies = list(tmp_path.glob(".taskman-undo-*"))
    assert len(copies) == 1 and copies[0].read_bytes() == b"first after"


def test_rollback_io_failure_keeps_recovery_bytes_and_history_entry(tmp_path, monkeypatch):
    history, first, second = two_file_edit(tmp_path)
    replace = os.replace
    first_writes = 0

    def fail_apply_and_rollback(source, destination):
        nonlocal first_writes
        if Path(destination) == first:
            first_writes += 1
        if Path(destination) == second or first_writes > 1:
            raise OSError("Disk failure")
        replace(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail_apply_and_rollback)
        with pytest.raises(HistoryConflict, match="Recovery copies"):
            history.undo()
    assert first.read_bytes() == b"first before"
    assert second.read_bytes() == b"second after"
    assert history.can_undo and not history.can_redo
    copies = list(tmp_path.glob(".taskman-undo-*"))
    assert len(copies) == 1 and copies[0].read_bytes() == b"first after"
    os.replace(copies[0], first)
    history.undo()
    assert first.read_bytes() == b"first before" and second.read_bytes() == b"second before"


def test_new_file_appearing_at_publish_boundary_is_not_overwritten(tmp_path, monkeypatch):
    history, old, new = note_rename(tmp_path)
    link = os.link

    def appear_before_link(source, destination, *args, **kwargs):
        if Path(destination) == old:
            old.write_bytes(b"External file")
        return link(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, "link", appear_before_link)
    with pytest.raises(FileExistsError):
        history.undo()
    assert old.read_bytes() == b"External file" and new.read_bytes() == b"Note body"
    assert history.can_undo and not history.can_redo


def test_rollback_does_not_overwrite_recreated_file_after_our_unlink(tmp_path, monkeypatch):
    history, old, new = note_rename(tmp_path)
    history.undo()
    link = os.link

    def recreate_deleted_file(source, destination, *args, **kwargs):
        if Path(destination) == new:
            old.write_bytes(b"External recreation")
            raise OSError("Creation failed")
        return link(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, "link", recreate_deleted_file)
    with pytest.raises(HistoryConflict, match="Recovery copies"):
        history.redo()
    assert old.read_bytes() == b"External recreation" and not new.exists()
    assert history.can_redo and not history.can_undo
    copies = list(tmp_path.glob(".taskman-undo-*"))
    assert len(copies) == 1 and copies[0].read_bytes() == b"Note body"


def test_unsupported_no_clobber_creation_is_detected_before_rename_deletion(tmp_path, monkeypatch):
    history, old, new = note_rename(tmp_path)
    history.undo()

    def unsupported(*args, **kwargs):
        raise OSError("Hard links are not supported")

    monkeypatch.setattr(os, "link", unsupported)
    with pytest.raises(OSError, match="not supported"):
        history.redo()
    assert old.read_bytes() == b"Note body" and not new.exists()
    assert history.can_redo and not history.can_undo
    assert not list(tmp_path.glob(".taskman-undo-*"))
