"""Filename links, preview conflicts and safe multi-file rename/delete behavior."""
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from tui import note_files as nf
from tui.history import History
from tui.notes import NoteConflict, NotesStore, TaskLink


def document(root, name, text):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return path


def setup(root, body="Body stays.", name="Notes/Old.md"):
    store = NotesStore(root)
    note = store.create("Display title stays", body, "Reference", tags=("keep",),
                        tasks=(TaskLink("a" * 32, "Task label"),), file=name)
    return store, note


def test_rename_updates_resolved_link_syntaxes_preserving_labels_metadata_and_line_endings(tmp_path):
    store, note = setup(tmp_path, "[self](Old.md#part)\nPlain Old.md stays.")
    uri = (tmp_path / note.file).as_uri()
    source = (
        '[inline](Notes/Old.md#keep "Title")\r\n'
        '![image](Notes/Old.md?raw=1#part)\r\n'
        '[reference]: <Notes/Old.md> "Reference title"\r\n[use][reference]\r\n'
        f'<{uri}#part>\r\n'
        '<a href="Notes/Old.md?x=1&amp;y=2">Label</a><img src=Notes/Old.md>\r\n'
        '[[Notes/Old|alias stays]] ![[Notes/Old.md#Heading]]\r\n'
        'Plain Notes/Old.md stays. [external](https://example.invalid/Notes/Old.md)\r\n'
        '`[code](Notes/Old.md)`\r\n```md\r\n[example](Notes/Old.md)\r\n```\r\n'
        '    [indented example](Notes/Old.md)\r\n'
        '<!-- [comment](Notes/Old.md) -->\r\n<code>[code](Notes/Old.md)</code>\r\n')
    root_doc = document(tmp_path, "Links.md", source)
    nested = document(tmp_path, "Tasks/Nested/list.md", '- [ ] [task](../../Notes/Old.md)\n  - [ ] Parent\n    - [ ] [child](../../Notes/Old.md)\n')
    template = document(tmp_path, ".taskman/templates/notes/Template.md", "[template](../../../Notes/Old.md)")
    ignored = document(tmp_path, "node_modules/readme.md", "[ignore](../Notes/Old.md)")
    old = (tmp_path / note.file).read_bytes()
    plan = nf.plan_rename(store, note, "New Name.md")
    assert plan.changed_links == 12 and plan.changed_files == 4
    assert plan.old_file == note.file and plan.new_file == "Notes/New Name.md"
    assert root_doc.read_bytes() == source.encode() and (tmp_path / note.file).read_bytes() == old
    with pytest.raises(FrozenInstanceError):
        plan.new_file = "Notes/Other.md"
    renamed = nf.apply_rename(store, plan)
    assert renamed.file == plan.new_file and renamed.title == note.title
    assert (renamed.category, renamed.tags, renamed.tasks) == (note.category, note.tags, note.tasks)
    assert not (tmp_path / note.file).exists()
    saved = root_doc.read_bytes().decode()
    assert '[inline](Notes/New%20Name.md#keep "Title")' in saved
    assert '![image](Notes/New%20Name.md?raw=1#part)' in saved
    assert '[reference]: <Notes/New%20Name.md> "Reference title"' in saved
    assert '<a href="Notes/New%20Name.md?x=1&amp;y=2">Label</a><img src=Notes/New%20Name.md>' in saved
    assert "[[Notes/New Name|alias stays]] ![[Notes/New Name.md#Heading]]" in saved
    assert (tmp_path / plan.new_file).as_uri() + "#part>" in saved
    for literal in ['Plain Notes/Old.md stays.', 'https://example.invalid/Notes/Old.md',
                    '`[code](Notes/Old.md)`', '[example](Notes/Old.md)', '[indented example](Notes/Old.md)',
                    '<!-- [comment](Notes/Old.md) -->', '<code>[code](Notes/Old.md)</code>']:
        assert literal in saved
    assert saved.count("\r\n") == source.count("\r\n")
    assert nested.read_text(encoding="utf-8").count("../../Notes/New%20Name.md") == 2
    assert template.read_text(encoding="utf-8") == "[template](../../../Notes/New%20Name.md)"
    assert ignored.read_text(encoding="utf-8") == "[ignore](../Notes/Old.md)"
    assert renamed.body == "[self](New%20Name.md#part)\nPlain Old.md stays."


