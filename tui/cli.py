"""Dependency-free command interface to the same Markdown task operations as the UI."""
from __future__ import annotations

import argparse
import json
import sys

if __package__:
    from . import taskman as tm
    from .recurrence import normalize_rule
else:
    import taskman as tm
    from recurrence import normalize_rule


class CommandError(ValueError):
    """A usage error that can also be rendered as a JSON response."""


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CommandError(message)


def add_arguments(parser: argparse.ArgumentParser, *, include_vault: bool = True) -> None:
    if include_vault:
        parser.add_argument("--vault", metavar="PATH", help="vault folder")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--plain", metavar="VIEW", help="list all, now (or today), overdue, next7, inbox, priority, completed, search, project, or projects")
    actions.add_argument("--check", action="store_true", help="show vault health and task counts")
    actions.add_argument("--add", metavar="TEXT", help="add a task and print its stable ID")
    actions.add_argument("--sub", metavar="TEXT", help="add a subtask under --under ID or FILE:LINE")
    actions.add_argument("--note", metavar="TEXT", help="replace the note under --under ID or FILE:LINE; empty text clears")
    actions.add_argument("--complete", metavar="ID", help="complete a task and create its next occurrence; repeating this command is safe")
    actions.add_argument("--ensure-id", metavar="ID_OR_LOCATION", help="give a legacy task a stable ID, or return its existing ID")
    actions.add_argument("--id", metavar="ID", help="read a task, or update its --due / --scheduled / --repeat fields")
    parser.add_argument("--under", metavar="ID_OR_LOCATION", help="parent task for --sub or --note")
    parser.add_argument("--project", metavar="NAME", help="project filter for a listing or destination for --add")
    parser.add_argument("--search", metavar="TEXT", help="search text for --plain search")
    parser.add_argument("--due", metavar="DATE", help="due date for --add, --sub, or --id; today/tomorrow/mon/+7/ISO/clear")
    parser.add_argument("--scheduled", metavar="DATE", help="planned work date for --add, --sub, or --id; same date language as --due")
    parser.add_argument("--repeat", metavar="RULE", help="repeat rule for --add, --sub, or --id; daily/weekly/monthly/yearly/every N days/clear")
    parser.add_argument("--json", action="store_true", help="return structured results (default listing: all open); reads do not add IDs")


def requested(args: argparse.Namespace) -> bool:
    """Every CLI-only modifier routes to validation, never an accidental UI launch."""
    return args.check or args.json or any(getattr(args, key) is not None for key in (
        "plain", "add", "sub", "note", "complete", "ensure_id", "id", "under",
        "project", "search", "due", "scheduled", "repeat",
    ))


def report_error(error: Exception, *, json_output: bool = False) -> int:
    if json_output:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
    else:
        print(f"Taskman: {error}", file=sys.stderr)
    return 2


def task_record(task: tm.Task) -> dict:
    warning = tm.recurrence_warning(task)
    recurrence_status = ("next-created" if task.recurrence_next else "blocked" if warning
                         else "active" if task.recurrence else "none")
    return {
        "id": task.anchor or None,
        "location": task.id,
        "file": task.file,
        "line": task.lineno,
        "status": task.status,
        "status_label": task.status_label,
        "description": task.description,
        "project": task.project,
        "due": task.due.isoformat() if task.due else None,
        "scheduled": task.scheduled.isoformat() if task.scheduled else None,
        "recurrence": task.recurrence or None,
        "recurrence_status": recurrence_status,
        "recurrence_warning": warning or None,
        "recurrence_next": task.recurrence_next or None,
        "start": task.start.isoformat() if task.start else None,
        "done_date": task.done_date.isoformat() if task.done_date else None,
        "cancelled_date": task.cancelled_date.isoformat() if task.cancelled_date else None,
        "priority": task.priority,
        "tags": list(task.tags),
        "note": task.note,
        "depth": task.depth,
        "context": task.context,
    }


