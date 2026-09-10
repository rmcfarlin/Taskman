"""Compact controls remain legible, clickable, and usable at real terminal sizes."""

import pytest
from textual.app import App
from textual.widgets import Input, TextArea

from tui import app as appmod
from tui.note_templates import MEETING_TEMPLATE
from tui.notes import Note
from tui.notes_dialogs import TemplateEditorScreen
from tui.notes_ui import NotesList, NotesWorkspace
from tui.test_commands import PaletteApp
from tui.test_notes_find import FindApp, _settle, _visible_active_matches
from tui.test_notes_ui import EditorApp
from tui.test_vault_screen import PickerApp, settled


SIZES = [(60, 20), (80, 24), (100, 28), (140, 38)]
pytestmark = pytest.mark.asyncio
TEMPLATE_ERROR = ("This template changed outside Taskman while editing. "
                  "Your draft is still here; reload before saving.")


def fully_visible(screen, selector):
    widget = screen.query_one(selector)
    geometry = screen._compositor.find_widget(widget)
    shown = geometry.region.intersection(geometry.clip).intersection(screen.region)
    assert shown == geometry.region and shown.area > 0
    return widget


class DatesHarness(App):
    CSS = appmod.TaskApp.CSS

    def on_mount(self):
        self.push_screen(appmod.DatesScreen())


@pytest.mark.parametrize("size", SIZES)
async def test_dates_dialog_compact_controls(size):
    app = DatesHarness()
    async with app.run_test(size=size) as pilot:
        dialog = app.screen
        assert isinstance(dialog, appmod.DatesScreen)
        save = fully_visible(dialog, "#ok")
        cancel = fully_visible(dialog, "#cancel")
        assert save.size.height == 1
        assert cancel.size.height == 1
        assert "Save" in str(save.label)
        assert "Cancel" in str(cancel.label)


@pytest.mark.parametrize("size", SIZES)
async def test_note_editor_compact_controls_survive_validation_and_mouse_save(size):
    app = EditorApp()
    async with app.run_test(size=size) as pilot:
        editor = app.editor
        assert not editor.query_one("#reference-error").display
        for selector in ("#reference-save", "#reference-cancel", "#note-title"):
            assert fully_visible(editor, selector).size.height == 1
        field = editor.query_one("#note-category", Input)
        assert field.styles.background != editor.query_one("#reference-fields").styles.background
        await pilot.click("#reference-save")
        assert editor.query_one("#reference-error").display
        fully_visible(editor, "#reference-error")
        fully_visible(editor, "#reference-save")
        fully_visible(editor, "#reference-cancel")
        editor.query_one("#note-title", Input).value = "Meeting follow-up"
        editor.query_one(TextArea).load_text("Keep the decision.")
        # Let the first button press finish its active effect before retrying
        # with the mouse, as a user would after reading the validation error.
        await pilot.pause(0.4)
        await pilot.click("#reference-save")
        assert app.results[0].title == "Meeting follow-up"
        assert app.results[0].body == "Keep the decision."


class TemplateApp(App):
    def __init__(self):
        super().__init__()
        self.editor = TemplateEditorScreen(MEETING_TEMPLATE, save_handler=self.save)
        self.results = []
        self.saved = []

    def save(self, value):
        self.saved.append(value)
        return TEMPLATE_ERROR if len(self.saved) == 1 else None

    def on_mount(self):
        self.push_screen(self.editor, self.results.append)


@pytest.mark.parametrize("size", SIZES)
async def test_template_editor_footer_and_error_stay_visible(size):
    app = TemplateApp()
    async with app.run_test(size=size) as pilot:
        assert not app.editor.query_one("#template-error").display
        assert fully_visible(app.editor, "#template-save").size.height == 1
        assert fully_visible(app.editor, "#template-cancel").size.height == 1
        body = app.editor.query_one(TextArea)
        body.load_text("Draft to keep")
        await pilot.click("#template-save")
        assert app.screen is app.editor and body.text == "Draft to keep"
        error = fully_visible(app.editor, "#template-error")
        visible_error = " ".join(error.render_line(row).text for row in range(error.size.height))
        assert " ".join(visible_error.split()) == TEMPLATE_ERROR
        fully_visible(app.editor, "#template-cancel")
        await pilot.press("ctrl+s")
        assert app.results == ["Draft to keep"]


@pytest.mark.parametrize("size", SIZES)
async def test_vault_compact_controls_browse_and_cancel_without_changes(size, tmp_path):
    (tmp_path / "Reference").mkdir()
    app = PickerApp(tmp_path)
    async with app.run_test(size=size) as pilot:
        await settled(app, pilot)
        for selector in ("#vault-path", "#vault-open", "#vault-setup", "#vault-cancel"):
            assert fully_visible(app.picker, selector).size.height == 1
        assert app.picker.query_one("#vault-folders").content_size.height >= 3
        await pilot.click("#vault-cancel")
        assert app.choices == [None]
        assert sorted(path.name for path in tmp_path.iterdir()) == ["Reference"]


@pytest.mark.parametrize("size", SIZES)
async def test_palette_keeps_clickable_close_and_one_row_search(size):
    app = PaletteApp()
    async with app.run_test(size=size) as pilot:
        assert fully_visible(app.palette, "#command-input").size.height == 1
        assert fully_visible(app.palette, "#command-close").size.height == 1
        await pilot.click("#command-close")
        assert app.results == [None]


async def test_notes_resize_keeps_selection_active_find_and_readable_category():
    note = Note("Notes/Decision.md", "Supplier decision", "First needle.\n\n" +
                "\n\n".join(f"Paragraph {i}." for i in range(25)) + "\n\nFinal needle.",
                "Meetings", ("supplier",), ("Operations",))
    app = FindApp([Note("Notes/Other.md", "Other", "Other note"), note], "needle")
    async with app.run_test(size=(140, 38)) as pilot:
        await pilot.press("down", "ctrl+f", "enter")
        await _settle(pilot)
        workspace = app.query_one(NotesWorkspace)
        assert workspace.current == note and workspace._find_index == 1
        for size in [(60, 20), (80, 24), (100, 28), (140, 14), (140, 38)]:
            await pilot.resize_terminal(*size)
            await _settle(pilot)
            assert workspace.current == note
            assert workspace._find_index == 1 and len(workspace._find_matches) == 2
            assert _visible_active_matches(app)
            listing = workspace.query_one(NotesList)
            lines = [listing.render_line(row).text for row in range(listing.size.height)]
            if size[0] < 78 or size[1] < 16:
                assert any("Supplier" in line and "Meetings" in line for line in lines)
            meta = str(workspace.query_one("#notes-preview-meta").content)
            assert "Meetings" in meta and "#supplier" in meta and "Operations" in meta
