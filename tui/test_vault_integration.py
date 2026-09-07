"""Real Textual vault lifecycle checks, with notifications and isolated folders."""

import asyncio
from pathlib import Path
import threading

import pytest
from textual.widgets import Input, TextArea
from textual.widgets._toast import Toast

from tui import app as appmod, settings, taskman as tm
from tui.vault_screen import VaultChoice, VaultScreen


@pytest.fixture
def two_vaults(tmp_path, monkeypatch):
    monkeypatch.delenv("TASKMAN_THEME", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    first, second = tmp_path / "First vault", tmp_path / "Second Résumé"
    for root, text in ((first, "- [ ] Alpha task\n  Old context.\n- [ ] Second anchor\n"),
                       (second, "- [ ] Different vault task\n  New context.\n")):
        (root / "Tasks").mkdir(parents=True)
        (root / "Tasks" / "Inbox.md").write_text(text, encoding="utf-8")
    return first, second


async def _settle(app, pilot):
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause(0.15)


async def _choose(app, pilot, choice):
    await pilot.press("ctrl+o")
    assert isinstance(app.screen, VaultScreen)
    app.screen.dismiss(choice)
    await _settle(app, pilot)


def _files(root: Path):
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*") if path.is_file()}


def test_first_launch_shows_welcome_without_scanning_or_mutating_cwd(tmp_path, monkeypatch):
    workspace = tmp_path / "Unselected folder"
    workspace.mkdir()
    (workspace / "Private.md").write_text("- [ ] Never implicitly load this\n", encoding="utf-8")
    before = _files(workspace)
    monkeypatch.chdir(workspace)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: workspace))
    scans = []
    original = tm.Store.refresh

    def observed(store, *args, **kwargs):
        scans.append(store.root)
        return original(store, *args, **kwargs)

    monkeypatch.setattr(tm.Store, "refresh", observed)

    async def go():
        app = appmod.TaskApp(theme="taskman-teal")
        async with app.run_test(notifications=True, size=(110, 34)) as pilot:
            await _settle(app, pilot)
            assert isinstance(app.screen, VaultScreen) and app.screen.welcome
            assert not app._vault_ready and app.store.tasks == [] and scans == []
            # Navigation keys typed in the chooser are never global task actions.
            await pilot.press("a", "space", "u", "ctrl+y")
            assert scans == [] and not app.history.can_undo
            assert _files(workspace) == before
            await pilot.press("escape")
        assert app.return_code == 0
        assert not (settings.config_dir() / "settings.json").exists()
    asyncio.run(go())
    assert _files(workspace) == before


def test_existing_arbitrary_folder_opens_without_initialization(two_vaults, tmp_path):
    first, _ = two_vaults
    other = tmp_path / "Ordinary notes"
    note = other / "Clients" / "Meeting.md"
    note.parent.mkdir(parents=True)
    note.write_text("- [ ] Follow up with client\n", encoding="utf-8")
    before = _files(other)

    async def go():
        app = appmod.TaskApp(first, theme="taskman-teal")
        async with app.run_test(notifications=True, size=(110, 34)) as pilot:
            await _choose(app, pilot, VaultChoice(other, initialize=False))
            assert app.vault == other and app._vault_ready
            assert app.view == appmod.DEFAULT_VIEW == "now"
            assert app.query_one(appmod.TaskList).current is None  # An undated task is outside Now.
            assert [task.description for task in app.store.tasks] == ["Follow up with client"]
            await pilot.press("1")
            assert app.query_one(appmod.TaskList).current.description == "Follow up with client"
            assert app.query_one(appmod.TaskList).has_focus
            assert settings.last_vault() == other
            assert _files(other) == before and not (other / ".taskman").exists()
            assert any("Opened Ordinary notes" in toast.render().plain
                       for toast in app.screen.query(Toast))
    asyncio.run(go())


def test_choice_can_initialize_a_missing_vault_then_add_with_keyboard(two_vaults, tmp_path):
    first, _ = two_vaults
    fresh = tmp_path / "New parent" / "My new vault"
    original = _files(first)

    async def go():
        app = appmod.TaskApp(first, theme="taskman-teal")
        async with app.run_test(notifications=True, size=(110, 34)) as pilot:
            await _choose(app, pilot, VaultChoice(fresh, initialize=True))
            assert app.vault == fresh and app.store.tasks == []
            assert (fresh / ".taskman" / "vault.json").is_file()
            assert (fresh / "Tasks" / "Inbox.md").is_file()
            await pilot.press("a")
            assert isinstance(app.screen, appmod.AddScreen)
            app.screen.query_one("#text", Input).value = "First task in my new vault"
            await pilot.press("enter")
            await pilot.pause(0.15)
            assert app.query_one(appmod.TaskList).current.description == "First task in my new vault"
            assert "First task in my new vault" in (fresh / "Tasks" / "Inbox.md").read_text(encoding="utf-8")
            assert _files(first) == original
    asyncio.run(go())


