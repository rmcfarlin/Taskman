"""Real keyboard and mouse behavior for the standalone reference widgets."""

import asyncio

import pytest
from textual import on
from textual.app import App, ComposeResult
from textual.widgets import Input, Static, TextArea

from tui.notes import Note, TaskLink
from tui.notes_ui import (
    DiscardNoteScreen, NoteDraft, NoteEditorScreen, NotesList, NotesPreview, NotesWorkspace,
)


NOTES = [
    Note("Notes/First.md", "Purchasing process", "## Approvals\n\nCall the supplier first.",
         "Processes", ("purchasing",), ("Automation",),
         (TaskLink("task-123", "Old task title"),)),
    Note("Notes/Second.md", "Meeting notes", "A decision to keep for later.", "Meetings"),
]


class LibraryApp(App):
    def __init__(self, notes=NOTES):
        super().__init__()
        self.notes = notes
        self.selected = []
        self.activated = []

    def compose(self) -> ComposeResult:
        yield NotesWorkspace(id="library")

    def on_mount(self):
        workspace = self.query_one(NotesWorkspace)
        workspace.set_notes(self.notes, task_labels={"task-123": "Current task title · completed"})
        workspace.focus_list()

    @on(NotesWorkspace.Selected)
    def note_selected(self, event):
        self.selected.append(event.note)

    @on(NotesWorkspace.Activated)
    def note_activated(self, event):
        self.activated.append(event.note)


class EditorApp(App):
    def __init__(self, note=None, *, save_handler=None):
        super().__init__()
        self.editor = NoteEditorScreen(note, categories=("Processes", "Meetings"),
                                       projects=("Automation", "Operations"),
                                       save_handler=save_handler)
        self.results = []

    def compose(self) -> ComposeResult:
        yield Static("Notes")

    def on_mount(self):
        self.push_screen(self.editor, self.results.append)


async def _type(pilot, text):
    await pilot.press(*(character if character != " " else "space" for character in text))


def test_library_keyboard_navigation_and_preview_keep_task_labels():
    async def run():
        app = LibraryApp()
        async with app.run_test(size=(120, 32)) as pilot:
            await pilot.pause()
            workspace = app.query_one(NotesWorkspace)
            assert workspace.current == NOTES[0]
            assert workspace.query_one(NotesList).has_focus
            assert "Current task title · completed" in str(workspace.query_one("#notes-preview-meta", Static).content)
            await pilot.press("down")
            assert workspace.current == NOTES[1]
            assert app.activated == []
            await pilot.press("right")
            assert workspace.query_one(NotesPreview).has_focus
            await pilot.press("left", "up", "enter")
            assert workspace.current == NOTES[0]
            assert app.activated == [NOTES[0]]
            await pilot.press("tab")
            assert workspace.query_one(NotesPreview).has_focus
    asyncio.run(run())


def test_library_mouse_selects_once_and_double_click_activates():
    async def run():
        app = LibraryApp()
        async with app.run_test(size=(120, 32)) as pilot:
            await pilot.pause()
            workspace = app.query_one(NotesWorkspace)
            listing = workspace.query_one(NotesList)
            original = listing.region
            await pilot.click("#notes-list", offset=(4, 2))
            assert workspace.current == NOTES[1]
            assert app.activated == []
            assert listing.region == original
            await pilot.double_click("#notes-list", offset=(4, 0))
            assert workspace.current == NOTES[0]
            assert app.activated == [NOTES[0]]
    asyncio.run(run())


@pytest.mark.parametrize("size", [(120, 32), (70, 24), (45, 18)])
def test_library_layout_fits_and_selection_survives_refresh(size):
    async def run():
        app = LibraryApp()
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            workspace = app.query_one(NotesWorkspace)
            await pilot.press("down")
            workspace.set_notes(NOTES)
            await pilot.pause()
            assert workspace.current == NOTES[1]
            listing = workspace.query_one(NotesList)
            preview = workspace.query_one(NotesPreview)
            assert listing.region.right <= size[0]
            assert preview.region.right <= size[0]
            assert preview.region.bottom <= size[1]
            assert preview.size.height >= 5
            if size[0] < 78:
                assert listing.region.bottom <= preview.region.y
            else:
                assert listing.region.right <= preview.region.x
            workspace.set_notes([])
            await pilot.pause()
            assert workspace.current is None
            assert "Press a" in str(workspace.query_one("#notes-preview-empty", Static).content)
            workspace.set_notes(NOTES, selected_file=NOTES[0].file)
            await pilot.pause()
            assert workspace.current == NOTES[0]
    asyncio.run(run())


