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
