"""Taskman core: markdown-native task parser + file store (stdlib only).

Source of truth is plain markdown checkboxes, Obsidian / Task Genius compatible:

    - [ ] Description 🔺 📅 2026-08-26 #tag
    - [x] Done task ✅ 2026-08-27

Sub-tasks are indented checkboxes (standard markdown nesting, renders as
nested lists in Obsidian). A task's *note* is any indented plain text right
under its line — Obsidian shows it as the item's paragraph:

    - [ ] Parent task 📅 2026-09-10
      Notes about the task go here, as many lines as you like.
      - [ ] Sub-task one
      - [ ] Sub-task two 🔼

This module deliberately has ZERO third-party dependencies so it stays fast,
testable, and easy to edit. The Textual UI in app.py imports from here; the
``--plain`` CLI below works with stock Python (screen-reader / pipe friendly).

Sections:
  1. Constants (priorities, emoji, views)
  2. Task dataclass
  3. Line parser  (parse_task_line)
  4. Vault scan   (iter_markdown_files, parse_file, load_all)
  5. Views/filters (inbox, today, overdue, next7, ...)
  6. Mutations    (toggle, set_due, set_priority, set_project, set_note, edit, delete, add, ...)
  7. Plain CLI    (``python -m tui --plain today``)
  8. Hierarchy    (depth/parent, children, subtree ops)
  9. Layout       (tree_rows + sections: what the TUI draws, unit-testable)

Projects: a task belongs to a project via a ``#project/Name`` tag, or by
living in ``Projects/Name.md`` (same rule Task Genius uses). ``set_project``
swaps the tag; ``ensure_project_file`` gives a new project its note.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import sys
import tempfile
import uuid

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable, Literal

from . import recurrence as recurrence_rules
from .vaults import (discover_vault, has_vault_marker, is_legacy_vault, is_linked,
                     normalize_folder, vault_path)

# ---------------------------------------------------------------------------
# 1. Constants
# ---------------------------------------------------------------------------

# Priority emoji -> (level, name). Higher level = more urgent.
PRIORITIES: dict[str, tuple[int, str]] = {
    "🔺": (5, "highest"),
    "⏫": (4, "high"),
    "🔼": (3, "medium"),
    "🔽": (2, "low"),
    "⏬": (1, "lowest"),
}
PRIORITY_BY_LEVEL: dict[int, str] = {lvl: emo for emo, (lvl, _) in PRIORITIES.items()}

# Checkbox status characters (Task Genius defaults) -> human word.
# ' ' open, '/' in progress, 'x' done, '-' cancelled, '>' forwarded, '?' question.
STATUS_LABELS: dict[str, str] = {
    " ": "open",
    "/": "in progress",
    "x": "done",
    "X": "done",
    "-": "cancelled",
    ">": "forwarded",
    "?": "question",
}
# Order offered by the status picker (char, label).
STATUS_CHOICES: tuple[tuple[str, str], ...] = (
    (" ", "open"), ("/", "in progress"), ("x", "done"),
    (">", "forwarded"), ("-", "cancelled"), ("?", "question"),
)

# Date markers used by Task Genius / Tasks plugin.
DUE, START, SCHED, DONE, CANCEL = "📅", "🛫", "⏳", "✅", "❌"

# Checkbox line, e.g. "  - [x] text" or "1. [/] text". Group 3 is status char.
TASK_LINE_RE = re.compile(r"^(\s*)(?:[-*+]|\d+[.)])\s+\[([^\]]?)\]\s*(.*)$")

# Emoji + YYYY-MM-DD (+ optional HH:MM which we keep but ignore for filtering).
def _date_re(emoji: str) -> "re.Pattern[str]":
    return re.compile(re.escape(emoji) + r"\s*(\d{4}-\d{2}-\d{2})(?:\s+\d{1,2}:\d{2})?")


DUE_RE, START_RE, SCHED_RE, DONE_RE, CANCEL_RE = (
    _date_re(DUE),
    _date_re(START),
    _date_re(SCHED),
    _date_re(DONE),
    _date_re(CANCEL),
)
DATE_RES = (DUE_RE, START_RE, SCHED_RE, DONE_RE, CANCEL_RE)

TAG_RE = re.compile(r"(?<![\w/])#([\w\-./]+)")
TASK_ANCHOR_RE = re.compile(r"<!-- taskman:id=([0-9a-f]{32}) -->")
TASK_ANCHOR_VALUE_RE = re.compile(r"[0-9a-f]{32}")
RECURRENCE_NEXT_RE = re.compile(r"<!-- taskman:next=([0-9a-f]{32}) -->")
RECURRENCE_RE = re.compile(
    r"🔁\s*([^📅🛫⏳✅❌🔺⏫🔼🔽⏬]*?)(?=📅|🛫|⏳|✅|❌|🔺|⏫|🔼|🔽|⏬|(?<![\w/])#[\w\-./]+|<!--|$)"
)

# Folders never scanned (plugin code, history, templates with placeholder boxes).
EXCLUDE_DIRS = {
    ".obsidian", ".git", ".taskman", "docs", "scripts", "Templates", "tui",
    "__pycache__", "node_modules", "vendor", "venv", ".venv", "env",
    "site-packages", "dist", "build", "artifacts", "coverage", "htmlcov",
    "target", "bin", "obj", ".tox", ".nox", ".cache", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".next", ".nuxt",
}
# Top-level folders that hold real notes. Root loose *.md files are also scanned
# (e.g. QuickCapture.md) so captures are never missed.
SCAN_DIRS = ("Tasks", "Projects", "Notes", "Documentation")

ViewName = Literal[
    "inbox", "now", "today", "overdue", "next7", "all", "completed", "priority", "project", "search"
]

VIEWS: tuple[tuple[str, str], ...] = (
    ("inbox", "Inbox  — no due or scheduled date"),
    ("now", "Now"),
    ("overdue", "Overdue"),
    ("next7", "Next 7 days"),
    ("all", "All open"),
    ("completed", "Completed"),
    ("priority", "High priority"),
)


# ---------------------------------------------------------------------------
# 2. Task dataclass
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class Task:
    """One markdown checkbox. ``file`` is vault-relative, ``lineno`` 1-based."""

    file: str
    lineno: int
    status: str = " "          # raw char inside [ ]; done <=> 'x'/'X'
    description: str = ""
    priority: int = 0          # 0 = none, else 1..5 (see PRIORITIES)
    due: "dt.date | None" = None
    start: "dt.date | None" = None
    scheduled: "dt.date | None" = None
    done_date: "dt.date | None" = None
    cancelled_date: "dt.date | None" = None   # ❌ date (kept verbatim on rewrite)
    tags: tuple[str, ...] = ()
    indent: str = ""
    bullet: str = "-"
    raw: str = ""
    depth: int = 0            # nesting level; 0 = top-level (set by parse_file)
    parent_lineno: int = 0    # lineno of parent task in same file; 0 = none
    note: str = ""            # indented plain text right under the line (see set_note)
    note_end: int = 0         # lineno of the note's last line; 0 = no note lines
    context: bool = False     # transient: True when shown only as a matched
                              # task's ancestor (set by view_tasks, never saved)
    anchor: str = ""          # durable identity; new tasks get one, legacy tasks lazily
    recurrence: str = ""      # supported or handwritten rule, without the 🔁 marker
    recurrence_next: str = "" # successor anchor; preserved on reopen to avoid duplicates

    # -- derived ------------------------------------------------------------
    @property
    def done(self) -> bool:
        return self.status.lower() == "x"

    @property
    def cancelled(self) -> bool:
        return self.status == "-"

    @property
    def closed(self) -> bool:
        """Done or cancelled — nothing left to do."""
        return self.done or self.cancelled

    @property
    def open(self) -> bool:
        return not self.closed

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, f"status [{self.status}]")

    @property
    def project(self) -> str:
        """Display project: the first ``#project/X`` tag, else the note's
        stem when the task lives in ``Projects/`` (Task Genius' rule)."""
        for g in self.tags:
            if g.lower().startswith("project/"):
                return g[len("project/"):]
        parts = Path(self.file).parts
        if len(parts) >= 2 and parts[0] == "Projects":
            return Path(self.file).stem
        return ""

    @property
    def plain_tags(self) -> tuple[str, ...]:
        """Tags minus ``project/...`` (those are shown as the project)."""
        return tuple(g for g in self.tags if not g.lower().startswith("project/"))

    @property
    def block_end(self) -> int:
        """Last line of the task's own block (its line, or its last note line)."""
        return max(self.lineno, self.note_end)

    @property
    def priority_name(self) -> str:
        for emo, (lvl, name) in PRIORITIES.items():
            if lvl == self.priority:
                return name
        return "none"

    @property
    def priority_emoji(self) -> str:
        return PRIORITY_BY_LEVEL.get(self.priority, "")

    @property
    def id(self) -> str:
        return f"{self.file}:{self.lineno}"

    def short(self, day: "dt.date | None" = None) -> str:
        """One plain-text line for screen readers / pipes (no color).

        Pass ``day`` to have overdue tasks say so in words.
        """
        box = "x" if self.done else self.status if self.status != " " else " "
        parts = [f"[{box}]", self.description or "(no text)"]
        if self.status not in (" ", "x", "X"):
            parts.append(f"[{self.status_label}]")
        if self.priority:
            parts.append(f"[{self.priority_name}]")
        if self.due:
            parts.append(f"due {self.due.isoformat()}")
            if day and self.open and self.due < day:
                parts.append("OVERDUE")
        if self.scheduled:
            parts.append(f"scheduled {self.scheduled.isoformat()}")
        if self.recurrence:
            parts.append(f"repeat {self.recurrence}")
        for t in self.tags:
            parts.append(f"#{t}")
        parts.append(f"({self.id})")
        return "  " * self.depth + " ".join(parts)


# ---------------------------------------------------------------------------
# 3. Line parser
# ---------------------------------------------------------------------------

def _ymd(text: str) -> "dt.date | None":
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        return None


def _first_date(pattern: "re.Pattern[str]", body: str) -> "dt.date | None":
    m = pattern.search(body)
    return _ymd(m.group(1)) if m else None


def parse_task_line(line: str, file: str = "", lineno: int = 0) -> "Task | None":
    """Parse one line; return None when it is not a checkbox task."""
    m = TASK_LINE_RE.match(line.rstrip("\n"))
    if not m:
        return None
    indent, status, body = m.group(1), m.group(2), m.group(3).strip()
    anchors = set(TASK_ANCHOR_RE.findall(body))
    # Conflicting markers are ambiguous: leave them visible and untouched rather
    # than choosing one identity. Linking rejects this case before writing.
    anchor = next(iter(anchors)) if len(anchors) == 1 else ""
    if anchor:
        body = TASK_ANCHOR_RE.sub(" ", body)
    successors = set(RECURRENCE_NEXT_RE.findall(body))
    recurrence_next = next(iter(successors)) if len(successors) == 1 else ""
    if recurrence_next:
        body = RECURRENCE_NEXT_RE.sub(" ", body)
    repeats = list(RECURRENCE_RE.finditer(body))
    recurrence = ""
    if repeats:
        # Keep unsupported rules as data so normal edits cannot silently lose them.
        recurrence = " 🔁 ".join(match[1].strip() for match in repeats)
        if recurrence:
            body = RECURRENCE_RE.sub(" ", body)
    bullet = "-"  # normalized on write; original marker is not significant
    stripped = line.lstrip()
    if stripped[:2] in ("* ", "+ ") or (stripped and stripped[0].isdigit()):
        bullet = stripped[0] if stripped[0] in "*+" else "-"

    priority = 0
    for emo, (lvl, _name) in PRIORITIES.items():
        if emo in body:
            priority = max(priority, lvl)

    task = Task(
        file=file,
        lineno=lineno,
        status=status,
        priority=priority,
        due=_first_date(DUE_RE, body),
        start=_first_date(START_RE, body),
        scheduled=_first_date(SCHED_RE, body),
        done_date=_first_date(DONE_RE, body),
        cancelled_date=_first_date(CANCEL_RE, body),
        tags=tuple(TAG_RE.findall(body)),
        indent=indent,
        bullet=bullet,
        raw=line.rstrip("\n"),
        anchor=anchor,
        recurrence=recurrence,
        recurrence_next=recurrence_next,
    )
    # Description = body minus priority emoji, date tokens, tags.
    desc = body
    for emo in PRIORITIES:
        desc = desc.replace(emo, " ")
    for rx in DATE_RES:
        desc = rx.sub(" ", desc)
    desc = TAG_RE.sub(" ", desc)
    task.description = re.sub(r"\s+", " ", desc).strip()
    return task


def find_task_by_anchor(tasks: Iterable[Task], anchor: str) -> "Task | None":
    """Resolve a valid anchor, or None if missing; duplicates raise ValueError.

    An empty/invalid anchor never matches unanchored tasks. A line containing
    conflicting markers has no usable anchor and must be repaired explicitly.
    """
    if not TASK_ANCHOR_VALUE_RE.fullmatch(anchor):
        return None
    matches = []
    for task in tasks:
        raw_anchors = set(TASK_ANCHOR_RE.findall(task.raw))
        if task.anchor == anchor or anchor in raw_anchors:
            if len(raw_anchors | ({task.anchor} if task.anchor else set())) > 1:
                raise ValueError(f"Conflicting task anchors include: {anchor}")
            matches.append(task)
    if len(matches) > 1:
        raise ValueError(f"Duplicate task anchor: {anchor}")
    return matches[0] if matches else None


# ---------------------------------------------------------------------------
# 4. Vault scan
# ---------------------------------------------------------------------------

def vault_root(explicit: "str | Path | None" = None) -> Path:
    """Resolve a chosen, recent, or recognized current vault, never app code."""
    root = discover_vault(explicit)
    if root is None:
        raise ValueError("No vault selected. Use taskman --vault PATH or open Taskman to choose a folder.")
    return root


def iter_markdown_files(root: "str | Path") -> list[Path]:
    """Find notes without traversing links, dependency trees, or app metadata.

    Original structured vaults keep their established scope until explicitly
    set up with a marker. Any other folder scans its ordinary subfolders too.
    """
    root = normalize_folder(root)
    found: list[Path] = []
    legacy = is_legacy_vault(root) and not has_vault_marker(root)
    excluded = {name.casefold() for name in EXCLUDE_DIRS}
    if not legacy:
        excluded -= {"docs", "scripts", "tui"}  # ordinary notes may live here

    def fail(error: OSError) -> None:
        raise error

    for directory, dirs, files in os.walk(root, followlinks=False, onerror=fail):
        current = Path(directory)
        dirs[:] = sorted(name for name in dirs
                         if not name.startswith(".") and name.casefold() not in excluded
                         and not is_linked(current / name)
                         and (not legacy or current != root or name in SCAN_DIRS))
        for name in files:
            path = current / name
            if (path.suffix.casefold() == ".md" and not name.startswith(".")
                    and not is_linked(path) and path.is_file()):
                found.append(path)
    return sorted(found, key=lambda path: path.relative_to(root).as_posix())


def parse_file(path: "str | Path", root: "str | Path") -> list[Task]:
    """Parse body checkboxes, excluding metadata/comments/code; keep line numbers."""
    root = Path(root)
    path = Path(path)
    try:
        rel = path.relative_to(root).as_posix()
    except ValueError:
        rel = path.name
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        try:
            text = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            return []
    tasks: list[Task] = []
    in_fence = False  # skip ```code blocks``` — docs show example checkboxes
    frontmatter = ""
    in_comment = False
    # Indent stack for hierarchy: (indent_width, lineno). A task nests under
    # the nearest previous task with a *smaller* indent, whatever indent
    # width the file uses (2 spaces, 4 spaces, tabs all work).
    stack: list[tuple[int, int]] = []
    # Note collection: plain lines indented deeper than the last task (blank
    # lines allowed inside) belong to that task until anything else shows up.
    last: "Task | None" = None
    buf: list[str] = []

    def flush() -> None:
        nonlocal last, buf
        if last is not None:
            while buf and not buf[-1].strip():   # trailing blanks are spacing
                buf.pop()
            if buf:
                last.note = _dedent_note(buf)
                last.note_end = last.lineno + len(buf)
        last, buf = None, []

    for i, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if i == 1:
            stripped = stripped.lstrip("\ufeff")
        if i == 1 and stripped in ("---", "+++"):
            frontmatter = stripped
            continue
        if frontmatter:
            if stripped == frontmatter or (frontmatter == "---" and stripped == "..."):
                frontmatter = ""
            continue
        if not in_comment and (stripped.startswith("```") or stripped.startswith("~~~")):
            flush()
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        # Standalone HTML comments may contain arbitrary text, including invalid
        # note metadata and checkbox examples. Keep inline task anchors intact.
        comment_line = in_comment or stripped.startswith("<!--")
        if comment_line:
            for marker in re.finditer(r"<!--|-->", line):
                in_comment = marker.group() == "<!--"
        t = None if comment_line else parse_task_line(line, file=rel, lineno=i)
        if t is not None:
            flush()
            width = len(t.indent.expandtabs(4))
            while stack and stack[-1][0] >= width:
                stack.pop()
            t.depth = len(stack)
            t.parent_lineno = stack[-1][1] if stack else 0
            stack.append((width, i))
            tasks.append(t)
            last = t
            continue
        if last is not None:
            if not stripped or _indent_width(line) > len(last.indent.expandtabs(4)):
                buf.append(line)
                continue
            flush()
    flush()
    return tasks


def _indent_width(line: str) -> int:
    expanded = line.expandtabs(4)
    return len(expanded) - len(expanded.lstrip())


def _dedent_note(raw: list[str]) -> str:
    """Note text without the file's indentation (blank lines kept blank)."""
    nonblank = [ln for ln in raw if ln.strip()]
    common = os.path.commonprefix([ln[: len(ln) - len(ln.lstrip())] for ln in nonblank])
    return "\n".join(ln[len(common):].rstrip() if ln.strip() else "" for ln in raw)


class Store:
    """In-memory task list with mtime cache. Call refresh() to rescan."""

    def __init__(self, root: "str | Path") -> None:
        self.root = normalize_folder(root)
        self.tasks: list[Task] = []
        self._mtimes: dict[str, float] = {}

    def refresh(self, force: bool = False) -> list[Task]:
        files = iter_markdown_files(self.root)
        if not force and self.tasks:
            # Fast path: skip rescan when nothing changed.
            current = {}
            changed = False
            for p in files:
                try:
                    mt = p.stat().st_mtime
                except OSError:
                    continue
                rel = p.relative_to(self.root).as_posix()
                current[rel] = mt
                if self._mtimes.get(rel) != mt:
                    changed = True
            if not changed and set(current) == set(self._mtimes):
                return self.tasks
        out: list[Task] = []
        mtimes: dict[str, float] = {}
        for p in files:
            try:
                mtimes[p.relative_to(self.root).as_posix()] = p.stat().st_mtime
            except OSError:
                continue
            out.extend(parse_file(p, self.root))
        # Stable, useful order: due date first (undated last), then priority.
        out.sort(key=lambda t: (t.due or dt.date.max, -t.priority, t.file, t.lineno))
        self.tasks = out
        self._mtimes = mtimes
        return out

    def projects(self) -> list[str]:
        """Project names from #project/xxx tags + Projects/*.md stems."""
        return project_names(self.tasks, self.root)


def project_names(tasks: Iterable[Task], root: "str | Path | None" = None) -> list[str]:
    """Distinct project names (case-insensitive, first spelling wins)."""
    seen: dict[str, str] = {}
    for t in tasks:
        name = t.project
        if name and name.casefold() not in seen:
            seen[name.casefold()] = name
    if root is not None:
        proj_dir = Path(root) / "Projects"
        if proj_dir.is_dir() and not is_linked(proj_dir):
            for p in sorted(proj_dir.glob("*.md")):
                if p.is_file() and not is_linked(p):
                    seen.setdefault(p.stem.casefold(), p.stem)
    return sorted(seen.values(), key=str.casefold)


def project_counts(tasks: Iterable[Task]) -> dict[str, int]:
    """Open-task count per project (keys are casefolded names)."""
    out: dict[str, int] = {}
    for t in tasks:
        if t.open and t.project:
            out[t.project.casefold()] = out.get(t.project.casefold(), 0) + 1
    return out


def load_all(root: "str | Path") -> list[Task]:
    return Store(root).refresh(force=True)


# ---------------------------------------------------------------------------
# 5. Views / filters (pure functions over a task list)
# ---------------------------------------------------------------------------

def today(date: "dt.date | None" = None) -> dt.date:
    return date or dt.date.today()


def parse_date(raw: str, day: "dt.date | None" = None) -> "dt.date | None":
    """Shared Due/Scheduled input language for the TUI and dependency-free CLI.

    A named weekday means its next occurrence, including next week when it
    names today. Empty text and clear/none/- remove the date.
    """
    value = raw.strip().casefold()
    day = today(day)
    if value in ("", "clear", "none", "-"):
        return None
    try:
        if value in ("today", "tod"):
            return day
        if value in ("tomorrow", "tom"):
            return day + dt.timedelta(days=1)
        weekdays = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
        for index, name in enumerate(weekdays):
            if value in (name, name[:3]):
                return day + dt.timedelta(days=(index - day.weekday()) % 7 or 7)
        if re.fullmatch(r"\+[0-9]+", value):
            return day + dt.timedelta(days=int(value[1:]))
        if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            return dt.date.fromisoformat(value)
    except (ValueError, OverflowError):
        pass
    raise ValueError("Date not understood. Use today, tomorrow, mon, +7, YYYY-MM-DD, or clear.")


def in_now(t: Task, day: dt.date) -> bool:
    """Work needing attention: deadlines win over postponing scheduled work."""
    return t.open and t.status != ">" and (
        (t.due is not None and t.due <= day)
        or (t.scheduled is not None and t.scheduled <= day)
    )


def now_date(t: Task, day: dt.date) -> tuple[str, "dt.date | None"]:
    """Label and date explaining a Now match; fall back for context ancestors."""
    if t.due is not None and t.due <= day:
        return "due", t.due
    if t.scheduled is not None and t.scheduled <= day:
        return "scheduled", t.scheduled
    return ("due", t.due) if t.due is not None else ("scheduled", t.scheduled)


def is_inbox(t: Task) -> bool:
    return t.open and t.due is None and t.scheduled is None


def view_tasks(tasks: Iterable[Task], view: str,
               day: "dt.date | None" = None, project: str = "",
               query: str = "", with_parents: bool = True) -> list[Task]:
    """Filter tasks for a named view (+ optional project / search query).

    With ``with_parents`` (default), ancestors of matches are included too so
    a sub-task is never shown orphaned; they are flagged ``context=True``
    (dimmed in the TUI) to distinguish them from real matches.
    """
    view = "now" if view == "today" else view
    task_list = list(tasks)
    for t in task_list:
        t.context = False
    day = today(day)
    q = query.strip().casefold()
    out: list[Task] = []
    for t in task_list:
        if view == "inbox" and not is_inbox(t):
            continue
        elif view == "now" and not in_now(t, day):
            continue
        elif view == "overdue" and not (t.open and t.due is not None and t.due < day):
            continue
        elif view == "next7" and not (
            t.open and t.due is not None and day <= t.due <= day + dt.timedelta(days=7)
        ):
            continue
        elif view == "all" and not t.open:
            continue
        elif view == "completed" and not t.closed:
            continue
        elif view == "priority" and not (t.open and t.priority >= 4):
            continue
        elif view == "project" and project:
            want = project.casefold()
            tags = [g.casefold() for g in t.tags]
            stems = project_stem_tags(t)
            if want not in tags and f"project/{want}" not in tags and want not in stems:
                continue
        elif view not in ("inbox", "now", "overdue", "next7", "all",
                          "completed", "priority", "project", "search", "alltasks"):
            raise ValueError(f"unknown view: {view!r}")
        if q and q not in f"{t.description} {t.file} {' '.join(t.tags)}".casefold():
            continue
        out.append(t)
    if with_parents and out:
        by_fl = {(t.file, t.lineno): t for t in task_list}
        seen = {t.id for t in out}
        for t in list(out):
            cur = t
            visited = {t.id}
            while cur.parent_lineno:
                p = by_fl.get((cur.file, cur.parent_lineno))
                if p is None or p.id in seen or p.id in visited:
                    break
                visited.add(p.id)
                seen.add(p.id)
                p.context = True
                out.append(p)
                cur = p
        # Keep vault order (due-sorted); parents usually already precede kids.
        order = {t.id: i for i, t in enumerate(task_list)}
        out.sort(key=lambda t: order.get(t.id, len(order)))
    return out


def project_stem_tags(t: Task) -> set[str]:
    """Match tasks that live in Projects/<name>.md even without a tag."""
    stem = Path(t.file).stem.casefold()
    if Path(t.file).as_posix().startswith("Projects/"):
        return {stem}
    return set()


def counts(tasks: Iterable[Task], day: "dt.date | None" = None) -> dict[str, int]:
    ts = list(tasks)
    kw = {"with_parents": False}  # counts are real matches, never context rows
    return {
        "inbox": len(view_tasks(ts, "inbox", day, **kw)),
        "now": len(view_tasks(ts, "now", day, **kw)),
        "today": len(view_tasks(ts, "now", day, **kw)),  # legacy API alias
        "overdue": len(view_tasks(ts, "overdue", day, **kw)),
        "next7": len(view_tasks(ts, "next7", day, **kw)),
        "all": len(view_tasks(ts, "all", day, **kw)),
        "completed": len(view_tasks(ts, "completed", day, **kw)),
        "priority": len(view_tasks(ts, "priority", day, **kw)),
    }


# ---------------------------------------------------------------------------
# 8. Hierarchy — sub-tasks are indented checkboxes in the same file.
#    depth / parent_lineno are recomputed by parse_file on every scan, so
#    helpers below take any task list and work from those two fields.
# ---------------------------------------------------------------------------

#: Indent added per nesting level for new sub-tasks (Obsidian-friendly).
INDENT_STEP = "  "


def _file_ordered(tasks: Iterable[Task], file: str) -> list[Task]:
    """Tasks of one file in document order (parents precede children)."""
    return sorted((t for t in tasks if t.file == file), key=lambda t: t.lineno)


def children_of(tasks: Iterable[Task], t: Task) -> list[Task]:
    """Direct sub-tasks of ``t`` in document order."""
    return [x for x in _file_ordered(tasks, t.file) if x.parent_lineno == t.lineno]


def ancestors_of(tasks: Iterable[Task], t: Task) -> list[Task]:
    """Parent chain, nearest first. Guards against corrupt line numbers."""
    by_fl = {(x.file, x.lineno): x for x in tasks if x.file == t.file}
    chain: list[Task] = []
    seen = {t.lineno}
    cur = t
    while cur.parent_lineno and cur.parent_lineno not in seen:
        p = by_fl.get((cur.file, cur.parent_lineno))
        if p is None:
            break
        chain.append(p)
        seen.add(p.lineno)
        cur = p
    return chain


def descendants_of(tasks: Iterable[Task], t: Task) -> list[Task]:
    """All sub-tasks under ``t`` (any depth) in document order."""
    ordered = _file_ordered(tasks, t.file)
    by_fl = {(x.file, x.lineno): x for x in ordered}
    out: list[Task] = []
    for x in ordered:
        if x.lineno <= t.lineno:
            continue
        cur_lineno, seen, is_desc = x.parent_lineno, {x.lineno}, False
        while cur_lineno and cur_lineno not in seen:
            if cur_lineno == t.lineno:
                is_desc = True
                break
            seen.add(cur_lineno)
            parent = by_fl.get((x.file, cur_lineno))
            cur_lineno = parent.parent_lineno if parent else 0
        if is_desc:
            out.append(x)
    return out


def subtree(tasks: Iterable[Task], t: Task) -> list[Task]:
    """``t`` plus all its descendants, in document order."""
    return [t] + descendants_of(tasks, t)


def last_child_map(tasks: Iterable[Task]) -> dict[str, bool]:
    """Map task id -> True when it is the last child of its parent.

    Used by the TUI to draw └ (last) vs ├ (more siblings follow) guides.
    """
    last_seen: dict[tuple[str, int], str] = {}  # (file, parent) -> task id
    for t in tasks:
        last_seen[(t.file, t.parent_lineno)] = t.id
    return {t.id: (last_seen.get((t.file, t.parent_lineno)) == t.id) for t in tasks}


def open_subtask_count(tasks: Iterable[Task], t: Task) -> tuple[int, int]:
    """(open_descendants, total_descendants) for detail lines / announces."""
    desc = descendants_of(tasks, t)
    return sum(1 for d in desc if d.open), len(desc)


def child_index(tasks: Iterable[Task]) -> dict[tuple[str, int], list[Task]]:
    """(file, parent_lineno) -> direct children in document order.

    One pass over the vault; the TUI uses it for ``[done/total]`` badges.
    """
    idx: dict[tuple[str, int], list[Task]] = {}
    for t in sorted(tasks, key=lambda x: (x.file, x.lineno)):
        idx.setdefault((t.file, t.parent_lineno), []).append(t)
    return idx


def progress_of(index: dict[tuple[str, int], list[Task]], t: Task) -> tuple[int, int]:
    """(closed_children, total_children) using a ``child_index``."""
    kids = index.get((t.file, t.lineno), [])
    return sum(1 for k in kids if k.closed), len(kids)


# ---------------------------------------------------------------------------
# 9. Display layout — trees + sections (pure, so the TUI stays dumb)
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class Node:
    """One display row: a task, how deep it sits, and its tree guide."""

    task: Task
    depth: int      # display depth (0 = root of its displayed tree)
    prefix: str     # e.g. "│ ├ " — drawn before the description


@dataclass(slots=True)
class Section:
    key: str        # bucket id, e.g. "overdue"
    title: str      # human title, e.g. "Overdue"
    nodes: list[Node]

    @property
    def count(self) -> int:
        """Real matches only — context ancestors do not count."""
        return sum(1 for n in self.nodes if not n.task.context)


# (key, title) in display order for open tasks, keyed by due date.
DUE_BUCKETS: tuple[tuple[str, str], ...] = (
    ("overdue", "Overdue"), ("today", "Today"), ("tomorrow", "Tomorrow"),
    ("week", "Next 7 days"), ("later", "Later"), ("nodate", "No due date"),
)
# Same idea for the Completed view, keyed by completion date.
DONE_BUCKETS: tuple[tuple[str, str], ...] = (
    ("today", "Completed today"), ("yesterday", "Yesterday"),
    ("week", "Last 7 days"), ("older", "Older"), ("nodate", "No completion date"),
)
NOW_BUCKETS: tuple[tuple[str, str], ...] = (
    ("overdue", "Overdue"), ("today", "Today"),
)


def now_bucket(t: Task, day: dt.date) -> int:
    """Only missed deadlines are overdue; all other Now work belongs to Today."""
    return 0 if t.due is not None and t.due < day else 1


def due_bucket(t: Task, day: dt.date) -> int:
    """Index into DUE_BUCKETS for an open task."""
    if t.due is None:
        return 5
    if t.due < day:
        return 0
    if t.due == day:
        return 1
    if t.due == day + dt.timedelta(days=1):
        return 2
    if t.due <= day + dt.timedelta(days=7):
        return 3
    return 4


def done_bucket(t: Task, day: dt.date) -> int:
    """Index into DONE_BUCKETS for a closed task."""
    ref = t.done_date or t.cancelled_date
    if ref is None:
        return 4
    if ref >= day:
        return 0
    if ref == day - dt.timedelta(days=1):
        return 1
    if ref >= day - dt.timedelta(days=7):
        return 2
    return 3


def tree_rows(rows: Iterable[Task]) -> list[list[Node]]:
    """Group displayed tasks into trees (one list of Nodes per root).

    Children follow their parent in document order, however the input was
    sorted. A task whose parent is not displayed becomes a root itself.
    Guides: ``├ `` more siblings follow, ``└ `` last child, ``│ `` an
    ancestor still has siblings below, two spaces otherwise.
    """
    rows = list(rows)
    shown = {t.id for t in rows}
    kids: dict[tuple[str, int], list[Task]] = {}
    roots: list[Task] = []
    for t in rows:
        if t.parent_lineno and f"{t.file}:{t.parent_lineno}" in shown:
            kids.setdefault((t.file, t.parent_lineno), []).append(t)
        else:
            roots.append(t)
    for lst in kids.values():
        lst.sort(key=lambda x: x.lineno)

    def push(stack: list, parent: Task, depth: int, guide: str) -> None:
        """Queue ``parent``'s children (reversed so pop() yields doc order)."""
        ch = kids.get((parent.file, parent.lineno), [])
        for i in range(len(ch) - 1, -1, -1):
            stack.append((ch[i], depth, guide, i == len(ch) - 1))

    trees: list[list[Node]] = []
    for root in roots:
        nodes = [Node(root, 0, "")]
        stack: list[tuple[Task, int, str, bool]] = []   # (task, depth, guide, is_last)
        push(stack, root, 1, "")
        while stack:
            t, depth, guide, last = stack.pop()
            nodes.append(Node(t, depth, guide + ("└ " if last else "├ ")))
            push(stack, t, depth + 1, guide + ("  " if last else "│ "))
        trees.append(nodes)
    return trees


def _tree_sort_key(nodes: list[Node], closed: bool, now: bool = False) -> tuple:
    """Order trees inside a section: most urgent first (newest done first)."""
    real = [n.task for n in nodes if not n.task.context] or [nodes[0].task]
    if closed:
        newest = max((t.done_date or t.cancelled_date or dt.date.min) for t in real)
        return (-newest.toordinal(), real[0].file, real[0].lineno)
    if now:
        priority = max(t.priority for t in real)
        earliest = min((date for t in real for date in (t.due, t.scheduled)
                        if date is not None), default=dt.date.max)
        return (-priority, earliest, real[0].file, real[0].lineno)
    due = min((t.due or dt.date.max) for t in real)
    prio = max(t.priority for t in real)
    return (due, -prio, real[0].file, real[0].lineno)


def sections(rows: Iterable[Task], view: str,
             day: "dt.date | None" = None) -> list[Section]:
    """Lay out view rows as titled sections of task trees.

    Now buckets by missed deadline versus all other attention-worthy work.
    Other open views bucket by due date (Overdue … No date); the Completed view
    buckets by completion date. A tree lands in the most urgent bucket of
    any *matched* task it contains, so a sub-task due today under an
    undated parent still shows under Today (parent dimmed as context).
    """
    day = today(day)
    closed = view == "completed"
    now = view in ("now", "today")
    buckets = DONE_BUCKETS if closed else NOW_BUCKETS if now else DUE_BUCKETS
    rank = done_bucket if closed else now_bucket if now else due_bucket
    grouped: dict[int, list[list[Node]]] = {}
    for tree in tree_rows(rows):
        real = [n.task for n in tree if not n.task.context] or [tree[0].task]
        grouped.setdefault(min(rank(t, day) for t in real), []).append(tree)
    out: list[Section] = []
    for i, (key, title) in enumerate(buckets):
        trees = grouped.get(i)
        if not trees:
            continue
        trees.sort(key=lambda ns: _tree_sort_key(ns, closed, now))
        out.append(Section(key, title, [n for ns in trees for n in ns]))
    return out


# ---------------------------------------------------------------------------
# 6. Mutations (read-modify-write one line; surrounding notes untouched)
# ---------------------------------------------------------------------------

def build_line(t: Task) -> str:
    """Rebuild the markdown line for a task (canonical, readable order)."""
    tokens: list[str] = []
    if t.recurrence:
        tokens.append(f"🔁 {t.recurrence}")
    if t.priority_emoji:
        tokens.append(t.priority_emoji)
    if t.start:
        tokens.append(f"{START} {t.start.isoformat()}")
    if t.scheduled:
        tokens.append(f"{SCHED} {t.scheduled.isoformat()}")
    if t.due:
        tokens.append(f"{DUE} {t.due.isoformat()}")
    if t.done_date:
        tokens.append(f"{DONE} {t.done_date.isoformat()}")
    if t.cancelled_date:
        tokens.append(f"{CANCEL} {t.cancelled_date.isoformat()}")
    for tag in t.tags:
        tokens.append(f"#{tag}")
    body = t.description
    if t.anchor:
        if not TASK_ANCHOR_VALUE_RE.fullmatch(t.anchor):
            raise ValueError("Task anchor must be 32 lowercase hexadecimal characters")
        body = re.sub(r"\s+", " ", TASK_ANCHOR_RE.sub(" ", body)).strip()
        tokens.append(f"<!-- taskman:id={t.anchor} -->")
    if t.recurrence_next:
        if not TASK_ANCHOR_VALUE_RE.fullmatch(t.recurrence_next):
            raise ValueError("Next occurrence anchor must be 32 lowercase hexadecimal characters")
        body = re.sub(r"\s+", " ", RECURRENCE_NEXT_RE.sub(" ", body)).strip()
        tokens.append(f"<!-- taskman:next={t.recurrence_next} -->")
    if tokens:
        body = f"{body} {' '.join(tokens)}" if body else " ".join(tokens)
    return f"{t.indent}{t.bullet} [{t.status}] {body}".rstrip()


# Kept here so the mutation machinery has one clearly bounded home. The core
# remains stdlib-only, including its cross-process writer coordination.
from contextlib import contextmanager
from threading import RLock, local
import stat
import time


_WRITER_LOCKS: dict[str, RLock] = {}
_WRITER_LOCKS_GUARD = RLock()
_WRITER_STATE = local()
_UNSET = object()


@contextmanager
def vault_write_lock(root: "str | Path"):
    """Serialize Taskman writers, reentrantly, in this process and across CLI/TUI.

    The OS releases the advisory lock if a process exits. External editors do
    not take this lock: byte checks before atomic replacement detect observed
    edits, but cannot prevent an external write in the final check/replace gap.
    """
    root = normalize_folder(root)
    key = os.path.normcase(str(root))
    with _WRITER_LOCKS_GUARD:
        lock = _WRITER_LOCKS.setdefault(key, RLock())
    with lock:
        held = getattr(_WRITER_STATE, "held", set())
        if key in held:
            yield
            return
        path = vault_path(root, ".taskman/write.lock")
        path.parent.mkdir(parents=True, exist_ok=True)
        path = vault_path(root, ".taskman/write.lock")
        with path.open("a+b") as stream:
            deadline = time.monotonic() + 5
            while True:
                try:
                    if os.name == "nt":
                        import msvcrt
                        stream.seek(0)
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as error:
                    if time.monotonic() >= deadline:
                        raise ValueError("Another Taskman writer is busy; try again") from error
                    time.sleep(0.05)
            _WRITER_STATE.held = held | {key}
            try:
                yield
            finally:
                _WRITER_STATE.held = held
                if os.name == "nt":
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _read_lines(path: Path) -> tuple[list[str], str]:
    """File lines + newline style (splitlines drops endings; we restore)."""
    text = path.read_bytes().decode("utf-8")
    return text.splitlines(), ("\r\n" if "\r\n" in text else "\n")


def _write_lines_atomic(path: Path, lines: list[str], nl: str,
                         trailing_nl: bool = True, *, expected=_UNSET) -> None:
    """Stage, flush and replace; reject a changed file immediately before replace."""
    tmp = tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8",
                                      dir=str(path.parent), newline="")
    try:
        tmp.write(nl.join(lines) + (nl if trailing_nl and lines else ""))
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp.close()
        if path.exists():
            os.chmod(tmp.name, stat.S_IMODE(path.stat().st_mode))
        if expected is not _UNSET:
            actual = path.read_bytes() if path.exists() else None
            if actual != expected:
                raise ValueError("Task file changed before saving; refresh and try again")
        os.replace(tmp.name, path)
    except BaseException:
        # A write/flush/fsync error can leave the staging handle open. Windows
        # cannot remove that file until it is closed; preserve the first error.
        try:
            tmp.close()
        except OSError:
            pass
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        raise


