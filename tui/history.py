"""Bounded, file-scoped undo/redo for one Taskman session.

Only declared files are read. Undo/redo checks all affected files before writing
and rolls back its own unchanged writes if a later operation fails. Writes are
atomic per file and coordinate with other Taskman writers. External editors do
not participate in that lock; their detected edits are preserved during rollback.
"""

from __future__ import annotations

import os
import stat
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any, Iterable, Iterator


class HistoryConflict(RuntimeError):
    """A file no longer matches the version this session would replace."""


@dataclass(frozen=True, slots=True)
class FileChange:
    path: str
    before: bytes | None
    after: bytes | None


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    label: str
    context: Any
    changes: tuple[FileChange, ...]


class History:
    """Record successful mutations of explicit vault-relative file paths.

    ``context`` is passed through unchanged for restoring the caller's view or
    selection. Use a fresh context value per record. Mutation exceptions are
    propagated and not recorded. Symlinks and junctions are rejected, including
    links pointing inside the vault, to keep restoration paths unambiguous.
    """

    def __init__(self, vault: Path | str, limit: int = 50) -> None:
        if limit < 1:
            raise ValueError("History limit must be at least one")
        self.vault = Path(vault).resolve(strict=True)
        if not self.vault.is_dir():
            raise ValueError("History vault must be a directory")
        self.limit = limit
        self._undo: list[HistoryEntry] = []
        self._redo: list[HistoryEntry] = []
        self._recording = False

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    @property
    def undo_label(self) -> str | None:
        return self._undo[-1].label if self._undo else None

    @property
    def redo_label(self) -> str | None:
        return self._redo[-1].label if self._redo else None

    def _relative(self, value: Path | str) -> str:
        # Reject Windows drive-relative/UNC paths even when tests run on Unix.
        raw = os.fspath(value)
        windows = PureWindowsPath(raw)
        path = Path(raw.replace("\\", "/"))
        if (not raw or windows.drive or windows.root or path.is_absolute()
                or ":" in raw or ".." in path.parts or not path.parts
                or any(part.endswith((" ", ".")) for part in path.parts)):
            raise ValueError(f"History requires a relative file path: {raw!r}")
        return path.as_posix()

    def _path(self, relative: str) -> Path:
        path = self.vault / relative
        if not path.resolve().is_relative_to(self.vault):
            raise ValueError(f"History path escapes the vault: {relative}")
        cursor = self.vault
        for part in ("", *Path(relative).parts):
            cursor /= part
            if cursor.is_symlink() or getattr(cursor, "is_junction", lambda: False)():
                raise ValueError(f"History cannot restore a linked path: {relative}")
        return path

    def _read(self, relative: str) -> bytes | None:
        path = self._path(relative)
        try:
            mode = path.stat().st_mode
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(mode):
            raise ValueError(f"History requires a regular file: {relative}")
        return path.read_bytes()

    @contextmanager
    def record(
        self, label: str, paths: Iterable[Path | str], *, context: Any = None
    ) -> Iterator[None]:
        from .taskman import vault_write_lock
        with vault_write_lock(self.vault):
            with self._record_locked(label, paths, context=context):
                yield

    @contextmanager
    def _record_locked(
        self, label: str, paths: Iterable[Path | str], *, context: Any = None
    ) -> Iterator[None]:
        if self._recording:
            raise RuntimeError("History records cannot be nested")
        if isinstance(paths, (str, Path)):
            raise TypeError("History paths must be a collection of relative paths")
        # Windows paths are case-insensitive: snapshot a file only once even if
        # callers use different spelling while assembling a multi-file action.
        relatives = tuple({os.path.normcase(relative): relative
                           for relative in map(self._relative, paths)}.values())
        before = {path: self._read(path) for path in relatives}
        self._recording = True
        try:
            yield
            after = {path: self._read(path) for path in relatives}
            changes = tuple(FileChange(path, before[path], after[path])
                            for path in relatives if before[path] != after[path])
            if changes:
                self._undo.append(HistoryEntry(label, context, changes))
                del self._undo[:-self.limit]
                self._redo.clear()
        finally:
            self._recording = False

    def undo(self) -> HistoryEntry | None:
        return self._restore(self._undo, self._redo, undo=True)

    def redo(self) -> HistoryEntry | None:
        return self._restore(self._redo, self._undo, undo=False)

    def _restore(
        self, source: list[HistoryEntry], destination: list[HistoryEntry], *, undo: bool
    ) -> HistoryEntry | None:
        if self._recording:
            raise RuntimeError("Cannot undo or redo during a history record")
        if not source:
            return None
        from .taskman import vault_write_lock
        with vault_write_lock(self.vault):
            return self._restore_locked(source, destination, undo=undo)

    def _restore_locked(
        self, source: list[HistoryEntry], destination: list[HistoryEntry], *, undo: bool
    ) -> HistoryEntry | None:
        if self._recording:
            raise RuntimeError("Cannot undo or redo during a history record")
        if not source:
            return None
        entry = source[-1]
        expected = {change.path: change.after if undo else change.before for change in entry.changes}
        desired = {change.path: change.before if undo else change.after for change in entry.changes}

        def check(relative: str, content: bytes | None) -> None:
            try:
                current = self._read(relative)
            except (ValueError, OSError) as error:
                raise HistoryConflict(f"Cannot safely restore {relative}: {error}") from error
            if current != content:
                raise HistoryConflict(f"{relative} changed outside this action; restoration was cancelled.")

        for relative, content in expected.items():
            check(relative, content)

        # Prepare both directions before touching originals. Recovery should not
        # need new disk allocation after a later write/unlink fails.
        staged: dict[str, Path] = {}
        recovery: dict[str, Path] = {}
        probes: set[Path] = set()
        retained: set[Path] = set()
        applied: list[tuple[str, os.stat_result | None]] = []

        def stage(relative: str, content: bytes, collection: dict[str, Path], *, recovery_copy=False) -> None:
            target = self._path(relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            target = self._path(relative)
            fd, name = tempfile.mkstemp(prefix=".taskman-undo-", dir=target.parent)
            temporary = Path(name)
            collection[relative] = temporary
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            if target.exists():
                original_stat = target.stat()
                temporary.chmod(stat.S_IMODE(original_stat.st_mode))
                if recovery_copy:
                    os.utime(temporary, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))

        def check_ours(relative: str, owned: os.stat_result | None) -> None:
            check(relative, desired[relative])
            if owned is not None:
                current = self._path(relative).stat()
                if (not os.path.samestat(owned, current)
                        or current.st_mtime_ns != owned.st_mtime_ns):
                    raise HistoryConflict(f"{relative} was replaced outside this action.")

        def check_creation_support(temporary: Path) -> None:
            # A rename may unlink its old name before creating its new name.
            # Verify no-clobber publication and recovery are supported by this
            # filesystem before permitting any destructive apply step.
            probe = temporary.with_name(temporary.name + "-probe")
            os.link(temporary, probe)
            probes.add(probe)
            probe.unlink()

        try:
            for relative, content in desired.items():
                if content is not None:
                    stage(relative, content, staged)
                    if expected[relative] is None:
                        check_creation_support(staged[relative])
                if expected[relative] is not None:
                    stage(relative, expected[relative], recovery, recovery_copy=True)
                    if content is None:
                        check_creation_support(recovery[relative])
            for relative, content in expected.items():
                check(relative, content)
            try:
                for relative, content in desired.items():
                    check(relative, expected[relative])
                    target = self._path(relative)
                    owned = staged[relative].stat() if content is not None else None
                    if content is None:
                        target.unlink()
                    elif expected[relative] is None:
                        # No-clobber publication protects a file that appears
                        # between the absence check and creation.
                        os.link(staged[relative], target)
                    else:
                        os.replace(staged[relative], target)
                    applied.append((relative, owned))
                for relative, owned in applied:
                    check_ours(relative, owned)
            except BaseException as error:
                unresolved = []
                for relative, owned in reversed(applied):
                    try:
                        check_ours(relative, owned)
                        target = self._path(relative)
                        if expected[relative] is None:
                            target.unlink()
                        elif desired[relative] is None:
                            os.link(recovery[relative], target)
                        else:
                            os.replace(recovery[relative], target)
                    except (ValueError, OSError, HistoryConflict):
                        # Never replace a later external edit, including a
                        # different file that happens to have identical bytes.
                        unresolved.append(relative)
                        if relative in recovery:
                            retained.add(recovery[relative])
                if unresolved:
                    locations = ", ".join(str(path.relative_to(self.vault)) for path in sorted(retained))
                    detail = f" Recovery copies: {locations}." if locations else ""
                    raise HistoryConflict(
                        "Restoration stopped; external changes or an I/O error prevented rollback of "
                        + ", ".join(unresolved) + ". Undo/redo history was not advanced." + detail
                    ) from error
                raise
            source.pop()
            destination.append(entry)
            return entry
        finally:
            for temporary in (*staged.values(), *recovery.values(), *probes):
                if temporary not in retained:
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError:
                        # Cleanup failure must not turn a completed restoration
                        # into a reported failure after its history was advanced.
                        pass
