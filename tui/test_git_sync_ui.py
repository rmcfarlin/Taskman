"""Git publishing stays explicit, asynchronous, and outside editor saves."""
import asyncio
import threading
from types import SimpleNamespace

import pytest
from textual.screen import ModalScreen
from textual.widgets import Input, TextArea
from textual.widgets._toast import Toast

from tui import app as appmod


@pytest.fixture
def vault(tmp_path):
    root = tmp_path / "Vault"
    (root / "Tasks").mkdir(parents=True)
    (root / "Tasks/Inbox.md").write_text("- [ ] Review contract\n", encoding="utf-8")
    return root


async def settled(app, pilot):
    await app.workers.wait_for_complete()
    await pilot.pause()


def notices(app):
    return "\n".join(toast.render().plain for toast in app.screen.query(Toast))


@pytest.mark.parametrize("entry", ["key", "palette", "notes_palette", "search"])
def test_explicit_push_dispatches_current_vault_in_worker(vault, monkeypatch, entry):
    calls = []
    main_thread = threading.get_ident()

    def push(root):
        calls.append((root, threading.get_ident()))
        return SimpleNamespace(message="Pushed vault snapshot to origin/main")

    monkeypatch.setattr(appmod.git_sync, "push_vault", push)

    async def go():
        app = appmod.TaskApp(vault)
        async with app.run_test(size=(110, 34), notifications=True) as pilot:
            await pilot.press("ctrl+s")
            assert calls == []
            if entry == "search":
                await pilot.press("/")
                app.query_one("#search", Input).value = "contract"
            if entry.endswith("palette"):
                if entry == "notes_palette":
                    await pilot.press("8")
                await pilot.press("ctrl+k", *"push vault", "enter")
            else:
                await pilot.press("ctrl+shift+s")
            await settled(app, pilot)
            assert len(calls) == 1 and calls[0][0] == vault
            assert calls[0][1] != main_thread
            assert "Pushed vault snapshot" in notices(app)
            assert not app._pushing_vault
            if entry == "search":
                assert app.query_one("#search", Input).value == "contract"

    asyncio.run(go())


def test_busy_push_blocks_duplicates_and_vault_switch_but_allows_navigation(vault, monkeypatch):
    began, release = threading.Event(), threading.Event()
    calls = []

    def push(root):
        calls.append(root)
        began.set()
        assert release.wait(10)
        return SimpleNamespace(message="Pushed vault snapshot")

    monkeypatch.setattr(appmod.git_sync, "push_vault", push)

    async def go():
        app = appmod.TaskApp(vault)
        async with app.run_test(size=(110, 34), notifications=True) as pilot:
            try:
                await pilot.press("ctrl+shift+s")
                assert await asyncio.to_thread(began.wait, 3)
                await pilot.press("ctrl+shift+s", "ctrl+o", "8")
                assert app.view == "notes"
                assert not isinstance(app.screen, ModalScreen)
                assert app.vault == vault and calls == [vault]
                status = app.query_one("#status-info")
                assert status.display and status.has_class("-message")
                assert "already running" in status.render_line(0).text
                assert "before opening another vault" in notices(app)
            finally:
                release.set()
                await settled(app, pilot)
            assert not app._pushing_vault
            assert "Pushed vault snapshot" in notices(app)

    asyncio.run(go())


def test_failed_push_reports_retained_commit_and_can_retry(vault, monkeypatch):
    calls = []

    def push(root):
        calls.append(root)
        if len(calls) == 1:
            raise ValueError("Push rejected; local commit retained. Resolve and retry.")
        return SimpleNamespace(message="Pushed vault snapshot")

    monkeypatch.setattr(appmod.git_sync, "push_vault", push)

    async def go():
        app = appmod.TaskApp(vault)
        async with app.run_test(size=(110, 34), notifications=True) as pilot:
            await pilot.press("ctrl+shift+s")
            await settled(app, pilot)
            assert "local commit retained" in notices(app)
            assert not app._pushing_vault
            await pilot.press("ctrl+shift+s")
            await settled(app, pilot)
            assert calls == [vault, vault]
            assert "Pushed vault snapshot" in notices(app)

    asyncio.run(go())


@pytest.mark.parametrize("editor", ["task_note", "dates", "reference_note"])
def test_shift_save_in_editor_saves_draft_without_publishing(vault, monkeypatch, editor):
    def unexpected(root):
        pytest.fail("Editor save must never push a vault")

    monkeypatch.setattr(appmod.git_sync, "push_vault", unexpected)

    async def go():
        app = appmod.TaskApp(vault)
        async with app.run_test(size=(110, 34), notifications=True) as pilot:
            await pilot.press("1")
            if editor == "task_note":
                await pilot.press("n")
                app.screen.query_one("#note-text", TextArea).load_text("Saved draft")
            elif editor == "dates":
                await pilot.press("d")
                app.screen.query_one("#scheduled", Input).value = "today"
            else:
                await pilot.press("8", "a")
                app.screen.query_one("#note-title", Input).value = "Saved reference"
            await pilot.press("ctrl+shift+s")
            await settled(app, pilot)
            assert not isinstance(app.screen, ModalScreen)
            assert not app._pushing_vault
            if editor == "task_note":
                assert "Saved draft" in (vault / "Tasks/Inbox.md").read_text(encoding="utf-8")
            elif editor == "dates":
                assert app.store.tasks[0].scheduled is not None
            else:
                assert (vault / "Notes/Saved reference.md").is_file()

    asyncio.run(go())


def test_direct_push_during_welcome_does_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(appmod.git_sync, "push_vault", lambda root: pytest.fail("No vault selected"))

    async def go():
        app = appmod.TaskApp()
        async with app.run_test(size=(110, 34)) as pilot:
            app.action_push_vault()
            await pilot.press("ctrl+shift+s")
            assert not app._pushing_vault
            assert not (tmp_path / ".git").exists()
            assert not (tmp_path / ".taskman").exists()

    asyncio.run(go())
