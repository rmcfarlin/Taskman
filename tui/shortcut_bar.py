"""A complete, wrapping shortcut dock that keeps the task selection focused.

Textual's single-line Footer deliberately overflows its bindings. This dock
measures each key and label in terminal cells and reserves all the rows needed,
so the actions stay discoverable at smaller terminal widths as well.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from rich.cells import cell_len
from rich.style import Style
from rich.text import Text
from textual import events
from textual.geometry import Size
from textual.message import Message
from textual.widget import Widget


@dataclass(frozen=True)
class Shortcut:
    key: str
    label: str
    action: str

    @property
    def width(self) -> int:
        """A padded keycap, a space, and the full action label."""
        return cell_len(self.key) + cell_len(self.label) + 3


TASK_SHORTCUTS = (
    Shortcut("a", "Add", "add"),
    Shortcut("e", "Edit", "edit"),
    Shortcut("Space", "Done", "toggle"),
    Shortcut("d", "Dates", "due"),
    Shortcut("p", "Priority", "priority"),
    Shortcut("s", "Status", "status"),
    Shortcut("j", "Project", "project"),
    Shortcut("t", "Subtask", "add_sub"),
    Shortcut("n", "Note", "note"),
    Shortcut("8", "Notes", "notes"),
    Shortcut("l", "Link note", "link_reference"),
    Shortcut("i", "Details", "inspect"),
    Shortcut("o", "Open", "open_note"),
    Shortcut("Del", "Delete", "delete"),
    Shortcut("/", "Find", "focus_search"),
    Shortcut("u", "Undo", "undo"),
    Shortcut("Ctrl+Y", "Redo", "redo"),
    Shortcut("r", "Rescan", "refresh"),
    Shortcut("Ctrl+S", "Save", "save"),
    Shortcut("Ctrl+O", "Vault", "open_vault"),
    Shortcut("m", "Theme", "theme"),
    Shortcut("h", "Help", "help"),
    Shortcut("q", "Quit", "quit"),
    Shortcut("Ctrl+K", "Commands", "commands"),
)

# Letter shortcuts edit the query while the search field has focus. Showing
# them as available actions there would teach the wrong keyboard behavior.
SEARCH_SHORTCUTS = (
    Shortcut("↓", "Results", "focus_tasks"),
    Shortcut("Esc", "Clear / back", "escape"),
    Shortcut("Alt+1", "Views", "focus_sidebar"),
    Shortcut("Alt+2", "Tasks", "focus_tasks"),
    Shortcut("Ctrl+O", "Vault", "open_vault"),
    Shortcut("Ctrl+K", "Commands", "commands"),
)

NOTES_SHORTCUTS = (
    Shortcut("a", "New note", "new_reference"),
    Shortcut("e", "Edit", "edit_reference"),
    Shortcut("Del", "Delete", "delete_reference"),
    Shortcut("F2", "Rename", "rename_reference"),
    Shortcut("s", "Sort", "note_sort"),
    Shortcut("/", "Find", "focus_search"),
    Shortcut("Ctrl+F", "Within note", "find_in_note"),
    Shortcut("c", "Category", "note_category"),
    Shortcut("t", "Tag", "note_tag"),
    Shortcut("j", "Project", "note_project"),
    Shortcut("l", "Link task", "link_reference"),
    Shortcut("k", "Linked tasks", "linked_references"),
    Shortcut("Ctrl+T", "Create task", "task_from_reference"),
    Shortcut("o", "Open", "open_note"),
    Shortcut("u", "Undo", "undo"),
    Shortcut("Ctrl+Y", "Redo", "redo"),
    Shortcut("r", "Rescan", "refresh"),
    Shortcut("1", "Tasks", "view_0"),
    Shortcut("Ctrl+K", "Commands", "commands"),
)

NOTE_FIND_SHORTCUTS = (
    Shortcut("Enter", "Next match", "next_note_match"),
    Shortcut("Shift+Enter", "Previous", "previous_note_match"),
    Shortcut("Esc", "Close find", "escape"),
    Shortcut("Ctrl+K", "Commands", "commands"),
)


class ShortcutBar(Widget, can_focus=False, can_focus_children=False):
    """Theme-aware keycaps with complete labels and mouse action messages.

    Handle :class:`Invoked` in the app and call its existing action. The dock
    does not move focus, so a click still acts on the focused inspector child.
    """

    ALLOW_SELECT = False
    COMPONENT_CLASSES = {
        "shortcut-bar--key",
        "shortcut-bar--label",
        "shortcut-bar--disabled",
    }
    DEFAULT_CSS = """
    ShortcutBar {
        dock: bottom;
        width: 1fr;
        height: auto;
        min-height: 2;
        padding: 0 1;
        background: $panel;
        color: $text;
    }
    ShortcutBar .shortcut-bar--key {
        color: $text;
        background: $primary 25%;
        text-style: bold;
    }
    ShortcutBar .shortcut-bar--label {
        color: $text;
    }
    ShortcutBar .shortcut-bar--disabled {
        color: $text-disabled;
    }
    """

    class Invoked(Message):
        """A shortcut was clicked without changing the current focus."""

        def __init__(self, action: str) -> None:
            self.action = action
            super().__init__()

    class Resized(Message):
        """The settled dock height changed; keep overlays above this area."""

        def __init__(self, height: int) -> None:
            self.height = height
            super().__init__()

    def __init__(self, *, id: str | None = None, classes: str | None = None) -> None:
        super().__init__(id=id, classes=classes)
        self._mode: Literal["tasks", "search", "inspector", "notes", "notes-search", "notes-find"] = "tasks"
        self._can_undo = True
        self._can_redo = True

    def set_mode(
        self,
        mode: Literal["tasks", "search", "inspector", "notes", "notes-search", "notes-find"],
        can_undo: bool = True,
        can_redo: bool = True,
    ) -> None:
        """Refresh contextual hints; unavailable history actions stay visible."""
        if mode not in ("tasks", "search", "inspector", "notes", "notes-search", "notes-find"):
            raise ValueError(f"Unknown shortcut mode: {mode}")
        state = (mode, can_undo, can_redo)
        if state != (self._mode, self._can_undo, self._can_redo):
            self._mode, self._can_undo, self._can_redo = state
            self.refresh(layout=True)

    @property
    def shortcuts(self) -> tuple[Shortcut, ...]:
        if self._mode == "notes-find":
            return NOTE_FIND_SHORTCUTS
        if self._mode == "notes":
            return NOTES_SHORTCUTS
        if self._mode == "notes-search":
            return tuple(Shortcut(s.key, "Notes" if s.action == "focus_tasks" and s.key == "Alt+2" else s.label, s.action)
                         for s in SEARCH_SHORTCUTS)
        return SEARCH_SHORTCUTS if self._mode == "search" else TASK_SHORTCUTS

    def _enabled(self, action: str) -> bool:
        if action == "undo":
            return self._can_undo
        if action == "redo":
            return self._can_redo
        return True

    def _rows(self, width: int) -> list[list[Shortcut]]:
        """Keep every key/label together; wrap only between shortcuts."""
        rows: list[list[Shortcut]] = [[]]
        used = 0
        for shortcut in self.shortcuts:
            gap = 2 if rows[-1] else 0
            if rows[-1] and used + gap + shortcut.width > width:
                rows.append([])
                used = 0
                gap = 0
            rows[-1].append(shortcut)
            used += gap + shortcut.width
        return rows

    def get_content_height(self, container: Size, viewport: Size, width: int) -> int:
        # Measure against the actual content width supplied by Textual, before
        # layout. Measuring against yesterday's widget size causes resize loops
        # or clipped bottom rows when shrinking the terminal.
        return len(self._rows(max(1, width)))

    def render(self) -> Text:
        result = Text(no_wrap=True, overflow="crop")
        key_style = self.get_component_rich_style("shortcut-bar--key")
        label_style = self.get_component_rich_style("shortcut-bar--label")
        disabled_style = self.get_component_rich_style("shortcut-bar--disabled")
        rows = self._rows(max(1, self.content_size.width))
        for row_index, row in enumerate(rows):
            if row_index:
                result.append("\n")
            for column, shortcut in enumerate(row):
                if column:
                    result.append("  ")
                enabled = self._enabled(shortcut.action)
                # This metadata is resolved from the rendered terminal cell,
                # which also handles wide characters and horizontal padding.
                action_style = Style(meta={"shortcut_action": shortcut.action}) if enabled else Style()
                result.append(
                    f" {shortcut.key} ",
                    (key_style if enabled else disabled_style) + action_style,
                )
                result.append(
                    f" {shortcut.label}",
                    (label_style if enabled else disabled_style) + action_style,
                )
        return result

    def on_resize(self, _event: events.Resize) -> None:
        self.refresh()
        self.post_message(self.Resized(self.outer_size.height))

    def on_click(self, event: events.Click) -> None:
        action = event.style.meta.get("shortcut_action")
        if isinstance(action, str) and self._enabled(action):
            event.stop()
            self.post_message(self.Invoked(action))
