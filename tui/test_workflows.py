"""Keyboard integration workflows against disposable Markdown vaults only."""

import asyncio
import datetime as dt
import pytest
from textual.widgets import Input, OptionList, TextArea

from tui import app as appmod
from tui.commands import CommandScreen


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("TASKMAN_THEME", raising=False)
    (tmp_path / "Tasks").mkdir()
    (tmp_path / "Projects").mkdir()
    (tmp_path / "Tasks" / "Inbox.md").write_text(
        "# Inbox\n\n"
        f"- [ ] Anchor parent 📅 {dt.date.today().isoformat()}\n"
        "  - [ ] Child target\n"
        "  - [ ] Sibling safe\n"
        "- [ ] Capture idea 🔼\n",
        encoding="utf-8",
    )
    (tmp_path / "Projects" / "Alpha.md").write_text(
        "# Alpha\n\n## Tasks\n\n- [ ] Alpha launch #project/Alpha\n", encoding="utf-8"
    )
    return tmp_path


async def _type(pilot, text):
    await pilot.press(*(char if char != " " else "space" for char in text))


async def _select(pilot, app, description):
    await pilot.press("f")
    await _type(pilot, description)
    await pilot.press("escape")
    await pilot.pause()
    assert app.query_one(appmod.TaskList).current.description == description


async def _command(pilot, app, title):
    await pilot.press("ctrl+k")
    assert isinstance(app.screen, CommandScreen)
    await _type(pilot, title)
    await pilot.press("enter")
    await pilot.pause()


def _files(vault):
    return {str(path.relative_to(vault)): path.read_bytes() for path in vault.rglob("*.md")}


def test_commands_type_safely_then_dispatch_add(vault):
    async def go():
        app = appmod.TaskApp(vault, theme="taskman-teal")
        save_calls = []
        app.action_save = lambda: save_calls.append(True)
        before = _files(vault)
        async with app.run_test(notifications=True, size=(100, 30)) as pilot:
            await pilot.press("ctrl+k")
            assert isinstance(app.screen, CommandScreen)
            await _type(pilot, "uadq1")
            await pilot.press("ctrl+s")
            assert app.screen.query_one(Input).value == "uadq1"
            assert save_calls == []
            assert not app.history.can_undo
            assert _files(vault) == before
            await pilot.press("ctrl+a")
            await _type(pilot, "Add task")
            await pilot.press("enter")
            assert isinstance(app.screen, appmod.AddScreen)
            await _type(pilot, "A unique capture")
            await pilot.press("enter")
            assert app.query_one(appmod.TaskList).current.description == "A unique capture"
            assert "A unique capture" in (vault / "Tasks/Inbox.md").read_text(encoding="utf-8")
            assert app.history.undo_label == "Add task"
    asyncio.run(go())


def test_command_shortcut_opens_from_search_and_closes_without_losing_query(vault):
    async def go():
        app = appmod.TaskApp(vault, theme="taskman-teal")
        before = _files(vault)
        async with app.run_test(notifications=True, size=(100, 30)) as pilot:
            await pilot.press("f")
            await _type(pilot, "Capture")
            search = app.query_one("#search", Input)
            assert search.has_focus
            await pilot.press("ctrl+k")
            assert isinstance(app.screen, CommandScreen)
            await _type(pilot, "due")
            await pilot.press("ctrl+k")
            assert not isinstance(app.screen, CommandScreen)
            assert search.has_focus
            assert app.search_query == search.value == "Capture"
            assert _files(vault) == before
    asyncio.run(go())


@pytest.mark.parametrize("title,view,project", [
    ("Go to Today", "today", ""),
    ("Go to All open", "all", ""),
    ("Open project: Alpha", "project", "Alpha"),
])
def test_command_navigation_clears_the_existing_search(vault, title, view, project):
    async def go():
        app = appmod.TaskApp(vault, theme="taskman-teal")
        async with app.run_test(notifications=True, size=(100, 30)) as pilot:
            await pilot.press("f")
            await _type(pilot, "Capture")
            await pilot.press("enter")
            assert app.search_query == "Capture"
            await _command(pilot, app, title)
            assert (app.view, app.project, app.search_query) == (view, project, "")
            assert app.query_one("#search", Input).value == ""
            assert app.query_one(appmod.TaskList).has_focus
    asyncio.run(go())


