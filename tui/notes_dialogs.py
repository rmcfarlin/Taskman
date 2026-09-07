"""Explicit filename preview and editable note template dialogs."""
from __future__ import annotations

from pathlib import PurePosixPath
from typing import Callable

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, TextArea

from .note_files import RenamePlan
from .note_templates import Template
from .notes import Note
from .notes_ui import DiscardNoteScreen


class RenameNoteScreen(ModalScreen[RenamePlan | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]
    DEFAULT_CSS = """
    RenameNoteScreen { align: center middle; }
    #rename-dialog { width: 80; max-width: 96%; height: 30; max-height: 95%;
        border: round $primary; background: $surface; padding: 1 2; }
    #rename-content { height: 1fr; min-height: 0; overflow-x: hidden; }
    #rename-content Label { width: 1fr; height: auto; margin-bottom: 1; }
    #rename-filename { margin-bottom: 1; }
    #rename-error { color: $error; }
    #rename-buttons { height: 3; margin-top: 1; align-horizontal: right; }
    #rename-buttons Button { margin-left: 1; }
    """

    def __init__(self, note: Note, *, plan_handler: Callable[[str], RenamePlan],
                 apply_handler: Callable[[RenamePlan], str | None]) -> None:
        super().__init__()
        self.note = note
        self._plan_handler = plan_handler
        self._apply_handler = apply_handler
        self.plan: RenamePlan | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="rename-dialog") as dialog:
            dialog.border_title = "Rename note file"
            with VerticalScroll(id="rename-content"):
                yield Label(Text(f"Current file: {self.note.file}"))
                yield Input(PurePosixPath(self.note.file).name, id="rename-filename")
                yield Label("Preview scans Markdown links throughout this vault. The note title stays the same.")
                yield Label("", id="rename-preview")
                yield Label("", id="rename-error")
            with Horizontal(id="rename-buttons"):
                yield Button("Preview", variant="primary", id="rename-apply")
                yield Button("Cancel", id="rename-cancel")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    @on(Input.Changed)
    def _changed(self, event: Input.Changed) -> None:
        event.stop()
        self.plan = None
        self.query_one("#rename-preview", Label).update("")
        self.query_one("#rename-error", Label).update("")
        self.query_one("#rename-apply", Button).label = "Preview"

    @on(Input.Submitted)
    def _submitted(self, event: Input.Submitted) -> None:
        event.stop()
        # Enter in the filename only previews. Apply is an explicit button.
        self._preview()

    def _preview(self) -> None:
        try:
            self.plan = self._plan_handler(self.query_one(Input).value)
        except (OSError, ValueError, RuntimeError) as error:
            self.plan = None
            self.query_one("#rename-preview", Label).update("")
            self.query_one("#rename-apply", Button).label = "Preview"
            self.query_one("#rename-error", Label).update(Text(str(error)))
            return
        plan = self.plan
        files = [path for path, _raw in plan.replacements]
        details = "\n".join(files[:8])
        if len(files) > 8:
            details += f"\n… and {len(files) - 8} more"
        self.query_one("#rename-preview", Label).update(Text(
            f"New file: {plan.new_file}\n{plan.changed_links} links updated in "
            f"{plan.changed_files} Markdown files.\n{details}\nUndo restores the filename and links together."))
        self.query_one("#rename-error", Label).update("")
        button = self.query_one("#rename-apply", Button)
        button.label = "Apply rename"
        self.set_focus(button, scroll_visible=False)
        # Bring the result into the scrolling viewport; the footer stays fixed.
        self.call_after_refresh(lambda: self.query_one("#rename-preview").scroll_visible(
            top=True, animate=False, immediate=True))

    @on(Button.Pressed)
    def _pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "rename-cancel":
            self.action_cancel()
        elif self.plan is None:
            self._preview()
        else:
            try:
                error = self._apply_handler(self.plan)
            except (OSError, ValueError, RuntimeError) as exception:
                error = str(exception)
            if error:
                self.query_one("#rename-error", Label).update(Text(error))
                self.plan = None
                self.query_one("#rename-apply", Button).label = "Preview"
            else:
                self.dismiss(self.plan)

    def action_cancel(self) -> None:
        self.dismiss(None)


class TemplateEditorScreen(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False, priority=True),
                Binding("ctrl+s,ctrl+shift+s", "save", "Save", show=False, priority=True)]
    DEFAULT_CSS = """
    TemplateEditorScreen { align: center middle; }
    #template-dialog { width: 100; max-width: 96%; height: 90%;
        border: round $primary; background: $surface; padding: 0 1; }
    #template-dialog Label { height: auto; }
    #template-text { height: 1fr; margin: 1 0; }
    #template-error { color: $error; max-height: 4; }
    #template-buttons { height: auto; align-horizontal: right; }
    #template-buttons Button { margin-left: 1; }
    """

    def __init__(self, template: Template, *, save_handler: Callable[[str], str | None]) -> None:
        super().__init__()
        self.template = template
        self._save_handler = save_handler

    def compose(self) -> ComposeResult:
        with Vertical(id="template-dialog") as dialog:
            dialog.border_title = Text(f"Edit template — {self.template.name}")
            dialog.border_subtitle = "Ctrl+S save · Esc cancel"
            yield Label(Text(f"{self.template.file}\nUse {{{{date}}}} for today's date. Changes apply to future notes."))
            yield TextArea(self.template.body, id="template-text", soft_wrap=True)
            yield Label("", id="template-error")
            with Horizontal(id="template-buttons"):
                yield Button("Save", variant="primary", id="template-save")
                yield Button("Cancel", id="template-cancel")

    def on_mount(self) -> None:
        self.query_one(TextArea).focus()

    def action_save(self) -> None:
        body = self.query_one(TextArea).text
        try:
            error = self._save_handler(body)
        except (OSError, ValueError, RuntimeError) as exception:
            error = str(exception)
        if error:
            self.query_one("#template-error", Label).update(Text(error))
        else:
            self.dismiss(body)

    def action_cancel(self) -> None:
        if self.query_one(TextArea).text == self.template.body:
            self.dismiss(None)
        else:
            self.app.push_screen(DiscardNoteScreen(), lambda discard: self.dismiss(None) if discard else None)

    @on(Button.Pressed)
    def _pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "template-save":
            self.action_save()
        else:
            self.action_cancel()
