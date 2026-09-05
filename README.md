<img src="assets/taskman.png" width="96" height="96" alt="Taskman icon">

# Taskman

A keyboard-first task manager for folders of Markdown files. Open a folder, capture your tasks, and keep your work readable in any text editor. No account or server is required.

## Download and run

**Windows:** download the `taskman-2.4.2-windows-x64.zip` asset from [GitHub Releases](https://github.com/rmcfarlin/Taskman/releases), extract the entire archive, and run:

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

Download and extract `taskman-2.4.2-source.zip`, open a terminal in the extracted project, then run one command. This route needs **Python 3.10+** and internet access to install dependencies.

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
| d / p / s | Due date / priority / status |
| j / t / n | Project / subtask / note |
| i | Task details |
| u / Ctrl+Y | Undo / redo |
| m / h | Theme / keyboard help |
| Ctrl+S | Confirm saved state |
| q | Quit |

Letter shortcuts apply while browsing tasks; typing in a text field edits the field. Use a terminal with a Unicode-capable font for the best display.

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
