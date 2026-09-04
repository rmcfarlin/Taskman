"""Export real Textual screens for visual review without editing vault tasks.

Run from the vault root: .venv/Scripts/python -m tui.visual_check
SVG files go to artifacts/visual. All sample data is isolated in a temp folder.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import os
import tempfile
from pathlib import Path

from tui.app import TaskApp
from textual.widgets import Input


async def capture(root: Path, output: Path, name: str, size: tuple[int, int],
                  keys: tuple[str, ...] = (), theme: str = "taskman-dark-teal") -> None:
    app = TaskApp(root, theme=theme)
    async with app.run_test(size=size, notifications=True) as pilot:
        await pilot.pause(0.3)
        if keys:
            await pilot.press(*keys)
            await pilot.pause()
        (output / f"{name}.svg").write_text(app.export_screenshot(title="Taskman"), encoding="utf-8")


async def main() -> None:
    os.environ.pop("NO_COLOR", None)
    os.environ.pop("TASKMAN_THEME", None)
    vault = Path(__file__).resolve().parent.parent
    output = vault / "artifacts" / "visual"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="visual-", dir=output.parent) as tmp:
        root = Path(tmp)
        os.environ["TASKMAN_CONFIG_DIR"] = str(root / "profile")
        (root / "Tasks").mkdir()
        (root / "Projects").mkdir()
        today = dt.date.today()
        date = lambda days: (today + dt.timedelta(days=days)).isoformat()
        (root / "Tasks" / "Inbox.md").write_text(
            "# Inbox\n\n"
            f"- [ ] Confirm the September close checklist 🔺 📅 {date(-2)} #finance\n"
            "  Review the account reconciliations and agree on owners before Friday.\n"
            "  - [x] Collect division checklists\n"
            "  - [ ] Review open reconciliation items\n"
            f"- [/] Prepare the leadership briefing ⏫ 📅 {date(0)} #planning\n"
            f"- [ ] Book the project review 📅 {date(1)}\n"
            "- [ ] Capture ideas for the next quarter #ideas\n",
            encoding="utf-8",
        )
        for name, content in {
            "Automation": f"- [ ] Verify the daily refresh ⏫ 📅 {date(0)} #systems\n"
                          f"- [ ] Simplify the exception report 📅 {date(4)} #reporting\n",
            "Operations": f"- [ ] Review the capacity plan 📅 {date(10)}\n"
                          "- [ ] Document the approval workflow\n",
        }.items():
            (root / "Projects" / f"{name}.md").write_text(f"# {name}\n\n## Tasks\n{content}", encoding="utf-8")
        scenarios = [
            ("overview-wide", (140, 38), ()),
            ("inspector-wide", (140, 38), ("right",)),
            ("commands", (120, 32), ("ctrl+k",)),
            ("commands-filtered", (100, 28), ("ctrl+k", "d", "u", "e")),
            ("overview-compact", (80, 24), ()),
            ("inspector-narrow", (60, 20), ("right",)),
            ("help-compact", (80, 24), ("h",)),
            ("due-compact", (60, 20), ("d",)),
            ("add-compact", (60, 20), ("a",)),
            ("note-compact", (60, 20), ("n",)),
            ("open-vault", (120, 32), ("ctrl+o",)),
        ]
        for name, size, keys in scenarios:
            await capture(root, output, name, size, keys)
        await capture(root, output, "overview-light", (100, 28), theme="catppuccin-latte")
        await capture(root, output, "overview-contrast", (80, 24), theme="high-contrast")
        await capture(root, output, "notification-compact", (80, 24), ("space",))
        for name, size in (("welcome", (120, 32)), ("welcome-small", (60, 20))):
            os.environ["TASKMAN_CONFIG_DIR"] = str(root / name)
            app = TaskApp(theme="taskman-dark-teal", choose_vault=True)
            async with app.run_test(size=size, notifications=True) as pilot:
                await pilot.pause()
                await app.workers.wait_for_complete()
                app.screen.query_one("#vault-path", Input).value = str(root / "My new vault")
                await pilot.press("enter")
                await app.workers.wait_for_complete()
                await pilot.pause()
                (output / f"{name}.svg").write_text(app.export_screenshot(title="Taskman"), encoding="utf-8")
    print(f"VISUAL_EXPORT_OK: {output}")


if __name__ == "__main__":
    asyncio.run(main())
