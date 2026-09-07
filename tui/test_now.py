"""Now has complete membership/section rules and a shared date language."""
import datetime as dt
from itertools import product

import pytest

from tui import taskman as tm


DAY = dt.date(2026, 9, 6)


def task(name, due=None, scheduled=None, **kw):
    return tm.Task(file=f"{name}.md", lineno=1, description=name,
                   due=due, scheduled=scheduled, **kw)


def test_every_now_match_lands_in_exactly_one_section():
    dates = (None, DAY - dt.timedelta(days=1), DAY, DAY + dt.timedelta(days=7))
    tasks = [task(str(i), due, scheduled, status=status)
             for i, (due, scheduled, status) in enumerate(product(dates, dates, (" ", "/", "?", ">", "x", "-")))]
    expected = {t.id for t in tasks if t.status in (" ", "/", "?")
                and (t.due is not None and t.due <= DAY
                     or t.scheduled is not None and t.scheduled <= DAY)}
    rows = tm.view_tasks(tasks, "now", DAY)
    assert {t.id for t in rows} == expected
    sections = tm.sections(rows, "now", DAY)
    displayed = [n.task.id for s in sections for n in s.nodes]
    assert set(displayed) == expected and len(displayed) == len(expected)
    assert [s.key for s in sections] == ["overdue", "today"]
    assert all(n.task.due is not None and n.task.due < DAY for n in sections[0].nodes)
    assert all(n.task.due is None or n.task.due >= DAY for n in sections[1].nodes)


def test_past_scheduled_future_due_is_today_and_shows_scheduled_reason():
    t = task("Carry forward", DAY + dt.timedelta(days=5), DAY - dt.timedelta(days=1))
    assert tm.view_tasks([t], "now", DAY) == [t]
    assert tm.sections([t], "now", DAY)[0].key == "today"
    assert tm.now_date(t, DAY) == ("scheduled", t.scheduled)


def test_scheduling_does_not_hide_missed_deadline():
    t = task("Deadline wins", DAY - dt.timedelta(days=1), DAY + dt.timedelta(days=1))
    assert tm.view_tasks([t], "now", DAY) == [t]
    assert tm.now_date(t, DAY) == ("due", t.due)
    assert tm.sections([t], "now", DAY)[0].key == "overdue"


def test_start_only_and_future_deadlines_stay_out_of_now():
    tasks = [task("Started", start=DAY - dt.timedelta(days=3)),
             task("Future deadline", due=DAY + dt.timedelta(days=2)),
             task("Inbox")]
    assert tm.view_tasks(tasks, "now", DAY) == []
    assert len(tm.view_tasks(tasks, "all", DAY)) == 3


def test_forwarded_tasks_remain_in_all_search_and_deadline_views():
    t = task("Delegated", due=DAY - dt.timedelta(days=1), status=">")
    assert tm.view_tasks([t], "now", DAY) == []
    assert tm.view_tasks([t], "all", DAY) == [t]
    assert tm.view_tasks([t], "search", DAY, query="Delegated") == [t]
    assert tm.view_tasks([t], "overdue", DAY) == [t]


def test_inbox_excludes_any_scheduled_date_but_keeps_start_only():
    tasks = [task("Undated"), task("Scheduled", scheduled=DAY),
             task("Future scheduled", scheduled=DAY + dt.timedelta(days=3)),
             task("Start only", start=DAY)]
    assert {t.description for t in tm.view_tasks(tasks, "inbox", DAY)} == {"Undated", "Start only"}


def test_legacy_today_alias_and_counts_include_overdue_and_scheduled():
    tasks = [task("Deadline", due=DAY - dt.timedelta(days=1)),
             task("Planned", scheduled=DAY), task("Undated")]
    assert tm.view_tasks(tasks, "today", DAY) == tm.view_tasks(tasks, "now", DAY)
    assert tm.sections(tm.view_tasks(tasks, "today", DAY), "today", DAY) == tm.sections(
        tm.view_tasks(tasks, "now", DAY), "now", DAY)
    assert tm.counts(tasks, DAY)["today"] == tm.counts(tasks, DAY)["now"] == 2
    assert len(tm.VIEWS) == 7 and ("now", "Now") in tm.VIEWS


def test_next_seven_and_overdue_keep_deadline_only_meaning():
    tasks = [task("Scheduled", scheduled=DAY - dt.timedelta(days=1)),
             task("Deadline", due=DAY + dt.timedelta(days=2)),
             task("Missed", due=DAY - dt.timedelta(days=1))]
    assert [t.description for t in tm.view_tasks(tasks, "next7", DAY)] == ["Deadline"]
    assert [t.description for t in tm.view_tasks(tasks, "overdue", DAY)] == ["Missed"]


def test_now_sorts_priority_then_earliest_date_then_location():
    tasks = [task("a", scheduled=DAY - dt.timedelta(days=2), priority=0),
             task("z", due=DAY + dt.timedelta(days=5), scheduled=DAY, priority=5),
             task("b", scheduled=DAY - dt.timedelta(days=1), priority=3),
             task("c", scheduled=DAY - dt.timedelta(days=1), priority=3)]
    assert [n.task.description for n in tm.sections(tasks, "now", DAY)[0].nodes] == ["z", "b", "c", "a"]


def test_now_retains_context_ancestors_without_counting_them(tmp_path):
    path = tmp_path / "Work.md"
    path.write_text(f"- [>] Parent\n  - [ ] Planned child ⏳ {DAY}\n", encoding="utf-8")
    tasks = tm.load_all(tmp_path)
    counts = tm.counts(tasks, DAY)
    rows = tm.view_tasks(tasks, "now", DAY)
    sections = tm.sections(rows, "now", DAY)
    assert counts["now"] == 1
    assert [t.context for t in rows] == [True, False]
    assert sections[0].key == "today" and sections[0].count == 1
    assert [n.task.description for n in sections[0].nodes] == ["Parent", "Planned child"]


@pytest.mark.parametrize("text,expected", [
    ("today", DAY), ("tomorrow", DAY + dt.timedelta(days=1)),
    (" mon ", DAY + dt.timedelta(days=1)), ("Sunday", DAY + dt.timedelta(days=7)),
    ("+0", DAY), ("+7", DAY + dt.timedelta(days=7)),
    ("2028-02-29", dt.date(2028, 2, 29)), ("clear", None), ("none", None),
    ("-", None), ("", None), ("  ", None),
])
def test_shared_date_language(text, expected):
    assert tm.parse_date(text, DAY) == expected


@pytest.mark.parametrize("text", ["yesterday", "2026-02-29", "20260906", "2026-9-6", "+", "-7", "+999999999999999", "next whenever"])
def test_shared_date_language_rejects_bad_dates(text):
    with pytest.raises(ValueError, match="Date not understood"):
        tm.parse_date(text, DAY)


def test_date_overflow_is_validation_error():
    with pytest.raises(ValueError):
        tm.parse_date("tomorrow", dt.date.max)


def test_plain_now_explains_scheduled_reason(tmp_path, capsys):
    (tmp_path / "Work.md").write_text(f"- [ ] Plan ⏳ {DAY} 📅 2026-09-10\n", encoding="utf-8")
    assert tm.cmd_plain(tmp_path, "now", day=DAY) == 0
    output = capsys.readouterr().out
    assert "Today (1)" in output and "scheduled 2026-09-06" in output and "due 2026-09-10" in output
