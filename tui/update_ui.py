"""Offer a release, then download, verify, and restart after acceptance."""
from __future__ import annotations

import webbrowser
import inspect
from typing import Awaitable, Callable

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static

from . import __version__, updater
from .dialog_style import COMPACT_DIALOG_CSS


RELEASES_URL = "https://github.com/rmcfarlin/Taskman/releases"


class UpdateScreen(ModalScreen[None]):
    """One explicit acceptance authorizes download, verification, and restart."""

    BINDINGS = [("escape", "close", "Close")]
    DEFAULT_CSS = """
    UpdateScreen { align: center middle; background: $background 70%; }
    #update-dialog { width: 84; max-width: 96%; height: 28; max-height: 94%;
        border: round $accent; background: $surface; padding: 0 1; }
    #update-current { height: 1; color: $text-muted; }
    #update-status { height: auto; width: 1fr; margin-top: 1; color: $text; }
    #update-detail { height: auto; width: 1fr; color: $text-muted; }
    #update-error { height: auto; width: 1fr; color: $error; display: none; }
    #update-error.has-error { display: block; }
    #update-notes-scroll { height: 1fr; min-height: 1; margin-top: 1;
        scrollbar-size-vertical: 1; }
    #update-notes { height: auto; }
    #update-buttons { height: 1; margin-top: 1; align-horizontal: right; }
    #update-buttons Button { height: 1; min-height: 1; border: none;
        min-width: 7; width: auto; margin-left: 1; padding: 0 1; background: $panel; }
    #update-buttons Button:focus { background: $primary; color: $block-cursor-foreground;
        text-style: bold; }
    """ + COMPACT_DIALOG_CSS

    def __init__(self, *, install_handler: Callable[[updater.PreparedUpdate], Awaitable[None] | None],
                 release=None, support=None, check_on_mount: bool = True) -> None:
        super().__init__()
        self._install_handler = install_handler
        self.release = release
        self.prepared = None
        self.support = support
        self._busy = not check_on_mount
        self._check_on_mount = check_on_mount
        self._update_closed = False
        self._generation = 0
        self._state = "checking"

    def compose(self) -> ComposeResult:
        with Vertical(id="update-dialog", classes="compact-dialog") as dialog:
            dialog.border_title = "Taskman updates"
            dialog.border_subtitle = "Esc close"
            yield Label(f"Installed: {__version__}", id="update-current")
            yield Label("Checking GitHub for updates…", id="update-status")
            with VerticalScroll(id="update-notes-scroll"):
                yield Label("", id="update-detail")
                yield Label("", id="update-error")
                yield Static("", id="update-notes")
            with Horizontal(id="update-buttons"):
                yield Button("Update now", variant="primary", id="update-install", disabled=True)
                yield Button("GitHub", id="update-release")
                yield Button("Later", id="update-close")

    def on_mount(self) -> None:
        self.query_one("#update-close", Button).focus()
        if self.release is not None and self.support is not None:
            self._checked(self.release, self.support)
        elif self._check_on_mount:
            self._begin_check()

    def _begin_check(self) -> None:
        self._generation += 1
        self._busy, self._state = True, "checking"
        self._error("")
        self.query_one("#update-status", Label).update("Checking GitHub for updates…")
        self.query_one("#update-install", Button).disabled = True
        self._check(self._generation)

    def _deliver(self, generation: int, callback, *args) -> None:
        if self._update_closed or generation != self._generation:
            return
        try:
            self.app.call_from_thread(self._receive, generation, callback, args)
        except RuntimeError:
            # The user may close the dialog or quit while a request completes.
            pass

    def _receive(self, generation: int, callback, args) -> None:
        if not self._update_closed and self.is_mounted and generation == self._generation:
            callback(*args)

    @work(thread=True, exclusive=True, group="release-check", exit_on_error=False)
    def _check(self, generation: int) -> None:
        try:
            release = updater.check_for_update(__version__)
            support = updater.installation_support() if release is not None else None
        except Exception as error:
            self._deliver(generation, self._check_failed, str(error))
        else:
            self._deliver(generation, self._checked, release, support)

    def _checked(self, release, support) -> None:
        self._busy = False
        self.release, self.support = release, support
        button = self.query_one("#update-install", Button)
        if release is None:
            self._state = "current"
            self.query_one("#update-status", Label).update("No newer stable release is available.")
            self.query_one("#update-detail", Label).update("You can check again whenever you want.")
            button.label, button.disabled = "Check again", False
            button.refresh(layout=True)
            self._close_label("Close")
            return
        self._state = "available"
        self.query_one("#update-status", Label).update(Text(f"Taskman {release.version} is available."))
        self.query_one("#update-notes", Static).update(Text(release.body or "See GitHub for release notes."))
        self.query_one("#update-detail", Label).update(Text(
            "Update now downloads and verifies this release, then restarts Taskman automatically. Your vault and settings are kept."
            if support.supported else support.reason))
        button.label, button.disabled = "Update now", not support.supported
        button.refresh(layout=True)
        self._close_label("Later")

    def _check_failed(self, message: str) -> None:
        self._busy, self._state = False, "check-failed"
        self.query_one("#update-status", Label).update("Could not check for updates.")
        self._error(message)
        button = self.query_one("#update-install", Button)
        button.label, button.disabled = "Try again", False
        button.refresh(layout=True)
        self._close_label("Close")

    def _close_label(self, text: str) -> None:
        button = self.query_one("#update-close", Button)
        button.label = text
        button.refresh(layout=True)

    def _error(self, message: str) -> None:
        label = self.query_one("#update-error", Label)
        label.update(Text(message))
        label.set_class(bool(message), "has-error")

    def _progress(self, generation: int, message: str) -> None:
        if self._update_closed or generation != self._generation:
            raise updater.UpdateCancelled("Update download cancelled.")
        self._deliver(generation, self._show_progress, message)

    def _show_progress(self, message: str) -> None:
        self.query_one("#update-status", Label).update(Text(message))

    @work(thread=True, exclusive=True, group="release-download", exit_on_error=False)
    def _download(self, generation: int) -> None:
        try:
            prepared = updater.prepare_update(self.release,
                progress=lambda message: self._progress(generation, message))
            if self._update_closed or generation != self._generation:
                updater.discard_update(prepared)
                return
        except updater.UpdateCancelled:
            return
        except Exception as error:
            self._deliver(generation, self._download_failed, str(error))
        else:
            try:
                self.app.call_from_thread(self._accept_download, generation, prepared)
            except RuntimeError:
                updater.discard_update(prepared)

    def _accept_download(self, generation, prepared) -> None:
        if self._update_closed or not self.is_mounted or generation != self._generation:
            self.app.run_worker(lambda: updater.discard_update(prepared), thread=True,
                                group="update-cleanup", exit_on_error=False)
            return
        self.prepared = prepared
        self._downloaded()

    def _download_failed(self, message: str) -> None:
        self._busy, self._state = False, "available"
        self.query_one("#update-status", Label).update("Could not prepare the update.")
        self._error(message)
        button = self.query_one("#update-install", Button)
        button.label, button.disabled = "Update now", False
        button.refresh(layout=True)
        self._close_label("Later")

    def _downloaded(self) -> None:
        self._busy, self._state = True, "launching"
        self.query_one("#update-close", Button).disabled = True
        self.query_one("#update-status", Label).update("Update verified. Starting the installer…")
        self.query_one("#update-detail", Label).update(
            "Taskman will restart automatically. The previous app is kept as a backup.")
        self._install()

    @work(exclusive=True, group="release-install", exit_on_error=False)
    async def _install(self) -> None:
        try:
            result = self._install_handler(self.prepared)
            if inspect.isawaitable(result):
                await result
        except Exception as error:
            prepared, self.prepared = self.prepared, None
            self.app.run_worker(lambda: updater.discard_update(prepared), thread=True,
                                group="update-cleanup", exit_on_error=False)
            self._busy, self._state = False, "available"
            self._show_progress("The installer could not start. Taskman is still open.")
            button = self.query_one("#update-install", Button)
            button.label, button.disabled = "Update now", False
            button.refresh(layout=True)
            self.query_one("#update-close", Button).disabled = False
            self._close_label("Later")
            self._error(str(error))
        else:
            # The helper owns the stage and waits for the app to exit.
            self.prepared = None
            self._update_closed = True

    @on(Button.Pressed)
    async def _pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "update-close":
            self.action_close()
        elif event.button.id == "update-release":
            self._open_release()
        elif event.button.id == "update-install" and not self._busy:
            if self._state in {"current", "check-failed"}:
                self._begin_check()
            elif self._state == "available" and self.release is not None:
                self._busy, self._state = True, "downloading"
                self._error("")
                button = self.query_one("#update-install", Button)
                button.label, button.disabled = "Updating…", True
                button.refresh(layout=True)
                self._close_label("Cancel")
                self._show_progress("Preparing the download…")
                self._download(self._generation)

    @work(thread=True, group="release-page", exit_on_error=False)
    def _open_release(self) -> None:
        url = self.release.html_url if self.release is not None else RELEASES_URL
        try:
            if not webbrowser.open(url):
                raise OSError(f"Open this page in your browser: {url}")
        except Exception as error:
            self._deliver(self._generation, self._error, str(error))

    def action_close(self) -> None:
        if self._state == "launching":
            return
        self._update_closed = True
        self._generation += 1
        if self.prepared is not None:
            prepared, self.prepared = self.prepared, None
            # A screen worker can be cancelled on unmount; the app owns this
            # bounded cleanup so it may finish after the dialog closes.
            self.app.run_worker(lambda: updater.discard_update(prepared), thread=True,
                                group="update-cleanup", exit_on_error=False)
        self.dismiss(None)

    def on_unmount(self) -> None:
        self._update_closed = True
