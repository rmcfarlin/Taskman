"""Exercise folder selection with real keyboard events and mounted widgets."""

import asyncio
from pathlib import Path
import threading

import pytest
from textual.app import App
from textual.widgets import Button, Input, OptionList, Static

from tui import vault_screen
from tui.vault_screen import VaultChoice, VaultScreen


class PickerApp(App):
    def __init__(self, folder, welcome=False):
        super().__init__()
        self.picker = VaultScreen(folder, welcome=welcome)
        self.choices = []

    def on_mount(self):
        self.push_screen(self.picker, self.choices.append)


async def settled(app, pilot):
    for _ in range(80):
        await pilot.pause(0.01)
        if not app.picker._busy:
            return
    pytest.fail("Folder picker did not finish loading")


def rendered(widget):
    return "\n".join(widget.render_line(y).text for y in range(widget.size.height))


def test_keyboard_browse_open_and_cancel_do_not_write(tmp_path, monkeypatch):
    monkeypatch.setattr(vault_screen, "recent_vaults", lambda: [])
    child = tmp_path / "Client plans Ω [2026]"
    child.mkdir()
    (child / "Personal.md").write_text("untouched", encoding="utf-8")

    async def go():
        app = PickerApp(tmp_path, welcome=True)
        async with app.run_test(size=(80, 24), notifications=True) as pilot:
            await settled(app, pilot)
            assert app.focused.id == "vault-path"
            assert "Welcome to Taskman" in rendered(app.picker.query_one("#vault-title"))
            await pilot.press("down", "end", "enter")
            await settled(app, pilot)
            assert app.picker._folder == child
            assert app.focused.id == "vault-folders"
            await pilot.press("ctrl+enter")
            assert app.choices == [VaultChoice(child, False)]
        app = PickerApp(child)
        async with app.run_test(notifications=True) as pilot:
            await settled(app, pilot)
            await pilot.press("escape")
            assert app.choices == [None]
    asyncio.run(go())
    assert sorted(path.name for path in child.iterdir()) == ["Personal.md"]
    assert (child / "Personal.md").read_text(encoding="utf-8") == "untouched"


def test_setup_returns_previewed_choice_without_creating_files(tmp_path):
    async def go():
        app = PickerApp(tmp_path)
        async with app.run_test(size=(120, 32), notifications=True) as pilot:
            await settled(app, pilot)
            preview = rendered(app.picker.query_one("#vault-preview"))
            assert "Tasks/Inbox.md" in preview and ".taskman/vault.json" in preview
            assert not app.picker.query_one("#vault-setup", Button).disabled
            await pilot.press("ctrl+n")
            assert app.choices == [VaultChoice(tmp_path, True)]
    asyncio.run(go())
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("value_kind", ["quoted", "environment", "tilde"])
def test_path_entry_expansion_and_literal_folder_names(tmp_path, monkeypatch, value_kind):
    destination = tmp_path / "Planning Ω [red]"
    destination.mkdir()
    monkeypatch.setenv("TASKMAN_PICKER_TEST_PATH", str(destination))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    value = {"quoted": f'"{destination}"', "environment": "$TASKMAN_PICKER_TEST_PATH",
             "tilde": "~/Planning Ω [red]"}[value_kind]

    async def go():
        app = PickerApp(tmp_path)
        async with app.run_test(notifications=True) as pilot:
            await settled(app, pilot)
            await pilot.press("ctrl+l")
            field = app.picker.query_one("#vault-path", Input)
            field.value = value
            await pilot.press("enter")
            await settled(app, pilot)
            assert app.picker._folder == destination
            assert field.value == str(destination)
            assert app.screen is app.picker
            await pilot.press("ctrl+enter")
            assert app.choices == [VaultChoice(destination, False)]
    asyncio.run(go())


def test_dirty_path_requires_preview_before_setup(tmp_path):
    destination = tmp_path / "Other"
    destination.mkdir()

    async def go():
        app = PickerApp(tmp_path)
        async with app.run_test(notifications=True) as pilot:
            await settled(app, pilot)
            app.picker.query_one("#vault-path", Input).value = str(destination)
            await pilot.pause()
            assert app.picker.query_one("#vault-setup", Button).disabled
            await pilot.press("ctrl+n")
            await settled(app, pilot)
            assert app.choices == []
            assert app.picker._folder == destination
            await pilot.press("ctrl+n")
            assert app.choices == [VaultChoice(destination, True)]
    asyncio.run(go())


