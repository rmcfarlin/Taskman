"""Read-only folder discovery and explicitly requested, non-overwriting setup."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path, PureWindowsPath
import stat

VAULT_DIRS = ("Tasks", "Projects", "Notes", "Documentation", "Templates", ".taskman")
VAULT_FILES = {
    "Tasks/Inbox.md": "---\ntype: inbox\n---\n\n# Inbox\n\n",
    ".taskman/vault.json": json.dumps({"format_version": 1, "scan": "recursive"}, indent=2) + "\n",
}

# Windows device names that cannot be file or folder segments (incl. CONIN$/superscripts).
_RESERVED_WINDOWS = frozenset({
    "CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
    *(f"{prefix}{digit}" for prefix in ("COM", "LPT") for digit in "¹²³"),
})


def is_reserved_windows_name(part: str) -> bool:
    """True when a path segment's stem is a reserved Windows device name."""
    return part.partition(".")[0].upper() in _RESERVED_WINDOWS


def is_linked(path: Path) -> bool:
    """Detect links without resolving them, including Windows junctions."""
    return path.is_symlink() or getattr(path, "is_junction", lambda: False)()


def _reject_linked_components(path: Path) -> None:
    for component in (path, *path.parents):
        if is_linked(component):
            raise ValueError(f"Choose the actual folder instead of a linked path: {component}")


def _folder_candidate(value: str | Path) -> Path:
    raw = os.fspath(value).strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        raw = raw[1:-1]
    if not raw:
        raise ValueError("Enter a folder path.")
    path = Path(os.path.expandvars(raw)).expanduser().absolute()
    _reject_linked_components(path)
    return path.resolve()


def normalize_folder(value: str | Path) -> Path:
    """Validate an existing readable folder without following linked targets."""
    path = _folder_candidate(value)
    if not path.exists():
        raise ValueError(f"Folder does not exist: {path}")
    if not path.is_dir():
        raise ValueError(f"Choose a folder, not a file: {path}")
    path = path.resolve(strict=True)
    with os.scandir(path):
        pass  # Actually check read permission; os.access alone is not sufficient.
    return path


def vault_path(root: str | Path, relative: str | Path) -> Path:
    """Validate one vault-relative target, rejecting escapes and linked parents."""
    return _target_path(normalize_folder(root), relative)


def _target_path(root: Path, relative: str | Path) -> Path:
    raw = os.fspath(relative)
    windows = PureWindowsPath(raw)
    rel = Path(raw.replace("\\", "/"))
    if (not raw or windows.drive or windows.root or rel.is_absolute()
            or ".." in rel.parts or ":" in raw or not rel.parts
            or any(part.endswith((" ", ".")) for part in rel.parts)):
        raise ValueError(f"Use a path inside the vault: {raw!r}")
    target = root / rel
    _reject_linked_components(target)
    if not target.resolve().is_relative_to(root):
        raise ValueError(f"Path leaves the vault: {raw!r}")
    return target


@dataclass(frozen=True, slots=True)
class VaultPlan:
    root: Path
    missing_dirs: tuple[str, ...]
    missing_files: tuple[str, ...]


def plan_vault(path: str | Path) -> VaultPlan:
    """Inspect the complete setup before any write is attempted."""
    root = _folder_candidate(path)
    if root.exists():
        normalize_folder(root)
    else:
        # Validate the nearest existing ancestor before offering creation.
        parent = next(parent for parent in root.parents if parent.exists())
        normalize_folder(parent)
    missing_dirs: list[str] = []
    missing_files: list[str] = []
    for relative in VAULT_DIRS:
        target = _target_path(root, relative)
        if target.exists():
            if not target.is_dir():
                raise ValueError(f"Setup needs a folder at {relative}; an existing file is in the way.")
        else:
            missing_dirs.append(relative)
    for relative in VAULT_FILES:
        target = _target_path(root, relative)
        if target.exists():
            if not stat.S_ISREG(target.stat().st_mode):
                raise ValueError(f"Setup needs a file at {relative}; an existing folder is in the way.")
        else:
            missing_files.append(relative)
    return VaultPlan(root, tuple(missing_dirs), tuple(missing_files))


def initialize_vault(path: str | Path) -> Path:
    """Create only missing structure; never overwrite existing user content."""
    plan = plan_vault(path)
    _reject_linked_components(plan.root)
    plan.root.mkdir(parents=True, exist_ok=True)
    normalize_folder(plan.root)
    for relative in plan.missing_dirs:
        target = vault_path(plan.root, relative)
        target.mkdir(exist_ok=True)
        # Recheck in case another process inserted a link during setup.
        vault_path(plan.root, relative)
    for relative in plan.missing_files:
        target = vault_path(plan.root, relative)
        try:
            # Exclusive creation is the non-overwrite guarantee even if a file
            # appears after the plan was created.
            with target.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(VAULT_FILES[relative])
        except FileExistsError:
            vault_path(plan.root, relative)
            if not target.is_file():
                raise ValueError(f"Setup needs a file at {relative}.") from None
    return plan.root


def has_vault_marker(root: Path) -> bool:
    try:
        marker = vault_path(root, ".taskman/vault.json")
        return marker.is_file()
    except (OSError, ValueError):
        return False


def is_legacy_vault(root: Path) -> bool:
    """Recognize the original layout, without treating source packages as vaults."""
    return (root / "Tasks").is_dir() and not is_linked(root / "Tasks")


def discover_vault(explicit: str | Path | None = None) -> Path | None:
    """Explicit folder, environment, recent folder, then recognized current folder."""
    if explicit is not None:
        return normalize_folder(explicit)
    env = os.environ.get("TASKMAN_VAULT")
    if env:
        return normalize_folder(env)
    from .settings import last_vault

    recent = last_vault()
    if recent is not None:
        return recent
    current = normalize_folder(Path.cwd())
    return current if has_vault_marker(current) or is_legacy_vault(current) else None
