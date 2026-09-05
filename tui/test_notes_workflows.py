"""Exercise real Notes UI actions against disposable Markdown vaults."""
from dataclasses import replace

import pytest
from textual.widgets import Input, OptionList, TextArea

from tui import taskman as tm
from tui.app import Inspector, TaskApp, TaskList
from tui.commands import CommandScreen
from tui.notes import NotesStore
from tui.notes_ui import NoteEditorScreen, NotesWorkspace


@pytest.fixture
def vault(tmp_path):
    (tmp_path / "Tasks").mkdir()
    (tmp_path / "Projects").mkdir()
    (tmp_path / "Tasks/Inbox.md").write_text("# Inbox\n\n- [ ] Review procedure\n- [ ] Other task\n", encoding="utf-8")
    return tmp_path


async def choose(pilot, text):
    await pilot.press(*["space" if c == " " else c for c in text])
    await pilot.press("enter")
    await pilot.pause()


@pytest.mark.asyncio
async def test_capture_edit_and_history(vault):
    app = TaskApp(vault)
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.press("8", "a")
        assert isinstance(app.screen, NoteEditorScreen)
        app.screen.query_one("#note-title", Input).value = "Purchasing reference"
        app.screen.query_one("#note-category", Input).value = "Procedures"
        app.screen.query_one("#note-tags", Input).value = "#purchasing, suppliers"
        app.screen.query_one("#note-projects", Input).value = "Operations"
        app.screen.query_one("#reference-text", TextArea).load_text("## Escalation\nCall purchasing before approval.")
        await pilot.press("ctrl+s")
        await pilot.pause()
        note = app.query_one(NotesWorkspace).current
        assert note and note.title == "Purchasing reference"
        assert note.category == "Procedures" and note.tags == ("purchasing", "suppliers")
        assert note.projects == ("Operations",)
        assert app.view == "notes" and not app.query_one(TaskList).display
        assert app.focused.id == "notes-list"
        await pilot.press("e")
        app.screen.query_one("#reference-text", TextArea).load_text("Revised reference material")
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert NotesStore(vault).load(note.file).body == "Revised reference material"
        await pilot.press("u")
        assert NotesStore(vault).load(note.file).body.startswith("## Escalation")
        assert app.view == "notes"
        await pilot.press("ctrl+y")
        assert NotesStore(vault).load(note.file).body == "Revised reference material"


@pytest.mark.asyncio
async def test_search_body_and_category_tag_project_filters(vault):
    store = NotesStore(vault)
    store.create("One", "Calibration reference code XYZ42", category="Equipment", tags=("quality",), projects=("Operations",))
    store.create("Two", "Vendor contact details", category="Suppliers", tags=("contact",))
    app = TaskApp(vault)
    async with app.run_test(size=(110, 32)) as pilot:
        await pilot.press("8", "/")
        app.query_one("#search", Input).value = "xyz42"
        await pilot.press("enter")
        assert app.query_one(NotesWorkspace).current.title == "One"
        assert app.query_one("#notes-list", OptionList).option_count == 1
        await pilot.press("escape", "c")
        assert isinstance(app.screen, CommandScreen)
        await choose(pilot, "Equipment")
        assert app.note_category == "Equipment"
        assert app.query_one("#notes-list", OptionList).option_count == 1
        await pilot.press("escape", "t")
        await choose(pilot, "contact")
        assert app.query_one(NotesWorkspace).current.title == "Two"
        await pilot.press("escape", "j")
        await choose(pilot, "Operations")
        assert app.note_project == "Operations"
        assert app.query_one(NotesWorkspace).current.title == "One"


@pytest.mark.asyncio
async def test_link_from_task_open_backlinks_and_undo(vault):
    note = NotesStore(vault).create("Reference manual", "Persistent instructions")
    before = (vault / "Tasks/Inbox.md").read_bytes()
    app = TaskApp(vault)
    async with app.run_test(size=(120, 36)) as pilot:
        target = app._selected()
        await pilot.press("l")
        await choose(pilot, "Reference manual")
        linked = NotesStore(vault).load(note.file)
        assert len(linked.tasks) == 1
        assert tm.find_task_by_anchor(tm.load_all(vault), linked.tasks[0].id).description == target.description
        await pilot.press("i")
        assert app.query_one("#ins-references", OptionList).option_count == 1
        await pilot.press("k")
        await choose(pilot, "Reference manual")
        assert app.view == "notes" and app.query_one(NotesWorkspace).current.file == note.file
        assert app.focused.id == "notes-list"
        await pilot.press("k", "enter")
        assert app.view == "all" and app._selected().description == target.description
        await pilot.press("u")
        assert (vault / "Tasks/Inbox.md").read_bytes() == before
        assert NotesStore(vault).load(note.file).tasks == ()


@pytest.mark.asyncio
async def test_note_survives_task_completion_deletion_and_can_unlink(vault):
    note = NotesStore(vault).create("Reference", "Keep this forever")
    app = TaskApp(vault)
    async with app.run_test(size=(110, 34)) as pilot:
        await pilot.press("8", "l")
        await choose(pilot, "Review procedure")
        linked = NotesStore(vault).load(note.file)
        task = tm.find_task_by_anchor(tm.load_all(vault), linked.tasks[0].id)
        tm.toggle(vault, task)
        await pilot.press("r", "k", "enter")
        assert app.view == "completed"
        assert app._selected().done
        tm.delete_task(vault, app._selected())
        await pilot.press("8", "r")
        assert NotesStore(vault).load(note.file).body == "Keep this forever"
        await pilot.press("k")
        assert isinstance(app.screen, CommandScreen)
        assert not app.screen.commands[0].enabled
        await pilot.press("escape", "ctrl+k")
        await choose(pilot, "Unlink task from note")
        await pilot.press("enter")
        assert NotesStore(vault).load(note.file).tasks == ()