def test_existing_folder_can_open_when_setup_has_a_name_conflict(tmp_path):
    (tmp_path / "Notes").write_text("An existing file, kept as-is.", encoding="utf-8")

    async def go():
        app = PickerApp(tmp_path)
        async with app.run_test(notifications=True) as pilot:
            await settled(app, pilot)
            assert not app.picker.query_one("#vault-open", Button).disabled
            assert app.picker.query_one("#vault-setup", Button).disabled
            assert "Setup unavailable" in rendered(app.picker.query_one("#vault-preview"))
            await pilot.press("ctrl+n")
            assert app.choices == []
            await pilot.press("ctrl+enter")
            assert app.choices == [VaultChoice(tmp_path, False)]
    asyncio.run(go())
    assert (tmp_path / "Notes").read_text(encoding="utf-8") == "An existing file, kept as-is."


@pytest.mark.parametrize("failure", ["file", "permission"])
def test_invalid_folder_stays_usable_with_inline_error(tmp_path, monkeypatch, failure):
    path = tmp_path / "Missing [red]"
    if failure == "file":
        path.write_text("not a folder", encoding="utf-8")
    original = vault_screen._directory_snapshot

    def snapshot(value):
        if failure == "permission" and value == str(path):
            raise PermissionError("Access denied [red]: choose a readable folder")
        return original(value)

    monkeypatch.setattr(vault_screen, "_directory_snapshot", snapshot)

    async def go():
        app = PickerApp(tmp_path)
        async with app.run_test(size=(60, 20), notifications=True) as pilot:
            await settled(app, pilot)
            field = app.picker.query_one("#vault-path", Input)
            field.value = str(path)
            await pilot.press("enter")
            await settled(app, pilot)
            error = app.picker.query_one("#vault-error", Static)
            assert error.has_class("has-error")
            assert app.screen is app.picker
            assert app.picker.query_one("#vault-open", Button).disabled
            assert app.focused is field
            assert app.picker.query_one("#vault-hints").region.bottom <= 20
            field.value = str(tmp_path)
            await pilot.press("enter")
            await settled(app, pilot)
            assert not error.has_class("has-error")
            await pilot.press("ctrl+enter")
            assert app.choices == [VaultChoice(tmp_path, False)]
    asyncio.run(go())


def test_missing_folder_offers_explicit_setup_and_never_creates_on_browse(tmp_path):
    destination = tmp_path / "Brand new" / "Tasks Ω"

    async def go():
        app = PickerApp(tmp_path)
        async with app.run_test(size=(60, 20), notifications=True) as pilot:
            await settled(app, pilot)
            app.picker.query_one("#vault-path", Input).value = str(destination)
            await pilot.press("enter")
            await settled(app, pilot)
            assert app.picker._folder == destination
            assert app.picker.query_one("#vault-open", Button).disabled
            assert not app.picker.query_one("#vault-setup", Button).disabled
            assert not destination.exists()
            await pilot.press("ctrl+enter")
            assert app.choices == []
            assert app.picker.query_one("#vault-error").has_class("has-error")
            await pilot.press("ctrl+n")
            assert app.choices == [VaultChoice(destination, True)]
    asyncio.run(go())
    assert not destination.exists()


