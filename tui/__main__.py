"""Installed taskman command, frozen app entry point, and plain Markdown CLI."""
from __future__ import annotations
from pathlib import Path
import sys

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tui import __version__, cli, taskman, settings
from tui.vaults import initialize_vault


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    taskman._utf8_stdout()
    parser = cli.ArgumentParser(prog="taskman", allow_abbrev=False,
                                description="Your tasks, in any folder. Markdown files stay yours.")
    parser.add_argument("folder", nargs="?", help="folder to open (same as --vault)")
    parser.add_argument("--vault", metavar="PATH", help="open a folder as your vault")
    parser.add_argument("--open-vault", action="store_true", help="choose a folder when the app opens")
    parser.add_argument("--init", metavar="PATH", help="create missing vault structure without replacing existing files")
    parser.add_argument("--theme", metavar="NAME", help="theme name, or 'list'")
    parser.add_argument("--version", action="version", version=f"Taskman {__version__}")
    cli.add_arguments(parser, include_vault=False)
    try:
        args = parser.parse_args(raw)
        if args.folder and args.vault:
            parser.error("use either a folder argument or --vault")
        vault = args.vault or args.folder
        plain = cli.requested(args)
        if args.init and (vault or plain or args.open_vault or args.theme):
            parser.error("--init PATH is a separate command")
        if args.open_vault and plain:
            parser.error("--open-vault is available in the interactive app")
        if args.theme is not None and plain:
            parser.error("--theme is available in the interactive app")
        if args.init:
            root = initialize_vault(args.init)
            settings.remember_vault(root)
            print(f"Vault ready: {root}")
            print(f'Open it with: taskman --vault "{root}"')
            return 0
        if plain:
            args.vault = vault
            return cli.run(args)
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
        return cli.report_error(exc, json_output="--json" in raw)


if __name__ == "__main__":
    raise SystemExit(main())
