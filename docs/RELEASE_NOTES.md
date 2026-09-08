# Taskman 3.3.0

Tag tasks by person or topic, then find them across projects or within a project. Existing Markdown vaults need no migration.

- Added a **Tags** section below Projects in the sidebar, sorted by open-task count from highest to lowest with alphabetical ties. Choose tags with the mouse or keyboard and use **All tags** to clear the filter. Counts update as tasks change; selecting a task tag from Notes opens All open.
- Added **g** to edit task tags and a **Tags** field when adding tasks or subtasks. Tags such as `person/alex` and `waiting` accept spaces or commas, preserve project assignment and task details, and support undo/redo.
- Added **Ctrl+G** to filter tasks by an exact tag, including people such as `person/alex`, across projects. The filter combines with the current view and Find, remains visible while navigating, and clears through **All tags**, Commands, or **Esc** after Find.
- Find understands exact `#tag` terms, including multiple tags combined with text. Tag matching ignores case and keeps untagged parents visible as context for matching subtasks.
- Markdown links, Obsidian wikilinks, URL fragments, and inline code examples stay intact when editing task tags.

# Taskman 3.2.0

This release gives tasks and notes more room and adds a GitHub release updater that checks automatically and installs after acceptance.

- Reduced the header to one row, removed redundant counts and navigation hints, and limited the contextual shortcut dock to two rows with **Ctrl+K More** for the full command list. Notes Delete and Undo remain discoverable.
- Added **Ctrl+B** to show or hide the sidebar for the session. Notes now appears above Projects; medium-width Notes layouts hide navigation before sacrificing the reading pane.
- Notes use compact title/category rows when space is limited. Resizing preserves selection and active in-note search.
- Reworked keyboard Help into aligned, wrapping rows with a visible Close control. Dialogs have compact buttons and inputs, clear focus, and visible Save/Cancel controls in small terminals.
- Routine confirmations appear temporarily in the status row, then restore the current view context. Errors and the 12-second GitHub push result remain prominent.
- Added a true Light theme, including readable selections, inputs, warnings, and errors. Existing Light settings migrate automatically.
- Renamed the undated task group to **No due date**, preserving its membership and ordering.
- Taskman checks for a newer stable release at startup and every six hours while open. Available updates show change notes; background checks stay quiet when current or offline and wait until editing, switching vaults, and pushing have finished.
- Accept **Update now** once to download, verify, install, and restart automatically. **Later** postpones that version for the session; **Check for updates** in **Ctrl+K** remains available, along with terminal-only `--check-updates`.
- Windows portable updates verify release digests and file ownership before installation. The installer waits for Taskman to close, retains the previous app, and rolls back failed installation. It preserves the vault and per-user settings. Downloads can be cancelled before installation starts.
- Earlier portable installs need one manual upgrade to obtain the updater manifest. Source, wheel, macOS, and Linux installations show the GitHub download route.

# Taskman 3.1.0

Notes now support deletion with undo, recent-first sorting, search within long notes, filename renaming with direct link updates, and reusable templates. Existing vaults need no migration.

- **Del** deletes a note after confirmation, keeps linked tasks, and supports session undo/redo.
- Notes default to **Recently modified**; **s** switches to title order and remembers the preference without moving the selection.
- **Ctrl+F** searches within the rendered note with highlights, match counts, next/previous navigation, and scrolling. The library's **/** filter remains independent.
- **F2** previews a filename rename with direct link updates across vault Markdown, including note templates. Labels and anchors are preserved. Ambiguous links and detected external changes stop the operation; one undo restores the filename and affected links together.
- **Ctrl+Shift+N** captures a note from a template. A Meeting template is included; **Edit note template** in **Ctrl+K** customizes future notes. Custom Markdown templates live in `.taskman/templates/notes/` and support `{{date}}`.
- Multi-file undo/redo now rechecks edits after staging and rolls back its own writes if applying a change fails.

# Taskman 3.0.0

Recurring tasks, scheduled work in Now, stable task IDs for automation, and reliable Windows keyboard pushing are now part of Taskman. Your vault stays plain Markdown, with no bulk migration required.

- Fixed physical **Ctrl+Shift+S** in the Windows console and Windows Terminal, including versions before 1.25. The input driver requests the Windows keyboard protocol so Shift survives the terminal transport, distinguishing push from local **Ctrl+S** saves.
- Push results now explicitly name **GitHub** for GitHub remotes, show the commit ID, and distinguish **Pushed** from **up to date**. The result remains visible for 12 seconds.
- Added **Repeat** to the Dates dialog with presets, typed rules, validation, and **Set recurrence** in the command menu. Due, scheduled, and recurrence changes save atomically with one undo.
- Completing a recurring task creates its next occurrence above the completed one. Dates retain their offsets; the task's own note is copied, while subtasks stay with the completed task. Each new occurrence gets its own stable ID.
- Supports daily, weekday, weekly, monthly, and yearly intervals, selected weekdays, first/last day of the month, and completion-based `when done` rules. Unsupported imported rules remain visible and produce a warning on completion without spawning a task.
- Reopening keeps the next occurrence; repeating completion does not create duplicates. Cancellation ends an occurrence without spawning. CLI `--repeat` edits the same Markdown rules, and completion results expose the successor and any warning.

