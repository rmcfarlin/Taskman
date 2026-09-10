# Development and releases

Use Python 3.10 or newer. Build a standalone application separately on each target operating system; PyInstaller is not a cross-compiler.

```sh
python -m venv .venv
# Windows: .venv\Scripts\python.exe
# macOS/Linux: .venv/bin/python
python -m pip install -e ".[test,build]"
python -m pytest tui -q
python scripts/build_release.py --standalone
python scripts/smoke_distribution.py
```

Activate the environment before the final four commands, or substitute its Python path. The build produces a wheel, source tarball, clean source ZIP, and native standalone ZIP in `dist/`, with SHA-256 checksums. The application wheel contains only the runtime modules in the explicit `setup.py` allowlist. The source archive includes tests and build instructions. Vaults, settings, logs, build environments, caches, and artifacts are excluded.

All four distribution formats include Taskman's `LICENSE`; wheel metadata declares `MIT`. Source archives also include `CONTRIBUTING.md` and `SECURITY.md`, and standalone downloads include `SECURITY.md` beside the executable. When changing packaging, verify these files remain present in the built archives. See the [contribution guide](../CONTRIBUTING.md) for the pull request workflow.

Update `docs/RELEASE_NOTES.md` with each version. Source archives include this file, standalone downloads place it beside the executable as `RELEASE_NOTES.md`, and the tag workflow uses it for the GitHub release description.

`smoke_distribution.py` requires the current version's wheel, source tarball, source ZIP, and native ZIP for the host platform. It checks MIT metadata and packaged license/document contents, then installs the wheel in a temporary environment outside the checkout, exercises plain commands against folders containing spaces and Unicode, and verifies the unpacked native executable without importing the source checkout. UI and integration tests exercise vault selection, switching, and keyboard workflows before packaging.

GitHub Actions runs the test suite on Windows, macOS, and Ubuntu with Python 3.10 and 3.13, then builds and smoke-tests each native download on 3.13. A `v*` tag additionally creates a GitHub release with the passing assets. Version tags must match `tui.__version__`. There is no automatic PyPI publication.

Optional local QA tools (not part of CI):

```sh
python -m tui.check          # parallel pytest groups with -W error; writes artifacts/checks-*
python -m tui.visual_check   # SVG screenshots into artifacts/visual
```

Neither module ships in the wheel. Prefer `python -m pytest tui -q` for day-to-day work.

For release review, confirm the full test job passes, inspect the artifacts and `SHA256SUMS.txt`, then smoke the downloaded asset. Native executable signing is not configured in this repository; builds are not code-signed.
