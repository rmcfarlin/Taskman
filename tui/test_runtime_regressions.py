"""Exercise the real notification renderer and modal lifecycle in private vaults.

Textual's run_test() disables notifications by default. These tests explicitly
enable them: a successful file write alone does not prove the UI survived it.
"""

import asyncio
import datetime as dt

import pytest
from textual.widgets import Button, Input, TextArea
from textual.widgets._toast import Toast

from tui import app as appmod
from tui.commands import CommandScreen


@pytest.fixture
def runtime_vault(tmp_path, monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("TASKMAN_THEME", raising=False)
    monkeypatch.setattr(appmod, "write_theme_file", lambda *_args, **_kwargs: None)
    (tmp_path / "Tasks").mkdir()
    (tmp_path / "Projects").mkdir()
    (tmp_path / "Tasks/Inbox.md").write_text(
        f"- [ ] Review [bold]literal[/bold] 📅 {dt.date.today().isoformat()}\n"
        "  Original context.\n"
        "- [ ] Second task\n",
        encoding="utf-8",
    )
    return tmp_path


def _files(vault):
    return {str(path.relative_to(vault)): path.read_bytes() for path in vault.rglob("*.md")}


async def _type(pilot, text):
    await pilot.press(*(character if character != " " else "space" for character in text))


async def _rendered_notifications(pilot, app):
    # Drain the Notify message, ToastRack mount, and actual layout/render cycle.
    await pilot.pause(0.15)
    toasts = list(app.screen.query(Toast))
    assert toasts, "The test must exercise a mounted toast, not a stub notification"
    rendered = [toast.render().plain for toast in toasts]
    assert app.is_running
    return rendered


async def _rendered_status(pilot, app):
    """Routine confirmations paint the status row without covering content."""
    await pilot.pause(0.15)
    status = app.query_one("#status-info")
    assert status.display and status.has_class("-message")
    assert status.size.height == 1
    assert not list(app.screen.query(Toast))
    return status.render_line(0).text


@pytest.mark.parametrize("operation,notice", [
    ("complete", "Done: Review [bold]literal[/bold]"),
    ("add", "Added: New [red]literal[/red] task"),
    ("edit", "Saved"),
    ("due", "Dates saved"),
    ("status", "Status:"),
    ("priority", "Priority:"),
    ("project", "Created Projects/Fresh-Project.md and assigned the task"),
    ("note", "Note saved"),
    ("delete", "Task deleted"),
])
def test_task_mutations_render_status_and_restore_exact_files(
    runtime_vault, operation, notice
):
    async def go():
        app = appmod.TaskApp(runtime_vault, theme="taskman-teal")
        before = _files(runtime_vault)
        async with app.run_test(size=(110, 32), notifications=True) as pilot:
            await pilot.press("1")  # This workflow exercises the All open view.
            if operation == "complete":
                await pilot.press("space")
            elif operation == "add":
                await pilot.press("a")
                await _type(pilot, "New [red]literal[/red] task")
                await pilot.press("enter")
            elif operation == "edit":
                await pilot.press("e", "ctrl+shift+a")
                await _type(pilot, "Updated [green]literal[/green]")
                await pilot.press("enter")
            elif operation == "due":
                await pilot.press("d", "ctrl+shift+a")
                await _type(pilot, "tomorrow")
                await pilot.press("enter")
            elif operation == "status":
                await pilot.press("s", "down", "enter")
            elif operation == "priority":
                await pilot.press("p", "home", "enter")
            elif operation == "project":
                await pilot.press("j")
                await _type(pilot, "Fresh Project")
                await pilot.press("enter")
            elif operation == "note":
                await pilot.press("n")
                await _type(pilot, " Added [blue]literal[/blue] context.")
                await pilot.press("ctrl+s")
            else:
                await pilot.press("delete", "shift+tab", "enter")
            assert notice in await _rendered_status(pilot, app)
            changed = _files(runtime_vault)
            assert changed != before
            await pilot.press("u")
            assert "Undid:" in await _rendered_status(pilot, app)
            assert _files(runtime_vault) == before
            await pilot.press("ctrl+y")
            assert "Redid:" in await _rendered_status(pilot, app)
            assert _files(runtime_vault) == changed
            await pilot.press("q")
    asyncio.run(go())


def test_storage_error_renders_and_leaves_app_usable(runtime_vault, monkeypatch):
    def denied(*_args, **_kwargs):
        raise PermissionError("Cannot write [draft] task file")

    monkeypatch.setattr(appmod.tm, "edit_text", denied)

    async def go():
        app = appmod.TaskApp(runtime_vault, theme="taskman-teal")
        before = _files(runtime_vault)
        async with app.run_test(size=(100, 30), notifications=True) as pilot:
            await pilot.press("1")  # This workflow exercises the All open view.
            await pilot.press("e", "ctrl+shift+a")
            await _type(pilot, "Attempted edit")
            await pilot.press("enter")
            notices = await _rendered_notifications(pilot, app)
            assert any("Could not save change" in text and "[draft]" in text for text in notices)
            assert _files(runtime_vault) == before
            assert not app.history.can_undo
            await pilot.press("ctrl+k")
            assert isinstance(app.screen, CommandScreen)
            await pilot.press("escape", "q")
    asyncio.run(go())


@pytest.mark.parametrize("restore", ["undo", "redo"])
def test_external_edit_conflict_renders_warning_without_overwriting(runtime_vault, restore):
    async def go():
        app = appmod.TaskApp(runtime_vault, theme="taskman-teal")
        path = runtime_vault / "Tasks/Inbox.md"
        async with app.run_test(size=(100, 30), notifications=True) as pilot:
            await pilot.press("1")  # This workflow exercises the All open view.
            await pilot.press("space")
            await _rendered_status(pilot, app)
            if restore == "redo":
                await pilot.press("u")
                await _rendered_status(pilot, app)
            path.write_bytes(path.read_bytes() + b"\nExternal [draft] context.\n")
            external = _files(runtime_vault)
            await pilot.press("u" if restore == "undo" else "ctrl+y")
            notices = await _rendered_notifications(pilot, app)
            assert any("Could not restore change" in text and "changed outside" in text for text in notices)
            assert _files(runtime_vault) == external
            await pilot.press("ctrl+k", "escape", "q")
    asyncio.run(go())


@pytest.mark.parametrize("key,screen_type", [
    ("a", appmod.AddScreen),
    ("j", appmod.ProjectScreen),
    ("m", appmod.ThemeScreen),
    ("f1", appmod.HelpScreen),
    ("ctrl+k", CommandScreen),
    ("delete", appmod.ConfirmScreen),
])
def test_open_dialog_survives_terminal_resize_and_clock_update(runtime_vault, key, screen_type):
    async def go():
        app = appmod.TaskApp(runtime_vault, theme="taskman-teal")
        before = _files(runtime_vault)
        async with app.run_test(size=(130, 34), notifications=True) as pilot:
            await pilot.press("1")  # This workflow exercises the All open view.
            await pilot.press(key)
            assert isinstance(app.screen, screen_type)
            for width, height in [(80, 24), (60, 20), (130, 34)]:
                await pilot.resize_terminal(width, height)
                app._tick()
                await pilot.pause()
                assert isinstance(app.screen, screen_type)
                assert app.focused is not None
                assert app.screen.region.width == width
                assert app.screen.region.height == height
            await pilot.press("escape")
            assert _files(runtime_vault) == before
            assert app.query_one(appmod.TaskList).has_focus
            await pilot.press("q")
    asyncio.run(go())


def test_note_resize_keep_editing_discard_and_save_use_status(runtime_vault):
    async def go():
        app = appmod.TaskApp(runtime_vault, theme="taskman-teal")
        before = _files(runtime_vault)
        async with app.run_test(size=(110, 32), notifications=True) as pilot:
            await pilot.press("1")  # This workflow exercises the All open view.
            await pilot.press("n")
            await _type(pilot, " Unsaved addition.")
            editor = app.screen.query_one(TextArea)
            draft = editor.text
            await pilot.resize_terminal(60, 20)
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, appmod.ConfirmScreen)
            assert app.screen.query_one("#cancel", Button).has_focus
            await pilot.press("enter")  # Keep editing is the safe default.
            await pilot.pause()
            assert isinstance(app.screen, appmod.NoteScreen)
            assert app.screen.query_one(TextArea).text == draft
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, appmod.ConfirmScreen)
            assert app.screen.query_one("#cancel", Button).has_focus
            await pilot.press("shift+tab")
            await pilot.pause()
            assert app.screen.query_one("#ok", Button).has_focus
            await pilot.press("enter")
            await pilot.pause()
            assert _files(runtime_vault) == before
            assert app.query_one(appmod.TaskList).has_focus
            await pilot.resize_terminal(110, 32)
            await pilot.press("n")
            await _type(pilot, " Saved addition.")
            await pilot.press("ctrl+s")
            assert "Note saved" in await _rendered_status(pilot, app)
            assert "Saved addition." in (runtime_vault / "Tasks/Inbox.md").read_text(encoding="utf-8")
            await pilot.press("q")
    asyncio.run(go())


