"""Background update discovery and explicit consent to install releases."""
from __future__ import annotations

import asyncio
import time

from textual import work
from textual.screen import ModalScreen
from textual.widgets import Input, TextArea

from . import __version__, updater
from .update_ui import UpdateScreen


class UpdateActions:
    UPDATE_CHECK_INTERVAL = 6 * 60 * 60

    def _init_update_state(self) -> None:
        self._automatic_updates_started = False
        self._automatic_updates_started_at = 0.0
        self._automatic_update_inflight = False
        self._automatic_update_generation = 0
        self._automatic_update_manual = False
        self._pending_update = None
        self._offered_update_versions: set[str] = set()
        self._active_update_screen = None

    def _start_automatic_updates(self) -> None:
        # Headless runs include tests, screenshots, and distribution probes.
        # They must stay deterministic and never contact GitHub implicitly.
        if self.is_headless or self._automatic_updates_started:
            return
        self._automatic_updates_started = True
        self._automatic_updates_started_at = time.monotonic()
        self.set_interval(self.UPDATE_CHECK_INTERVAL, self._check_automatic_updates)
        self.set_interval(2, self._offer_pending_update)
        self._check_automatic_updates()

    def _check_automatic_updates(self) -> None:
        if (not self.is_running or self._automatic_update_inflight
                or self._active_update_screen is not None):
            return
        if self._pending_update is not None:
            self._offer_pending_update()
            return
        self._automatic_update_inflight = True
        self._automatic_update_manual = False
        self._automatic_update_generation += 1
        self._discover_update(self._automatic_update_generation)

    @work(thread=True, group="automatic-update-check", exit_on_error=False)
    def _discover_update(self, generation: int) -> None:
        release = support = None
        error = ""
        try:
            release = updater.check_for_update(__version__)
            support = updater.installation_support() if release is not None else None
        except Exception as exc:
            # An offline machine should open normally without a warning.
            error = str(exc)
        try:
            self.call_from_thread(self._automatic_update_checked, generation,
                                  release, support, error)
        except RuntimeError:
            pass  # The user quit while GitHub was responding.

    def _automatic_update_checked(self, generation, release, support, error="") -> None:
        if generation != self._automatic_update_generation or not self.is_running:
            return
        self._automatic_update_inflight = False
        if self._automatic_update_manual:
            # A manual request can adopt the check already in progress. It
            # still shows current/offline results, without another request.
            if release is not None:
                self._offered_update_versions.add(release.version)
            screen = self._active_update_screen
            if screen is not None:
                self.call_after_refresh(self._deliver_manual_update_result,
                                        screen, release, support, error)
            return
        if error or release is None or release.version in self._offered_update_versions:
            return
        self._pending_update = release, support
        self._offer_pending_update()

    def _deliver_manual_update_result(self, screen, release, support, error) -> None:
        if (self._active_update_screen is not screen or not screen.is_mounted
                or screen._update_closed):
            return
        if error:
            screen._check_failed(error)
        else:
            screen._checked(release, support)

    def _offer_pending_update(self) -> None:
        if (self._pending_update is None or not self.is_running
                or self._active_update_screen is not None
                or isinstance(self.screen, ModalScreen)
                or self._opening_vault or self._pushing_vault
                or isinstance(self.focused, (Input, TextArea))
                or (self._automatic_updates_started
                    and time.monotonic() - self._automatic_updates_started_at < 15)):
            return
        release, support = self._pending_update
        self._pending_update = None
        if release.version in self._offered_update_versions:
            return
        self._offered_update_versions.add(release.version)
        self._open_update_screen(release=release, support=support)

    def action_check_updates(self) -> None:
        if isinstance(self.screen, ModalScreen) or self._opening_vault or self._pushing_vault:
            return
        if self._active_update_screen is not None:
            return
        if self._pending_update is not None:
            release, support = self._pending_update
            self._pending_update = None
            self._offered_update_versions.add(release.version)
            self._open_update_screen(release=release, support=support)
        elif self._automatic_update_inflight:
            self._automatic_update_manual = True
            self._open_update_screen(check_on_mount=False)
        else:
            self._open_update_screen()

    def _open_update_screen(self, *, release=None, support=None, check_on_mount=True) -> None:
        async def install(prepared) -> None:
            args = ["--vault", str(self.vault)] if self._vault_ready else []
            await asyncio.to_thread(updater.launch_update, prepared, relaunch_args=args)
            self.exit()

        screen = UpdateScreen(install_handler=install, release=release,
                              support=support, check_on_mount=check_on_mount)
        self._active_update_screen = screen

        def closed(_result) -> None:
            if screen.release is not None:
                self._offered_update_versions.add(screen.release.version)
            if self._active_update_screen is screen:
                self._active_update_screen = None

        self.push_screen(screen, closed)

    @work(thread=True, group="update-receipt", exit_on_error=False)
    def _check_update_receipt(self) -> None:
        try:
            receipt = updater.read_update_result()
            if receipt is not None:
                self.call_from_thread(self._show_update_receipt, receipt)
        except (updater.UpdateError, OSError, ValueError, RuntimeError):
            # Receipt lookup must never prevent opening the user's vault.
            return

    def _show_update_receipt(self, receipt) -> None:
        if not self.is_running:
            return
        status = receipt.get("status", "")
        message = receipt.get("message") or ("Taskman updated." if status == "success" else "Taskman update needs attention.")
        self.notify(message, title="Taskman update",
                    severity="information" if status in {"success", "PASS"} else "warning",
                    timeout=12, markup=False)
        self._consume_update_receipt()

    @work(thread=True, group="update-receipt-consume", exit_on_error=False)
    def _consume_update_receipt(self) -> None:
        try:
            updater.read_update_result(consume=True)
        except (updater.UpdateError, OSError, ValueError):
            pass