def test_relative_paths_choose_exact_note_and_ambiguous_wiki_blocks_preview(tmp_path):
    store, note = setup(tmp_path)
    document(tmp_path, "Other/Old.md", "Other note")
    links = document(tmp_path, "Other/list.md", "[other](Old.md) [correct](../Notes/Old.md)\n")
    plan = nf.plan_rename(store, note, "New")
    assert plan.changed_links == 1
    links.write_text("[[Old]] [[Notes/Old]]\n", encoding="utf-8")
    before = (tmp_path / note.file).read_bytes()
    with pytest.raises(NoteConflict, match="Ambiguous link"):
        nf.plan_rename(store, note, "New")
    assert (tmp_path / note.file).read_bytes() == before


def test_rename_cannot_turn_unique_implicit_wiki_into_ambiguous_destination(tmp_path):
    store, note = setup(tmp_path)
    document(tmp_path, "Other/New.md", "Existing different note")
    links = document(tmp_path, "Links.md", "[[Old|Keep label]]")
    with pytest.raises(NoteConflict, match="would make a link ambiguous"):
        nf.plan_rename(store, note, "New.md")
    assert (tmp_path / note.file).exists()
    links.write_text("[[Notes/Old|Keep label]]", encoding="utf-8")
    nf.apply_rename(store, nf.plan_rename(store, note, "New.md"))
    assert links.read_text(encoding="utf-8") == "[[Notes/New|Keep label]]"


@pytest.mark.parametrize("raw,new", [("Old%20Name.md", "New%20Name.md"),
                                    ("Old(Name).md", "New%20Name.md"),
                                    ("Old\\(Name\\).md", "New%20Name.md")])
def test_encoded_spaces_and_balanced_or_escaped_parentheses(tmp_path, raw, new):
    filename = "Old Name.md" if "%20" in raw else "Old(Name).md"
    store, note = setup(tmp_path, name="Notes/" + filename)
    path = document(tmp_path, "Notes/Links.md", f"[Label]({raw}#anchor)")
    nf.apply_rename(store, nf.plan_rename(store, note, "New Name.md"))
    assert path.read_text(encoding="utf-8") == f"[Label]({new}#anchor)"


def test_file_uri_with_percent_encoded_hash_and_nonlocal_uri(tmp_path):
    store, note = setup(tmp_path, name="Notes/Old#Name.md")
    path = document(tmp_path, "links.md", f'<{(tmp_path / note.file).as_uri()}?a=1#keep>\n<file://server/Old%23Name.md>')
    nf.apply_rename(store, nf.plan_rename(store, note, "New.md"))
    assert path.read_text(encoding="utf-8") == f'<{(tmp_path / "Notes/New.md").as_uri()}?a=1#keep>\n<file://server/Old%23Name.md>'


def test_malformed_links_attribute_prose_and_nested_fences_are_unchanged(tmp_path):
    store, note = setup(tmp_path)
    text = ("Prose ](Notes/Old.md)\n[invalid](Notes/Old.md words)\n"
            "[reference]: Notes/Old.md prose is not a title\n"
            '<div title="href=\'Notes/Old.md\'">Text</div>\n'
            "\\[[Notes/Old]]\n"
            "- [ ] Parent\n  - [ ] Child\n    ~~~md\n    [example](Notes/Old.md)\n    ~~~\n")
    path = document(tmp_path, "Links.md", text)
    plan = nf.plan_rename(store, note, "New.md")
    assert plan.changed_links == 0
    nf.apply_rename(store, plan)
    assert path.read_text(encoding="utf-8") == text


def test_multiline_html_and_encoded_separator_keep_destinations_valid(tmp_path):
    store, note = setup(tmp_path)
    path = document(tmp_path, "Links.md", '<a\n href="Notes/Old.md">Label</a>\n[encoded](Notes%2FOld.md)')
    nf.apply_rename(store, nf.plan_rename(store, note, "New Name.md"))
    assert path.read_text(encoding="utf-8") == '<a\n href="Notes/New%20Name.md">Label</a>\n[encoded](Notes%2FNew%20Name.md)'


