"""Updates require acceptance, then install automatically with cancellation."""
import asyncio
import threading
from types import SimpleNamespace

import pytest
from textual.app import App
from textual.widgets import Button, Label

from tui import updater
from tui.update_ui import UpdateScreen


class UpdateApp(App):
    def __init__(self):
        super().__init__()
        self.installs = []

    def on_mount(self):
        self.push_screen(UpdateScreen(install_handler=self.installs.append))


def available():
    return SimpleNamespace(version="9.0.0", body="New features\n" * 30,
                           html_url="https://github.com/rmcfarlin/Taskman/releases/tag/v9.0.0")


async def settled(pilot, predicate):
    for _ in range(100):
        await pilot.pause(0.02)
        if predicate():
            return
    raise AssertionError("Update UI did not settle")


@pytest.fixture
def release(monkeypatch):
    value = available()
    monkeypatch.setattr(updater, "check_for_update", lambda *args: value)
    monkeypatch.setattr(updater, "installation_support", lambda: SimpleNamespace(supported=True, reason=""))
    return value


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(60, 20), (80, 24), (140, 38)])
async def test_one_acceptance_downloads_verifies_and_installs_with_visible_buttons(monkeypatch, release, size):
    prepared = SimpleNamespace(version="9.0.0")
    downloads, discarded = [], []
    monkeypatch.setattr(updater, "prepare_update", lambda chosen, **kwargs: downloads.append(chosen) or prepared)
    monkeypatch.setattr(updater, "discard_update", discarded.append)
    app = UpdateApp()
    async with app.run_test(size=size) as pilot:
        screen = app.screen
        await settled(pilot, lambda: screen._state == "available")
        assert downloads == [] and app.installs == []
        for key in ("update-install", "update-close", "update-release"):
            button = screen.query_one(f"#{key}", Button)
            assert 0 <= button.region.y < app.size.height
            assert button.region.bottom <= app.size.height
            assert 0 <= button.region.x and button.region.right <= app.size.width
        assert "Update now" in screen.query_one("#update-install", Button).render_line(0).text
        assert "Later" in screen.query_one("#update-close", Button).render_line(0).text
        await pilot.click("#update-install")
        await settled(pilot, lambda: bool(app.installs))
        assert downloads == [release]
        assert app.installs == [prepared] and discarded == []


@pytest.mark.asyncio
async def test_no_update_and_network_error_are_recoverable(monkeypatch):
    responses = [updater.UpdateError("Connection unavailable"), None]
    def check(*_args):
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response
    monkeypatch.setattr(updater, "check_for_update", check)
    app = UpdateApp()
    async with app.run_test(size=(60, 20)) as pilot:
        screen = app.screen
        await settled(pilot, lambda: screen._state == "check-failed")
        assert screen.query_one("#update-error", Label).display
        await pilot.click("#update-install")
        await settled(pilot, lambda: screen._state == "current")
        assert not screen.query_one("#update-error", Label).display
        assert not app.installs
        await pilot.press("escape")
        assert not isinstance(app.screen, UpdateScreen)


@pytest.mark.asyncio
async def test_source_install_explains_manual_route(monkeypatch, release):
    monkeypatch.setattr(updater, "installation_support", lambda: SimpleNamespace(
        supported=False, reason="Open GitHub and use the source package installer."))
    app = UpdateApp()
    async with app.run_test(size=(60, 20)) as pilot:
        screen = app.screen
        await settled(pilot, lambda: screen._state == "available")
        assert screen.query_one("#update-install", Button).disabled
        assert not screen.query_one("#update-release", Button).disabled
        assert "source package" in str(screen.query_one("#update-detail", Label).render())


@pytest.mark.asyncio
async def test_long_failure_keeps_retry_and_close_visible(monkeypatch):
    def fail(*_args):
        raise updater.UpdateError("Unable to verify this application folder. " * 40)
    monkeypatch.setattr(updater, "check_for_update", fail)
    app = UpdateApp()
    async with app.run_test(size=(60, 20)) as pilot:
        screen = app.screen
        await settled(pilot, lambda: screen._state == "check-failed")
        assert screen.query_one("#update-error", Label).region.width < app.size.width
        for key in ("update-install", "update-close"):
            button = screen.query_one(f"#{key}", Button)
            assert 0 <= button.region.y < button.region.bottom <= app.size.height
        await pilot.click("#update-close")
        assert not isinstance(app.screen, UpdateScreen)


