"""Small, explicit Obsidian-style recurrence grammar and calendar arithmetic."""
from __future__ import annotations

import calendar
from dataclasses import dataclass
import datetime as dt
import re


WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_DAYS = {name: i for i, name in enumerate(WEEKDAYS)}
_DAYS.update({name[:3]: i for i, name in enumerate(WEEKDAYS)})
_ALIASES = {"daily": "every day", "weekly": "every week", "monthly": "every month",
            "yearly": "every year", "weekdays": "every weekday"}


@dataclass(frozen=True, slots=True)
class Rule:
    kind: str
    interval: int = 1
    weekdays: tuple[int, ...] = ()
    month_day: str = ""
    when_done: bool = False

    @property
    def text(self) -> str:
        if self.kind == "weekday":
            result = "every weekday"
        elif self.weekdays:
            result = "every week on " + ", ".join(WEEKDAYS[day] for day in self.weekdays)
        elif self.month_day:
            result = "every month on the " + ("1st" if self.month_day == "first" else "last")
        elif self.interval == 1:
            result = f"every {self.kind}"
        else:
            result = f"every {self.interval} {self.kind}s"
        return result + (" when done" if self.when_done else "")


def parse_rule(text: str) -> Rule:
    """Parse only supported rules; caller retains unsupported Markdown verbatim."""
    if not isinstance(text, str):
        raise ValueError("Repeat must be text")
    value = re.sub(r"\s+", " ", text.strip().removeprefix("🔁").strip()).lower()
    when_done = value.endswith(" when done")
    if when_done:
        value = value[:-len(" when done")].strip()
    value = _ALIASES.get(value, value)
    if value == "every weekday":
        return Rule("weekday", when_done=when_done)
    if value in ("every month on the 1st", "every month on the last"):
        return Rule("month", month_day="first" if value.endswith("1st") else "last", when_done=when_done)
    days = re.fullmatch(r"every week on (.+)", value)
    if days:
        names = [name.strip() for name in days[1].split(",")]
        if all(name in _DAYS for name in names):
            return Rule("week", weekdays=tuple(sorted({_DAYS[name] for name in names})), when_done=when_done)
    ordinary = re.fullmatch(r"every (?:(\d+) )?(day|days|week|weeks|month|months|year)", value)
    if ordinary:
        count, unit = ordinary.groups()
        if count is None and unit.endswith("s"):
            ordinary = None
        elif unit == "year" and count not in (None, "1"):
            ordinary = None
        else:
            interval = int(count) if count else 1
            if interval > 0:
                return Rule(unit.rstrip("s"), interval, when_done=when_done)
    if "31st" in value:
        raise ValueError("Use 'every month on the last' for month end")
    raise ValueError("Unsupported repeat rule. Choose a preset or use a supported 'every …' rule")


def normalize_rule(text: str) -> str:
    """Canonical supported rule, or empty text for none/clear/blank."""
    if not isinstance(text, str):
        raise ValueError("Repeat must be text")
    if text.strip().lower() in ("", "none", "clear"):
        return ""
    return parse_rule(text).text


def _month(base: dt.date, amount: int, day: int | None = None) -> dt.date:
    index = base.year * 12 + base.month - 1 + amount
    year, month = divmod(index, 12)
    month += 1
    if not 1 <= year <= 9999:
        raise ValueError("Calendar overflow")
    return dt.date(year, month, min(day or base.day, calendar.monthrange(year, month)[1]))


def next_date(rule: Rule | str, reference: dt.date,
              completion: dt.date | None = None) -> dt.date:
    """Return one strictly later occurrence from the old date or completion day.

    Ordinary rules advance one interval even after a late completion; they do
    not skip overdue occurrences. Calendar months/years clamp missing days.
    """
    rule = parse_rule(rule) if isinstance(rule, str) else rule
    base = (completion or dt.date.today()) if rule.when_done else reference
    if not isinstance(base, dt.date) or isinstance(base, dt.datetime):
        raise ValueError("A recurrence reference must be a calendar date")
    try:
        if rule.kind == "weekday":
            result = base + dt.timedelta(days=1)
            while result.weekday() >= 5:
                result += dt.timedelta(days=1)
            return result
        if rule.weekdays:
            distance = min((weekday - base.weekday()) % 7 or 7 for weekday in rule.weekdays)
            return base + dt.timedelta(days=distance)
        if rule.month_day:
            day = 1 if rule.month_day == "first" else calendar.monthrange(base.year, base.month)[1]
            candidate = base.replace(day=day)
            if candidate > base:
                return candidate
            following = _month(base, 1, 1)
            return following if rule.month_day == "first" else following.replace(
                day=calendar.monthrange(following.year, following.month)[1])
        if rule.kind == "day":
            return base + dt.timedelta(days=rule.interval)
        if rule.kind == "week":
            return base + dt.timedelta(weeks=rule.interval)
        if rule.kind == "month":
            return _month(base, rule.interval)
        if rule.kind == "year":
            return _month(base, 12)
    except (OverflowError, ValueError):
        raise ValueError("Next occurrence is outside the supported calendar") from None
    raise ValueError("Unsupported repeat rule")
