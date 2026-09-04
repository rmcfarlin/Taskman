"""Bounded, file-scoped undo/redo for one Taskman session.

Only declared files are read. Undo/redo checks *all* affected files before
writing, so a detected external edit never results in a partial restoration.
Writes are atomic per file; this is not a cross-file filesystem transaction
and cannot lock out another program writing between the check and replacement.
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
        entry = source[-1]
        for change in entry.changes:
            expected = change.after if undo else change.before
            try:
                current = self._read(change.path)
            except (ValueError, OSError) as error:
                raise HistoryConflict(f"Cannot safely restore {change.path}: {error}") from error
            if current != expected:
                raise HistoryConflict(
                    f"{change.path} changed outside this action; no files were restored."
                )

        # Stage every replacement before touching the recorded files. This also
        # makes a write/permission failure during staging leave originals intact.
        staged: dict[str, Path] = {}
        try:
            for change in entry.changes:
                content = change.before if undo else change.after
                if content is None:
                    continue
                target = self._path(change.path)
                target.parent.mkdir(parents=True, exist_ok=True)
                fd, name = tempfile.mkstemp(prefix=".taskman-undo-", dir=target.parent)
                temporary = Path(name)
                staged[change.path] = temporary
                with os.fdopen(fd, "wb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                if target.exists():
                    temporary.chmod(stat.S_IMODE(target.stat().st_mode))

            for change in entry.changes:
                target = self._path(change.path)
                content = change.before if undo else change.after
                if content is None:
                    target.unlink()
                else:
                    os.replace(staged[change.path], target)
            source.pop()
            destination.append(entry)
            return entry
        finally:
            for temporary in staged.values():
                temporary.unlink(missing_ok=True)
