"""Now and Dates contracts against real Textual screens and disposable vaults."""
import asyncio
import datetime as dt
from pathlib import Path

import pytest
from textual.widgets import Input, Label

from tui import app as appmod
from tui import taskman as tm


@pytest.fixture
def dates_vault(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("TASKMAN_THEME", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    (tmp_path / "Tasks").mkdir()
    day = dt.date.today()
    (tmp_path / "Tasks/Inbox.md").write_text(
        f"- [ ] Target 📅 {day} ⏳ {day - dt.timedelta(days=1)} <!-- taskman:id={'a' * 32} -->\n"
        f"- [ ] Work today ⏳ {day} 📅 {day + dt.timedelta(days=4)}\n"
        f"- [ ] Carried work ⏳ {day - dt.timedelta(days=2)}\n"
        f"- [ ] Missed deadline 📅 {day - dt.timedelta(days=1)} ⏳ {day + dt.timedelta(days=2)}\n"
        "- [ ] Someday\n",
        encoding="utf-8")
    return tmp_path


def _task(root, description="Target"):
    return next(t for t in tm.load_all(root) if t.description == description)


async def _target(pilot, app):
    table = app.query_one(appmod.TaskList)
    table.cursor = table.index_of(_task(app.vault).id)
    table._cursor_moved()
    await pilot.pause()
    await pilot.press("d")
    await pilot.pause()
    assert isinstance(app.screen, appmod.DatesScreen)
    return app.screen


def test_now_is_default_and_notes_retains_key_eight(dates_vault):
    async def go():
        app = appmod.TaskApp(dates_vault)
        async with app.run_test(size=(120, 32)) as pilot:
            await pilot.pause()
            assert app.view == "now"
            table = app.query_one(appmod.TaskList)
            rows = [r for r in table.rows if isinstance(r, appmod.TaskRow)]
            assert {r.task.description for r in rows} == {
                "Target", "Work today", "Carried work", "Missed deadline"}
            assert [r.title for r in table.rows if isinstance(r, appmod.HeaderRow) and r.title] == ["Overdue", "Today"]
            sidebar = app.query_one(appmod.Sidebar)
            assert sidebar.highlighted_option.id == "view:now"
            await pilot.press("8")
            assert app.view == "notes"
            await pilot.press("2")
            assert app.view == "now" and table.has_focus
            await pilot.press("1")
            assert "Someday" in {r.task.description for r in table.rows if isinstance(r, appmod.TaskRow)}
    asyncio.run(go())


def test_date_column_explains_now_membership(dates_vault):
    table = appmod.TaskList()
    table.day = dt.date.today()
    table.view = "now"
    assert table._due_cell(_task(dates_vault, "Work today")) == ("Sched today", "today")
    assert table._due_cell(_task(dates_vault, "Target")) == ("Due today", "today")
    assert table._due_cell(_task(dates_vault, "Missed deadline")) == ("Due 1d late", "overdue")
    assert table._due_cell(_task(dates_vault, "Carried work"))[0].startswith("Sched ")
    assert "late" not in table._due_cell(_task(dates_vault, "Carried work"))[0]
    table.view = "all"
    assert table._due_cell(_task(dates_vault, "Work today"))[0].startswith("Due ")
    assert table._due_cell(_task(dates_vault, "Carried work"))[0].startswith("Sched ")


def test_dates_prefill_and_validate_both_before_writing(dates_vault):
    before = (dates_vault / "Tasks/Inbox.md").read_bytes()
    async def go():
        app = appmod.TaskApp(dates_vault)
        async with app.run_test(size=(100, 30)) as pilot:
            dialog = await _target(pilot, app)
            due = dialog.query_one("#due", Input)
            scheduled = dialog.query_one("#scheduled", Input)
            assert due.value == dt.date.today().isoformat()
            assert scheduled.value == (dt.date.today() - dt.timedelta(days=1)).isoformat()
            due.value, scheduled.value = "tomorrow", "not a date"
            await pilot.press("enter")
            assert app.screen is dialog and scheduled.has_focus
            assert due.value == "tomorrow" and scheduled.value == "not a date"
            assert "Scheduled" in str(dialog.query_one("#form-error", Label).render())
            assert (dates_vault / "Tasks/Inbox.md").read_bytes() == before
            assert not app.history.can_undo
            await pilot.press("escape")
            assert app.query_one(appmod.TaskList).has_focus
    asyncio.run(go())


@pytest.mark.parametrize("pointer", [False, True])
def test_quick_dates_target_the_last_focused_field(dates_vault, pointer):
    async def go():
        app = appmod.TaskApp(dates_vault)
        async with app.run_test(size=(100, 30)) as pilot:
            dialog = await _target(pilot, app)
            due = dialog.query_one("#due", Input)
            scheduled = dialog.query_one("#scheduled", Input)
            initial_due = due.value
            if pointer:
                await pilot.click(scheduled)
                await pilot.click("#q-tomorrow")
            else:
                await pilot.press("tab", "tab", "tab", "enter")
            await pilot.pause()
            assert app.screen is dialog and scheduled.has_focus
            assert due.value == initial_due and scheduled.value == "tomorrow"
            assert "Scheduled" in str(dialog.query_one("#date-target", Label).render())
            await pilot.press("ctrl+s")
            await pilot.pause()
            assert not isinstance(app.screen, appmod.DatesScreen)
            saved = _task(dates_vault)
            assert saved.due == dt.date.today()
            assert saved.scheduled == dt.date.today() + dt.timedelta(days=1)
    asyncio.run(go())


def test_dates_clear_is_one_undoable_change(dates_vault):
    path = dates_vault / "Tasks/Inbox.md"
    before = path.read_bytes()
    async def go():
        app = appmod.TaskApp(dates_vault)
        async with app.run_test(size=(100, 30)) as pilot:
            dialog = await _target(pilot, app)
            dialog.query_one("#due", Input).value = "clear"
            dialog.query_one("#scheduled", Input).value = ""
            await pilot.press("enter")
            await pilot.pause()
            task = _task(dates_vault)
            assert task.due is None and task.scheduled is None
            changed = path.read_bytes()
            assert changed != before and app.history.undo_label == "Change dates"
            await pilot.press("u")
            assert path.read_bytes() == before and not app.history.can_undo
            await pilot.press("ctrl+y")
            assert path.read_bytes() == changed
    asyncio.run(go())


def test_dates_conflict_retains_drafts_and_external_change(dates_vault):
    path = dates_vault / "Tasks/Inbox.md"
    async def go():
        app = appmod.TaskApp(dates_vault)
        async with app.run_test(size=(100, 30)) as pilot:
            dialog = await _target(pilot, app)
            dialog.query_one("#due", Input).value = "+7"
            dialog.query_one("#scheduled", Input).value = "tomorrow"
            path.write_text(path.read_text(encoding="utf-8").replace("Target", "External edit"), encoding="utf-8")
            external = path.read_bytes()
            await pilot.press("ctrl+s")
            await pilot.pause()
            assert app.screen is dialog
            assert dialog.query_one("#due", Input).value == "+7"
            assert dialog.query_one("#scheduled", Input).value == "tomorrow"
            assert "Could not save" in str(dialog.query_one("#form-error", Label).render())
            assert path.read_bytes() == external and not app.history.can_undo
    asyncio.run(go())


@pytest.mark.parametrize("action", ["dates", "complete"])
def test_moved_task_history_records_the_current_file(dates_vault, action):
    source = dates_vault / "Tasks/Inbox.md"
    destination = dates_vault / "Tasks/Moved.md"
    async def go():
        app = appmod.TaskApp(dates_vault)
        async with app.run_test(size=(100, 30)) as pilot:
            dialog = await _target(pilot, app)
            if action == "complete":
                await pilot.press("escape")
            lines = source.read_text(encoding="utf-8").splitlines(keepends=True)
            destination.write_text("# Moved\n\n" + lines[0], encoding="utf-8")
            source.write_text("".join(lines[1:]), encoding="utf-8")
            current_source, current_destination = source.read_bytes(), destination.read_bytes()
            if action == "dates":
                dialog.query_one("#due", Input).value = "+7"
                await pilot.press("ctrl+s")
            else:
                await pilot.press("space")
            await pilot.pause()
            assert source.read_bytes() == current_source
            assert destination.read_bytes() != current_destination
            await pilot.press("u")
            assert source.read_bytes() == current_source
            assert destination.read_bytes() == current_destination
    asyncio.run(go())


def test_now_refreshes_at_midnight_without_reloading_files(dates_vault, monkeypatch):
    from types import SimpleNamespace
    day = dt.date.today()
    path = dates_vault / "Tasks/Inbox.md"
    with path.open("a", encoding="utf-8") as stream:
        stream.write(f"- [ ] Starts tomorrow ⏳ {day + dt.timedelta(days=1)}\n")

    class TomorrowDate(dt.date):
        @classmethod
        def today(cls):
            return day + dt.timedelta(days=1)

    async def go():
        app = appmod.TaskApp(dates_vault)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            table = app.query_one(appmod.TaskList)
            def descriptions():
                return {r.task.description for r in table.rows if isinstance(r, appmod.TaskRow)}
            assert "Starts tomorrow" not in descriptions()
            mtime = path.stat().st_mtime_ns
            calls = []
            original = app.refresh_tasks
            def refresh(**kwargs):
                calls.append(kwargs)
                return original(**kwargs)
            monkeypatch.setattr(app, "refresh_tasks", refresh)
            monkeypatch.setattr(appmod, "dt", SimpleNamespace(
                date=TomorrowDate, datetime=dt.datetime, timedelta=dt.timedelta))
            app._tick()
            assert "Starts tomorrow" in descriptions()
            assert table.day == day + dt.timedelta(days=1)
            assert calls == [{"reload": False}] and path.stat().st_mtime_ns == mtime
            app._tick()
            assert len(calls) == 1
    asyncio.run(go())


def test_inspector_distinguishes_absent_due_and_scheduled_dates(dates_vault):
    async def go():
        app = appmod.TaskApp(dates_vault)
        async with app.run_test(size=(110, 32)) as pilot:
            await pilot.press("1")
            table = app.query_one(appmod.TaskList)
            table.cursor = table.index_of(_task(dates_vault, "Someday").id)
            table._cursor_moved()
            await pilot.press("right")
            await pilot.pause()
            inspector = app.query_one(appmod.Inspector)
            rendered = str(inspector.query_one("#ins-facts").render())
            assert "no due date" in rendered and "no scheduled date" in rendered
    asyncio.run(go())


@pytest.mark.parametrize("size", [(50, 16), (80, 24)])
def test_dates_labels_and_controls_fit_without_scrolling(dates_vault, size):
    async def go():
        app = appmod.TaskApp(dates_vault)
        async with app.run_test(size=size) as pilot:
            screen = await _target(pilot, app)
            dialog = screen.query_one("#dlg")
            assert dialog.scroll_y == 0 and dialog.max_scroll_y == 0
            for selector in ("#date-help", "#due-label", "#due", "#scheduled-label", "#scheduled",
                             "#date-target", "#q-today", "#q-tomorrow", "#q-week", "#q-clear", "#ok", "#cancel"):
                control = screen.query_one(selector)
                assert control.region.x >= dialog.content_region.x
                assert control.region.right <= dialog.content_region.right
                assert control.region.y >= dialog.content_region.y
                assert control.region.bottom <= dialog.content_region.bottom
                assert control.region.y >= 0 and control.region.bottom <= size[1]
            await pilot.press("tab")
            await pilot.pause()
            assert screen.query_one("#scheduled", Input).has_focus
            assert dialog.scroll_y == 0
    asyncio.run(go())
