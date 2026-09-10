"""Verified portable-release updates. No git operations or vault writes.

The old app prepares a release; a separate copy of the new runtime waits for
the old process to exit, verifies both trees again, then swaps whole folders.
Only directories exactly matching a Taskman distribution manifest qualify.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import stat
import subprocess
import sys
import time
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import uuid
import zipfile

from . import __version__
from .vaults import is_reserved_windows_name

REPOSITORY = "rmcfarlin/Taskman"
RELEASES_URL = f"https://github.com/{REPOSITORY}/releases"
API_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
APP_ID = "taskman-vault"
MANIFEST_NAME = ".taskman-install.json"
MAX_ARCHIVE = 256 * 1024 * 1024
MAX_EXPANDED = 1024 * 1024 * 1024
MAX_FILES = 6000
MAX_FILE = 256 * 1024 * 1024
HELPER_READY_TIMEOUT = 10
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_VERSION = re.compile(r"v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:(?:\.)?(dev|a|b|rc)(\d+))?\Z")


class UpdateError(Exception):
    """An update cannot safely proceed; suitable for display to the user."""


class UpdateCancelled(UpdateError):
    pass


@dataclass(frozen=True)
class Release:
    version: str
    body: str
    html_url: str
    asset_name: str
    download_url: str
    sha256: str
    checksum_url: str
    asset_size: int
    checksum_sha256: str


@dataclass(frozen=True)
class InstallSupport:
    supported: bool
    reason: str
    install_dir: Path | None = None


@dataclass(frozen=True)
class PreparedUpdate:
    plan_path: Path
    helper_executable: Path
    version: str


def _version(value: str) -> tuple[int, ...]:
    match = _VERSION.fullmatch(value) if isinstance(value, str) else None
    if not match:
        raise UpdateError(f"Unrecognized Taskman version: {value!r}")
    major, minor, patch, stage, number = match.groups()
    return (int(major), int(minor), int(patch), {"dev": 0, "a": 1, "b": 2, "rc": 3, None: 4}[stage], int(number or 0))


def platform_asset(version: str, system: str | None = None, machine: str | None = None) -> str:
    system = system or platform.system()
    machine = (machine or platform.machine()).lower()
    systems = {"Windows": "windows", "Linux": "linux", "Darwin": "macos"}
    arch = {"amd64": "x64", "x86_64": "x64", "x64": "x64", "aarch64": "arm64", "arm64": "arm64"}.get(machine)
    if system not in systems or arch is None:
        raise UpdateError("No portable updater for this platform. Download the matching release manually.")
    return f"taskman-{version}-{systems[system]}-{arch}.zip"


def _safe_url(url: str) -> None:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except (ValueError, TypeError) as exc:
        raise UpdateError("The update download URL is invalid.") from exc
    if (parsed.scheme != "https" or parsed.username or parsed.password or port not in (None, 443)
            or parsed.hostname not in {"api.github.com", "github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"}
            or parsed.fragment):
        raise UpdateError("The update download left GitHub's trusted download hosts.")


class _Redirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _safe_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _request(url: str, limit: int, progress=None) -> bytes:
    _safe_url(url)
    request = Request(url, headers={"User-Agent": f"Taskman/{__version__} (+https://github.com/{REPOSITORY})",
                                   "Accept": "application/vnd.github+json" if url == API_URL else "application/octet-stream",
                                   "X-GitHub-Api-Version": "2022-11-28"})
    try:
        with build_opener(_Redirects()).open(request, timeout=20) as response:
            _safe_url(response.geturl())
            length = response.headers.get("Content-Length")
            if length is not None and (not length.isdigit() or int(length) > limit):
                raise UpdateError("The update download exceeds its size limit.")
            chunks, count = [], 0
            deadline = time.monotonic() + 180
            while True:
                if time.monotonic() > deadline:
                    raise UpdateError("The update download timed out. Try again later.")
                chunk = response.read(min(1024 * 1024, limit + 1 - count))
                if not chunk:
                    break
                count += len(chunk)
                if count > limit:
                    raise UpdateError("The update download exceeds its size limit.")
                chunks.append(chunk)
                if progress:
                    progress(f"Downloading… {count // (1024 * 1024)} MB")
            return b"".join(chunks)
    except HTTPError as exc:
        if exc.code in (403, 429):
            raise UpdateError("GitHub's request limit was reached. Try again later.") from exc
        raise UpdateError(f"GitHub could not provide the update (HTTP {exc.code}).") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise UpdateError("Could not connect to GitHub. Check your connection and try again.") from exc


def _fetch(url: str, limit: int, transport, progress=None) -> bytes:
    _safe_url(url)
    data = (transport or _request)(url, limit, progress)
    if not isinstance(data, bytes) or len(data) > limit:
        raise UpdateError("The update response is invalid or too large.")
    return data


def _json(data: bytes) -> dict:
    try:
        value = json.loads(data)
        if not isinstance(value, dict):
            raise ValueError("not an object")
        return value
    except (ValueError, UnicodeError) as exc:
        raise UpdateError("The update metadata is invalid.") from exc


def _digest(value) -> str:
    if not isinstance(value, str) or not value.startswith("sha256:") or not _HEX.fullmatch(value[7:]):
        raise UpdateError("GitHub did not provide the required SHA-256 digest for this release.")
    return value[7:]


def check_for_update(current_version: str = __version__, *, system=None, machine=None, transport=None) -> Release | None:
    payload = _json(_fetch(API_URL, 2 * 1024 * 1024, transport))
    tag = payload.get("tag_name")
    if not isinstance(tag, str):
        raise UpdateError("GitHub returned a release without a version.")
    version = tag.removeprefix("v")
    if payload.get("draft") is not False or payload.get("prerelease") is not False or _version(version)[3] != 4:
        raise UpdateError("GitHub did not return a stable public release.")
    if _version(version) <= _version(current_version):
        return None
    expected_url = f"{RELEASES_URL}/tag/{quote(tag, safe='')}"
    if payload.get("html_url") != expected_url:
        raise UpdateError("GitHub returned a release from an unexpected repository.")
    name = platform_asset(version, system, machine)
    assets = payload.get("assets")
    if not isinstance(assets, list):
        raise UpdateError("The release has no downloadable assets.")
    selected = []
    for expected in (name, "SHA256SUMS.txt"):
        matches = [item for item in assets if isinstance(item, dict) and item.get("name") == expected]
        if len(matches) != 1:
            raise UpdateError(f"The release does not contain exactly one {expected}. Use the release page for manual downloads.")
        item = matches[0]
        url = f"https://github.com/{REPOSITORY}/releases/download/{quote(tag, safe='')}/{expected}"
        if item.get("browser_download_url") != url or item.get("state") != "uploaded":
            raise UpdateError("The release asset is not a completed Taskman upload.")
        size = item.get("size")
        if type(size) is not int or not 0 < size <= (MAX_ARCHIVE if expected == name else 1024 * 1024):
            raise UpdateError("The release asset size is invalid.")
        selected.append((url, _digest(item.get("digest")), size))
    body = payload.get("body") or "No release notes provided."
    if not isinstance(body, str):
        raise UpdateError("The release notes are invalid.")
    return Release(version, body, expected_url, name, selected[0][0], selected[0][1], selected[1][0], selected[0][2], selected[1][1])


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _no_links(path: Path) -> Path:
    path = Path(os.path.abspath(path))
    for part in (*reversed(path.parents), path):
        if part.exists() or part.is_symlink():
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise UpdateError("Updates cannot operate through symbolic links or junctions.")
    return path


def _member(name: str) -> str:
    if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
        raise UpdateError("The update contains an unsafe filename.")
    parts = name.split("/")
    if any(not part or part in (".", "..") or part[-1:] in (" ", ".") or
           any(ord(ch) < 32 or ch in '<>:"|?*' for ch in part) or
           is_reserved_windows_name(part) for part in parts):
        raise UpdateError("The update contains an unsafe filename.")
    return PurePosixPath(*parts).as_posix()


def _walk(root: Path, *, build_links: bool = False):
    """Inspect a directory before descending; never traverse a junction."""
    _no_links(root)
    pending = [root]
    count = 0
    while pending:
        directory = pending.pop()
        _no_links(directory)
        for path in sorted(directory.iterdir()):
            count += 1
            if count > MAX_FILES + 1000:
                raise UpdateError("The application directory has too many entries.")
            if build_links and path.is_symlink() and path.is_dir():
                # PyInstaller macOS frameworks include directory aliases.
                # The ZIP builder archives their canonical files and flattens
                # file aliases; it does not traverse directory aliases.
                if not path.resolve().is_relative_to(root.resolve()):
                    raise UpdateError("The build contains a link outside its bundle.")
                continue
            if not (build_links and path.is_symlink() and path.is_file()):
                _no_links(path)
            yield path
            if path.is_dir():
                pending.append(path)


def _tree(root: Path, *, build_links: bool = False) -> dict[str, str]:
    files = {}
    aliases = set()
    for path in _walk(root, build_links=build_links):
        relative = _member(path.relative_to(root).as_posix())
        if path.is_symlink() and build_links:
            if not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
                raise UpdateError("The build contains a link outside its bundle.")
        else:
            _no_links(path)
        alias = relative.casefold()
        if alias in aliases:
            raise UpdateError("The application contains colliding filenames.")
        aliases.add(alias)
        if path.is_file() and relative != MANIFEST_NAME:
            files[relative] = _hash(path)
        elif not path.is_dir() and relative != MANIFEST_NAME:
            raise UpdateError("The application contains an unsupported filesystem entry.")
    return files


def write_install_manifest(bundle: Path, version: str, *, system=None, machine=None) -> dict:
    """Build-time ownership manifest; archived copies contain ordinary files."""
    _version(version)
    value = {"schema": 1, "app_id": APP_ID, "version": version,
             "asset": platform_asset(version, system, machine), "files": _tree(bundle, build_links=True)}
    (bundle / MANIFEST_NAME).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    return value


def _manifest(root: Path, expected_version=None) -> dict:
    _no_links(root)
    path = root / MANIFEST_NAME
    if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
        raise UpdateError("This copy needs a one-time manual update to an updater-enabled portable release.")
    _no_links(path)
    value = _json(path.read_bytes())
    if value.get("schema") != 1 or value.get("app_id") != APP_ID or not isinstance(value.get("version"), str):
        raise UpdateError("The installed application manifest is not recognized.")
    _version(value["version"])
    if expected_version is not None and value["version"] != expected_version:
        raise UpdateError("The downloaded application version does not match the release.")
    files = value.get("files")
    if not isinstance(files, dict) or not 1 <= len(files) <= MAX_FILES:
        raise UpdateError("The application manifest is invalid.")
    for name, digest in files.items():
        _member(name)
        if name == MANIFEST_NAME or not isinstance(digest, str) or not _HEX.fullmatch(digest):
            raise UpdateError("The application manifest is invalid.")
    exe = "taskman.exe" if os.name == "nt" else "taskman"
    if exe not in files or "LICENSE" not in files or "RELEASE_NOTES.md" not in files:
        raise UpdateError("The application manifest is incomplete.")
    if value.get("asset") != platform_asset(value["version"]):
        raise UpdateError("The application manifest targets a different platform.")
    if _tree(root) != files:
        raise UpdateError("The application contains changed or additional files. Keep them safe and update manually.")
    # Empty extra directories are also ownership boundaries, even without files.
    expected_dirs = {str(parent) for name in files for parent in PurePosixPath(name).parents if str(parent) != "."}
    if {p.relative_to(root).as_posix() for p in _walk(root) if p.is_dir()} != expected_dirs:
        raise UpdateError("The application contains additional folders. Move them outside the app before updating.")
    return value


def installation_support(executable=None, frozen=None) -> InstallSupport:
    if frozen is None:
        frozen = getattr(sys, "frozen", False)
    if not frozen:
        return InstallSupport(False, "This is a source or Python installation. Update it using its original installer, or download a portable release.")
    if platform.system() != "Windows":
        return InstallSupport(False, "Automatic installation is available for Windows portable copies. Download the matching release and replace the app folder manually on this platform.")
    install = Path(executable or sys.executable).absolute().parent
    try:
        executable = _no_links(Path(executable or sys.executable))
        if executable.name != ("taskman.exe" if os.name == "nt" else "taskman") or install == install.parent or (install / ".git").exists():
            raise UpdateError("This application location cannot be replaced by the portable updater.")
        _manifest(install)
    except (OSError, UpdateError) as exc:
        return InstallSupport(False, str(exc), install)
    return InstallSupport(True, "Portable installation can be updated after Taskman closes.", install)


def _extract(data: bytes, destination: Path) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            if not 1 <= len(members) <= MAX_FILES + 100:
                raise UpdateError("The update archive has too many files.")
            seen, spellings, files, total = set(), {}, [], 0
            for info in members:
                original = info.orig_filename[:-1] if info.is_dir() else info.orig_filename
                _member(original)
                raw = info.filename[:-1] if info.is_dir() else info.filename
                name = _member(raw)
                if name != "taskman" and not name.startswith("taskman/"):
                    raise UpdateError("The update archive has an unexpected root folder.")
                alias = name.casefold()
                if alias in seen:
                    raise UpdateError("The update archive contains duplicate filenames.")
                seen.add(alias)
                for item in (PurePosixPath(name), *PurePosixPath(name).parents):
                    spelling = item.as_posix()
                    prior = spellings.setdefault(spelling.casefold(), spelling)
                    if prior != spelling:
                        raise UpdateError("The update archive contains case-colliding directories.")
                mode = info.external_attr >> 16
                kind = stat.S_IFMT(mode)
                if kind not in (0, stat.S_IFREG, stat.S_IFDIR) or bool(info.flag_bits & 1):
                    raise UpdateError("The update archive contains a link, special file, or encrypted file.")
                if info.is_dir():
                    continue
                total += info.file_size
                if info.file_size > MAX_FILE or total > MAX_EXPANDED or info.file_size > max(1024 * 1024, info.compress_size * 1000):
                    raise UpdateError("The update archive exceeds its extraction limits.")
                files.append((info, name.removeprefix("taskman/"), mode))
            file_names = {name.casefold() for _, name, _ in files}
            if any(str(parent).casefold() in file_names for _, name, _ in files for parent in PurePosixPath(name).parents):
                raise UpdateError("The update archive contains overlapping file paths.")
            destination.mkdir()
            for info, name, mode in files:
                target = destination.joinpath(*PurePosixPath(name).parts)
                _no_links(target)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("xb") as output:
                    copied = 0
                    while chunk := source.read(1024 * 1024):
                        copied += len(chunk)
                        if copied > info.file_size:
                            raise UpdateError("An update file exceeds its declared size.")
                        output.write(chunk)
                if copied != info.file_size:
                    raise UpdateError("An update file is incomplete.")
                if os.name != "nt":
                    target.chmod(0o755 if mode & 0o111 else 0o644)
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
        raise UpdateError("The update archive is damaged or unsupported.") from exc


def _atomic_json(path: Path, value: dict) -> None:
    _no_links(path)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _probe(executable: Path, version: str, work: Path) -> None:
    work.mkdir(exist_ok=True)
    environment = os.environ.copy()
    environment["TASKMAN_CONFIG_DIR"] = str(work / "config")
    environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    environment.pop("PYTHONPATH", None)
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        result = subprocess.run([str(executable), "--version"], cwd=work, env=environment,
                                capture_output=True, timeout=30, creationflags=flags)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpdateError("The updated executable could not start.") from exc
    if result.returncode or result.stdout.decode("utf-8", errors="replace").strip() != f"Taskman {version}":
        raise UpdateError("The updated executable did not report the expected version.")


def prepare_update(release: Release, *, executable=None, frozen=None, transport=None, progress=None) -> PreparedUpdate:
    support = installation_support(executable, frozen)
    if not support.supported or support.install_dir is None:
        raise UpdateError(support.reason)
    install = support.install_dir
    old = _manifest(install)
    if _version(release.version) <= _version(old["version"]):
        raise UpdateError("The selected release is not newer than this installation.")
    if release.asset_name != platform_asset(release.version) or release.download_url != f"https://github.com/{REPOSITORY}/releases/download/v{release.version}/{release.asset_name}":
        raise UpdateError("The selected download does not match this Taskman release and platform.")
    if release.checksum_url != f"https://github.com/{REPOSITORY}/releases/download/v{release.version}/SHA256SUMS.txt":
        raise UpdateError("The selected release checksum location is invalid.")
    updates = _no_links(install.parent / ".taskman-updates")
    updates.mkdir(exist_ok=True)
    stage = updates / uuid.uuid4().hex
    stage.mkdir()
    try:
        exe_name = Path(executable or sys.executable).name
        _probe(install / exe_name, old["version"], stage / "probe-current")
        if progress:
            progress("Downloading release checksums…")
        checksums = _fetch(release.checksum_url, 1024 * 1024, transport, progress)
        if hashlib.sha256(checksums).hexdigest() != release.checksum_sha256:
            raise UpdateError("The checksum file does not match GitHub's SHA-256 digest.")
        try:
            lines = checksums.decode("utf-8").splitlines()
        except UnicodeError as exc:
            raise UpdateError("The release checksum file is invalid.") from exc
        matches = [line.split() for line in lines if len(line.split()) == 2 and line.split()[1].lstrip("*") == release.asset_name]
        if len(matches) != 1 or matches[0][0] != release.sha256:
            raise UpdateError("The release checksum and GitHub's SHA-256 digest do not agree.")
        data = _fetch(release.download_url, MAX_ARCHIVE, transport, progress)
        if len(data) != release.asset_size or hashlib.sha256(data).hexdigest() != release.sha256:
            raise UpdateError("The downloaded archive failed its size or SHA-256 check.")
        if progress:
            progress("Verifying application files…")
        incoming = stage / "incoming"
        _extract(data, incoming)
        new = _manifest(incoming, release.version)
        _probe(incoming / exe_name, release.version, stage / "probe-before")
        _manifest(incoming, release.version)
        if _manifest(install) != old:
            raise UpdateError("The installed app changed while the update was downloading.")
        # The bootstrap stays outside the folder that Windows will move.
        bootstrap = stage / "bootstrap"
        shutil.copytree(incoming, bootstrap)
        if _manifest(bootstrap) != new:
            raise UpdateError("The update helper copy failed verification.")
        plan = {"schema": 1, "app_id": APP_ID, "install_dir": str(install), "exe_name": exe_name,
                "version": release.version, "old_manifest": old, "new_manifest": new,
                "manifest_sha256": _hash(incoming / MANIFEST_NAME), "original_pid": None,
                "relaunch_args": [], "state": "prepared"}
        path = stage / "plan.json"
        _atomic_json(path, plan)
        if progress:
            progress("Verified and ready to install.")
        return PreparedUpdate(path, bootstrap / exe_name, release.version)
    except BaseException:
        # Only this freshly created UUID directory is ours. Never clean up a
        # user application, old installation, or another pending update.
        if stage.parent == _no_links(updates) and re.fullmatch(r"[0-9a-f]{32}", stage.name):
            _no_links(stage)
            shutil.rmtree(stage)
        raise


def _read_plan(path: Path, *, helper=None) -> tuple[Path, Path, dict]:
    path = _no_links(path)
    stage = path.parent
    if path.name != "plan.json" or stage.parent.name != ".taskman-updates" or not re.fullmatch(r"[0-9a-f]{32}", stage.name):
        raise UpdateError("The update plan is outside its staging directory.")
    if path.stat().st_size > 4 * 1024 * 1024:
        raise UpdateError("The update plan is too large.")
    plan = _json(path.read_bytes())
    if plan.get("schema") != 1 or plan.get("app_id") != APP_ID or not isinstance(plan.get("install_dir"), str):
        raise UpdateError("The update plan is invalid.")
    install = _no_links(Path(plan["install_dir"]))
    if str(install) != plan["install_dir"] or install.parent != stage.parent.parent or install.name in (".taskman-updates", "", ".git"):
        raise UpdateError("The update plan does not target its original application directory.")
    name = "taskman.exe" if os.name == "nt" else "taskman"
    if plan.get("exe_name") != name or (helper is not None and _no_links(Path(helper)) != stage / "bootstrap" / name):
        raise UpdateError("The update helper is not running from its verified bootstrap directory.")
    if not isinstance(plan.get("version"), str) or not isinstance(plan.get("old_manifest"), dict) or not isinstance(plan.get("new_manifest"), dict):
        raise UpdateError("The update plan has incomplete version information.")
    if _version(plan["version"]) <= _version(plan["old_manifest"].get("version", "")):
        raise UpdateError("The update plan would downgrade this installation.")
    args = plan.get("relaunch_args")
    if not isinstance(args, list) or (args and (len(args) != 2 or args[0] != "--vault" or not isinstance(args[1], str) or not Path(args[1]).is_absolute())):
        raise UpdateError("The update plan contains unsupported launch arguments.")
    return stage, install, plan


def launch_update(prepared: PreparedUpdate, *, original_pid=None, relaunch_args=None):
    stage, install, plan = _read_plan(prepared.plan_path, helper=prepared.helper_executable)
    if plan.get("state") != "prepared" or plan["version"] != prepared.version:
        raise UpdateError("This update has already started or its version changed.")
    if _manifest(install) != plan["old_manifest"] or _manifest(stage / "incoming") != plan["new_manifest"] or _manifest(stage / "bootstrap") != plan["new_manifest"]:
        raise UpdateError("The application or staged update changed. Download the update again.")
    plan["original_pid"] = original_pid if original_pid is not None else os.getpid()
    if type(plan["original_pid"]) is not int or plan["original_pid"] <= 0:
        raise UpdateError("The update has an invalid process identifier.")
    args = relaunch_args or []
    if not isinstance(args, list) or (args and (len(args) != 2 or args[0] != "--vault" or not isinstance(args[1], str) or not Path(args[1]).is_absolute())):
        raise UpdateError("The update plan contains unsupported launch arguments.")
    plan["relaunch_args"] = args
    plan["state"] = "waiting"
    _atomic_json(prepared.plan_path, plan)
    _read_plan(prepared.plan_path, helper=prepared.helper_executable)
    flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS if os.name == "nt" else 0
    environment = os.environ.copy()
    environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    try:
        process = subprocess.Popen([str(prepared.helper_executable), "--apply-update", str(prepared.plan_path)],
                                   cwd=stage, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, creationflags=flags, env=environment, start_new_session=os.name != "nt")
    except OSError as exc:
        plan["state"] = "prepared"
        plan["original_pid"] = None
        _atomic_json(prepared.plan_path, plan)
        raise UpdateError("The update helper could not start. Taskman has stayed open.") from exc
    # Do not close the user's app until the helper has loaded and accepted the
    # plan. Failure cancels the plan; no helper is ever force-terminated.
    deadline = time.monotonic() + HELPER_READY_TIMEOUT
    try:
        ready = stage / "ready.json"
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise UpdateError("The update helper exited before it was ready. Taskman has stayed open.")
            _no_links(ready)
            if ready.is_file() and ready.stat().st_size <= 16384:
                value = _json(ready.read_bytes())
                if value == {"app_id": APP_ID, "pid": process.pid, "original_pid": plan["original_pid"], "version": prepared.version}:
                    return process
                raise UpdateError("The update helper returned an unexpected readiness response.")
            time.sleep(0.05)
        raise UpdateError("The update helper did not become ready in time. Taskman has stayed open.")
    except Exception:
        plan["state"] = "cancelled"
        _atomic_json(prepared.plan_path, plan)
        raise


def discard_update(prepared: PreparedUpdate) -> None:
    """Remove only an unlaunched, verified UUID stage owned by this update."""
    if not prepared.plan_path.exists():
        return
    stage, _, plan = _read_plan(prepared.plan_path, helper=prepared.helper_executable)
    if plan.get("state") != "prepared" or plan.get("original_pid") is not None:
        raise UpdateError("An update that has started cannot be discarded.")
    if plan["version"] != prepared.version or _manifest(stage / "incoming") != plan["new_manifest"] or _manifest(stage / "bootstrap") != plan["new_manifest"]:
        raise UpdateError("The pending update changed and was preserved for inspection.")
    _no_links(stage)
    for path in _walk(stage):
        _no_links(path)
    shutil.rmtree(stage)


def _wait_for_exit(pid: int, timeout: float = 120, *, cancelled=None) -> None:
    if type(pid) is not int or pid <= 0 or pid == os.getpid():
        raise UpdateError("The update plan has an invalid process identifier.")
    if os.name == "nt":
        import ctypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x00100000, False, pid)
        if not handle:
            if ctypes.get_last_error() == 87:
                return
            raise UpdateError("Could not check whether Taskman closed. No files were replaced.")
        try:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if cancelled and cancelled():
                    raise UpdateCancelled("The update was cancelled before Taskman closed.")
                status = kernel.WaitForSingleObject(handle, min(200, max(1, int((deadline - time.monotonic()) * 1000))))
                if status == 0:
                    return
                if status != 258:
                    raise UpdateError("Could not wait for Taskman to close.")
            raise UpdateError("Taskman did not close in time. No files were replaced.")
        finally:
            kernel.CloseHandle(handle)
        return
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cancelled and cancelled():
            raise UpdateCancelled("The update was cancelled before Taskman closed.")
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        except PermissionError as exc:
            raise UpdateError("Could not check whether Taskman closed.") from exc
        time.sleep(0.2)
    raise UpdateError("Taskman did not close in time. No files were replaced.")


def _receipt_path(install: Path) -> Path:
    key = hashlib.sha256(str(install).encode("utf-8")).hexdigest()[:16]
    return install.parent / ".taskman-updates" / f"last-{key}.json"


def _save_receipt(install: Path, result: dict) -> None:
    if result["status"] == "cancelled":
        return
    path = _no_links(_receipt_path(install))
    if path.is_file() and path.stat().st_size <= 16384:
        try:
            previous = _json(path.read_bytes())
            if previous.get("app_id") == APP_ID and previous.get("install_dir") == str(install):
                newer = _version(previous.get("version")) > _version(result["version"])
                same_success = previous.get("version") == result["version"] and previous.get("status") == "success" and result["status"] != "success"
                if newer or same_success:
                    return
        except (UpdateError, OSError):
            pass
    _atomic_json(path, result)


def read_update_result(executable=None, *, consume=False) -> dict | None:
    try:
        install = _no_links(Path(executable or sys.executable)).parent
        path = _no_links(_receipt_path(install))
        if not path.is_file() or path.stat().st_size > 16384:
            return None
        result = _json(path.read_bytes())
        if result.get("app_id") != APP_ID or result.get("install_dir") != str(install) or result.get("status") not in ("success", "failed") or result.get("shown") or not isinstance(result.get("message"), str):
            return None
        if consume:
            _atomic_json(path, {**result, "shown": True})
        return result
    except (OSError, UpdateError):
        return None


def apply_update(plan_path, *, helper_executable=None, wait=None, probe=None, relaunch=True) -> int:
    """Internal helper mode. Injectable wait/probe are for disposable tests."""
    stage, install, plan = _read_plan(Path(plan_path), helper=helper_executable or sys.executable)
    incoming, backup = stage / "incoming", stage / "previous-app"
    old_moved = new_moved = old_exited = False
    result = {"app_id": APP_ID, "install_dir": str(install), "version": plan["version"], "status": "failed", "shown": False}
    probe = probe or _probe
    try:
        if plan.get("state") == "cancelled":
            raise UpdateCancelled("The update was cancelled before Taskman closed.")
        if plan.get("state") != "waiting" or backup.exists():
            raise UpdateError("The update was already applied or its backup location is occupied.")
        _atomic_json(stage / "ready.json", {"app_id": APP_ID, "pid": os.getpid(), "original_pid": plan.get("original_pid"), "version": plan["version"]})
        if wait is None:
            def cancelled():
                _, _, latest = _read_plan(Path(plan_path), helper=helper_executable or sys.executable)
                return latest.get("state") == "cancelled"
            _wait_for_exit(plan.get("original_pid"), cancelled=cancelled)
        else:
            wait(plan.get("original_pid"))
        old_exited = True
        stage, install, current = _read_plan(Path(plan_path), helper=helper_executable or sys.executable)
        if current.get("state") == "cancelled":
            raise UpdateCancelled("The update was cancelled before Taskman closed.")
        if current != plan:
            raise UpdateError("The update plan changed while waiting for Taskman to close.")
        if _manifest(install) != plan["old_manifest"] or _manifest(incoming, plan["version"]) != plan["new_manifest"] or _manifest(stage / "bootstrap") != plan["new_manifest"]:
            raise UpdateError("The application or staged update changed. No files were replaced.")
        if _hash(incoming / MANIFEST_NAME) != plan.get("manifest_sha256"):
            raise UpdateError("The staged application manifest changed.")
        probe(incoming / plan["exe_name"], plan["version"], stage / "probe-apply")
        if _manifest(install) != plan["old_manifest"] or _manifest(incoming) != plan["new_manifest"]:
            raise UpdateError("The application changed before installation.")
        # All paths are siblings on the same filesystem. rename never merges
        # folders, and a retained full backup makes rollback reversible.
        _no_links(backup)
        install.rename(backup)
        old_moved = True
        if _manifest(backup) != plan["old_manifest"]:
            raise UpdateError("The old application changed during installation.")
        incoming.rename(install)
        new_moved = True
        if _manifest(install) != plan["new_manifest"]:
            raise UpdateError("The installed application failed verification.")
        probe(install / plan["exe_name"], plan["version"], stage / "probe-after")
        if _manifest(install) != plan["new_manifest"]:
            raise UpdateError("The installed application changed during its startup check.")
        result.update(status="success", message=f"Updated Taskman to {plan['version']}. Previous app saved in {backup}.", backup=str(backup))
    except Exception as exc:
        if isinstance(exc, UpdateCancelled):
            relaunch = False
            result["status"] = "cancelled"
        message = str(exc) or type(exc).__name__
        try:
            if old_moved:
                if new_moved:
                    # Preserve anything changed after the swap in its entirety.
                    _no_links(install)
                    install.rename(stage / "failed-app")
                if install.exists():
                    raise UpdateError("Another application appeared at the install location; it was preserved.")
                _no_links(backup)
                backup.rename(install)
                message += " The previous application was restored."
        except Exception as rollback:
            message += f" Automatic restore could not finish: {rollback}. Previous application: {backup}."
        result["message"] = f"Taskman update failed: {message}"
    plan["state"] = result["status"]
    # A full/read-only disk must not strand the user with both app processes
    # closed after a verified swap or rollback. Persist independently, then
    # always attempt to reopen the verified app.
    for path, value in ((Path(plan_path), plan), (stage / "update-result.json", result)):
        try:
            _atomic_json(path, value)
        except (OSError, UpdateError):
            result["message"] += f" Could not save update status to {path}."
    try:
        _save_receipt(install, result)
    except (OSError, UpdateError):
        result["message"] += " Could not save the update notification."
    safe_to_relaunch = False
    if relaunch and old_exited and install.is_dir():
        try:
            safe_to_relaunch = _manifest(install) in (plan["new_manifest"], plan["old_manifest"])
        except (OSError, UpdateError):
            pass
    if safe_to_relaunch:
        try:
            # This is the interactive app, intentionally a visible console.
            flags = subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0
            environment = os.environ.copy()
            environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
            launched = subprocess.Popen([str(install / plan["exe_name"]), *plan["relaunch_args"]], cwd=install.parent, creationflags=flags, env=environment)
            if type(getattr(launched, "pid", None)) is int:
                result["relaunch_pid"] = launched.pid
                try:
                    _atomic_json(stage / "update-result.json", result)
                except (OSError, UpdateError):
                    pass
        except OSError:
            result["message"] += " Open Taskman using your normal shortcut."
            try:
                _atomic_json(stage / "update-result.json", result)
            except (OSError, UpdateError):
                pass
            try:
                _save_receipt(install, result)
            except (OSError, UpdateError):
                pass
    return 0 if result["status"] == "success" else 1
