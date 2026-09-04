"""Run the complete suite in isolated parallel groups and retain QA evidence.

Usage: .venv/Scripts/python -m tui.check
Each run owns a new artifacts/checks-* directory; no user task files are edited.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    artifacts = root / "artifacts"
    artifacts.mkdir(exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix="checks-", dir=artifacts))
    groups: list[list[Path]] = [[], [], []]
    for path in sorted((root / "tui").glob("test_*.py")):
        index = (1 if path.stem in {"test_keyboard", "test_commands", "test_shortcut_bar", "test_dock_integration"} else
                 2 if path.stem in {"test_history", "test_workflows", "test_runtime_regressions", "test_vault_integration"} else 0)
        groups[index].append(path)
    source = list((root / "tui").glob("*.py")) + [root / "tui" / "requirements.txt"]
    source += list((root / "scripts").glob("*.ps1")) + list((root / "scripts").glob("*.cmd"))
    source += list((root / "scripts").glob("*.py")) + list((root / "scripts").glob("*.sh"))
    source += [root / name for name in ("pyproject.toml", "setup.py", "MANIFEST.in")]
    source += list((root / ".github" / "workflows").glob("*.yml"))
    hashes = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in source}

    def run_group(item: tuple[int, list[Path]]) -> tuple[int, int, int]:
        index, paths = item
        report = output / f"group-{index}.xml"
        command = [sys.executable, "-m", "pytest", *map(str, paths), "-q", "-W", "error",
                   "-p", "no:cacheprovider", "--basetemp", str(output / f"temp-{index}"),
                   "--junitxml", str(report)]
        environment = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        try:
            result = subprocess.run(command, cwd=root, env=environment, capture_output=True,
                                    encoding="utf-8", errors="replace", timeout=600)
        except subprocess.TimeoutExpired:
            print(f"Group {index}: timed out", flush=True)
            return 1, 0, 0
        (output / f"group-{index}.log").write_text(result.stdout + result.stderr, encoding="utf-8")
        tests = skipped = failures = 0
        if report.exists():
            for suite in ET.parse(report).getroot().iter("testsuite"):
                tests += int(suite.get("tests", "0"))
                skipped += int(suite.get("skipped", "0"))
                failures += int(suite.get("failures", "0")) + int(suite.get("errors", "0"))
        passed = tests - skipped - failures
        print(f"Group {index}: {passed} passed, {skipped} skipped, {failures} failed", flush=True)
        if result.returncode:
            print(f"Failure details: {output / f'group-{index}.log'}", flush=True)
        return result.returncode, passed, skipped

    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(run_group, enumerate(groups)))
    unchanged = all(hashlib.sha256((root / name).read_bytes()).hexdigest() == value
                    for name, value in hashes.items())
    passed = sum(row[1] for row in results)
    skipped = sum(row[2] for row in results)
    ok = all(row[0] == 0 for row in results) and unchanged
    (output / "summary.json").write_text(json.dumps(
        {"ok": ok, "passed": passed, "skipped": skipped, "source_unchanged": unchanged,
         "source_sha256": hashes}, indent=2), encoding="utf-8")
    print(f"{'CHECKS_OK' if ok else 'CHECKS_FAILED'}: {passed} passed, {skipped} skipped")
    print(f"Evidence: {output}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
