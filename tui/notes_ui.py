"""Keyboard-first browsing and editing for the Markdown reference library.

These widgets own presentation only. Callers load and save notes, resolve task
links, and choose what activation means without importing the main application.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
import re

from rich.text import Text
from rich.table import Table
from textual import events, on
from textual.app import ComposeResult
from textual.await_complete import AwaitComplete
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.content import Content
from textual.geometry import Region
from textual.message import Message
from textual.screen import ModalScreen
from textual.style import Style
from textual.suggester import SuggestFromList
from textual.visual import Visual
from textual.widgets import Button, Input, Label, Markdown, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from .notes import Note
from .dialog_style import COMPACT_DIALOG_CSS


@dataclass(frozen=True)
class NoteDraft:
    title: str
    body: str
    category: str = "Unfiled"
    tags: tuple[str, ...] = ()
    projects: tuple[str, ...] = ()


def _labels(value: str, *, tags: bool = False) -> tuple[str, ...]:
    """Normalize editor lists without changing a user's capitalization."""
    result: list[str] = []
    seen: set[str] = set()
    for item in re.split(r"[\s,]+" if tags else r",", value):
        item = item.strip().lstrip("#") if tags else item.strip()
        key = item.casefold()
        if item and key not in seen:
            result.append(item)
            seen.add(key)
    return tuple(result)


class NotesList(OptionList):
    """A click selects; Enter or a double click opens the editor."""

    BINDINGS = [
        Binding("right", "preview", "Preview", show=False),
    ]

    async def _on_click(self, event: events.Click) -> None:
        event.stop()
        event.prevent_default()
        index: int | None = event.style.meta.get("option")
        if index is None:
            return
        option = self.get_option_at_index(index)
        if option.disabled:
            return
        self.highlighted = index
        if event.chain >= 2:
            self.action_select()

    def action_preview(self) -> None:
        if isinstance(self.parent, NotesWorkspace):
            self.parent.focus_preview()


class NotesPreview(VerticalScroll):
    BINDINGS = [Binding("left", "list", "Notes", show=False)]

    def action_list(self) -> None:
        if isinstance(self.parent, NotesWorkspace):
            self.parent.focus_list()


class NoteFindInput(Input):
    """Find keys stay local even when the application also binds Escape."""

    BINDINGS = [
        Binding("enter", "next_match", "Next match", show=False, priority=True),
        Binding("shift+enter", "previous_match", "Previous match", show=False, priority=True),
        Binding("escape", "close_find", "Close Find", show=False, priority=True),
        Binding("ctrl+a", "select_all", "Select all", show=False),
    ]

    def action_next_match(self) -> None:
        self.query_ancestor(NotesWorkspace).action_next_match()

    def action_previous_match(self) -> None:
        self.query_ancestor(NotesWorkspace).action_previous_match()

    def action_close_find(self) -> None:
        self.query_ancestor(NotesWorkspace).dismiss_find()


def _literal_matches(text: str, query: str) -> list[tuple[int, int]]:
    """Casefold while keeping offsets in the original Unicode text."""
    if not query:
        return []
    folded: list[str] = []
    offsets: list[int] = []
    for index, character in enumerate(text):
        value = character.casefold()
        folded.append(value)
        offsets.extend([index] * len(value))
    haystack, needle = "".join(folded), query.casefold()
    matches: list[tuple[int, int]] = []
    cursor = 0
    while (position := haystack.find(needle, cursor)) >= 0:
        span = offsets[position], offsets[position + len(needle) - 1] + 1
        if not matches or span != matches[-1]:
            matches.append(span)
        cursor = position + len(needle)
    return matches