@pytest.mark.parametrize("separator", ["&sol;", "&#47;", "&#x2F;"])
@pytest.mark.parametrize("suffix", ["#keep", "?raw=1&amp;x=2#keep", "&#63;raw=1&amp;x=2&#35;keep"])
def test_entity_encoded_link_paths_preserve_directory_and_suffix(tmp_path, separator, suffix):
    store, note = setup(tmp_path)
    destination = f"Notes{separator}Old.md{suffix}"
    source = f'[Markdown]({destination})\n<a href="{destination}">HTML</a>\n[[{destination}|Wiki]]\n'
    path = document(tmp_path, "Links.md", source)
    plan = nf.plan_rename(store, note, "New Name.md")
    assert plan.changed_links == 3
    nf.apply_rename(store, plan)
    expected = (f'[Markdown](Notes{separator}New%20Name.md{suffix})\n'
                f'<a href="Notes{separator}New%20Name.md{suffix}">HTML</a>\n'
                f'[[Notes{separator}New Name.md{suffix}|Wiki]]\n')
    assert path.read_text(encoding="utf-8") == expected


def test_link_entities_decode_once_and_new_entity_looking_filename_is_escaped(tmp_path):
    store, note = setup(tmp_path, name="Notes/Old&sol;.md")
    source = ('[Markdown](Notes/Old&amp;sol;.md#keep)\n'
              '<a href="Notes/Old&amp;sol;.md">HTML</a>\n'
              '[[Notes/Old&amp;sol;.md|Wiki]]\n'
              '[Different path](Notes/Old&sol;.md)\n')
    path = document(tmp_path, "Links.md", source)
    plan = nf.plan_rename(store, note, "New&sol;.md")
    assert plan.changed_links == 3
    nf.apply_rename(store, plan)
    assert path.read_text(encoding="utf-8") == (
        '[Markdown](Notes/New%26sol%3B.md#keep)\n'
        '<a href="Notes/New%26sol%3B.md">HTML</a>\n'
        '[[Notes/New%26sol%3B.md|Wiki]]\n'
        '[Different path](Notes/Old&sol;.md)\n')


def test_multiline_markdown_labels_preserve_label_but_not_invalid_paragraph_links(tmp_path):
    store, note = setup(tmp_path)
    path = document(tmp_path, "Links.md", '[See\nreference](Notes/Old.md#keep "Title")\n'
                    '![Image\r\nlabel](Notes/Old.md?raw=1)\n'
                    '[Paragraph\n\nbreak](Notes/Old.md)\nProse ](Notes/Old.md)\n')
    plan = nf.plan_rename(store, note, "New.md")
    assert plan.changed_links == 2
    nf.apply_rename(store, plan)
    assert path.read_bytes() == ('[See\nreference](Notes/New.md#keep "Title")\n'
                                 '![Image\r\nlabel](Notes/New.md?raw=1)\n'
                                 '[Paragraph\n\nbreak](Notes/Old.md)\nProse ](Notes/Old.md)\n').encode()


@pytest.mark.parametrize("filename,encoded", [("New's(odd.md", "New%27s%28odd.md"),
                                            ("New#].md", "New%23%5D.md"),
                                            ("Résumé α.md", "R%C3%A9sum%C3%A9%20%CE%B1.md")])
def test_new_filename_punctuation_cannot_break_markdown_or_html_destinations(tmp_path, filename, encoded):
    store, note = setup(tmp_path)
    path = document(tmp_path, "Links.md", "[link](Notes/Old.md) <a href='Notes/Old.md'>Label</a> [directory](Notes/Old.md/)")
    nf.apply_rename(store, nf.plan_rename(store, note, filename))
    assert path.read_text(encoding="utf-8") == f"[link](Notes/{encoded}) <a href='Notes/{encoded}'>Label</a> [directory](Notes/Old.md/)"


@pytest.mark.parametrize("new", ["../bad", "C:/bad", "a\\b", "", ".md", "NUL", "CON.md", "x?y", "trailing.", " name", "old.MD"])
def test_invalid_names_and_case_only_renames_are_rejected(tmp_path, new):
    store, note = setup(tmp_path)
    with pytest.raises((ValueError, NoteConflict)):
        nf.plan_rename(store, note, new)
    assert (tmp_path / note.file).exists()