def _changed_lines(original: bytes, changes: dict[int, str]) -> str:
    """Keep every unrelated byte, including mixed endings and missing final LF."""
    lines = original.decode("utf-8").splitlines(keepends=True)
    for lineno, new_line in changes.items():
        if not 1 <= lineno <= len(lines):
            raise ValueError("Task line changed before saving; refresh and try again")
        old = lines[lineno - 1]
        ending = old[len(old.rstrip("\r\n")):]
        lines[lineno - 1] = new_line + ending
    return "".join(lines)


def _commit_text(root: Path, rel: str, original: "bytes | None", text: str) -> None:
    path = vault_path(root, rel)
    if original is not None and text.encode("utf-8") == original:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path = vault_path(root, rel)
    _write_lines_atomic(path, [text], "", trailing_nl=False, expected=original)


def _rewrite_lines(root: Path, rel: str, changes: dict[int, str]) -> None:
    """Replace several 1-based lines together under the shared writer lock."""
    with vault_write_lock(root):
        original = vault_path(root, rel).read_bytes()
        _commit_text(root, rel, original, _changed_lines(original, changes))


def _check_anchors(task: Task) -> None:
    if len(set(TASK_ANCHOR_RE.findall(task.raw or build_line(task)))) > 1:
        raise ValueError("Task has conflicting anchors; repair them before editing")


