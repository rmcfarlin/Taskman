"""Recurrence editing and completion through the actual task workflow."""
import asyncio
import datetime as dt

import pytest
from textual.widgets import Input, Label, OptionList
from textual.widgets._toast import Toast

from tui import app as appmod
from tui import taskman as tm


@pytest.fixture
def vault(tmp_path):
    (tmp_path / "Tasks").mkdir()
    (tmp_path / "Tasks/Inbox.md").write_text(
        "- [ ] Monthly close #project/Finance 🔁 every month on the last 📅 2026-09-30 ⏳ 2026-09-28 "
        f"<!-- taskman:id={'a' * 32} -->\n"
        "  Keep the reconciliation [[Checklist]].\n"
        "  - [ ] Review balances\n"
        "- [ ] Someday\n", encoding="utf-8")
    return tmp_path


def tasks(vault):
    return tm.load_all(vault)


async def select(pilot, app, description="Monthly close"):
    await pilot.press("1")
    task = next(t for t in tasks(app.vault) if t.description == description and t.open)
    table = app.query_one(appmod.TaskList)
    table.cursor = table.index_of(task.id)
    table._cursor_moved()
    await pilot.pause()


def messages(app):
    return "\n".join(t.render().plain for t in app.screen.query(Toast))


def test_edit_repeat_and_dates_is_one_undoable_write(vault):
    path = vault / "Tasks/Inbox.md"
    before = path.read_bytes()

    async def go():
        app = appmod.TaskApp(vault)
        async with app.run_test(size=(100, 30)) as pilot:
            await select(pilot, app)
            await pilot.press("d")
            dialog = app.screen
            assert dialog.query_one("#repeat", Input).value == "every month on the last"
            dialog.query_one("#due", Input).value = "2026-10-31"
            dialog.query_one("#scheduled", Input).value = "2026-10-29"
            dialog.query_one("#repeat", Input).value = "every 2 months when done"
            await pilot.press("ctrl+s")
            task = tasks(vault)[0]
            assert task.due == dt.date(2026, 10, 31)
            assert task.scheduled == dt.date(2026, 10, 29)
            assert task.recurrence == "every 2 months when done"
            changed = path.read_bytes()
            await pilot.press("u")
            assert path.read_bytes() == before and not app.history.can_undo
            await pilot.press("ctrl+y")
            assert path.read_bytes() == changed
    asyncio.run(go())


@pytest.mark.parametrize("rule", ["every month on the 31st", "every 0 days", "occasionally"])
def test_invalid_repeat_retains_draft_and_does_not_write(vault, rule):
    path = vault / "Tasks/Inbox.md"
    before = path.read_bytes()

    async def go():
        app = appmod.TaskApp(vault)
        async with app.run_test(size=(100, 30)) as pilot:
            await select(pilot, app)
            await pilot.press("d")
            dialog = app.screen
            dialog.query_one("#due", Input).value = "tomorrow"
            repeat = dialog.query_one("#repeat", Input)
            repeat.value = rule
            await pilot.press("ctrl+s")
            assert app.screen is dialog and repeat.has_focus
            assert repeat.value == rule and path.read_bytes() == before
            assert str(dialog.query_one("#form-error", Label).render())
            assert not app.history.can_undo
    asyncio.run(go())


def test_repeat_needs_a_date_and_none_clears_the_rule(vault):
    async def go():
        app = appmod.TaskApp(vault)
        async with app.run_test(size=(100, 30)) as pilot:
            await select(pilot, app, "Someday")
            await pilot.press("ctrl+k", *"set recurrence", "enter")
            dialog = app.screen
            repeat = dialog.query_one("#repeat", Input)
            assert repeat.has_focus
            repeat.value = "every week"
            await pilot.press("ctrl+s")
            assert app.screen is dialog and repeat.has_focus
            assert "needs" in str(dialog.query_one("#form-error", Label).render())
            repeat.value = "none"
            await pilot.press("ctrl+s")
            assert not isinstance(app.screen, appmod.DatesScreen)
            assert next(t for t in tasks(vault) if t.description == "Someday").recurrence == ""
    asyncio.run(go())


