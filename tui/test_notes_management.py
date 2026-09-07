"""Notes management through real app bindings and disposable vault files."""
import datetime as dt
import os
from dataclasses import replace

import pytest
from textual.widgets import Input, Label, OptionList, TextArea

from tui import settings, taskman as tm
from tui.app import ConfirmScreen, TaskApp
from tui.notes import NotesStore, TaskLink
from tui.notes_dialogs import RenameNoteScreen, TemplateEditorScreen
from tui.notes_ui import DiscardNoteScreen, NoteEditorScreen, NotesWorkspace


@pytest.fixture
def vault(tmp_path):
    (tmp_path / "Tasks").mkdir()
    (tmp_path / "Tasks/Inbox.md").write_text("- [ ] Keep task <!-- taskman:id=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa -->\n", encoding="utf-8")
    return tmp_path


@pytest.mark.asyncio
async def test_delete_confirm_undo_redo_preserves_linked_task(vault):
    note = NotesStore(vault).create("Keep reference", "Details", tasks=(TaskLink("a" * 32, "Keep task"),))
    raw = (vault / note.file).read_bytes()
    task_raw = (vault / "Tasks/Inbox.md").read_bytes()
    app = TaskApp(vault)
    async with app.run_test(size=(110, 34)) as pilot:
        await pilot.press("8", "delete")
        assert isinstance(app.screen, ConfirmScreen)
        assert app.focused.id == "cancel"
        await pilot.press("escape")
        assert (vault / note.file).read_bytes() == raw
        assert not app.history.can_undo
        await pilot.press("delete")
        await pilot.click("#ok")
        assert not (vault / note.file).exists()
        assert (vault / "Tasks/Inbox.md").read_bytes() == task_raw
        await pilot.press("u")
        assert (vault / note.file).read_bytes() == raw
        assert app.query_one(NotesWorkspace).current.file == note.file
        assert NotesStore(vault).load(note.file).tasks == note.tasks
        await pilot.press("ctrl+y")
        assert not (vault / note.file).exists()
        assert (vault / "Tasks/Inbox.md").read_bytes() == task_raw


@pytest.mark.asyncio
async def test_delete_refuses_note_changed_while_confirming(vault):
    note = NotesStore(vault).create("Reference", "Original")
    app = TaskApp(vault)
    async with app.run_test(size=(110, 34)) as pilot:
        await pilot.press("8", "delete")
        NotesStore(vault).save(replace(note, body="External edit"))
        await pilot.click("#ok")
        assert NotesStore(vault).load(note.file).body == "External edit"
        assert not app.history.can_undo


@pytest.mark.asyncio
async def test_sort_observes_mtime_remembers_order_and_preserves_selection(vault):
    store = NotesStore(vault)
    alpha = store.create("Alpha", "First")
    zulu = store.create("Zulu", "Recent")
    os.utime(vault / alpha.file, ns=(1_000_000_000, 1_000_000_000))
    os.utime(vault / zulu.file, ns=(2_000_000_000, 2_000_000_000))
    app = TaskApp(vault)
    async with app.run_test(size=(110, 34)) as pilot:
        await pilot.press("8")
        listing = app.query_one("#notes-list", OptionList)
        assert listing.get_option_at_index(0).id == zulu.file
        assert app.query_one(NotesWorkspace).current.file == zulu.file
        await pilot.press("s")
        assert listing.get_option_at_index(0).id == alpha.file
        assert app.query_one(NotesWorkspace).current.file == zulu.file
        assert settings.read_note_sort() == "title"
        await pilot.press("s")
        assert listing.get_option_at_index(0).id == zulu.file
        os.utime(vault / alpha.file, ns=(3_000_000_000, 3_000_000_000))
        await pilot.press("r")
        assert listing.get_option_at_index(0).id == alpha.file
        assert app.query_one(NotesWorkspace).current.file == zulu.file
        await pilot.press("s")
    assert TaskApp(vault).note_sort == "title"
    settings.write_theme("textual-dark")
    assert settings.read_note_sort() == "title"