def test_recent_folders_are_keyboard_accessible(tmp_path, monkeypatch):
    recent = tmp_path / "Recent Ω [bold]"
    recent.mkdir()
    monkeypatch.setattr(vault_screen, "recent_vaults", lambda: [recent])

    async def go():
        app = PickerApp(tmp_path)
        async with app.run_test(size=(80, 24), notifications=True) as pilot:
            await settled(app, pilot)
            await pilot.press("alt+r")
            await pilot.pause(0.1)
            listing = app.picker.query_one("#vault-folders", OptionList)
            assert listing.has_focus
            # Preserve the literal path, including spaces and markup-like text.
            assert str(listing.get_option_at_index(0).prompt) == str(recent)
            # Terminal wrapping may split a word; validate visible characters
            # separately from the exact, unwrapped label above.
            assert "".join(recent.name.split()) in "".join(rendered(listing).split())
            await pilot.press("enter")
            await settled(app, pilot)
            assert app.picker._folder == recent
            await pilot.press("alt+up")
            await settled(app, pilot)
            assert app.picker._folder == tmp_path
            await pilot.press("left")
            await settled(app, pilot)
            assert app.picker._folder == tmp_path.parent
    asyncio.run(go())


def test_empty_recents_and_resize_keep_path_entry_available(tmp_path, monkeypatch):
    monkeypatch.setattr(vault_screen, "recent_vaults", lambda: [])

    async def go():
        app = PickerApp(tmp_path)
        async with app.run_test(size=(140, 38), notifications=True) as pilot:
            await settled(app, pilot)
            await pilot.press("alt+r")
            await pilot.pause(0.1)
            assert "No recent vaults" in rendered(app.picker.query_one("#vault-folders"))
            for width, height in ((60, 20), (80, 24), (140, 38)):
                await pilot.resize_terminal(width, height)
                await pilot.pause()
                assert app.picker.query_one("#vault-hints").region.bottom <= height
                await pilot.press("ctrl+l")
                assert app.focused.id == "vault-path"
            await pilot.press("escape")
            assert app.choices == [None]
    asyncio.run(go())


@pytest.mark.parametrize("size", [(140, 38), (80, 24), (60, 20)])
def test_controls_and_keyhints_fit_and_tab_navigation_works(tmp_path, size):
    async def go():
        app = PickerApp(tmp_path, welcome=True)
        async with app.run_test(size=size, notifications=True) as pilot:
            await settled(app, pilot)
            screen = app.picker
            for selector in ("#vault-path", "#vault-folders", "#vault-preview", "#vault-open",
                             "#vault-setup", "#vault-cancel", "#vault-hints"):
                widget = screen.query_one(selector)
                assert widget.region.x >= 0 and widget.region.y >= 0
                assert widget.region.right <= size[0] and widget.region.bottom <= size[1], selector
                assert widget.visible and widget.size.height > 0
            hints = rendered(screen.query_one("#vault-hints"))
            assert all(key in hints for key in ("Ctrl+L", "Enter", "Alt+R", "Ctrl+N", "Esc"))
            focused = set()
            for _ in range(7):
                focused.add(app.focused.id)
                await pilot.press("tab")
            assert focused == {"vault-path", "vault-recent", "vault-folders", "vault-open", "vault-setup", "vault-cancel"}
            await pilot.press("ctrl+l")
            assert app.focused.id == "vault-path"
            await pilot.press("escape")
            assert app.choices == [None]
    asyncio.run(go())


def test_slow_folder_does_not_block_cancel_or_replace_newer_selection(tmp_path, monkeypatch):
    slow = tmp_path / "Slow"
    slow.mkdir()
    gate = threading.Event()
    started = threading.Event()
    original = vault_screen._directory_snapshot

    def delayed(value):
        if value == str(slow):
            started.set()
            gate.wait(10)
        return original(value)

    monkeypatch.setattr(vault_screen, "_directory_snapshot", delayed)

    async def go():
        app = PickerApp(tmp_path)
        async with app.run_test(notifications=True) as pilot:
            await settled(app, pilot)
            app.picker.query_one("#vault-path", Input).value = str(slow)
            await pilot.press("enter")
            await pilot.pause(0.03)
            assert started.is_set()
            assert app.picker._busy
            await pilot.press("ctrl+l")
            assert app.focused.id == "vault-path"
            app.picker.query_one("#vault-path", Input).value = str(tmp_path)
            await pilot.press("enter")
            await settled(app, pilot)
            assert app.picker._folder == tmp_path
            gate.set()
            await pilot.pause(0.05)
            assert app.picker._folder == tmp_path
            await pilot.press("escape")
            assert app.choices == [None]
    try:
        asyncio.run(go())
    finally:
        gate.set()