def test_completion_clones_parent_note_with_new_id_and_undo_restores_both(vault):
    path = vault / "Tasks/Inbox.md"
    before = path.read_bytes()

    async def go():
        app = appmod.TaskApp(vault)
        async with app.run_test(size=(110, 34), notifications=True) as pilot:
            await select(pilot, app)
            await pilot.press("space")
            rows = tasks(vault)
            next_task = next(t for t in rows if t.description == "Monthly close" and t.open)
            done = next(t for t in rows if t.description == "Monthly close" and t.done)
            assert next_task.open and done.done and next_task.lineno < done.lineno
            assert next_task.due == dt.date(2026, 10, 31)
            assert next_task.scheduled == dt.date(2026, 10, 29)
            assert next_task.anchor and next_task.anchor != done.anchor
            assert done.anchor == "a" * 32 and done.recurrence_next == next_task.anchor
            assert next_task.project == "Finance" and next_task.note == done.note
            assert not tm.children_of(rows, next_task)
            assert len(tm.children_of(rows, done)) == 1 and tm.children_of(rows, done)[0].done
            assert "next occurrence" in messages(app)
            completed = path.read_bytes()
            await pilot.press("u")
            assert path.read_bytes() == before and not app.history.can_undo
            await pilot.press("ctrl+y")
            assert path.read_bytes() == completed
    asyncio.run(go())


@pytest.mark.parametrize("rule,date", [("every January on the 15th", " 📅 2026-09-30"),
                                       ("every week", "")])
def test_imported_blocked_rule_completes_visibly_without_spawning(vault, rule, date):
    (vault / "Tasks/Inbox.md").write_text(f"- [ ] Imported 🔁 {rule}{date}\n", encoding="utf-8")

    async def go():
        app = appmod.TaskApp(vault)
        async with app.run_test(size=(110, 34), notifications=True) as pilot:
            await select(pilot, app, "Imported")
            await pilot.press("right")
            facts = str(app.query_one("#ins-facts").render())
            assert rule in facts
            await pilot.press("left", "space")
            rows = tasks(vault)
            assert len(rows) == 1 and rows[0].done and rows[0].recurrence == rule
            assert tm.recurrence_warning(rows[0]) in messages(app)
    asyncio.run(go())


@pytest.mark.parametrize("size", [(50, 16), (80, 24)])
def test_repeat_controls_fit_and_presets_return_to_draft(vault, size):
    async def go():
        app = appmod.TaskApp(vault)
        async with app.run_test(size=size) as pilot:
            await select(pilot, app)
            await pilot.press("d")
            dialog = app.screen
            card = dialog.query_one("#dlg")
            assert card.max_scroll_y == 0
            for selector in ("#due", "#scheduled", "#repeat-label", "#repeat", "#repeat-presets", "#ok"):
                control = dialog.query_one(selector)
                assert control.region.y >= card.content_region.y
                assert control.region.bottom <= card.content_region.bottom
                assert control.region.right <= card.content_region.right
            await pilot.click("#repeat-presets")
            assert isinstance(app.screen, appmod.PickScreen)
            options = app.screen.query_one(OptionList)
            options.highlighted = 2  # weekdays
            await pilot.press("enter")
            assert app.screen is dialog
            field = dialog.query_one("#repeat", Input)
            assert field.has_focus and field.value == "every weekday"
            await pilot.press("escape")
            assert tasks(vault)[0].recurrence == "every month on the last"
    asyncio.run(go())


@pytest.mark.parametrize("action,draft", [
    ("a", "Follow up 🔁 every day"),
    ("t", "Check details 🔁 every month on the 31st 📅 2026-09-30"),
    ("e", "Renamed 🔁 every week"),
])
def test_invalid_inline_repeat_keeps_capture_or_rename_draft(vault, action, draft):
    before = (vault / "Tasks/Inbox.md").read_bytes()

    async def go():
        app = appmod.TaskApp(vault)
        async with app.run_test(size=(100, 30)) as pilot:
            await select(pilot, app)
            await pilot.press(action)
            dialog = app.screen
            field = dialog.query_one("#text", Input)
            field.value = draft
            await pilot.press("enter")
            assert app.screen is dialog and field.has_focus and field.value == draft
            assert str(dialog.query_one("#form-error", Label).render())
            assert (vault / "Tasks/Inbox.md").read_bytes() == before
            assert not app.history.can_undo
    asyncio.run(go())