@pytest.mark.parametrize("action", ["edit", "due"])
def test_palette_preserves_the_highlighted_inspector_child(vault, action):
    async def go():
        app = appmod.TaskApp(vault, theme="taskman-teal")
        async with app.run_test(notifications=True, size=(130, 34)) as pilot:
            await _select(pilot, app, "Anchor parent")
            await pilot.press("alt+3", "tab")
            kids = app.query_one("#ins-kids", OptionList)
            assert kids.has_focus
            assert app._selected().description == "Child target"
            await pilot.press("ctrl+k")
            assert isinstance(app.screen, CommandScreen)
            assert app.screen.context == "Child target"
            await _type(pilot, "Edit task" if action == "edit" else "Change due date")
            await pilot.press("enter")
            if action == "edit":
                assert isinstance(app.screen, appmod.EditScreen)
                assert app.screen.query_one(Input).value == "Child target"
                await pilot.press("ctrl+shift+a")
                await _type(pilot, "Child upgraded")
                await pilot.press("enter")
            else:
                assert isinstance(app.screen, appmod.DueScreen)
                await _type(pilot, "tomorrow")
                await pilot.press("enter")
            tasks = {task.description: task for task in app.store.tasks}
            assert tasks["Anchor parent"].due == dt.date.today()
            assert tasks["Sibling safe"].due is None
            if action == "edit":
                assert "Child upgraded" in tasks and "Child target" not in tasks
            else:
                assert tasks["Child target"].due == dt.date.today() + dt.timedelta(days=1)
    asyncio.run(go())


@pytest.mark.parametrize("action", ["complete", "delete"])
def test_branch_changes_restore_exact_bytes_with_undo_and_redo(vault, action):
    async def go():
        app = appmod.TaskApp(vault, theme="taskman-teal")
        before = _files(vault)
        async with app.run_test(notifications=True, size=(100, 30)) as pilot:
            await _select(pilot, app, "Anchor parent")
            if action == "complete":
                await pilot.press("space")
                assert all(task.done for task in app.store.tasks if task.description in {
                    "Anchor parent", "Child target", "Sibling safe"
                })
            else:
                await pilot.press("delete")
                assert isinstance(app.screen, appmod.ConfirmScreen)
                await pilot.press("shift+tab", "enter")
                assert not any(task.description == "Anchor parent" for task in app.store.tasks)
            changed = _files(vault)
            assert changed != before
            assert app.history.can_undo
            await pilot.press("u")
            assert _files(vault) == before
            assert app.query_one(appmod.TaskList).current.description == "Anchor parent"
            assert app.query_one(appmod.TaskList).has_focus
            await pilot.press("ctrl+y")
            assert _files(vault) == changed
            await pilot.press("ctrl+z")
            assert _files(vault) == before
    asyncio.run(go())


def test_add_in_a_new_project_can_remove_and_recreate_the_file(vault):
    async def go():
        app = appmod.TaskApp(vault, theme="taskman-teal")
        before = _files(vault)
        project_file = vault / "Projects/Brand-New.md"
        async with app.run_test(notifications=True, size=(100, 30)) as pilot:
            await pilot.press("a")
            await _type(pilot, "Launch the new work")
            await pilot.press("tab")
            assert app.screen.query_one("#proj", Input).has_focus
            await _type(pilot, "Brand New")
            await pilot.press("enter")
            assert project_file.exists()
            assert app.query_one(appmod.TaskList).current.description == "Launch the new work"
            changed = _files(vault)
            await pilot.press("u")
            assert not project_file.exists()
            assert _files(vault) == before
            await pilot.press("ctrl+y")
            assert _files(vault) == changed
    asyncio.run(go())


def test_note_edit_undo_redo_and_typing_u_stay_in_the_editor(vault):
    async def go():
        app = appmod.TaskApp(vault, theme="taskman-teal")
        before = _files(vault)
        async with app.run_test(notifications=True, size=(100, 30)) as pilot:
            await _select(pilot, app, "Capture idea")
            await pilot.press("n")
            await _type(pilot, "Useful follow up")
            await pilot.press("ctrl+s")
            changed = _files(vault)
            assert changed != before and app.history.undo_label == "Edit note"
            await pilot.press("f")
            await _type(pilot, "u")
            assert app.search_query == "u" and _files(vault) == changed
            await pilot.press("escape", "e")
            assert isinstance(app.screen, appmod.EditScreen)
            await _type(pilot, "u")
            assert app.screen.query_one(Input).value.endswith("u")
            assert _files(vault) == changed and app.history.undo_label == "Edit note"
            await pilot.press("escape", "n")
            await _type(pilot, "u")
            assert app.screen.query_one(TextArea).text.endswith("u")
            assert _files(vault) == changed
            await pilot.press("escape")
            assert isinstance(app.screen, appmod.ConfirmScreen)
            await pilot.press("shift+tab", "enter")
            await pilot.press("u")
            assert _files(vault) == before
            await pilot.press("ctrl+y")
            assert _files(vault) == changed
    asyncio.run(go())