@pytest.mark.asyncio
async def test_cancelled_download_that_returns_a_prepared_update_discards_it(monkeypatch, release):
    prepared = SimpleNamespace(version="9.0.0")
    entered, finish = threading.Event(), threading.Event()
    discarded = []
    def download(*_args, **_kwargs):
        entered.set()
        assert finish.wait(5)
        return prepared
    monkeypatch.setattr(updater, "prepare_update", download)
    monkeypatch.setattr(updater, "discard_update", discarded.append)
    app = UpdateApp()
    try:
        async with app.run_test(size=(80, 24)) as pilot:
            screen = app.screen
            await settled(pilot, lambda: screen._state == "available")
            await pilot.click("#update-install")
            await settled(pilot, entered.is_set)
            await pilot.press("escape")
            finish.set()
            await settled(pilot, lambda: bool(discarded))
            assert discarded == [prepared] and app.installs == []
    finally:
        finish.set()


@pytest.mark.asyncio
async def test_close_during_download_cannot_install_or_reopen_dialog(monkeypatch, release):
    entered, finish, cancelled = threading.Event(), threading.Event(), threading.Event()
    def download(_release, *, progress):
        entered.set()
        assert finish.wait(5)
        try:
            progress("Download complete")
        except updater.UpdateCancelled:
            cancelled.set()
            raise
        raise AssertionError("Closing the dialog must cancel its download")
    monkeypatch.setattr(updater, "prepare_update", download)
    app = UpdateApp()
    try:
        async with app.run_test(size=(80, 24)) as pilot:
            screen = app.screen
            await settled(pilot, lambda: screen._state == "available")
            await pilot.click("#update-install")
            await settled(pilot, entered.is_set)
            await pilot.press("escape")
            finish.set()
            await settled(pilot, cancelled.is_set)
            assert not isinstance(app.screen, UpdateScreen) and app.installs == []
    finally:
        finish.set()


@pytest.mark.asyncio
async def test_helper_launch_failure_keeps_app_open_and_offers_fresh_download(monkeypatch, release):
    prepared = SimpleNamespace(version="9.0.0")
    monkeypatch.setattr(updater, "prepare_update", lambda *_args, **_kwargs: prepared)
    monkeypatch.setattr(updater, "discard_update", lambda *_args: None)
    app = UpdateApp()
    async with app.run_test(size=(80, 24)) as pilot:
        screen = app.screen
        await settled(pilot, lambda: screen._state == "available")
        def fail(_prepared):
            raise OSError("App folder became unavailable")
        screen._install_handler = fail
        await pilot.click("#update-install")
        await settled(pilot, lambda: screen.query_one("#update-error", Label).display)
        assert app.screen is screen and screen.prepared is None
        assert screen._state == "available"
        assert str(screen.query_one("#update-install", Button).label) == "Update now"
        assert screen.query_one("#update-error", Label).display


@pytest.mark.asyncio
async def test_later_never_downloads_or_installs(monkeypatch, release):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Later must not download or install")
    monkeypatch.setattr(updater, "prepare_update", forbidden)
    app = UpdateApp()
    async with app.run_test(size=(60, 20)) as pilot:
        screen = app.screen
        await settled(pilot, lambda: screen._state == "available")
        assert screen.query_one("#update-close", Button).has_focus
        await pilot.press("enter")
        assert not isinstance(app.screen, UpdateScreen)
        assert app.installs == []


@pytest.mark.asyncio
async def test_async_installer_handoff_happens_once_and_blocks_late_cancel(monkeypatch, release):
    prepared = SimpleNamespace(version="9.0.0")
    finish = asyncio.Event()
    launches = []
    async def install(value):
        launches.append(value)
        await finish.wait()
    monkeypatch.setattr(updater, "prepare_update", lambda *_args, **_kwargs: prepared)
    app = UpdateApp()
    try:
        async with app.run_test(size=(60, 20)) as pilot:
            screen = app.screen
            screen._install_handler = install
            await settled(pilot, lambda: screen._state == "available")
            await pilot.click("#update-install")
            await settled(pilot, lambda: bool(launches))
            assert screen._state == "launching"
            assert screen.query_one("#update-close", Button).disabled
            await pilot.press("escape")
            assert app.screen is screen and launches == [prepared]
            finish.set()
            await settled(pilot, lambda: screen._update_closed)
    finally:
        finish.set()


@pytest.mark.asyncio
async def test_failed_verification_never_launches_installer(monkeypatch, release):
    def fail(*_args, **_kwargs):
        raise updater.UpdateError("Checksum verification failed")
    monkeypatch.setattr(updater, "prepare_update", fail)
    app = UpdateApp()
    async with app.run_test(size=(60, 20)) as pilot:
        screen = app.screen
        await settled(pilot, lambda: screen._state == "available")
        await pilot.click("#update-install")
        await settled(pilot, lambda: screen.query_one("#update-error", Label).display)
        assert not app.installs and screen._state == "available"
        assert not screen.query_one("#update-close", Button).disabled
