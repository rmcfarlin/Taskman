"""Installed taskman command, frozen app entry point, and plain Markdown CLI."""
from __future__ import annotations
import argparse
from pathlib import Path
import sys

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tui import __version__, taskman, settings
from tui.vaults import initialize_vault


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="taskman", description="Your tasks, in any folder. Markdown files stay yours.")
    parser.add_argument("folder", nargs="?", help="folder to open (same as --vault)")
    parser.add_argument("--vault", metavar="PATH", help="open a folder as your vault")
    parser.add_argument("--open-vault", action="store_true", help="choose a folder when the app opens")
    parser.add_argument("--init", metavar="PATH", help="create missing vault structure without replacing existing files")
    parser.add_argument("--theme", metavar="NAME", help="theme name, or 'list'")
    parser.add_argument("--version", action="version", version=f"Taskman {__version__}")
    parser.add_argument("--plain", metavar="VIEW", help="print all, today, overdue, next7, inbox, priority, completed, or projects")
    parser.add_argument("--check", action="store_true", help="show vault health and task counts")
    parser.add_argument("--add", metavar="TEXT", help="add a task to the inbox or --project")
    parser.add_argument("--sub", metavar="TEXT", help="add a subtask under --under FILE:LINE")
    parser.add_argument("--note", metavar="TEXT", help="replace the note under --under FILE:LINE")
    parser.add_argument("--under", metavar="FILE:LINE", help="parent task for --sub or --note")
    parser.add_argument("--project", metavar="NAME", help="project filter or destination for --add")
    parser.add_argument("--search", metavar="TEXT", help="filter the plain task listing")
    args = parser.parse_args(argv)
    if args.folder and args.vault:
        parser.error("use either a folder argument or --vault")
    vault = args.vault or args.folder
    plain = args.check or any(getattr(args, name) is not None for name in ("plain", "add", "sub", "note"))
    if args.init and (vault or plain or args.open_vault):
        parser.error("--init PATH is a separate command")
    if args.open_vault and plain:
        parser.error("--open-vault is available in the interactive app")
    try:
        if args.init:
            root = initialize_vault(args.init)
            settings.remember_vault(root)
            print(f"Vault ready: {root}")
            print(f'Open it with: taskman --vault "{root}"')
            return 0
        if plain:
            forwarded = ["--vault", vault] if vault else []
            if args.check:
                forwarded.append("--check")
            for name in ("plain", "add", "sub", "note", "under", "project", "search"):
                value = getattr(args, name)
                if value is not None:
                    forwarded.extend((f"--{name}", value))
            return taskman.main(forwarded)
        try:
            from tui.app import THEMES, run
        except ModuleNotFoundError as exc:
            if (exc.name or "").split(".", 1)[0] not in {"textual", "rich"}:
                raise
            print(f"Taskman cannot start: {exc.name} is not installed for {sys.executable}.", file=sys.stderr)
            print("Use the standalone Taskman download, or run install.ps1 from the source download.", file=sys.stderr)
            return 1
        if args.theme == "list":
            print(" ".join(theme[0] for theme in THEMES))
            return 0
        if args.open_vault:
            return run(vault, args.theme, choose_vault=True) or 0
        return run(vault, args.theme) or 0
    except (OSError, ValueError) as exc:
        print(f"Taskman: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