@pytest.mark.parametrize("folder", [False, True])
def test_case_insensitive_file_and_folder_collisions(tmp_path, folder):
    store, note = setup(tmp_path)
    target = tmp_path / "Notes/NEW.md"
    target.mkdir() if folder else target.write_text("Keep", encoding="utf-8")
    with pytest.raises(NoteConflict, match="already"):
        nf.plan_rename(store, note, "New.md")


@pytest.mark.parametrize("mutation", ["note", "link", "new", "removed"])
def test_preview_revalidates_all_scanned_bytes_and_file_list(tmp_path, mutation):
    store, note = setup(tmp_path)
    link = document(tmp_path, "Tasks/List.md", "[link](../Notes/Old.md)")
    plan = nf.plan_rename(store, note, "New.md")
    if mutation == "note":
        (tmp_path / note.file).write_text("External note", encoding="utf-8")
    elif mutation == "link":
        link.write_text("External link", encoding="utf-8")
    elif mutation == "new":
        document(tmp_path, "NewLinks.md", "[new](Notes/Old.md)")
    else:
        link.unlink()
    before = {str(path): path.read_bytes() for path in tmp_path.rglob("*.md")}
    with pytest.raises(NoteConflict, match="changed"):
        nf.apply_rename(store, plan)
    assert before == {str(path): path.read_bytes() for path in tmp_path.rglob("*.md")}


def test_unreadable_markdown_fails_preview_instead_of_skipping_links(tmp_path, monkeypatch):
    store, note = setup(tmp_path)
    hidden = document(tmp_path, "Nested/Unreadable.md", "[link](../Notes/Old.md)")
    original = Path.read_bytes
    def read(path):
        if path == hidden:
            raise PermissionError("denied")
        return original(path)
    monkeypatch.setattr(Path, "read_bytes", read)
    with pytest.raises(NoteConflict, match="Cannot safely read"):
        nf.plan_rename(store, note, "New.md")


def test_linked_content_folder_fails_preview(tmp_path, monkeypatch):
    store, note = setup(tmp_path)
    document(tmp_path, "Linked/Links.md", "[link](../Notes/Old.md)")
    original = nf.is_linked
    monkeypatch.setattr(nf, "is_linked", lambda path: path.name == "Linked" or original(path))
    with pytest.raises(NoteConflict, match="linked folder"):
        nf.plan_rename(store, note, "New.md")


def test_rename_and_delete_each_undo_redo_as_one_file_scoped_action(tmp_path):
    store, note = setup(tmp_path)
    link = document(tmp_path, "Links.md", "[link](Notes/Old.md)\r\n")
    note_bytes, link_bytes = (tmp_path / note.file).read_bytes(), link.read_bytes()
    history = History(tmp_path)
    plan = nf.plan_rename(store, note, "New.md")
    with history.record("Rename note", plan.paths):
        renamed = nf.apply_rename(store, plan)
    assert history.can_undo
    history.undo()
    assert (tmp_path / note.file).read_bytes() == note_bytes and link.read_bytes() == link_bytes
    assert not (tmp_path / plan.new_file).exists() and not history.can_undo
    history.redo()
    assert not (tmp_path / note.file).exists() and (tmp_path / plan.new_file).exists()
    task = document(tmp_path, "Tasks/Inbox.md", "- [ ] Keep task <!-- taskman:id=" + "a" * 32 + " -->\n")
    task_bytes = task.read_bytes()
    with history.record("Delete note", [renamed.file]):
        nf.delete_note(store, store.load(renamed.file))
    assert not (tmp_path / renamed.file).exists() and task.read_bytes() == task_bytes
    history.undo()
    assert (tmp_path / renamed.file).read_bytes() == note_bytes and task.read_bytes() == task_bytes
    history.redo()
    assert not (tmp_path / renamed.file).exists()


def test_delete_rejects_external_edit_and_keeps_linked_task(tmp_path):
    store, note = setup(tmp_path)
    path = tmp_path / note.file
    path.write_bytes(path.read_bytes() + b"External content\n")
    before = path.read_bytes()
    with pytest.raises(NoteConflict, match="changed"):
        nf.delete_note(store, note)
    assert path.read_bytes() == before


