"""Background discovery respects drafts, consent, and manual update checks."""
import threading
import time
from dataclasses import replace

import pytest
from textual.widgets import Input, TextArea

from tui import __version__, app as appmod, updater
from tui.update_actions import UpdateActions
from tui.update_ui import UpdateScreen


@pytest.fixture
def release():
    return updater.Release("999.0.0", "A useful improvement.",
        "https://github.com/rmcfarlin/Taskman/releases/tag/v999.0.0",
        "taskman-999.0.0-windows-x64.zip", "https://github.com/example.zip",
        "a" * 64, "https://github.com/SHA256SUMS.txt", 100, "b" * 64)


@pytest.fixture
def app(tmp_path, monkeypatch):
    (tmp_path / "Tasks").mkdir()
    (tmp_path / "Tasks/Inbox.md").write_text("- [ ] Keep this task\n", encoding="utf-8")
    monkeypatch.setattr(updater, "read_update_result", lambda **kwargs: None)
    monkeypatch.setattr(updater, "installation_support",
                        lambda: updater.InstallSupport(True, "", tmp_path / "app"))
    return appmod.TaskApp(tmp_path)


async def finish_check(app, pilot):
    await app.workers.wait_for_complete()
    await pilot.pause()


def test_startup_registers_six_hour_checks_once_and_skips_headless():
    class Harness(UpdateActions):
        is_headless = True

        def __init__(self):
            self._init_update_state()
            self.intervals = []
            self.checks = 0

        def set_interval(self, interval, callback):
            self.intervals.append((interval, callback))

        def _check_automatic_updates(self):
            self.checks += 1

    app = Harness()
    app._start_automatic_updates()
    assert app.intervals == []
    assert app.checks == 0
    app.is_headless = False
    app._start_automatic_updates()
    app._start_automatic_updates()
    assert app.checks == 1
    assert app._automatic_updates_started_at > 0
    assert [interval for interval, _ in app.intervals] == [6 * 60 * 60, 2]


@pytest.mark.asyncio
async def test_run_test_never_performs_implicit_network_check(app, monkeypatch):
    calls = []
    monkeypatch.setattr(updater, "check_for_update", lambda version: calls.append(version))
    async with app.run_test(size=(90, 26)) as pilot:
        await finish_check(app, pilot)
        assert calls == []
        assert not app._automatic_updates_started
        assert not isinstance(app.screen, UpdateScreen)


@pytest.mark.asyncio
@pytest.mark.parametrize("offline", [False, True])
async def test_current_or_offline_discovery_is_quiet(app, monkeypatch, offline):
    calls, notifications = [], []

    def check(version):
        calls.append(version)
        if offline:
            raise updater.UpdateError("Network unavailable")
        return None

    monkeypatch.setattr(updater, "check_for_update", check)
    async with app.run_test(size=(90, 26)) as pilot:
        monkeypatch.setattr(app, "notify", lambda *args, **kwargs: notifications.append(args))
        app._check_automatic_updates()
        await finish_check(app, pilot)
        assert calls == [__version__]
        assert notifications == []
        assert app._pending_update is None
        assert not app._automatic_update_inflight
        assert not isinstance(app.screen, UpdateScreen)


@pytest.mark.asyncio
async def test_automatic_offer_waits_fifteen_seconds_manual_check_does_not(app, release):
    support = updater.InstallSupport(True, "", app.vault / "app")
    async with app.run_test(size=(90, 26)) as pilot:
        app._automatic_updates_started = True
        app._automatic_updates_started_at = time.monotonic()
        app._pending_update = (release, support)
        app._offer_pending_update()
        await pilot.pause()
        assert not isinstance(app.screen, UpdateScreen)
        app._automatic_updates_started_at = time.monotonic() - 14.9
        app._offer_pending_update()
        await pilot.pause()
        assert not isinstance(app.screen, UpdateScreen)
        app.action_check_updates()
        await pilot.pause()
        assert isinstance(app.screen, UpdateScreen)
        assert app.screen.release == release
        await pilot.press("escape")


@pytest.mark.asyncio
async def test_automatic_offer_proceeds_after_fifteen_seconds(app, release):
    support = updater.InstallSupport(True, "", app.vault / "app")
    async with app.run_test(size=(90, 26)) as pilot:
        app._automatic_updates_started = True
        app._automatic_updates_started_at = time.monotonic() - 15.0
        app._pending_update = (release, support)
        app._offer_pending_update()
        await pilot.pause()
        assert isinstance(app.screen, UpdateScreen)
        assert app.screen.release == release
        await pilot.press("escape")


@pytest.mark.asyncio
async def test_new_version_is_offered_once_until_user_checks_again(app, monkeypatch, release):
    calls, preparations = [], []
    monkeypatch.setattr(updater, "check_for_update", lambda version: calls.append(version) or release)
    monkeypatch.setattr(updater, "prepare_update", lambda *args, **kwargs: preparations.append(args))
    async with app.run_test(size=(90, 26)) as pilot:
        app._check_automatic_updates()
        await finish_check(app, pilot)
        assert isinstance(app.screen, UpdateScreen)
        assert app.screen.release == release
        assert calls == [__version__]
        assert preparations == []  # Offering an update grants no install consent.
        await pilot.press("escape")
        app._check_automatic_updates()
        await finish_check(app, pilot)
        assert not isinstance(app.screen, UpdateScreen)
        assert len(calls) == 2
        app.action_check_updates()
        await finish_check(app, pilot)
        assert isinstance(app.screen, UpdateScreen)
        assert app.screen.release == release
        assert len(calls) == 3
        await pilot.press("escape")


