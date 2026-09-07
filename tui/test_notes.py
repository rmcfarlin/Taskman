"""Standalone notes keep Markdown, external edits, and linked task history safe."""
from dataclasses import replace
import json
from pathlib import Path

import pytest

from tui import notes as nm, taskman as tm


def test_create_roundtrip_search_and_independent_task_links(tmp_path):
    store = nm.NotesStore(tmp_path)
    note = store.create("Casting résumé", "Supplier reference\n\nUse the revised temperature.",
                        category="Manufacturing", tags=("#Process", "process", "Supplier"),
                        projects=("Alpha", "ALPHA"), tasks=(nm.TaskLink("task-abc", "Review spec"),))
    assert note.file == "Notes/Casting résumé.md"
    assert note.tags == ("Process", "Supplier") and note.projects == ("Alpha",)
    assert note.revision and len(note.revision) == 64
    assert store.load(note.file) == note
    assert store.refresh() == [note]
    assert store.categories() == ["Manufacturing"]
    assert store.tags() == ["Process", "Supplier"]
    raw = (tmp_path / note.file).read_text(encoding="utf-8")
    assert "<!-- taskman-note:" in raw and "# Casting résumé\n\n" in raw
    assert nm.filter_notes([note], "RÉSUMÉ temperature Alpha", category="manufacturing", tag="#process") == [note]
    assert nm.filter_notes([note], "missing") == []
    assert nm.filter_notes([note], category="Other") == []
    assert nm.filter_notes([note], tag="Other") == []


def test_hash_search_terms_match_exact_tags_and_combine_with_text(tmp_path):
    store = nm.NotesStore(tmp_path)
    tagged = store.create("Inspection", "Supplier measurements", tags=("Quality", "reference"))
    untagged = store.create("Mentions", "The literal #quality appears in this body")
    longer = store.create("Long tag", tags=("quality-control",))
    notes = [tagged, untagged, longer]
    assert nm.filter_notes(notes, "#QUALITY") == [tagged]
    assert nm.filter_notes(notes, "supplier #Quality #REFERENCE") == [tagged]
    assert nm.filter_notes(notes, "#Quality missing") == []
    assert nm.filter_notes(notes, "#qual") == []
    assert nm.filter_notes(notes, "#") == []
    assert nm.filter_notes(notes, "quality") == notes


def test_save_manages_generated_heading_without_duplicating_it(tmp_path):
    store = nm.NotesStore(tmp_path)
    original = store.create("Before", "# A heading in the body\nKeep this.")
    updated = store.save(replace(original, title="After", body=original.body + "\nMore."))
    assert updated.body == "# A heading in the body\nKeep this.\nMore."
    assert updated.revision != original.revision
    raw = (tmp_path / updated.file).read_text(encoding="utf-8")
    assert raw.count("\n# After\n") == 1 and "\n# Before\n" not in raw
    assert store.save(updated) == updated
    assert store.load(updated.file) == updated


@pytest.mark.parametrize("delimiter,end", [("---", "---"), ("---", "..."), ("+++", "+++")])
def test_legacy_note_preserves_bom_frontmatter_and_unknown_content(tmp_path, delimiter, end):
    folder = tmp_path / "Notes" / "Reference"
    folder.mkdir(parents=True)
    path = folder / "Legacy.MD"
    prefix = f"\ufeff{delimiter}\r\ncustom: {{arbitrary: [1, 2]}}\r\n{end}\r\n"
    text = "\r\n# Existing heading\r\n<!-- other-tool: keep -->\r\nReference body.\r\n"
    raw = (prefix + text).encode("utf-8")
    path.write_bytes(raw)
    store = nm.NotesStore(tmp_path)
    note = store.load("Notes/Reference/Legacy.MD")
    assert note.title == "Existing heading" and note.category == "Unfiled"
    assert "custom:" not in note.body and "# Existing heading" in note.body
    assert store.save(note) == note
    assert path.read_bytes() == raw
    updated = store.save(replace(note, body=note.body.replace("Reference body.", "Updated body."), tags=("legacy",)))
    after = path.read_bytes()
    assert after.startswith(prefix.encode("utf-8"))
    assert b"<!-- other-tool: keep -->\r\n" in after
    assert b"# Existing heading\r\n" in after
    assert after.count(b"# Existing heading") == 1
    assert b"Updated body.\r\n" in after
    assert store.load(note.file) == updated