def resolve_task(root: "str | Path", identifier: str) -> Task:
    """Find one stable 32-hex ID or legacy vault-relative ``file.md:line``.

    Reads never add IDs. Unknown identifiers, unsafe paths and duplicate or
    conflicting anchors raise ValueError, rather than selecting an arbitrary row.
    """
    root = normalize_folder(root)
    if TASK_ANCHOR_VALUE_RE.fullmatch(identifier):
        task = find_task_by_anchor(load_all(root), identifier)
    else:
        relative, sep, number = identifier.rpartition(":")
        if not sep or not number.isascii() or not number.isdecimal() or int(number) < 1:
            raise ValueError("Invalid task identifier; use a stable ID or file.md:line")
        path = vault_path(root, relative)
        if path.suffix.lower() != ".md":
            raise ValueError("Task identifiers require a Markdown file")
        relative = path.relative_to(root).as_posix()
        tasks = load_all(root)
        task = next((item for item in tasks
                     if item.file == relative and item.lineno == int(number)), None)
        if task and task.anchor:
            find_task_by_anchor(tasks, task.anchor)
    if task is None:
        raise ValueError(f"Task not found: {identifier}")
    _check_anchors(task)
    return task


def fresh_task(root: "str | Path", task: Task) -> Task:
    """Resolve a task again and reject stale contents before a write/history record.

    Stable IDs follow unchanged task lines across file moves and line insertions.
    Legacy locations must still contain the exact line the caller originally read.
    """
    root = normalize_folder(root)
    vault_path(root, task.file)  # Reject an unsafe supplied location even with an ID.
    _check_anchors(task)
    try:
        current = resolve_task(root, task.anchor or task.id)
    except ValueError as error:
        if "not found" in str(error):
            raise ValueError("Task changed or was removed; refresh and try again") from error
        raise
    expected = task.raw or build_line(task)
    if current.raw != expected or current.note != task.note:
        raise ValueError("Task changed before saving; refresh and try again")
    # Detect an edit during the vault scan, not only changes since the dialog opened.
    path = vault_path(root, current.file)
    original = path.read_bytes()
    parsed = parse_file(path, root)
    check = next((item for item in parsed if item.lineno == current.lineno), None)
    if (check is None or check.raw != current.raw or check.note != current.note
            or path.read_bytes() != original):
        raise ValueError("Task file changed before saving; refresh and try again")
    return check