- Restored **Ctrl+Shift+S** and **Push vault** in the command menu to commit vault changes and push to an existing Git remote. Git setup and repository creation remain explicit external actions. Local save and editor save shortcuts do not publish.
- Git pushes run in the background, respect ignored files, exclude the temporary writer lock, preserve local commits after a failed push, and report missing or ambiguous destinations. No automatic pull, rebase, force-push, or vault script execution.
- Replaced Today with **Now** (**2**) and made it the initial view: work due or scheduled today or earlier, excluding forwarded tasks. Missed deadlines appear under Overdue; all remaining qualifying work appears under Today. **All open** remains on **1** and **Notes** on **8**.
- Added a **Dates** editor (**d**) for due, scheduled, and recurrence, with focused-field quick date buttons, shared natural-language date input, atomic saving, and draft retention on errors. Date labels explain why a task appears in Now.
- Kept Overdue and Next 7 due-date-only; Inbox now excludes scheduled work. The plain `today` command remains an alias of `now`.
- Added stable IDs to newly created tasks and subtasks, plus explicit ID assignment for legacy tasks. Reads never migrate a vault or change existing task IDs.
- Added CLI lookup, date updates, explicit completion, and JSON output. Stable IDs also work with subtask and attached-note commands. Repeating completion never reopens a task.
- Added shared writer coordination and stale-task checks for task changes, Notes writes, and undo/redo. Anchored targets can be resolved after line shifts or moves; ambiguous IDs and detected external edits are rejected.

Existing vaults need no bulk migration. Recurrence runs when you explicitly complete a task; opening a vault does not generate occurrences.

# Taskman 2.5.0

Keep reusable reference material alongside your tasks. The new Notes workspace supports standalone Markdown notes, full-text search, categories, tags, and optional links to tasks and projects.

- Added a **Notes** workspace (**8**) for standalone Markdown notes in the vault's `Notes/` folder, including existing files, with a list and reading preview.
- Added a note editor with title, Markdown body, category, tags, and projects. **a** creates a note in Notes, **e** or **Enter** edits it, **Ctrl+N** captures a note from a task view, and **Ctrl+S** saves. Cancelling unsaved edits offers to keep editing or discard them.
- Added full-content note search (**/**), category/tag/project filters (**c**, **t**, **j**), and commands for unreadable notes. **Esc** clears search before clearing the other filters.
- Added links between notes and tasks: **l** links them, **k** opens related items, and the task details pane lists reference notes. **Ctrl+T** creates a linked task while preserving the original note. The command menu can unlink a task without deleting either item.
- Added persistent task identities when linking, so relationships survive task edits, line shifts, and moves. Deleting a task preserves its reference notes. Existing task-attached notes remain available with **n**.
- Added undo and redo for note changes, checks for external edits, and rollback when a linked task or note cannot be saved. An unsuccessful save keeps the note editor and its draft open.
- Adapted the reading pane and editor for compact terminals, with visible focus, readable Markdown headings, and complete editor borders.
- Excluded checkbox-shaped text in Markdown frontmatter and HTML comments from task lists, while preserving real task line numbers and task-attached notes.

Existing vaults need no bulk migration. Notes and their labels remain in local Markdown files, using existing backup and sync tools. Task identity comments are added only when linking. Reopen Taskman and press **8** to start using Notes.

# Taskman 2.4.3

This release gives tasks more room and makes mouse navigation more predictable.

- Removed the duplicate view heading and its command hint. The sidebar and status bar continue to show the current view and task counts.
- Find stays hidden until you press **/**, choose Find in the command menu, or click its shortcut. **Enter** or **Down** returns to the results, keeping an active filter visible and closing an empty field. **Escape** clears the filter and closes Find.
- Recovered up to five rows for tasks on larger terminals, and three rows at common compact sizes.
- Applied the selected theme's background consistently across the workspace, while retaining clear selection and focus indicators.
- Replaced the extra task-pane outline with a single divider between the sidebar and tasks.
- Fixed sidebar clicks selecting the wrong row when focus changed. Rows now stay in place, and hovering no longer paints a second selection.
- Task rows also stay in place when you click or double-click from Find, so opening the field cannot change which task a click selects.
- Preserved keyboard navigation, including returning to Find after closing the command menu or keyboard guide.
- Included these notes in the source and standalone downloads, with automated checks for their contents.

Existing vaults and settings need no migration. Extract the complete standalone archive into an application folder, then reopen Taskman. Task editing and Markdown storage are unchanged.