@pytest.mark.parametrize("restore", ["undo", "redo"])
def test_conflicting_external_project_edit_blocks_all_restore_writes(vault, restore):
    async def go():
        app = appmod.TaskApp(vault, theme="taskman-teal")
        notices = []
        app.notify = lambda message, **kwargs: notices.append((str(message), kwargs))
        async with app.run_test(notifications=True, size=(100, 30)) as pilot:
            await _select(pilot, app, "Capture idea")
            await pilot.press("j")
            await _type(pilot, "Fresh Project")
            await pilot.press("enter")
            assert app.history.undo_label == "Change project"
            project_file = vault / "Projects/Fresh-Project.md"
            assert project_file.exists()
            if restore == "redo":
                await pilot.press("u")
                assert not project_file.exists()
                project_file.write_bytes(b"External project file survives.\n")
            else:
                project_file.write_bytes(project_file.read_bytes() + b"\nExternal edit survives.\n")
            externally_modified = _files(vault)
            await pilot.press("u" if restore == "undo" else "ctrl+y")
            assert _files(vault) == externally_modified
            assert app.history.can_undo is (restore == "undo")
            assert app.history.can_redo is (restore == "redo")
            assert any(
                "changed outside" in message
                and options.get("title") == "Could not restore change"
                and options.get("severity") == "warning"
                for message, options in notices
            )
    asyncio.run(go())


def test_failed_edit_notifies_without_crashing_or_changing_files(vault, monkeypatch):
    def denied(*_args, **_kwargs):
        raise PermissionError("Cannot write the read-only task file")

    monkeypatch.setattr(appmod.tm, "edit_text", denied)

    async def go():
        app = appmod.TaskApp(vault, theme="taskman-teal")
        notices = []
        app.notify = lambda message, **kwargs: notices.append((str(message), kwargs))
        before = _files(vault)
        async with app.run_test(notifications=True, size=(100, 30)) as pilot:
            await _select(pilot, app, "Capture idea")
            await pilot.press("e", "ctrl+shift+a")
            await _type(pilot, "Attempted change")
            await pilot.press("enter")
            assert app.is_running
            assert _files(vault) == before
            assert not app.history.can_undo
            assert any(
                "read-only task file" in message
                and options.get("title") == "Could not save change"
                and options.get("severity") == "error"
                for message, options in notices
            )
            await pilot.press("ctrl+k")
            assert isinstance(app.screen, CommandScreen)
    asyncio.run(go())


def test_read_details_from_child_palette_keeps_child_as_note_target(vault):
    async def go():
        app = appmod.TaskApp(vault, theme="taskman-teal")
        async with app.run_test(notifications=True, size=(130, 34)) as pilot:
            await _select(pilot, app, "Anchor parent")
            await pilot.press("alt+3", "tab")
            assert app.query_one("#ins-kids", OptionList).has_focus
            assert app._selected().description == "Child target"
            await _command(pilot, app, "Read task details")
            inspector = app.query_one(appmod.Inspector)
            assert inspector.has_focus
            assert "Child target" in str(inspector.query_one("#ins-title").render())
            assert app._selected().description == "Child target"
            await pilot.press("n")
            assert isinstance(app.screen, appmod.NoteScreen)
            assert "Child target" in app.screen.query_one("#dlg").border_title
            await _type(pilot, "Child-only context")
            await pilot.press("ctrl+s")
            tasks = {task.description: task for task in app.store.tasks}
            assert tasks["Child target"].note == "Child-only context"
            assert tasks["Anchor parent"].note == ""
            assert tasks["Sibling safe"].note == ""
    asyncio.run(go())


@pytest.mark.parametrize("restore", ["undo", "redo"])
def test_search_undo_keys_cannot_restore_task_history(vault, restore):
    async def go():
        app = appmod.TaskApp(vault, theme="taskman-teal")
        async with app.run_test(notifications=True, size=(100, 30)) as pilot:
            await _select(pilot, app, "Capture idea")
            await pilot.press("space")
            assert app.history.can_undo
            if restore == "redo":
                await pilot.press("u")
                assert app.history.can_redo
            unchanged = _files(vault)
            history_state = (app.history.undo_label, app.history.redo_label)
            await pilot.press("f")
            await _type(pilot, "anchor")
            await pilot.press("ctrl+z" if restore == "undo" else "ctrl+y")
            assert _files(vault) == unchanged
            assert (app.history.undo_label, app.history.redo_label) == history_state
            assert app.query_one("#search", Input).has_focus
            assert app.search_query == "anchor"
    asyncio.run(go())