def _task_snapshot(root: Path, task: Task) -> tuple[Task, list[Task], bytes]:
    current = fresh_task(root, task)
    path = vault_path(root, current.file)
    original = path.read_bytes()
    tasks = parse_file(path, root)
    check = next((item for item in tasks if item.lineno == current.lineno), None)
    if (check is None or check.raw != current.raw or check.note != current.note
            or path.read_bytes() != original):
        raise ValueError("Task file changed before saving; refresh and try again")
    return check, tasks, original


def _updated_task(root: Path, original: Task, current: Task) -> Task:
    result = next((item for item in parse_file(vault_path(root, current.file), root)
                   if item.lineno == current.lineno), None)
    if result is None or (current.anchor and result.anchor != current.anchor):
        raise ValueError("Task changed after saving; refresh and try again")
    for field in Task.__dataclass_fields__:
        setattr(original, field, getattr(result, field))
    return original


def _edit_task(root: "str | Path", task: Task, edit) -> Task:
    root = normalize_folder(root)
    with vault_write_lock(root):
        current, _tasks, original = _task_snapshot(root, task)
        edit(current)
        _commit_text(root, current.file, original,
                     _changed_lines(original, {current.lineno: build_line(current)}))
        return _updated_task(root, task, current)


def _rewrite_line(root: Path, t: Task, new_line: str) -> None:
    """Replace one 1-based line atomically (temp file + rename)."""
    with vault_write_lock(root):
        current, _tasks, original = _task_snapshot(root, t)
        _commit_text(root, current.file, original,
                     _changed_lines(original, {current.lineno: new_line}))
        _updated_task(root, t, current)