@pytest.mark.asyncio
async def test_rename_preview_links_one_undo_and_redo_selection(vault):
    store = NotesStore(vault)
    note = store.create("Title remains", "Self [label](Old.md#section)", file="Notes/Old.md")
    refs = vault / "Tasks/Inbox.md"
    refs.write_text("- [ ] Read [original](../Notes/Old.md#section)\n", encoding="utf-8")
    before = {note.file: (vault / note.file).read_bytes(), "Tasks/Inbox.md": refs.read_bytes()}
    app = TaskApp(vault)
    async with app.run_test(size=(110, 34)) as pilot:
        await pilot.press("8", "f2")
        screen = app.screen
        assert isinstance(screen, RenameNoteScreen)
        screen.query_one(Input).value = "New name.md"
        await pilot.press("enter")
        assert screen.plan.changed_links == 2
        assert (vault / note.file).exists() and not (vault / "Notes/New name.md").exists()
        await pilot.click("#rename-apply")
        renamed = app.query_one(NotesWorkspace).current
        assert renamed.file == "Notes/New name.md" and renamed.title == note.title
        assert "New%20name.md#section" in refs.read_text(encoding="utf-8")
        assert not (vault / note.file).exists()
        await pilot.press("u")
        assert app.query_one(NotesWorkspace).current.file == note.file
        assert not (vault / renamed.file).exists()
        assert all((vault / file).read_bytes() == raw for file, raw in before.items())
        assert not app.history.can_undo
        await pilot.press("ctrl+y")
        assert app.query_one(NotesWorkspace).current.file == renamed.file
        assert "New%20name.md#section" in refs.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_rename_conflict_retains_filename_draft(vault):
    note = NotesStore(vault).create("Original", "Body")
    app = TaskApp(vault)
    async with app.run_test(size=(90, 30)) as pilot:
        await pilot.press("8", "f2")
        screen = app.screen
        screen.query_one(Input).value = "Revised.md"
        await pilot.press("enter")
        (vault / "New external document.md").write_text("[link](Notes/Original.md)", encoding="utf-8")
        await pilot.click("#rename-apply")
        assert app.screen is screen
        assert screen.query_one(Input).value == "Revised.md"
        assert str(screen.query_one("#rename-error", Label).content)
        assert (vault / note.file).exists() and not (vault / "Notes/Revised.md").exists()
        assert not app.history.can_undo


@pytest.mark.asyncio
async def test_templates_capture_edit_cancel_and_history(vault):
    app = TaskApp(vault)
    async with app.run_test(size=(110, 34)) as pilot:
        await pilot.press("ctrl+shift+n", "enter")
        assert isinstance(app.screen, NoteEditorScreen) and app.screen.note is None
        assert dt.date.today().isoformat() in app.screen.query_one("#reference-text", TextArea).text
        await pilot.press("escape")
        assert not (vault / "Notes").exists()
        await pilot.press("ctrl+shift+n", "enter", "ctrl+s")
        note = app.query_one(NotesWorkspace).current
        assert note and "Attendees" in note.body
        captured = (vault / note.file).read_bytes()
        app.action_edit_note_template()
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, TemplateEditorScreen)
        app.screen.query_one(TextArea).load_text("## Custom agenda\n{{date}}\nDecisions")
        await pilot.press("ctrl+shift+s")
        custom = vault / ".taskman/templates/notes/Meeting.md"
        assert custom.exists()
        assert (vault / note.file).read_bytes() == captured
        await pilot.press("ctrl+shift+n", "enter")
        assert app.screen.query_one("#reference-text", TextArea).text.startswith("## Custom agenda")
        await pilot.press("escape", "u")
        assert not custom.exists()
        await pilot.press("ctrl+y")
        assert custom.exists()
        assert len(tm.load_all(vault)) == 1
        assert len(NotesStore(vault).refresh()) == 1


