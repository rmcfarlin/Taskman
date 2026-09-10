"""Exercise the shortcut dock through actual Textual layout and mouse events."""

import asyncio

import pytest
from rich.cells import cell_len
from textual.app import App, ComposeResult
from textual.widgets import Input, OptionList

from tui.shortcut_bar import NOTES_SHORTCUTS, ShortcutBar, TASK_SHORTCUTS


class DockApp(App):
    def __init__(self):
        super().__init__()
        self.invocations = []
        self.dock_heights = []

    def compose(self) -> ComposeResult:
        yield Input(id="query")
        yield OptionList("Parent", "Child", id="selection")
        yield ShortcutBar(id="shortcuts")

    def on_mount(self):
        choices = self.query_one(OptionList)
        choices.highlighted = 1
        choices.focus()

    def on_shortcut_bar_invoked(self, message: ShortcutBar.Invoked):
        self.invocations.append((message.action, self.focused))

    def on_shortcut_bar_resized(self, message: ShortcutBar.Resized):
        self.dock_heights.append(message.height)


def _visible_text(dock):
    """Read actual rendered strips; a source Text alone would miss clipping."""
    return [dock.render_line(y).text for y in range(dock.size.height)]


@pytest.mark.parametrize("width", [400, 160, 120, 80, 60, 40])
def test_primary_shortcuts_and_more_fit_two_rows(width):
    async def go():
        app = DockApp()
        async with app.run_test(size=(width, 26)) as pilot:
            await pilot.pause()
            dock = app.query_one(ShortcutBar)
            rendered = _visible_text(dock)
            assert 1 <= dock.size.height <= 2
            assert all(cell_len(line) <= width for line in rendered)
            content = "\n".join(rendered)
            expected = TASK_SHORTCUTS if width == 400 else tuple(
                s for s in TASK_SHORTCUTS if s.action in {"add", "edit", "toggle", "undo", "focus_search", "commands"})
            for item in expected:
                assert f" {item.key}  {item.label}" in content, content
            assert dock.region.bottom == app.size.height
            assert not dock.can_focus
    asyncio.run(go())


def test_resize_reflows_primary_shortcuts_without_losing_focus():
    async def go():
        app = DockApp()
        async with app.run_test(size=(140, 30)) as pilot:
            selected = app.query_one(OptionList)
            for width in (60, 120, 80, 160, 60):
                await pilot.resize_terminal(width, 30)
                await pilot.pause()
                dock = app.query_one(ShortcutBar)
                content = "\n".join(_visible_text(dock))
                assert all(f" {item.key}  {item.label}" in content for item in TASK_SHORTCUTS
                           if item.action in {"add", "edit", "toggle", "undo", "focus_search", "commands"})
                assert dock.size.height <= 2
                assert selected.has_focus and selected.highlighted == 1
                assert app.dock_heights[-1] == dock.outer_size.height
    asyncio.run(go())


@pytest.mark.parametrize("action", ["edit", "note", "open_note", "delete", "refresh", "save", "open_vault", "commands", "quit"])
def test_click_dispatch_preserves_focused_child(action):
    async def go():
        app = DockApp()
        async with app.run_test(size=(400, 26)) as pilot:
            await pilot.pause()
            dock = app.query_one(ShortcutBar)
            selected = app.query_one(OptionList)
            shortcut = next(item for item in TASK_SHORTCUTS if item.action == action)
            for y, line in enumerate(_visible_text(dock)):
                needle = f" {shortcut.key}  {shortcut.label}"
                if needle in line:
                    x = cell_len(line[:line.index(needle)]) + 1
                    await pilot.click(dock, offset=(x, y))
                    break
            else:
                pytest.fail(f"Shortcut not rendered: {action}")
            await pilot.pause()
            assert app.invocations == [(action, selected)]
            assert selected.has_focus and selected.highlighted == 1
    asyncio.run(go())


def test_history_disabled_actions_stay_visible_and_do_not_invoke():
    async def go():
        app = DockApp()
        async with app.run_test(size=(400, 26)) as pilot:
            dock = app.query_one(ShortcutBar)
            dock.set_mode("tasks", can_undo=False, can_redo=False)
            await pilot.pause()
            for y, line in enumerate(_visible_text(dock)):
                for key, label in (("u", "Undo"), ("Ctrl+Y", "Redo")):
                    needle = f" {key}  {label}"
                    if needle in line:
                        x = cell_len(line[:line.index(needle)]) + 1
                        await pilot.click(dock, offset=(x, y))
            assert "Undo" in "\n".join(_visible_text(dock))
            assert "Redo" in "\n".join(_visible_text(dock))
            assert app.invocations == []
    asyncio.run(go())


def test_hidden_shortcuts_are_those_that_missed_the_two_rows():
    async def go():
        app = DockApp()
        async with app.run_test(size=(80, 26)) as pilot:
            await pilot.pause()
            dock = app.query_one(ShortcutBar)
            hidden = dock.hidden_shortcuts(dock.content_size.width)
            shown = "\n".join(_visible_text(dock))
            assert hidden
            for item in hidden:
                assert f" {item.key}  {item.label}" not in shown
            for item in TASK_SHORTCUTS:
                if item not in hidden:
                    assert f" {item.key}  {item.label}" in shown
            assert any(item.action in {"due", "task_tags", "project", "help", "notes"}
                       for item in hidden)
            assert dock.size.height <= 2
            dock.set_mode("notes")
            await pilot.pause()
            assert next(item for item in NOTES_SHORTCUTS if item.action == "view_0").label == "All open"
            assert dock.hidden_shortcuts(400) == ()
    asyncio.run(go())


def test_search_hints_follow_text_input_behavior_and_restore_task_actions():
    async def go():
        app = DockApp()
        async with app.run_test(size=(60, 26)) as pilot:
            dock = app.query_one(ShortcutBar)
            app.query_one(Input).focus()
            dock.set_mode("search")
            await pilot.pause()
            content = "\n".join(_visible_text(dock))
            assert "Results" in content and "Clear / back" in content and "More" in content
            assert " q  Quit" not in content
            await pilot.press("q", "a")
            assert app.query_one(Input).value == "qa"
            dock.set_mode("inspector")
            await pilot.pause()
            assert " a  Add" in "\n".join(_visible_text(dock))
            assert "Ctrl+K" in "\n".join(_visible_text(dock))
            assert dock.size.height <= 2
    asyncio.run(go())
