"""Taskman TUI — the Textual interface. Run:  python -m tui

Everything task-shaped lives in taskman.py (stdlib only). This file is
pure presentation: layout, key handling, dialogs, themes.

Layout (one screen, no wasted rows)
  topbar ........... ◆ Taskman · vault — N open · due ≤7d · overdue     theme · clock
  sidebar | list | inspect
                     views + projects (counts) | the task table | INSPECT
                     pane (i / Enter toggles): facts, SUB-TASKS, NOTE
  statusbar ........ VIEW badge · counts                 cursor/total · inspect
  footer ........... key hints — every key here is also a clickable button

The table is a Line-API widget (TaskList): it renders only the visible
lines, so thousands of tasks scroll instantly. Rows are grouped into
sections (Overdue · Today · Tomorrow · Next 7 days · Later · No date) and
sub-tasks hang under their parent with tree guides. Columns — PRIO · TASK ·
DUE · PROJECT · TAGS — flex with the width; the right-hand ones hide on
narrow terminals rather than wrap.

Accessibility notes (please keep these true when editing)
  - Nothing means *only* color: status is a distinct glyph (○ ◐ ● → ✕) AND
    a word in the inspector; priority is a glyph (▲ ⇈ ↑ ↓ ⇊) AND a short
    word in its column; overdue says "late" / "Overdue" in words.
  - Every action is keyboard-accessible and discoverable in the command menu. Focus is always
    visible (accent pane divider, inverted cursor row, and contextual key hints).
  - Common actions have lowercase shortcuts, taught in the footer and
    command menu. Ctrl+K searches every action, view, and project. Arrow
    keys and Alt+1/2/3 navigate panes; Tab visits fields and subtasks.
    Session undo/redo checks for external edits before restoring files.
    Ctrl+O opens another vault. Task changes save immediately to Markdown.
  - --plain mode (taskman.py) prints the same data as plain text for
    screen readers and pipes:  python -m tui --plain today
  - Honors NO_COLOR: set it to force the high-contrast monochrome theme.
"""

from __future__ import annotations

import datetime as dt
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Union
from contextlib import contextmanager
from functools import wraps

from rich.segment import Segment
from rich.style import Style
from rich.text import Text
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.geometry import Region, Size
from textual.message import Message
from textual.screen import ModalScreen
from textual.scroll_view import ScrollView
from textual.strip import Strip
from textual.suggester import SuggestFromList
from textual.theme import Theme
from textual.widgets import Button, Input, Label, OptionList, Static, TextArea
from textual.widgets.option_list import Option

try:  # `python -m tui` (package) vs `python tui/app.py` (script)
    from tui import taskman as tm
    from tui.taskman import Task
    from tui.commands import Command, CommandScreen
    from tui.history import History, HistoryConflict
    from tui.shortcut_bar import ShortcutBar
    from tui.diagnostics import record_error
    from tui import settings
    from tui.vaults import discover_vault, initialize_vault, normalize_folder
    from tui.vault_screen import VaultScreen, VaultChoice
    from tui.notes import NotesStore, NoteConflict
    from tui.notes_ui import NotesWorkspace
    from tui.notes_actions import NotesActions
except ImportError:  # pragma: no cover -- direct-script fallback
    import taskman as tm  # type: ignore[no-redef]
    from taskman import Task  # type: ignore[no-redef]
    from commands import Command, CommandScreen
    from history import History, HistoryConflict
    from shortcut_bar import ShortcutBar
    from diagnostics import record_error
    import settings
    from vaults import discover_vault, initialize_vault, normalize_folder
    from vault_screen import VaultScreen, VaultChoice
    from notes import NotesStore, NoteConflict
    from notes_ui import NotesWorkspace
    from notes_actions import NotesActions

# Views shown in the sidebar: (hotkey, view-name, label). Order = 1..7 keys.
SIDEBAR_VIEWS: tuple[tuple[str, str, str], ...] = (
    ("1", "all", "All open"),
    ("2", "today", "Today"),
    ("3", "overdue", "Overdue"),
    ("4", "next7", "Next 7 days"),
    ("5", "inbox", "Inbox"),
    ("6", "priority", "High priority"),
    ("7", "completed", "Completed"),
)
VIEW_LABEL = {name: label for _, name, label in SIDEBAR_VIEWS}
VIEW_ICON = {
    "all": "≡", "today": "☀", "overdue": "⚠", "next7": "◷",
    "inbox": "▤", "priority": "▲", "completed": "✓", "project": "◆",
}
DEFAULT_VIEW = "all"

# Status char -> (glyph, component-class suffix). Glyph shapes differ, so
# status never relies on color alone.
STATUS_GLYPH: dict[str, tuple[str, str]] = {
    " ": ("○", "status-open"),
    "/": ("◐", "status-progress"),
    "x": ("●", "status-done"),
    "X": ("●", "status-done"),
    "-": ("✕", "status-cancel"),
    ">": ("→", "status-forward"),
    "?": ("?", "status-open"),
}
# Priority level -> glyph. Mirrors the markdown emoji: 🔺▲  ⏫⇈  🔼↑  🔽↓  ⏬⇊
PRIO_GLYPH = {5: "▲", 4: "⇈", 3: "↑", 2: "↓", 1: "⇊", 0: " "}
# ... and the short word shown next to it in the PRIO column.
PRIO_SHORT = {5: "top", 4: "hi", 3: "md", 2: "lo", 1: "min", 0: ""}
NOTE_GLYPH = "≣"   # shown after a task that has a note under it


def note_preview(note: str, glyph_style: Style | str = "") -> Text:
    """One-line preview: '≣ 4 lines · first line…' (count first so it
    survives truncation; a single-line note shows just the line)."""
    lines = note.splitlines()
    out = Text(no_wrap=True)
    out.append(f"{NOTE_GLYPH} ", glyph_style)
    if len(lines) > 1:
        out.append(f"{len(lines)} lines · ", "dim")
    out.append(next((ln for ln in lines if ln.strip()), ""))
    return out


def _fg_only(style: Style) -> Style:
    """Copy of a Rich style without its background (keeps color + attributes)."""
    return Style(color=style.color, bold=style.bold, dim=style.dim, italic=style.italic,
                 underline=style.underline, strike=style.strike, reverse=style.reverse)


# ---------------------------------------------------------------------------
# Themes — dark themes: five of one family (a single hue carries the
# structure; white carries the words; warm colors only carry meaning) plus
# Darcula and One Dark ports, then Light and a true High contrast theme for
# low vision (the promise NO_COLOR makes). `m` opens a live-preview picker;
# the choice persists in the user's Taskman settings.
# --theme NAME / $TASKMAN_THEME override it for a session.
# ---------------------------------------------------------------------------

def _family(name: str, *, primary: str, secondary: str, accent: str, foreground: str,
            background: str, surface: str, panel: str, warning: str, error: str,
            success: str, muted: str, primary_muted: str, border: str,
            cursor_text: str, text_primary: str | None = None) -> Theme:
    """Build one dark theme. ``primary`` is the structure color (borders, the
    inverted cursor, badges); ``accent`` is the focus/title color;
    ``cursor_text`` is the text color drawn on the cursor bar (dark on a
    bright primary, white on a mid-blue one like Darcula's). ``text_primary``
    overrides the derived text tint when the primary is too dark to read
    as text (upcoming due dates, in-progress glyphs, section titles)."""
    variables = {
        "block-cursor-background": primary,
        "block-cursor-foreground": cursor_text,
        "block-cursor-text-style": "none",
        "block-cursor-blurred-background": f"{primary} 30%",
        "block-cursor-blurred-foreground": foreground,
        "border-blurred": border,
        "primary-muted": primary_muted,
        "text-muted": muted,
        "footer-key-foreground": accent,
    }
    if text_primary:
        variables["text-primary"] = text_primary
    return Theme(
        name=name, dark=True,
        primary=primary, secondary=secondary, accent=accent, foreground=foreground,
        background=background, surface=surface, panel=panel, boost=primary,
        warning=warning, error=error, success=success,
        variables=variables,
    )


DARK_THEMES: tuple[tuple[Theme, str], ...] = (
    (_family("taskman-teal", primary="#19b3a3", secondary="#4fa89f", accent="#7de8d9",
             foreground="#d5e6e3", background="#0b171a", surface="#10222a", panel="#143038",
             warning="#e6b866", error="#ef7d86", success="#6fd3a1",
             muted="#7fa3a0", primary_muted="#1e5f5c", border="#1e4a4f", cursor_text="#06171a"),
     "Teal"),
    (_family("taskman-ocean", primary="#3d8ef0", secondary="#6fa8f5", accent="#a3cdff",
             foreground="#d9e3f2", background="#0a111d", surface="#0f1a2c", panel="#16253d",
             warning="#e8c46a", error="#f27e86", success="#6fcf97",
             muted="#7e93b3", primary_muted="#24466f", border="#223a5e", cursor_text="#061020"),
     "Ocean"),
    (_family("taskman-ember", primary="#e0842e", secondary="#e9a45c", accent="#ffd0a0",
             foreground="#f0e4d6", background="#150f0a", surface="#1e1710", panel="#2a1f15",
             warning="#f2d56b", error="#f06a6a", success="#8fcf8f",
             muted="#a38e78", primary_muted="#6a3f1c", border="#5a3a22", cursor_text="#1a0f05"),
     "Ember"),
    (_family("taskman-iris", primary="#8c6ff0", secondary="#a88ff5", accent="#cdbcff",
             foreground="#e2dcf5", background="#0f0c1b", surface="#171331", panel="#201b40",
             warning="#e8c46a", error="#f47e92", success="#7fd6a4",
             muted="#8f86b5", primary_muted="#423670", border="#352c5c", cursor_text="#0d0a1a"),
     "Iris"),
    (_family("taskman-moss", primary="#4fae6d", secondary="#72c48a", accent="#aeeabc",
             foreground="#dae7dd", background="#0b140f", surface="#112019", panel="#182b21",
             warning="#e4c96c", error="#ef7b7b", success="#8fe3a8",
             muted="#7fa38b", primary_muted="#2b5d3d", border="#24513a", cursor_text="#06120a"),
     "Moss"),
    # JetBrains Darcula: #2B2B2B editor, #3C3F41 tool windows, blue list
    # selection, orange keywords / yellow functions / green strings.
    (_family("taskman-darcula", primary="#4b6eaf", secondary="#6897bb", accent="#cc7832",
             foreground="#a9b7c6", background="#242424", surface="#2b2b2b", panel="#3c3f41",
             warning="#ffc66d", error="#ff6b68", success="#6a8759",
             muted="#808080", primary_muted="#555555", border="#45494a", cursor_text="#ffffff"),
     "Darcula"),
    # Atom One Dark: #282C34 base, #21252B gutter, blue / purple / cyan
    # accents, red-green-yellow semantic set.
    (_family("taskman-onedark", primary="#61afef", secondary="#56b6c2", accent="#c678dd",
             foreground="#abb2bf", background="#21252b", surface="#282c34", panel="#2c313a",
             warning="#e5c07b", error="#e06c75", success="#98c379",
             muted="#7f8794", primary_muted="#3e4451", border="#3e4451", cursor_text="#282c34"),
     "One Dark"),
    # Dark Teal: charcoal surfaces with white text; deep teal #244D4F
    # fills the cursor bar and badges
    # (white on top), lighter teal tints carry borders, titles and hotkeys
    # where the deep teal would vanish on dark; favorable green #4FA672.
    (_family("taskman-dark-teal", primary="#244d4f", secondary="#9ccfc8", accent="#5cc2bd",
             foreground="#ffffff", background="#242424", surface="#2b2b2b", panel="#3c3f41",
             warning="#ffc66d", error="#e5716c", success="#4fa672",
             muted="#9aa5a6", primary_muted="#3d7375", border="#3a5a5b", cursor_text="#ffffff",
             text_primary="#7fcbc6"),
     "Dark Teal"),
)

# (theme_name, friendly_label, is_dark). Order = picker order.
THEMES: tuple[tuple[str, str, bool], ...] = tuple(
    (theme.name, label, True) for theme, label in DARK_THEMES
) + (
    ("catppuccin-latte", "Light", False),
    ("high-contrast", "High contrast", True),
)
DEFAULT_THEME = "taskman-dark-teal"
THEME_ALIASES = {"taskman-rcm": "taskman-dark-teal"}
HIGH_CONTRAST = "high-contrast"
THEME_FILE = "theme.txt"  # compatibility for callers passing an explicit file


def teal_theme() -> Theme:
    """The original Teal theme object, kept for existing callers."""
    return DARK_THEMES[0][0]


def theme_swatch(name: str) -> Text:
    """Four color blocks — primary, accent, warning, error — for the picker,
    so you can see a theme before you try it."""
    from textual.theme import BUILTIN_THEMES
    theme = next((t for t, _ in DARK_THEMES if t.name == name), None)
    if theme is None and name == HIGH_CONTRAST:
        theme = high_contrast_theme()
    if theme is None:
        theme = BUILTIN_THEMES.get(name)
    out = Text(no_wrap=True)
    if theme is None:
        return out.append("    ")
    for color in (theme.primary, theme.accent, theme.warning or theme.primary,
                  theme.error or theme.primary):
        out.append("█", Style(color=color))
    return out


def high_contrast_theme() -> Theme:
    """Maximum-legibility theme: pure black/white + bright accents."""
    return Theme(
        name=HIGH_CONTRAST,
        dark=True,
        primary="#ffffff",
        secondary="#ffff00",
        accent="#00ffff",
        foreground="#ffffff",
        background="#000000",
        surface="#000000",
        panel="#111111",
        boost="#ffffff",
        warning="#ffff00",
        error="#ff5555",
        success="#55ff55",
        variables={
            "text-muted": "#d0d0d0",
            "border-blurred": "#a0a0a0",
            "primary-muted": "#a0a0a0",
            "block-cursor-background": "#ffffff",
            "block-cursor-foreground": "#000000",
            "block-cursor-blurred-background": "#555555",
            "block-cursor-blurred-foreground": "#ffffff",
        },
    )


def theme_file_path() -> Path:
    return settings.config_dir() / THEME_FILE


def read_theme_file(path: Path | None = None) -> str:
    """Saved theme name, or '' when missing/invalid (caller falls back)."""
    try:
        name = path.read_text(encoding="utf-8").strip() if path is not None else settings.read_theme()
    except OSError:
        return ""
    name = THEME_ALIASES.get(name, name)
    return name if any(name == t[0] for t in THEMES) else ""


def write_theme_file(name: str, path: Path | None = None) -> None:
    if path is not None:
        path.write_text(name + "\n", encoding="utf-8")
    else:
        settings.write_theme(name)