class NotesWorkspace(Horizontal):
    """A note list and readable preview, with one selected note at a time."""

    BINDINGS = [
        Binding("ctrl+f", "find", "Find in note", show=False),
        Binding("escape", "close_find", "Close Find", show=False),
        Binding("enter", "next_match", "Next match", show=False),
        Binding("shift+enter", "previous_match", "Previous match", show=False),
    ]

    DEFAULT_CSS = """
    NotesWorkspace {
        height: 1fr;
        width: 1fr;
        background: $background;
    }
    NotesWorkspace #notes-list {
        width: 34%;
        min-width: 25;
        max-width: 46;
        height: 1fr;
        padding: 0 1;
        border: none;
        border-right: solid $primary-muted;
        background: $background;
    }
    NotesWorkspace #notes-list:focus {
        border: none;
        border-right: solid $accent;
        background: $background;
        background-tint: transparent;
    }
    NotesWorkspace #notes-list > .option-list--option {
        padding: 0 1;
    }
    NotesWorkspace #notes-list > .option-list--option-hover {
        background: transparent;
    }
    NotesWorkspace #notes-list > .option-list--option-highlighted {
        background: $primary 15%;
        color: $text;
        text-style: bold;
    }
    NotesWorkspace #notes-list:focus > .option-list--option-highlighted {
        background: $primary 25%;
    }
    NotesWorkspace #notes-preview {
        width: 1fr;
        height: 1fr;
        padding: 0 2;
        border: none;
        background: $background;
        background-tint: transparent;
    }
    NotesWorkspace #notes-preview:focus {
        background: $background;
        background-tint: transparent;
    }
    NotesWorkspace #notes-findbar {
        display: none;
        dock: top;
        height: 1;
        width: 1fr;
        background: $background;
    }
    NotesWorkspace #notes-findbar Label { width: 5; height: 1; color: $text-muted; }
    NotesWorkspace #notes-find {
        width: 1fr; min-width: 3; height: 1; border: none; padding: 0;
        background: $primary 15%; background-tint: transparent;
    }
    NotesWorkspace #notes-find:focus { border: none; background: $primary 25%; }
    NotesWorkspace #notes-find-count {
        width: auto; min-width: 4; max-width: 12; height: 1;
        margin: 0 1; color: $text-muted;
    }
    NotesWorkspace #notes-findbar Button {
        width: 3; min-width: 3; height: 1; min-height: 1;
        margin: 0; padding: 0; border: none;
        background: $background; color: $text-accent;
    }
    NotesWorkspace #notes-findbar Button:hover,
    NotesWorkspace #notes-findbar Button:focus { background: $primary 25%; }
    NotesWorkspace #notes-preview-title {
        height: auto;
        margin-bottom: 1;
        color: $text;
        text-style: bold;
    }
    NotesWorkspace #notes-preview:focus #notes-preview-title {
        color: $text-accent;
        text-style: bold underline;
    }
    NotesWorkspace #notes-preview-meta {
        height: auto;
        margin-bottom: 1;
        color: $text-muted;
    }
    NotesWorkspace #notes-preview-body {
        height: auto;
        padding: 0;
        margin: 0;
        background: $background;
    }
    NotesWorkspace #notes-preview-body MarkdownHeader {
        margin: 0 0 1 0;
        color: $text-accent;
        background: $background;
        text-style: bold;
    }
    NotesWorkspace #notes-preview-empty {
        height: auto;
        padding: 1 0;
        color: $text-muted;
    }
    NotesWorkspace.-compact {
        layout: vertical;
    }
    NotesWorkspace.-compact #notes-list {
        width: 1fr;
        min-width: 0;
        max-width: 100%;
        height: 6;
        border-right: none;
        border-bottom: solid $primary-muted;
    }
    NotesWorkspace.-compact #notes-list:focus {
        border-right: none;
        border-bottom: solid $accent;
    }
    NotesWorkspace.-compact #notes-preview {
        padding: 0 1;
    }
    NotesWorkspace.-compact.-short #notes-list {
        height: 4;
    }
    """

    class Selected(Message):
        def __init__(self, note: Note | None) -> None:
            super().__init__()
            self.note = note

    class Activated(Message):
        def __init__(self, note: Note) -> None:
            super().__init__()
            self.note = note

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._notes: dict[str, Note] = {}
        self._selected_file = ""
        self._task_labels: dict[str, str] = {}
        self._pending: tuple[list[Note], str] | None = None
        self._library_query = ""
        self._find_query = ""
        self._find_open = False
        self._find_return_to_list = False
        self._find_blocks: list[tuple[Static, Content, int]] = []
        self._find_matches: list[tuple[int, int]] = []
        self._find_index = -1
        self._preview_generation = 0
        self._preview_loading = False

    def compose(self) -> ComposeResult:
        yield NotesList(id="notes-list")
        with NotesPreview(id="notes-preview", can_focus=True):
            with Horizontal(id="notes-findbar"):
                yield Label("Find")
                yield NoteFindInput(placeholder="In this note", compact=True, id="notes-find")
                yield Static("", id="notes-find-count")
                yield Button("↑", id="notes-find-previous", tooltip="Previous match · Shift+Enter")
                yield Button("↓", id="notes-find-next", tooltip="Next match · Enter")
                yield Button("×", id="notes-find-close", tooltip="Close Find · Esc")
            yield Static("", id="notes-preview-title")
            yield Static("", id="notes-preview-meta")
            yield Markdown("", id="notes-preview-body")
            yield Static("", id="notes-preview-empty")

    def on_mount(self) -> None:
        self.set_class(self.size.width < 78, "-compact")
        self.set_class(self.size.height < 16, "-short")
        if self._pending is not None:
            notes, selected = self._pending
            self._pending = None
            self.set_notes(notes, selected)
        else:
            self._show_preview()

    def on_resize(self, event: events.Resize) -> None:
        was_dense = self.has_class("-compact") or self.has_class("-short")
        self.set_class(event.size.width < 78, "-compact")
        self.set_class(event.size.height < 16, "-short")
        if was_dense != (self.has_class("-compact") or self.has_class("-short")):
            self._populate_list()
        if self._find_open:
            self.call_after_refresh(self._scroll_to_match)

    def on_show(self) -> None:
        if self._find_open:
            self.call_after_refresh(lambda: self._rebuild_find(preserve_index=True))

    @property
    def current(self) -> Note | None:
        return self._notes.get(self._selected_file)

    @property
    def find_open(self) -> bool:
        return self._find_open

    def set_library_query(self, query: str) -> None:
        """Offer the library filter as the initial Find text, without editing it."""
        self._library_query = query

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action in {"close_find", "next_match", "previous_match"}:
            return self._find_open
        return True

    def action_find(self) -> None:
        if not self._find_open:
            self._find_return_to_list = self.query_one(NotesList).has_focus
            self._find_open = True
            self.query_one("#notes-findbar").display = True
            field = self.query_one("#notes-find", NoteFindInput)
            with field.prevent(Input.Changed):
                field.value = self._find_query or self._library_query
            self._find_query = field.value
            self._rebuild_find()
        field = self.query_one("#notes-find", NoteFindInput)
        field.focus(scroll_visible=False)
        field.action_select_all()

    def dismiss_find(self) -> bool:
        """Close only in-note Find. Return whether there was anything to close."""
        if not self._find_open:
            return False
        self._find_open = False
        self._restore_find_blocks()
        self.query_one("#notes-findbar").display = False
        if self._find_return_to_list:
            self.focus_list()
        else:
            self.query_one(NotesPreview).focus(scroll_visible=False)
        return True

    def action_close_find(self) -> None:
        self.dismiss_find()

    def action_next_match(self) -> None:
        self._move_match(1)

    def action_previous_match(self) -> None:
        self._move_match(-1)

    def _move_match(self, delta: int) -> None:
        if self._find_open and self._find_matches:
            self._find_index = (self._find_index + delta) % len(self._find_matches)
            self._paint_find()

    def _restore_find_blocks(self) -> None:
        for widget, content, _ in self._find_blocks:
            if widget.is_mounted:
                widget.update(content, layout=False)
        self._find_blocks = []

    def _rebuild_find(self, *, preserve_index: bool = False) -> None:
        previous_index = self._find_index if preserve_index else 0
        self._restore_find_blocks()
        self._find_matches = []
        self._find_index = -1
        if not self._find_open:
            return
        if self._preview_loading:
            self._paint_find()
            return
        text: list[str] = []
        offset = 0
        if self.current is not None:
            widgets = [self.query_one("#notes-preview-title", Static),
                       *self.query_one("#notes-preview-body", Markdown).query(Static)]
            for widget in widgets:
                # Container blocks have no text of their own; table cells and
                # code labels are included as the visible leaves of Markdown.
                content = widget.visual
                if not isinstance(content, Content) or not content.plain:
                    continue
                self._find_blocks.append((widget, content, offset))
                text.append(content.plain)
                offset += len(content.plain) + 1
        self._find_matches = _literal_matches("\n".join(text), self._find_query)
        self._find_index = min(max(previous_index, 0), len(self._find_matches) - 1)
        self._paint_find()

    def _paint_find(self) -> None:
        total = len(self._find_matches)
        count = f"{self._find_index + 1}/{total}" if total else ("0/0" if self._find_query else "—")
        if self._preview_loading:
            count = "…"
        self.query_one("#notes-find-count", Static).update(count)
        self.query_one("#notes-find-count").tooltip = "No matches" if self._find_query and not total else "Current / total matches"
        for button_id in ("#notes-find-previous", "#notes-find-next"):
            self.query_one(button_id, Button).disabled = not total
        for widget, original, offset in self._find_blocks:
            content = original
            for index, (start, end) in enumerate(self._find_matches):
                left, right = max(0, start - offset), min(len(original), end - offset)
                if left < right:
                    # Keep Markdown styles and links. The active match uses
                    # inverse colors; the others use an unobtrusive underline.
                    style = Style(reverse=index == self._find_index, underline=True, bold=True)
                    style += Style.from_meta({"note_find_match": index})
                    content = content.stylize(style, left, right)
            widget.update(content, layout=False)
        self.call_after_refresh(self._scroll_to_match)

    def _scroll_to_match(self, *, settle: bool = True) -> None:
        if not self._find_open or self._find_index < 0:
            return
        start, end = self._find_matches[self._find_index]
        preview = self.query_one(NotesPreview)
        for widget, original, offset in self._find_blocks:
            if start >= offset + len(original) or end <= offset or not widget.is_mounted:
                continue
            # Inspect the actual rendered lines, so wrapping, wide Unicode,
            # code padding and table cell widths all have the same coordinates
            # as the text the user sees.
            lines = Visual.to_strips(widget, widget.visual, widget.content_size.width,
                                    None, widget.visual_style)
            for row, line in enumerate(lines):
                column = 0
                for segment in line:
                    if segment.style and segment.style.meta.get("note_find_match") == self._find_index:
                        region = Region(widget.content_region.x + column,
                                        widget.content_region.y + row, segment.cell_length, 1)
                        # A code block may also need horizontal scrolling.
                        parent = widget.parent
                        while parent is not None and parent is not self:
                            if isinstance(parent, VerticalScroll) or parent.is_scrollable:
                                local = region.translate(-parent.scrollable_content_region.offset + parent.scroll_offset)
                                movement = parent.scroll_to_region(local, animate=False, immediate=True,
                                                                   center=parent is preview)
                                region = region.translate(-movement)
                            parent = parent.parent
                        if settle:
                            # Showing a vertical scrollbar can shrink a code
                            # block after this layout; settle both axes once.
                            self.call_after_refresh(lambda: self._scroll_to_match(settle=False))
                        return
                    column += segment.cell_length

    @on(Input.Changed, "#notes-find")
    def _find_changed(self, event: Input.Changed) -> None:
        event.stop()
        self._find_query = event.value
        self._rebuild_find()

    @on(Button.Pressed, "#notes-findbar Button")
    def _find_button(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "notes-find-close":
            self.dismiss_find()
        else:
            self._move_match(-1 if event.button.id == "notes-find-previous" else 1)
            self.query_one("#notes-find", NoteFindInput).focus(scroll_visible=False)

    async def _preview_updated(self, update: AwaitComplete, generation: int) -> None:
        await update
        if generation == self._preview_generation and self.is_mounted:
            self._preview_loading = False
            self.call_after_refresh(self._rebuild_find)

    def set_notes(
        self, notes: Iterable[Note], selected_file: str = "", *,
        task_labels: Mapping[str, str] | None = None,
    ) -> None:
        notes = list(notes)
        if task_labels is not None:
            self._task_labels = dict(task_labels)
        if not self.is_mounted:
            self._pending = (notes, str(selected_file))
            return
        previous = str(selected_file) or self._selected_file
        self._notes = {str(note.file): note for note in notes}
        self._selected_file = previous if previous in self._notes else next(iter(self._notes), "")
        self._populate_list()
        self._show_preview()
        self.post_message(self.Selected(self.current))

    def _populate_list(self) -> None:
        """Resize the list without rebuilding the preview or resetting Find."""
        if not self.is_mounted:
            return
        dense = self.has_class("-compact") or self.has_class("-short")
        options: list[Option] = []
        for file, note in self._notes.items():
            detail = note.category or "Unfiled"
            row = Table.grid(expand=True, padding=(0, 1) if dense else 0)
            row.add_column(ratio=3, no_wrap=True, overflow="ellipsis")
            if dense:
                row.add_column(ratio=1, min_width=8, max_width=20,
                               justify="right", no_wrap=True, overflow="ellipsis")
                row.add_row(Text(note.title), Text(detail, style="dim"))
            else:
                if note.tags:
                    detail += " · " + " ".join(f"#{tag}" for tag in note.tags)
                row.add_row(Text(note.title))
                row.add_row(Text(detail, style="dim"))
            options.append(Option(row, id=file))
        listing = self.query_one("#notes-list", NotesList)
        with listing.prevent(OptionList.OptionHighlighted):
            listing.clear_options()
            listing.add_options(options)
            listing.highlighted = (
                list(self._notes).index(self._selected_file) if self._selected_file else None
            )

    def set_task_labels(self, labels: Mapping[str, str]) -> None:
        self._task_labels = dict(labels)
        if self.is_mounted:
            self._show_preview()

    def focus_list(self) -> None:
        self.query_one("#notes-list", NotesList).focus()

    def focus_preview(self) -> None:
        self.query_one("#notes-preview", NotesPreview).focus()

    def _show_preview(self) -> None:
        self._restore_find_blocks()
        self._preview_generation += 1
        note = self.current
        title = self.query_one("#notes-preview-title", Static)
        meta = self.query_one("#notes-preview-meta", Static)
        body = self.query_one("#notes-preview-body", Markdown)
        empty = self.query_one("#notes-preview-empty", Static)
        title.display = meta.display = body.display = note is not None
        empty.display = note is None or not note.body.strip()
        if note is None:
            self._preview_loading = False
            empty.update("No notes to show.\n\nPress a to capture a note, or clear Find and filters to see more.")
            self._rebuild_find()
            return
        title.update(Text(note.title))
        details = Text(note.category or "Unfiled")
        if note.tags:
            details.append("  ·  " + " ".join(f"#{tag}" for tag in note.tags))
        if note.projects:
            details.append("\nProjects: " + ", ".join(note.projects))
        if note.tasks:
            details.append("\nTasks: ")
            details.append("; ".join(
                self._task_labels.get(link.id, link.title) for link in note.tasks
            ))
        meta.update(details)
        preview = note.body
        # Imported Markdown commonly contains its own title heading.
        lines = preview.splitlines()
        if lines and lines[0].strip() == f"# {note.title}":
            preview = "\n".join(lines[1:]).lstrip("\n")
        # Markdown parses asynchronously. Track the latest document without
        # blocking selection/input messages or searching a previous note.
        self._preview_loading = True
        self._rebuild_find()
        self.run_worker(self._preview_updated(body.update(preview), self._preview_generation),
                        group="note-preview")
        if not note.body.strip():
            empty.update("This note is empty. Press Enter or e to add content.")
        self.query_one("#notes-preview", NotesPreview).scroll_home(animate=False)

    @on(OptionList.OptionHighlighted, "#notes-list")
    def _highlighted(self, event: OptionList.OptionHighlighted) -> None:
        event.stop()
        if event.option_id not in self._notes:
            return
        self._selected_file = event.option_id
        self._show_preview()
        self.post_message(self.Selected(self.current))

    @on(OptionList.OptionSelected, "#notes-list")
    def _activated(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        if event.option_id not in self._notes:
            return
        self._selected_file = event.option_id
        self._show_preview()
        if (note := self.current) is not None:
            self.post_message(self.Activated(note))


class NoteField(Input):
    BINDINGS = [Binding("ctrl+a", "select_all", "Select all", show=False)]

    def __init__(self, *args, **kwargs) -> None:
        kwargs.setdefault("compact", True)
        super().__init__(*args, **kwargs)


class DiscardNoteScreen(ModalScreen[bool]):
    BINDINGS = [Binding("escape", "keep", "Keep editing", show=False, priority=True)]

    DEFAULT_CSS = """
    DiscardNoteScreen { align: center middle; background: $background 70%; }
    DiscardNoteScreen #discard-note-dialog {
        width: 58; max-width: 94%; height: auto;
        padding: 1 2; border: round $accent; background: $background;
    }
    DiscardNoteScreen Label { width: 1fr; height: auto; margin-bottom: 1; }
    DiscardNoteScreen Horizontal { height: auto; align-horizontal: right; }
    DiscardNoteScreen Button { margin-left: 1; }
    """ + COMPACT_DIALOG_CSS

    def compose(self) -> ComposeResult:
        with Vertical(id="discard-note-dialog", classes="compact-dialog") as dialog:
            dialog.border_title = "Discard changes?"
            yield Label("Your edits have not been saved.")
            with Horizontal():
                yield Button("Discard", variant="error", id="discard-note")
                yield Button("Keep editing", variant="primary", id="keep-note")

    def on_mount(self) -> None:
        self.query_one("#keep-note", Button).focus()

    def action_keep(self) -> None:
        self.dismiss(False)

    @on(Button.Pressed)
    def _pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(event.button.id == "discard-note")


class NoteEditorScreen(ModalScreen[NoteDraft | None]):
    """Edit a note; saving errors retain the editor and every typed field."""

    BINDINGS = [
        Binding("ctrl+s,ctrl+shift+s", "save", "Save", show=False, priority=True),
        Binding("escape", "cancel", "Cancel", show=False, priority=True),
    ]

    DEFAULT_CSS = """
    NoteEditorScreen { align: center middle; background: $background 70%; }
    NoteEditorScreen #reference-dialog {
        width: 96; max-width: 96%; height: 92%; min-height: 12;
        padding: 0 1; border: round $accent; background: $background;
    }
    NoteEditorScreen #reference-fields {
        width: 1fr; height: 1fr; background: $background;
    }
    NoteEditorScreen .reference-row { width: 1fr; height: 1; }
    NoteEditorScreen .reference-row Label {
        width: 10; height: 1; color: $text-muted;
    }
    NoteEditorScreen NoteField {
        width: 1fr;
    }
    NoteEditorScreen #reference-text {
        height: 1fr; min-height: 6; margin-top: 1;
        border: round $primary-muted; background: $background;
    }
    NoteEditorScreen #reference-text:focus { border: round $accent; }
    NoteEditorScreen #reference-error {
        display: none; height: auto; max-height: 3; color: $error; width: 1fr;
    }
    NoteEditorScreen #reference-error.has-error { display: block; }
    NoteEditorScreen #reference-buttons { height: 1; margin-top: 1; align-horizontal: right; }
    NoteEditorScreen #reference-status {
        width: 1fr; height: 1; color: $text-muted;
    }
    NoteEditorScreen Button { margin-left: 1; min-width: 9; }
    """ + COMPACT_DIALOG_CSS

    def __init__(
        self, note: Note | None = None, *, categories: Iterable[str] = (),
        projects: Iterable[str] = (),
        initial: NoteDraft | None = None,
        save_handler: Callable[[NoteDraft], str | None] | None = None,
    ) -> None:
        super().__init__()
        if note is not None and initial is not None:
            raise ValueError("An initial draft is only supported for a new note.")
        self.note = note
        self._initial = initial or NoteDraft(
            title=note.title if note else "",
            body=note.body if note else "",
            category=(note.category if note else "Unfiled") or "Unfiled",
            tags=tuple(note.tags) if note else (),
            projects=tuple(note.projects) if note else (),
        )
        self._categories = tuple(categories)
        self._projects = tuple(projects)
        self._save_handler = save_handler

    def compose(self) -> ComposeResult:
        with Vertical(id="reference-dialog", classes="compact-dialog") as dialog:
            dialog.border_title = "Edit note" if self.note else "New note"
            dialog.border_subtitle = "Ctrl+S save · Tab fields · Esc cancel"
            with VerticalScroll(id="reference-fields"):
                with Horizontal(classes="reference-row"):
                    yield Label("Title")
                    yield NoteField(self._initial.title, placeholder="Give this note a title", id="note-title")
                with Horizontal(classes="reference-row"):
                    yield Label("Category")
                    yield NoteField(self._initial.category, placeholder="Unfiled", id="note-category",
                                    suggester=SuggestFromList(self._categories, case_sensitive=False))
                with Horizontal(classes="reference-row"):
                    yield Label("Tags")
                    yield NoteField(", ".join(self._initial.tags), placeholder="meeting, process, reference", id="note-tags")
                with Horizontal(classes="reference-row"):
                    yield Label("Projects")
                    yield NoteField(", ".join(self._initial.projects), placeholder="Optional; separate with commas", id="note-projects",
                                    suggester=SuggestFromList(self._projects, case_sensitive=False))
                yield TextArea(self._initial.body, id="reference-text", soft_wrap=True,
                               show_line_numbers=False, placeholder="Write your note. Markdown is supported.")
            yield Label("", id="reference-error")
            with Horizontal(id="reference-buttons"):
                yield Label("", id="reference-status")
                yield Button("Save", variant="primary", id="reference-save")
                yield Button("Cancel", id="reference-cancel")

    def on_mount(self) -> None:
        if self.note is None:
            self.query_one("#note-title", NoteField).focus()
        else:
            body = self.query_one("#reference-text", TextArea)
            body.move_cursor(body.document.end)
            body.focus()
        self._status()

    def _draft(self) -> NoteDraft:
        return NoteDraft(
            title=self.query_one("#note-title", Input).value.strip(),
            body=self.query_one("#reference-text", TextArea).text,
            category=self.query_one("#note-category", Input).value.strip() or "Unfiled",
            tags=_labels(self.query_one("#note-tags", Input).value, tags=True),
            projects=_labels(self.query_one("#note-projects", Input).value),
        )

    def _status(self) -> None:
        if not self.is_mounted:
            return
        changed = self._draft() != self._initial
        self.query_one("#reference-status", Label).update("Unsaved changes" if changed else "")

    @on(Input.Changed)
    @on(TextArea.Changed)
    def _changed(self, event: Input.Changed | TextArea.Changed) -> None:
        event.stop()
        self._status()

    @on(Input.Submitted)
    def _submitted(self, event: Input.Submitted) -> None:
        event.stop()
        # Enter in metadata advances; multiline content never saves by accident.
        self.focus_next()

    def action_save(self) -> None:
        draft = self._draft()
        error = self.query_one("#reference-error", Label)
        if not draft.title:
            error.update("Enter a title before saving.")
            error.add_class("has-error")
            self.query_one("#note-title", NoteField).focus()
            return
        if self._save_handler is not None:
            try:
                problem = self._save_handler(draft)
            except (OSError, ValueError, RuntimeError) as exception:
                problem = str(exception)
            if problem:
                error.update(Text(problem))
                error.add_class("has-error")
                return
        self.dismiss(draft)

    def action_cancel(self) -> None:
        if self._draft() == self._initial:
            self.dismiss(None)
            return

        def answer(discard: bool | None) -> None:
            if discard:
                self.dismiss(None)

        self.app.push_screen(DiscardNoteScreen(), answer)

    @on(Button.Pressed)
    def _pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "reference-save":
            self.action_save()
        elif event.button.id == "reference-cancel":
            self.action_cancel()