def ensure_task_anchor(vault: Path, task: Task) -> Task:
    """Return a freshly parsed task with a durable anchor, adding one lazily.

    Legacy tasks receive an ID only on explicit request or linking; new app
    tasks already have one. Stale tasks and ambiguous anchors fail without writing.
    """
    root = normalize_folder(vault)
    with vault_write_lock(root):
        current, _tasks, original = _task_snapshot(root, task)
        if current.anchor:
            return current
        anchor = _new_anchor(root, current)
        separator = "" if current.raw and current.raw[-1].isspace() else " "
        line = f"{current.raw}{separator}<!-- taskman:id={anchor} -->"
        _commit_text(root, current.file, original,
                     _changed_lines(original, {current.lineno: line}))
        return resolve_task(root, anchor)


def toggle(root: "str | Path", t: Task, day: "dt.date | None" = None,
           cascade: bool = True) -> Task:
    """Flip done <-> todo, stamping/clearing the ✅ date like Task Genius.

    With ``cascade`` (default) the whole branch flips together: completing a
    parent completes its open sub-tasks, reopening reopens done sub-tasks.
    One atomic write covers the subtree.
    """
    return _completion(root, t, day, cascade, toggle=True)


def complete(root: "str | Path", t: Task, day: "dt.date | None" = None,
             cascade: bool = True) -> Task:
    """Complete once; retries never reopen a task or change its completion date."""
    return _completion(root, t, day, cascade, toggle=False)


