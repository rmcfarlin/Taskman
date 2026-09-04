"""PyInstaller entry point; keeps relative package imports intact."""
import sys

# Frozen Python ignores PYTHONUTF8. Keep redirected command output readable for
# every vault path, including characters outside the Windows ANSI code page.
for stream in (sys.stdout, sys.stderr):
    if stream is not None and hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="backslashreplace")

from tui.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
