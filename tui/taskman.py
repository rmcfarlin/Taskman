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

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal

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
    "inbox", "today", "overdue", "next7", "all", "completed", "priority", "project", "search"
]

VIEWS: tuple[tuple[str, str], ...] = (
    ("inbox", "Inbox  — no due date"),
    ("today", "Today"),
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
    """Parse every checkbox in one file. Never raises on bad encoding."""
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
        if stripped.startswith("```") or stripped.startswith("~~~"):
            flush()
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        t = parse_task_line(line, file=rel, lineno=i)
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


def is_inbox(t: Task) -> bool:
    return t.open and t.due is None


def view_tasks(tasks: Iterable[Task], view: str,
               day: "dt.date | None" = None, project: str = "",
               query: str = "", with_parents: bool = True) -> list[Task]:
    """Filter tasks for a named view (+ optional project / search query).

    With ``with_parents`` (default), ancestors of matches are included too so
    a sub-task is never shown orphaned; they are flagged ``context=True``
    (dimmed in the TUI) to distinguish them from real matches.
    """
    task_list = list(tasks)
    for t in task_list:
        t.context = False
    day = today(day)
    q = query.strip().casefold()
    out: list[Task] = []
    for t in task_list:
        if view == "inbox" and not is_inbox(t):
            continue
        elif view == "today" and not (t.open and t.due == day):
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
        elif view not in ("inbox", "today", "overdue", "next7", "all",
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
        "today": len(view_tasks(ts, "today", day, **kw)),
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
    ("week", "Next 7 days"), ("later", "Later"), ("nodate", "No date"),
)
# Same idea for the Completed view, keyed by completion date.
DONE_BUCKETS: tuple[tuple[str, str], ...] = (
    ("today", "Completed today"), ("yesterday", "Yesterday"),
    ("week", "Last 7 days"), ("older", "Older"), ("nodate", "No completion date"),
)


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


def _tree_sort_key(nodes: list[Node], closed: bool) -> tuple:
    """Order trees inside a section: most urgent first (newest done first)."""
    real = [n.task for n in nodes if not n.task.context] or [nodes[0].task]
    if closed:
        newest = max((t.done_date or t.cancelled_date or dt.date.min) for t in real)
        return (-newest.toordinal(), real[0].file, real[0].lineno)
    due = min((t.due or dt.date.max) for t in real)
    prio = max(t.priority for t in real)
    return (due, -prio, real[0].file, real[0].lineno)


def sections(rows: Iterable[Task], view: str,
             day: "dt.date | None" = None) -> list[Section]:
    """Lay out view rows as titled sections of task trees.

    Open views bucket by due date (Overdue … No date); the Completed view
    buckets by completion date. A tree lands in the most urgent bucket of
    any *matched* task it contains, so a sub-task due today under an
    undated parent still shows under Today (parent dimmed as context).
    """
    day = today(day)
    closed = view == "completed"
    buckets = DONE_BUCKETS if closed else DUE_BUCKETS
    rank = done_bucket if closed else due_bucket
    grouped: dict[int, list[list[Node]]] = {}
    for tree in tree_rows(rows):
        real = [n.task for n in tree if not n.task.context] or [tree[0].task]
        grouped.setdefault(min(rank(t, day) for t in real), []).append(tree)
    out: list[Section] = []
    for i, (key, title) in enumerate(buckets):
        trees = grouped.get(i)
        if not trees:
            continue
        trees.sort(key=lambda ns: _tree_sort_key(ns, closed))
        out.append(Section(key, title, [n for ns in trees for n in ns]))
    return out


# ---------------------------------------------------------------------------
# 6. Mutations (read-modify-write one line; surrounding notes untouched)
# ---------------------------------------------------------------------------

def build_line(t: Task) -> str:
    """Rebuild the markdown line for a task (canonical, readable order)."""
    tokens: list[str] = []
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
    if tokens:
        body = f"{body} {' '.join(tokens)}" if body else " ".join(tokens)
    return f"{t.indent}{t.bullet} [{t.status}] {body}".rstrip()


def _read_lines(path: Path) -> tuple[list[str], str]:
    """File lines + newline style (splitlines drops endings; we restore)."""
    text = path.read_text(encoding="utf-8")
    return text.splitlines(), ("\r\n" if "\r\n" in text else "\n")


def _write_lines_atomic(path: Path, lines: list[str], nl: str,
                         trailing_nl: bool = True) -> None:
    """Replace a file's content via temp file + rename (crash-safe)."""
    tmp = tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8",
                                      dir=str(path.parent), newline="")
    try:
        tmp.write(nl.join(lines) + (nl if trailing_nl and lines else ""))
        tmp.close()
        os.replace(tmp.name, path)
    except BaseException:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        raise


