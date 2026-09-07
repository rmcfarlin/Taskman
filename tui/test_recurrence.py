"""Calendar rules and atomic, repeatable Markdown occurrence completion."""
import datetime as dt
from dataclasses import replace

import pytest

from tui import recurrence as rules
from tui import taskman as tm
from tui.history import History


DAY = dt.date(2026, 9, 6)
ANCHOR = "0123456789abcdef0123456789abcdef"
CHILD = "fedcba9876543210fedcba9876543210"


def date(value):
    return dt.date.fromisoformat(value)


def write_task(root, body, *, newline="\n"):
    path = root / "Tasks" / "Inbox.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body.replace("\n", newline).encode("utf-8"))
    return path, tm.load_all(root)[0]


def recurring(root, rule="every day", dates="📅 2026-09-06", suffix="", **kwargs):
    return write_task(root, f"- [ ] Review 🔁 {rule} {dates} <!-- taskman:id={ANCHOR} -->\n{suffix}", **kwargs)


@pytest.mark.parametrize("source,expected", [
    ("daily", "every day"), (" weekly ", "every week"),
    ("Monthly", "every month"), ("yearly", "every year"),
    ("weekdays", "every weekday"), ("every 1 days", "every day"),
    ("every 2 days", "every 2 days"), ("every 2 weeks when done", "every 2 weeks when done"),
    ("  🔁 every week on WED, Monday, Wed ", "every week on monday, wednesday"),
    ("every month on the last when done", "every month on the last when done"),
    ("clear", ""), ("NONE", ""), ("", ""),
])
def test_normalize_rules(source, expected):
    assert tm.normalize_recurrence(source) == expected


@pytest.mark.parametrize("rule", ["every 0 days", "every -1 week", "every month on the 31st",
    "every second Thursday", "every 2 years", "every weeks", "every week on Monday and Friday",
    "every week on moonday", "every week on Monday,", "every day until Christmas"])
def test_unsupported_rules_rejected(rule):
    with pytest.raises(ValueError):
        rules.parse_rule(rule)


@pytest.mark.parametrize("rule,reference,completion,expected", [
    ("every day", "2026-09-01", "2026-09-20", "2026-09-02"),
    ("every day when done", "2026-09-01", "2026-09-20", "2026-09-21"),
    ("every 3 days", "2026-09-29", None, "2026-10-02"),
    ("every weekday", "2026-09-04", None, "2026-09-07"),
    ("every weekday", "2026-09-06", None, "2026-09-07"),
    ("every week", "2026-12-28", None, "2027-01-04"),
    ("every 2 weeks", "2026-09-01", None, "2026-09-15"),
    ("every week on Monday, Wednesday", "2026-09-07", None, "2026-09-09"),
    ("every week on Monday, Wednesday", "2026-09-09", None, "2026-09-14"),
    ("every week on Monday", "2026-09-07", None, "2026-09-14"),
    ("every month", "2026-01-31", None, "2026-02-28"),
    ("every month", "2028-01-31", None, "2028-02-29"),
    ("every 2 months", "2026-12-31", None, "2027-02-28"),
    ("every month on the 1st", "2026-01-31", None, "2026-02-01"),
    ("every month on the 1st", "2026-02-01", None, "2026-03-01"),
    ("every month on the last", "2026-02-01", None, "2026-02-28"),
    ("every month on the last", "2026-02-28", None, "2026-03-31"),
    ("every year", "2024-02-29", None, "2025-02-28"),
    ("every month when done", "2026-01-01", "2026-01-31", "2026-02-28"),
])
def test_calendar(rule, reference, completion, expected):
    assert rules.next_date(rule, date(reference), date(completion) if completion else None) == date(expected)


@pytest.mark.parametrize("rule", ["every day", "every month", "every year", "every 99999999999999999 days"])
def test_calendar_overflow_is_a_clear_validation_error(rule):
    with pytest.raises(ValueError, match="calendar"):
        rules.next_date(rule, dt.date.max)


