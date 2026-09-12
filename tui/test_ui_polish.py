"""Exercise the compact chrome with real layout, keyboard and mouse events."""
import asyncio

import pytest
from rich.text import Text
from textual.color import Color
from textual.widgets import Button, Input, Label, Static

from tui import app as appmod
from tui.notes import NotesStore
from tui.notes_ui import NotesWorkspace
from tui.shortcut_bar import ShortcutBar


@pytest.fixture
def vault(tmp_path):
    (tmp_path / "Tasks").mkdir()
    (tmp_path / "Projects").mkdir()
    (tmp_path / "Tasks/Inbox.md").write_text("- [ ] Review draft\n- [ ] Final check\n", encoding="utf-8")
    (tmp_path / "Projects/Alpha.md").write_text("- [ ] Alpha task\n", encoding="utf-8")
    NotesStore(tmp_path).create("Reference", "A readable note.")
    return tmp_path


@pytest.mark.asyncio
@pytest.mark.parametrize("width", [40, 60, 80, 100, 140])
async def test_one_row_header_and_status_preserve_room_and_primary_note_actions(vault, width):
    app = appmod.TaskApp(vault)
    async with app.run_test(size=(width, 24)) as pilot:
        await pilot.press("8")
        await pilot.pause()
        dock = app.query_one(ShortcutBar)
        assert app.query_one("#topbar").size.height == 1
        assert app.query_one("#statusbar").size.height == 1
        assert app.query_one("#status-info").display
        assert dock.size.height <= 2
        text = "\n".join(dock.render_line(y).text for y in range(dock.size.height))
        for label in ("Add", "Edit", "Delete", "Undo", "Find", "Ctrl+K", "More"):
            assert label in text, (width, text)
        assert app.query_one(NotesWorkspace).size.height >= 19
        assert "open" not in str(app.query_one("#crumb", Label).content)


@pytest.mark.asyncio
async def test_routine_status_survives_refresh_and_old_timer_at_narrow_width(vault):
    app = appmod.TaskApp(vault)
    async with app.run_test(size=(40, 24), notifications=True) as pilot:
        await pilot.press("1")
        app.announce("Saved [draft]", timeout=30)
        old_generation = app._status_generation
        app.announce("Second save", timeout=30)
        app._set_status_info(Text("Current view after refresh"))
        app._clear_announcement(old_generation)
        await pilot.pause()
        label = app.query_one("#status-info", Label)
        assert label.content.plain == "Second save"
        assert label.display and label.size.width >= 18
        assert not list(app.screen.query("Toast"))
        app._clear_announcement(app._status_generation)
        assert label.content.plain == "Current view after refresh"
        assert not label.has_class("-message")
        app.announce("Expires", timeout=0.05)
        await asyncio.sleep(0.08)
        await pilot.pause()
        assert label.content.plain == "Current view after refresh"


@pytest.mark.asyncio
async def test_modal_hides_letter_shortcuts_and_returns_them_on_close(vault):
    app = appmod.TaskApp(vault)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.press("a")
        await pilot.pause()
        dock = app.query_one(ShortcutBar)
        assert not dock.visible
        field = app.screen.query_one("#text", Input)
        error = app.screen.query_one("#form-error", appmod.FormError)
        assert field.size.height == 1
        assert not error.display
        await pilot.press("enter")
        assert error.display
        await pilot.press("q", "h", "m")
        assert field.value == "qhm"
        assert field.background_colors[1] != Color.parse(app.theme_variables["surface"])
        await pilot.click("#cancel")
        await pilot.pause()
        assert dock.visible
        assert app.query_one(appmod.TaskList).has_focus


