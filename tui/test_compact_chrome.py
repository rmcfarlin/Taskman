"""Search appears when needed and the task workspace stays visually continuous."""
import asyncio

import pytest
from textual.color import Color

from tui import app as appmod


@pytest.fixture
def chrome_vault(tmp_path, monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("TASKMAN_THEME", raising=False)
    (tmp_path / "Tasks").mkdir()
    (tmp_path / "Projects").mkdir()
    (tmp_path / "Tasks" / "Inbox.md").write_text(
        "# Inbox\n\n- [ ] Review release docs\n- [ ] Review keyboard help\n"
        "- [ ] Schedule demo\n", encoding="utf-8")
    return tmp_path


def _task_descriptions(tasks):
    return {row.task.description for row in tasks.rows if isinstance(row, appmod.TaskRow)}


@pytest.mark.parametrize("size", [(140, 38), (80, 24), (60, 16)])
def test_default_workspace_has_no_find_bar_or_duplicate_view_heading(chrome_vault, size):
    async def go():
        app = appmod.TaskApp(chrome_vault, theme="taskman-dark-teal")
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            tasks = app.query_one(appmod.TaskList)
            assert not app.query_one("#searchbar").display
            assert not list(app.query("#viewhead"))
            assert tasks.has_focus
            assert tasks.region.y == app.query_one("#main").content_region.y
            assert tasks.size.height >= 4
            assert _task_descriptions(tasks) == {
                "Review release docs", "Review keyboard help", "Schedule demo"}
    asyncio.run(go())


@pytest.mark.parametrize("size", [(140, 38), (68, 20)])
@pytest.mark.parametrize("handoff", [None, "enter", "down"])
def test_search_filters_and_escape_recovers_space_and_focus(chrome_vault, size, handoff):
    async def go():
        app = appmod.TaskApp(chrome_vault, theme="taskman-dark-teal")
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            searchbar = app.query_one("#searchbar")
            search = app.query_one("#search", appmod.Input)
            tasks = app.query_one(appmod.TaskList)
            initial_region = tasks.region
            assert not searchbar.display
            await pilot.press("/")
            await pilot.pause()
            assert searchbar.display and search.has_focus
            assert tasks.region.y > initial_region.y
            await pilot.press(*"Review")
            await pilot.pause()
            assert app.search_query == "Review"
            assert _task_descriptions(tasks) == {"Review release docs", "Review keyboard help"}
            if handoff is not None:
                await pilot.press(handoff)
                await pilot.pause()
                assert tasks.has_focus
                assert searchbar.display and search.value == "Review"
                assert app.search_query == "Review"
            await pilot.press("escape")
            await pilot.pause()
            assert app.search_query == search.value == ""
            assert not searchbar.display
            assert tasks.has_focus
            assert tasks.region == initial_region
            assert _task_descriptions(tasks) == {
                "Review release docs", "Review keyboard help", "Schedule demo"}
    asyncio.run(go())


@pytest.mark.parametrize("exit_key", ["enter", "down", "escape"])
def test_empty_find_bar_closes_for_explicit_dismissal(chrome_vault, exit_key):
    async def go():
        app = appmod.TaskApp(chrome_vault, theme="taskman-dark-teal")
        async with app.run_test(size=(140, 38)) as pilot:
            await pilot.pause()
            searchbar = app.query_one("#searchbar")
            search = app.query_one("#search", appmod.Input)
            await pilot.press("/")
            await pilot.pause()
            assert searchbar.display and search.has_focus and search.value == ""
            await pilot.press(exit_key)
            await pilot.pause()
            assert not search.has_focus
            assert not searchbar.display
            assert app.search_query == ""
            assert app.query_one(appmod.TaskList).has_focus
    asyncio.run(go())


@pytest.mark.parametrize("key,focus_type", [
    ("tab", appmod.TaskList), ("shift+tab", appmod.Sidebar),
])
def test_tab_leaves_empty_find_visible_without_moving_results(chrome_vault, key, focus_type):
    async def go():
        app = appmod.TaskApp(chrome_vault, theme="taskman-dark-teal")
        async with app.run_test(size=(140, 38)) as pilot:
            await pilot.pause()
            await pilot.press("/")
            await pilot.pause()
            tasks = app.query_one(appmod.TaskList)
            search = app.query_one("#search", appmod.Input)
            origin = tasks.scrollable_content_region.offset
            assert search.has_focus
            await pilot.press(key)
            await pilot.pause()
            assert app.query_one(focus_type).has_focus
            assert not search.has_focus and search.value == ""
            assert app.query_one("#searchbar").display
            assert tasks.scrollable_content_region.offset == origin
            await pilot.press("escape")
            await pilot.pause()
            assert not app.query_one("#searchbar").display
            assert tasks.has_focus
    asyncio.run(go())


def test_search_opens_from_fullscreen_inspector_on_small_terminal(chrome_vault):
    async def go():
        app = appmod.TaskApp(chrome_vault, theme="taskman-dark-teal")
        async with app.run_test(size=(68, 20)) as pilot:
            await pilot.pause()
            await pilot.press("right")
            await pilot.pause()
            inspector = app.query_one(appmod.Inspector)
            assert inspector.display and inspector.has_focus
            assert not app.query_one("#main").display
            await pilot.press("/")
            await pilot.pause()
            assert not inspector.display
            assert app.query_one("#main").display
            assert app.query_one("#searchbar").display
            assert app.query_one("#search", appmod.Input).has_focus
            await pilot.press("escape")
            await pilot.pause()
            assert not app.query_one("#searchbar").display
            assert app.query_one(appmod.TaskList).has_focus
    asyncio.run(go())


@pytest.mark.parametrize("size", [(140, 38), (68, 20)])
@pytest.mark.parametrize("clicks", [1, 2])
def test_empty_find_mouse_clicks_keep_the_original_task_target(chrome_vault, size, clicks):
    async def go():
        app = appmod.TaskApp(chrome_vault, theme="taskman-dark-teal")
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            tasks = app.query_one(appmod.TaskList)
            await pilot.press("end", "/")
            await pilot.pause()
            search = app.query_one("#search", appmod.Input)
            assert search.has_focus and search.value == ""
            assert tasks.current.description == "Schedule demo"
            row_index = next(index for index, row in enumerate(tasks.rows)
                             if isinstance(row, appmod.TaskRow)
                             and row.task.description == "Review release docs")
            target = tasks.rows[row_index].task
            content = tasks.scrollable_content_region
            line = tasks.HEADER_LINES + row_index - tasks.scroll_offset.y
            assert "Review release docs" in tasks.render_line(line).text
            # Keep the original screen cell for the full mouse sequence, including
            # the second click: collapsing Find on focus would hit another task.
            point = (content.x + 8, content.y + line)
            assert await pilot.click(offset=point, times=clicks)
            await pilot.pause()
            assert tasks.current.id == target.id
            assert search.value == app.search_query == ""
            assert app.query_one("#searchbar").display
            inspector = app.query_one(appmod.Inspector)
            if clicks == 1:
                assert tasks.has_focus and not inspector.display
                assert tasks.scrollable_content_region.offset == content.offset
            else:
                assert inspector.display
                assert target.description in str(inspector.query_one("#ins-title").render())
                if size[0] >= 72:
                    assert tasks.scrollable_content_region.offset == content.offset
                else:
                    assert inspector.has_focus and not app.query_one("#main").display
    asyncio.run(go())


@pytest.mark.parametrize("modal_key,screen_type", [
    ("ctrl+k", appmod.CommandScreen), ("f1", appmod.HelpScreen),
])
def test_canceling_modal_restores_empty_find_before_escape_closes_it(chrome_vault,
                                                                  modal_key, screen_type):
    async def go():
        app = appmod.TaskApp(chrome_vault, theme="taskman-dark-teal")
        async with app.run_test(size=(140, 38)) as pilot:
            await pilot.pause()
            searchbar = app.query_one("#searchbar")
            search = app.query_one("#search", appmod.Input)
            await pilot.press("/")
            await pilot.pause()
            assert searchbar.display and search.has_focus and search.value == ""
            await pilot.press(modal_key)
            await pilot.pause()
            assert isinstance(app.screen, screen_type)
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, screen_type)
            assert searchbar.display and search.has_focus
            assert search.value == app.search_query == ""
            await pilot.press("escape")
            await pilot.pause()
            assert not searchbar.display
            assert app.query_one(appmod.TaskList).has_focus
    asyncio.run(go())


@pytest.mark.parametrize("theme", ["taskman-dark-teal", "catppuccin-latte", "high-contrast"])
def test_workspace_backgrounds_follow_theme_through_pane_focus(chrome_vault, theme):
    async def go():
        app = appmod.TaskApp(chrome_vault, theme=theme)
        async with app.run_test(size=(140, 38)) as pilot:
            await pilot.pause()
            expected = Color.parse(app.theme_variables["background"])
            await pilot.press("right")
            await pilot.pause()
            assert app.query_one(appmod.Inspector).display
            for focus_key in ("alt+1", "right", "alt+3"):
                await pilot.press(focus_key)
                await pilot.pause()
                for selector in ("#body", "#main", "#sidebar", "#tasks", "#inspector",
                                 "#topbar", "#contextbar"):
                    widget = app.query_one(selector)
                    assert widget.background_colors[1] == expected, (theme, selector, focus_key)
    asyncio.run(go())
