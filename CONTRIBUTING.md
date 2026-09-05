# Contributing to Taskman

Bug reports, documentation fixes, and focused code changes are welcome.

## Report a bug or suggest a change

Search [existing issues](https://github.com/rmcfarlin/Taskman/issues) before opening a new one. For a bug, include the Taskman version, operating system, terminal, steps to reproduce, and expected and actual behavior. Use a small, fictional Markdown example when file content matters. For a larger feature, open an issue to discuss the intended behavior before starting implementation.

Never include personal vault data, credentials, or private file paths in issues, pull requests, screenshots, logs, or test fixtures. Review diagnostic reports before sharing them. Follow [SECURITY.md](SECURITY.md) for suspected vulnerabilities.

## Set up a development environment

Use Python 3.10 or newer. Fork the repository, clone your fork, and create a branch for your change.

```sh
python -m venv .venv
```

Activate the environment with `.venv\Scripts\Activate.ps1` in Windows PowerShell or `source .venv/bin/activate` on macOS/Linux. Then install the development dependencies and run the tests:

```sh
python -m pip install -e ".[test,build]"
python -m pytest tui -q
```

You can use the environment's Python path instead of activating it: `.venv\Scripts\python.exe` on Windows or `.venv/bin/python` on macOS/Linux.

For manual checks, create a disposable folder outside the checkout and use only fictional tasks:

```sh
python -m tui --init "PATH_TO_DISPOSABLE_VAULT"
python -m tui --vault "PATH_TO_DISPOSABLE_VAULT"
```

Replace the placeholder with your test folder's path. Task edits save immediately, so do not use your personal vault for development checks. See [development and release checks](docs/DEVELOPMENT.md) for packaging and distribution smoke tests.

## Submit a pull request

Keep changes focused. Preserve existing Markdown content and file behavior, and keep workflows usable from the keyboard. For behavior changes, add or update tests that reproduce the issue or demonstrate the new behavior. Use temporary folders and fictional data in tests.

Run the test suite before opening a [pull request](https://github.com/rmcfarlin/Taskman/pulls) against `main`. Explain what changed and why, link the relevant issue, and describe the checks you ran. Include screenshots for visible UI changes when useful. Do not commit vaults, local settings, diagnostic reports, build outputs, or virtual environments.

Contributions are made under the project's [MIT License](LICENSE). Submit only work you have the right to contribute, and retain required attribution and license notices for any third-party material.
