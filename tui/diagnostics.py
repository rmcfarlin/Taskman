"""Local runtime diagnostics. No telemetry, task snapshots, or captured locals."""

from __future__ import annotations

from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import platform
import re
import sys
import traceback
from uuid import uuid4
from tui.settings import config_dir


def record_error(exc: BaseException, *, log_dir: Path | None = None,
                 context: dict | None = None) -> Path | None:
    """Save one failure and return its path; diagnostics must never crash the UI.

    Logs contain ordinary tracebacks and dependency versions. Context accepts
    only a screen class identifier; task data, input text and local variables
    are not collected. Logs live in the user's settings directory.
    """
    try:
        destination = Path(log_dir) if log_dir is not None else config_dir() / "logs"
        destination.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc)
        path = destination / f"taskman-{stamp:%Y%m%dT%H%M%S}-{uuid4().hex[:8]}.log"
        try:
            textual_version = version("textual")
        except PackageNotFoundError:
            textual_version = "not installed"
        details = [
            f"Taskman runtime failure at {stamp.isoformat()}",
            f"Python: {platform.python_version()}",
            f"Executable: {sys.executable}",
            f"Platform: {platform.system()} {platform.release()}",
            f"Textual: {textual_version}",
        ]
        for key in ("screen", "screen_class"):
            value = (context or {}).get(key)
            if isinstance(value, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]{0,100}", value):
                details.append(f"{key}: {value}")
        details.append("")
        details.extend(traceback.TracebackException.from_exception(exc, capture_locals=False).format())
        with path.open("x", encoding="utf-8") as output:
            output.write("\n".join(details))
        return path
    except Exception:
        return None
