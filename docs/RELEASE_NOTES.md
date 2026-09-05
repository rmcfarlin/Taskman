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
