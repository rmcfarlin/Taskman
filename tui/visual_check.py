"""Export real Textual screens for visual review without editing vault tasks.

Run from the vault root: .venv/Scripts/python -m tui.visual_check
SVG files go to artifacts/visual. All sample data is isolated in a temp folder.
Use --notes-only --output artifacts/visual-notes for the reference library.
"""
from __future__ import annotations

import asyncio
import argparse
import datetime as dt
import os
import tempfile
from pathlib import Path

from tui.app import TaskApp
from tui.notes import NotesStore, TaskLink
from tui import taskman as tm
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


def seed_notes(root: Path) -> None:
    """Representative reference material, with a real stable task link."""
    tasks = tm.Store(root).refresh()
    linked = tm.ensure_task_anchor(root, next(
        task for task in tasks if task.description == "Document the approval workflow"))
    notes = NotesStore(root)
    notes.create(
        title="Approval workflow", category="Processes", tags=("purchasing", "reference"),
        projects=("Operations",), tasks=(TaskLink(linked.anchor, linked.description),),
        body="## Before approving\n\n"
             "1. Confirm the request includes the supplier quote.\n"
             "2. Check the owner and the expected delivery date.\n"
             "3. Record the decision where the team can retrieve it.\n\n"
             "## Reference\n\n"
             "**Owner:** Operations\n\n"
             "This procedure remains available after the related task is complete.\n\n"
             "> Capture reusable decisions here; keep the next action in a task.\n",
    )
    notes.create(
        title="Daily refresh runbook", category="Systems", tags=("automation", "runbook"),
        projects=("Automation",),
        body="## When a refresh fails\n\n"
             "Review the exception report and compare its source timestamp.\n\n"
             "Record the failure time, affected report, and recovery steps.\n",
    )
    notes.create(
        title="Month-end reconciliation reference", category="Finance", tags=("close", "reference"),
        body="## Review questions\n\n"
             "- Does the support agree to the ledger?\n"
             "- Are reconciling items assigned to an owner?\n"
             "- Is the explanation clear enough to retrieve next month?\n",
    )
    notes.create(
        title="Supplier meeting — follow-up", category="Meetings", tags=("supplier",),
        projects=("Operations",),
        body="The supplier confirmed a new delivery window.\n\n"
             "Keep the revised quote and the decision together for the next review.\n",
    )


async def main(*, notes_only: bool = False, output: Path | None = None) -> None:
    os.environ.pop("NO_COLOR", None)
    os.environ.pop("TASKMAN_THEME", None)
    vault = Path(__file__).resolve().parent.parent
    output = output or vault / "artifacts" / "visual"
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
        seed_notes(root)
        note_scenarios = [
            ("notes-wide", (140, 38), ("8",)),
            ("notes-linked-preview", (140, 38), ("8", "right")),
            ("notes-compact", (80, 24), ("8",)),
            ("notes-editor", (120, 36), ("8", "enter")),
            ("notes-editor-small", (60, 20), ("8", "enter")),
            ("notes-capture-small", (60, 20), ("8", "a")),
            ("notes-find", (140, 38), ("8", "/", "s", "u", "p", "p", "l", "i", "e", "r", "enter")),
        ]
        for name, size, keys in note_scenarios:
            await capture(root, output, name, size, keys)
        await capture(root, output, "notes-light", (100, 28), ("8",), theme="catppuccin-latte")
        await capture(root, output, "notes-contrast", (100, 28), ("8",), theme="high-contrast")
        scenarios = [
            ("overview-wide", (140, 38), ()),
            ("find-open", (140, 38), ("/",)),
            ("find-results", (140, 38), ("/", "r", "e", "v", "i", "e", "w", "enter")),
            ("find-cleared", (140, 38), ("/", "r", "e", "v", "escape")),
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
        if not notes_only:
            for name, size, keys in scenarios:
                await capture(root, output, name, size, keys)
            await capture(root, output, "overview-light", (100, 28), theme="catppuccin-latte")
            await capture(root, output, "overview-contrast", (80, 24), theme="high-contrast")
            await capture(root, output, "notification-compact", (80, 24), ("space",))
        for name, size in (() if notes_only else (("welcome", (120, 32)), ("welcome-small", (60, 20)))):
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--notes-only", action="store_true", help="Export only Notes scenarios")
    parser.add_argument("--output", type=Path, help="Folder for the exported SVG screens")
    args = parser.parse_args()
    asyncio.run(main(notes_only=args.notes_only, output=args.output))