def normalize_recurrence(rule: str) -> str:
    """Normalize supported repeat text; blank, none and clear remove the rule."""
    return recurrence_rules.normalize_rule(rule)


def _recurrence_dates(task: Task, day: "dt.date | None" = None) -> dict:
    rule = recurrence_rules.parse_rule(task.recurrence)
    reference = task.due or task.scheduled or task.start
    if reference is None:
        raise ValueError("Repeat needs a Due, Scheduled, or Start date")
    following = recurrence_rules.next_date(rule, reference, today(day))
    delta = following - reference
    try:
        return {field: value + delta if value else None for field in ("due", "scheduled", "start")
                for value in (getattr(task, field),)}
    except (OverflowError, ValueError):
        raise ValueError("Next occurrence is outside the supported calendar") from None


def recurrence_warning(task: Task, day: "dt.date | None" = None) -> str:
    """Explain why a handwritten repeat cannot spawn, including after completion."""
    if not task.recurrence or task.recurrence_next:
        return ""
    if len(set(RECURRENCE_NEXT_RE.findall(task.raw))) > 1:
        return "Conflicting next occurrence markers; no next occurrence created"
    try:
        _recurrence_dates(task, day if day is not None else task.done_date)
    except ValueError as error:
        return f"{error}; no next occurrence created"
    return ""


def _completion(root: "str | Path", t: Task, day: "dt.date | None",
                cascade: bool, *, toggle: bool) -> Task:
    root = normalize_folder(root)
    with vault_write_lock(root):
        cur, tasks, original = _task_snapshot(root, t)
        if cur.done and not toggle:
            return _updated_task(root, t, cur)
        targets = [cur] + (descendants_of(tasks, cur) if cascade else [])
        new_done = not cur.done
        successor = None
        if new_done and cur.recurrence and not cur.recurrence_next and not recurrence_warning(cur, day):
            successor = replace(cur, status=" ", done_date=None, cancelled_date=None,
                                anchor="", raw="", recurrence_next="", context=False,
                                **_recurrence_dates(cur, day))
            successor.anchor = _new_anchor(root, successor)
            cur.recurrence_next = successor.anchor
        changes = _completion_lines(targets, cur, new_done, day)
        if changes:
            content = _changed_lines(original, changes)
            if successor:
                # Only the selected task and its own note are cloned. Keep every
                # descendant under the completed original, with the same IDs.
                chunks = original.decode("utf-8").splitlines(keepends=True)
                newline = "\r\n" if b"\r\n" in original else "\n"
                note = "".join(chunks[cur.lineno:cur.block_end])
                insertion = build_line(successor) + newline + note
                if not insertion.endswith(("\n", "\r")):
                    insertion += newline
                updated = content.splitlines(keepends=True)
                updated.insert(cur.lineno - 1, insertion)
                content = "".join(updated)
                cur.lineno += len(insertion.splitlines())
            _commit_text(root, cur.file, original, content)
        return _updated_task(root, t, cur)


def _completion_lines(targets: list[Task], cur: Task, new_done: bool,
                      day: "dt.date | None") -> dict[int, str]:
    changes: dict[int, str] = {}
    for x in targets:
        # The task itself always flips; sub-tasks only if they need to
        # (cancelled sub-tasks stay cancelled when a parent completes).
        if new_done and (x is cur or x.open):
            x.status = "x"
            x.done_date = today(day)
            x.cancelled_date = None
            changes[x.lineno] = build_line(x)
        elif not new_done and x.done:
            x.status = " "
            x.done_date = None
            changes[x.lineno] = build_line(x)
    return changes


def set_status(root: "str | Path", t: Task, status: str,
               day: "dt.date | None" = None) -> Task:
    """Set the checkbox character, keeping the ✅ / ❌ dates truthful.

    Entering done stamps ✅ today; entering cancelled stamps ❌ today;
    leaving either state clears its date. Other statuses ('/', '>', '?')
    touch nothing else. For the whole-branch behaviour use ``toggle``.
    """
    status = status or " "
    if len(status) != 1 or status in "\r\n]":
        raise ValueError("Status must be one checkbox character")
    if status.lower() == "x":
        return complete(root, t, day, cascade=False)

    def edit(cur: Task) -> None:
        cur.status = status
        cur.done_date = None
        cur.cancelled_date = (cur.cancelled_date or today(day)) if cur.cancelled else None

    return _edit_task(root, t, edit)