def test_preserves_unknown_own_metadata_and_task_link_fields(tmp_path):
    path = tmp_path / "Notes" / "Imported.md"
    path.parent.mkdir()
    metadata = {"version": 1, "title": "Imported", "category": "Reference", "tags": [],
                "tasks": [{"id": "anchor-1", "title": "Old title", "future": {"keep": True}}],
                "other-tool": {"nested": [1, "two"]}}
    path.write_text("<!-- taskman-note: " + json.dumps(metadata) + " -->\n\nBody", encoding="utf-8")
    store = nm.NotesStore(tmp_path)
    before = store.load("Notes/Imported.md")
    store.save(replace(before, body="Revised", tasks=(nm.TaskLink("anchor-1", "New title"),)))
    text = path.read_text(encoding="utf-8")
    saved = json.loads(text.split("<!-- taskman-note: ", 1)[1].split(" -->", 1)[0])
    assert saved["other-tool"] == metadata["other-tool"]
    assert saved["tasks"] == [{"id": "anchor-1", "title": "New title", "future": {"keep": True}}]


@pytest.mark.parametrize("content", [
    "<!-- taskman-note: nope -->\n\nBody",
    "<!-- taskman-note: {\"version\": 1, \"title\": \"Broken\"}",
    '<!-- taskman-note: {"version": 2, "title": "Future"} -->',
    '<!-- taskman-note: {"version": 1, "title": "Invalid", "tags": "one"} -->',
    '<!-- taskman-note: {"version": 1, "title": "Invalid", "tasks": [4]} -->',
    '<!-- taskman-note: {"version": 1, "title": "Invalid", "tasks": [{"id": "x"}]} -->',
    '<!-- taskman-note: {"version": 1, "title": "Managed", "heading": true} -->\n\n# Different\n\n',
])
def test_malformed_own_metadata_is_reported_and_never_rewritten(tmp_path, content):
    path = tmp_path / "Notes" / "Broken.md"
    path.parent.mkdir()
    path.write_text(content, encoding="utf-8")
    before = path.read_bytes()
    store = nm.NotesStore(tmp_path)
    good = store.create("Good", "Readable")
    assert store.refresh() == [good]
    assert "Notes/Broken.md" in store.errors
    with pytest.raises(nm.NoteFormatError):
        store.load("Notes/Broken.md")
    with pytest.raises(nm.NoteFormatError):
        store.save(nm.Note("Notes/Broken.md", "Overwrite", "No", revision=nm.hashlib.sha256(before).hexdigest()))
    assert path.read_bytes() == before


def test_invalid_encoding_is_reported_without_touching_file(tmp_path):
    path = tmp_path / "Notes" / "Binary.md"
    path.parent.mkdir()
    path.write_bytes(b"\xff\x00")
    store = nm.NotesStore(tmp_path)
    assert store.refresh() == [] and "Notes/Binary.md" in store.errors
    assert path.read_bytes() == b"\xff\x00"


def test_external_change_removal_and_unloaded_save_are_conflicts(tmp_path):
    store = nm.NotesStore(tmp_path)
    note = store.create("Reference", "Original")
    path = tmp_path / note.file
    path.write_text("# Written externally\nKeep this.", encoding="utf-8")
    with pytest.raises(nm.NoteConflict, match="changed"):
        store.save(replace(note, body="Stale edit"))
    assert path.read_text(encoding="utf-8") == "# Written externally\nKeep this."
    fresh = store.load(note.file)
    with pytest.raises(nm.NoteConflict):
        store.save(replace(fresh, revision="", body="No revision"))
    path.unlink()
    with pytest.raises(nm.NoteConflict, match="removed"):
        store.save(replace(fresh, body="Do not recreate"))
    assert not path.exists()