@pytest.mark.asyncio
async def test_template_editor_external_conflict_and_discard(vault):
    app = TaskApp(vault)
    async with app.run_test(size=(90, 30)) as pilot:
        app.action_edit_note_template()
        await pilot.press("enter")
        await pilot.pause()
        editor = app.screen
        editor.query_one(TextArea).load_text("Unsaved draft")
        path = vault / editor.template.file
        path.parent.mkdir(parents=True)
        path.write_text("External template", encoding="utf-8")
        await pilot.press("ctrl+s")
        assert app.screen is editor
        assert editor.query_one(TextArea).text == "Unsaved draft"
        assert path.read_text(encoding="utf-8") == "External template"
        await pilot.press("escape")
        assert isinstance(app.screen, DiscardNoteScreen)
        await pilot.click("#discard-note")
        assert not app.history.can_undo


@pytest.mark.asyncio
async def test_find_stays_separate_from_filter_and_editor(vault):
    note = NotesStore(vault).create("Needle", "Beginning\n\n" + "Body\n\n" * 70 + "needle end")
    app = TaskApp(vault)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("8", "/")
        app.query_one("#search", Input).value = "needle"
        await pilot.press("enter", "ctrl+f")
        workspace = app.query_one(NotesWorkspace)
        assert workspace.find_open
        field = app.query_one("#notes-find", Input)
        assert field.value == "needle"
        field.value = "end"
        await pilot.press("enter", "delete")
        assert app.search_query == "needle"
        assert (vault / note.file).exists()
        await pilot.press("escape")
        assert not workspace.find_open and app.search_query == "needle"
        await pilot.press("e")
        assert isinstance(app.screen, NoteEditorScreen)
        await pilot.press("ctrl+f", "f2", "ctrl+shift+n")
        assert isinstance(app.screen, NoteEditorScreen)
        assert not workspace.find_open


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(45, 18), (70, 24), (120, 36)])
async def test_long_rename_preview_reveals_apply_and_cancel_in_compact_terminal(vault, size):
    note = NotesStore(vault).create("Reference", "Details", file="Notes/Old.md")
    for index in range(11):
        (vault / f"Long reference document {index:02}.md").write_text("[link](Notes/Old.md)", encoding="utf-8")
    app = TaskApp(vault)
    async with app.run_test(size=size) as pilot:
        def footer_regions(screen):
            regions = []
            for selector in ("#rename-apply", "#rename-cancel"):
                button = screen.query_one(selector)
                assert button.parent.id == "rename-buttons"
                assert button.parent.parent.id == "rename-dialog"
                geometry = screen._compositor.find_widget(button)
                shown = geometry.region.intersection(geometry.clip).intersection(screen.region)
                assert shown == geometry.region and shown.area > 0
                regions.append(geometry.region)
            return regions
        await pilot.press("8", "f2")
        screen = app.screen
        initial_footer = footer_regions(screen)
        screen.query_one(Input).value = "New name.md"
        await pilot.press("enter")
        await pilot.pause()
        assert app.focused.id == "rename-apply"
        assert footer_regions(screen) == initial_footer
        preview = screen.query_one("#rename-preview")
        geometry = screen._compositor.find_widget(preview)
        shown = geometry.region.intersection(geometry.clip).intersection(screen.region)
        assert shown.y == geometry.region.y and shown.height >= 3
        screen.query_one("#rename-content").scroll_end(animate=False, immediate=True)
        await pilot.pause()
        assert footer_regions(screen) == initial_footer
        assert (vault / note.file).exists() and not app.history.can_undo
        await pilot.click("#rename-cancel")
        assert (vault / note.file).exists() and not app.history.can_undo
        await pilot.press("f2")
        app.screen.query_one(Input).value = "New name.md"
        await pilot.press("enter")
        assert footer_regions(app.screen) == initial_footer
        await pilot.click("#rename-apply")
        assert (vault / "Notes/New name.md").exists()
        await pilot.press("u")
        assert (vault / note.file).exists() and not (vault / "Notes/New name.md").exists()
