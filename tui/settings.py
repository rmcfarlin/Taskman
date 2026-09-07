"""Small, portable user preferences, independent of the installed application."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any

MAX_RECENT_VAULTS = 10


def config_dir() -> Path:
    """Return the user settings directory without creating it."""
    override = os.environ.get("TASKMAN_CONFIG_DIR")
    if override:
        return Path(os.path.expandvars(override)).expanduser().absolute()
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        return base / "Taskman"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Taskman"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "taskman"


def _read() -> dict[str, Any]:
    try:
        value = json.loads((config_dir() / "settings.json").read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, UnicodeError):
        return {}


def _write(values: dict[str, Any]) -> None:
    """Replace preferences atomically; a failed save preserves the old file."""
    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".settings-", suffix=".tmp", dir=directory)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(values, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, directory / "settings.json")
    finally:
        temporary.unlink(missing_ok=True)


def read_theme() -> str:
    theme = _read().get("theme")
    return theme if isinstance(theme, str) else ""


def read_note_sort() -> str:
    value = _read().get("note_sort", "modified")
    return value if value in ("modified", "title") else "modified"


def write_note_sort(value: str) -> None:
    if value not in ("modified", "title"):
        raise ValueError("Choose recently modified or title order.")
    values = _read()
    values["note_sort"] = value
    _write(values)


def write_theme(name: str) -> None:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Choose a theme name.")
    values = _read()
    values["theme"] = name.strip()
    _write(values)


def recent_vaults() -> list[Path]:
    """Available, unique folders, most recently opened first."""
    from .vaults import normalize_folder

    values = _read().get("recent_vaults", [])
    if not isinstance(values, list):
        return []
    result: list[Path] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        try:
            path = normalize_folder(value)
        except (OSError, ValueError):
            continue
        key = os.path.normcase(str(path))
        if key not in seen:
            seen.add(key)
            result.append(path)
        if len(result) == MAX_RECENT_VAULTS:
            break
    return result


def last_vault() -> Path | None:
    recent = recent_vaults()
    return recent[0] if recent else None


def remember_vault(path: str | Path) -> None:
    from .vaults import normalize_folder

    chosen = normalize_folder(path)
    others = [p for p in recent_vaults()
              if os.path.normcase(str(p)) != os.path.normcase(str(chosen))]
    values = _read()
    values["recent_vaults"] = [str(p) for p in [chosen, *others][:MAX_RECENT_VAULTS]]
    _write(values)