def test_switch_resets_search_inspector_and_undo_targets(two_vaults):
    first, second = two_vaults

    async def go():
        app = appmod.TaskApp(first, theme="taskman-teal")
        async with app.run_test(notifications=True, size=(130, 36)) as pilot:
            await pilot.press("1", "space")  # Complete an undated task from All open.
            assert app.history.can_undo
            old_history = app.history
            saved_first = _files(first)
            await pilot.press("/")
            app.query_one("#search", Input).value = "Second anchor"
            await pilot.press("down", "right")
            await pilot.pause()
            assert app.search_query == "Second anchor"
            assert app.query_one(appmod.Inspector).display
            await _choose(app, pilot, VaultChoice(second))
            assert app.vault == second and app.store.root == second
            assert app.history is not old_history and not app.history.can_undo and not app.history.can_redo
            assert app.search_query == app.query_one("#search", Input).value == ""
            assert app.view == appmod.DEFAULT_VIEW and app.project == ""
            assert not app.query_one(appmod.Inspector).display
            assert app._command_target is None
            assert app.query_one(appmod.TaskList).current is None  # The new vault also starts in Now.
            await pilot.press("1")
            assert app.query_one(appmod.TaskList).current.description == "Different vault task"
            assert app._inspected_task is None or app._inspected_task in app.store.tasks
            before_second = _files(second)
            await pilot.press("u", "ctrl+y")
            assert _files(first) == saved_first and _files(second) == before_second
            # First-row ids collide across these folders. A new action must
            # still target only the currently open folder.
            await pilot.press("1", "space")  # Complete an undated task from All open.
            assert tm.load_all(second)[0].done
            assert _files(first) == saved_first
    asyncio.run(go())


def test_failed_scan_retains_active_vault_history_and_selection(two_vaults, monkeypatch):
    first, second = two_vaults
    original = tm.Store.refresh

    def unavailable(store, *args, **kwargs):
        if store.root == second:
            raise PermissionError("Folder [notes] became unavailable")
        return original(store, *args, **kwargs)

    monkeypatch.setattr(tm.Store, "refresh", unavailable)

    async def go():
        app = appmod.TaskApp(first, theme="taskman-teal")
        async with app.run_test(notifications=True, size=(110, 34)) as pilot:
            await pilot.press("1", "space")  # Complete an undated task from All open.
            old_store, old_history = app.store, app.history
            selected = app.query_one(appmod.TaskList).current.id
            saved = _files(first)
            await _choose(app, pilot, VaultChoice(second))
            assert app.vault == first and app.store is old_store and app.history is old_history
            assert app.history.can_undo and not app._opening_vault
            assert app.query_one(appmod.TaskList).current.id == selected
            assert _files(first) == saved and settings.last_vault() == first
            assert any("Folder [notes] became unavailable" in toast.render().plain
                       for toast in app.screen.query(Toast))
            assert app.is_running
    asyncio.run(go())


def test_open_vault_refuses_to_leave_dirty_note_editor(two_vaults):
    first, _ = two_vaults
    before = _files(first)

    async def go():
        app = appmod.TaskApp(first, theme="taskman-teal")
        async with app.run_test(notifications=True, size=(110, 34)) as pilot:
            await pilot.press("1", "n")  # The note belongs to an undated task.
            screen = app.screen
            assert isinstance(screen, appmod.NoteScreen)
            area = screen.query_one("#note-text", TextArea)
            area.load_text("My unsaved note must stay here")
            await pilot.press("ctrl+o")
            await pilot.pause(0.15)
            assert app.screen is screen and area.text == "My unsaved note must stay here"
            assert app.vault == first and _files(first) == before
            assert any("Finish or cancel this dialog" in toast.render().plain
                       for toast in app.screen.query(Toast))
            await pilot.press("ctrl+s")
            await pilot.pause()
            assert "My unsaved note must stay here" in (first / "Tasks" / "Inbox.md").read_text(encoding="utf-8")
    asyncio.run(go())


def test_cancelled_background_open_ignores_late_result(two_vaults, monkeypatch):
    first, second = two_vaults
    began, release = threading.Event(), threading.Event()
    original = tm.Store.refresh

    def slow(store, *args, **kwargs):
        if store.root == second:
            began.set()
            if not release.wait(8):
                raise TimeoutError("Test did not release the folder scan")
        return original(store, *args, **kwargs)

    monkeypatch.setattr(tm.Store, "refresh", slow)

    async def go():
        app = appmod.TaskApp(first, theme="taskman-teal")
        async with app.run_test(notifications=True, size=(110, 34)) as pilot:
            before = _files(first)
            old_store, old_history = app.store, app.history
            await pilot.press("ctrl+o")
            assert isinstance(app.screen, VaultScreen)
            app.screen.dismiss(VaultChoice(second))
            await pilot.pause()
            try:
                assert await asyncio.to_thread(began.wait, 3)
                assert isinstance(app.screen, appmod.OpeningVaultScreen)
                await pilot.press("escape")
                assert not app._opening_vault and app.vault == first
            finally:
                release.set()
            await _settle(app, pilot)
            assert app.vault == first and app.store is old_store and app.history is old_history
            assert not isinstance(app.screen, (VaultScreen, appmod.OpeningVaultScreen))
            assert settings.last_vault() == first and _files(first) == before
            assert app.is_running
    asyncio.run(go())


def test_cancelling_picker_keeps_current_folder_and_history(two_vaults):
    first, _ = two_vaults

    async def go():
        app = appmod.TaskApp(first, theme="taskman-teal")
        async with app.run_test(notifications=True, size=(110, 34)) as pilot:
            await pilot.press("1", "space")  # Complete an undated task from All open.
            before, old_history = _files(first), app.history
            await pilot.press("ctrl+o", "escape")
            await pilot.pause()
            assert app.vault == first and app.history is old_history and app.history.can_undo
            assert _files(first) == before and app.query_one(appmod.TaskList).has_focus
    asyncio.run(go())