@pytest.mark.parametrize("body", [
    "Review 🔁 every week on Monday, Wednesday ⏫ 📅 2026-09-06 #project/House #home",
    "Review ⏫ 📅 2026-09-06 #project/House #home 🔁 every week on Monday, Wednesday",
])
def test_parser_separates_rule_from_text_and_other_metadata(body):
    task = tm.parse_task_line(f"- [ ] {body} <!-- taskman:id={ANCHOR} --> <!-- taskman:next={CHILD} -->")
    assert task.description == "Review"
    assert task.recurrence == "every week on Monday, Wednesday"
    assert task.priority == 4 and task.project == "House" and task.due == DAY
    assert task.anchor == ANCHOR and task.recurrence_next == CHILD
    rebuilt = tm.parse_task_line(tm.build_line(task))
    assert rebuilt.recurrence == task.recurrence and rebuilt.description == task.description
    assert rebuilt.recurrence_next == CHILD and "taskman:" not in task.short()


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_completion_clones_only_parent_and_own_note_preserving_ids_offsets_and_bytes(tmp_path, newline, monkeypatch):
    body = (f"# Heading\n\n* [/] Review 🔁 every month ⏫ 🛫 2026-01-25 ⏳ 2026-01-29 📅 2026-01-31 #project/House #home <!-- taskman:id={ANCHOR} -->\n"
            "  Keep **formatted** note and [[reference]].\n\n  Second paragraph.\n"
            f"  - [ ] Child 🔁 every day 📅 2026-01-31 <!-- taskman:id={CHILD} -->\n"
            "    Child note.\n  - [-] Cancelled ❌ 2026-01-01\n\nUnrelated tail without newline")
    path, task = write_task(tmp_path, body, newline=newline)
    calls = []
    original_write = tm._write_lines_atomic
    def observe(*args, **kwargs):
        calls.append(args[0])
        return original_write(*args, **kwargs)
    monkeypatch.setattr(tm, "_write_lines_atomic", observe)
    assert tm.complete(tmp_path, task, DAY) is task
    all_tasks = tm.parse_file(path, tmp_path)
    assert len(all_tasks) == 4 and len(calls) == 1
    next_task, source, child, cancelled = all_tasks
    assert source.anchor == ANCHOR and source.done and source.done_date == DAY
    assert task == source and task.lineno == 7
    assert next_task.anchor == source.recurrence_next and next_task.anchor not in (ANCHOR, CHILD)
    assert next_task.open and next_task.recurrence_next == "" and next_task.done_date is None
    assert next_task.description == source.description and next_task.tags == source.tags
    assert next_task.priority == source.priority and next_task.bullet == "*"
    assert next_task.note == source.note == "Keep **formatted** note and [[reference]].\n\nSecond paragraph."
    assert (next_task.start, next_task.scheduled, next_task.due) == (
        date("2026-02-22"), date("2026-02-26"), date("2026-02-28"))
    assert child.done and child.anchor == CHILD and child.recurrence_next == "" and child.parent_lineno == source.lineno
    assert cancelled.cancelled and cancelled.cancelled_date == date("2026-01-01")
    saved = path.read_bytes()
    assert saved.startswith(f"# Heading{newline}{newline}".encode())
    assert saved.endswith(b"Unrelated tail without newline")
    assert saved.count(f"<!-- taskman:id={ANCHOR} -->".encode()) == 1
    assert saved.count(f"<!-- taskman:id={CHILD} -->".encode()) == 1
    assert saved.count(f"  Keep **formatted** note and [[reference]].{newline}".encode()) == 2


@pytest.mark.parametrize("dates,expected_due,expected_scheduled,expected_start", [
    ("📅 2026-09-06 ⏳ 2026-09-04 🛫 2026-09-01", "2026-09-07", "2026-09-05", "2026-09-02"),
    ("⏳ 2026-09-04 🛫 2026-09-01", None, "2026-09-05", "2026-09-02"),
    ("🛫 2026-09-01", None, None, "2026-09-02"),
])
def test_reference_precedence_and_missing_dates_stay_missing(tmp_path, dates, expected_due, expected_scheduled, expected_start):
    _, task = recurring(tmp_path, dates=dates)
    tm.complete(tmp_path, task, DAY)
    following = tm.resolve_task(tmp_path, task.recurrence_next)
    assert (following.due, following.scheduled, following.start) == tuple(
        date(value) if value else None for value in (expected_due, expected_scheduled, expected_start))