def test_explicit_revision_is_checked_and_new_revision_returned(tmp_path):
    store = nm.NotesStore(tmp_path)
    note = store.create("Example", "Before")
    with pytest.raises(nm.NoteConflict):
        store.save(replace(note, body="After"), expected_revision="wrong")
    saved = store.save(replace(note, body="After"), expected_revision=note.revision)
    assert saved.body == "After" and saved.revision != note.revision


def test_external_change_during_temp_write_is_not_overwritten(tmp_path, monkeypatch):
    store = nm.NotesStore(tmp_path)
    note = store.create("Example", "Before")
    path = tmp_path / note.file
    read = store._read
    calls = 0
    def changed(file):
        nonlocal calls
        calls += 1
        if calls == 2:
            path.write_text("External concurrent edit", encoding="utf-8")
        return read(file)
    monkeypatch.setattr(store, "_read", changed)
    with pytest.raises(nm.NoteConflict, match="while saving"):
        store.save(replace(note, body="Editor change"))
    assert path.read_text(encoding="utf-8") == "External concurrent edit"
    assert not list(path.parent.glob(".taskman-note-*"))


def test_failed_atomic_replace_preserves_original_and_removes_temporary(tmp_path, monkeypatch):
    store = nm.NotesStore(tmp_path)
    note = store.create("Example", "Before")
    path = tmp_path / note.file
    before = path.read_bytes()
    def refused(*_args):
        raise PermissionError("destination busy")
    monkeypatch.setattr(nm.os, "replace", refused)
    with pytest.raises(PermissionError, match="busy"):
        store.save(replace(note, body="After"))
    assert path.read_bytes() == before
    assert not list(path.parent.glob(".taskman-note-*"))


def test_exclusive_creation_never_overwrites_even_after_a_path_was_chosen(tmp_path):
    first, second = nm.NotesStore(tmp_path), nm.NotesStore(tmp_path)
    chosen = first.new_path("Meeting")
    original = second.create("Meeting", "Created by another editor", file=chosen)
    with pytest.raises(FileExistsError):
        first.create("Meeting", "Would overwrite", file=chosen)
    assert first.load(chosen) == original
    unique = first.create("meeting", "Separate note")
    assert unique.file.casefold() != chosen.casefold()


@pytest.mark.parametrize("failure", ["write", "fsync"])
def test_failed_create_removes_its_partial_file(tmp_path, monkeypatch, failure):
    store = nm.NotesStore(tmp_path)
    opened = Path.open
    class BrokenWriter:
        def __init__(self, stream):
            self.stream = stream
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            self.stream.close()
        def fileno(self):
            return self.stream.fileno()
        def write(self, raw):
            self.stream.write(raw[:12])
            raise OSError("disk write failed")
    def open_file(path, *args, **kwargs):
        stream = opened(path, *args, **kwargs)
        return BrokenWriter(stream) if args and args[0] == "xb" else stream
    def fsync(_fd):
        raise OSError("disk sync failed")
    if failure == "write":
        monkeypatch.setattr(Path, "open", open_file)
    else:
        monkeypatch.setattr(nm.os, "fsync", fsync)
    with pytest.raises(OSError, match="disk .* failed"):
        store.create("Failed", "Content that should not survive an incomplete write")
    assert not (tmp_path / "Notes" / "Failed.md").exists()
    assert store.refresh() == []


def test_failed_create_does_not_remove_a_replacement_file(tmp_path, monkeypatch):
    store = nm.NotesStore(tmp_path)
    path = tmp_path / "Notes" / "Failed.md"
    original_path_check = store._path
    failed = False
    def path_check(file):
        nonlocal failed
        if failed:  # Cleanup after closing the failed write, regardless of validation calls.
            failed = False
            path.rename(path.with_suffix(".partial"))
            path.write_text("Externally replaced content", encoding="utf-8")
        return original_path_check(file)
    def fsync(_fd):
        nonlocal failed
        failed = True
        raise OSError("sync failed")
    monkeypatch.setattr(store, "_path", path_check)
    monkeypatch.setattr(nm.os, "fsync", fsync)
    with pytest.raises(OSError, match="sync failed"):
        store.create("Failed", file="Notes/Failed.md")
    assert path.read_text(encoding="utf-8") == "Externally replaced content"