@pytest.mark.asyncio
async def test_notes_sidebar_yields_width_and_manual_toggle_keeps_selection(vault):
    app = appmod.TaskApp(vault)
    async with app.run_test(size=(100, 26)) as pilot:
        await pilot.press("8")
        await pilot.pause()
        sidebar = app.query_one(appmod.Sidebar)
        assert not sidebar.display
        assert app.query_one(NotesWorkspace).size.width >= 78
        await pilot.resize_terminal(140, 26)
        await pilot.pause()
        assert sidebar.display
        ids = [option.id for option in sidebar.options]
        assert ids.index("view:notes") < ids.index("h:projects")
        assert "h:reference" not in ids
        note = app.query_one(NotesWorkspace).current.file
        await pilot.press("ctrl+b")
        assert not sidebar.display
        await pilot.resize_terminal(100, 26)
        await pilot.resize_terminal(140, 26)
        assert not sidebar.display
        await pilot.press("ctrl+b")
        assert sidebar.display
        assert app.query_one(NotesWorkspace).current.file == note


@pytest.mark.asyncio
@pytest.mark.parametrize("width", [45, 80])
async def test_help_wraps_descriptions_in_own_column_and_close_is_clickable(vault, width):
    app = appmod.TaskApp(vault)
    async with app.run_test(size=(width, 24)) as pilot:
        await pilot.press("f1")
        await pilot.pause()
        rows = list(app.screen.query(".help-row"))
        assert len(rows) > 40
        for row in rows[:3]:
            key = row.query_one(".help-key", Static)
            desc = row.query_one(".help-description", Static)
            assert desc.region.x > key.region.x
            assert desc.region.right <= app.screen.query_one("#help-content").content_region.right
            assert row.size.height == max(key.size.height, desc.size.height)
        close = app.screen.query_one("#ok", Button)
        assert close.size.height == 1
        assert close.region.bottom <= app.size.height
        await pilot.click(close)
        assert not isinstance(app.screen, appmod.HelpScreen)


@pytest.mark.asyncio
async def test_task_picker_titles_identify_the_target_and_github_result_stays_prominent(vault):
    app = appmod.TaskApp(vault)
    async with app.run_test(size=(90, 24), notifications=True) as pilot:
        await pilot.press("1")
        task = app.query_one(appmod.TaskList).current
        for key in ("d", "p", "s"):
            await pilot.press(key)
            assert task.description in str(app.screen.query_one("#dlg").border_title)
            await pilot.press("escape")
        app._finish_vault_push("Pushed to GitHub", False)
        await pilot.pause()
        toast = app.screen.query_one("Toast")
        assert toast._notification.message == "Pushed to GitHub"
        assert toast._notification.timeout == 12


def test_light_theme_alias_and_semantic_contrast(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert appmod.resolve_theme("catppuccin-latte") == "taskman-catppuccin-latte"
    assert not appmod.theme_is_dark("catppuccin-latte")
    assert appmod.LIGHT_THEME is appmod.LIGHT_THEMES[0][0]
    assert not appmod.LIGHT_THEME.dark

    def luminance(color):
        values = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in values]
        return sum(c * weight for c, weight in zip(linear, (.2126, .7152, .0722)))

    gruvbox = next(theme for theme, _label in appmod.LIGHT_THEMES if theme.name == "taskman-gruvbox-light")
    assert gruvbox.surface == "#f2e5bc" and gruvbox.primary == "#076678"
    assert gruvbox.accent == "#9a3203" and gruvbox.error == "#9d0006"
    latte = next(theme for theme, _label in appmod.LIGHT_THEMES if theme.name == "taskman-catppuccin-latte")
    assert latte.background == "#eff1f5" and latte.primary == "#1e66f5"
    assert latte.surface == "#e6e9ef" and latte.panel == "#dce0e8"
    for theme, _label in appmod.LIGHT_THEMES:
        assert not theme.dark
        for foreground in (theme.foreground, theme.warning, theme.error, theme.success,
                           theme.variables["text-muted"], theme.variables["text-primary"]):
            for background in (theme.background, theme.surface, theme.panel):
                ratio = (luminance(background) + .05) / (luminance(foreground) + .05)
                assert ratio >= 4.5, (theme.name, foreground, background, ratio)
