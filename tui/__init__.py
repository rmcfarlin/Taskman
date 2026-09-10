"""Taskman — a keyboard-first task manager for Markdown folders.

Usage:
    python -m tui                       # full terminal UI (opens on "Now")
    python -m tui --plain now           # plain-text list (screen-reader / pipes)
    python -m tui --check               # vault health summary
    python -m tui --add "text"          # quick-add to Tasks/Inbox.md
    python -m tui --add "text" --project Name   # ... or into Projects/Name.md
    python -m tui --note "text" --under Tasks/Inbox.md:9   # set a task's note

Primary modules:
    taskman.py     parser, vault store, views, mutations, layout (stdlib only)
    cli.py         stable-ID commands and structured JSON output (stdlib only)
    app.py         Textual UI: TaskList, Sidebar, Inspector, dialogs, themes
    notes*.py      reference notes store, workspace, actions, and dialogs
    history.py     session undo/redo of vault file bytes
    vaults.py      vault discovery and non-overwriting setup
    updater.py     GitHub release check and portable install helper
"""

__version__ = "3.3.1"
