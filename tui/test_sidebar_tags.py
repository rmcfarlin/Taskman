"""The sidebar exposes people/task tags without losing the current task view."""

from pathlib import Path

import pytest
from textual.widgets import Input

from tui import app as appmod, taskman as tm
from tui.notes import NotesStore


@pytest.fixture
def sidebar_tag_vault(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("TASKMAN_THEME", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    (tmp_path / "Tasks").mkdir()
    (tmp_path / "Projects").mkdir()
    (tmp_path / "Tasks/Inbox.md").write_text("- [ ] All-tag task #all\n", encoding="utf-8")
    (tmp_path / "Projects/Alpha.md").write_text(
        "# Alpha\n\n"
        "- [ ] Alpha review #person/amy #PERSON/AMY #review #project/Alpha\n"
        "- [ ] Alpha urgent #person/amy #urgent\n"
        "- [ ] Parent\n"
        "  - [ ] Child Amy #person/amy\n"
        "- [x] Alpha closed #person/amy #archived\n",
        encoding="utf-8",
    )
    (tmp_path / "Projects/Beta.md").write_text(
        "# Beta\n\n"
        "- [ ] Beta review #person/amy #review\n"
        "- [ ] Bob review #Person/Bob #review\n"
        "- [ ] Bob urgent #person/bob #urgent\n"
        "- [x] Beta closed #closed-only\n",
        encoding="utf-8",
    )
    return tmp_path


def _tag_rows(sidebar):
    return [option for option in sidebar.options if (option.id or "").startswith("tag:value:")]


def _counts(sidebar):
    return {option.id.removeprefix("tag:value:"): int(str(option.prompt).split()[-1])
            for option in _tag_rows(sidebar)}


def _matches(app):
    return {row.task.description for row in app.query_one(appmod.TaskList).rows
            if isinstance(row, appmod.TaskRow) and not row.task.context}


def _option_offset(app, sidebar, option_id):
    index = sidebar.get_option_index(option_id)
    region = sidebar.scrollable_content_region
    x = region.x + 4
    for y in range(region.y, region.bottom):
        if app.screen.get_style_at(x, y).meta.get("option") == index:
            return x - sidebar.region.x, y - sidebar.region.y
    raise AssertionError(f"Sidebar option {option_id} is not visible")


async def _click(app, pilot, option_id):
    sidebar = app.query_one(appmod.Sidebar)
    assert await pilot.click(sidebar, offset=_option_offset(app, sidebar, option_id))
    await pilot.pause()


@pytest.mark.asyncio
async def test_tag_section_sorts_global_open_counts_and_deduplicates_case(sidebar_tag_vault):
    app = appmod.TaskApp(sidebar_tag_vault)
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.press("1")
        sidebar = app.query_one(appmod.Sidebar)
        ids = [option.id for option in sidebar.options]
        assert ids.index("h:tags") > ids.index("proj:Beta") > ids.index("h:projects")
        assert sidebar.get_option("h:tags").disabled
        assert [option.id for option in _tag_rows(sidebar)] == [
            "tag:value:person/amy", "tag:value:review", "tag:value:person/bob",
            "tag:value:urgent", "tag:value:all", "tag:value:archived", "tag:value:closed-only",
        ]
        assert _counts(sidebar) == {
            "person/amy": 4, "review": 3, "person/bob": 2, "urgent": 2,
            "all": 1, "archived": 0, "closed-only": 0,
        }
        assert not any("project/" in option.id for option in _tag_rows(sidebar))
        # Counts describe the whole vault even while this project is filtered.
        await _click(app, pilot, "proj:Beta")
        await _click(app, pilot, "tag:value:person/amy")
        assert _matches(app) == {"Beta review"}
        assert _counts(sidebar)["person/amy"] == 4
        assert _counts(sidebar)["person/bob"] == 2
        await pilot.press("7")
        assert _counts(sidebar)["person/amy"] == 4
        assert _matches(app) == {"Alpha closed"}


@pytest.mark.asyncio
@pytest.mark.parametrize("theme", ["taskman-dark-teal", "taskman-light"])
async def test_mouse_tag_selection_preserves_project_and_search_and_clear(sidebar_tag_vault, theme):
    app = appmod.TaskApp(sidebar_tag_vault, theme=theme)
    async with app.run_test(size=(140, 42)) as pilot:
        await _click(app, pilot, "proj:Alpha")
        await pilot.press("/")
        app.query_one("#search", Input).value = "review"
        await pilot.pause()
        sidebar = app.query_one(appmod.Sidebar)
        before_region = sidebar.scrollable_content_region
        tag_offset = _option_offset(app, sidebar, "tag:value:person/amy")
        assert await pilot.mouse_down(sidebar, offset=tag_offset)
        await pilot.pause()
        # Leaving Find can add a shortcut-dock row at the bottom; the pane
        # origin and tag hit target must remain fixed under the pointer.
        assert sidebar.scrollable_content_region.offset == before_region.offset
        assert sidebar.scrollable_content_region.width == before_region.width
        assert _option_offset(app, sidebar, "tag:value:person/amy") == tag_offset
        assert await pilot.mouse_up(sidebar, offset=tag_offset)
        # Pilot emits Click explicitly; mouse_down/up only probe focus/layout.
        assert await pilot.click(sidebar, offset=tag_offset)
        await pilot.pause()
        assert (app.view, app.project, app.search_query, app.task_tag) == (
            "project", "Alpha", "review", "person/amy")
        assert _matches(app) == {"Alpha review"}
        assert sidebar.highlighted_option.id == "tag:value:person/amy"
        assert str(sidebar.get_option("proj:Alpha").prompt).startswith("▸")
        assert app.query_one(appmod.TaskList).has_focus
        await _click(app, pilot, "tag:all")
        assert (app.view, app.project, app.search_query, app.task_tag) == (
            "project", "Alpha", "review", "")
        assert sidebar.highlighted_option.id == "tag:all"
        assert str(sidebar.get_option("proj:Alpha").prompt).startswith("▸")
        assert not str(sidebar.get_option("tag:value:person/amy").prompt).startswith("▸")
        assert _matches(app) == {"Alpha review"}
        assert app.query_one(appmod.TaskList).has_focus


@pytest.mark.asyncio
async def test_real_all_tag_is_separate_from_all_tags_clear(sidebar_tag_vault):
    app = appmod.TaskApp(sidebar_tag_vault)
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.press("1")
        await _click(app, pilot, "tag:value:all")
        assert app.task_tag == "all" and _matches(app) == {"All-tag task"}
        await _click(app, pilot, "tag:all")
        assert app.task_tag == "" and "Beta review" in _matches(app)


@pytest.mark.asyncio
async def test_keyboard_browses_tags_without_losing_focus_even_with_no_results(sidebar_tag_vault):
    app = appmod.TaskApp(sidebar_tag_vault)
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.press("1")
        await _click(app, pilot, "tag:value:person/amy")
        await pilot.press("/")
        app.query_one("#search", Input).value = "nothing matches"
        await pilot.pause()
        await pilot.press("alt+1")
        sidebar = app.query_one(appmod.Sidebar)
        assert sidebar.has_focus and sidebar.highlighted_option.id == "tag:value:person/amy"
        for key in ("review", "person/bob", "urgent"):
            await pilot.press("down")
            await pilot.pause()
            assert sidebar.has_focus
            assert app.task_tag.casefold() == key
            assert sidebar.highlighted_option.id == f"tag:value:{key}"
            assert app.search_query == "nothing matches" and _matches(app) == set()
        await pilot.press("up")
        await pilot.pause()
        assert sidebar.has_focus and app.task_tag.casefold() == "person/bob"
        await pilot.press("right")
        assert app.query_one(appmod.TaskList).has_focus
        await pilot.press("alt+1", "enter")
        await pilot.pause()
        assert app.query_one(appmod.TaskList).has_focus
        assert app.task_tag.casefold() == "person/bob"


@pytest.mark.asyncio
async def test_counts_and_sort_refresh_after_edit_complete_and_undo(sidebar_tag_vault):
    app = appmod.TaskApp(sidebar_tag_vault)
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.press("1")
        table = app.query_one(appmod.TaskList)
        target = next(row.task for row in table.rows if isinstance(row, appmod.TaskRow)
                      and row.task.description == "Alpha review")
        table.cursor = table.index_of(target.id)
        table._cursor_moved()
        await pilot.press("g")
        app.screen.query_one("#task-tags", Input).value = "person/bob"
        await pilot.press("enter")
        await pilot.pause()
        sidebar = app.query_one(appmod.Sidebar)
        assert _counts(sidebar)["person/amy"] == 3
        assert _counts(sidebar)["person/bob"] == 3
        assert _counts(sidebar)["review"] == 2
        assert [option.id for option in _tag_rows(sidebar)][:4] == [
            "tag:value:person/amy", "tag:value:person/bob", "tag:value:review", "tag:value:urgent"]
        await pilot.press("u")
        await pilot.pause()
        assert _counts(sidebar)["person/amy"] == 4
        assert _counts(sidebar)["person/bob"] == 2
        await _click(app, pilot, "tag:value:all")
        await pilot.press("space")
        await pilot.pause()
        assert _matches(app) == set() and app.task_tag == "all"
        assert _counts(sidebar)["all"] == 0
        assert sidebar.highlighted_option.id == "tag:value:all"
        await pilot.press("u")
        await pilot.pause()
        assert _counts(sidebar)["all"] == 1 and _matches(app) == {"All-tag task"}


@pytest.mark.asyncio
async def test_active_tag_remains_clearable_after_its_last_tagged_task_is_edited(sidebar_tag_vault):
    app = appmod.TaskApp(sidebar_tag_vault)
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.press("1")
        await _click(app, pilot, "tag:value:all")
        await pilot.press("g")
        app.screen.query_one("#task-tags", Input).value = ""
        await pilot.press("enter")
        await pilot.pause()
        sidebar = app.query_one(appmod.Sidebar)
        assert app.task_tag == "all" and _matches(app) == set()
        assert sidebar.highlighted_option.id == "tag:value:all"
        assert _counts(sidebar)["all"] == 0
        await _click(app, pilot, "tag:all")
        assert app.task_tag == "" and "All-tag task" in _matches(app)


@pytest.mark.asyncio
async def test_selecting_task_tag_from_notes_opens_all_tasks_and_keeps_notes_tag_independent(sidebar_tag_vault):
    NotesStore(sidebar_tag_vault).create("Reference", "notes-only reference text", tags=("reference",))
    app = appmod.TaskApp(sidebar_tag_vault)
    async with app.run_test(size=(140, 42)) as pilot:
        await pilot.press("8")
        app.note_tag = "reference"
        await pilot.press("/")
        app.query_one("#search", Input).value = "notes-only"
        await pilot.pause()
        sidebar = app.query_one(appmod.Sidebar)
        assert _counts(sidebar)["person/amy"] == 4
        await _click(app, pilot, "tag:value:person/amy")
        assert (app.view, app.project, app.search_query, app.task_tag) == ("all", "", "", "person/amy")
        assert app.note_tag == "reference"
        assert _matches(app) == {"Alpha review", "Alpha urgent", "Beta review", "Child Amy"}
        assert app.query_one(appmod.TaskList).has_focus


@pytest.mark.asyncio
async def test_many_tags_scroll_and_keep_keyboard_selection_visible(sidebar_tag_vault):
    path = sidebar_tag_vault / "Tasks/Many.md"
    path.write_text("".join(f"- [ ] Topic work {index:02d} #topic{index:02d}\n" for index in range(45)),
                    encoding="utf-8")
    app = appmod.TaskApp(sidebar_tag_vault)
    async with app.run_test(size=(120, 24)) as pilot:
        await pilot.press("1", "alt+1", "end")
        await pilot.pause()
        sidebar = app.query_one(appmod.Sidebar)
        assert sidebar.has_focus and sidebar.scroll_y > 0
        assert sidebar.highlighted_option.id == "tag:value:closed-only"
        assert app.task_tag == "closed-only"
        _option_offset(app, sidebar, "tag:value:closed-only")
        await pilot.press("up", "up")
        await pilot.pause()
        assert sidebar.has_focus and app.task_tag == "topic44"
        assert sidebar.highlighted_option.id == "tag:value:topic44"
        _option_offset(app, sidebar, "tag:value:topic44")
        assert _matches(app) == {"Topic work 44"}
        await pilot.press("enter")
        assert app.query_one(appmod.TaskList).has_focus