def test_when_done_uses_completion_day_and_preserves_relative_offsets(tmp_path):
    _, task = recurring(tmp_path, "every 2 days when done", "📅 2026-09-01 ⏳ 2026-08-30")
    tm.complete(tmp_path, task, DAY)
    following = tm.resolve_task(tmp_path, task.recurrence_next)
    assert following.due == date("2026-09-08") and following.scheduled == DAY


def test_completion_retry_and_reopen_do_not_create_another_successor_even_if_deleted(tmp_path):
    path, task = recurring(tmp_path)
    tm.complete(tmp_path, task, DAY)
    first = path.read_bytes()
    next_id = task.recurrence_next
    tm.complete(tmp_path, task, DAY + dt.timedelta(days=1))
    assert path.read_bytes() == first and task.done_date == DAY
    tm.toggle(tmp_path, task, DAY)
    assert task.open and task.recurrence_next == next_id
    tm.complete(tmp_path, task, DAY)
    assert len(tm.load_all(tmp_path)) == 2 and task.recurrence_next == next_id
    tm.delete_task(tmp_path, tm.resolve_task(tmp_path, next_id))
    tm.toggle(tmp_path, task, DAY)
    tm.complete(tmp_path, task, DAY)
    assert len(tm.load_all(tmp_path)) == 1 and task.recurrence_next == next_id


def test_cancel_does_not_spawn_but_done_status_does(tmp_path):
    _, task = recurring(tmp_path)
    tm.set_status(tmp_path, task, "-", DAY)
    assert len(tm.load_all(tmp_path)) == 1 and task.recurrence_next == ""
    tm.set_status(tmp_path, task, "x", DAY)
    assert len(tm.load_all(tmp_path)) == 2 and task.recurrence_next


@pytest.mark.parametrize("rule,dates,warning,completion_day", [
    ("every second Thursday", "📅 2026-09-06", "Unsupported", DAY),
    ("every month on the 31st", "📅 2026-09-06", "month end", DAY),
    ("every day", "", "needs", DAY),
    ("every day", "📅 9999-12-31", "calendar", DAY),
    ("every month when done", "📅 0002-01-01 🛫 0001-01-01", "calendar", dt.date.min),
])
def test_handwritten_unusable_rules_complete_without_spawn_and_keep_warning(tmp_path, rule, dates, warning, completion_day):
    _, task = recurring(tmp_path, rule, dates)
    assert warning in tm.recurrence_warning(task, completion_day)
    tm.complete(tmp_path, task, completion_day)
    assert task.done and not task.recurrence_next and len(tm.load_all(tmp_path)) == 1
    assert task.recurrence == rule and warning in tm.recurrence_warning(task)


def test_conflicting_successor_markers_warn_and_never_spawn(tmp_path):
    _, task = recurring(tmp_path, dates=f"📅 2026-09-06 <!-- taskman:next={ANCHOR} --> <!-- taskman:next={CHILD} -->")
    assert "Conflicting" in tm.recurrence_warning(task)
    tm.complete(tmp_path, task, DAY)
    assert len(tm.load_all(tmp_path)) == 1 and "Conflicting" in tm.recurrence_warning(task)


def test_atomic_dates_repeat_validation_and_clear(tmp_path):
    path, task = recurring(tmp_path)
    before = path.read_bytes()
    original = replace(task)
    for due, planned, rule in [(None, None, "every day"), (DAY, DAY, "nonsense")]:
        with pytest.raises(ValueError):
            tm.set_dates(tmp_path, task, due, planned, recurrence=rule)
        assert path.read_bytes() == before and task == original
    tm.set_dates(tmp_path, task, None, DAY, recurrence="weekly")
    assert task.due is None and task.scheduled == DAY and task.recurrence == "every week"
    with pytest.raises(ValueError, match="needs"):
        tm.set_scheduled(tmp_path, task, None)
    tm.set_dates(tmp_path, task, None, None, recurrence="clear")
    assert not task.recurrence and task.scheduled is None


def test_unsupported_existing_rule_survives_unrelated_edits_and_date_updates(tmp_path):
    _, task = recurring(tmp_path, "every second Thursday")
    tm.set_priority(tmp_path, task, 4)
    tm.edit_text(tmp_path, task, "Renamed")
    tm.set_dates(tmp_path, task, DAY, DAY, recurrence=task.recurrence)
    tm.set_recurrence(tmp_path, task, task.recurrence)
    assert task.description == "Renamed" and task.recurrence == "every second Thursday"


