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
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.suggester import SuggestFromList
from textual.widgets import Button, Input, Label, Markdown, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from .notes import Note


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


class NotesWorkspace(Horizontal):
    """A note list and readable preview, with one selected note at a time."""

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

    def compose(self) -> ComposeResult:
        yield NotesList(id="notes-list")
        with NotesPreview(id="notes-preview", can_focus=True):
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
        self.set_class(event.size.width < 78, "-compact")
        self.set_class(event.size.height < 16, "-short")

    @property
    def current(self) -> Note | None:
        return self._notes.get(self._selected_file)

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
        options: list[Option] = []
        for file, note in self._notes.items():
            detail = note.category or "Unfiled"
            if note.tags:
                detail += " · " + " ".join(f"#{tag}" for tag in note.tags)
            row = Table.grid(expand=True)
            row.add_column(no_wrap=True, overflow="ellipsis")
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
        self._show_preview()
        self.post_message(self.Selected(self.current))

    def set_task_labels(self, labels: Mapping[str, str]) -> None:
        self._task_labels = dict(labels)
        if self.is_mounted:
            self._show_preview()

    def focus_list(self) -> None:
        self.query_one("#notes-list", NotesList).focus()

    def focus_preview(self) -> None:
        self.query_one("#notes-preview", NotesPreview).focus()

    def _show_preview(self) -> None:
        note = self.current
        title = self.query_one("#notes-preview-title", Static)
        meta = self.query_one("#notes-preview-meta", Static)
        body = self.query_one("#notes-preview-body", Markdown)
        empty = self.query_one("#notes-preview-empty", Static)
        title.display = meta.display = body.display = note is not None
        empty.display = note is None or not note.body.strip()
        if note is None:
            empty.update("No notes to show.\n\nPress a to capture a note, or clear Find and filters to see more.")
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
        body.update(preview)
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
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="discard-note-dialog") as dialog:
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
        width: 1fr; height: 1; border: none; padding: 0;
        background: $background; background-tint: transparent;
    }
    NoteEditorScreen NoteField:focus {
        border: none; background: $primary 15%; background-tint: transparent;
    }
    NoteEditorScreen #reference-text {
        height: 1fr; min-height: 6; margin-top: 1;
        border: round $primary-muted; background: $background;
    }
    NoteEditorScreen #reference-text:focus { border: round $accent; }
    NoteEditorScreen #reference-error {
        height: auto; max-height: 3; color: $error; width: 1fr;
    }
    NoteEditorScreen #reference-buttons { height: 3; align-horizontal: right; }
    NoteEditorScreen #reference-status {
        width: 1fr; height: 1; margin-top: 1; color: $text-muted;
    }
    NoteEditorScreen Button { margin-left: 1; min-width: 9; }
    """

    def __init__(
        self, note: Note | None = None, *, categories: Iterable[str] = (),
        projects: Iterable[str] = (),
        save_handler: Callable[[NoteDraft], str | None] | None = None,
    ) -> None:
        super().__init__()
        self.note = note
        self._initial = NoteDraft(
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
        with Vertical(id="reference-dialog") as dialog:
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
            self.query_one("#note-title", NoteField).focus()
            return
        if self._save_handler is not None:
            try:
                problem = self._save_handler(draft)
            except (OSError, ValueError, RuntimeError) as exception:
                problem = str(exception)
            if problem:
                error.update(Text(problem))
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