def test_editor_capture_uses_keyboard_and_normalizes_labels():
    async def run():
        app = EditorApp()
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            assert app.editor.query_one("#note-title", Input).has_focus
            await _type(pilot, "Supplier reference")
            await pilot.press("enter", "ctrl+a")
            await _type(pilot, "Processes")
            await pilot.press("tab")
            await _type(pilot, "#Vendor, vendor urgent")
            await pilot.press("tab")
            await _type(pilot, "Automation, Operations")
            await pilot.press("tab")
            assert app.editor.query_one(TextArea).has_focus
            await _type(pilot, "Contact the supplier before approving.")
            await pilot.press("ctrl+s")
            assert app.results == [NoteDraft("Supplier reference", "Contact the supplier before approving.",
                                             "Processes", ("Vendor", "urgent"), ("Automation", "Operations"))]
    asyncio.run(run())


def test_editor_requires_title_and_failed_save_retains_every_field():
    calls = []

    def save(draft):
        calls.append(draft)
        return "This file changed outside Taskman. Keep your edits and reload separately."

    async def run():
        app = EditorApp(save_handler=save)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.press("ctrl+s")
            assert app.results == calls == []
            assert "Enter a title" in str(app.editor.query_one("#reference-error").content)
            await _type(pilot, "Keep my draft")
            app.editor.query_one(TextArea).focus()
            await _type(pilot, "Unsaved work")
            await pilot.press("ctrl+s")
            assert app.screen is app.editor
            assert app.results == []
            assert calls[0].title == "Keep my draft"
            assert calls[0].body == "Unsaved work"
            assert app.editor.query_one(TextArea).text == "Unsaved work"
            assert "changed outside" in str(app.editor.query_one("#reference-error").content)
    asyncio.run(run())


def test_editor_escape_preserves_work_until_discard_is_confirmed():
    async def run():
        app = EditorApp(NOTES[0])
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            assert app.editor.query_one(TextArea).has_focus
            await _type(pilot, " More details.")
            await pilot.press("escape")
            assert isinstance(app.screen, DiscardNoteScreen)
            assert app.screen.query_one("#keep-note").has_focus
            await pilot.press("escape")
            assert app.screen is app.editor
            assert app.editor.query_one(TextArea).text.endswith(" More details.")
            assert not app.results
            await pilot.press("escape", "shift+tab", "enter")
            assert app.results == [None]
    asyncio.run(run())


@pytest.mark.parametrize("size", [(80, 24), (45, 18)])
def test_editor_compact_keyboard_reaches_body_and_can_save(size):
    async def run():
        app = EditorApp()
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            body = app.editor.query_one(TextArea)
            fields = app.editor.query_one("#reference-fields")
            assert body.content_region.intersection(fields.content_region).height >= 4
            await _type(pilot, "Small terminal")
            await pilot.press("tab", "tab", "tab", "tab")
            await pilot.pause()
            assert body.has_focus
            assert body.region.overlaps(app.editor.query_one("#reference-fields").region)
            dialog = app.editor.query_one("#reference-dialog")
            assert dialog.region.right <= size[0]
            assert dialog.region.bottom <= size[1]
            await _type(pilot, "Still editable")
            await pilot.press("ctrl+s")
            assert app.results[0].body == "Still editable"
    asyncio.run(run())


def test_unchanged_editor_cancels_without_confirmation():
    async def run():
        app = EditorApp(NOTES[0])
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.press("escape")
            assert app.results == [None]
    asyncio.run(run())