@pytest.mark.parametrize("existing", [False, True])
def test_rename_cannot_introduce_or_duplicate_repeat_metadata(tmp_path, existing):
    path, task = recurring(tmp_path) if existing else write_task(tmp_path, "- [ ] Original\n")
    before, snapshot = path.read_bytes(), replace(task)
    with pytest.raises(ValueError, match="Use Dates"):
        tm.edit_text(tmp_path, task, "New title 🔁 every day")
    assert path.read_bytes() == before and task == snapshot


@pytest.mark.parametrize("subtask", [False, True])
def test_creation_validates_inline_and_explicit_repeat_before_write(tmp_path, subtask):
    parent = tm.add_task(tmp_path, "Parent")
    create = (lambda *args, **kwargs: tm.add_subtask(tmp_path, parent, *args, **kwargs)) if subtask else (
        lambda *args, **kwargs: tm.add_task(tmp_path, *args, **kwargs))
    before = (tmp_path / parent.file).read_bytes()
    for text, kwargs in [("Bad 🔁 every day", {}), ("Bad 📅 2026-09-06 🔁 nonsense", {}),
                         ("Bad", {"due": DAY, "recurrence": "nonsense"})]:
        with pytest.raises(ValueError):
            create(text, **kwargs)
        assert (tmp_path / parent.file).read_bytes() == before
    task = create("New 📅 2026-09-06 🔁 daily")
    assert task.recurrence == "every day" and task.anchor and task.anchor != parent.anchor
    cleared = create("One-off 📅 2026-09-06 🔁 every day", due=None, recurrence="clear")
    assert cleared.recurrence == "" and cleared.due is None


def test_completion_follows_moved_id_and_rejects_stale_edits(tmp_path):
    path, task = recurring(tmp_path)
    path.write_bytes(b"# Prefix\n" + path.read_bytes())
    moved = tmp_path / "Moved.md"
    path.rename(moved)
    tm.complete(tmp_path, task, DAY)
    assert task.file == "Moved.md" and task.lineno == 3 and task.recurrence_next
    following = tm.resolve_task(tmp_path, task.recurrence_next)
    moved.write_bytes(moved.read_bytes().replace(b"Review", b"External edit", 1))
    before = moved.read_bytes()
    with pytest.raises(ValueError, match="changed"):
        tm.complete(tmp_path, following, DAY)
    assert moved.read_bytes() == before


def test_completion_cas_conflict_leaves_no_successor_or_handle_change(tmp_path, monkeypatch):
    path, task = recurring(tmp_path)
    before = path.read_bytes()
    snapshot = replace(task)
    original_write = tm._write_lines_atomic
    def external_edit(*args, **kwargs):
        path.write_bytes(before + b"External writer\n")
        return original_write(*args, **kwargs)
    monkeypatch.setattr(tm, "_write_lines_atomic", external_edit)
    with pytest.raises(ValueError, match="changed"):
        tm.complete(tmp_path, task, DAY)
    assert path.read_bytes() == before + b"External writer\n" and task == snapshot
    assert len(tm.load_all(tmp_path)) == 1


def test_completion_is_one_undo_redo_entry_with_exact_restoration(tmp_path):
    path, task = recurring(tmp_path, suffix="  Keep note.\n  - [ ] Child\n", newline="\r\n")
    before = path.read_bytes()
    history = History(tmp_path)
    with history.record("Complete", [task.file]):
        tm.complete(tmp_path, task, DAY)
    after = path.read_bytes()
    next_id = task.recurrence_next
    history.undo()
    assert path.read_bytes() == before and not history.can_undo
    history.redo()
    assert path.read_bytes() == after and tm.resolve_task(tmp_path, next_id).open


def test_legacy_task_completion_returns_source_and_new_task_has_id(tmp_path):
    path, task = write_task(tmp_path, "- [ ] Legacy 🔁 every day 📅 2026-09-06")
    tm.complete(tmp_path, task, DAY)
    assert task.lineno == 2 and task.done and task.anchor == "" and task.recurrence_next
    following = tm.resolve_task(tmp_path, task.recurrence_next)
    assert following.lineno == 1 and following.open and following.anchor
    saved = path.read_bytes()
    tm.complete(tmp_path, task, DAY)
    assert path.read_bytes() == saved and not saved.endswith(b"\n")
