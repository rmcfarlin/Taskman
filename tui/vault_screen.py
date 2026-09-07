"""A keyboard folder chooser; browsing and previewing never modify a folder."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import os
from pathlib import Path

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.events import Resize
from textual.screen import ModalScreen
from textual.widgets import Button, Input, OptionList, Static
from textual.widgets.option_list import Option

from .settings import recent_vaults
from .vaults import normalize_folder, plan_vault
from .dialog_style import COMPACT_DIALOG_CSS


@dataclass(frozen=True)
class VaultChoice:
    path: Path
    initialize: bool = False


class FolderInput(Input):
    BINDINGS = [Binding("down", "folders", "Folders", show=False)]

    def action_folders(self) -> None:
        self.screen.query_one("#vault-folders", OptionList).focus()


class FolderList(OptionList):
    BINDINGS = [Binding("left,backspace", "parent_folder", "Parent", show=False)]

    def action_parent_folder(self) -> None:
        self.screen.action_parent_folder()


def _directory_snapshot(value: str):
    """All potentially slow filesystem calls run off the UI event loop."""
    setup_error = ""
    try:
        plan = plan_vault(value)
        root = plan.root
    except (OSError, ValueError) as error:
        # A setup conflict must not prevent opening an otherwise usable folder.
        root = normalize_folder(value)
        plan = None
        setup_error = str(error)
    if not root.exists():
        return root, plan, [], False, setup_error
    normalize_folder(root)
    children: list[Path] = []
    with os.scandir(root) as entries:
        for entry in entries:
            try:
                child = Path(entry.path)
                if (entry.is_dir(follow_symlinks=False) and not entry.is_symlink()
                        and not (hasattr(child, "is_junction") and child.is_junction())):
                    children.append(child)
            except OSError:
                continue  # A disappearing child must not break the entire picker.
    children.sort(key=lambda path: path.name.casefold())
    return root, plan, children, True, setup_error


class VaultScreen(ModalScreen[VaultChoice | None]):
    """Choose an existing folder, with an explicit optional setup preview.

    Enter in the path field browses it; Enter in the list enters a folder.
    Ctrl+Enter opens the folder displayed in the path. Ctrl+N sets it up.
    The app receiving the result owns all actual setup and vault switching.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False, priority=True),
        Binding("ctrl+l", "location", "Path", show=False, priority=True),
        Binding("alt+up", "parent_folder", "Parent", show=False, priority=True),
        Binding("alt+r", "recent", "Recent", show=False, priority=True),
        Binding("ctrl+enter", "open_folder", "Open", show=False, priority=True),
        Binding("ctrl+n", "setup_folder", "Set up", show=False, priority=True),
    ]

    DEFAULT_CSS = """
    VaultScreen { align: center middle; background: $background 70%; }
    VaultScreen #vault-dialog {
        width: 94%; max-width: 104; height: 90%; max-height: 34;
        padding: 1 2; border: round $primary; background: $surface;
    }
    VaultScreen #vault-title { height: 1; color: $text; text-style: bold; }
    VaultScreen #vault-intro { height: 2; color: $text-muted; }
    VaultScreen #vault-path { margin: 0; }
    VaultScreen #vault-browser-heading { height: 1; margin-top: 1; }
    VaultScreen #vault-location { width: 1fr; height: 1; color: $text-muted; }
    VaultScreen #vault-recent {
        min-width: 18; width: 18; height: 1; min-height: 1;
        margin: 0; padding: 0; border: none; color: $accent;
        background: $surface;
    }
    VaultScreen #vault-recent:focus { background: $primary; color: $text; }
    VaultScreen #vault-folders {
        height: 1fr; min-height: 3; margin: 0; padding: 0;
        border: solid $primary-muted; background: $background;
    }
    VaultScreen #vault-folders:focus { border: solid $accent; }
    VaultScreen #vault-preview { height: 3; color: $text-muted; margin-top: 1; }
    VaultScreen #vault-error { display: none; height: auto; max-height: 2; color: $error; }
    VaultScreen #vault-error.has-error { display: block; }
    VaultScreen #vault-actions { height: 1; margin-bottom: 1; }
    VaultScreen #vault-actions Button { min-width: 10; width: auto; margin: 0 1 0 0; }
    VaultScreen #vault-hints { height: 2; color: $text-muted; }
    VaultScreen.compact #vault-dialog { width: 100%; height: 100%; padding: 0 1; }
    VaultScreen.compact #vault-intro { display: none; }
    VaultScreen.compact #vault-browser-heading { margin-top: 0; }
    VaultScreen.compact #vault-preview { height: 3; margin-top: 0; }
    VaultScreen.compact #vault-hints { height: 2; }
    """ + COMPACT_DIALOG_CSS

    def __init__(self, current: Path | None = None, welcome: bool = False):
        super().__init__()
        self.current = current
        self.welcome = welcome
        self._folder: Path | None = None
        self._plan = None
        self._entries: list[Path] = []
        self._generation = 0
        self._validated_input = ""
        self._showing_recent = False
        self._busy = False
        self._exists = False

    def compose(self) -> ComposeResult:
        with Vertical(id="vault-dialog", classes="compact-dialog"):
            yield Static("Welcome to Taskman" if self.welcome else "Open vault",
                         id="vault-title", markup=False)
            yield Static("Choose a folder for your Markdown tasks. Your files stay on your computer.",
                         id="vault-intro", markup=False)
            yield FolderInput(value=str(self.current or Path.home()),
                              placeholder="Folder path — Enter to browse", compact=True, id="vault-path")
            with Horizontal(id="vault-browser-heading"):
                yield Static("Folders", id="vault-location", markup=False)
                yield Button("Recent · Alt+R", id="vault-recent")
            yield FolderList(id="vault-folders")
            yield Static("Choose a folder to see its setup.", id="vault-preview", markup=False)
            yield Static("", id="vault-error", markup=False)
            with Horizontal(id="vault-actions"):
                yield Button("Open folder", variant="primary", id="vault-open", disabled=True)
                yield Button("Set up & open", id="vault-setup", disabled=True)
                yield Button("Cancel", id="vault-cancel")
            yield Static("Ctrl+L Path   ↑↓ / Enter Browse   Alt+R Recent\n"
                         "Ctrl+Enter Open   Ctrl+N Set up   Esc Cancel", id="vault-hints", markup=False)

    def on_mount(self) -> None:
        self.set_class(self.size.height < 28 or self.size.width < 80, "compact")
        self.query_one("#vault-path", Input).focus()
        self._browse(self.query_one("#vault-path", Input).value)

    def on_resize(self, event: Resize) -> None:
        self.set_class(event.size.height < 28 or event.size.width < 80, "compact")

    def _error(self, message: str) -> None:
        widget = self.query_one("#vault-error", Static)
        widget.update(message)
        widget.set_class(bool(message), "has-error")

    def _set_actions(self) -> None:
        ready = (not self._busy and self._folder is not None
                 and self.query_one("#vault-path", Input).value == self._validated_input)
        self.query_one("#vault-open", Button).disabled = not (ready and self._exists)
        self.query_one("#vault-setup", Button).disabled = not (
            ready and self._plan is not None and (self._plan.missing_dirs or self._plan.missing_files))

    @on(Input.Changed, "#vault-path")
    def _path_changed(self, event: Input.Changed) -> None:
        if self.is_mounted:
            self._set_actions()

    @on(Input.Submitted, "#vault-path")
    def _path_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self._browse(event.value, focus_list=True)

    def _browse(self, value: str, *, focus_list: bool = False) -> None:
        self._generation += 1
        self._busy = True
        self._error("")
        self.query_one("#vault-location", Static).update("Reading folder…")
        self._set_actions()
        self.run_worker(self._load(value, self._generation, focus_list),
                        group="vault-browse", exclusive=True, exit_on_error=False)

    async def _load(self, value: str, generation: int, focus_list: bool) -> None:
        try:
            folder, plan, children, exists, setup_error = await asyncio.to_thread(_directory_snapshot, value)
        except (OSError, ValueError) as error:
            if generation != self._generation or not self.is_mounted:
                return
            self._busy = False
            self._plan = None
            self._validated_input = ""
            self.query_one("#vault-location", Static).update("Folder unavailable")
            self.query_one("#vault-preview", Static).update("Enter another path or choose a recent folder.")
            self._error(str(error))
            self._set_actions()
            self.query_one("#vault-path", Input).focus()
            return
        if generation != self._generation or not self.is_mounted:
            return
        self._busy = False
        self._folder, self._plan = folder, plan
        self._exists = exists
        self._validated_input = str(folder)
        self.query_one("#vault-path", Input).value = self._validated_input
        self._showing_recent = False
        self.query_one("#vault-recent", Button).label = "Recent · Alt+R"
        self.query_one("#vault-location", Static).update(
            f"Folders · {len(children)}" if exists else "New folder · setup required")
        self._entries = ([folder.parent] if folder.parent != folder else []) + children
        options = []
        for index, path in enumerate(self._entries):
            label = "↑  Parent folder" if index == 0 and path == folder.parent else f"▸  {path.name}"
            options.append(Option(Text(label), id=str(index)))
        if not exists:
            options.append(Option("Set up & open creates this folder.", disabled=True))
        elif not options:
            options.append(Option("No subfolders. You can open this folder.", disabled=True))
        listing = self.query_one("#vault-folders", OptionList)
        listing.clear_options().add_options(options)
        listing.highlighted = 0 if self._entries else None
        missing = [*plan.missing_dirs, *plan.missing_files] if plan else []
        if setup_error:
            self.query_one("#vault-preview", Static).update(f"Open existing files.\nSetup unavailable: {setup_error}")
        elif missing:
            names = ", ".join(missing)
            detail = "Existing files are kept." if exists else "The folder will be created."
            self.query_one("#vault-preview", Static).update(f"Set up adds: {names}\n{detail}")
        else:
            self.query_one("#vault-preview", Static).update("Ready to open. Taskman folder structure is already in place.")
        self._set_actions()
        if focus_list:
            listing.focus()

    @on(OptionList.OptionSelected, "#vault-folders")
    def _folder_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        if event.option.id is not None:
            index = int(event.option.id)
            if index < len(self._entries):
                self._browse(str(self._entries[index]), focus_list=True)

    @on(Button.Pressed)
    def _button(self, event: Button.Pressed) -> None:
        event.stop()
        actions = {"vault-open": self.action_open_folder,
                   "vault-setup": self.action_setup_folder,
                   "vault-cancel": self.action_cancel,
                   "vault-recent": self.action_recent}
        action = actions.get(event.button.id)
        if action is not None:
            action()

    def action_location(self) -> None:
        path = self.query_one("#vault-path", Input)
        path.focus()
        path.action_select_all()

    def action_parent_folder(self) -> None:
        if self._folder is not None:
            self._browse(str(self._folder.parent), focus_list=True)

    def action_recent(self) -> None:
        if self._showing_recent and self._folder is not None:
            self._browse(str(self._folder), focus_list=True)
            return
        self._generation += 1
        self.workers.cancel_group(self, "vault-browse")
        self._busy = False
        self._showing_recent = True
        self._error("")
        self.query_one("#vault-location", Static).update("Recent vaults")
        self.query_one("#vault-recent", Button).label = "Folders · Alt+R"
        self.run_worker(self._load_recent(self._generation),
                        group="vault-browse", exclusive=True, exit_on_error=False)

    async def _load_recent(self, generation: int) -> None:
        try:
            recent = await asyncio.to_thread(recent_vaults)
        except (OSError, ValueError) as error:
            recent = []
            if self.is_mounted and generation == self._generation:
                self._error(str(error))
        if not self.is_mounted or generation != self._generation:
            return
        self._entries = recent
        options = [Option(Text(str(path)), id=str(index)) for index, path in enumerate(recent)]
        if not options:
            options = [Option("No recent vaults. Enter a folder path above.", disabled=True)]
        listing = self.query_one("#vault-folders", OptionList)
        listing.clear_options().add_options(options)
        listing.highlighted = 0 if recent else None
        listing.focus()
        self._set_actions()

    def _choose(self, initialize: bool) -> None:
        value = self.query_one("#vault-path", Input).value
        if self._busy:
            return
        if self._folder is None or value != self._validated_input:
            self._browse(value)
            return  # Show the setup preview before a second, explicit action.
        if not initialize and not self._exists:
            self._error("This folder does not exist yet. Choose Set up & open to create it.")
            return
        if initialize and self._plan is None:
            self._error("Setup is unavailable for this folder. Open folder uses its existing files.")
            return
        if initialize and not (self._plan.missing_dirs or self._plan.missing_files):
            initialize = False
        self.dismiss(VaultChoice(self._folder, initialize=initialize))

    def action_open_folder(self) -> None:
        self._choose(False)

    def action_setup_folder(self) -> None:
        self._choose(True)

    def action_cancel(self) -> None:
        self._generation += 1
        self.dismiss(None)
