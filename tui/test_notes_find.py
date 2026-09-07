"""Find searches the rendered note and moves the visible preview to each hit."""

import asyncio

import pytest
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.geometry import Region
from textual.widgets import Input, Markdown, Static, TextArea

from tui.notes import Note
from tui.notes_ui import (
    DiscardNoteScreen, NoteDraft, NoteEditorScreen, NoteFindInput,
    NotesList, NotesPreview, NotesWorkspace,
)


class FindApp(App):
    BINDINGS = [Binding("escape", "global_escape", show=False)]

    def __init__(self, notes, query=""):
        super().__init__()
        self.notes = notes
        self.library_query = query
        self.global_escapes = 0
        self.activations = []

    def compose(self) -> ComposeResult:
        yield NotesWorkspace()

    def on_mount(self):
        workspace = self.query_one(NotesWorkspace)
        workspace.set_notes(self.notes)
        workspace.set_library_query(self.library_query)
        workspace.focus_list()

    def action_global_escape(self):
        self.global_escapes += 1
        self.library_query = ""

    def on_notes_workspace_activated(self, event):
        self.activations.append(event.note.file)


async def _settle(pilot):
    """Wait for Markdown parsing, painting, then the following scroll frame."""
    await pilot.pause()
    await pilot.app.workers.wait_for_complete()
    await pilot.pause()
    await pilot.pause()


def _count(app):
    return str(app.query_one("#notes-find-count", Static).content)


def _visible_active_matches(app):
    """Read real rendered output, not the implementation's offset estimate."""
    workspace = app.query_one(NotesWorkspace)
    window = app.query_one(NotesPreview).scrollable_content_region
    regions = []
    for widget, _, _ in workspace._find_blocks:
        for row in range(widget.content_size.height):
            column = 0
            for segment in widget.render_line(row):
                style = segment.style
                if style and style.meta.get("note_find_match") == workspace._find_index:
                    region = Region(widget.content_region.x + column,
                                    widget.content_region.y + row,
                                    segment.cell_length, 1)
                    if region.intersection(window):
                        regions.append(region)
                column += segment.cell_length
    return regions


@pytest.mark.parametrize("size", [(120, 32), (70, 24), (45, 14)])
def test_find_jumps_offscreen_and_wraps_both_directions(size):
    async def run():
        note = Note("Notes/Long.md", "Long note", "First NEEDLE.\n\n" +
                    "\n\n".join(f"Paragraph {i}: useful context." for i in range(60)) +
                    "\n\nLast needle.")
        app = FindApp([note], "needle")
        async with app.run_test(size=size) as pilot:
            workspace = app.query_one(NotesWorkspace)
            preview = app.query_one(NotesPreview)
            await _settle(pilot)
            assert not app.query_one("#notes-findbar").display
            await pilot.press("ctrl+f")
            await _settle(pilot)
            assert app.query_one(NoteFindInput).has_focus
            assert _count(app) == "1/2"
            assert _visible_active_matches(app)
            first_scroll = preview.scroll_y
            await pilot.press("enter")
            await _settle(pilot)
            assert _count(app) == "2/2"
            assert preview.scroll_y > first_scroll + 30
            assert _visible_active_matches(app)
            bar = app.query_one("#notes-findbar")
            assert bar.region.y == preview.content_region.y
            assert bar.region.right <= size[0]
            assert app.query_one("#notes-find-close").region.right <= size[0]
            await pilot.press("enter")
            await _settle(pilot)
            assert _count(app) == "1/2"
            assert _visible_active_matches(app)
            await pilot.press("shift+enter")
            await _settle(pilot)
            assert _count(app) == "2/2"
            assert _visible_active_matches(app)
            await pilot.press("escape")
            await _settle(pilot)
            assert not workspace.find_open
            assert app.query_one(NotesList).has_focus
            assert app.global_escapes == 0
            assert app.library_query == "needle"
            assert not app.activations
    asyncio.run(run())


@pytest.mark.parametrize("size", [(110, 28), (45, 14)])
def test_match_deep_inside_wrapped_paragraph_is_visible(size):
    async def run():
        app = FindApp([Note("Notes/Wrap.md", "Wrapped", "界 context " * 500 + "FiNaL-Hit")])
        async with app.run_test(size=size) as pilot:
            await pilot.press("ctrl+f")
            app.query_one(NoteFindInput).value = "final-hit"
            await _settle(pilot)
            assert _count(app) == "1/1"
            assert app.query_one(NotesPreview).scroll_y > 50
            assert _visible_active_matches(app)
            await pilot.resize_terminal(60, 18)
            await _settle(pilot)
            assert _visible_active_matches(app)
    asyncio.run(run())