@pytest.mark.asyncio
@pytest.mark.parametrize("busy", ["dialog", "input", "textarea", "opening", "pushing"])
async def test_available_update_waits_until_edits_and_vault_operations_finish(
        app, monkeypatch, release, busy):
    calls = []
    monkeypatch.setattr(updater, "check_for_update", lambda version: calls.append(version) or release)
    async with app.run_test(size=(90, 26)) as pilot:
        if busy == "dialog":
            app.push_screen(appmod.HelpScreen())
        elif busy == "input":
            app.action_focus_search()
        elif busy == "textarea":
            editor = TextArea("Unsaved draft", id="update-test-draft")
            await app.screen.mount(editor)
            editor.focus()
        else:
            setattr(app, "_opening_vault" if busy == "opening" else "_pushing_vault", True)
        await pilot.pause()
        if busy in {"input", "textarea"}:
            assert isinstance(app.focused, (Input, TextArea))
        app._check_automatic_updates()
        await finish_check(app, pilot)
        assert not isinstance(app.screen, UpdateScreen)
        assert app._pending_update[0] == release
        assert calls == [__version__]
        if busy == "dialog":
            await pilot.press("escape")
        elif busy == "textarea":
            assert editor.text == "Unsaved draft"
            await editor.remove()
            app.query_one(appmod.TaskList).focus()
        elif busy == "input":
            app.query_one(appmod.TaskList).focus()
        else:
            setattr(app, "_opening_vault" if busy == "opening" else "_pushing_vault", False)
        await pilot.pause()
        app._offer_pending_update()
        await pilot.pause()
        assert isinstance(app.screen, UpdateScreen)
        assert calls == [__version__]
        await pilot.press("escape")


@pytest.mark.asyncio
async def test_manual_check_reuses_pending_release_without_second_request(app, monkeypatch, release):
    calls = []
    monkeypatch.setattr(updater, "check_for_update", lambda version: calls.append(version) or release)
    async with app.run_test(size=(90, 26)) as pilot:
        app.action_focus_search()
        await pilot.pause()
        app._check_automatic_updates()
        await finish_check(app, pilot)
        assert app._pending_update[0] == release
        app.action_check_updates()
        await finish_check(app, pilot)
        assert isinstance(app.screen, UpdateScreen)
        assert app.screen.release == release
        assert app._pending_update is None
        assert calls == [__version__]
        await pilot.press("escape")
        app.query_one(appmod.TaskList).focus()
        app._offer_pending_update()
        await pilot.pause()
        assert not isinstance(app.screen, UpdateScreen)


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["available", "current", "offline", "closed"])
async def test_manual_check_adopts_inflight_check_without_duplicate_or_reopened_dialog(
        app, monkeypatch, release, outcome):
    started, finish = threading.Event(), threading.Event()
    calls = []

    def check(version):
        calls.append(version)
        started.set()
        if not finish.wait(10):
            raise AssertionError("The test did not release its update check")
        if outcome == "offline":
            raise updater.UpdateError("No connection")
        return None if outcome == "current" else release

    monkeypatch.setattr(updater, "check_for_update", check)
    async with app.run_test(size=(90, 26)) as pilot:
        app._check_automatic_updates()
        for _ in range(100):
            if started.is_set():
                break
            await pilot.pause(0.01)
        assert started.is_set()
        try:
            app._check_automatic_updates()  # A periodic tick does not duplicate it.
            app.action_check_updates()
            await pilot.pause()
            assert isinstance(app.screen, UpdateScreen)
            screen = app.screen
            assert screen._busy
            app._check_automatic_updates()
            if outcome == "closed":
                await pilot.press("escape")
        finally:
            finish.set()
        await finish_check(app, pilot)
        assert calls == [__version__]
        if outcome == "closed":
            assert not isinstance(app.screen, UpdateScreen)
            app._offer_pending_update()
            assert release.version in app._offered_update_versions
        else:
            assert app.screen is screen
            expected = {"available": "is available", "current": "No newer stable", "offline": "Could not check"}
            assert expected[outcome] in str(screen.query_one("#update-status").content)
            assert not screen._busy
            await pilot.press("escape")


@pytest.mark.asyncio
async def test_manual_offer_is_not_repeated_by_later_automatic_check(app, monkeypatch, release):
    monkeypatch.setattr(updater, "check_for_update", lambda version: release)
    async with app.run_test(size=(90, 26)) as pilot:
        app.action_check_updates()
        await finish_check(app, pilot)
        assert isinstance(app.screen, UpdateScreen)
        await pilot.press("escape")
        assert release.version in app._offered_update_versions
        app._check_automatic_updates()
        await finish_check(app, pilot)
        assert not isinstance(app.screen, UpdateScreen)


@pytest.mark.asyncio
async def test_result_arriving_during_manual_dialog_mount_is_not_lost(app, release):
    async with app.run_test(size=(90, 26)) as pilot:
        app._automatic_update_inflight = True
        app._automatic_update_generation = 1
        app.action_check_updates()
        app._automatic_update_checked(1, release, updater.InstallSupport(True, ""))
        await pilot.pause()
        assert isinstance(app.screen, UpdateScreen)
        assert app.screen.release == release
        assert not app.screen._busy
        await pilot.press("escape")


@pytest.mark.asyncio
async def test_later_suppresses_only_that_version(app, monkeypatch, release):
    releases = iter((release, replace(release, version="999.1.0")))
    monkeypatch.setattr(updater, "check_for_update", lambda _version: next(releases))
    async with app.run_test(size=(90, 26)) as pilot:
        app._check_automatic_updates()
        await finish_check(app, pilot)
        assert isinstance(app.screen, UpdateScreen)
        await pilot.press("escape")
        app._check_automatic_updates()
        await finish_check(app, pilot)
        assert isinstance(app.screen, UpdateScreen)
        assert app.screen.release.version == "999.1.0"
        await pilot.press("escape")