@pytest.mark.parametrize("external", [False, True])
def test_failure_rolls_back_our_writes_but_preserves_external_replacement(tmp_path, monkeypatch, external):
    store, note = setup(tmp_path)
    first = document(tmp_path, "A.md", "[a](Notes/Old.md)")
    second = document(tmp_path, "B.md", "[b](Notes/Old.md)")
    originals = {path: path.read_bytes() for path in (first, second, tmp_path / note.file)}
    plan = nf.plan_rename(store, note, "New.md")
    original = nf.os.replace
    def replace(source, destination):
        if Path(destination) == second:
            if external:
                first.write_text("External writer content", encoding="utf-8")
            raise OSError("simulated replacement failure")
        return original(source, destination)
    monkeypatch.setattr(nf.os, "replace", replace)
    with pytest.raises(NoteConflict if external else OSError):
        nf.apply_rename(store, plan)
    assert (tmp_path / note.file).read_bytes() == originals[tmp_path / note.file]
    assert second.read_bytes() == originals[second]
    assert first.read_bytes() == (b"External writer content" if external else originals[first])
    assert not (tmp_path / plan.new_file).exists()
    assert not list(tmp_path.rglob(".taskman-rename-*"))


def test_new_destination_created_during_publication_is_never_overwritten(tmp_path, monkeypatch):
    store, note = setup(tmp_path)
    old = (tmp_path / note.file).read_bytes()
    plan = nf.plan_rename(store, note, "New.md")
    original = nf._install_new
    def collide(root, name, temporary):
        (root / name).write_bytes(b"External new note")
        return original(root, name, temporary)
    monkeypatch.setattr(nf, "_install_new", collide)
    with pytest.raises(FileExistsError):
        nf.apply_rename(store, plan)
    assert (tmp_path / plan.new_file).read_bytes() == b"External new note"
    assert (tmp_path / note.file).read_bytes() == old


@pytest.mark.parametrize("victim_name", ["A.md", "Notes/New.md"])
def test_rollback_preserves_external_file_replacement_even_with_identical_bytes(tmp_path, monkeypatch, victim_name):
    store, note = setup(tmp_path)
    first = document(tmp_path, "A.md", "[a](Notes/Old.md)")
    second = document(tmp_path, "B.md", "[b](Notes/Old.md)")
    plan = nf.plan_rename(store, note, "New.md")
    replace = nf.os.replace
    captured = {}
    def fail_after_external_replace(source, destination):
        if Path(destination) == second:
            victim = tmp_path / victim_name
            other = tmp_path / "external.tmp"
            captured["bytes"] = victim.read_bytes()
            other.write_bytes(captured["bytes"])
            replace(other, victim)
            captured["stat"] = victim.stat()
            raise OSError("later document could not be replaced")
        return replace(source, destination)
    monkeypatch.setattr(nf.os, "replace", fail_after_external_replace)
    with pytest.raises(NoteConflict, match="external edits were preserved"):
        nf.apply_rename(store, plan)
    victim = tmp_path / victim_name
    assert victim.read_bytes() == captured["bytes"]
    assert nf.os.path.samestat(victim.stat(), captured["stat"])
    assert (tmp_path / note.file).exists()
    assert second.read_bytes() == b"[b](Notes/Old.md)"
    if victim_name != "A.md":
        assert first.read_bytes() == b"[a](Notes/Old.md)"
    assert not list(tmp_path.rglob(".taskman-rename-*"))


def test_staging_failure_writes_nothing_and_cleans_temporary_files(tmp_path, monkeypatch):
    store, note = setup(tmp_path)
    path = document(tmp_path, "Links.md", "[link](Notes/Old.md)")
    plan = nf.plan_rename(store, note, "New.md")
    before = {str(item): item.read_bytes() for item in (path, tmp_path / note.file)}
    def fail(_fd):
        raise OSError("simulated sync failure")
    monkeypatch.setattr(nf.os, "fsync", fail)
    with pytest.raises(OSError):
        nf.apply_rename(store, plan)
    assert before == {str(item): item.read_bytes() for item in (path, tmp_path / note.file)}
    assert not (tmp_path / plan.new_file).exists() and not list(tmp_path.rglob(".taskman-rename-*"))