def test_search_state_does_not_shadow_framework_query_and_focus(runtime_vault):
    async def go():
        app = appmod.TaskApp(runtime_vault, theme="taskman-teal")
        async with app.run_test(size=(100, 30), notifications=True) as pilot:
            await pilot.press("1")  # This workflow exercises the All open view.
            assert app.query("#tasks").first() is app.query_one(appmod.TaskList)
            await app.action_focus("tasks")
            await pilot.press("/")
            await pilot.pause()
            assert app.query_one("#search", Input).has_focus
            await _type(pilot, "Review")
            assert app.search_query == "Review"
            assert app.query("#tasks").first() is app.query_one(appmod.TaskList)
            await pilot.press("escape", "q")
    asyncio.run(go())


def test_repeated_keyboard_dialog_cycles_keep_focus_and_unsaved_files(runtime_vault):
    async def go():
        app = appmod.TaskApp(runtime_vault, theme="taskman-teal")
        before = _files(runtime_vault)
        async with app.run_test(size=(110, 32), notifications=True) as pilot:
            await pilot.press("1")  # This workflow exercises the All open view.
            # Fifty dialog open/close actions, while background notifications and
            # resize events exercise layout and focus restoration between screens.
            for index, key in enumerate(["ctrl+k", "f1", "a", "e", "d", "p", "s", "j", "n", "m"] * 2 + ["a", "e", "n", "f1", "ctrl+k"]):
                app.announce(f"Keyboard check {index + 1} [literal]")
                await pilot.press(key)
                assert isinstance(app.screen, appmod.ModalScreen)
                if index % 5 == 0:
                    await pilot.resize_terminal(80 if index % 10 == 0 else 110, 28)
                await pilot.press("escape")
                await pilot.pause()
                assert app.query_one(appmod.TaskList).has_focus
                assert app.is_running
                assert _files(runtime_vault) == before
            await pilot.press("q")
    asyncio.run(go())
