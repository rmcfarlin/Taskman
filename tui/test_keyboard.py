"""Keyboard contracts exercised against the real Textual app and a private vault."""

import asyncio
import datetime as dt
from pathlib import Path

import pytest

from tui import app as appmod
from tui import taskman as tm


@pytest.fixture
def keyboard_vault(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.delenv("TASKMAN_THEME", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    (tmp_path / "Tasks").mkdir()
    (tmp_path / "Projects").mkdir()
    note = "".join(f"  Context line {i:02}: details that need keyboard scrolling.\n" for i in range(45))
    (tmp_path / "Tasks" / "Inbox.md").write_text(
        f"# Inbox\n\n- [ ] Parent task 📅 {dt.date.today().isoformat()}\n"
        + note
        + "  - [ ] Child one\n  - [ ] Child two\n- [ ] Separate task\n",
        encoding="utf-8",
    )
    return tmp_path


def _task(root: Path, description: str):
    return next(t for t in tm.load_all(root) if t.description == description)


async def _type(pilot, text: str) -> None:
    await pilot.press(*(ch if ch != " " else "space" for ch in text))


def test_inspector_note_is_readable_without_a_mouse(keyboard_vault):
    async def go():
        app = appmod.TaskApp(keyboard_vault, theme="taskman-teal")
        async with app.run_test(notifications=True, size=(130, 30)) as pilot:
            await pilot.press("1")  # This workflow exercises the All open view.
            await pilot.pause()
            tasks = app.query_one(appmod.TaskList)
            assert tasks.current.description == "Parent task"
            await pilot.press("right")
            await pilot.pause()
            inspector = app.query_one(appmod.Inspector)
            assert inspector.display and inspector.has_focus
            assert inspector.max_scroll_y > 0
            await pilot.press("pagedown")
            await pilot.wait_for_scheduled_animations()
            assert inspector.scroll_y > 0
            await pilot.press("end")
            await pilot.wait_for_scheduled_animations()
            assert inspector.scroll_y == inspector.max_scroll_y
            await pilot.press("home")
            await pilot.wait_for_scheduled_animations()
            assert inspector.scroll_y == 0
            await pilot.press("left")
            await pilot.pause()
            assert tasks.has_focus
    asyncio.run(go())


@pytest.mark.parametrize("action", ["c", "e", "d"])
def test_inspector_actions_target_the_focused_child(keyboard_vault, action):
    async def go():
        app = appmod.TaskApp(keyboard_vault, theme="taskman-teal")
        async with app.run_test(notifications=True, size=(130, 30)) as pilot:
            await pilot.press("1")  # This workflow exercises the All open view.
            await pilot.pause()
            await pilot.press("right", "tab")
            await pilot.pause()
            kids = app.query_one("#ins-kids", appmod.OptionList)
            assert kids.has_focus
            assert kids.highlighted == 0
            await pilot.press(action)
            await pilot.pause()
            if action == "e":
                assert isinstance(app.screen, appmod.EditScreen)
                assert app.screen.query_one("#text", appmod.Input).value == "Child one"
                await pilot.press("ctrl+a")
                await _type(pilot, "Child renamed")
                await pilot.press("enter")
            elif action == "d":
                assert isinstance(app.screen, appmod.DueScreen)
                await _type(pilot, "tomorrow")
                await pilot.press("enter")
            await pilot.pause()
            parent = _task(keyboard_vault, "Parent task")
            assert parent.open and parent.due == dt.date.today()
            assert app.query_one(appmod.TaskList).current.description == "Parent task"
            assert kids.has_focus
            assert _task(keyboard_vault, "Child two").open
            child = _task(keyboard_vault, "Child renamed" if action == "e" else "Child one")
            if action == "c":
                assert child.done
            elif action == "d":
                assert child.due == dt.date.today() + dt.timedelta(days=1)
    asyncio.run(go())


@pytest.mark.parametrize("key,screen_type", [("a", appmod.AddScreen), ("e", appmod.EditScreen)])
def test_empty_task_text_stays_in_the_form(keyboard_vault, key, screen_type):
    before = (keyboard_vault / "Tasks" / "Inbox.md").read_bytes()

    async def go():
        app = appmod.TaskApp(keyboard_vault, theme="taskman-teal")
        async with app.run_test(notifications=True, size=(90, 28)) as pilot:
            await pilot.press("1")  # This workflow exercises the All open view.
            await pilot.press(key)
            await pilot.pause()
            await _type(pilot, "temporary text")
            await pilot.press("ctrl+a", "backspace", "enter")
            await pilot.pause()
            assert isinstance(app.screen, screen_type)
            assert app.screen.query_one("#text", appmod.Input).has_focus
            assert str(app.screen.query_one("#form-error").render()).strip()
            assert (keyboard_vault / "Tasks" / "Inbox.md").read_bytes() == before
            await pilot.press("escape")
            await pilot.pause()
            assert app.query_one(appmod.TaskList).has_focus
    asyncio.run(go())


def test_invalid_due_date_preserves_input_and_allows_correction(keyboard_vault):
    async def go():
        app = appmod.TaskApp(keyboard_vault, theme="taskman-teal")
        async with app.run_test(notifications=True, size=(90, 28)) as pilot:
            await pilot.press("1")  # This workflow exercises the All open view.
            await pilot.press("d", "ctrl+a")
            await _type(pilot, "notadate")
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, appmod.DueScreen)
            field = app.screen.query_one("#due", appmod.Input)
            assert field.value == "notadate" and field.has_focus
            assert str(app.screen.query_one("#form-error").render()).strip()
            assert _task(keyboard_vault, "Parent task").due == dt.date.today()
            await pilot.press("ctrl+a")
            await _type(pilot, "tomorrow")
            await pilot.press("enter")
            await pilot.pause()
            assert not isinstance(app.screen, appmod.DueScreen)
            assert _task(keyboard_vault, "Parent task").due == dt.date.today() + dt.timedelta(days=1)
    asyncio.run(go())


@pytest.mark.parametrize("raw", ["+99999999999999999999999999999999999999", "+999999999", "2026-02-30"])
def test_due_parser_handles_out_of_range_input(raw):
    assert appmod.TaskApp.parse_due_text(raw, dt.date(2026, 9, 4)) == "invalid"


def test_adding_from_search_reveals_the_created_task(keyboard_vault):
    async def go():
        app = appmod.TaskApp(keyboard_vault, theme="taskman-teal")
        async with app.run_test(notifications=True, size=(90, 28)) as pilot:
            await pilot.press("1")  # This workflow exercises the All open view.
            await pilot.press("slash")
            await _type(pilot, "Separate")
            await pilot.press("enter")
            await pilot.pause()
            assert app.search_query == "Separate"
            await pilot.press("a")
            await _type(pilot, "Newly created task")
            await pilot.press("enter")
            await pilot.pause()
            assert app.search_query == ""
            assert app.query_one("#search", appmod.Input).value == ""
            assert app.query_one(appmod.TaskList).current.description == "Newly created task"
    asyncio.run(go())


def test_typing_shortcut_letters_in_a_modal_cannot_mutate_tasks(keyboard_vault):
    before = (keyboard_vault / "Tasks" / "Inbox.md").read_bytes()

    async def go():
        app = appmod.TaskApp(keyboard_vault, theme="taskman-teal")
        async with app.run_test(notifications=True, size=(90, 28)) as pilot:
            await pilot.press("1")  # This workflow exercises the All open view.
            await pilot.press("a")
            await _type(pilot, "acsdpejnq123")
            await pilot.pause()
            assert isinstance(app.screen, appmod.AddScreen)
            assert app.screen.query_one("#text", appmod.Input).value == "acsdpejnq123"
            assert app.view == "all"
            assert (keyboard_vault / "Tasks" / "Inbox.md").read_bytes() == before
            await pilot.press("escape")
    asyncio.run(go())


def test_delete_confirmation_defaults_to_keeping_the_branch(keyboard_vault):
    before = (keyboard_vault / "Tasks" / "Inbox.md").read_bytes()

    async def go():
        app = appmod.TaskApp(keyboard_vault, theme="taskman-teal")
        async with app.run_test(notifications=True, size=(90, 28)) as pilot:
            await pilot.press("1")  # This workflow exercises the All open view.
            await pilot.press("delete")
            await pilot.pause()
            assert isinstance(app.screen, appmod.ConfirmScreen)
            assert app.screen.query_one("#cancel", appmod.Button).has_focus
            await pilot.press("enter")
            await pilot.pause()
            assert not isinstance(app.screen, appmod.ConfirmScreen)
            assert (keyboard_vault / "Tasks" / "Inbox.md").read_bytes() == before
    asyncio.run(go())


@pytest.mark.parametrize("size", [(80, 24), (60, 20)])
def test_compact_help_is_in_bounds_and_keyboard_scrollable(keyboard_vault, size):
    async def go():
        app = appmod.TaskApp(keyboard_vault, theme="taskman-teal")
        async with app.run_test(notifications=True, size=size) as pilot:
            await pilot.press("1")  # This workflow exercises the All open view.
            await pilot.press("f1")
            await pilot.pause()
            assert isinstance(app.screen, appmod.HelpScreen)
            dialog = app.screen.query_one("#dlg")
            assert dialog.region.x >= 0 and dialog.region.y >= 0
            assert dialog.region.right <= size[0] and dialog.region.bottom <= size[1]
            content = app.screen.query_one("#help-content", appmod.VerticalScroll)
            assert content.has_focus and content.max_scroll_y > 0
            await pilot.press("end")
            await pilot.wait_for_scheduled_animations()
            assert content.scroll_y == content.max_scroll_y
            await pilot.press("escape")
            await pilot.pause()
            assert app.query_one(appmod.TaskList).has_focus
    asyncio.run(go())


@pytest.mark.parametrize("key", ["a", "d", "n"])
def test_compact_forms_keep_action_buttons_visible(keyboard_vault, key):
    async def go():
        app = appmod.TaskApp(keyboard_vault, theme="taskman-teal")
        async with app.run_test(notifications=True, size=(60, 20)) as pilot:
            await pilot.press("1")  # This workflow exercises the All open view.
            await pilot.press(key)
            await pilot.pause()
            dialog = app.screen.query_one("#dlg")
            assert dialog.region.x >= 0 and dialog.region.y >= 0
            assert dialog.region.right <= 60 and dialog.region.bottom <= 20
            for button in app.screen.query(appmod.Button):
                assert button.region.x >= dialog.region.x
                assert button.region.right <= dialog.region.right
                assert button.region.y >= dialog.region.y
                assert button.region.bottom <= dialog.region.bottom
    asyncio.run(go())


def test_narrow_sidebar_focus_opens_view_picker_not_commands(keyboard_vault):
    async def go():
        app = appmod.TaskApp(keyboard_vault, theme="taskman-teal")
        async with app.run_test(notifications=True, size=(60, 20)) as pilot:
            await pilot.press("1")  # This workflow exercises the All open view.
            await pilot.pause()
            assert not app.query_one(appmod.Sidebar).display
            await pilot.press("alt+1")
            await pilot.pause()
            assert isinstance(app.screen, appmod.PickScreen)
            assert not isinstance(app.screen, appmod.CommandScreen)
            ids = [oid for oid, _ in app.screen._options]
            assert ids == [name for _, name, _ in appmod.SIDEBAR_VIEWS] + ["notes"]
            assert app.screen._options[-1][1].plain == "8 Notes"
            await pilot.press("enter")
            await pilot.pause()
            assert app.view == "all"
            assert app.query_one(appmod.TaskList).has_focus
    asyncio.run(go())


def test_narrow_inspector_receives_focus_and_returns_to_tasks(keyboard_vault):
    async def go():
        app = appmod.TaskApp(keyboard_vault, theme="taskman-teal")
        async with app.run_test(notifications=True, size=(60, 20)) as pilot:
            await pilot.press("1")  # This workflow exercises the All open view.
            await pilot.press("i")
            await pilot.pause()
            inspector = app.query_one(appmod.Inspector)
            assert inspector.display and inspector.has_focus
            assert inspector.region.x >= 0 and inspector.region.right <= 60
            await pilot.press("escape")
            await pilot.pause()
            assert not inspector.display and app.query_one(appmod.TaskList).has_focus
    asyncio.run(go())


@pytest.mark.parametrize("key", ["delete", "n", "j", "t"])
def test_task_brackets_are_literal_in_dialogs(keyboard_vault, key):
    path = keyboard_vault / "Tasks" / "Inbox.md"
    title = "Parent [bold]literal[/bold]"
    path.write_text(path.read_text(encoding="utf-8").replace("Parent task", title), encoding="utf-8")

    async def go():
        app = appmod.TaskApp(keyboard_vault, theme="taskman-teal")
        async with app.run_test(notifications=True, size=(110, 30)) as pilot:
            await pilot.press("1")  # This workflow exercises the All open view.
            await pilot.press(key)
            await pilot.pause()
            if key == "delete":
                assert title in str(app.screen.query_one(".field", appmod.Label).render())
            else:
                rendered_title = appmod.Text.from_markup(app.screen.query_one("#dlg").border_title).plain
                assert title in rendered_title
            await pilot.press("escape")
    asyncio.run(go())


def test_inspector_starts_at_the_top_when_a_different_task_is_selected(keyboard_vault):
    path = keyboard_vault / "Tasks" / "Inbox.md"
    with path.open("a", encoding="utf-8") as source:
        source.writelines(f"  Separate task context {i:02}\n" for i in range(45))

    async def go():
        app = appmod.TaskApp(keyboard_vault, theme="taskman-teal")
        async with app.run_test(notifications=True, size=(130, 30)) as pilot:
            await pilot.press("1")  # This workflow exercises the All open view.
            await pilot.press("right", "end")
            await pilot.wait_for_scheduled_animations()
            inspector = app.query_one(appmod.Inspector)
            assert inspector.scroll_y > 0
            await pilot.press("left", "end")
            await pilot.pause()
            assert app.query_one(appmod.TaskList).current.description == "Separate task"
            await pilot.press("right")
            await pilot.wait_for_scheduled_animations()
            assert inspector.has_focus and inspector.max_scroll_y > 0
            assert inspector.scroll_y == 0
    asyncio.run(go())


def test_theme_preview_preserves_the_highlighted_child(keyboard_vault, monkeypatch):
    monkeypatch.setattr(appmod, "write_theme_file", lambda *_args, **_kwargs: None)

    async def go():
        app = appmod.TaskApp(keyboard_vault, theme="taskman-teal")
        async with app.run_test(notifications=True, size=(130, 30)) as pilot:
            await pilot.press("1")  # This workflow exercises the All open view.
            await pilot.press("right", "tab", "down")
            await pilot.pause()
            kids = app.query_one("#ins-kids", appmod.OptionList)
            assert kids.has_focus and kids.highlighted == 1
            await pilot.press("m", "down")
            await pilot.pause()
            assert isinstance(app.screen, appmod.ThemeScreen)
            await pilot.press("escape")
            await pilot.pause()
            assert kids.has_focus and kids.highlighted == 1
            assert app._selected().description == "Child two"
            # The same repaint also preserves a reader's position in the note.
            await pilot.press("shift+tab", "end")
            await pilot.wait_for_scheduled_animations()
            inspector = app.query_one(appmod.Inspector)
            position = inspector.scroll_y
            assert inspector.has_focus and position > 0
            await pilot.press("m", "down")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.wait_for_scheduled_animations()
            assert inspector.has_focus and inspector.scroll_y == position
    asyncio.run(go())


def test_read_child_details_keeps_child_target_after_theme_preview(keyboard_vault, monkeypatch):
    monkeypatch.setattr(appmod, "write_theme_file", lambda *_args, **_kwargs: None)

    async def go():
        app = appmod.TaskApp(keyboard_vault, theme="taskman-teal")
        async with app.run_test(notifications=True, size=(130, 30)) as pilot:
            await pilot.press("1")  # This workflow exercises the All open view.
            await pilot.press("right", "tab", "down", "ctrl+k")
            await _type(pilot, "Read task details")
            await pilot.press("enter")
            await pilot.pause()
            inspector = app.query_one(appmod.Inspector)
            assert inspector.has_focus
            assert "Child two" in str(inspector.query_one("#ins-title").render())
            await pilot.press("m", "down")
            await pilot.pause()
            assert isinstance(app.screen, appmod.ThemeScreen)
            await pilot.press("escape")
            await pilot.pause()
            assert inspector.has_focus
            assert "Child two" in str(inspector.query_one("#ins-title").render())
            await pilot.press("n")
            await pilot.pause()
            assert isinstance(app.screen, appmod.NoteScreen)
            title = appmod.Text.from_markup(app.screen.query_one("#dlg").border_title).plain
            assert "Child two" in title
            assert app.screen.query_one("#note-text", appmod.TextArea).text == ""
            await pilot.press("escape")
    asyncio.run(go())


def test_subtasks_collapse_and_expand_from_the_task_list(keyboard_vault):
    async def go():
        app = appmod.TaskApp(keyboard_vault, theme="taskman-teal")
        async with app.run_test(notifications=True, size=(130, 30)) as pilot:
            await pilot.press("1")
            await pilot.pause()
            tasks = app.query_one(appmod.TaskList)
            assert tasks.current.description == "Parent task"
            visible = [r.task.description for r in tasks.rows if isinstance(r, appmod.TaskRow)]
            assert visible == ["Parent task", "Child one", "Child two", "Separate task"]
            await pilot.press("minus")
            await pilot.pause()
            visible = [r.task.description for r in tasks.rows if isinstance(r, appmod.TaskRow)]
            assert visible == ["Parent task", "Separate task"]
            row = next(r for r in tasks.rows if isinstance(r, appmod.TaskRow) and r.task.description == "Parent task")
            assert row.folded is True
            await pilot.press("right")
            await pilot.pause()
            visible = [r.task.description for r in tasks.rows if isinstance(r, appmod.TaskRow)]
            assert visible == ["Parent task", "Child one", "Child two", "Separate task"]
            assert tasks.has_focus
            await pilot.press("left")
            await pilot.pause()
            visible = [r.task.description for r in tasks.rows if isinstance(r, appmod.TaskRow)]
            assert visible == ["Parent task", "Separate task"]
            await pilot.press("left")
            await pilot.pause()
            assert app.query_one(appmod.Sidebar).has_focus
    asyncio.run(go())


def test_subtask_fold_marker_toggles_with_the_mouse(keyboard_vault):
    async def go():
        app = appmod.TaskApp(keyboard_vault, theme="taskman-teal")
        async with app.run_test(notifications=True, size=(130, 30)) as pilot:
            await pilot.press("1")
            await pilot.pause()
            tasks = app.query_one(appmod.TaskList)
            assert tasks.current.description == "Parent task"
            # Content column 9 is the fold marker; the section header row
            # occupies content line 1, so the parent row is line 2.
            inset_x = tasks.content_region.x - tasks.region.x
            inset_y = tasks.content_region.y - tasks.region.y
            await pilot.click(tasks, offset=(9 + inset_x, 2 + inset_y))
            await pilot.pause()
            visible = [r.task.description for r in tasks.rows if isinstance(r, appmod.TaskRow)]
            assert visible == ["Parent task", "Separate task"]
            await pilot.click(tasks, offset=(9 + inset_x, 2 + inset_y))
            await pilot.pause()
            visible = [r.task.description for r in tasks.rows if isinstance(r, appmod.TaskRow)]
            assert visible == ["Parent task", "Child one", "Child two", "Separate task"]
    asyncio.run(go())