def test_find_keeps_markdown_and_searches_formatted_text_code_tables_unicode():
    async def run():
        note = Note("Notes/Unicode.md", "Straße", "# Straße\n\n"
                    "A **STRASSE** and [Straße](https://example.org).\n\n"
                    "```text\nStraße\nnext line\n```\n\n"
                    "| Place | Meaning |\n| --- | --- |\n| Straße | Road |\n\n"
                    "Literal [a.*] punctuation and Ελληνικά.")
        app = FindApp([note])
        async with app.run_test(size=(90, 25)) as pilot:
            await _settle(pilot)
            markdown = app.query_one(Markdown)
            blocks = list(markdown.query(Static))
            originals = [widget.content for widget in blocks]
            await pilot.press("right", "ctrl+f")
            field = app.query_one(NoteFindInput)
            field.value = "strasse"
            await _settle(pilot)
            assert _count(app) == "1/5"
            assert list(markdown.query(Static)) == blocks
            assert markdown.query("MarkdownTable")
            assert markdown.query("MarkdownFence")
            for _ in range(5):
                assert _visible_active_matches(app)
                await pilot.press("enter")
                await _settle(pilot)
            field.value = "[a.*]"
            await _settle(pilot)
            assert _count(app) == "1/1"
            assert _visible_active_matches(app)
            field.value = "ελληνικά"
            await _settle(pilot)
            assert _count(app) == "1/1"
            field.value = "Straße\nnext line"
            await _settle(pilot)
            assert _count(app) == "1/1"
            assert _visible_active_matches(app)
            await pilot.press("escape")
            await _settle(pilot)
            assert app.query_one(NotesPreview).has_focus
            assert [widget.content for widget in blocks] == originals
    asyncio.run(run())


def test_find_scrolls_both_axes_for_a_match_in_a_long_code_block():
    async def run():
        body = "```text\n" + "\n".join("context" for _ in range(50))
        body += "\n" + "x" * 150 + " TARGET\n```"
        app = FindApp([Note("Notes/Code.md", "Code", body)], "target")
        async with app.run_test(size=(60, 18)) as pilot:
            await pilot.press("ctrl+f")
            await _settle(pilot)
            assert _count(app) == "1/1"
            assert app.query_one(NotesPreview).scroll_y > 30
            fence = app.query_one("MarkdownFence")
            assert fence.scroll_x > 50
            matches = _visible_active_matches(app)
            assert matches
            assert all(region in fence.scrollable_content_region for region in matches)
    asyncio.run(run())


def test_find_missing_empty_query_mouse_controls_and_note_refresh():
    async def run():
        notes = [Note("Notes/A.md", "A", "Cat cat"), Note("Notes/B.md", "B", "cat")]
        app = FindApp(notes, "cat")
        async with app.run_test(size=(80, 24)) as pilot:
            workspace = app.query_one(NotesWorkspace)
            await pilot.press("ctrl+f")
            await _settle(pilot)
            await pilot.click("#notes-find-next")
            await _settle(pilot)
            assert _count(app) == "2/2"
            assert app.query_one(NoteFindInput).has_focus
            await pilot.click("#notes-find-previous")
            await _settle(pilot)
            assert _count(app) == "1/2"
            workspace.set_notes(notes, "Notes/B.md")
            await _settle(pilot)
            assert _count(app) == "1/1"
            assert _visible_active_matches(app)
            workspace.set_notes([Note("Notes/B.md", "B", "cat cat cat")])
            await _settle(pilot)
            assert _count(app) == "1/3"
            field = app.query_one(NoteFindInput)
            field.value = "missing"
            await _settle(pilot)
            assert _count(app) == "0/0"
            assert app.query_one("#notes-find-next").disabled
            assert not _visible_active_matches(app)
            await pilot.press("enter", "shift+enter")
            assert _count(app) == "0/0"
            field.value = ""
            await _settle(pilot)
            assert _count(app) == "—"
            workspace.set_notes([])
            await _settle(pilot)
            assert _count(app) == "—"
            field.value = "cat"
            await _settle(pilot)
            assert _count(app) == "0/0"
            await pilot.click("#notes-find-close")
            await _settle(pilot)
            assert not workspace.find_open
            assert app.library_query == "cat"
    asyncio.run(run())


