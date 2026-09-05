"""Notes workflows shared with TaskApp; Markdown remains the source of truth."""
from __future__ import annotations

import datetime as dt
from dataclasses import replace

from rich.text import Text
from textual.widgets import Input, Label, OptionList
from textual.widgets.option_list import Option

from . import taskman as tm
from .commands import Command, CommandScreen
from .history import History, HistoryConflict
from .notes import Note, NoteConflict, TaskLink, filter_notes
from .notes_ui import NoteDraft, NoteEditorScreen, NotesWorkspace


class NotesActions:
    """Application actions; the task list never supplies a target in Notes."""

    def _save_with_task_change(self, paths, change, save):
        """Roll back our task writes if the note cannot be saved.

        Only task files enter the rollback journal. A concurrent external note
        edit must never be mistaken for our own write and restored over.
        """
        rollback = History(self.vault)
        error = None
        result = None
        with rollback.record("Prepare linked task", paths):
            try:
                result = change()
            except (OSError, ValueError, NoteConflict) as exc:
                error = exc
        if error is None:
            try:
                return save(result)
            except (OSError, ValueError, NoteConflict) as exc:
                error = exc
        rollback.undo()
        raise error

    def _sync_notes_mode(self) -> None:
        notes = self.view == "notes"
        self.query_one("#tasks").display = not notes
        self.query_one(NotesWorkspace).display = notes
        self.query_one("#search", Input).placeholder = (
            "Find in notes, categories, projects, or #tags…" if notes
            else "Find a task, project, or #tag…")
        if notes:
            self.query_one("#inspector").display = False
            self.query_one("#main").display = True
        self._layout_chrome()

    def _refresh_notes(self, *, reload: bool = True) -> None:
        tasks = self.store.refresh() if reload else self.store.tasks
        matches = filter_notes(self._notes, self.search_query, self.note_category, self.note_tag)
        if self.note_project:
            matches = [n for n in matches if self.note_project.casefold()
                       in {p.casefold() for p in n.projects}]
        labels = {}
        for note in matches:
            for link in note.tasks:
                try:
                    task = tm.find_task_by_anchor(tasks, link.id)
                    labels[link.id] = (f"{task.description} · {task.status_label}" if task
                                       else f"{link.title} · unavailable")
                except ValueError:
                    labels[link.id] = f"{link.title} · ambiguous link"
        workspace = self.query_one(NotesWorkspace)
        workspace.set_notes(matches, selected_file=self._selected_note_file, task_labels=labels)
        self._selected_note_file = workspace.current.file if workspace.current else ""
        counts = tm.counts(tasks, dt.date.today())
        self.query_one("#sidebar").populate(counts, self.store.projects(),
                                            tm.project_counts(tasks), "notes", "", len(self._notes))
        self._summary = f"Notes · {len(matches)} of {len(self._notes)}"
        if self.size.width < 90:
            active = [self.note_category, f"#{self.note_tag}" if self.note_tag else "", self.note_project]
            self._summary += "".join(f" · {label}" for label in active if label)
        self._update_crumb()
        filters = [self.note_category or "All categories", f"#{self.note_tag}" if self.note_tag else "",
                   self.note_project, f'“{self.search_query}”' if self.search_query else ""]
        self.query_one("#status-view", Label).update("NOTES")
        self.query_one("#status-info", Label).update(Text(" · ".join(f for f in filters if f)))
        skipped = len(self.notes_store.errors)
        self.query_one("#status-right", Label).update(
            f"{skipped} unreadable · Ctrl+K details" if skipped else "Markdown · saved in Notes/")
        self._context_hint()

    def _reference_selected(self, event: NotesWorkspace.Selected) -> None:
        self._selected_note_file = event.note.file if event.note else ""

    def _reference_activated(self, event: NotesWorkspace.Activated) -> None:
        self._edit_reference(event.note)

    def _open_reference(self, file: str = "") -> None:
        self.view, self.project, self.search_query = "notes", "", ""
        self.note_category = self.note_tag = self.note_project = ""
        self._selected_note_file = file
        with self.prevent(Input.Changed):
            self.query_one("#search", Input).value = ""
        self.refresh_tasks()
        self.action_focus_tasks()
        # Switching from a modal can restore its previous focus after this
        # callback. Wait for the newly visible workspace to finish layout.
        self.call_after_refresh(self.action_focus_tasks)

    def action_notes(self) -> None:
        self._open_reference(self._selected_note_file)

    def action_new_reference(self) -> None:
        self._edit_reference()

    def action_new_linked_reference(self) -> None:
        task = self._selected()
        if task:
            self._edit_reference(task=task)

    def action_edit_reference(self) -> None:
        note = self.query_one(NotesWorkspace).current
        if note:
            self._edit_reference(note)

    def _edit_reference(self, note: Note | None = None, *, task=None) -> None:
        saved = None

        def save(draft: NoteDraft) -> str | None:
            nonlocal saved
            try:
                path = note.file if note else self.notes_store.new_path(draft.title)
                paths = [path] + ([task.file] if task else [])
                with self._record("Edit reference note" if note else "Create reference note", paths):
                    def write(anchored=None):
                        links = note.tasks if note else ()
                        if anchored:
                            links += (TaskLink(anchored.anchor, anchored.description),)
                        fields = dict(title=draft.title, body=draft.body, category=draft.category,
                                      tags=draft.tags, projects=draft.projects, tasks=links)
                        return (self.notes_store.save(replace(note, **fields)) if note
                                else self.notes_store.create(**fields, file=path))
                    saved = (self._save_with_task_change([task.file],
                             lambda: tm.ensure_task_anchor(self.vault, task), write) if task else write())
            except (OSError, ValueError, NoteConflict, HistoryConflict) as exc:
                return str(exc)
            return None

        def done(draft: NoteDraft | None) -> None:
            if draft is not None and saved is not None:
                self._open_reference(saved.file)
                self.announce("Note saved")

        self.push_screen(NoteEditorScreen(note, categories=self.notes_store.categories(),
                                          projects=self.store.projects(), save_handler=save), done)

    def _filter_reference(self, field: str, values: list[str]) -> None:
        plural = "categories" if field == "category" else f"{field}s"
        commands = [Command("all", f"All {plural}", description="Clear this filter")]
        commands += [Command(f"value:{i}", value) for i, value in enumerate(values)]

        def chosen(choice: str | None) -> None:
            if choice is None:
                return
            value = "" if choice == "all" else values[int(choice.split(":")[1])]
            setattr(self, f"note_{field}", value)
            self.refresh_tasks()
            self.action_focus_tasks()
        self.push_screen(CommandScreen(commands, context=f"Filter notes by {field}"), chosen)

    def action_note_category(self) -> None:
        if self.view == "notes":
            self._filter_reference("category", self.notes_store.categories())

    def action_note_tag(self) -> None:
        if self.view == "notes":
            self._filter_reference("tag", self.notes_store.tags())

    def action_note_project(self) -> None:
        if self.view == "notes":
            self._filter_reference("project", sorted({p for n in self._notes for p in n.projects}, key=str.casefold))

    def _link_reference(self, note: Note, task) -> None:
        current = self.notes_store.load(note.file)
        if current.revision != note.revision:
            raise NoteConflict("This note changed outside Taskman. Rescan and try linking again.")
        if task.anchor and any(link.id == task.anchor for link in note.tasks):
            self.announce("This note is already linked to the task")
            return
        with self._record("Link reference note", [note.file, task.file]):
            self._save_with_task_change([task.file], lambda: tm.ensure_task_anchor(self.vault, task),
                lambda anchored: self.notes_store.save(replace(note, tasks=note.tasks + (
                    TaskLink(anchored.anchor, anchored.description),))))
        self.refresh_tasks()
        self.announce("Note linked to task")

    def action_link_reference(self) -> None:
        if self.view == "notes":
            note = self.query_one(NotesWorkspace).current
            if note is None:
                self.announce("Create a note first")
                return
            tasks = list(self.store.refresh())
            linked = {link.id for link in note.tasks}
            commands = [Command(str(i), task.description or "(no text)",
                                description=f"{task.status_label} · {task.file}:{task.lineno}",
                                enabled=not (task.anchor and task.anchor in linked))
                        for i, task in enumerate(tasks)]
            if not commands:
                self.announce("No tasks yet. Ctrl+T creates a task from this note.")
                return
            def chosen(choice):
                if choice is not None:
                    self._link_reference(note, tasks[int(choice)])
            self.push_screen(CommandScreen(commands, context="Link an existing task"), self._guard(chosen))
        else:
            task = self._selected()
            if task is None:
                self.announce("Select a task to link a reference note")
                return
            notes = self.notes_store.refresh()
            commands = [Command("new", "Create a linked reference note", keywords="new capture")]
            commands += [Command(str(i), note.title, description=f"{note.category} · {note.file}",
                                 enabled=not (task.anchor and any(l.id == task.anchor for l in note.tasks)))
                         for i, note in enumerate(notes)]
            def chosen(choice):
                if choice == "new":
                    self._edit_reference(task=task)
                elif choice is not None:
                    self._link_reference(notes[int(choice)], task)
            self.push_screen(CommandScreen(commands, context="Link a reference note"), self._guard(chosen))

    def action_linked_references(self) -> None:
        if self.view == "notes":
            note = self.query_one(NotesWorkspace).current
            if note is None or not note.tasks:
                self.announce("No linked tasks. Press l to link one.")
                return
            tasks = self.store.refresh()
            found = []
            commands = []
            for i, link in enumerate(note.tasks):
                try:
                    task = tm.find_task_by_anchor(tasks, link.id)
                except ValueError:
                    task = None
                found.append(task)
                commands.append(Command(str(i), task.description if task else link.title,
                                        description=task.status_label if task else "Task unavailable; note is preserved",
                                        enabled=task is not None))
            def chosen(choice):
                if choice is not None and (task := found[int(choice)]) is not None:
                    self.view, self.project, self.search_query = ("completed" if task.closed else "all"), "", ""
                    with self.prevent(Input.Changed):
                        self.query_one("#search", Input).value = ""
                    self.refresh_tasks(keep_id=task.id)
                    self.action_focus_tasks()
                    self.call_after_refresh(self.action_focus_tasks)
            self.push_screen(CommandScreen(commands, context="Open a linked task"), chosen)
        else:
            task = self._selected()
            notes = self.notes_store.refresh()
            linked = [n for n in notes if task and task.anchor and any(l.id == task.anchor for l in n.tasks)]
            if not linked:
                self.announce("No linked notes. Press l to link one.")
                return
            self.push_screen(CommandScreen([Command(str(i), n.title, description=n.category)
                                            for i, n in enumerate(linked)], context="Reference notes"),
                             lambda choice: self._open_reference(linked[int(choice)].file) if choice is not None else None)

    def action_unlink_reference(self) -> None:
        if self.view != "notes":
            return
        note = self.query_one(NotesWorkspace).current
        if not note or not note.tasks:
            return
        def chosen(choice):
            if choice is not None:
                with self._record("Unlink task from note", [note.file]):
                    self.notes_store.save(replace(note, tasks=tuple(l for i, l in enumerate(note.tasks) if i != int(choice))))
                self.refresh_tasks()
                self.announce("Task unlinked; note preserved")
        self.push_screen(CommandScreen([Command(str(i), l.title) for i, l in enumerate(note.tasks)],
                                       context="Unlink a task from this note"), self._guard(chosen))

    def action_task_from_reference(self) -> None:
        if self.view != "notes":
            return
        from .app import AddScreen
        note = self.query_one(NotesWorkspace).current
        if note is None:
            return
        def done(result):
            if not result:
                return
            current = self.notes_store.load(note.file)
            if current.revision != note.revision:
                raise NoteConflict("The note changed outside Taskman. Rescan before creating a linked task.")
            project = tm.clean_project_name(result["project"])
            path = f"Projects/{project}.md" if project else "Tasks/Inbox.md"
            with self._record("Create task from note", [path, note.file]):
                def create():
                    task = tm.add_task(self.vault, result["text"], project=project)
                    return tm.ensure_task_anchor(self.vault, task)
                self._save_with_task_change([path], create, lambda task: self.notes_store.save(
                    replace(note, tasks=note.tasks + (TaskLink(task.anchor, task.description),))))
            self.refresh_tasks()
            self.announce("Task created and linked; note preserved")
        self.push_screen(AddScreen(self.store.projects(), project=note.projects[0] if note.projects else "",
                                   initial=note.title), self._guard(done))

    def _show_reference_links(self, task) -> None:
        linked = [n for n in self._notes if task and task.anchor and any(l.id == task.anchor for l in n.tasks)]
        self.query_one("#ins-refhead").display = bool(linked)
        options = self.query_one("#ins-references", OptionList)
        options.display = bool(linked)
        options.set_options([Option(Text(n.title), id=n.file) for n in linked])

    def _open_inspector_reference(self, event: OptionList.OptionSelected) -> None:
        if event.option_id:
            self._open_reference(event.option_id)

    def action_note_errors(self) -> None:
        self.push_screen(CommandScreen([Command(str(i), file, description=error, enabled=False)
                                        for i, (file, error) in enumerate(self.notes_store.errors.items())],
                                       context="Notes that could not be read"))

    def _reference_commands(self) -> list[Command]:
        notes = self.view == "notes"
        current = self.query_one(NotesWorkspace).current if notes else None
        target = self._selected()
        commands = [Command("notes", "Go to Notes", "8", "Browse reference notes"),
                    Command("new_reference", "New reference note", "Ctrl+N", "Capture a standalone Markdown note"),
                    Command("link_reference", "Link task to note" if notes else "Link reference note", "l",
                            "Connect existing notes and tasks", enabled=bool(current or target)),
                    Command("linked_references", "Open linked tasks" if notes else "Open linked notes", "k",
                            "Read the related reference material or task", enabled=bool(current or target))]
        if notes:
            commands += [Command("edit_reference", "Edit reference note", "e", enabled=current is not None),
                         Command("note_category", "Filter notes by category", "c"),
                         Command("note_tag", "Filter notes by tag", "t"),
                         Command("note_project", "Filter notes by project", "j"),
                         Command("task_from_reference", "Create task from note", "Ctrl+T", enabled=current is not None),
                         Command("unlink_reference", "Unlink task from note", enabled=bool(current and current.tasks))]
        elif target:
            commands.append(Command("new_linked_reference", "Create a linked reference note"))
        if self.notes_store.errors:
            commands.append(Command("note_errors", "Show unreadable notes", description="Files were preserved without changes"))
        return commands
