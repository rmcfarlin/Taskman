"""The updater is reachable from the real app, with no live network or install."""
import pytest

from tui import __version__, app as appmod, updater
from tui.notes import NotesStore
from tui.shortcut_bar import ShortcutBar
from tui.update_ui import UpdateScreen


@pytest.mark.asyncio
@pytest.mark.parametrize("view_key", ["1", "8"])
async def test_check_for_updates_is_reachable_through_tasks_and_notes_palette(tmp_path, monkeypatch, view_key):
    (tmp_path / "Tasks").mkdir()
    (tmp_path / "Tasks/Inbox.md").write_text("- [ ] Keep this task\n", encoding="utf-8")
    NotesStore(tmp_path).create("Keep this note", "Reference content")
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*.md")}
    calls = []
    monkeypatch.setattr(updater, "check_for_update", lambda version: calls.append(version) or None)
    monkeypatch.setattr(updater, "read_update_result", lambda **kwargs: None)
    app = appmod.TaskApp(tmp_path)
    async with app.run_test(size=(90, 26)) as pilot:
        await pilot.press(view_key, "ctrl+k")
        await pilot.press(*"Check for updates")
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert isinstance(app.screen, UpdateScreen)
        assert "No newer stable release" in str(app.screen.query_one("#update-status").content)
        assert calls == [__version__]
        assert not app.query_one(ShortcutBar).visible
        await pilot.click("#update-close")
        await pilot.pause()
        assert app.query_one(ShortcutBar).visible
        assert app.view == ("all" if view_key == "1" else "notes")
        after = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*.md")}
        assert after == before


@pytest.mark.asyncio
async def test_update_action_works_without_vault_but_blocks_during_dialog_and_push(tmp_path, monkeypatch):
    monkeypatch.setattr(appmod, "discover_vault", lambda _vault: None)
    monkeypatch.setattr(updater, "check_for_update", lambda _version: None)
    monkeypatch.setattr(updater, "read_update_result", lambda **kwargs: None)
    app = appmod.TaskApp()
    app._choose_on_start = False
    async with app.run_test(size=(80, 24)) as pilot:
        assert not app._vault_ready
        assert app.check_action("check_updates", ()) is True
        app._pushing_vault = True
        assert app.check_action("check_updates", ()) is False
        app.action_check_updates()
        assert not isinstance(app.screen, UpdateScreen)
        app._pushing_vault = False
        app._opening_vault = True
        assert app.check_action("check_updates", ()) is False
        app.action_check_updates()
        assert not isinstance(app.screen, UpdateScreen)
        app._opening_vault = False
        app.push_screen(appmod.HelpScreen())
        await pilot.pause()
        assert app.check_action("check_updates", ()) is False
        app.action_check_updates()
        assert isinstance(app.screen, appmod.HelpScreen)
        await pilot.press("escape")
        app.action_check_updates()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert isinstance(app.screen, UpdateScreen)
        assert "No newer stable release" in str(app.screen.query_one("#update-status").content)
        await pilot.press("escape")