def set_due(root: "str | Path", t: Task, due: "dt.date | None") -> Task:
    return _set_dates(root, t, due=due)


def _valid_date(value: "dt.date | None") -> "dt.date | None":
    if value is not None and (not isinstance(value, dt.date) or isinstance(value, dt.datetime)):
        raise ValueError("Dates must be a calendar date or None")
    return value


def set_scheduled(root: "str | Path", t: Task, scheduled: "dt.date | None") -> Task:
    return _set_dates(root, t, scheduled=scheduled)


def set_dates(root: "str | Path", t: Task, due: "dt.date | None",
              scheduled: "dt.date | None", *, recurrence=_UNSET) -> Task:
    """Validate and save dates and optional repeat rule in one replacement."""
    return _set_dates(root, t, due=due, scheduled=scheduled, recurrence=recurrence)


def _set_dates(root, t, *, due=_UNSET, scheduled=_UNSET, recurrence=_UNSET) -> Task:
    if due is not _UNSET:
        due = _valid_date(due)
    if scheduled is not _UNSET:
        scheduled = _valid_date(scheduled)
    def edit(cur: Task) -> None:
        if due is not _UNSET:
            cur.due = due
        if scheduled is not _UNSET:
            cur.scheduled = scheduled
        if recurrence is not _UNSET and recurrence != cur.recurrence:
            cur.recurrence = normalize_recurrence(recurrence)
        if cur.recurrence and not (cur.due or cur.scheduled or cur.start):
            raise ValueError("Repeat needs a Due, Scheduled, or Start date")
    return _edit_task(root, t, edit)


def set_recurrence(root: "str | Path", t: Task, rule: str) -> Task:
    return _set_dates(root, t, recurrence=rule)


def set_priority(root: "str | Path", t: Task, level: int) -> Task:
    if not 0 <= level <= 5:
        raise ValueError("priority level must be 0..5")
    return _edit_task(root, t, lambda cur: setattr(cur, "priority", level))


def edit_text(root: "str | Path", t: Task, description: str) -> Task:
    def edit(cur: Task) -> None:
        if "🔁" in description and description != cur.description:
            raise ValueError("Use Dates to change Repeat")
        text = TASK_ANCHOR_RE.sub(" ", description) if cur.anchor else description
        cur.description = re.sub(r"\s+", " ", text).strip()

    return _edit_task(root, t, edit)


def block_linenos(tasks: Iterable[Task], t: Task) -> set[int]:
    """Every line owned by ``t``: its line, its note, and all descendants'
    lines and notes. What delete/move operate on."""
    out: set[int] = set()
    for x in subtree(tasks, t):
        out.update(range(x.lineno, x.block_end + 1))
    return out


def delete_task(root: "str | Path", t: Task) -> int:
    """Delete a task *with its notes and sub-tasks* (whole branch).
    Returns the number of lines removed."""
    root = normalize_folder(root)
    with vault_write_lock(root):
        cur, tasks, original = _task_snapshot(root, t)
        victims = block_linenos(tasks, cur)
        lines = original.decode("utf-8").splitlines(keepends=True)
        kept = [ln for i, ln in enumerate(lines, start=1) if i not in victims]
        _commit_text(root, cur.file, original, "".join(kept))
        return len(victims)


def set_note(root: "str | Path", t: Task, text: str) -> Task:
    """Replace the task's note (indented plain lines right under it).

    Lines are written as ``<task indent> + two spaces + text`` so Obsidian
    renders them as the list item's own paragraph; blank lines inside the
    note are kept, leading/trailing blank lines dropped. Empty text removes
    the note. Sub-tasks stay where they are (after the note).
    """
    root = normalize_folder(root)
    clean = text.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    body = [ln.rstrip() for ln in clean.split("\n")] if clean.strip() else []
    with vault_write_lock(root):
        cur, _tasks, original = _task_snapshot(root, t)
        new_lines = [f"{cur.indent}{INDENT_STEP}{ln}" if ln.strip() else "" for ln in body]
        text = _splice_lines(original, cur.lineno, cur.block_end, new_lines)
        _commit_text(root, cur.file, original, text)
        return _updated_task(root, t, cur)


def _splice_lines(original: bytes, start: int, stop: int, inserted: list[str]) -> str:
    """Splice whole lines while preserving the endings of untouched lines."""
    text = original.decode("utf-8")
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    if inserted and start and not lines[start - 1].endswith(("\n", "\r")):
        lines[start - 1] += newline
    lines[start:stop] = [line + newline for line in inserted]
    return "".join(lines)


def _parse_inline(text: str) -> Task:
    """Parse quick-add text so emoji/dates/tags work in the add box too."""
    probe = parse_task_line(f"- [ ] {text}") or Task(file="", lineno=0)
    return probe


PROJECT_NAME_RE = re.compile(r"[^\w\-./]+")   # what a #tag can hold (see TAG_RE)


def clean_project_name(name: str) -> str:
    """Normalize user input into something a ``#project/`` tag can hold.

    Strips '#' and 'project/', turns spaces and other tag-unsafe characters
    into '-' ('Q4 Plan (v2)' -> 'Q4-Plan-v2'). Nested names like
    'Work/Alpha' survive (Task Genius path projects).
    """
    name = name.strip().lstrip("#").strip()
    if name.lower().startswith("project/"):
        name = name[len("project/"):]
    name = PROJECT_NAME_RE.sub("-", name)
    name = re.sub(r"-{2,}", "-", name)
    return name.strip(" -./")


def ensure_project_file(root: "str | Path", project: str) -> tuple[Path, bool]:
    """Make sure ``Projects/<project>.md`` exists. Returns (path, created)."""
    root = normalize_folder(root)
    with vault_write_lock(root):
        target = vault_path(root, f"Projects/{project}.md")
        if target.exists():
            return target, False
        _commit_text(root, target.relative_to(root).as_posix(), None, _project_header(project))
        return target, True


def _project_header(project: str) -> str:
    return f"---\ntype: project\nstatus: active\n---\n\n# {project}\n\n## Tasks\n\n"


def set_project(root: "str | Path", t: Task, project: str) -> Task:
    """Assign ``t`` to a project by tag: ``#project/<name>`` replaces any
    existing project tag(s); an empty name removes them. The line stays in
    its note (moving lines would orphan sub-tasks and lose context).
    """
    project = clean_project_name(project)
    def edit(cur: Task) -> None:
        keep = [g for g in cur.tags if not g.lower().startswith("project/")]
        if project:
            keep.append(f"project/{project}")
        cur.tags = tuple(keep)

    return _edit_task(root, t, edit)


def _new_anchor(root: Path, probe: Task) -> str:
    _check_anchors(probe)
    tasks = load_all(root)
    used = {anchor for task in tasks for anchor in TASK_ANCHOR_RE.findall(task.raw)}
    if probe.anchor:
        if probe.anchor in used:
            raise ValueError(f"Duplicate task anchor: {probe.anchor}")
        return probe.anchor
    anchor = uuid.uuid4().hex
    while anchor in used:
        anchor = uuid.uuid4().hex
    return anchor


