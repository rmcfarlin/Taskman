"""A small, keyboard-first command palette with no application side effects.

The caller supplies the available commands and dispatches the returned command
ID.  Matching and presentation stay here, so the palette is also usable on its
own without TaskApp's stylesheet. Textual processes priority App bindings
before modal bindings; callers must guard those actions while a modal is open.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from rich.table import Table
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, OptionList, Static
from textual.widgets.option_list import Option

from .dialog_style import COMPACT_DIALOG_CSS


@dataclass(frozen=True)
class Command:
    id: str
    title: str
    shortcut: str = ""
    description: str = ""
    keywords: str = ""
    enabled: bool = True


def _match_score(needle: str, haystack: str) -> int | None:
    """Prefer whole words and contiguous matches over a loose subsequence."""
    if needle == haystack:
        return 160
    if needle in haystack.split():
        return 140
    if haystack.startswith(needle):
        return 130
    at = haystack.find(needle)
    if at >= 0:
        return 110 - min(at, 30)
    positions: list[int] = []
    at = -1
    for char in needle:
        at = haystack.find(char, at + 1)
        if at < 0:
            return None
        positions.append(at)
    if not positions:
        return 0
    gaps = positions[-1] - positions[0] + 1 - len(positions)
    return 50 - min(gaps, 30) - min(positions[0], 15)


def rank_commands(commands: list[Command], query: str) -> list[Command]:
    """Rank case-insensitive words, shortcuts, aliases, and fuzzy abbreviations.

    Every query word must match. Original order breaks ties, allowing callers
    to put the most useful commands first without surprising alphabetical jumps.
    Disabled commands remain discoverable but follow equally relevant actions.
    """
    tokens = query.casefold().split()
    scored: list[tuple[int, bool, int, Command]] = []
    for index, command in enumerate(commands):
        fields = (
            (command.title.casefold(), 80),
            (command.shortcut.casefold(), 70),
            (command.keywords.casefold(), 35),
            (command.id.replace("_", " ").casefold(), 15),
            (command.description.casefold(), 0),
        )
        score = 0
        for token in tokens:
            matches = [
                value + weight
                for field, weight in fields
                if field and (value := _match_score(token, field)) is not None
            ]
            if not matches:
                break
            score += max(matches)
        else:
            # A complete title or its prefix should beat scattered word hits.
            normalized = " ".join(tokens)
            if normalized and command.title.casefold() == normalized:
                score += 300
            elif normalized and command.title.casefold().startswith(normalized):
                score += 120
            scored.append((-score, not command.enabled, index, command))
    scored.sort(key=lambda item: item[:3])
    return [item[3] for item in scored]


def _prompt(command: Command, query: str) -> Table:
    title = Text(command.title, no_wrap=True, overflow="ellipsis")
    for token in query.casefold().split():
        start = command.title.casefold().find(token)
        if start >= 0:
            title.stylize("bold underline", start, start + len(token))
    if not command.enabled:
        title.append(" · unavailable", style="dim")
    # Rich layout keeps shortcuts at the edge when terminal width changes.
    row = Table.grid(expand=True, padding=(0, 1))
    row.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
    row.add_column(justify="right", no_wrap=True)
    row.add_row(title, Text(f"[{command.shortcut}]" if command.shortcut else "", style="dim"))
    return row


class CommandScreen(ModalScreen[str | None]):
    """Type to search; navigate without taking focus away from the input."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False, priority=True),
        Binding("up", "previous", "Previous", show=False, priority=True),
        Binding("down", "next", "Next", show=False, priority=True),
        Binding("pageup", "page_up", "Page up", show=False, priority=True),
        Binding("pagedown", "page_down", "Page down", show=False, priority=True),
        Binding("ctrl+a", "select_query", "Select query", show=False, priority=True),
    ]

    DEFAULT_CSS = """
    CommandScreen {
        align: center middle;
        background: $background 70%;
    }
    CommandScreen #command-dialog {
        width: 76;
        max-width: 96%;
        height: 22;
        max-height: 94%;
        border: round $accent;
        background: $surface;
        padding: 0 1;
    }
    CommandScreen #command-heading { height: 1; }
    CommandScreen #command-title {
        width: 1fr;
        color: $text;
        text-style: bold;
    }
    CommandScreen #command-count {
        width: auto;
        color: $text-muted;
        text-align: right;
    }
    CommandScreen #command-context {
        height: 1;
        color: $text-muted;
        text-overflow: ellipsis;
    }
    CommandScreen #command-input {
        margin-top: 1;
    }
    CommandScreen #command-results {
        height: 1fr;
        min-height: 1;
        padding: 0;
        border: none;
        background: $surface;
        scrollbar-size-vertical: 1;
    }
    CommandScreen #command-results > .option-list--option {
        padding: 0 1;
    }
    CommandScreen #command-results > .option-list--option-highlighted {
        background: $block-cursor-background;
        color: $block-cursor-foreground;
        text-style: bold;
    }
    CommandScreen #command-results > .option-list--option-disabled {
        color: $text-disabled;
    }
    CommandScreen #command-description {
        height: 2;
        margin-top: 1;
        color: $text;
    }
    CommandScreen #command-hints {
        height: 1; width: 1fr;
        color: $text-muted;
    }
    CommandScreen #command-footer { height: 1; }
    """ + COMPACT_DIALOG_CSS

    def __init__(self, commands: list[Command], context: str = "") -> None:
        super().__init__()
        self.commands = list(commands)
        self.context = re.sub(r"\s+", " ", context).strip()
        self.matches: list[Command] = []
        self._commands_by_id = {command.id: command for command in commands}
        if len(self._commands_by_id) != len(commands):
            raise ValueError("Command IDs must be unique")

    def compose(self) -> ComposeResult:
        with Vertical(id="command-dialog", classes="compact-dialog"):
            with Horizontal(id="command-heading"):
                yield Static("Commands", id="command-title")
                yield Static("", id="command-count")
            yield Static(self.context, id="command-context", markup=False)
            yield Input(placeholder="Search actions, views, or shortcuts…", compact=True, id="command-input")
            results = OptionList(id="command-results", compact=True)
            results.can_focus = False
            yield results
            yield Static("", id="command-description", markup=False)
            with Horizontal(id="command-footer"):
                yield Static("↑↓ choose · Enter run · Esc close", id="command-hints")
                yield Button("Close", id="command-close", tooltip="Close commands · Esc")

    def on_mount(self) -> None:
        self.query_one("#command-context").display = bool(self.context)
        self._populate("")
        self.query_one("#command-input", Input).focus()

    def _populate(self, query: str) -> None:
        self.matches = rank_commands(self.commands, query)
        results = self.query_one("#command-results", OptionList)
        results.set_options([
            Option(_prompt(command, query), id=command.id, disabled=not command.enabled)
            for command in self.matches
        ])
        results.highlighted = next(
            (index for index, command in enumerate(self.matches) if command.enabled), None
        )
        if not self.matches:
            results.add_option(Option("No matching commands", disabled=True))
        count = len(self.matches)
        self.query_one("#command-count", Static).update(f"{count} action{'s' if count != 1 else ''}")
        self._update_description()

    def _update_description(self) -> None:
        results = self.query_one("#command-results", OptionList)
        if results.highlighted is not None:
            command = self.matches[results.highlighted]
            description = command.description or command.title
        elif self.matches:
            description = "These actions are unavailable in the current context."
        else:
            description = "Try a different word, an abbreviation, or a shortcut."
        self.query_one("#command-description", Static).update(description)

    @on(Input.Changed, "#command-input")
    def _changed(self, event: Input.Changed) -> None:
        event.stop()
        self._populate(event.value)

    @on(Input.Submitted, "#command-input")
    def _submitted(self, event: Input.Submitted) -> None:
        event.stop()
        results = self.query_one("#command-results", OptionList)
        if results.highlighted is not None:
            self._choose(results.get_option_at_index(results.highlighted).id)

    @on(OptionList.OptionHighlighted, "#command-results")
    def _highlighted(self, event: OptionList.OptionHighlighted) -> None:
        event.stop()
        self._update_description()

    @on(OptionList.OptionSelected, "#command-results")
    def _selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self._choose(event.option_id)

    def _choose(self, command_id: str | None) -> None:
        command = self._commands_by_id.get(command_id or "")
        if command is not None and command.enabled:
            self.dismiss(command.id)

    def action_cancel(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#command-close")
    def _close(self, event: Button.Pressed) -> None:
        event.stop()
        self.action_cancel()

    def action_select_query(self) -> None:
        self.query_one("#command-input", Input).action_select_all()

    def action_next(self) -> None:
        self.query_one("#command-results", OptionList).action_cursor_down()

    def action_previous(self) -> None:
        self.query_one("#command-results", OptionList).action_cursor_up()

    def action_page_down(self) -> None:
        self.query_one("#command-results", OptionList).action_page_down()

    def action_page_up(self) -> None:
        self.query_one("#command-results", OptionList).action_page_up()
