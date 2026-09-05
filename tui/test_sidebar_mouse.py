"""Sidebar mouse targets stay fixed while keyboard navigation keeps working."""
import asyncio
import datetime as dt

import pytest

from tui import app as appmod


@pytest.fixture
def sidebar_vault(tmp_path, monkeypatch):
    monkeypatch.delenv("TASKMAN_THEME", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    (tmp_path / "Tasks").mkdir()
    (tmp_path / "Projects").mkdir()
    (tmp_path / "Tasks" / "Inbox.md").write_text(
        f"# Inbox\n\n- [ ] Today's task 📅 {dt.date.today().isoformat()}\n"
        "- [ ] Other task\n", encoding="utf-8")
    (tmp_path / "Projects" / "Alpha.md").write_text(
        "# Alpha\n\n## Tasks\n\n- [ ] Alpha task #project/Alpha\n", encoding="utf-8")
    return tmp_path


def _option_offset(app, sidebar, option_id):
    """Find a visible rendered hit target, including the actual pane gutters."""
    index = sidebar.get_option_index(option_id)
    region = sidebar.scrollable_content_region
    x = region.x + 4
    for y in range(region.y, region.bottom):
        if app.screen.get_style_at(x, y).meta.get("option") == index:
            return (x - sidebar.region.x, y - sidebar.region.y)
    raise AssertionError(f"Option {option_id} is not visible")


def _background(app, sidebar, offset):
    return app.screen.get_style_at(
        sidebar.region.x + offset[0], sidebar.region.y + offset[1]).bgcolor


@pytest.mark.parametrize("theme", ["taskman-dark-teal", "catppuccin-latte", "high-contrast"])
def test_mouse_focus_keeps_rows_fixed_and_clicks_exact_view_or_project(sidebar_vault, theme):
    async def go():
        app = appmod.TaskApp(sidebar_vault, theme=theme)
        async with app.run_test(size=(144, 42)) as pilot:
            await pilot.pause()
            sidebar = app.query_one(appmod.Sidebar)
            tasks = app.query_one(appmod.TaskList)
            original_region = sidebar.scrollable_content_region
            today = _option_offset(app, sidebar, "view:today")
            assert tasks.has_focus

            # MouseDown focuses before Click; it must not move a row under the pointer.
            assert await pilot.mouse_down(sidebar, offset=today)
            await pilot.pause()
            assert sidebar.has_focus
            assert sidebar.scrollable_content_region == original_region
            assert _option_offset(app, sidebar, "view:today") == today
            assert await pilot.mouse_up(sidebar, offset=today)
            await pilot.pause()
            assert sidebar.scrollable_content_region == original_region
            await pilot.press("right")
            await pilot.pause()
            assert tasks.has_focus

            for option_id, view, project in (("view:today", "today", ""),
                                             ("proj:Alpha", "project", "Alpha")):
                target = _option_offset(app, sidebar, option_id)
                assert await pilot.click(sidebar, offset=target)
                await pilot.pause()
                assert (app.view, app.project) == (view, project)
                assert sidebar.highlighted_option.id == option_id
                assert tasks.has_focus
                assert sidebar.scrollable_content_region == original_region
    asyncio.run(go())


@pytest.mark.parametrize("theme", ["taskman-dark-teal", "catppuccin-latte", "high-contrast"])
def test_hover_preserves_view_and_does_not_paint_a_second_selection(sidebar_vault, theme):
    async def go():
        app = appmod.TaskApp(sidebar_vault, theme=theme)
        async with app.run_test(size=(144, 42)) as pilot:
            await pilot.pause()
            sidebar = app.query_one(appmod.Sidebar)
            for focused in (False, True):
                if focused:
                    await pilot.press("alt+1")
                    await pilot.pause()
                assert sidebar.has_focus == focused
                active = _option_offset(app, sidebar, "view:all")
                target = _option_offset(app, sidebar, "view:today")
                active_background = _background(app, sidebar, active)
                target_background = _background(app, sidebar, target)
                assert active_background != target_background
                assert await pilot.hover(sidebar, offset=target)
                await pilot.pause()
                assert (app.view, app.project) == ("all", "")
                assert sidebar.highlighted_option.id == "view:all"
                assert sidebar.has_focus == focused
                assert _background(app, sidebar, target) == target_background
                assert _background(app, sidebar, active) == active_background
                await pilot.hover(appmod.TaskList, offset=(4, 4))
                await pilot.pause()
                assert _background(app, sidebar, target) == target_background
    asyncio.run(go())


def test_clicking_headings_and_separator_does_not_navigate(sidebar_vault):
    async def go():
        app = appmod.TaskApp(sidebar_vault, theme="taskman-dark-teal")
        async with app.run_test(size=(144, 42)) as pilot:
            await pilot.pause()
            sidebar = app.query_one(appmod.Sidebar)
            await pilot.click(sidebar, offset=_option_offset(app, sidebar, "view:today"))
            await pilot.pause()
            assert app.view == "today"
            for heading in ("h:views", "h:projects"):
                assert await pilot.click(sidebar, offset=_option_offset(app, sidebar, heading))
                await pilot.pause()
                assert (app.view, app.project) == ("today", "")
                assert sidebar.highlighted_option.id == "view:today"
            x, y = _option_offset(app, sidebar, "h:projects")
            separator = (x, y - 1)
            assert "option" not in app.screen.get_style_at(
                sidebar.region.x + x, sidebar.region.y + y - 1).meta
            assert await pilot.click(sidebar, offset=separator)
            await pilot.pause()
            assert (app.view, app.project) == ("today", "")
            assert sidebar.highlighted_option.id == "view:today"
    asyncio.run(go())


def test_keyboard_browsing_and_focus_handoff_survive_mouse_navigation(sidebar_vault):
    async def go():
        app = appmod.TaskApp(sidebar_vault, theme="taskman-dark-teal")
        async with app.run_test(size=(144, 42)) as pilot:
            await pilot.pause()
            sidebar = app.query_one(appmod.Sidebar)
            tasks = app.query_one(appmod.TaskList)
            await pilot.click(sidebar, offset=_option_offset(app, sidebar, "view:today"))
            await pilot.pause()
            await pilot.press("alt+1")
            await pilot.pause()
            assert sidebar.has_focus and app.view == "today"
            await pilot.press("up")
            await pilot.pause()
            assert sidebar.has_focus and app.view == "all"
            await pilot.press("down")
            await pilot.pause()
            assert sidebar.has_focus and app.view == "today"
            await pilot.press("right")
            await pilot.pause()
            assert tasks.has_focus and app.view == "today"
            await pilot.press("alt+1", "down")
            await pilot.pause()
            assert sidebar.has_focus and app.view == "overdue"
            await pilot.press("enter")
            await pilot.pause()
            assert tasks.has_focus and app.view == "overdue"
    asyncio.run(go())