def _rewrite_lines(root: Path, rel: str, changes: dict[int, str]) -> None:
    """Replace several 1-based lines in one atomic write."""
    path = vault_path(root, rel)
    lines, nl = _read_lines(path)
    for lineno, new_line in changes.items():
        lines[lineno - 1] = new_line
    _write_lines_atomic(path, lines, nl, trailing_nl=True)


def _rewrite_line(root: Path, t: Task, new_line: str) -> None:
    """Replace one 1-based line atomically (temp file + rename)."""
    _rewrite_lines(root, t.file, {t.lineno: new_line})


def toggle(root: "str | Path", t: Task, day: "dt.date | None" = None,
           cascade: bool = True) -> Task:
    """Flip done <-> todo, stamping/clearing the ✅ date like Task Genius.

    With ``cascade`` (default) the whole branch flips together: completing a
    parent completes its open sub-tasks, reopening reopens done sub-tasks.
    One atomic write covers the subtree.
    """
    root = Path(root)
    fresh = parse_file(root / t.file, root)
    cur = next((x for x in fresh if x.lineno == t.lineno), t)
    targets = [cur] + (descendants_of(fresh, cur) if cascade else [])
    new_done = not cur.done
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
    if changes:
        _rewrite_lines(root, t.file, changes)
    t.status, t.done_date = cur.status, cur.done_date
    return t


def set_status(root: "str | Path", t: Task, status: str,
               day: "dt.date | None" = None) -> Task:
    """Set the checkbox character, keeping the ✅ / ❌ dates truthful.

    Entering done stamps ✅ today; entering cancelled stamps ❌ today;
    leaving either state clears its date. Other statuses ('/', '>', '?')
    touch nothing else. For the whole-branch behaviour use ``toggle``.
    """
    t.status = status or " "
    if t.done:
        t.done_date = t.done_date or today(day)
        t.cancelled_date = None
    elif t.cancelled:
        t.cancelled_date = t.cancelled_date or today(day)
        t.done_date = None
    else:
        t.done_date = None
        t.cancelled_date = None
    _rewrite_line(Path(root), t, build_line(t))
    return t


def set_due(root: "str | Path", t: Task, due: "dt.date | None") -> Task:
    t.due = due
    _rewrite_line(Path(root), t, build_line(t))
    return t


def set_priority(root: "str | Path", t: Task, level: int) -> Task:
    if not 0 <= level <= 5:
        raise ValueError("priority level must be 0..5")
    t.priority = level
    _rewrite_line(Path(root), t, build_line(t))
    return t


def edit_text(root: "str | Path", t: Task, description: str) -> Task:
    t.description = re.sub(r"\s+", " ", description).strip()
    _rewrite_line(Path(root), t, build_line(t))
    return t


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
    root = Path(root)
    fresh = parse_file(root / t.file, root)
    cur = next((x for x in fresh if x.lineno == t.lineno), t)
    victims = block_linenos(fresh, cur)
    path = vault_path(root, t.file)
    lines, nl = _read_lines(path)
    kept = [ln for i, ln in enumerate(lines, start=1) if i not in victims]
    _write_lines_atomic(path, kept, nl, trailing_nl=bool(kept))
    return len(victims)


def set_note(root: "str | Path", t: Task, text: str) -> Task:
    """Replace the task's note (indented plain lines right under it).

    Lines are written as ``<task indent> + two spaces + text`` so Obsidian
    renders them as the list item's own paragraph; blank lines inside the
    note are kept, leading/trailing blank lines dropped. Empty text removes
    the note. Sub-tasks stay where they are (after the note).
    """
    root = Path(root)
    path = vault_path(root, t.file)
    fresh = parse_file(path, root)
    cur = next((x for x in fresh if x.lineno == t.lineno), t)
    clean = text.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    body = [ln.rstrip() for ln in clean.split("\n")] if clean.strip() else []
    new_lines = [f"{cur.indent}{INDENT_STEP}{ln}" if ln.strip() else "" for ln in body]
    lines, nl = _read_lines(path)
    lines[cur.lineno:cur.block_end] = new_lines      # old note block -> new one
    _write_lines_atomic(path, lines, nl, trailing_nl=True)
    t.note = "\n".join(body)
    t.note_end = cur.lineno + len(new_lines) if new_lines else 0
    return t


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
    root = Path(root)
    target = vault_path(root, f"Projects/{project}.md")
    if target.exists():
        return target, False
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        f"---\ntype: project\nstatus: active\n---\n\n# {project}\n\n## Tasks\n\n",
        encoding="utf-8",
    )
    return target, True