def test_library_seed_does_not_replace_edited_find_and_modal_is_unaffected():
    async def run():
        app = FindApp([Note("Notes/A.md", "A", "cat dog")], "cat")
        async with app.run_test(size=(90, 30)) as pilot:
            workspace = app.query_one(NotesWorkspace)
            await pilot.press("ctrl+f")
            app.query_one(NoteFindInput).value = "dog"
            await _settle(pilot)
            await pilot.press("escape")
            workspace.set_library_query("different library filter")
            await pilot.press("ctrl+f")
            assert app.query_one(NoteFindInput).value == "dog"
            await pilot.press("escape")
            editor = NoteEditorScreen(initial=NoteDraft("Draft", "cat dog"))
            app.push_screen(editor)
            await _settle(pilot)
            await pilot.press("ctrl+f")
            assert not workspace.find_open
            body = editor.query_one(TextArea)
            body.focus()
            await pilot.press("ctrl+f", "/", "enter")
            assert not workspace.find_open
            assert "/" in body.text
            assert "\n" in body.text
            assert editor.query_one("#note-title", Input).value == "Draft"
    asyncio.run(run())


def test_hidden_workspace_can_replace_notes_without_retaining_old_matches():
    async def run():
        app = FindApp([Note("Notes/Old.md", "Old vault", "needle needle")], "needle")
        async with app.run_test(size=(70, 20)) as pilot:
            workspace = app.query_one(NotesWorkspace)
            await pilot.press("ctrl+f", "enter")
            await _settle(pilot)
            assert _count(app) == "2/2"
            # The Tasks view hides this same mounted workspace.
            workspace.display = False
            await _settle(pilot)
            workspace.display = True
            await _settle(pilot)
            assert workspace.find_open
            assert _count(app) == "2/2"
            assert _visible_active_matches(app)
            # A newly opened vault can replace the list while Notes is hidden.
            workspace.display = False
            workspace.set_notes([])
            workspace.set_library_query("")
            await _settle(pilot)
            assert _count(app) == "0/0"
            workspace.set_notes([Note("Notes/New.md", "New vault",
                                      "\n\n".join("Context" for _ in range(40)) + "\n\nneedle")])
            await _settle(pilot)
            assert _count(app) == "1/1"
            assert workspace.current.title == "New vault"
            workspace.display = True
            workspace.focus_preview()
            await _settle(pilot)
            assert _visible_active_matches(app)
            assert app.query_one(NotesPreview).scroll_y > 30
    asyncio.run(run())


class DraftApp(App):
    def __init__(self, initial):
        super().__init__()
        self.editor = NoteEditorScreen(initial=initial)
        self.results = []

    def compose(self):
        yield Static("Fixture")

    def on_mount(self):
        self.push_screen(self.editor, self.results.append)


def test_prefilled_new_draft_can_save_and_unchanged_cancel_is_clean():
    async def run():
        initial = NoteDraft("Meeting", "## Decisions\n\n", "Meetings", ("meeting",), ("Work",))
        for key in ("ctrl+s", "escape"):
            app = DraftApp(initial)
            async with app.run_test(size=(80, 24)) as pilot:
                assert app.editor.note is None
                assert app.editor.query_one("#reference-dialog").border_title == "New note"
                assert app.editor._draft() == initial
                assert not str(app.editor.query_one("#reference-status", Static).content)
                await pilot.press(key)
                assert app.results == [initial if key == "ctrl+s" else None]
    asyncio.run(run())


def test_prefilled_draft_edits_still_require_discard_confirmation():
    async def run():
        app = DraftApp(NoteDraft("Meeting", "Template"))
        async with app.run_test(size=(80, 24)) as pilot:
            app.editor.query_one(TextArea).focus()
            await pilot.press("x", "escape")
            await _settle(pilot)
            assert isinstance(app.screen, DiscardNoteScreen)
            assert not app.results
            await pilot.press("escape")
            await _settle(pilot)
            assert app.screen is app.editor
            assert app.editor.query_one(TextArea).text == "xTemplate"
    asyncio.run(run())


def test_initial_draft_cannot_override_an_existing_note():
    with pytest.raises(ValueError, match="new note"):
        NoteEditorScreen(Note("Notes/A.md", "A", "Body"), initial=NoteDraft("B", "Other"))