def _result(args: argparse.Namespace, action: str, task: tm.Task, *, changed: bool,
            warning: str = "", next_task: tm.Task | None = None) -> int:
    if args.json:
        result = {"ok": True, "action": action, "changed": changed, "task": task_record(task)}
        if action == "complete":
            result.update(warning=warning or None,
                          next_task=task_record(next_task) if next_task else None)
        print(json.dumps(result, ensure_ascii=False))
    elif action in ("add", "sub", "ensure-id"):
        print(task.anchor)
    elif action == "get":
        print(task.short(tm.today()))
        for line in task.note.splitlines():
            print(f"      {line}")
    else:
        print(f"{action}: {task.anchor or task.id}" + (" (unchanged)" if not changed else ""))
        if next_task and changed:
            print(f"next: {next_task.anchor or next_task.id}")
    if warning and not args.json:
        print(f"Taskman: {warning}", file=sys.stderr)
    return 0


def _validate(args: argparse.Namespace) -> None:
    creation = args.add is not None or args.sub is not None
    dates = args.due is not None or args.scheduled is not None
    listing = args.plain is not None or (args.json and not any((
        args.check, args.add is not None, args.sub is not None, args.note is not None,
        args.complete is not None, args.ensure_id is not None, args.id is not None,
    )))
    if args.under is not None and args.sub is None and args.note is None:
        raise CommandError("--under requires --sub or --note")
    if (args.sub is not None or args.note is not None) and not args.under:
        raise CommandError("--sub / --note requires --under ID or FILE:LINE")
    if dates and not (creation or args.id is not None):
        raise CommandError("--due / --scheduled requires --add, --sub, or --id")
    if args.repeat is not None and not (creation or args.id is not None):
        raise CommandError("--repeat requires --add, --sub, or --id")
    if args.project is not None and not (args.add is not None or listing):
        raise CommandError("--project requires --add or a task listing")
    if args.search is not None and args.plain != "search":
        raise CommandError("--search requires --plain search")
    if args.plain is not None and args.plain not in {
        "all", "now", "today", "overdue", "next7", "inbox", "priority", "completed", "search", "project", "projects",
    }:
        raise CommandError(f"Unknown view: {args.plain}")
    if args.plain == "projects" and args.project is not None:
        raise CommandError("--project filters tasks, not the projects listing")
    if args.plain == "project" and not (args.project or "").strip():
        raise CommandError("--plain project requires --project NAME")
    if creation and not (args.add if args.add is not None else args.sub).strip():
        raise CommandError("Task text cannot be empty")
    for name in ("complete", "ensure_id", "id"):
        if getattr(args, name) is not None and not getattr(args, name).strip():
            raise CommandError(f"--{name.replace('_', '-')} requires a task ID")
    if not listing and not any((args.check, creation, args.note is not None,
                               args.complete is not None, args.ensure_id is not None, args.id is not None)):
        raise CommandError("Choose a command, such as --plain now or --add TEXT")


def _listing_rows(tasks: list[tm.Task], args: argparse.Namespace, view: str) -> list[tm.Task]:
    rows = tm.view_tasks(tasks, view, project=args.project or "", query=args.search or "",
                         with_parents=False)
    if args.project:
        matching = {task.id for task in tm.view_tasks(tasks, "project", project=args.project,
                                                     with_parents=False)}
        rows = [task for task in rows if task.id in matching]
    # A matching subtask retains its ancestors as explicitly marked context,
    # including an ancestor whose own project does not match the filter.
    by_location = {task.id: task for task in tasks}
    seen = {task.id for task in rows}
    for task in list(rows):
        while task.parent_lineno:
            parent = by_location.get(f"{task.file}:{task.parent_lineno}")
            if parent is None or parent.id in seen:
                break
            parent.context = True
            seen.add(parent.id)
            rows.append(parent)
            task = parent
    order = {task.id: i for i, task in enumerate(tasks)}
    return sorted(rows, key=lambda task: order[task.id])