def set_project(root: "str | Path", t: Task, project: str) -> Task:
    """Assign ``t`` to a project by tag: ``#project/<name>`` replaces any
    existing project tag(s); an empty name removes them. The line stays in
    its note (moving lines would orphan sub-tasks and lose context).
    """
    project = clean_project_name(project)
    keep = [g for g in t.tags if not g.lower().startswith("project/")]
    if project:
        keep.append(f"project/{project}")
    t.tags = tuple(keep)
    _rewrite_line(Path(root), t, build_line(t))
    return t


def add_task(root: "str | Path", text: str, project: str = "",
             due: "dt.date | None" = None, priority: int = 0) -> Task:
    """Append a task. Project tasks go under that file's ``## Tasks`` section."""
    root = Path(root)
    probe = _parse_inline(text)
    desc = probe.description or text.strip()
    tags = list(probe.tags)
    prio = priority or probe.priority
    due = due or probe.due
    extras = {"start": probe.start, "scheduled": probe.scheduled}
    project = clean_project_name(project) if project else ""
    if project and not any(g.casefold() == f"project/{project}".casefold() for g in tags):
        tags.append(f"project/{project}")

    if project:
        target, _created = ensure_project_file(root, project)
    else:
        target = vault_path(root, "Tasks/Inbox.md")
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_text("---\ntype: inbox\n---\n\n# Inbox\n\n", encoding="utf-8")
    lines, nl = _read_lines(target)
    rel = target.relative_to(root).as_posix()

    t = Task(file=rel, lineno=len(lines) + 1, status=" ", description=desc,
             priority=prio, due=due, start=extras["start"],
             scheduled=extras["scheduled"], tags=tuple(tags))
    line = build_line(t)

    # Project notes: join the list under "## Tasks" (right after the last
    # task there, before any trailing blank lines / the next heading).
    idx = next((i for i, ln in enumerate(lines)
                if ln.strip().casefold() == "## tasks"), None) if project else None
    if idx is not None:
        j = idx + 1
        while j < len(lines) and (lines[j].strip() == "" or TASK_LINE_RE.match(lines[j])):
            j += 1
        while j > idx + 1 and lines[j - 1].strip() == "":
            j -= 1
        if j == idx + 1:            # empty section: blank line under heading
            lines.insert(j, "")
            j += 1
        lines.insert(j, line)
        t.lineno = j + 1
    else:
        if lines and lines[-1].strip() and not TASK_LINE_RE.match(lines[-1]):
            lines.append("")        # keep prose and the checkbox list apart
        lines.append(line)
        t.lineno = len(lines)
    _write_lines_atomic(target, lines, nl, trailing_nl=True)
    return t


def add_subtask(root: "str | Path", parent: Task, text: str,
                due: "dt.date | None" = None, priority: int = 0) -> Task:
    """Insert a sub-task under ``parent`` (after its existing children).

    The new line copies the file's nesting style: it reuses the indent of
    ``parent``'s current children when there are any, else parent + two
    spaces. Markdown stays clean and Obsidian renders the nesting.
    """
    root = Path(root)
    fresh = parse_file(root / parent.file, root)
    cur = next((x for x in fresh if x.lineno == parent.lineno), parent)
    kids = [x for x in fresh if x.parent_lineno == cur.lineno]
    indent = kids[0].indent if kids else cur.indent + INDENT_STEP
    probe = _parse_inline(text)
    t = Task(
        file=parent.file, lineno=0, status=" ",
        description=probe.description or text.strip(),
        priority=priority or probe.priority, due=due or probe.due,
        start=probe.start, scheduled=probe.scheduled,
        tags=tuple(probe.tags), indent=indent, depth=cur.depth + 1,
        parent_lineno=cur.lineno,
    )
    path = vault_path(root, parent.file)
    lines, nl = _read_lines(path)
    # Insert after the last line of the parent's block (its note lines and
    # every descendant's lines/notes), so notes stay glued to their task.
    last = max(block_linenos(fresh, cur))
    lines.insert(last, build_line(t))  # 0-based index == line after `last`
    _write_lines_atomic(path, lines, nl, trailing_nl=True)
    t.lineno = last + 1
    return t


def _shift_subtree(root: Path, rel: str, cur: Task, new_indent: str) -> Task:
    """Move ``cur`` + descendants (and everyone's note lines) to
    ``new_indent``, keeping relative nesting."""
    fresh = parse_file(root / rel, root)
    node = next((x for x in fresh if x.lineno == cur.lineno), cur)
    branch = [node] + descendants_of(fresh, node)
    old = node.indent
    lines, _nl = _read_lines(root / rel)

    def reindent(prefix: str) -> "str | None":
        if not old:
            return new_indent + prefix
        if prefix.startswith(old):
            return new_indent + prefix[len(old):]
        return None  # malformed nesting; leave untouched

    changes: dict[int, str] = {}
    for x in branch:
        shifted = new_indent if x.lineno == node.lineno else reindent(x.indent)
        if shifted is None:
            continue
        x.indent = shifted
        changes[x.lineno] = build_line(x)
        for ln in range(x.lineno + 1, x.block_end + 1):     # note lines follow
            raw = lines[ln - 1]
            if not raw.strip():
                continue
            lead = raw[: len(raw) - len(raw.lstrip())]
            new_lead = reindent(lead)
            if new_lead is not None:
                changes[ln] = new_lead + raw.lstrip()
    _rewrite_lines(root, rel, changes)
    cur.indent = new_indent
    return cur


