<img src="assets/taskman.png" width="96" height="96" alt="Taskman icon">

# Taskman

A keyboard-first task and notes manager for folders of Markdown files. Open a folder, capture tasks and reference notes, and keep your work readable in any text editor. No account or server is required.

## Download and run

**Windows:** download the `taskman-3.1.0-windows-x64.zip` asset from [GitHub Releases](https://github.com/rmcfarlin/Taskman/releases/tag/v3.1.0), extract the entire archive, and run:

```powershell
.\taskman\taskman.exe
```

The Windows executable includes the Taskman icon. The download also includes `taskman.ico` and `taskman.png` for shortcuts and terminal profiles.

The download includes Python and its dependencies. Keep the `_internal` folder beside `taskman.exe`. You can also double-click `Taskman.cmd`; it keeps errors visible if startup fails.

The welcome screen lets you enter or browse to a folder. Press **Ctrl+O** anytime to open another vault, or choose **Open Vault** from **Ctrl+K** commands. Recent folders are remembered.

Open a specific folder directly:

```powershell
.\taskman\taskman.exe --vault "C:\My work\Tasks"
```

Linux and macOS builds use the same commands with `./taskman/taskman`. Download the matching asset when available; each release lists the builds that have passed its checks. Linux standalone builds require a compatible recent glibc distribution. For other environments, use the Python installation below.

## Install from source

Download and extract `taskman-3.1.0-source.zip`, open a terminal in the extracted project, then run one command. This route needs **Python 3.10+** and internet access to install dependencies.

Windows PowerShell:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1
```

macOS / Linux:

```sh
sh scripts/install.sh
```

The installer creates an isolated environment in your user profile and launches Taskman. It prints the installed launch command. It does not put Python, application files, or dependencies inside your vault. Run the installer from a new source download to upgrade; your task files and settings remain separate.

If you already use uv or pipx, install the downloaded source folder with `uv tool install .` or `pipx install .`, then run `taskman`. The distribution name is `taskman-vault`; the command is `taskman`. This project does not require a PyPI release.

## Your folder, your files

Choose **Open folder** to work with existing Markdown. Choose **Set up & open** to add the missing Taskman structure after reviewing the preview:

```text
Your folder/
├── Tasks/Inbox.md
├── Projects/
├── Notes/
├── Documentation/
├── Templates/
└── .taskman/vault.json
```

Setup preserves existing files and folders. Tasks are standard checkboxes, such as `- [ ] Review launch plan`. Dates, priorities, projects, subtasks, and notes stay in Markdown. Task changes save immediately. Undo and redo apply to the current session and reset when you switch folders.

For scripted setup:

```powershell
taskman --init "C:\My work\Tasks"
taskman --vault "C:\My work\Tasks"
```

Setup can create a new folder or fill in missing structure in an existing one. Opening without setup requires an existing folder. Switching vaults never moves your tasks. The application does not initialize Git or run scripts from folders you open. Sync and backups can use the tools you already trust.

## Keyboard essentials

The bottom shortcut bar shows the actions available where you are working and wraps to fit the terminal. Dark Teal is the default theme; press **m** to preview the other themes, including Light and High contrast.

Press **/** to open Find. It stays visible while a filter is active; **Enter** or **↓** returns to the results, and **Esc** clears the filter and closes Find.

| Key | Action |
| --- | --- |
| Tab / Shift+Tab | Move between panes and fields |
| ↑ / ↓ | Move through tasks, options, and menus |
| Enter | Open the selected task / accept a dialog |
| Esc | Close a dialog or return from search |
| Ctrl+O | Open a vault |
| Ctrl+K | Search commands |
| / | Find tasks |
| a / e | Add / edit task |
| Space | Complete or reopen task |
| d / p / s | Due, scheduled, and repeat / priority / status |
| j / t / n | Project / subtask / note |
| i | Task details |
| u / Ctrl+Y | Undo / redo |
| m / h | Theme / keyboard help |
| Ctrl+S | Confirm saved state |
| Ctrl+Shift+S | Commit and push the vault to its existing Git remote |
| q | Quit |

Letter shortcuts apply while browsing tasks; typing in a text field edits the field. Use a terminal with a Unicode-capable font for the best display.

## Push to an existing Git remote

Press **Ctrl+Shift+S** from a task or Notes view, or choose **Push vault** from **Ctrl+K**. Taskman commits changed vault files and pushes the current branch to its existing remote. This includes notes, attachments, additions, and deletions; Git-ignored files and Taskman's temporary writer lock are excluded. With no new changes, it still pushes existing local commits.

The vault must be the root of an existing Git repository. Taskman uses the branch's configured push remote or upstream, then `origin` or a sole remote when no destination is configured. It never initializes Git, creates a GitHub repository, adds a remote, or force-pushes. If no remote exists, your changes stay saved locally. A rejected push keeps the local commit so you can resolve the problem and retry.

Git must be installed and authenticated already. Taskman reports missing credentials, ambiguous remotes, staged work, or unfinished Git operations instead of changing their configuration. It does not run a vault's `push.ps1` or Git hooks, and it does not pull, merge, or rebase automatically.

Repositories that use Git LFS or content filters must be pushed with their normal Git tooling.

**Ctrl+S** remains local save confirmation. In an editor, save shortcuts save that draft without publishing. Taskman preserves Shift in both the classic Windows console and Windows Terminal, including versions before 1.25. **Ctrl+K → Push vault** is also available in other terminals that cannot distinguish these keys.

For GitHub remotes, a successful result says **Pushed to GitHub** or **GitHub is up to date**, followed by the commit ID. The result stays visible for 12 seconds. A local Markdown save message only confirms files on your computer.

## Now and scheduling

Taskman opens in **Now** (**2**): open tasks due today or earlier, plus tasks scheduled for today or earlier. Forwarded tasks stay in All and search. **Overdue** contains missed deadlines; **Today** contains the rest, including unfinished scheduled work with a future deadline. Undated tasks remain available in **Inbox** (**5**) and **All open** (**1**). Inbox excludes tasks with either a due or scheduled date. Start dates remain visible in task details and do not determine Now membership.

The date column explains why a task appears: **Due today**, **Due 2d late**, or **Sched Sep 6**. **Overdue** (**3**) and **Next 7 days** (**4**) keep their due-date meaning.

Press **d** to edit **Due** (the deadline) and **Scheduled** (when you plan to work on it). Both fields accept `today`, `tomorrow`, a weekday such as `mon`, `+7`, or `YYYY-MM-DD`. Leave a field empty or type `clear` to remove it. A weekday means its next occurrence; naming today's weekday chooses next week. Quick buttons change the last date field you focused. **Enter** or **Ctrl+S** saves both fields together; **Esc** cancels. An invalid date or save conflict keeps your entries open. Undo restores both dates in one step.

## Recurring tasks

Press **d** and fill in **Repeat**, or choose **Set recurrence** from **Ctrl+K**. Choose **Presets** or type a rule, such as `every week`, `every 2 months`, or `every month on the last`. Set a due or scheduled date first; an existing start date also counts. Dates and Repeat save together. Choose `none` to remove recurrence.

Supported rules are `every day` / `every N days`, `every weekday`, `every week` / `every N weeks`, `every week on Monday, Wednesday, Friday`, `every month` / `every N months`, `every month on the 1st`, `every month on the last`, and `every year`. Add `when done` to calculate the next date from completion. Otherwise, it advances from the old date, even when that leaves the next occurrence overdue. Weekdays mean Monday through Friday; holidays are not excluded.

Completing a repeating task inserts the next occurrence immediately above it in the same Markdown file:

```markdown
- [ ] Monthly close 🔁 every month on the last 📅 2026-10-31
- [x] Monthly close 🔁 every month on the last 📅 2026-09-30 ✅ 2026-09-30
```

The reference date is due, then scheduled, then start. Other dates move by the same number of days, preserving the planned lead time. Plain monthly rules clamp to the last valid date when needed; use `every month on the last` to consistently choose month-end. `every month on the 31st` is unsupported.

The new occurrence keeps the task's text, priority, tags, project, recurrence, and own note. It gets a new stable ID and no subtasks. Completing a parent also completes its open subtasks; only the selected task creates a next occurrence. One undo restores the entire change. Cancelling does not create a successor. Reopening keeps the successor, and completing that same old occurrence again does not create a duplicate. Hidden Markdown identity and successor comments track this relationship; the example above omits them for readability.

Existing rules outside this supported set remain in the file and appear in task details. Completing an unsupported or undated repeating task marks it done and displays a warning without creating a successor. The Dates dialog rejects new unsupported rules and recurrence without a date.

Scripts can create or update recurrence with `--repeat "every week"` alongside `--add`, or with `--id ID`. `--complete ID` generates the successor once; `--json` includes recurrence details and any warning.

Scheduling work for tomorrow removes it from Now only when its deadline has not arrived. Tasks already due remain visible until you complete them or change their deadline.

## Automation

New tasks and subtasks receive a stable ID stored in a hidden Markdown comment. The ID survives line shifts and file moves. Existing files need no bulk migration: reading and listing tasks never adds IDs. Use `--ensure-id` to give an existing task a stable handle; legacy `FILE:LINE` references continue to work for `--under`.

```powershell
# Create a task and capture its stable ID.
$created = taskman --vault "C:\My work\Tasks" --add "Review supplier terms" --project Operations --due +7 --scheduled today --json | ConvertFrom-Json
$taskId = $created.task.id

# Read, reschedule, add an attached note, or complete that exact task.
taskman --vault "C:\My work\Tasks" --id $taskId --json
taskman --vault "C:\My work\Tasks" --id $taskId --scheduled tomorrow
taskman --vault "C:\My work\Tasks" --note "Check the revised quote." --under $taskId
taskman --vault "C:\My work\Tasks" --complete $taskId --json

# Give an older task an ID explicitly, or list Now without changing files.
taskman --vault "C:\My work\Tasks" --ensure-id "Projects/Operations.md:12" --json
taskman --vault "C:\My work\Tasks" --plain now --json
```

Completing an already-completed task does nothing. JSON output includes the stable ID (or `null` for a legacy task), current location, status, description, project, and dates. JSON errors return `ok: false` and a nonzero exit code. `--plain today` remains an alias for `--plain now`.

Taskman coordinates its own writers and rejects detected stale edits or ambiguous IDs. If another editor changes a task while you are editing it, refresh and retry. External editors and sync software do not participate in Taskman's writer lock.

## Reference notes

Press **8** to browse Markdown files in your vault's `Notes/` folder, including existing notes. Select a note to read its preview; press **e** or **Enter** to edit it. Press **a** to create a note, or **Ctrl+N** to capture one from a task view. Notes have a title, Markdown body, category, tags, and optional projects. Separate tags and projects with commas. Use **Tab** to move through the editor and **Ctrl+S** to save. **Esc** offers to discard unsaved changes or keep editing.

| Key while browsing Notes | Action |
| --- | --- |
| / | Search titles, full note contents, categories, tags, and projects |
| Ctrl+F | Find within the rendered note; Enter / Shift+Enter move between highlighted matches |
| s | Switch recently modified / title order; remembers your choice and selected note |
| Del | Delete the note with confirmation; **u** undoes and **Ctrl+Y** redoes |
| F2 | Preview a filename rename and update direct links across the vault |
| Ctrl+Shift+N | Capture a note from an editable template |
| c / t / j | Filter by category / tag / project |
| Esc | Clear search first, then clear category, tag, and project filters |
| l | Link the selected note to an existing task |
| k | Open a linked task |
| Ctrl+T | Create and link a task using the note's title as a starting point |
| Ctrl+K | Find commands, including **Unlink task from note** and **Show unreadable notes** when applicable |

Notes can stand alone or support several tasks. Creating a task from a note keeps the original note and its contents. Deleting a linked task leaves the note available, with that task marked unavailable. Unlinking removes the relationship without deleting either item.

Notes open in **Recently modified** order. **Ctrl+F** searches the reading pane independently of the library's **/** filter. **Esc** closes in-note Find before clearing library filters. Deleting a note keeps its linked tasks. Undo restores its file, content, and task relationships during the current session (up to 50 actions).

**F2** renames the file in its current folder; editing the title alone leaves the filename unchanged. Preview lists affected Markdown files and link counts before you apply. Taskman updates local Markdown links, reference definitions, wiki links, and HTML links that resolve to this file, preserving labels and anchors. The scan covers the vault's Markdown, including task files, other notes, and note templates; it excludes hidden and technical folders except the note template directory. Ordinary filename mentions, code examples, and external URLs stay untouched. Ambiguous links, unreadable content, existing filenames, and detected external edits stop the rename. Case-only renames are not supported. The rename and link rewrites share one undo action.

**Ctrl+Shift+N** starts a new note from a template. The included **Meeting** template has attendees, discussion, decisions, and actions. Use **Ctrl+K → Edit note template** to customize it for future notes. Custom templates are Markdown files in `.taskman/templates/notes/`; add more `.md` files there to make them available in the picker. A custom `Meeting.md` replaces the built-in version. `{{date}}` inserts today's ISO date on capture. Template edits support undo and never change notes already created from them.

While browsing tasks, **l** links an existing reference note or creates a linked note, and **k** opens related notes. Linked notes also appear in the task's details pane. The existing **n** shortcut still edits the task's attached note.

Notes remain local Markdown files and use your existing sync and backup tools. Taskman stores note labels and relationships in a Markdown comment. Linking an older task adds an identity comment so the relationship survives edits and moves; keep these comments when editing files externally. New tasks already have these IDs.

## Plain terminal commands

```powershell
taskman --version
taskman --help
taskman --vault "C:\My work\Tasks" --check
taskman --vault "C:\My work\Tasks" --plain today
taskman --vault "C:\My work\Tasks" --add "Review launch plan"
taskman --vault "C:\My work\Tasks" --plain search --search launch
```

Plain output works in pipes and with screen readers. See `taskman --help` for the complete command options.

## Settings and troubleshooting

Themes, recent folders, and crash reports live in the per-user Taskman configuration directory. Set `TASKMAN_CONFIG_DIR` to choose a different location; this is useful for portable use or separate profiles. Set `TASKMAN_VAULT` to select a default vault. An explicit `--vault` takes precedence.

If startup fails, run the command in an existing terminal or use `Taskman.cmd` to keep the message visible. Diagnostic reports contain traceback information without capturing local variables; the error message shows the report location when one is written.

To upgrade the standalone app, extract the new download into a new application folder and launch it. To remove it, delete that application folder. Neither action removes your vaults. Source installations can be removed by deleting the installer-created environment folder; uv and pipx installations can be removed with their respective `tool uninstall taskman-vault` / `uninstall taskman-vault` commands.

See [development and release checks](docs/DEVELOPMENT.md) to build the app and verify a release.

## Contributing and security

See the [contribution guide](https://github.com/rmcfarlin/Taskman/blob/main/CONTRIBUTING.md) for bug reports, feature proposals, development setup, and pull requests. Report vulnerabilities privately using the [security policy](SECURITY.md).

## License

Taskman is licensed under the [MIT License](LICENSE). Third-party dependencies retain their own licenses.
