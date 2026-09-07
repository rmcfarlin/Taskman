"""Shortcut dock contracts in the complete app, with real notification UI."""

import asyncio
import datetime as dt

import pytest
from rich.cells import cell_len
from textual.widgets import Input, OptionList

from tui import app as appmod
from tui import taskman as tm
from tui.shortcut_bar import ShortcutBar, TASK_SHORTCUTS


@pytest.fixture
def dock_vault(tmp_path, monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("TASKMAN_THEME", raising=False)
    (tmp_path / "Tasks").mkdir()
    (tmp_path / "Projects").mkdir()
    (tmp_path / "Tasks" / "Inbox.md").write_text(
        "# Inbox\n\n"
        f"- [ ] Parent task 📅 {dt.date.today().isoformat()}\n"
        "  - [ ] Child one\n"
        "    Child-specific context.\n"
        "  - [ ] Child two\n"
        "- [ ] Separate task\n",
        encoding="utf-8",
    )
    return tmp_path


def _dock_lines(dock):
    return [dock.render_line(y).text for y in range(dock.size.height)]


async def _click_action(pilot, dock, action):
    shortcut = next(item for item in dock.shortcuts if item.action == action)
    needle = f" {shortcut.key}  {shortcut.label}"
    for y, line in enumerate(_dock_lines(dock)):
        if needle in line:
            x = cell_len(line[:line.index(needle)]) + 1
            await pilot.click(dock, offset=(x, y))
            await pilot.pause()
            return
    pytest.fail(f"Action {action} is missing from the rendered dock")


@pytest.mark.parametrize("action,screen_type", [("edit", appmod.EditScreen), ("due", appmod.DueScreen)])
def test_dock_form_clicks_edit_focused_child_and_return_focus(dock_vault, action, screen_type):
    async def go():
        app = appmod.TaskApp(dock_vault, theme="taskman-teal")
        async with app.run_test(notifications=True, size=(130, 30)) as pilot:
            await pilot.press("1")  # This workflow exercises the All open view.
            await pilot.press("right", "tab")
            await pilot.pause()
            children = app.query_one("#ins-kids", OptionList)
            assert children.has_focus
            assert app._selected().description == "Child one"
            await _click_action(pilot, app.query_one(ShortcutBar), action)
            assert isinstance(app.screen, screen_type)
            if action == "edit":
                assert app.screen.query_one("#text", Input).value == "Child one"
            else:
                # The child has no due date; the parent is due today.
                assert app.screen._current is None
                assert app.screen.query_one("#due", Input).value == ""
            await pilot.press("escape")
            await pilot.pause()
            assert children.has_focus and children.highlighted == 0
            assert app.query_one(appmod.TaskList).current.description == "Parent task"
    asyncio.run(go())


def test_dock_completion_changes_only_focused_child_with_notifications(dock_vault):
    async def go():
        app = appmod.TaskApp(dock_vault, theme="taskman-teal")
        async with app.run_test(notifications=True, size=(130, 30)) as pilot:
            await pilot.press("1")  # This workflow exercises the All open view.
            await pilot.press("right", "tab")
            await pilot.pause()
            children = app.query_one("#ins-kids", OptionList)
            await _click_action(pilot, app.query_one(ShortcutBar), "toggle")
            tasks = {task.description: task for task in tm.load_all(dock_vault)}
            assert tasks["Child one"].done
            assert tasks["Parent task"].open
            assert tasks["Child two"].open
            assert children.has_focus
            assert app.history.can_undo
            assert app.query_one(ShortcutBar)._can_undo
    asyncio.run(go())


@pytest.mark.parametrize("size", [(60, 16), (80, 18), (120, 18), (130, 30)])
def test_short_terminal_keeps_task_rows_and_every_dock_action_visible(dock_vault, size):
    async def go():
        app = appmod.TaskApp(dock_vault, theme="taskman-teal")
        async with app.run_test(notifications=True, size=size) as pilot:
            await pilot.press("1")  # This workflow exercises the All open view.
            await pilot.pause()
            dock = app.query_one(ShortcutBar)
            tasks = app.query_one(appmod.TaskList)
            context = app.query_one("#contextbar")
            status = app.query_one("#statusbar")
            assert tasks.size.height >= 4
            assert tasks.region.bottom <= context.region.y
            assert context.region.bottom <= status.region.y
            assert status.region.bottom <= dock.region.y
            assert dock.region.bottom == size[1]
            content = "\n".join(_dock_lines(dock))
            for shortcut in TASK_SHORTCUTS:
                assert f" {shortcut.key}  {shortcut.label}" in content
            assert app.screen.get_widget_at(dock.region.x + 2, dock.region.y)[0] is dock
    asyncio.run(go())


def test_search_dock_tracks_focus_and_escape_restores_full_actions(dock_vault):
    async def go():
        app = appmod.TaskApp(dock_vault, theme="taskman-teal")
        async with app.run_test(notifications=True, size=(80, 24)) as pilot:
            await pilot.press("1")  # This workflow exercises the All open view.
            dock = app.query_one(ShortcutBar)
            await pilot.press("/")
            await pilot.pause()
            content = "\n".join(_dock_lines(dock))
            assert "Results" in content and "Clear / back" in content
            assert " q  Quit" not in content
            await pilot.press("q")
            assert app.query_one("#search", Input).value == "q"
            await _click_action(pilot, dock, "escape")
            assert app.query_one("#search", Input).value == ""
            assert app.query_one(appmod.TaskList).has_focus
            assert " q  Quit" in "\n".join(_dock_lines(dock))
            assert not dock._can_undo and not dock._can_redo
    asyncio.run(go())


@pytest.mark.parametrize("size", [(80, 24), (130, 30)])
def test_notifications_leave_the_dock_visible_and_clickable(dock_vault, size):
    async def go():
        app = appmod.TaskApp(dock_vault, theme="taskman-teal")
        async with app.run_test(notifications=True, size=size) as pilot:
            await pilot.press("1")  # This workflow exercises the All open view.
            app.notify("Task updated. Press u to undo.", title="Updated", timeout=20)
            await pilot.pause()
            dock = app.query_one(ShortcutBar)
            toasts = list(app.screen.query("Toast"))
            assert toasts
            for toast in toasts:
                assert toast.region.bottom <= dock.region.y, (toast.region, dock.region)
            await _click_action(pilot, dock, "commands")
            assert type(app.screen).__name__ == "CommandScreen"
    asyncio.run(go())