def indent_task(root: "str | Path", t: Task) -> "Task | None":
    """Nest ``t`` one level deeper under its previous sibling.

    Returns the updated task, or None when there is no previous sibling to
    nest under (e.g. first task in the file). Children move along with it.
    """
    root = Path(root)
    fresh = parse_file(root / t.file, root)
    cur = next((x for x in fresh if x.lineno == t.lineno), None)
    if cur is None:
        return None
    sibs = [x for x in fresh if x.lineno < cur.lineno and x.depth == cur.depth]
    if not sibs:
        return None
    return _shift_subtree(root, t.file, cur, sibs[-1].indent + INDENT_STEP)


def outdent_task(root: "str | Path", t: Task) -> "Task | None":
    """Lift ``t`` one level (aligns with its parent). Children move along.

    Returns the updated task, or None when already top-level.
    """
    root = Path(root)
    fresh = parse_file(root / t.file, root)
    cur = next((x for x in fresh if x.lineno == t.lineno), None)
    if cur is None or cur.depth == 0:
        return None
    parent = next((x for x in fresh if x.lineno == cur.parent_lineno), None)
    return _shift_subtree(root, t.file, cur, parent.indent if parent else "")


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
    if any(g.casefold() == f"project/{project}".casefold() for g in t.tags):
        return None
    t.tags = tuple(list(t.tags) + [f"project/{project}"])
    _rewrite_line(Path(root), t, build_line(t))
    return t


def link_note(root: "str | Path", t: Task, title: str) -> "Task | None":
    """Append a ``[[Note]]`` wikilink to the task text (Obsidian link).

    Returns the updated task, or None when already linked (no write).
    """
    title = title.strip().strip("[]")
    if not title or title.casefold() in (n.casefold() for n in note_links(t)):
        return None
    return edit_text(Path(root), t, f"{t.description} [[{title}]]".strip())


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
    ap = argparse.ArgumentParser(
        prog="taskman",
        description="Markdown-native task manager (vault stays human-readable).",
    )
    ap.add_argument("--vault", default=None, help="vault folder (or TASKMAN_VAULT, recent folder, current vault)")
    ap.add_argument("--plain", metavar="VIEW",
                    help="print one task per line: inbox,today,overdue,next7,"
                         "all,completed,priority,search + 'projects'")
    ap.add_argument("--project", default="",
                    help="project filter for --plain (or target project for --add)")
    ap.add_argument("--search", default="", help="search text for --plain")
    ap.add_argument("--add", metavar="TEXT",
                    help="quick-add TEXT to the inbox (or to --project NAME)")
    ap.add_argument("--sub", metavar="TEXT", help="add TEXT as a sub-task (needs --under)")
    ap.add_argument("--note", metavar="TEXT",
                    help="set the note under a task (needs --under; '' clears)")
    ap.add_argument("--under", metavar="ID", help="task id (file:line) for --sub / --note")
    ap.add_argument("--check", action="store_true", help="vault health summary")
    args = ap.parse_args(argv)
    try:
        root = vault_root(args.vault)
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2
    _utf8_stdout()

    if args.check:
        tasks = load_all(root)
        c = counts(tasks)
        print(f"vault: {root}")
        print(f"files: {len(iter_markdown_files(root))}  tasks: {len(tasks)}")
        for name, _label in VIEWS:
            print(f"{name}: {c[name]}")
        return 0
    if args.add:
        t = add_task(root, args.add, project=args.project)
        print(f"added {t.id}: {t.description}")
        return 0
    if args.sub or args.note is not None:
        flag = "--sub" if args.sub else "--note"
        if not args.under:
            print(f"{flag} needs --under FILE:LINE", file=sys.stderr)
            return 2
        target = next((x for x in load_all(root) if x.id == args.under), None)
        if target is None:
            print(f"no task {args.under}", file=sys.stderr)
            return 1
        if args.sub:
            t = add_subtask(root, target, args.sub)
            print(f"added {t.id} under {target.id}: {t.description}")
        else:
            set_note(root, target, args.note)
            print(f"note {'cleared' if not args.note.strip() else 'saved'} on {target.id}")
        return 0
    if args.plain:
        view = "search" if args.plain == "search" else args.plain
        return cmd_plain(root, view, args.project, args.search)
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