def _execute(args: argparse.Namespace) -> int:
    _validate(args)
    # Validate dates and rule grammar before any file or folder is created.
    date_values = {key: tm.parse_date(getattr(args, key)) for key in ("due", "scheduled")
                   if getattr(args, key) is not None}
    repeat_values = {"recurrence": normalize_rule(args.repeat)} if args.repeat is not None else {}
    root = tm.vault_root(args.vault)
    if args.add is not None:
        task = tm.add_task(root, args.add, project=args.project or "", **date_values, **repeat_values)
        return _result(args, "add", task, changed=True)
    if args.sub is not None or args.note is not None:
        with tm.vault_write_lock(root):
            target = tm.resolve_task(root, args.under)
            if args.sub is not None:
                task = tm.add_subtask(root, target, args.sub, **date_values, **repeat_values)
                return _result(args, "sub", task, changed=True)
            previous_note = target.note
            task = tm.set_note(root, target, args.note)
            changed = previous_note != task.note
        return _result(args, "note", task, changed=changed)
    if args.complete is not None:
        with tm.vault_write_lock(root):
            task = tm.resolve_task(root, args.complete)
            changed = not task.done
            warning = tm.recurrence_warning(task) if changed and not task.recurrence_next else ""
            task = tm.complete(root, task)
            matches = ([item for item in tm.load_all(root) if item.anchor == task.recurrence_next]
                       if task.recurrence_next else [])
            next_task = matches[0] if len(matches) == 1 else None
        return _result(args, "complete", task, changed=changed, warning=warning, next_task=next_task)
    if args.ensure_id is not None:
        with tm.vault_write_lock(root):
            task = tm.resolve_task(root, args.ensure_id)
            changed = not bool(task.anchor)
            task = tm.ensure_task_anchor(root, task)
        return _result(args, "ensure-id", task, changed=changed)
    if args.id is not None:
        if date_values or repeat_values:
            with tm.vault_write_lock(root):
                task = tm.resolve_task(root, args.id)
                due = date_values.get("due", task.due)
                scheduled = date_values.get("scheduled", task.scheduled)
                recurrence = repeat_values.get("recurrence", task.recurrence)
                changed = (due, scheduled, recurrence) != (task.due, task.scheduled, task.recurrence)
                task = tm.set_dates(root, task, due, scheduled, **repeat_values)
            return _result(args, "update", task, changed=changed)
        task = tm.resolve_task(root, args.id)
        return _result(args, "get", task, changed=False)
    tasks = tm.load_all(root)
    if args.check:
        counts = tm.counts(tasks)
        files = len(tm.iter_markdown_files(root))
        if args.json:
            print(json.dumps({"ok": True, "vault": str(root), "files": files,
                              "task_count": len(tasks), "counts": counts}, ensure_ascii=False))
        else:
            print(f"vault: {root}\nfiles: {files}  tasks: {len(tasks)}")
            for name, _label in tm.VIEWS:
                print(f"{name}: {counts[name]}")
        return 0
    view = "now" if args.plain == "today" else args.plain or "all"
    if view == "projects":
        counts = tm.project_counts(tasks)
        projects = [{"name": name, "open": counts.get(name.casefold(), 0)}
                    for name in tm.project_names(tasks, root)]
        if args.json:
            print(json.dumps({"ok": True, "view": view, "projects": projects}, ensure_ascii=False))
        else:
            for project in projects:
                print(f"{project['name']}  ({project['open']} open)")
    else:
        sections = tm.sections(_listing_rows(tasks, args, view), view)
        if args.json:
            ordered = [node.task for section in sections for node in section.nodes]
            print(json.dumps({"ok": True, "view": view, "tasks": [task_record(task) for task in ordered]},
                             ensure_ascii=False))
        else:
            for section in sections:
                print(f"== {section.title} ({section.count}) ==")
                for node in section.nodes:
                    print(node.task.short(tm.today()))
                    for line in node.task.note.splitlines():
                        print("  " * node.task.depth + "      " + line)
    return 0


def run(args: argparse.Namespace) -> int:
    try:
        return _execute(args)
    except (OSError, ValueError) as error:
        return report_error(error, json_output=args.json)


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    tm._utf8_stdout()
    parser = ArgumentParser(prog="taskman", allow_abbrev=False,
                            description="Markdown-native tasks with stable IDs and structured output.")
    add_arguments(parser)
    try:
        return run(parser.parse_args(raw))
    except CommandError as error:
        return report_error(error, json_output="--json" in raw)