def resolve_theme(explicit: str | None = None) -> str:
    """Precedence: explicit/--theme > $TASKMAN_THEME > user settings > default.

    NO_COLOR always wins with High contrast (accessibility promise).
    """
    if os.environ.get("NO_COLOR"):
        return HIGH_CONTRAST
    valid = [t[0] for t in THEMES]
    for cand in (explicit, os.environ.get("TASKMAN_THEME"), read_theme_file()):
        cand = THEME_ALIASES.get(cand, cand)
        if cand and cand in valid:
            return cand
    return DEFAULT_THEME


def theme_is_dark(name: str) -> bool:
    return next((t[2] for t in THEMES if t[0] == name), True)


def due_style_today(theme_name: str) -> str:
    """Rich style for 'today' — never bare yellow on a light background."""
    return "bold yellow" if theme_is_dark(theme_name) else "bold dark_orange"


# ---------------------------------------------------------------------------
# Date words
# ---------------------------------------------------------------------------

def _mday(d: dt.date) -> str:
    return f"{d.strftime('%b')} {d.day}"


def format_due(d: dt.date, day: dt.date) -> str:
    """Short words for the Due column (≤ 10 cells; the inspector has the ISO)."""
    if d < day:
        return f"{(day - d).days}d late"
    if d == day:
        return "today"
    if d == day + dt.timedelta(days=1):
        return "tomorrow"
    if d <= day + dt.timedelta(days=7):
        return f"{d.strftime('%a')} {d.month}/{d.day}"
    if d.year == day.year:
        return _mday(d)
    return d.isoformat()


def format_date_long(d: dt.date, day: dt.date, past: str = "ago") -> str:
    """Inspector words: 'Fri Sep 5 (tomorrow)', 'Wed Sep 2 (2 days late)'.

    ``past`` is the word for dates behind us: "late" for due dates, "ago"
    for everything else (a start date in the past is not late).
    """
    base = f"{d.strftime('%a')} {_mday(d)}" + (f" {d.year}" if d.year != day.year else "")
    delta = (d - day).days
    if delta == 0:
        rel = "today"
    elif delta == 1:
        rel = "tomorrow"
    elif delta == -1 and past == "ago":
        rel = "yesterday"
    elif delta < 0:
        rel = f"{-delta} day{'s' if delta != -1 else ''} {past}"
    elif delta <= 14:
        rel = f"in {delta} days"
    else:
        return base
    return f"{base} ({rel})"


EMPTY_STATE = {
    "all": "No open tasks — press a to add one.",
    "inbox": "Inbox is clear. Press a to capture a thought.",
    "today": "Nothing due today. Press d on a task to schedule it here.",
    "overdue": "Nothing overdue. Nice.",
    "next7": "Nothing due in the next 7 days.",
    "completed": "Nothing completed yet. Press c on a task when it's done.",
    "priority": "No high-priority tasks right now — p sets one.",
    "project": "No tasks in this project yet — press a to add one.",
    "search": "No matches. Esc clears the search.",
}


# ---------------------------------------------------------------------------
# TaskList — Line-API table: fixed header + sections + task trees
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class HeaderRow:
    key: str      # bucket id ("overdue", "today", ...) -> section color
    title: str    # "" renders as a blank spacer line
    count: int


@dataclass(slots=True)
class TaskRow:
    task: Task
    depth: int
    prefix: str                 # tree guide, e.g. "│ └ "
    progress: tuple[int, int]   # (closed direct children, total children)
    context: bool = False       # ancestor shown only for its matching children
                                # (snapshot: Task.context is reset by every view_tasks call)


Row = Union[HeaderRow, TaskRow]


