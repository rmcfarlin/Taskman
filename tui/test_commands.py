"""Behavioral checks for the standalone palette, including narrow terminals."""

import asyncio

import pytest
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Input, OptionList, Static

from tui.commands import Command, CommandScreen, rank_commands


COMMANDS = [
    Command("add", "Add task", "a", "Capture a task in your inbox.", "new create"),
    Command("complete", "Complete task", "Space", "Mark the selected task complete.", "done finish check"),
    Command("now", "Go to Now", "2", "Tasks due or scheduled by today.", "view due scheduled"),
    Command("project", "Choose project", "j", "Assign the selected task to a project.", "move assign"),
    Command("delete", "Delete task", "Delete", "Delete the selected task.", enabled=False),
]


class PaletteApp(App):
    BINDINGS = [
        Binding("a,d,q,1", "parent_action", "Action"),
        Binding("ctrl+s", "parent_action", "Save", priority=True),
    ]

    def __init__(self, commands=None, *, theme="textual-dark", context="All open · selected task"):
        super().__init__()
        self.palette = CommandScreen(COMMANDS if commands is None else commands, context=context)
        self.results = []
        self.parent_action_count = 0
        self.theme = theme

    def compose(self) -> ComposeResult:
        yield Static("Parent app")

    def on_mount(self):
        self.push_screen(self.palette, self.results.append)

    def action_parent_action(self):
        self.parent_action_count += 1


def test_ranking_supports_words_aliases_shortcuts_and_abbreviations():
    assert rank_commands(COMMANDS, "complete")[0].id == "complete"
    assert rank_commands(COMMANDS, " CMPL ")[0].id == "complete"
    assert rank_commands(COMMANDS, "task add")[0].id == "add"
    assert rank_commands(COMMANDS, "new")[0].id == "add"
    assert rank_commands(COMMANDS, "2")[0].id == "now"
    assert rank_commands(COMMANDS, "finish")[0].id == "complete"
    assert rank_commands(COMMANDS, "zzzzzz") == []
    assert rank_commands(COMMANDS, "") == COMMANDS
    assert rank_commands(COMMANDS, "delete")[0].enabled is False


def test_duplicate_command_ids_are_rejected():
    with pytest.raises(ValueError, match="unique"):
        CommandScreen([Command("same", "First"), Command("same", "Second")])


def test_typing_navigating_and_executing_keep_input_focus():
    async def go():
        app = PaletteApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            palette = app.palette
            search = palette.query_one(Input)
            results = palette.query_one(OptionList)
            assert search.has_focus
            assert results.highlighted == 0
            await pilot.press("down")
            assert search.has_focus
            assert results.highlighted == 1
            assert "Mark the selected task complete" in str(palette.query_one("#command-description", Static).content)
            await pilot.press("up")
            assert results.highlighted == 0
            await pilot.press(*"done")
            await pilot.pause()
            assert search.has_focus
            assert palette.matches[0].id == "complete"
            assert app.parent_action_count == 0
            await pilot.press("enter")
            assert app.results == ["complete"]
    asyncio.run(go())


def test_no_match_and_disabled_commands_cannot_execute():
    async def go():
        app = PaletteApp()
        async with app.run_test(size=(60, 20)) as pilot:
            await pilot.press(*"zzzzzz", "enter")
            assert app.screen is app.palette
            assert app.palette.matches == []
            assert "different word" in str(app.palette.query_one("#command-description", Static).content)
            await pilot.press("ctrl+a", "backspace", *"Delete", "enter")
            await pilot.pause()
            assert app.palette.matches[0].id == "delete"
            assert app.palette.query_one(OptionList).highlighted is None
            assert app.results == []
            await pilot.press("escape")
            assert app.results == [None]
    asyncio.run(go())


def test_typing_shortcuts_does_not_trigger_parent_actions():
    async def go():
        app = PaletteApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.press("a", "d", "q", "1")
            assert app.parent_action_count == 0
            assert app.palette.query_one(Input).value == "adq1"
            assert app.screen is app.palette
    asyncio.run(go())


@pytest.mark.parametrize("size,theme", [((60, 20), "textual-dark"), ((80, 24), "textual-light")])
def test_palette_fits_small_terminals_and_pages_long_results(size, theme):
    commands = [
        Command(f"action_{n}", f"Command {n:02d} with a longer action title", f"Ctrl+{n}", f"Description {n}")
        for n in range(30)
    ]

    async def go():
        app = PaletteApp(commands, theme=theme)
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            palette = app.palette
            panel = palette.query_one("#command-dialog")
            results = palette.query_one(OptionList)
            hints = palette.query_one("#command-hints")
            description = palette.query_one("#command-description")
            assert panel.region.x >= 0 and panel.region.right <= size[0]
            assert panel.region.y >= 0 and panel.region.bottom <= size[1]
            assert hints.region.bottom <= panel.region.bottom - 1
            assert description.region.bottom <= hints.region.y
            assert results.size.height >= 3
            await pilot.press("pagedown")
            assert results.highlighted > 1
            assert palette.query_one(Input).has_focus
            await pilot.press("pagedown", "pagedown", "pagedown", "pagedown", "pagedown")
            assert results.scroll_y > 0
            previous = results.highlighted
            await pilot.press("pageup")
            assert results.highlighted < previous
            await pilot.press("enter")
            assert len(app.results) == 1
            assert app.results[0].startswith("action_")
    asyncio.run(go())


def test_empty_palette_has_a_safe_empty_state():
    async def go():
        app = PaletteApp([], context="")
        async with app.run_test(size=(60, 20)) as pilot:
            assert app.palette.query_one(Input).has_focus
            assert not app.palette.query_one("#command-context").display
            await pilot.press("down", "pagedown", "enter")
            assert app.results == []
            await pilot.press("escape")
            assert app.results == [None]
    asyncio.run(go())