@pytest.mark.asyncio
async def test_create_task_from_note_keeps_body_and_links(vault):
    note = NotesStore(vault).create("Review supplier terms", "Full reference remains here")
    app = TaskApp(vault)
    async with app.run_test(size=(110, 34)) as pilot:
        await pilot.press("8", "ctrl+t")
        assert app.screen.query_one("#text", Input).value == note.title
        await pilot.press("enter")
        await pilot.pause()
        saved = NotesStore(vault).load(note.file)
        assert saved.body == note.body and len(saved.tasks) == 1
        assert tm.find_task_by_anchor(tm.load_all(vault), saved.tasks[0].id).description == note.title
        await pilot.press("u")
        assert len(tm.load_all(vault)) == 2
        assert NotesStore(vault).load(note.file).tasks == ()


@pytest.mark.asyncio
async def test_external_editor_conflict_retains_draft(vault):
    note = NotesStore(vault).create("Shared note", "Original")
    app = TaskApp(vault)
    async with app.run_test(size=(110, 34)) as pilot:
        await pilot.press("8", "e")
        editor = app.screen
        editor.query_one("#reference-text", TextArea).load_text("My unsaved draft")
        NotesStore(vault).save(replace(note, body="External update"))
        await pilot.press("ctrl+s")
        assert app.screen is editor
        assert editor.query_one("#reference-text", TextArea).text == "My unsaved draft"
        assert NotesStore(vault).load(note.file).body == "External update"


@pytest.mark.asyncio
async def test_notes_actions_do_not_mutate_hidden_task_selection(vault):
    NotesStore(vault).create("General reference", "No task attached")
    before = (vault / "Tasks/Inbox.md").read_bytes()
    app = TaskApp(vault)
    async with app.run_test(size=(80, 28)) as pilot:
        await pilot.press("8", "space", "x", "delete", "d", "p", "s", "right_square_bracket")
        assert app._selected() is None
        assert (vault / "Tasks/Inbox.md").read_bytes() == before
        await pilot.press("alt+3")
        assert app.focused.id == "notes-preview"
        await pilot.press("alt+2", "1")
        assert app.view == "all" and app.query_one(TaskList).display
        assert not app.query_one(NotesWorkspace).display


@pytest.mark.asyncio
async def test_enter_opens_note_and_rescan_preserves_browsed_selection(vault):
    store = NotesStore(vault)
    store.create("First", "First body")
    second = store.create("Second", "Second body")
    app = TaskApp(vault)
    async with app.run_test(size=(110, 34)) as pilot:
        await pilot.press("8", "down", "r")
        assert app.query_one(NotesWorkspace).current.file == second.file
        await pilot.press("enter")
        assert isinstance(app.screen, NoteEditorScreen)
        assert app.screen.query_one("#reference-text", TextArea).text == "Second body"


@pytest.mark.asyncio
async def test_inspector_reference_enter_opens_library(vault):
    note = NotesStore(vault).create("Reference", "Instructions")
    app = TaskApp(vault)
    async with app.run_test(size=(120, 36)) as pilot:
        app._link_reference(note, app._selected())
        await pilot.press("i")
        links = app.query_one("#ins-references", OptionList)
        links.focus()
        links.highlighted = 0
        await pilot.press("enter")
        assert app.view == "notes"
        assert app.query_one(NotesWorkspace).current.file == note.file


@pytest.mark.asyncio
async def test_failed_link_save_rolls_back_anchor_and_keeps_external_note(vault, monkeypatch):
    from tui.notes import NoteConflict
    note = NotesStore(vault).create("Reference", "Original")
    before = (vault / "Tasks/Inbox.md").read_bytes()
    app = TaskApp(vault)
    async with app.run_test(size=(110, 34)):
        def fail_save(changed):
            NotesStore(vault).save(replace(note, body="External change"))
            raise NoteConflict("External edit detected")
        monkeypatch.setattr(app.notes_store, "save", fail_save)
        with pytest.raises(NoteConflict):
            app._link_reference(note, app._selected())
        assert (vault / "Tasks/Inbox.md").read_bytes() == before
        assert NotesStore(vault).load(note.file).body == "External change"
        assert not app.history.can_undo  # An external edit is not our undo entry.


@pytest.mark.asyncio
async def test_switching_vault_resets_notes_filters_selection_and_history(vault):
    from tui.history import History
    NotesStore(vault).create("Original", "First vault", category="Original category")
    second = vault / "Other vault"
    (second / "Tasks").mkdir(parents=True)
    NotesStore(second).create("Different", "Second vault")
    app = TaskApp(vault)
    async with app.run_test(size=(110, 34)) as pilot:
        await pilot.press("8")
        app.note_category = "Original category"
        app._finish_vault_open(app._open_generation, (second, tm.Store(second), History(second)), None)
        await pilot.press("8")
        assert app.query_one(NotesWorkspace).current.title == "Different"
        assert app.note_category == app.note_tag == app.note_project == ""
        assert not app.history.can_undo


@pytest.mark.asyncio
async def test_create_task_failure_does_not_leave_an_unlinked_task(vault, monkeypatch):
    note = NotesStore(vault).create("Reference", "Body")
    before = (vault / "Tasks/Inbox.md").read_bytes()
    app = TaskApp(vault)
    async with app.run_test(size=(110, 34)) as pilot:
        def fail_save(changed):
            raise OSError("Disk write failed")
        monkeypatch.setattr(app.notes_store, "save", fail_save)
        await pilot.press("8", "ctrl+t", "enter")
        assert (vault / "Tasks/Inbox.md").read_bytes() == before
        assert NotesStore(vault).load(note.file).tasks == ()
        assert not app.history.can_undo