def add_task(root: "str | Path", text: str, project: str = "",
             due=_UNSET, priority: int = 0, scheduled=_UNSET, recurrence=_UNSET) -> Task:
    """Append a task. Project tasks go under that file's ``## Tasks`` section."""
    root = normalize_folder(root)
    probe = _parse_inline(text)
    desc = probe.description or text.strip()
    tags = list(probe.tags)
    prio = priority or probe.priority
    due = probe.due if due is _UNSET else _valid_date(due)
    scheduled = probe.scheduled if scheduled is _UNSET else _valid_date(scheduled)
    recurrence = normalize_recurrence(probe.recurrence if recurrence is _UNSET else recurrence)
    if recurrence and not (due or scheduled or probe.start):
        raise ValueError("Repeat needs a Due, Scheduled, or Start date")
    if not 0 <= prio <= 5:
        raise ValueError("priority level must be 0..5")
    project = clean_project_name(project) if project else ""
    if project and not any(g.casefold() == f"project/{project}".casefold() for g in tags):
        tags.append(f"project/{project}")

    rel = f"Projects/{project}.md" if project else "Tasks/Inbox.md"
    # Reject unsafe destinations before writer coordination creates metadata.
    # Resolve again under the lock in case the filesystem changed meanwhile.
    vault_path(root, rel)
    with vault_write_lock(root):
        anchor = _new_anchor(root, probe)
        target = vault_path(root, rel)
        original = target.read_bytes() if target.exists() else None
        content = original if original is not None else (
            _project_header(project) if project else "---\ntype: inbox\n---\n\n# Inbox\n\n"
        ).encode("utf-8")
        lines = content.decode("utf-8").splitlines()
        t = Task(file=rel, lineno=len(lines) + 1, status=" ", description=desc,
                 priority=prio, due=due, start=probe.start, scheduled=scheduled,
                 tags=tuple(tags), anchor=anchor, recurrence=recurrence)
        line = build_line(t)
        # Place after complete task blocks, including notes and descendants.
        idx = next((i for i, ln in enumerate(lines)
                    if ln.strip().casefold() == "## tasks"), None) if project else None
        inserted = [line]
        if idx is not None:
            j = idx + 1
            while j < len(lines) and not lines[j].startswith("#"):
                j += 1
            while j > idx + 1 and not lines[j - 1].strip():
                j -= 1
            if j == idx + 1:
                inserted.insert(0, "")
        else:
            j = len(lines)
            if lines and lines[-1].strip() and not TASK_LINE_RE.match(lines[-1]):
                inserted.insert(0, "")
        _commit_text(root, rel, original, _splice_lines(content, j, j, inserted))
        return resolve_task(root, anchor)


def add_subtask(root: "str | Path", parent: Task, text: str,
                due=_UNSET, priority: int = 0, scheduled=_UNSET, recurrence=_UNSET) -> Task:
    """Insert a sub-task under ``parent`` (after its existing children).

    The new line copies the file's nesting style: it reuses the indent of
    ``parent``'s current children when there are any, else parent + two
    spaces. Markdown stays clean and Obsidian renders the nesting.
    """
    root = normalize_folder(root)
    probe = _parse_inline(text)
    due = probe.due if due is _UNSET else _valid_date(due)
    scheduled = probe.scheduled if scheduled is _UNSET else _valid_date(scheduled)
    recurrence = normalize_recurrence(probe.recurrence if recurrence is _UNSET else recurrence)
    if recurrence and not (due or scheduled or probe.start):
        raise ValueError("Repeat needs a Due, Scheduled, or Start date")
    priority = priority or probe.priority
    if not 0 <= priority <= 5:
        raise ValueError("priority level must be 0..5")
    with vault_write_lock(root):
        cur, tasks, original = _task_snapshot(root, parent)
        kids = [x for x in tasks if x.parent_lineno == cur.lineno]
        indent = kids[0].indent if kids else cur.indent + INDENT_STEP
        anchor = _new_anchor(root, probe)
        t = Task(
            file=cur.file, lineno=0, status=" ",
            description=probe.description or text.strip(), priority=priority, due=due,
            start=probe.start, scheduled=scheduled, tags=tuple(probe.tags), indent=indent,
            depth=cur.depth + 1, parent_lineno=cur.lineno, anchor=anchor, recurrence=recurrence,
        )
        last = max(block_linenos(tasks, cur))
        _commit_text(root, cur.file, original, _splice_lines(original, last, last, [build_line(t)]))
        return resolve_task(root, anchor)


def _shift_subtree(root: Path, rel: str, cur: Task, new_indent: str) -> Task:
    """Move ``cur`` + descendants (and everyone's note lines) to
    ``new_indent``, keeping relative nesting."""
    with vault_write_lock(root):
        node, tasks, original = _task_snapshot(root, cur)
        branch = [node] + descendants_of(tasks, node)
        old = node.indent
        lines = original.decode("utf-8").splitlines()

        def reindent(prefix: str) -> "str | None":
            if not old:
                return new_indent + prefix
            if prefix.startswith(old):
                return new_indent + prefix[len(old):]
            return None  # malformed nesting; leave untouched

        changes: dict[int, str] = {}
        for x in branch:
            _check_anchors(x)
            shifted = new_indent if x.lineno == node.lineno else reindent(x.indent)
            if shifted is None:
                continue
            x.indent = shifted
            changes[x.lineno] = build_line(x)
            for ln in range(x.lineno + 1, x.block_end + 1):
                raw = lines[ln - 1]
                if not raw.strip():
                    continue
                lead = raw[:len(raw) - len(raw.lstrip())]
                new_lead = reindent(lead)
                if new_lead is not None:
                    changes[ln] = new_lead + raw.lstrip()
        _commit_text(root, node.file, original, _changed_lines(original, changes))
        return _updated_task(root, cur, node)


def indent_task(root: "str | Path", t: Task) -> "Task | None":
    """Nest ``t`` one level deeper under its previous sibling.

    Returns the updated task, or None when there is no previous sibling to
    nest under (e.g. first task in the file). Children move along with it.
    """
    root = normalize_folder(root)
    with vault_write_lock(root):
        cur, tasks, _original = _task_snapshot(root, t)
        sibs = [x for x in tasks if x.lineno < cur.lineno and x.depth == cur.depth]
        if not sibs:
            return None
        return _shift_subtree(root, cur.file, t, sibs[-1].indent + INDENT_STEP)


def outdent_task(root: "str | Path", t: Task) -> "Task | None":
    """Lift ``t`` one level (aligns with its parent). Children move along.

    Returns the updated task, or None when already top-level.
    """
    root = normalize_folder(root)
    with vault_write_lock(root):
        cur, tasks, _original = _task_snapshot(root, t)
        if cur.depth == 0:
            return None
        parent = next((x for x in tasks if x.lineno == cur.parent_lineno), None)
        return _shift_subtree(root, cur.file, t, parent.indent if parent else "")


WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


def note_links(t: Task) -> list[str]:
    """``[[Note]]`` titles referenced in the task text."""
    return WIKILINK_RE.findall(t.description)


def project_links(t: Task) -> list[str]:
    """Project names from the task's own ``#project/X`` tags."""
    return [g[len("project/"):] for g in t.tags if g.startswith("project/")]


def link_project(root: "str | Path", t: Task, project: str) -> "Task | None":
    """Attach ``#project/<name>`` so the task joins that project's view.

    Returns the updated task, or None when already linked (no write).
    """
    project = project.strip().lstrip("#")
    if project.lower().startswith("project/"):
        project = project[len("project/"):]
    if not project:
        return None
    with vault_write_lock(root):
        current = fresh_task(root, t)
        if any(g.casefold() == f"project/{project}".casefold() for g in current.tags):
            return None
        return _edit_task(root, t, lambda cur: setattr(cur, "tags", (*cur.tags, f"project/{project}")))


def link_note(root: "str | Path", t: Task, title: str) -> "Task | None":
    """Append a ``[[Note]]`` wikilink to the task text (Obsidian link).

    Returns the updated task, or None when already linked (no write).
    """
    title = title.strip().strip("[]")
    if not title:
        return None
    with vault_write_lock(root):
        current = fresh_task(root, t)
        if title.casefold() in (n.casefold() for n in note_links(current)):
            return None
        return edit_text(Path(root), t, f"{current.description} [[{title}]]".strip())


# ---------------------------------------------------------------------------
# 7. Plain CLI — accessible without the TUI (pipes, screen readers, tests)
# ---------------------------------------------------------------------------

def cmd_plain(root: Path, view: str, project: str = "", query: str = "",
              day: "dt.date | None" = None) -> int:
    tasks = load_all(root)
    day = today(day)
    if view == "projects":
        counts_by = project_counts(tasks)
        for name in project_names(tasks, root):
            print(f"{name}  ({counts_by.get(name.casefold(), 0)} open)")
        return 0
    rows = view_tasks(tasks, view, day, project, query)
    for sec in sections(rows, view, day):
        print(f"== {sec.title} ({sec.count}) ==")
        for node in sec.nodes:
            print(node.task.short(day))
            for line in node.task.note.splitlines():   # notes ride along, indented
                print("  " * node.task.depth + "      " + line)
    return 0


def _utf8_stdout() -> None:
    """Never crash on emoji: pipes/files get UTF-8, consoles keep theirs but
    substitute unencodable characters (Windows cp1252 would otherwise die)."""
    out = sys.stdout
    try:
        if out.isatty():
            out.reconfigure(errors="replace")  # type: ignore[union-attr]
        else:
            out.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass


def main(argv: "list[str] | None" = None) -> int:
    """Run the shared dependency-free command interface."""
    if __package__:
        from .cli import main as command_main
    else:
        from cli import main as command_main
    return command_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