class TaskList(ScrollView, can_focus=True):
    """A table of sections + task trees, one line per row, rendered lazily.

    Line 0 is a fixed column header (PRIO · TASK · DUE · PROJECT · TAGS);
    the rows scroll underneath it. Section header rows are decoration: the
    cursor hops over them. Column widths come from the widget width on
    every render, so resizing just works. Styling is 100% via component
    classes (theme-aware, high-contrast OK). The focused cursor row is an
    inverted block; blurred it is a tint.
    """

    DEFAULT_CSS = """
    TaskList {
        background: $background;
        color: $foreground;
        overflow-x: hidden;
        scrollbar-size-horizontal: 0;
        & > .tasklist--cursor {
            color: $block-cursor-blurred-foreground;
            background: $block-cursor-blurred-background;
        }
        &:focus > .tasklist--cursor {
            color: $block-cursor-foreground;
            background: $block-cursor-background;
        }
        & > .tasklist--colhead { color: $text-muted; text-style: bold; }
        & > .tasklist--header { color: $text-muted; text-style: bold; }
        & > .tasklist--rule { color: $foreground 15%; }
        & > .tasklist--guide { color: $foreground 35%; }
        & > .tasklist--desc { color: $foreground; }
        & > .tasklist--done { color: $text-muted; }
        & > .tasklist--context { color: $text-muted; text-style: italic; }
        & > .tasklist--progress { color: $text-muted; }
        & > .tasklist--tag { color: $text-secondary; }
        & > .tasklist--project { color: $text-accent; }
        & > .tasklist--muted { color: $text-muted; }
        & > .tasklist--empty { color: $text-muted; text-style: italic; }
        & > .tasklist--overdue { color: $text-error; text-style: bold; }
        & > .tasklist--today { color: $text-warning; text-style: bold; }
        & > .tasklist--soon { color: $text-primary; }
        & > .tasklist--later { color: $text-muted; }
        & > .tasklist--status-open { color: $foreground 55%; }
        & > .tasklist--status-progress { color: $text-primary; text-style: bold; }
        & > .tasklist--status-done { color: $text-success; }
        & > .tasklist--status-cancel { color: $text-muted; }
        & > .tasklist--status-forward { color: $text-secondary; }
        & > .tasklist--prio-5 { color: $text-error; text-style: bold; }
        & > .tasklist--prio-4 { color: $text-warning; text-style: bold; }
        & > .tasklist--prio-3 { color: $text-accent; }
        & > .tasklist--prio-2 { color: $text-success; }
        & > .tasklist--prio-1 { color: $text-muted; }
        & > .tasklist--prio-0 { color: $foreground; }
        & > .tasklist--section-overdue { color: $text-error; }
        & > .tasklist--section-today { color: $text-warning; }
        & > .tasklist--section-tomorrow { color: $text-primary; }
        & > .tasklist--section-week { color: $text-primary; }
        & > .tasklist--section-later { color: $text-muted; }
        & > .tasklist--section-nodate { color: $text-muted; }
        & > .tasklist--section-yesterday { color: $text-success; }
        & > .tasklist--section-older { color: $text-muted; }
    }
    """

    COMPONENT_CLASSES = {
        "tasklist--cursor", "tasklist--colhead", "tasklist--header",
        "tasklist--rule", "tasklist--guide", "tasklist--desc", "tasklist--done",
        "tasklist--context", "tasklist--progress", "tasklist--tag",
        "tasklist--project", "tasklist--muted", "tasklist--empty",
        "tasklist--overdue", "tasklist--today", "tasklist--soon", "tasklist--later",
        "tasklist--status-open", "tasklist--status-progress", "tasklist--status-done",
        "tasklist--status-cancel", "tasklist--status-forward",
        "tasklist--prio-5", "tasklist--prio-4", "tasklist--prio-3",
        "tasklist--prio-2", "tasklist--prio-1", "tasklist--prio-0",
        "tasklist--section-overdue", "tasklist--section-today",
        "tasklist--section-tomorrow", "tasklist--section-week",
        "tasklist--section-later", "tasklist--section-nodate",
        "tasklist--section-yesterday", "tasklist--section-older",
    }

    BINDINGS = [
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("pageup", "page_up", "Page up", show=False),
        Binding("pagedown", "page_down", "Page down", show=False),
        Binding("home", "cursor_first", "First", show=False),
        Binding("end", "cursor_last", "Last", show=False),
        Binding("enter", "select", "Open", show=False),
        Binding("right", "app.focus_inspector", "Details", show=False),
        Binding("left", "app.focus_sidebar", "Views", show=False),
    ]

    HEADER_LINES = 1   # the fixed column header at the top

    class Highlighted(Message):
        """The cursor moved (or the rows changed under it)."""

        def __init__(self, task: Task | None) -> None:
            super().__init__()
            self.task = task

    class Selected(Message):
        """Enter / double-click on a task."""

        def __init__(self, task: Task) -> None:
            super().__init__()
            self.task = task

    def __init__(self, *, id: str | None = None, classes: str | None = None) -> None:
        super().__init__(id=id, classes=classes)
        self.rows: list[Row] = []
        self.cursor: int = -1
        self.day: dt.date = dt.date.today()
        self.empty_message: str = "Nothing here."
        self._cache: dict[tuple, Strip] = {}

    # -- data ---------------------------------------------------------------
    @property
    def current(self) -> Task | None:
        if 0 <= self.cursor < len(self.rows):
            row = self.rows[self.cursor]
            if isinstance(row, TaskRow):
                return row.task
        return None

    @property
    def task_count(self) -> int:
        return sum(1 for r in self.rows if isinstance(r, TaskRow))

    @property
    def cursor_ordinal(self) -> int:
        """1-based position of the cursor among task rows (0 = none)."""
        if self.cursor < 0:
            return 0
        return sum(1 for r in self.rows[: self.cursor + 1] if isinstance(r, TaskRow))

    def index_of(self, task_id: str) -> int:
        for i, r in enumerate(self.rows):
            if isinstance(r, TaskRow) and r.task.id == task_id:
                return i
        return -1

    def set_rows(self, rows: list[Row], *, keep_id: str | None = None,
                 select: int | None = None) -> None:
        """Replace all rows; put the cursor on keep_id, else near ``select``
        (a row index), else where it was."""
        self.rows = list(rows)
        self._cache.clear()
        self.virtual_size = Size(1, len(self.rows) + self.HEADER_LINES)
        idx = self.index_of(keep_id) if keep_id else -1
        if idx < 0:
            want = select if select is not None else max(self.cursor, 0)
            idx = self._nearest(want, +1)
        self.cursor = idx
        self._cursor_moved()

    def _nearest(self, index: int, direction: int) -> int:
        """Closest task row to ``index`` (searching ``direction`` first)."""
        n = len(self.rows)
        if n == 0:
            return -1
        index = max(0, min(index, n - 1))
        for step in (direction, -direction):
            j = index
            while 0 <= j < n:
                if isinstance(self.rows[j], TaskRow):
                    return j
                j += step
        return -1

    def _cursor_moved(self) -> None:
        if self.cursor >= 0:
            top = self.cursor
            while top > 0 and isinstance(self.rows[top - 1], HeaderRow):
                top -= 1  # keep the section title (and spacer) in view too
            # Rows live at virtual line index+1 (line 0 is the column header,
            # which also covers virtual line scroll_y) — ask for one extra
            # line above so the top row is never hidden under the header.
            self.scroll_to_region(Region(0, top, 1, self.cursor - top + 2),
                                  animate=False, force=True)
        self.refresh()
        self.post_message(self.Highlighted(self.current))

    # -- navigation -----------------------------------------------------------
    def _step(self, direction: int, count: int = 1) -> None:
        if not self.rows:
            return
        i = self.cursor if self.cursor >= 0 else self._nearest(0, +1)
        for _ in range(count):
            j = i + direction
            while 0 <= j < len(self.rows) and isinstance(self.rows[j], HeaderRow):
                j += direction
            if not 0 <= j < len(self.rows):
                break
            i = j
        if i != self.cursor:
            self.cursor = i
            self._cursor_moved()

    def action_cursor_up(self) -> None:
        self._step(-1)

    def action_cursor_down(self) -> None:
        self._step(+1)

    def action_page_up(self) -> None:
        self._step(-1, max(1, self.scrollable_content_region.height - 2))

    def action_page_down(self) -> None:
        self._step(+1, max(1, self.scrollable_content_region.height - 2))

    def action_cursor_first(self) -> None:
        idx = self._nearest(0, +1)
        if idx >= 0 and idx != self.cursor:
            self.cursor = idx
            self._cursor_moved()

    def action_cursor_last(self) -> None:
        idx = self._nearest(len(self.rows) - 1, -1)
        if idx >= 0 and idx != self.cursor:
            self.cursor = idx
            self._cursor_moved()

    def action_select(self) -> None:
        t = self.current
        if t is not None:
            self.post_message(self.Selected(t))

    def on_click(self, event: events.Click) -> None:
        offset = event.get_content_offset(self)
        if offset is None or offset.y < self.HEADER_LINES:
            return
        idx = offset.y - self.HEADER_LINES + self.scroll_offset.y
        if 0 <= idx < len(self.rows) and isinstance(self.rows[idx], TaskRow):
            if idx != self.cursor:
                self.cursor = idx
                self._cursor_moved()
            if event.chain >= 2:
                self.action_select()

    # -- invalidation -------------------------------------------------------
    def on_focus(self) -> None:
        self._cache.clear()
        self.refresh()

    def on_blur(self) -> None:
        self._cache.clear()
        self.refresh()

    def on_resize(self) -> None:
        self._cache.clear()

    def notify_style_update(self) -> None:
        super().notify_style_update()
        self._cache.clear()

    # -- rendering ------------------------------------------------------------
    def _style(self, name: str) -> Style:
        """Foreground + text attributes of a component class, fully resolved.

        The *full* style is used (not ``partial``) because partial styles
        drop alpha: ``$foreground 35%`` would come back as bright
        foreground. We then strip the background so the row's cursor tint
        can show through.
        """
        return _fg_only(self.get_component_rich_style(f"tasklist--{name}"))

    def style_for(self, name: str) -> Style:
        """Public alias so the inspector reuses the same palette."""
        return self._style(name)

    def _columns(self, width: int) -> tuple[int, int, int, int]:
        """(task, due, project, tags) widths; right-hand columns hide when narrow."""
        due_w = 10 if width >= 50 else 0
        proj_w = min(16, max(8, width // 9)) if width >= 76 else 0
        tags_w = min(20, max(10, width // 8)) if width >= 94 else 0
        fixed = 1 + 1 + 1 + 5 + 1 + 1   # pad, status, gap, prio, gap, right pad
        for w in (due_w, proj_w, tags_w):
            fixed += (w + 1) if w else 0
        return max(8, width - fixed), due_w, proj_w, tags_w

    def render_line(self, y: int) -> Strip:
        _scroll_x, scroll_y = self.scroll_offset
        width = self.scrollable_content_region.width
        base = self.rich_style
        if width <= 0:
            return Strip.blank(0)
        if y < self.HEADER_LINES:
            key = ("colhead", width)
            strip = self._cache.get(key)
            if strip is None:
                text = self._render_colhead(width)
                strip = Strip(list(text.render(self.app.console, end="")))
                strip = strip.adjust_cell_length(width, base).apply_style(base)
                self._cache[key] = strip
            return strip
        if not self.rows:
            return self._render_empty(y, width, base)
        index = y - self.HEADER_LINES + scroll_y
        if not 0 <= index < len(self.rows):
            return Strip.blank(width, base)
        is_cursor = index == self.cursor
        key = (index, width, is_cursor, self.has_focus)
        strip = self._cache.get(key)
        if strip is None:
            row = self.rows[index]
            if isinstance(row, HeaderRow):
                text = self._render_header(row, width)
            else:
                text = self._render_task(row, width)
            strip = Strip(list(text.render(self.app.console, end="")))
            strip = strip.adjust_cell_length(width, base)
            if is_cursor and self.has_focus:
                # Inverted block: the cursor colors win over every cell's own.
                cur = self.get_component_rich_style("tasklist--cursor")
                post = Style(color=cur.color, bgcolor=cur.bgcolor)
                strip = Strip(list(Segment.apply_style(list(strip), post_style=post)), width)
                strip = strip.apply_style(base)
            elif is_cursor:
                strip = strip.apply_style(base + self.get_component_rich_style("tasklist--cursor"))
            else:
                strip = strip.apply_style(base)
            self._cache[key] = strip
        return strip

    def _render_colhead(self, width: int) -> Text:
        task_w, due_w, proj_w, tags_w = self._columns(width)
        head = self._style("colhead")
        line = Text(no_wrap=True)
        line.append(" ")
        line.append("✓", self._style("muted"))
        line.append(" ")
        line.append("PRIO ", head)
        line.append(" ")
        line.append("TASK".ljust(task_w), head)
        if due_w:
            line.append(" ")
            line.append("DUE".ljust(due_w), head)
        if proj_w:
            line.append(" ")
            line.append("PROJECT".ljust(proj_w), head)
        if tags_w:
            line.append(" ")
            line.append("TAGS".ljust(tags_w), head)
        line.truncate(width, pad=True)
        return line

    def _render_empty(self, y: int, width: int, base: Style) -> Strip:
        mid = max(2, self.scrollable_content_region.height // 2)
        if y == mid - 1:
            text = Text("◌", style=self._style("muted"))
        elif y == mid:
            text = Text(self.empty_message, style=self._style("empty"))
        else:
            return Strip.blank(width, base)
        text.truncate(width - 2, overflow="ellipsis")
        text.align("center", width)
        return Strip(list(text.render(self.app.console, end=""))).apply_style(base)

    def _render_header(self, row: HeaderRow, width: int) -> Text:
        if not row.title:                       # spacer between sections
            return Text(" " * width, no_wrap=True)
        color = self._style(f"section-{row.key}")
        line = Text(no_wrap=True)
        line.append(" ")
        line.append(row.title.upper(), self._style("header") + color)
        line.append(f"  {row.count}", self._style("muted"))
        line.append("  ")
        rest = width - line.cell_len - 1
        if rest > 0:
            line.append("─" * rest, self._style("rule"))
        line.truncate(width, pad=True)
        return line

    def _due_cell(self, t: Task) -> tuple[str, str]:
        day = self.day
        if t.closed:
            ref = t.done_date or t.cancelled_date
            if ref is None:
                return "", "muted"
            return ("✓ " if t.done else "✕ ") + _mday(ref), "muted"
        if t.due is None:
            return "", "muted"
        words = format_due(t.due, day)
        if t.due < day:
            return words, "overdue"
        if t.due == day:
            return words, "today"
        if t.due <= day + dt.timedelta(days=7):
            return words, "soon"
        return words, "later"

    def _render_task(self, row: TaskRow, width: int) -> Text:
        t = row.task
        task_w, due_w, proj_w, tags_w = self._columns(width)
        line = Text(no_wrap=True)
        line.append(" ")
        glyph, gclass = STATUS_GLYPH.get(t.status, ("?", "status-open"))
        line.append(glyph, self._style(gclass))
        line.append(" ")
        prio = Text(f"{PRIO_GLYPH.get(t.priority, ' ')} {PRIO_SHORT.get(t.priority, '')}".rstrip(),
                    self._style(f"prio-{t.priority}"), no_wrap=True)
        prio.truncate(5, pad=True)
        line.append_text(prio)
        line.append(" ")

        cell = Text(no_wrap=True)
        if row.prefix:
            cell.append(row.prefix, self._style("guide"))
        if t.closed:
            dstyle = self._style("done")
        elif row.context:
            dstyle = self._style("context")
        else:
            dstyle = self._style("desc")
        cell.append(t.description or "(no text)", dstyle)
        if t.note:
            cell.append(f" {NOTE_GLYPH}", self._style("progress"))   # "has a note"
        closed_n, total = row.progress
        if total:
            cell.append(f"  {closed_n}/{total}", self._style("progress"))
        if not tags_w:                          # no TAGS column: tags ride inline
            for g in t.plain_tags:
                cell.append(f"  #{g}", self._style("tag"))
        cell.truncate(task_w, overflow="ellipsis", pad=True)
        line.append_text(cell)

        if due_w:
            words, dclass = self._due_cell(t)
            due = Text(words, self._style(dclass), no_wrap=True)
            due.truncate(due_w, overflow="ellipsis", pad=True)
            line.append(" ")
            line.append_text(due)
        if proj_w:
            proj = Text(t.project, self._style("project"), no_wrap=True)
            proj.truncate(proj_w, overflow="ellipsis", pad=True)
            line.append(" ")
            line.append_text(proj)
        if tags_w:
            tags = Text(" ".join(f"#{g}" for g in t.plain_tags), self._style("tag"), no_wrap=True)
            tags.truncate(tags_w, overflow="ellipsis", pad=True)
            line.append(" ")
            line.append_text(tags)
        line.append(" ")
        line.truncate(width, pad=True)
        return line


# ---------------------------------------------------------------------------
# Sidebar — views + projects with live counts
# ---------------------------------------------------------------------------

class Sidebar(OptionList):
    """Navigation list. Option ids: ``view:<name>`` or ``proj:<name>``."""

    BINDINGS = [Binding("right", "app.focus_tasks", "Tasks", show=False)]

    DEFAULT_CSS = """
    Sidebar {
        width: 28;
        height: 1fr;
        border: none;
        border-right: solid $primary-muted;
        border-title-color: $text-accent;
        border-title-style: bold;
        padding: 1 1;
        background: $background;
        /* OptionList's default focus border must not move mouse targets. */
        &:focus {
            border: none;
            border-right: solid $accent;
            background-tint: transparent;
        }
        & > .option-list--option { padding: 0; }
        & > .option-list--option-hover { background: transparent; }
        & > .sidebar--heading { color: $text-accent; text-style: bold; }
        & > .sidebar--hotkey { color: $text-accent; text-style: bold; }
        & > .sidebar--icon { color: $text-muted; }
        & > .sidebar--label { color: $foreground; }
        & > .sidebar--active { color: $text-accent; text-style: bold; }
        & > .sidebar--count { color: $text-muted; }
        & > .sidebar--hot { color: $text-error; text-style: bold; }
        & > .sidebar--empty { color: $text-muted; text-style: italic; }
    }
    """
    COMPONENT_CLASSES = OptionList.COMPONENT_CLASSES | {
        "sidebar--heading", "sidebar--hotkey", "sidebar--icon", "sidebar--label",
        "sidebar--active", "sidebar--count", "sidebar--hot", "sidebar--empty",
    }

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._last_args: tuple | None = None   # to re-fit items on resize

    def _inner_width(self) -> int:
        w = self.scrollable_content_region.width
        return w if w > 8 else 24

    def on_resize(self) -> None:
        """Items are pre-fitted to the width, so rebuild them when it changes."""
        if self._last_args is not None:
            self.populate(*self._last_args)

    def _s(self, name: str) -> Style:
        return _fg_only(self.get_component_rich_style(name))

    def _item(self, icon: str, label: str, count: int, active: bool,
              hot: bool = False, hotkey: str = "") -> Text:
        width = self._inner_width()
        line = Text(no_wrap=True)
        line.append("▸ " if active else "  ", self._s("sidebar--active"))
        if hotkey:
            line.append(f"{hotkey} ", self._s("sidebar--hotkey"))
        if width >= 22 or not hotkey:
            line.append(f"{icon} ", self._s("sidebar--icon"))
        elif label == "High priority":
            label = "Priority"
        num = str(count)
        room = width - line.cell_len - len(num) - 1
        lab = Text(label, self._s("sidebar--active" if active else "sidebar--label"))
        lab.truncate(max(room, 3), overflow="ellipsis", pad=True)
        line.append_text(lab)
        line.append(" ")
        cls = "sidebar--hot" if hot and count else "sidebar--count"
        line.append(num, self._s(cls))
        return line

    def populate(self, view_counts: dict[str, int], projects: list[str],
                 project_counts: dict[str, int], view: str, project: str,
                 note_count: int = 0) -> None:
        self._last_args = (view_counts, projects, project_counts, view, project, note_count)
        opts: list[Option | None] = [
            Option(Text("VIEWS", self._s("sidebar--heading")), id="h:views", disabled=True),
        ]
        for key, name, label in SIDEBAR_VIEWS:
            active = name == view and not project
            opts.append(Option(
                self._item(VIEW_ICON[name], label, view_counts.get(name, 0), active,
                           hot=name == "overdue", hotkey=key),
                id=f"view:{name}"))
        opts.append(None)
        opts.append(Option(Text("PROJECTS", self._s("sidebar--heading")),
                           id="h:projects", disabled=True))
        if not projects:
            opts.append(Option(Text("  j  Create a project", self._s("sidebar--empty")),
                               id="h:noproj", disabled=True))
        for name in projects:
            active = bool(project) and name.casefold() == project.casefold()
            opts.append(Option(
                self._item(VIEW_ICON["project"], name,
                           project_counts.get(name.casefold(), 0), active),
                id=f"proj:{name}"))
        opts.extend([None, Option(Text("REFERENCE", self._s("sidebar--heading")),
                                 id="h:reference", disabled=True),
                     Option(self._item("▤", "Notes", note_count, view == "notes", hotkey="8"),
                            id="view:notes")])
        self.set_options(opts)
        # NB: index into self.options (separators are not options), not opts.
        active_id = (f"proj:{project}" if project else f"view:{view}").casefold()
        for i, o in enumerate(self.options):
            if o.id is not None and o.id.casefold() == active_id:
                self.highlighted = i
                break


class TaskInput(Input):
    """Use the familiar select-all shortcut in every single-line field."""

    BINDINGS = [Binding("ctrl+a", "select_all", "Select all", show=False)]


class SearchInput(TaskInput):
    """On-demand Find. Enter/Down return to tasks; Escape closes the bar."""

    BINDINGS = [Binding("down", "to_list", "To list", show=False)]

    def action_to_list(self) -> None:
        self.app.action_focus_tasks()

    def _light(self) -> None:
        if self.parent is not None:
            active = self.has_focus or bool(self.value)
            self.parent.set_class(active, "-active")
            # Keep rows fixed during clicks, including the second of a double-click.
            # Only explicit dismissal may collapse an empty Find bar.
            if active:
                self.parent.display = True

    def on_focus(self) -> None:
        self.call_later(self._light)

    def on_blur(self) -> None:
        self.call_later(self._light)

    def watch_value(self, _value: str) -> None:
        self._light()


# ---------------------------------------------------------------------------
# Inspector — the right-hand pane for the selected task (i / Enter toggles)
# ---------------------------------------------------------------------------

class Inspector(VerticalScroll, can_focus=True):
    """Deeper dive on the selected task: facts, SUB-TASKS (Enter toggles
    one), and the NOTE in full. The pane takes focus for keyboard scrolling;
    Tab reaches the sub-task list, where task actions target the child."""

    BINDINGS = [Binding("left", "app.focus_tasks", "Back to tasks", show=False)]

    DEFAULT_CSS = """
    Inspector {
        width: 44;
        height: 1fr;
        margin-left: 1;
        padding: 0 1;
        border: round $primary-muted;
        border-title-color: $text-accent;
        border-title-style: bold;
        border-subtitle-color: $text-muted;
        background: $background;
        scrollbar-size-vertical: 1;
    }
    Inspector:focus-within { border: round $accent; }
    Inspector #ins-title { text-style: bold; }
    Inspector .ins-head { color: $text-accent; text-style: bold; margin-top: 1; }
    Inspector #ins-facts { margin-top: 1; }
    Inspector #ins-kids {
        height: auto; max-height: 12; border: none; padding: 0; background: transparent;
        text-wrap: nowrap; text-overflow: ellipsis;
    }
    Inspector #ins-kids > .option-list--option-highlighted { background: transparent; color: $foreground; }
    Inspector #ins-kids:focus { background: $background; background-tint: transparent; }
    Inspector #ins-kids:focus > .option-list--option-highlighted {
        background: $block-cursor-background; color: $block-cursor-foreground;
    }
    Inspector #ins-note { color: $foreground; }
    Inspector #ins-references {
        height: auto; max-height: 8; border: none; padding: 0; background: $background;
    }
    Inspector #ins-references:focus { border: none; background-tint: transparent; }
    Inspector #ins-references > .option-list--option-hover { background: transparent; }
    """

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._displayed_task_id: str | None = None

    def compose(self) -> ComposeResult:
        yield Static("", id="ins-title")
        yield Static("", id="ins-facts")
        yield Label("SUB-TASKS", classes="ins-head", id="ins-subhead")
        yield OptionList(id="ins-kids")
        yield Label("NOTE", classes="ins-head", id="ins-notehead")
        yield Static("", id="ins-note")
        yield Label("REFERENCE NOTES · Enter opens", classes="ins-head", id="ins-refhead")
        yield OptionList(id="ins-references")

    def on_mount(self) -> None:
        self.border_title = "INSPECT"
        self.border_subtitle = "↑↓ scroll · Tab subtasks · Esc close"

    def show(self, t: Task | None, tasks: list[Task], day: dt.date,
             palette: Callable[[str], Style], context: bool = False) -> None:
        title = self.query_one("#ins-title", Static)
        facts = self.query_one("#ins-facts", Static)
        kids_list = self.query_one("#ins-kids", OptionList)
        note = self.query_one("#ins-note", Static)
        task_id = t.id if t else None
        same_task = task_id == self._displayed_task_id
        child_index = kids_list.highlighted if same_task else None
        child_id = (kids_list.get_option_at_index(child_index).id
                    if child_index is not None and child_index < kids_list.option_count else None)
        if not same_task:
            self.scroll_to(x=0, y=0, animate=False, immediate=True, force=True)
        self._displayed_task_id = task_id
        if t is None:
            title.update(Text("Nothing selected", "dim italic"))
            facts.update("")
            self.query_one("#ins-subhead", Label).update("SUB-TASKS")
            kids_list.set_options([])
            note.update("")
            return

        glyph, gclass = STATUS_GLYPH.get(t.status, ("?", "status-open"))
        head = Text()
        head.append(f"{glyph} ", palette(gclass) + Style(bold=True))
        head.append(t.description or "(no text)", "bold")
        if context:
            head.append("\nshown as context — an ancestor of a match", "dim italic")
        title.update(head)

        rows: list[tuple[str, Text]] = []

        def fact(label: str, value: str | Text, style: Style | str = "") -> None:
            rows.append((label, value if isinstance(value, Text) else Text(value, style)))

        fact("project", t.project or "— (j assigns one)",
             palette("project") + Style(bold=True) if t.project else "dim")
        pr = Text()
        pr.append(f"{PRIO_GLYPH[t.priority]} " if t.priority else "", palette(f"prio-{t.priority}"))
        pr.append(t.priority_name if t.priority else "none", "" if t.priority else "dim")
        fact("priority", pr)
        if t.due:
            style: Style | str = ""
            if t.open and t.due < day:
                style = palette("overdue")
            elif t.open and t.due == day:
                style = palette("today")
            fact("due", format_date_long(t.due, day, past="late" if t.open else "ago"), style)
        else:
            fact("due", "unscheduled", "dim")
        if t.scheduled:
            fact("scheduled", format_date_long(t.scheduled, day))
        if t.start:
            fact("start", format_date_long(t.start, day))
        fact("status", t.status_label)
        if t.done_date:
            fact("done", format_date_long(t.done_date, day))
        if t.cancelled_date:
            fact("cancelled", format_date_long(t.cancelled_date, day))
        tags = t.plain_tags
        fact("tags", " ".join(f"#{g}" for g in tags) if tags else "—", "" if tags else "dim")
        for n in tm.note_links(t):
            fact("link", f"[[{n}]]")
        fact("file", f"{t.file}:{t.lineno}", "dim")
        if t.depth:
            fact("nesting", f"level {t.depth} sub-task", "dim")
        body = Text()
        for i, (label, value) in enumerate(rows):
            if i:
                body.append("\n")
            body.append(f"{label:<10}", "dim")
            body.append_text(value)
        facts.update(body)

        kids = tm.children_of(tasks, t)
        closed_n = sum(1 for k in kids if k.closed)
        self.query_one("#ins-subhead", Label).update(
            f"SUB-TASKS {closed_n}/{len(kids)}" if kids else "SUB-TASKS")
        opts: list[Option] = []
        for k in kids:
            line = Text(no_wrap=True)
            box = "[x]" if k.done else ("[-]" if k.cancelled else ("[/]" if k.status == "/" else "[ ]"))
            line.append(f"{box} ", "dim" if k.closed else palette("status-progress") if k.status == "/" else "")
            line.append(k.description or "(no text)", "dim strike" if k.closed else "")
            if k.due and k.open:
                line.append(f"  {format_due(k.due, day)}", palette("overdue") if k.due < day else "dim")
            if k.note:
                line.append(f" {NOTE_GLYPH}", "dim")
            opts.append(Option(line, id=f"k:{k.id}"))
        if not opts:
            opts.append(Option(Text("none — t adds one", "dim italic"), id="k:none", disabled=True))
        kids_list.set_options(opts)
        if kids:
            # Repainting after a theme change or task update must not move the
            # child's cursor, even while a modal temporarily owns focus.
            kids_list.highlighted = next(
                (index for index, option in enumerate(opts) if option.id == child_id),
                min(child_index or 0, len(kids) - 1),
            )

        if t.note:
            note.update(Text(t.note))
        else:
            note.update(Text("no note — n opens a text field", "dim italic"))


# ---------------------------------------------------------------------------
# Modal dialogs — all share the #dlg card styling (see TaskApp.CSS)
# ---------------------------------------------------------------------------

def validate_project_name(raw: str, vault: str | Path | None = None) -> str:
    """Return a cleaned project name, validating its path before any write.

    Hosts without a vault get name validation only. TaskApp supplies its actual
    vault so linked paths and existing non-file targets are checked as well.
    """
    from pathlib import PureWindowsPath

    candidate = raw.strip().lstrip("#").strip()
    if candidate.casefold().startswith("project/"):
        candidate = candidate[len("project/"):]
    if not candidate:
        raise ValueError("Give the project a name containing letters or numbers.")
    windows = PureWindowsPath(candidate)
    segments = candidate.replace("\\", "/").split("/")
    if (windows.drive or windows.root or ":" in candidate or "\\" in candidate
            or any(part.strip() in (".", "..") for part in segments)
            or any(not part for part in segments)):
        raise ValueError("Use a relative project name, such as Work/Planning.")
    clean = tm.clean_project_name(raw)
    if not clean:
        raise ValueError("Give the project a name containing letters or numbers.")
    reserved = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    reserved.update(f"{prefix}{number}" for prefix in ("COM", "LPT")
                    for number in "123456789¹²³")
    for part in clean.split("/"):
        stem = part.partition(".")[0].upper()
        if (not part or part.endswith((".", " ")) or stem in reserved
                or any(ord(char) < 32 for char in part)):
            raise ValueError("Choose a project name that Windows can use as a folder or file.")
    if vault is not None:
        try:
            # The public history preflight checks this one explicit path and
            # records nothing because the context performs no mutation.
            with History(vault).record("Validate project", [f"Projects/{clean}.md"]):
                pass
        except (OSError, ValueError) as error:
            raise ValueError(f"Cannot use this project location: {error}") from error
    return clean


class AddScreen(ModalScreen["dict | None"]):
    """Quick-add. Inline emoji/dates/tags work: 'Call Amy 🔺 📅 2026-09-06'.

    ``parent`` (task text) switches to sub-task mode. ``project`` pre-fills
    the project box (used when adding from a project view).
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

    def __init__(self, projects: list[str], parent: str = "", project: str = "", initial: str = "") -> None:
        super().__init__()
        self._projects = projects
        self._parent_name = parent
        self._project = project
        self._initial_text = initial

    def action_cancel(self) -> None:
        self.dismiss(None)

    def compose(self) -> ComposeResult:
        with Vertical(id="dlg") as dlg:
            dlg.border_title = Text(f"Add sub-task under “{self._parent_name[:40]}”"
                                    if self._parent_name else "Add task")
            dlg.border_subtitle = "Enter = add · Esc = cancel"
            yield Label("Task", classes="field first")
            yield TaskInput(value=self._initial_text, placeholder="What needs doing?  #tags and task emoji work here",
                        id="text")
            yield Label("", id="form-error", classes="form-error")
            if not self._parent_name:
                yield Label("Project (optional · → accepts suggestion)",
                            classes="field")
                yield TaskInput(value=self._project, placeholder="Automation",
                            suggester=SuggestFromList(self._projects, case_sensitive=False),
                            id="proj")
            with Horizontal(classes="btns"):
                yield Button("Add", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#text", Input).focus()

    def _submit(self) -> None:
        text = self.query_one("#text", Input).value.strip()
        proj = ""
        if not self._parent_name:
            proj = self.query_one("#proj", Input).value.strip()
        if not text:
            self.query_one("#form-error", Label).update("Give your task a name.")
            self.query_one("#text", Input).focus()
            return
        if proj:
            try:
                proj = validate_project_name(proj, getattr(self.app, "vault", None))
            except ValueError as error:
                self.query_one("#form-error", Label).update(Text(str(error)))
                self.query_one("#proj", Input).focus()
                return
        self.dismiss({"text": text, "project": proj})

    @on(Input.Submitted)
    def _enter(self, _e: Input.Submitted) -> None:
        self._submit()

    @on(Button.Pressed)
    def _btn(self, e: Button.Pressed) -> None:
        if e.button.id == "ok":
            self._submit()
        else:
            self.dismiss(None)


class EditScreen(ModalScreen["str | None"]):
    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

    def __init__(self, initial: str) -> None:
        super().__init__()
        self._initial = initial

    def action_cancel(self) -> None:
        self.dismiss(None)

    def compose(self) -> ComposeResult:
        with Vertical(id="dlg") as dlg:
            dlg.border_title = "Edit task text"
            dlg.border_subtitle = "Enter = save · Esc = cancel"
            yield TaskInput(value=self._initial, id="text")
            yield Label("", id="form-error", classes="form-error")
            with Horizontal(classes="btns"):
                yield Button("Save", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#text", Input).focus()

    @on(Input.Submitted)
    def _enter(self, _e: Input.Submitted) -> None:
        self._submit()

    def _submit(self) -> None:
        value = self.query_one("#text", Input).value.strip()
        if not value:
            self.query_one("#form-error", Label).update("The task name cannot be empty.")
            self.query_one("#text", Input).focus()
            return
        self.dismiss(value)

    @on(Button.Pressed)
    def _btn(self, e: Button.Pressed) -> None:
        if e.button.id == "ok":
            self._submit()
        else:
            self.dismiss(None)


class DueScreen(ModalScreen["str | None"]):
    """Returns 'today', 'tomorrow', '+N', 'YYYY-MM-DD', 'clear', or None."""

    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

    def __init__(self, current: dt.date | None = None) -> None:
        super().__init__()
        self._current = current

    def action_cancel(self) -> None:
        self.dismiss(None)

    def compose(self) -> ComposeResult:
        with Vertical(id="dlg") as dlg:
            dlg.border_title = "Due date"
            dlg.border_subtitle = "Enter = set · Esc = cancel"
            cur = f"Now {self._current.isoformat()}" if self._current else "No due date yet"
            yield Label(f"{cur}  ·  today · tomorrow · mon · +7 · YYYY-MM-DD · clear",
                        classes="field first")
            yield TaskInput(placeholder="today", id="text")
            yield Label("", id="form-error", classes="form-error")
            with Horizontal(classes="btns quick"):
                yield Button("Today", id="q-today")
                yield Button("Tomorrow", id="q-tomorrow")
                yield Button("+7 days", id="q-week")
                yield Button("Clear", id="q-clear")
            with Horizontal(classes="btns"):
                yield Button("Set", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#text", Input).focus()

    @on(Input.Submitted)
    def _enter(self, _e: Input.Submitted) -> None:
        self._submit()

    def _submit(self) -> None:
        value = self.query_one("#text", Input).value.strip()
        if TaskApp.parse_due_text(value) == "invalid":
            self.query_one("#form-error", Label).update(
                "Try today, tomorrow, a weekday, +7, YYYY-MM-DD, or clear.")
            self.query_one("#text", Input).focus()
            return
        self.dismiss(value)

    @on(Button.Pressed)
    def _btn(self, e: Button.Pressed) -> None:
        quick = {"q-today": "today", "q-tomorrow": "tomorrow", "q-week": "+7",
                 "q-clear": "clear"}
        if e.button.id in quick:
            self.dismiss(quick[e.button.id])
        elif e.button.id == "ok":
            self._submit()
        else:
            self.dismiss(None)


class PickScreen(ModalScreen["str | None"]):
    """Generic chooser: options are (id, Text); returns the picked id."""

    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

    def __init__(self, title: str, options: list[tuple[str, Text]],
                 current: str | None = None, hint: str = "Enter = choose · Esc = cancel") -> None:
        super().__init__()
        self._title = title
        self._options = options
        self._current = current
        self._hint = hint

    def action_cancel(self) -> None:
        self.dismiss(None)

    def compose(self) -> ComposeResult:
        with Vertical(id="dlg") as dlg:
            dlg.border_title = self._title
            dlg.border_subtitle = self._hint
            yield OptionList(*[Option(text, id=oid) for oid, text in self._options],
                             id="opts", classes="first")

    def on_mount(self) -> None:
        ol = self.query_one("#opts", OptionList)
        ids = [oid for oid, _ in self._options]
        ol.highlighted = ids.index(self._current) if self._current in ids else 0
        ol.focus()

    @on(OptionList.OptionSelected)
    def _picked(self, e: OptionList.OptionSelected) -> None:
        self.dismiss(e.option_id)


class ProjectScreen(ModalScreen["str | None"]):
    """Filter-as-you-type project picker. Returns the project name, '' to
    remove the project, or None on cancel. Typing a new name offers
    "Create" — the project note is made on the spot."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("down", "next", "Next", show=False),
        Binding("up", "prev", "Previous", show=False),
    ]

    def __init__(self, projects: list[str], current: str, task_title: str) -> None:
        super().__init__()
        self._projects = projects
        self._current = current
        self._task_title = task_title

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_next(self) -> None:
        self.query_one("#opts", OptionList).action_cursor_down()

    def action_prev(self) -> None:
        self.query_one("#opts", OptionList).action_cursor_up()

    def compose(self) -> ComposeResult:
        with Vertical(id="dlg") as dlg:
            dlg.border_title = Text(f"Project — {self._task_title[:44]}")
            dlg.border_subtitle = "↑↓ move · Enter = choose · Esc = cancel"
            yield TaskInput(placeholder="Type to filter — or a new name to create it", id="filter")
            yield Label("", id="form-error", classes="form-error")
            yield OptionList(id="opts")

    def on_mount(self) -> None:
        self._populate("")
        self.query_one("#filter", Input).focus()

    def _populate(self, query: str) -> None:
        q = query.strip()
        ql = q.casefold()
        try:
            clean = validate_project_name(q) if q else ""
        except ValueError:
            clean = ""
        opts: list[Option] = []
        matches = [p for p in self._projects if ql in p.casefold()
                   or (clean and clean.casefold() in p.casefold())]
        for p in matches:
            is_cur = p.casefold() == self._current.casefold()
            text = Text(no_wrap=True)
            text.append("● " if is_cur else "◆ ", "bold" if is_cur else "")
            text.append(p)
            if is_cur:
                text.append("  (current)", "dim")
            opts.append(Option(text, id=f"p:{p}"))
        if clean and not any(p.casefold() == clean.casefold() for p in self._projects):
            text = Text(no_wrap=True)
            text.append("+ ", "bold")
            text.append(f"Create “{clean}”  — makes Projects/{clean}.md")
            opts.append(Option(text, id="new"))
        if self._current:
            text = Text(no_wrap=True)
            text.append("○ ")
            text.append("No project  (remove the tag)", "dim")
            opts.append(Option(text, id="none"))
        if not opts:
            opts.append(Option(Text("Type a name to create your first project", "dim italic"),
                               id="hint", disabled=True))
        ol = self.query_one("#opts", OptionList)
        ol.set_options(opts)
        first = next((i for i, o in enumerate(opts) if not o.disabled), None)
        ol.highlighted = first

    @on(Input.Changed, "#filter")
    def _changed(self, e: Input.Changed) -> None:
        self.query_one("#form-error", Label).update("")
        self._populate(e.value)

    @on(Input.Submitted, "#filter")
    def _enter(self, _e: Input.Submitted) -> None:
        raw = self.query_one("#filter", Input).value.strip()
        ol = self.query_one("#opts", OptionList)
        if ol.highlighted is not None:
            oid = ol.get_option_at_index(ol.highlighted).id
            if raw and oid in ("none", "hint") and self._validate(raw) is None:
                return
            self._pick(oid)
        else:
            self._validate(raw)

    @on(OptionList.OptionSelected, "#opts")
    def _selected(self, e: OptionList.OptionSelected) -> None:
        self._pick(e.option_id)

    def _pick(self, oid: str | None) -> None:
        if oid is None or oid == "hint":
            return
        if oid == "none":
            self.dismiss("")
        elif oid == "new":
            project = self._validate(self.query_one("#filter", Input).value)
            if project is not None:
                self.dismiss(project)
        elif oid.startswith("p:"):
            project = self._validate(oid[2:])
            if project is not None:
                self.dismiss(project)

    def _validate(self, raw: str) -> str | None:
        try:
            return validate_project_name(raw, getattr(self.app, "vault", None))
        except ValueError as error:
            self.query_one("#form-error", Label).update(Text(str(error)))
            self.query_one("#filter", Input).focus()
            return None


class ConfirmScreen(ModalScreen["bool"]):
    """Yes/no question. The safe answer (``no``) has focus; Esc = no."""

    BINDINGS = [Binding("escape", "cancel", "Keep", show=False)]

    def __init__(self, message: str, title: str = "Delete?",
                 yes: str = "Delete", no: str = "Keep") -> None:
        super().__init__()
        self._message, self._title, self._yes, self._no = message, title, yes, no

    def action_cancel(self) -> None:
        self.dismiss(False)

    def compose(self) -> ComposeResult:
        with Vertical(id="dlg") as dlg:
            dlg.border_title = Text(self._title)
            dlg.border_subtitle = Text(f"Esc = {self._no.lower()}")
            yield Label(Text(self._message), classes="field first")
            with Horizontal(classes="btns"):
                yield Button(self._yes, variant="error", id="ok")
                yield Button(self._no, variant="primary", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#cancel", Button).focus()

    @on(Button.Pressed)
    def _btn(self, e: Button.Pressed) -> None:
        self.dismiss(e.button.id == "ok")


class NoteScreen(ModalScreen["str | None"]):
    """A large text field for the task's note (saved as indented lines under
    the task in the markdown). Ctrl+S saves, Esc cancels — asking first if
    you typed something. Returns the new text, or None when cancelled."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("ctrl+s,ctrl+shift+s", "save", "Save", show=False),
    ]

    def __init__(self, title: str, initial: str) -> None:
        super().__init__()
        self._title = title
        self._initial = initial

    def compose(self) -> ComposeResult:
        with Vertical(id="dlg", classes="note") as dlg:
            dlg.border_title = Text(f"Note — {self._title[:60]}")
            dlg.border_subtitle = "Ctrl+S = save · Esc = cancel"
            yield TextArea(self._initial, id="note-text", soft_wrap=True,
                           show_line_numbers=False,
                           placeholder="Anything about this task — context, links, "
                                       "decisions, next steps. Plain text; lines wrap.")
            with Horizontal(classes="btns"):
                yield Label("", id="note-status")
                yield Button("Save", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        ta = self.query_one("#note-text", TextArea)
        ta.move_cursor(ta.document.end)
        ta.focus()
        self._status()

    def _text(self) -> str:
        return self.query_one("#note-text", TextArea).text

    def _status(self) -> None:
        text = self._text()
        n = len(text.splitlines()) if text.strip() else 0
        edited = text != self._initial
        self.query_one("#note-status", Label).update(
            f"{n} line{'s' if n != 1 else ''}" + ("  ·  unsaved" if edited else ""))

    @on(TextArea.Changed)
    def _changed(self, _e: TextArea.Changed) -> None:
        self._status()

    def action_save(self) -> None:
        self.dismiss(self._text())

    def action_cancel(self) -> None:
        if self._text() == self._initial:
            self.dismiss(None)
            return

        def _answer(discard: bool | None) -> None:
            if discard:
                self.dismiss(None)
        self.app.push_screen(
            ConfirmScreen("Throw away what you typed?", title="Discard changes?",
                          yes="Discard", no="Keep editing"), _answer)

    @on(Button.Pressed)
    def _btn(self, e: Button.Pressed) -> None:
        if e.button.id == "ok":
            self.action_save()
        else:
            self.action_cancel()


class ThemeScreen(ModalScreen["str | None"]):
    """Theme picker with live preview: ↑↓ applies the highlighted theme to
    the whole app behind the dialog, Enter keeps it, Esc puts the original
    back. Returns the chosen theme name, or None when cancelled."""

    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

    def __init__(self, current: str) -> None:
        super().__init__()
        self._original = current
        self._previewed = current

    def compose(self) -> ComposeResult:
        with Vertical(id="dlg", classes="themes") as dlg:
            dlg.border_title = "Theme"
            dlg.border_subtitle = "↑↓ preview · Enter = keep · Esc = revert"
            opts: list[Option | None] = [Option(Text("DARK", "bold"), id="h:dark", disabled=True)]
            for name, label, dark in THEMES:
                if not dark or name == HIGH_CONTRAST:
                    continue
                opts.append(Option(self._row(name, label), id=name))
            opts.append(None)
            opts.append(Option(Text("OTHER", "bold"), id="h:other", disabled=True))
            for name, label, dark in THEMES:
                if not dark or name == HIGH_CONTRAST:
                    opts.append(Option(self._row(name, label), id=name))
            yield OptionList(*opts, id="opts", classes="first")
            yield Label("Preview with ↑↓. Enter keeps your choice; Esc restores it.",
                        classes="hint")

    def _row(self, name: str, label: str) -> Text:
        line = Text(no_wrap=True)
        line.append_text(theme_swatch(name))
        line.append("  ")
        line.append(label, "bold" if name == self._original else "")
        if name == self._original:
            line.append("   current", "dim")
        return line

    def on_mount(self) -> None:
        ol = self.query_one("#opts", OptionList)
        for i, o in enumerate(ol.options):
            if o.id == self._original:
                ol.highlighted = i
                break
        ol.focus()

    @on(OptionList.OptionHighlighted, "#opts")
    def _preview(self, e: OptionList.OptionHighlighted) -> None:
        name = e.option_id or ""
        if name and not name.startswith("h:") and name != self._previewed:
            self._previewed = name
            self.app.preview_theme(name)   # type: ignore[attr-defined]

    @on(OptionList.OptionSelected, "#opts")
    def _keep(self, e: OptionList.OptionSelected) -> None:
        if e.option_id and not e.option_id.startswith("h:"):
            self.dismiss(e.option_id)

    def action_cancel(self) -> None:
        if self._previewed != self._original:
            self.app.preview_theme(self._original)   # type: ignore[attr-defined]
        self.dismiss(None)


class HelpScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape,h,question_mark", "cancel", "Close", show=False)]

    # Lowercase keys only — the capital letter in each label is the key.
    SECTIONS = (
        ("Navigate", (
            ("Ctrl+K  or  :", "Commands — search every action, view, and project"),
            ("1–7", "views: All open · Today · Overdue · Next 7 · Inbox · Priority · Completed"),
            ("Tab / Shift+Tab", "next / previous pane or field"),
            ("Alt+1 / 2 / 3", "focus views / tasks / inspector; ← → move between panes"),
            ("PgUp / PgDn", "scroll the focused list, inspector, or help"),
            ("↑ ↓", "move between tasks (Home/End, PgUp/PgDn too)"),
            ("f  or  /", "Find — Esc clears, ↓ or Enter jumps back to the list"),
            ("i  or  Enter", "Inspect — open/close the right-hand pane (facts, sub-tasks, note)"),
            ("r", "Rescan the vault (files edited in Obsidian show up)"),
        )),
        ("Tasks", (
            ("u / Ctrl+Z", "Undo the last task change (this session)"),
            ("Ctrl+Y", "Redo; external file edits are checked before restoring"),
            ("a", "Add a task (to Inbox, or to a project)"),
            ("t", "add a subTask under the selected task"),
            ("e", "Edit the task text"),
            ("n", "Note — a full text field saved under the task in the markdown"),
            ("c", "Complete — toggle done; the whole branch flips together (Space works too)"),
            ("s", "Status: open · in progress · done · forwarded · cancelled · question"),
            ("d", "Due date (today · tomorrow · weekday · +7 · date · clear)"),
            ("p", "Priority"),
            ("j", "proJect — filter the list, or type a new name to create one"),
            ("]  /  [", "indent under the previous task  /  outdent"),
            ("Delete", "delete the task, its note and its sub-tasks (asks first)"),
            ("o", "Open the note in your editor"),
        )),
        ("Reference notes", (
            ("8", "Notes library — standalone Markdown in Notes/"),
            ("Ctrl+N", "Capture a new reference note from anywhere"),
            ("a / e / Enter", "in Notes: new / edit / edit selected note"),
            ("/", "in Notes: search titles, full content, categories, tags, and projects"),
            ("c / t / j", "in Notes: filter category / tag / project; Esc clears filters"),
            ("l", "Link an existing note to a task, or a task to the selected note"),
            ("k", "Open the selected task's notes, or the selected note's tasks"),
            ("Ctrl+T", "in Notes: create a task from this note and keep the reference"),
            ("Alt+3 / Tab", "Focus the reading pane; arrows and PgUp/PgDn scroll"),
            ("Ctrl+S / Esc", "in the note editor: save / cancel with a discard check"),
            ("Ctrl+K", "Unlink tasks, inspect unreadable notes, and find every action"),
        )),
        ("App", (
            ("Ctrl+O", "Open Vault — browse folders, recent vaults, or set up a new folder"),
            ("Ctrl+S", "Confirm saved — task changes save immediately to Markdown"),
            ("m", "theMe — Teal · Ocean · Ember · Iris · Moss · Darcula · One Dark · Dark Teal (+ Light, High contrast)"),
            ("h", "Help — this screen"),
            ("q", "Quit"),
        )),
    )

    def action_cancel(self) -> None:
        self.dismiss(None)

    def compose(self) -> ComposeResult:
        with Vertical(id="dlg", classes="help") as dlg:
            dlg.border_title = "Keyboard guide"
            dlg.border_subtitle = "↑↓ / PgUp / PgDn scroll · Esc close"
            with VerticalScroll(id="help-content") as content:
                content.can_focus = True
                for section, keys in self.SECTIONS:
                    yield Label(section, classes="sect")
                    for k, what in keys:
                        line = Text()
                        line.append(f"{k:<17}", "bold")
                        line.append(what)
                        yield Static(line, classes="key")
            with Horizontal(classes="btns"):
                yield Button("Close", variant="primary", id="ok")

    def on_mount(self) -> None:
        self.query_one("#help-content").focus()

    @on(Button.Pressed)
    def _btn(self, _e: Button.Pressed) -> None:
        self.dismiss(None)


# ---------------------------------------------------------------------------
# The app
# ---------------------------------------------------------------------------

def guard_change(method):
    """Keep a filesystem or validation error from terminating the interface."""
    @wraps(method)
    def guarded(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except (OSError, ValueError, HistoryConflict, NoteConflict) as exc:
            self._change_failed(exc)
            return None
    return guarded


class OpeningVaultScreen(ModalScreen):
    """Keep the old vault inert while a folder is scanned off the UI thread."""
    BINDINGS = [Binding("escape", "cancel", "Cancel opening", priority=True)]

    def __init__(self, path: Path):
        super().__init__()
        self.path = path

    def compose(self) -> ComposeResult:
        with Vertical(id="dlg"):
            yield Label("Opening vault…", classes="sect")
            yield Static(Text(str(self.path)))
            yield Label("Reading your Markdown tasks.  Esc cancels opening.", classes="hint")

    def action_cancel(self) -> None:
        self.app.cancel_vault_open()


class TaskApp(NotesActions, App):
    """Markdown-native task manager for the C:\\Tasks vault."""

    ENABLE_COMMAND_PALETTE = False

    CSS = """
    ModalScreen { align: center middle; background: $background 60%; }

    /* top bar */
    #topbar { height: 3; background: $background; padding: 0 2; align-vertical: middle; border-bottom: solid $primary-muted; }
    #topbar.-compact { height: 1; border-bottom: none; }
    #brand { width: auto; color: $text-accent; text-style: bold; }
    #crumb { width: 1fr; color: $text-muted; padding: 0 2; content-align: center middle; text-wrap: nowrap; text-overflow: ellipsis; }
    #clock { width: auto; color: $text-muted; }

    /* body */
    #body { height: 1fr; background: $background; }
    #main { width: 1fr; padding: 0 1; background: $background; }
    #searchbar { display: none; height: 1; margin: 0 0 1 0; background: $panel; }
    #searchbar.-active { background: $primary 25%; }
    #search-icon { width: 3; content-align: center middle; color: $text-accent; text-style: bold; }
    #search {
        width: 1fr; height: 1; padding: 0 1 0 0;
        background: transparent; color: $foreground;
        & > .input--placeholder { color: $text-muted; }
    }
    #search-hint { width: auto; color: $text-muted; padding-right: 1; }
    #tasks {
        height: 1fr; margin-left: 0;
        border: none;
        padding: 0 1;
    }

    /* status bar */
    #statusbar { height: 1; background: $background; }
    #status-view { width: auto; padding: 0 1; background: $primary; color: $block-cursor-foreground; text-style: bold; }
    #status-info { width: 1fr; padding: 0 1; color: $text-muted; }
    #status-right { width: auto; padding: 0 1; color: $text-muted; }
    #contextbar { height: 1; width: 1fr; padding: 0 2; color: $text-muted; background: $background; text-wrap: nowrap; text-overflow: ellipsis; }
    #shortcuts { background: $background; }

    /* dialogs */
    #dlg {
        width: 70; max-width: 96%; height: auto; max-height: 94%; overflow-y: auto;
        background: $surface; border: round $accent; padding: 1 2;
        border-title-color: $text-accent; border-title-style: bold;
        border-subtitle-color: $text-muted;
    }
    #dlg .field { color: $text-muted; margin-top: 1; width: 1fr; height: auto; }
    #dlg .field.first { margin-top: 0; }
    #dlg Input { margin-top: 0; }
    #dlg OptionList { height: auto; max-height: 14; margin-top: 1; border: round $primary-muted; }
    #dlg OptionList.first { margin-top: 0; }
    #dlg OptionList:focus { border: round $accent; }
    #dlg .btns { height: 3; align: right middle; margin-top: 1; }
    #dlg .btns Button { margin-left: 1; min-width: 8; }
    #dlg .btns.quick { align: left middle; }
    #dlg .btns.quick Button { margin-left: 0; margin-right: 1; }
    #dlg .sect { color: $text-accent; text-style: bold; margin-top: 1; }
    #dlg .key { margin: 0 0 1 0; }
    #dlg .hint { color: $text-muted; margin-top: 1; }
    #dlg .form-error { height: auto; color: $text-error; }
    #dlg.help { width: 92; height: 90%; overflow-y: hidden; }
    #help-content { height: 1fr; padding: 0 1; border: none; }
    #help-content:focus { border-left: solid $accent; }
    #dlg.themes { width: 60; }

    /* note editor: a real text field, most of the screen */
    #dlg.note { width: 96; height: 80%; min-height: 16; }
    #note-text { height: 1fr; border: round $primary-muted; }
    #note-text:focus { border: round $accent; }
    #note-status { width: 1fr; color: $text-muted; content-align: left middle; }
    """

    # The wrapping shortcut dock provides the full visible reference.
    BINDINGS = [
        Binding("ctrl+k", "commands", "Commands", key_display="ctrl+k", priority=True),
        Binding("ctrl+o", "open_vault", "Open Vault", priority=True),
        Binding("colon", "commands", "Commands", show=False),
        Binding("a", "add", "Add"),
        Binding("e", "edit", "Edit"),
        Binding("space,x", "toggle", "Complete", key_display="space"),
        Binding("c", "complete_or_category", "Complete / category", show=False),
        Binding("8", "notes", "Notes", show=False),
        Binding("ctrl+n", "new_reference", "New note", show=False),
        Binding("ctrl+t", "task_from_reference", "Create task from note", show=False),
        Binding("l", "link_reference", "Link note", show=False),
        Binding("k", "linked_references", "Linked notes / tasks", show=False),
        Binding("d", "due", "Due"),
        Binding("p", "priority", "Priority", show=False),
        Binding("s", "status", "Status", show=False),
        Binding("t", "add_sub", "Subtask", show=False),
        Binding("j", "project", "Project", show=False),
        Binding("n", "note", "Note", show=False),
        Binding("i", "inspect", "Inspect", show=False),
        Binding("slash,f", "focus_search", "Find", key_display="/"),
        Binding("u,ctrl+z", "undo", "Undo"),
        Binding("ctrl+y", "redo", "Redo", show=False),
        Binding("m", "theme", "Theme", show=False),
        Binding("h,question_mark,f1", "help", "Help"),
        Binding("q", "quit", "Quit", show=False),
        Binding("alt+1", "focus_sidebar", "Focus views", show=False),
        Binding("alt+2", "focus_tasks", "Focus tasks", show=False),
        Binding("alt+3", "focus_inspector", "Focus inspector", show=False),
        # Hidden (documented in Help)
        Binding("ctrl+shift+s,ctrl+s", "save", "Saved to Markdown", show=False),
        Binding("o", "open_note", "Open in editor", show=False),
        Binding("r", "refresh", "Rescan", show=False),
        Binding("right_square_bracket", "indent", "Indent", show=False),
        Binding("left_square_bracket", "outdent", "Outdent", show=False),
        Binding("delete", "delete", "Delete", show=False),
        Binding("escape", "escape", "Clear search / close inspector", show=False),
        Binding("1", "view_0", show=False), Binding("2", "view_1", show=False),
        Binding("3", "view_2", show=False), Binding("4", "view_3", show=False),
        Binding("5", "view_4", show=False), Binding("6", "view_5", show=False),
        Binding("7", "view_6", show=False),
    ]

    def __init__(self, vault: str | Path | None = None,
                 theme: str | None = None, *, choose_vault: bool = False) -> None:
        super().__init__()
        discovered = discover_vault(vault)
        self._vault_ready = discovered is not None
        self._choose_on_start = choose_vault or not self._vault_ready
        # This placeholder is never scanned or edited before the user chooses.
        self.vault = discovered or Path.cwd().resolve()
        self.store = tm.Store(self.vault)
        self.notes_store = NotesStore(self.vault)
        self.note_category = self.note_tag = self.note_project = ""
        self._notes = []
        self._selected_note_file = ""
        self.history = History(self.vault)
        self._opening_vault = False
        self._open_generation = 0
        self._command_target: Task | None = None
        self._inspected_task: Task | None = None
        self._last_main_id: str | None = None
        self.error_log: Path | None = None
        self._error_recorded = False
        self.view = DEFAULT_VIEW
        self.project = ""
        self.search_query = ""
        self._context_ids: set[str] = set()
        self._summary = ""            # "17 open · 3 due ≤7d · 2 overdue" for the top bar
        for dark_theme, _label in DARK_THEMES:
            self.register_theme(dark_theme)
        self.register_theme(high_contrast_theme())
        self.theme_name = resolve_theme(theme)
        self.theme = self.theme_name

    # -- theme -----------------------------------------------------------------
    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if self._opening_vault and action != "quit":
            return False
        if not self._vault_ready and action not in {"open_vault", "quit", "commands", "help", "theme"}:
            return False
        # Editing a search or a text field must never restore task files.
        if action in ("undo", "redo") and isinstance(self.focused, (Input, TextArea)):
            return False
        if isinstance(self.screen, ModalScreen) and action in {
                "new_reference", "task_from_reference", "notes", "link_reference", "linked_references"}:
            return False
        return True

    @property
    def theme_label(self) -> str:
        return next((t[1] for t in THEMES if t[0] == self.theme_name), self.theme_name)

    def preview_theme(self, name: str) -> None:
        """Apply a theme to everything on screen without remembering it
        (the picker calls this as you move; Esc calls it with the original)."""
        if name not in [t[0] for t in THEMES]:
            return
        self.theme_name = name
        self.theme = name
        # Widgets that bake theme colors into text (sidebar, inspector) are
        # rebuilt once the new CSS variables have been applied.
        self.call_after_refresh(self.refresh_tasks)
        self.call_after_refresh(self._tick)

    def set_theme(self, name: str, persist: bool = True) -> None:
        """Apply a theme now and remember it in per-user settings."""
        if name not in [t[0] for t in THEMES]:
            return
        self.preview_theme(name)
        if persist and not os.environ.get("NO_COLOR"):
            try:
                write_theme_file(name)
            except OSError:
                pass
        self.announce(f"Theme: {self.theme_label}")

    # -- layout -------------------------------------------------------------
    def compose(self) -> ComposeResult:
        with Horizontal(id="topbar"):
            yield Label("◆ Taskman", id="brand")
            yield Label("", id="crumb")
            yield Label("", id="clock")
        with Horizontal(id="body"):
            sidebar = Sidebar(id="sidebar")
            sidebar.border_title = "NAVIGATE"
            yield sidebar
            with Vertical(id="main"):
                with Horizontal(id="searchbar"):
                    yield Label("/", id="search-icon")
                    yield SearchInput(placeholder="Find a task, project, or #tag…",
                                      id="search", compact=True, select_on_focus=False)
                    yield Label("Esc clear", id="search-hint")
                yield TaskList(id="tasks")
                notes = NotesWorkspace(id="notes-workspace")
                notes.display = False
                yield notes
            inspector = Inspector(id="inspector")
            inspector.display = False
            yield inspector
        yield Label("", id="contextbar")
        with Horizontal(id="statusbar"):
            yield Label("", id="status-view")
            yield Label("", id="status-info")
            yield Label("", id="status-right")
        yield ShortcutBar(id="shortcuts")

    def _handle_exception(self, error: Exception) -> None:
        if not self._error_recorded:
            self._error_recorded = True
            self.error_log = record_error(error, context={
                "screen": type(self.screen_stack[-1]).__name__ if self.screen_stack else "TaskApp",
            })
        super()._handle_exception(error)

    @on(ShortcutBar.Invoked)
    async def _shortcut_invoked(self, event: ShortcutBar.Invoked) -> None:
        await self.run_action(event.action)

    @on(ShortcutBar.Resized)
    def _shortcut_resized(self, _event: ShortcutBar.Resized) -> None:
        self._position_notifications()

    def _position_notifications(self) -> None:
        bars = list(self.query(ShortcutBar))
        if bars:
            # Textual assumes a one-line footer. Keep notifications above our
            # wrapping dock, status, and navigation hints at every width.
            bottom = bars[0].outer_size.height + 2
            for screen in self.screen_stack:
                for rack in screen.query("#textual-toastrack"):
                    rack.styles.margin = (0, 0, bottom, 0)

    def on_mount(self) -> None:
        self.title = "Taskman"
        if self._vault_ready:
            self.refresh_tasks(select=0)
            self._remember_vault()
        else:
            self._summary = "Choose a folder to begin"
            self._update_crumb()
        self._tick()
        self.set_interval(20, self._tick)
        self.query_one(TaskList).focus()
        if self._choose_on_start:
            self.call_after_refresh(self.action_open_vault)

    def _remember_vault(self) -> None:
        try:
            settings.remember_vault(self.vault)
        except OSError as exc:
            self.notify(f"Vault opened; recent folders could not be saved: {exc}", severity="warning", markup=False)

    def action_open_vault(self) -> None:
        if isinstance(self.screen, (VaultScreen, OpeningVaultScreen)):
            return
        if isinstance(self.screen, ModalScreen):
            # Finish the active editor first: its callbacks belong to this vault.
            self.notify("Finish or cancel this dialog before opening another vault.", markup=False)
            return

        def chosen(choice: VaultChoice | None) -> None:
            if choice is None:
                if not self._vault_ready:
                    self.exit()
                return
            self._opening_vault = True
            self._open_generation += 1
            generation = self._open_generation
            self.push_screen(OpeningVaultScreen(choice.path))
            self._prepare_vault(choice, generation)

        self.push_screen(VaultScreen(current=self.vault if self._vault_ready else None,
                                    welcome=not self._vault_ready), chosen)

    @work(thread=True, exclusive=True, group="open-vault")
    def _prepare_vault(self, choice: VaultChoice, generation: int) -> None:
        try:
            root = initialize_vault(choice.path) if choice.initialize else normalize_folder(choice.path)
            candidate = tm.Store(root)
            candidate.refresh(force=True)
            history = History(root)
            result = (root, candidate, history)
            error = None
        except (OSError, ValueError) as exc:
            result, error = None, str(exc)
        try:
            self.call_from_thread(self._finish_vault_open, generation, result, error)
        except RuntimeError:
            if self.is_running:
                raise

    def cancel_vault_open(self) -> None:
        self._open_generation += 1
        self._opening_vault = False
        if isinstance(self.screen, OpeningVaultScreen):
            self.pop_screen()
        if not self._vault_ready:
            self.call_after_refresh(self.action_open_vault)

    def _finish_vault_open(self, generation: int, result, error: str | None) -> None:
        if generation != self._open_generation:
            return
        self._opening_vault = False
        if isinstance(self.screen, OpeningVaultScreen):
            self.pop_screen()
        if error:
            self.notify(error, title="Could not open folder", severity="error", timeout=8, markup=False)
            if not self._vault_ready:
                self.call_after_refresh(self.action_open_vault)
            return
        self.vault, self.store, self.history = result
        self.notes_store = NotesStore(self.vault)
        self.note_category = self.note_tag = self.note_project = ""
        self._selected_note_file = ""
        self._notes = []
        self._vault_ready = True
        self.view, self.project, self.search_query = DEFAULT_VIEW, "", ""
        self._command_target = self._inspected_task = None
        self._last_main_id = None
        self._context_ids.clear()
        with self.prevent(Input.Changed):
            self.query_one("#search", Input).value = ""
        self.query_one(Inspector).display = False
        self._layout_chrome()
        self.refresh_tasks(select=0, reload=False)
        self.query_one(TaskList).focus()
        self._remember_vault()
        self._tick()
        self.announce(f"Opened {self.vault.name}")

    def _tick(self) -> None:
        now = dt.datetime.now()
        self.query_one("#clock", Label).update(
            f"{self.theme_label}  ·  {now.strftime('%a %b')} {now.day}  {now.strftime('%H:%M')}")

    def on_resize(self, _event: events.Resize) -> None:
        if self.is_mounted:
            self._layout_chrome()

    def _layout_chrome(self) -> None:
        """Keep task titles and due dates readable on smaller terminals.
        Number keys and the command menu reach every hidden view/project."""
        width = self.size.width
        insp = self.query_one(Inspector)
        sidebar = self.query_one(Sidebar)
        sidebar.display = width >= 96 and not (insp.display and width < 120)
        sidebar.styles.width = 26
        fullscreen = insp.display and width < 72
        self.query_one("#main").display = not fullscreen
        insp.styles.width = width if fullscreen else max(34, min(52, width // 3))
        insp.styles.margin = (0, 0, 0, 0 if fullscreen else 1)
        self.query_one("#topbar").set_class(self.size.height < 30, "-compact")
        self._update_crumb()
        self.query_one("#clock").display = width >= 100
        self.query_one("#search-hint").display = width >= 80
        self.query_one("#status-info").display = width >= 90
        if self.is_mounted:
            self.query_one(TaskList).border_title = Text(
                "TASKS" if self.size.height >= 27 else self._view_label().upper())
        if fullscreen and self.query_one(TaskList).has_focus:
            insp.focus()

    # -- data ---------------------------------------------------------------
    def _update_crumb(self) -> None:
        summary = f"{self.vault.name}   ·   {self._summary}" if self.size.width >= 100 else self._summary
        self.query_one("#crumb", Label).update(Text(summary))

    def _view_label(self) -> str:
        if self.project:
            return f"◆ {self.project}"
        return VIEW_LABEL.get(self.view, self.view)

    def refresh_tasks(self, select: int | None = None,
                      keep_id: str | None = None, *, reload: bool = True) -> None:
        """Reload the list.

        Cursor policy: with neither argument, stay on the current task
        (mutations). ``keep_id`` pins a task (falls back to ``select`` or the
        nearest row when it is gone). ``select`` alone jumps to that row —
        view switches pass ``select=0`` so a new view opens at the top.
        """
        if not self._vault_ready:
            return
        self._notes = self.notes_store.refresh()
        self._sync_notes_mode()
        if self.view == "notes":
            self._refresh_notes(reload=reload)
            return
        tl = self.query_one(TaskList)
        previous_main_id = tl.current.id if tl.current else None
        navigating = select is not None and keep_id is None
        child_id = None
        if self.focused and self.focused.id == "ins-kids":
            child = self._selected()
            child_id = child.id if child else None
            if tl.current:
                keep_id = tl.current.id
        if keep_id is None and select is None and tl.current is not None:
            keep_id = tl.current.id
        day = dt.date.today()
        tasks = self.store.refresh() if reload else self.store.tasks
        # Sidebar counts first: counts() runs view_tasks per view, and every
        # view_tasks call resets Task.context — so do it before we read flags.
        counts = tm.counts(tasks, day)
        matches = tm.view_tasks(tasks, self.view, day, project=self.project, query=self.search_query)
        kids = tm.child_index(tasks)
        rows: list[Row] = []
        for sec in tm.sections(matches, self.view, day):
            if rows:
                rows.append(HeaderRow(sec.key, "", 0))   # breathing room
            rows.append(HeaderRow(sec.key, sec.title, sec.count))
            for node in sec.nodes:
                rows.append(TaskRow(node.task, node.depth, node.prefix,
                                    tm.progress_of(kids, node.task), node.task.context))
        self._context_ids = {t.id for t in matches if t.context}
        tl.day = day
        tl.empty_message = EMPTY_STATE["search"] if self.search_query else EMPTY_STATE.get(
            self.view, EMPTY_STATE["all"])
        tl.set_rows(rows, keep_id=keep_id, select=select)

        n = sum(1 for t in matches if not t.context)
        label = self._view_label()
        tl.border_title = Text("TASKS" if self.size.height >= 27 else label.upper())
        tl.border_subtitle = Text(f"{n} match{'es' if n != 1 else ''} for “{self.search_query}”"
                                 if self.search_query else f"{n} task{'s' if n != 1 else ''}")
        self._summary = (f"{counts['all']} open · {counts['next7']} due ≤7d · "
                         f"{counts['overdue']} overdue")
        self._update_crumb()

        self.query_one(Sidebar).populate(counts, self.store.projects(),
                                         tm.project_counts(tasks), self.view, self.project, len(self._notes))
        # Status bar: what am I looking at, and how much of it is urgent.
        real = [t for t in matches if not t.context]
        hi = sum(1 for t in real if t.open and t.priority >= 4)
        late = sum(1 for t in real if t.open and t.due and t.due < day)
        soon = sum(1 for t in real if t.open and t.due and day <= t.due <= day + dt.timedelta(days=7))
        self.query_one("#status-view", Label).update(Text(label.upper()))
        self.query_one("#status-info", Label).update(Text(
            f"{n} task{'s' if n != 1 else ''} · {late} overdue · {soon} due ≤7d · {hi} high"
            + (f" · filter “{self.search_query}”" if self.search_query else "")))
        main_id = tl.current.id if tl.current else None
        inspected = tl.current
        if not navigating and main_id == previous_main_id and self._inspected_task:
            inspected = next((task for task in tasks if task.id == self._inspected_task.id), inspected)
        self._last_main_id = main_id
        self._update_inspector(inspected)
        if child_id:
            children = self.query_one("#ins-kids", OptionList)
            for index, option in enumerate(children.options):
                if option.id == f"k:{child_id}":
                    children.highlighted = index
                    break

    def _selected(self) -> Task | None:
        if self.view == "notes":
            return None
        if self._command_target is not None:
            return self._command_target
        if isinstance(self.focused, Inspector) and self._inspected_task is not None:
            return self._inspected_task
        if isinstance(self.focused, OptionList) and self.focused.id == "ins-kids":
            ol = self.focused
            if ol.highlighted is not None:
                oid = ol.get_option_at_index(ol.highlighted).id
                return next((t for t in self.store.tasks if f"k:{t.id}" == oid), None)
        return self.query_one(TaskList).current

    def _update_inspector(self, t: Task | None) -> None:
        if self.view == "notes":
            self._context_hint()
            return
        tl = self.query_one(TaskList)
        insp = self.query_one(Inspector)
        self.query_one("#status-right", Label).update(
            f"{tl.cursor_ordinal}/{tl.task_count}  ·  inspect {'open' if insp.display else 'closed'}")
        if insp.display:
            self._inspected_task = t
            insp.show(t, self.store.tasks, dt.date.today(), tl.style_for,
                      context=bool(t and t.id in self._context_ids))
            self._show_reference_links(t)
        self._context_hint()

    def _context_hint(self) -> None:
        if not self.is_mounted or not super().query("#contextbar"):
            return
        if isinstance(self.focused, SearchInput):
            hint = "FIND  ·  Enter / ↓ results  ·  Esc clear"
        elif isinstance(self.focused, Sidebar):
            hint = "VIEWS  ·  ↑↓ browse  ·  Enter / → tasks  ·  Ctrl+K projects"
        elif isinstance(self.focused, Inspector):
            hint = "INSPECT  ·  ↑↓ / PgUp / PgDn scroll  ·  Tab subtasks  ·  ← back"
        elif self.focused and self.focused.id == "ins-kids":
            hint = "SUBTASKS  ·  Space complete child  ·  e edit  ·  n note  ·  Esc back"
        elif self.focused and self.focused.id == "notes-preview":
            hint = "READING  ·  ↑↓ / PgUp / PgDn scroll  ·  ← notes  ·  e edit  ·  k linked tasks"
        elif self.view == "notes":
            hint = "NOTES  ·  ↑↓ browse  ·  Enter edit  ·  Tab read  ·  Esc clear filters  ·  Ctrl+K actions"
        else:
            hint = "↑↓ move  ·  Enter inspect  ·  Tab panes  ·  Ctrl+K all actions"
        self.query_one("#contextbar", Label).update(hint)
        self.query_one(ShortcutBar).set_mode(
            ("notes-search" if self.view == "notes" else "search") if isinstance(self.focused, SearchInput)
            else ("notes" if self.view == "notes" else "tasks"),
            can_undo=self.history.can_undo, can_redo=self.history.can_redo,
        )
        self._position_notifications()

    def on_descendant_focus(self, _event: events.DescendantFocus) -> None:
        self._context_hint()

    @contextmanager
    def _record(self, label: str, paths: list[str]):
        """Record only files touched by this change; restore navigation on undo."""
        current = self.query_one(TaskList).current
        context = {"view": self.view, "project": self.project, "query": self.search_query,
                   "task_id": current.id if current else None, "note_file": self._selected_note_file,
                   "note_category": self.note_category, "note_tag": self.note_tag,
                   "note_project": self.note_project}
        with self.history.record(label, paths, context=context):
            yield

    def _restore_history(self, redo: bool = False) -> None:
        try:
            entry = self.history.redo() if redo else self.history.undo()
        except (HistoryConflict, OSError, ValueError) as exc:
            self.notify(str(exc), title="Could not restore change", severity="warning", timeout=7, markup=False)
            return
        if entry is None:
            self.announce("Nothing to redo" if redo else "Nothing to undo")
            return
        context = entry.context or {}
        self.view = context.get("view", self.view)
        self.project = context.get("project", self.project)
        self.search_query = context.get("query", "")
        self._selected_note_file = context.get("note_file", "")
        self.note_category = context.get("note_category", "")
        self.note_tag = context.get("note_tag", "")
        self.note_project = context.get("note_project", "")
        self.query_one("#search", Input).value = self.search_query
        self.store.refresh(force=True)
        self.refresh_tasks(keep_id=context.get("task_id"))
        self.action_focus_tasks()
        self.announce(f"{'Redid' if redo else 'Undid'}: {entry.label}")

    def action_undo(self) -> None:
        self._restore_history()

    def action_redo(self) -> None:
        self._restore_history(redo=True)

    def announce(self, message: str) -> None:
        self.notify(message, timeout=2.5, markup=False)

    def _guard(self, callback):
        @wraps(callback)
        def guarded(*args, **kwargs):
            try:
                return callback(*args, **kwargs)
            except (OSError, ValueError, HistoryConflict, NoteConflict) as exc:
                self._change_failed(exc)
                return None
        return guarded

    def _change_failed(self, exc: Exception) -> None:
        # A storage helper may have updated its Task object before an I/O
        # error. Reload the source so the next action uses the saved state.
        try:
            self.store.refresh(force=True)
            self.refresh_tasks()
        except OSError:
            pass
        self.notify(str(exc), title="Could not save change", severity="error", timeout=7, markup=False)

    # -- events ---------------------------------------------------------------
    # Textual registers @on handlers on MessagePump classes, not plain mixins.
    @on(NotesWorkspace.Selected)
    def _notes_selection_event(self, event: NotesWorkspace.Selected) -> None:
        self._reference_selected(event)

    @on(NotesWorkspace.Activated)
    def _notes_activation_event(self, event: NotesWorkspace.Activated) -> None:
        self._reference_activated(event)

    @on(OptionList.OptionSelected, "#ins-references")
    def _reference_link_event(self, event: OptionList.OptionSelected) -> None:
        self._open_inspector_reference(event)

    @on(TaskList.Highlighted)
    def _row_moved(self, e: TaskList.Highlighted) -> None:
        task_id = e.task.id if e.task else None
        if task_id == self._last_main_id or not super().query(TaskList):
            return
        self._last_main_id = task_id
        self._update_inspector(e.task)

    @on(TaskList.Selected)
    def _row_chosen(self, e: TaskList.Selected) -> None:
        self.action_inspect()

    @on(Input.Changed, "#search")
    def _search_changed(self, e: Input.Changed) -> None:
        self.search_query = e.value
        cur = self._selected()   # stay on the task if it still matches, else top
        self.refresh_tasks(select=0, keep_id=cur.id if cur else None)

    @on(Input.Submitted, "#search")
    def _search_submitted(self, _e: Input.Submitted) -> None:
        self.action_focus_tasks()

    def _sidebar_pick(self, option_id: str | None) -> None:
        if not option_id or option_id.startswith("h:"):
            return
        kind, _, name = option_id.partition(":")
        if kind == "view":
            if self.view == name and not self.project and not self.search_query:
                return
            self.view, self.project = name, ""
        elif kind == "proj":
            if self.project.casefold() == name.casefold() and not self.search_query:
                return
            self.view, self.project = "project", name
        else:
            return
        self.search_query = ""
        self.query_one("#search", Input).value = ""
        self.refresh_tasks(select=0)

    @on(OptionList.OptionHighlighted, "#sidebar")
    def _sidebar_browse(self, e: OptionList.OptionHighlighted) -> None:
        if e.option_list.has_focus and e.option_list.is_mounted:   # browsing with ↑↓ switches live
            self._sidebar_pick(e.option_id)

    @on(OptionList.OptionSelected, "#sidebar")
    def _sidebar_selected(self, e: OptionList.OptionSelected) -> None:
        self._sidebar_pick(e.option_id)
        self.action_focus_tasks()

    @on(OptionList.OptionSelected, "#ins-kids")
    @guard_change
    def _inspector_kid_toggled(self, e: OptionList.OptionSelected) -> None:
        oid = e.option_id or ""
        kid = next((x for x in self.store.tasks if f"k:{x.id}" == oid), None)
        if kid is None:
            return
        parent = self._selected()
        with self._record("Complete subtask" if not kid.done else "Reopen subtask", [kid.file]):
            tm.toggle(self.vault, kid)
        self.refresh_tasks(keep_id=parent.id if parent else None)
        self.announce(f"{'Done' if kid.done else 'Reopened'}: {kid.description or kid.id}")
        ol = self.query_one("#ins-kids", OptionList)
        if ol.option_count:
            ol.highlighted = min(e.option_index, ol.option_count - 1)

    # -- pickers ------------------------------------------------------------------
    def priority_picker(self, t: Task | None) -> PickScreen:
        opts = []
        for level in (5, 4, 3, 2, 1, 0):
            text = Text(no_wrap=True)
            text.append(f"{PRIO_GLYPH[level]} ", "bold")
            text.append(Task(file="", lineno=0, priority=level).priority_name)
            if level:
                text.append(f"   {tm.PRIORITY_BY_LEVEL[level]}", "dim")
            opts.append((str(level), text))
        return PickScreen("Priority", opts, current=str(t.priority if t else 0))

    def status_picker(self, t: Task | None) -> PickScreen:
        opts = []
        for ch, label in tm.STATUS_CHOICES:
            glyph, _ = STATUS_GLYPH.get(ch, ("?", ""))
            text = Text(no_wrap=True)
            text.append(f"{glyph} ", "bold")
            text.append(label)
            text.append(f"   [{ch}]", "dim")
            opts.append((ch, text))
        return PickScreen("Status", opts, current=(t.status if t else " "))

    def project_picker(self, t: Task) -> ProjectScreen:
        return ProjectScreen(self.store.projects(), t.project, t.description or t.id)

    # -- shared mutations -----------------------------------------------------------
    @guard_change
    def toggle_task(self, t: Task) -> None:
        _open, total = tm.open_subtask_count(self.store.tasks, t)
        with self._record("Complete task" if not t.done else "Reopen task", [t.file]):
            tm.toggle(self.vault, t)
        self.refresh_tasks(keep_id=t.id)
        extra = f" (+{total} sub-task{'s' if total != 1 else ''})" if total else ""
        self.announce(f"{'Done' if t.done else 'Reopened'}: {t.description or t.id}{extra}")

    @staticmethod
    def parse_due_text(raw: str, day: dt.date | None = None) -> "dt.date | None | str":
        """'today'→date, 'tomorrow'→date, '+N'→date, 'YYYY-MM-DD'→date,
        'clear'→None, anything else→'invalid'."""
        s = raw.strip().casefold()
        day = day or dt.date.today()
        if s in ("clear", "none", "-"):
            return None
        if s in ("today", "tod"):
            return day
        if s in ("tomorrow", "tom"):
            return day + dt.timedelta(days=1)
        weekdays = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
        for index, name in enumerate(weekdays):
            if s in (name, name[:3]):
                return day + dt.timedelta(days=(index - day.weekday()) % 7 or 7)
        try:
            if s.startswith("+") and s[1:].isdigit():
                return day + dt.timedelta(days=int(s[1:]))
            return dt.date.fromisoformat(raw.strip())
        except (ValueError, OverflowError):
            return "invalid"

    @guard_change
    def apply_due(self, t: Task, raw: str) -> bool:
        parsed = self.parse_due_text(raw)
        if parsed == "invalid":
            self.announce("Due date not understood — use today / tomorrow / +7 / YYYY-MM-DD / clear")
            return False
        assert parsed is None or isinstance(parsed, dt.date)
        with self._record("Change due date", [t.file]):
            tm.set_due(self.vault, t, parsed)
        self.refresh_tasks(keep_id=t.id)
        self.announce(f"Due {'cleared' if parsed is None else format_date_long(parsed, dt.date.today())}")
        return True

    @guard_change
    def apply_status(self, t: Task, status: str) -> None:
        with self._record("Change status", [t.file]):
            if status in ("x", "X") and not t.done:
                tm.toggle(self.vault, t)       # branch semantics + ✅ stamp
            elif status == " " and t.done:
                tm.toggle(self.vault, t)       # reopen the branch
            else:
                tm.set_status(self.vault, t, status)
        self.refresh_tasks(keep_id=t.id)
        self.announce(f"Status: {t.status_label}")

    def edit_note(self, t: Task, after: "Callable[[], None] | None" = None) -> None:
        """Open the note editor for ``t``; save straight into the markdown."""
        def _done(text: str | None) -> None:
            if text is None:
                return
            if text.strip() == (t.note or "").strip():
                self.announce("Note unchanged")
            else:
                with self._record("Edit note", [t.file]):
                    tm.set_note(self.vault, t, text)
                self.refresh_tasks(keep_id=t.id)
                self.announce("Note cleared" if not text.strip() else "Note saved")
            if after:
                after()
        self.push_screen(NoteScreen(t.description or t.id, t.note), self._guard(_done))

    @guard_change
    def apply_project(self, t: Task, project: str) -> None:
        project = tm.clean_project_name(project)
        paths = [t.file] + ([f"Projects/{project}.md"] if project else [])
        with self._record("Change project", paths):
            if project:
                _path, created = tm.ensure_project_file(self.vault, project)
            else:
                created = False
            tm.set_project(self.vault, t, project)
        self.refresh_tasks(keep_id=t.id)
        if not project:
            self.announce("Project removed")
        elif created:
            self.announce(f"Created Projects/{tm.clean_project_name(project)}.md and assigned the task")
        else:
            self.announce(f"Project: {project}")

    def action_save(self) -> None:
        self.announce("All changes saved to Markdown")

    # -- actions --------------------------------------------------------------
    def action_commands(self) -> None:
        if isinstance(self.screen, CommandScreen):
            self.screen.dismiss(None)
            return
        if isinstance(self.screen, ModalScreen):
            return
        target = self._selected()
        has_task = target is not None
        actions = [
            ("add", "Add task", "a", "Capture a task in Inbox or the current project", "new create capture", True),
            ("toggle", "Reopen task" if target and target.done else "Complete task", "Space", "Toggle the selected task and its subtasks", "done check finish", has_task),
            ("edit", "Edit task", "e", "Change the selected task's text", "rename text", has_task),
            ("due", "Change due date", "d", "Today, tomorrow, a weekday, +7, or a date", "schedule postpone deadline", has_task),
            ("priority", "Change priority", "p", "Set how urgent this task is", "high low important", has_task),
            ("status", "Change status", "s", "Open, in progress, completed, or cancelled", "start progress cancel", has_task),
            ("project", "Assign project", "j", "Choose a project or create one", "move organize", has_task),
            ("note", "Edit task note", "n", "Write context, links, or next steps", "notes details", has_task),
            ("add_sub", "Add subtask", "t", "Break the selected task into smaller steps", "child new", has_task),
            ("focus_inspector", "Read task details", "→ / Alt+3", "Open and focus the inspector; scroll with the keyboard", "inspect note read", has_task),
            ("indent", "Indent task", "]", "Nest under the previous task", "child hierarchy", has_task),
            ("outdent", "Outdent task", "[", "Move up one level", "parent hierarchy", has_task),
            ("delete", "Delete task", "Delete", "Confirm before deleting this task and its subtasks", "remove", has_task),
            ("undo", "Undo" + (f": {self.history.undo_label}" if self.history.can_undo else ""), "u / Ctrl+Z", "Restore the last change from this session", "restore recover", self.history.can_undo),
            ("redo", "Redo" + (f": {self.history.redo_label}" if self.history.can_redo else ""), "Ctrl+Y", "Reapply an undone change", "restore", self.history.can_redo),
            ("focus_search", "Find tasks", "/", "Search task text, projects, and tags in this view", "filter search", True),
            ("theme", "Change theme", "m", "Preview colors live; Escape restores your current theme", "appearance color dark teal", True),
            ("refresh", "Rescan vault", "r", "Reload changes made in Obsidian or your editor", "refresh sync", True),
            ("open_note", "Open source note", "o", "Open this Markdown file in your editor", "file markdown", has_task),
            ("save", "Save changes", "Ctrl+S", "Task changes are saved automatically to Markdown", "save files", True),
            ("open_vault", "Open Vault…", "Ctrl+O", "Choose a folder or set up a new vault", "folder switch recent workspace", True),
            ("help", "Keyboard guide", "h / F1", "Browse all shortcuts and interactions", "help keys", True),
            ("quit", "Quit Taskman", "q", "Task changes are already saved to Markdown", "exit close", True),
        ]
        commands = [Command(key, title, shortcut, description, keywords, enabled)
                    for key, title, shortcut, description, keywords, enabled in actions]
        if self.view == "notes":
            commands = [command for command in commands if command.id in {
                "undo", "redo", "focus_search", "theme", "refresh", "open_note", "save",
                "open_vault", "help", "quit"}]
            commands = [Command(command.id, "Find notes" if command.id == "focus_search" else command.title,
                                command.shortcut,
                                "Search titles, content, categories, tags, and projects" if command.id == "focus_search" else command.description,
                                command.keywords,
                                bool(self.query_one(NotesWorkspace).current) if command.id == "open_note" else command.enabled)
                        for command in commands]
        references = self._reference_commands()
        commands = references + commands if self.view == "notes" else commands + references
        commands += [Command(f"view:{name}", f"Go to {label}", key,
                             "Open this view and clear the search filter", "view navigate")
                     for key, name, label in SIDEBAR_VIEWS]
        commands += [Command(f"proj:{name}", f"Open project: {name}", "",
                             "Show open tasks in this project", "project navigate")
                     for name in self.store.projects()]

        def choose(command_id: str | None) -> None:
            if command_id is None:
                return
            if command_id.startswith(("view:", "proj:")):
                self._sidebar_pick(command_id)
                self.action_focus_tasks()
                return
            # Preserve the highlighted child while the palette temporarily owns focus.
            self._command_target = target
            try:
                getattr(self, f"action_{command_id}")()
            finally:
                self._command_target = None
        self.push_screen(CommandScreen(commands, context=target.description if target else self._view_label()), choose)

    def action_focus_tasks(self) -> None:
        if self.query_one(Inspector).display and self.size.width < 72:
            self._set_inspector(False)
        if not self.query_one("#search", Input).value:
            self.query_one("#searchbar").display = False
        if self.view == "notes":
            self.query_one(NotesWorkspace).focus_list()
        else:
            self.query_one(TaskList).focus()

    def action_focus_sidebar(self) -> None:
        sidebar = self.query_one(Sidebar)
        if sidebar.display:
            sidebar.focus()
        else:
            self.action_commands()

    def action_focus_inspector(self) -> None:
        if self.view == "notes":
            self.query_one(NotesWorkspace).focus_preview()
            return
        selected = self._selected()
        self._set_inspector(True)
        if selected is not None:
            self._update_inspector(selected)
        self.query_one(Inspector).focus()

    def action_focus_search(self) -> None:
        if not self.query_one("#main").display:
            self._set_inspector(False)
        self.query_one("#searchbar").display = True
        self.query_one("#search", Input).focus()

    def action_escape(self) -> None:
        """Esc: clear a search first; otherwise close the inspector."""
        search = self.query_one("#search", Input)
        if self.query_one("#searchbar").display:
            search.value = ""          # triggers Changed -> refresh
            self.query_one("#searchbar").display = False
        elif self.query_one(Inspector).display:
            self._set_inspector(False)
        elif self.view == "notes":
            self.note_category = self.note_tag = self.note_project = ""
            self.refresh_tasks()
        self.action_focus_tasks()

    def _set_inspector(self, show: bool) -> None:
        insp = self.query_one(Inspector)
        insp.display = show
        self._layout_chrome()
        self._update_inspector(self.query_one(TaskList).current)

    def action_inspect(self) -> None:
        if isinstance(self.screen, ModalScreen):
            return
        if self.view == "notes":
            self.query_one(NotesWorkspace).focus_preview()
            return
        insp = self.query_one(Inspector)
        self._set_inspector(not insp.display)
        if insp.display and self.size.width < 72:
            insp.focus()
        else:
            self.query_one(TaskList).focus()

    def action_view_0(self) -> None: self._goto_view(0)
    def action_view_1(self) -> None: self._goto_view(1)
    def action_view_2(self) -> None: self._goto_view(2)
    def action_view_3(self) -> None: self._goto_view(3)
    def action_view_4(self) -> None: self._goto_view(4)
    def action_view_5(self) -> None: self._goto_view(5)
    def action_view_6(self) -> None: self._goto_view(6)

    def _goto_view(self, i: int) -> None:
        _, name, _label = SIDEBAR_VIEWS[i]
        self.view, self.project = name, ""
        self.search_query = ""
        self.query_one("#search", Input).value = ""
        self.refresh_tasks(select=0)
        self.action_focus_tasks()

    def action_refresh(self) -> None:
        sel = self._selected()
        self.store.refresh(force=True)
        self.refresh_tasks(keep_id=sel.id if sel else None)
        self.announce("Vault rescanned")

    def action_theme(self) -> None:
        if isinstance(self.screen, ModalScreen):
            return

        def _done(name: str | None) -> None:
            if name:
                self.set_theme(name)
        self.push_screen(ThemeScreen(self.theme_name), _done)

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_complete_or_category(self) -> None:
        if self.view == "notes":
            self.action_note_category()
        else:
            self.action_toggle()

    def action_toggle(self) -> None:
        t = self._selected()
        if t:
            self.toggle_task(t)

    def action_status(self) -> None:
        t = self._selected()
        if not t:
            return

        def _done(res: str | None) -> None:
            if res is not None:
                self.apply_status(t, res)
        self.push_screen(self.status_picker(t), _done)

    def action_note(self) -> None:
        if self.view == "notes":
            self.action_edit_reference()
            return
        t = self._selected()
        if not t:
            self.announce("Select a task first, then press n")
            return
        self.edit_note(t)

    def action_project(self) -> None:
        if self.view == "notes":
            self.action_note_project()
            return
        t = self._selected()
        if not t:
            self.announce("Select a task first, then press j")
            return

        def _done(res: str | None) -> None:
            if res is not None:
                self.apply_project(t, res)
        self.push_screen(self.project_picker(t), _done)

    def _show_new_task(self, t: Task, project: str = "") -> None:
        """Make sure a freshly added task is on screen (switch view if not)."""
        self.search_query = ""
        self.query_one("#search", Input).value = ""
        self.action_focus_tasks()
        self.refresh_tasks(keep_id=t.id)
        cur = self._selected()
        if cur is None or cur.id != t.id:
            if project:
                self.view, self.project = "project", project
            else:
                self.view, self.project = "all", ""
            self.refresh_tasks(keep_id=t.id)

    def action_add(self) -> None:
        if self.view == "notes":
            self.action_new_reference()
            return
        def _done(res: dict | None) -> None:
            if not res:
                return
            project = tm.clean_project_name(res["project"])
            path = f"Projects/{project}.md" if project else "Tasks/Inbox.md"
            with self._record("Add task", [path]):
                t = tm.add_task(self.vault, res["text"], project=project)
            self._show_new_task(t, tm.clean_project_name(res["project"]) if res["project"] else "")
            self.announce(f"Added: {t.description}")
        self.push_screen(AddScreen(self.store.projects(), project=self.project), self._guard(_done))

    def action_add_sub(self) -> None:
        if self.view == "notes":
            self.action_note_tag()
            return
        t = self._selected()
        if not t:
            self.announce("Select a parent task first, then press t")
            return

        def _done(res: dict | None) -> None:
            if not res:
                return
            with self._record("Add subtask", [t.file]):
                nt = tm.add_subtask(self.vault, t, res["text"])
            self._show_new_task(nt, self.project)
            self.announce(f"Added sub-task under {t.description or t.id}")
        self.push_screen(AddScreen(self.store.projects(), parent=t.description or t.id), self._guard(_done))

    def action_edit(self) -> None:
        if self.view == "notes":
            self.action_edit_reference()
            return
        t = self._selected()
        if not t:
            return

        def _done(res: str | None) -> None:
            if res:
                with self._record("Edit task", [t.file]):
                    tm.edit_text(self.vault, t, res)
                self.refresh_tasks(keep_id=t.id)
                self.announce("Saved")
        self.push_screen(EditScreen(t.description), self._guard(_done))

    @guard_change
    def action_indent(self) -> None:
        t = self._selected()
        if not t:
            return
        with self._record("Indent task", [t.file]):
            result = tm.indent_task(self.vault, t)
        if result is None:
            self.announce("Cannot indent — no previous task to nest under")
            return
        self.refresh_tasks(keep_id=t.id)
        self.announce("Nested under the previous task")

    @guard_change
    def action_outdent(self) -> None:
        t = self._selected()
        if not t:
            return
        with self._record("Outdent task", [t.file]):
            result = tm.outdent_task(self.vault, t)
        if result is None:
            self.announce("Already top-level — nothing to lift")
            return
        self.refresh_tasks(keep_id=t.id)
        self.announce("Lifted one level")

    def action_due(self) -> None:
        t = self._selected()
        if not t:
            return

        def _done(res: str | None) -> None:
            if res is not None:
                self.apply_due(t, res)
        self.push_screen(DueScreen(t.due), _done)

    def action_priority(self) -> None:
        t = self._selected()
        if not t:
            return

        def _done(res: str | None) -> None:
            if res is not None and res.isdigit():
                with self._record("Change priority", [t.file]):
                    tm.set_priority(self.vault, t, int(res))
                self.refresh_tasks(keep_id=t.id)
                self.announce(f"Priority: {t.priority_name}")
        self.push_screen(self.priority_picker(t), self._guard(_done))

    def action_delete(self) -> None:
        t = self._selected()
        if not t:
            return
        _open, total = tm.open_subtask_count(self.store.tasks, t)
        msg = (f"Delete “{t.description or t.id}” and its {total} "
               f"sub-task{'s' if total != 1 else ''}?" if total
               else f"Delete “{t.description or t.id}”?")

        def _done(ok: bool | None) -> None:
            if ok:
                with self._record("Delete task", [t.file]):
                    n = tm.delete_task(self.vault, t)
                self.refresh_tasks(select=self.query_one(TaskList).cursor)
                extra = f" ({n} lines)" if n > 1 else ""
                self.announce(f"Task deleted{extra}")
        self.push_screen(ConfirmScreen(msg), self._guard(_done))

    def action_open_note(self) -> None:
        t = self._selected()
        if self.view == "notes":
            t = self.query_one(NotesWorkspace).current
        if not t:
            return
        path = self.vault / t.file
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(path))  # noqa: S606 -- user opens own note
            elif sys.platform == "darwin":
                subprocess.run(["open", str(path)], check=False)
            else:
                subprocess.run(["xdg-open", str(path)], check=False)
            self.announce(f"Opened {t.file}")
        except Exception as exc:
            self.announce(f"Could not open note: {exc}")


def run(vault: str | Path | None = None, theme: str | None = None, *, choose_vault: bool = False) -> int:
    app = TaskApp(vault, theme, choose_vault=choose_vault)
    app.run()
    if app.error_log:
        print(f"Taskman error report: {app.error_log}", file=sys.stderr)
    return app.return_code


if __name__ == "__main__":
    _theme = None
    _args = sys.argv[1:]
    if "--theme" in _args:
        _i = _args.index("--theme")
        _theme = _args[_i + 1] if _i + 1 < len(_args) else None
    raise SystemExit(run(theme=_theme))