@pytest.mark.parametrize("file", ["../Outside.md", "Notes/../Outside.md", "Tasks/Other.md",
    "/Notes/Bad.md", "C:\\Notes\\Bad.md", "Notes/bad.md:stream", "Notes/folder./Bad.md",
    "Notes/Bad.txt", "Notes", "Notes/sub/../../Bad.md", "Notes/CON.md", "Notes/a\nb.md"])
def test_note_paths_cannot_escape_or_target_other_vault_content(tmp_path, file):
    store = nm.NotesStore(tmp_path)
    with pytest.raises(ValueError):
        store.create("Bad", file=file)
    with pytest.raises(ValueError):
        store.load(file)
    assert not list(tmp_path.iterdir())


def test_generated_paths_are_safe_unique_and_do_not_create_files(tmp_path):
    store = nm.NotesStore(tmp_path)
    assert store.new_path("CON") == "Notes/_CON.md"
    chosen = store.new_path('Mañana / reference: A?')
    assert chosen.startswith("Notes/Mañana") and chosen.endswith(".md")
    assert all(character not in Path(chosen).name for character in '/\\:*?"<>|')
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("target_kind", ["file", "directory"])
def test_symlinks_are_never_followed_for_read_or_write(tmp_path, target_kind):
    root, outside = tmp_path / "Vault", tmp_path / "Outside"
    root.mkdir()
    outside.mkdir()
    (root / "Notes").mkdir()
    source = outside / "Secret.md"
    source.write_text("External content", encoding="utf-8")
    link = root / "Notes" / ("Link.md" if target_kind == "file" else "Linked")
    try:
        link.symlink_to(source if target_kind == "file" else outside,
                        target_is_directory=target_kind == "directory")
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation is unavailable on this host")
    relative = "Notes/Link.md" if target_kind == "file" else "Notes/Linked/Secret.md"
    store = nm.NotesStore(root)
    assert store.refresh() == []
    with pytest.raises(ValueError):
        store.load(relative)
    with pytest.raises(ValueError):
        store.create("No overwrite", file=relative)
    assert source.read_text(encoding="utf-8") == "External content"


def test_note_links_and_content_survive_task_completion_and_deletion(tmp_path):
    (tmp_path / "Tasks").mkdir()
    task_file = tmp_path / "Tasks" / "Inbox.md"
    task_file.write_text("- [ ] Review spec\n", encoding="utf-8")
    store = nm.NotesStore(tmp_path)
    link = nm.TaskLink("stable-task-id", "Review spec")
    note = store.create("Specification", "Permanent reference", tasks=(link,))
    before = (tmp_path / note.file).read_bytes()
    task = tm.parse_file(task_file, tmp_path)[0]
    tm.toggle(tmp_path, task)
    tm.delete_task(tmp_path, tm.parse_file(task_file, tmp_path)[0])
    assert (tmp_path / note.file).read_bytes() == before
    assert store.load(note.file).tasks == (link,)


def test_comment_terminators_in_metadata_cannot_break_document(tmp_path):
    store = nm.NotesStore(tmp_path)
    note = store.create("A --> B", "Body", tasks=(nm.TaskLink("abc", "Task --> title"),))
    assert store.load(note.file) == note
    assert (tmp_path / note.file).read_text(encoding="utf-8").count("-->") == 2  # comment end and H1


def test_legacy_heading_preserves_hash_in_title(tmp_path):
    path = tmp_path / "Notes" / "Programming.md"
    path.parent.mkdir()
    path.write_text("# C#\n\nReference.", encoding="utf-8")
    store = nm.NotesStore(tmp_path)
    assert store.load("Notes/Programming.md").title == "C#"
    path.write_text("# C# reference ###\n\nReference.", encoding="utf-8")
    assert store.load("Notes/Programming.md").title == "C# reference"
