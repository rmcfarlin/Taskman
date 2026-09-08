"""Taskman — a keyboard-first task manager for Markdown folders.

Usage:
    python -m tui                       # full terminal UI (opens on "Now")
    python -m tui --plain now           # plain-text list (screen-reader / pipes)
    python -m tui --check               # vault health summary
    python -m tui --add "text"          # quick-add to Tasks/Inbox.md
    python -m tui --add "text" --project Name   # ... or into Projects/Name.md
    python -m tui --note "text" --under Tasks/Inbox.md:9   # set a task's note

Files:
    taskman.py   parser, vault store, views, mutations, layout (stdlib only)
    cli.py       stable-ID commands and structured JSON output (stdlib only)
    app.py       Textual UI: TaskList (Line API table), Sidebar, Inspector
                 pane, Notes workspace, dialogs and live-preview themes (m),
                 local autosave and a keyboard-accessible vault chooser
"""

__version__ = "3.3.0"
