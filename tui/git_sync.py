"""Explicit commit and push of an existing vault Git repository; stdlib only.

No repository or remote is created. Git hooks and interactive credential prompts
are disabled. A private index and Git's index lock protect existing staging from
failures, while an expected-old-commit check protects the current branch.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
from urllib.parse import urlsplit

from .taskman import vault_write_lock
from .vaults import normalize_folder


class GitSyncError(ValueError):
    """A concise user-facing failure without remote URLs or credential output."""


@dataclass(frozen=True, slots=True)
class SyncResult:
    message: str
    committed: bool = False
    pushed: bool = False


@dataclass(frozen=True, slots=True)
class _Repository:
    branch: str
    remote: str
    target: str
    head: str
    destination: str


class _Git:
    def __init__(self, root: Path):
        self.root = root
        executable = shutil.which("git")
        if not executable:
            raise GitSyncError("Git is not installed. Install Git to push this vault.")
        self.executable = str(Path(executable).resolve())
        self.ssh_command: str | None = None
        self.ssh_variant: str | None = None

    def configure_transport(self, destination: str) -> None:
        """Keep configured SSH keys/proxies while suppressing interactive prompts."""
        colon = destination.find(":")
        slash = destination.find("/")
        scp = colon > 0 and (slash < 0 or colon < slash) and not re.match(r"^[A-Za-z]:[\\/]", destination)
        if not (destination.startswith("ssh://") or (scp and "://" not in destination)):
            return
        command = os.environ.get("GIT_SSH_COMMAND")
        if command is None:
            command = self.config("core.sshCommand")
        if command is None:
            command = shlex.quote(os.environ["GIT_SSH"]) if os.environ.get("GIT_SSH") else "ssh"
        try:
            executable = shlex.split(command)[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
        except (ValueError, IndexError) as error:
            raise GitSyncError("The configured SSH command is invalid. Correct it before pushing.") from error
        variant = os.environ.get("GIT_SSH_VARIANT") or self.config("ssh.variant")
        if variant in (None, "auto"):
            variant = {"ssh": "ssh", "ssh.exe": "ssh", "plink": "plink", "plink.exe": "plink",
                       "tortoiseplink": "tortoiseplink", "tortoiseplink.exe": "tortoiseplink"}.get(executable)
        if variant not in ("ssh", "plink", "tortoiseplink"):
            raise GitSyncError("This SSH transport cannot be made noninteractive. Configure OpenSSH or Plink before pushing from Taskman.")
        # Insert before existing options: OpenSSH uses the first BatchMode
        # value. Preserve the rest of the configured shell command verbatim.
        first = re.match(r'''^\s*(?:'[^']*'|"(?:\\.|[^"])*"|\\.|[^\s'"])+''', command)
        if first is None:
            raise GitSyncError("The configured SSH command is invalid. Correct it before pushing.")
        flag = " -o BatchMode=yes" if variant == "ssh" else " -batch"
        self.ssh_command = command[:first.end()] + flag + command[first.end():]
        self.ssh_variant = variant

    def run(self, *arguments: str, input: bytes | None = None,
            index: Path | None = None, timeout: int = 30) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
                    "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
                    "GIT_NAMESPACE", "GIT_PREFIX"):
            env.pop(key, None)
        for key in list(env):
            if key.startswith("GIT_TRACE") or key == "GIT_CURL_VERBOSE":
                env.pop(key)
        env.update(GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="", GCM_INTERACTIVE="never",
                   GCM_GUI_PROMPT="0", SSH_ASKPASS="", SSH_ASKPASS_REQUIRE="never",
                   GIT_OPTIONAL_LOCKS="0")
        if self.ssh_command is not None:
            env["GIT_SSH_COMMAND"] = self.ssh_command
            env["GIT_SSH_VARIANT"] = self.ssh_variant
        if index is not None:
            env["GIT_INDEX_FILE"] = str(index)
        command = [self.executable, "-c", "core.hooksPath=" + os.devnull,
                   "-c", "core.fsmonitor=false", "-c", "core.askPass=",
                   "-c", "commit.gpgSign=false", "-c", "push.followTags=false",
                   "-C", str(self.root), *arguments]
        try:
            return subprocess.run(command, input=input, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, env=env, timeout=timeout,
                                  **({"stdin": subprocess.DEVNULL} if input is None else {}),
                                  creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0))
        except subprocess.TimeoutExpired as error:
            raise GitSyncError("Git timed out. Local files and commits are saved; retry when Git is available.") from error
        except OSError as error:
            raise GitSyncError("Could not start Git. Check your Git installation and retry.") from error

    def text(self, *arguments: str, error: str) -> str:
        result = self.run(*arguments)
        if result.returncode:
            raise GitSyncError(error)
        return result.stdout.decode("utf-8", errors="replace").strip()

    def config(self, key: str) -> str | None:
        result = self.run("config", "--get", key)
        if result.returncode == 1:
            return None
        if result.returncode:
            raise GitSyncError("Could not read the vault's Git configuration.")
        return result.stdout.decode("utf-8", errors="replace").strip()

    def path(self, name: str) -> Path:
        value = self.text("rev-parse", "--git-path", name,
                          error="Could not locate the vault's Git metadata.")
        path = Path(value)
        return path if path.is_absolute() else self.root / path


def _inspect(git: _Git) -> _Repository | None:
    top = git.text("rev-parse", "--show-toplevel",
                   error="This vault is not a Git repository. Configure Git and a remote before pushing.")
    if os.path.normcase(str(Path(top).resolve())) != os.path.normcase(str(git.root)):
        raise GitSyncError("The Git repository belongs to a parent folder. Open its root as the vault before pushing.")
    branch = git.text("symbolic-ref", "--quiet", "HEAD",
                      error="This vault has a detached HEAD. Switch to a branch before pushing.")
    if not branch.startswith("refs/heads/"):
        raise GitSyncError("Select a local Git branch before pushing this vault.")
    name = branch[len("refs/heads/"):]
    remotes = git.text("remote", error="Could not read the vault's Git remotes.").splitlines()
    if not remotes:
        return None
    upstream_remote = git.config(f"branch.{name}.remote")
    remote = next((value for value in (git.config(f"branch.{name}.pushRemote"),
                                       git.config("remote.pushDefault"), upstream_remote)
                   if value is not None), None)
    if remote is None:
        remote = "origin" if "origin" in remotes else remotes[0] if len(remotes) == 1 else None
    if remote is None:
        raise GitSyncError("Several Git remotes are configured. Set a push remote for this branch first.")
    if not remote or remote.startswith("-") or remote not in remotes:
        raise GitSyncError("The configured push remote is unavailable. Correct the Git remote configuration first.")
    urls = git.run("remote", "get-url", "--push", "--all", remote)
    if urls.returncode or not urls.stdout.strip():
        raise GitSyncError("The configured push remote has no usable destination.")
    destinations = urls.stdout.decode("utf-8", errors="replace").splitlines()
    if len(destinations) != 1:
        raise GitSyncError("This remote has several push destinations. Configure one destination before pushing from Taskman.")
    destination = destinations[0]
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*::", destination):
        raise GitSyncError("This remote uses a custom Git transport. Configure a standard Git push destination first.")
    git.configure_transport(destination)
    mirror = git.run("config", "--bool", "--get", f"remote.{remote}.mirror")
    if mirror.returncode not in (0, 1):
        raise GitSyncError("The Git remote has an invalid mirror setting. Correct it before pushing.")
    if mirror.stdout.strip() == b"true":
        raise GitSyncError("This remote is configured as a mirror. Use a regular branch remote for vault pushes.")
    target = branch
    if remote == upstream_remote:
        merges = git.run("config", "--get-all", f"branch.{name}.merge")
        if merges.returncode not in (0, 1):
            raise GitSyncError("Could not read the branch's upstream configuration.")
        values = merges.stdout.decode("utf-8", errors="replace").splitlines()
        if len(values) > 1:
            raise GitSyncError("This branch has several upstream targets. Configure one branch before pushing.")
        if values:
            target = values[0]
    if not target.startswith("refs/heads/") or git.run("check-ref-format", target).returncode:
        raise GitSyncError("The configured upstream is not a valid branch.")
    head_result = git.run("rev-parse", "--verify", "HEAD")
    head = head_result.stdout.decode("ascii", errors="replace").strip() if head_result.returncode == 0 else ""
    return _Repository(branch, remote, target, head, destination)


def _check_ready(git: _Git) -> None:
    for name in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply"):
        if git.path(name).exists():
            raise GitSyncError("Finish the current Git merge, rebase, or cherry-pick before pushing.")
    tracked_lock = git.run("ls-files", "--error-unmatch", "--", ":(icase,literal).taskman/write.lock")
    if tracked_lock.returncode == 0:
        raise GitSyncError("The Taskman writer lock is tracked by Git. Remove .taskman/write.lock from tracking before pushing.")
    if tracked_lock.returncode != 1:
        raise GitSyncError("Could not inspect the vault's Git index.")
    staged = git.run("diff", "--cached", "--quiet", "--no-ext-diff", "--no-textconv",
                     "--ita-visible-in-index", "--exit-code")
    if staged.returncode == 1:
        raise GitSyncError("Git has staged changes. Commit or unstage them before using Push vault.")
    if staged.returncode:
        raise GitSyncError("Could not inspect staged changes. Resolve Git conflicts before pushing.")
    unmerged = git.run("ls-files", "--unmerged", "-z")
    if unmerged.returncode or unmerged.stdout:
        raise GitSyncError("Resolve Git conflicts before pushing this vault.")
    _check_filters(git)


def _check_filters(git: _Git) -> None:
    """Refuse applicable external filters without executing them to find changes.

    In particular, suppressing pre-push hooks means Git LFS uploads would be
    incomplete. Ordinary Git remains the correct tool for those repositories.
    """
    files = git.run("ls-files", "--cached", "--others", "--exclude-standard", "-z")
    if files.returncode:
        raise GitSyncError("Could not inspect files for Git sync.")
    attributes = git.run("check-attr", "--stdin", "-z", "filter", input=files.stdout)
    if attributes.returncode:
        raise GitSyncError("Could not inspect the vault's Git attributes.")
    values = attributes.stdout.split(b"\0")
    checked = {}
    for position in range(0, len(values) - 2, 3):
        path, _attribute, driver = values[position:position + 3]
        if path.lower() == b".taskman/write.lock":
            continue
        if driver in (b"unspecified", b"unset", b"set"):
            continue
        name = driver.decode("utf-8", errors="replace")
        if name not in checked:
            checked[name] = bool(git.config(f"filter.{name}.clean") or git.config(f"filter.{name}.process"))
        if checked[name]:
            # Even an unchanged file may invoke its filter during Git's racy
            # stat checks. Keep filtered repositories on the normal Git path.
            raise GitSyncError("This vault uses Git filters or Git LFS. Use ordinary Git to commit and push it.")


def _stage_and_commit(git: _Git, expected: _Repository) -> tuple[str, bool]:
    """Keep the real index untouched until staging succeeds; CAS the branch."""
    index = git.path("index")
    lock_path = Path(str(index) + ".lock")
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise GitSyncError("Git is busy or its index is locked. Finish the other Git operation and retry.") from error
    os.close(descriptor)
    lock_stat = lock_path.stat()
    temporary = None
    original = None
    original_stat = None
    installed = False
    committed = False
    try:
        current = _inspect(git)
        if current != expected:
            raise GitSyncError("The Git branch or destination changed. Retry Push vault.")
        _check_ready(git)
        original = index.read_bytes() if index.exists() else None
        original_stat = index.stat() if original is not None else None
        fd, filename = tempfile.mkstemp(prefix=".taskman-sync-index-", dir=index.parent)
        os.close(fd)
        temporary = Path(filename)
        if original is None:
            temporary.unlink()
        else:
            temporary.write_bytes(original)
            # Git compares entry timestamps against the index file's timestamp
            # to detect racy-clean, same-size edits. A freshly dated copy would
            # incorrectly make those entries appear safe to skip.
            os.utime(temporary, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        added = git.run("add", "--all", "--", ".", ":(exclude,icase,literal).taskman/write.lock", index=temporary)
        if added.returncode:
            raise GitSyncError("Git could not stage the vault. Files and the original Git index are unchanged.")
        tree_result = git.run("write-tree", index=temporary)
        if tree_result.returncode:
            raise GitSyncError("Git could not prepare the vault snapshot. Resolve Git conflicts and retry.")
        tree = tree_result.stdout.decode("ascii").strip()
        if expected.head:
            old_tree = git.text("rev-parse", expected.head + "^{tree}",
                                error="The previous Git commit is unavailable.")
            if tree == old_tree:
                return expected.head, False
        else:
            entries = git.run("ls-files", "-z", index=temporary)
            if not entries.stdout:
                raise GitSyncError("There are no committed files to push yet. Add vault files and retry.")
        arguments = ["commit-tree", tree]
        if expected.head:
            arguments += ["-p", expected.head]
        arguments += ["-m", "Save vault changes from Taskman"]
        result = git.run(*arguments, index=temporary)
        if result.returncode:
            raise GitSyncError("Git could not create a commit. Check Git user.name and user.email, then retry; files and staging are unchanged.")
        head = result.stdout.decode("ascii").strip()
        if _inspect(git) != expected:
            raise GitSyncError("The Git branch or destination changed while preparing the snapshot. Retry Push vault.")
        # Hold index.lock throughout installation and branch update. Other Git
        # writers cannot stage over it, and update-ref rejects a moved branch.
        os.replace(temporary, index)
        temporary = None
        installed = True
        update = git.run("update-ref", "-m", "Taskman: save vault changes", expected.branch,
                         head, expected.head or "0" * len(head))
        if update.returncode:
            raise GitSyncError("The Git branch changed before the commit was saved. Retry Push vault.")
        committed = True
        return head, True
    finally:
        if installed and not committed:
            if original is None:
                index.unlink(missing_ok=True)
            else:
                fd, name = tempfile.mkstemp(prefix=".taskman-restore-index-", dir=index.parent)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(original)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.utime(name, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
                os.replace(name, index)
        if temporary is not None:
            temporary.unlink(missing_ok=True)
            Path(str(temporary) + ".lock").unlink(missing_ok=True)
        if lock_path.exists() and os.path.samestat(lock_stat, lock_path.stat()):
            lock_path.unlink()


def _match_ref(pattern: str, reference: str) -> str | None:
    if "*" not in pattern:
        return "" if pattern == reference else None
    if pattern.count("*") != 1:
        return None
    prefix, suffix = pattern.split("*")
    if reference.startswith(prefix) and reference.endswith(suffix) and len(reference) >= len(prefix) + len(suffix):
        return reference[len(prefix):len(reference) - len(suffix) if suffix else None]
    return None


def _tracking_refs(git: _Git, repository: _Repository) -> list[tuple[str, str]]:
    """Snapshot configured remote-tracking refs, never local branch mappings."""
    result = git.run("config", "--get-all", f"remote.{repository.remote}.fetch")
    if result.returncode:
        return []
    specs = result.stdout.decode("utf-8", errors="replace").splitlines()
    if any(spec.startswith("^") and _match_ref(spec[1:], repository.target) is not None for spec in specs):
        return []
    references = set()
    for spec in specs:
        source, separator, destination = spec.lstrip("+").partition(":")
        matched = _match_ref(source, repository.target)
        if not separator or matched is None or not destination.startswith("refs/remotes/"):
            continue
        reference = destination.replace("*", matched)
        if git.run("check-ref-format", reference).returncode == 0:
            references.add(reference)
    snapshots = []
    for reference in sorted(references):
        if git.run("symbolic-ref", "--quiet", reference).returncode == 0:
            continue
        old = git.run("rev-parse", "--verify", reference)
        snapshots.append((reference, old.stdout.decode("ascii", errors="replace").strip()
                          if old.returncode == 0 else ""))
    return snapshots


def _remember_push(git: _Git, references: list[tuple[str, str]], head: str) -> None:
    for reference, old in references:
        try:
            # A fetch that changed this ref during the push wins. Tracking
            # housekeeping must never turn a successful push into a failure.
            if git.run("symbolic-ref", "--quiet", reference).returncode == 0:
                continue
            git.run("update-ref", "--no-deref", "-m", "Taskman: pushed vault snapshot", reference,
                    head, old or "0" * len(head))
        except GitSyncError:
            pass


def _success_message(destination: str, head: str, *, up_to_date: bool) -> str:
    """Name GitHub when recognized, without exposing a URL or credentials."""
    if "://" in destination:
        try:
            host = urlsplit(destination).hostname
        except ValueError:
            host = None
    else:
        match = re.match(r"^(?:[^/@:]+@)?([^/:]+):[^\\]", destination)
        host = match.group(1) if match else None
    label = "GitHub" if host and host.lower() in {"github.com", "ssh.github.com"} else "Git remote"
    action = f"{label} is up to date" if up_to_date else f"Pushed to {label}"
    return f"{action} (commit {head[:8]})."


def push_vault(root: "str | Path") -> SyncResult:
    """Commit the vault snapshot and push only its selected existing branch remote."""
    try:
        root = normalize_folder(root)
        git = _Git(root)
        repository = _inspect(git)
        if repository is None:
            return SyncResult("No Git remote is configured. Your changes are saved locally.")
        _check_ready(git)
        with vault_write_lock(root):
            head, committed = _stage_and_commit(git, repository)
        current = _inspect(git)
        if current is None or (current.branch, current.remote, current.target, current.destination) != (
                repository.branch, repository.remote, repository.target, repository.destination):
            raise GitSyncError("The Git branch or push destination changed. Local commits are saved; retry Push vault.")
        tracking = _tracking_refs(git, repository)
        # Network work must not hold the application writer lock. Later edits
        # stay local for the next push; this refspec names exactly our snapshot.
        pushed = git.run("push", "--porcelain", "--no-force", "--no-follow-tags", "--no-verify",
                         "--recurse-submodules=no", "--", repository.destination,
                         f"{head}:{repository.target}", timeout=120)
        if pushed.returncode:
            raise GitSyncError("Could not push the vault. Local files and commits are saved; check authentication or remote branch changes, then retry.")
        _remember_push(git, tracking, head)
        up_to_date = any(line.startswith(b"=\t") for line in pushed.stdout.splitlines())
        return SyncResult(_success_message(repository.destination, head, up_to_date=up_to_date),
                          committed, True)
    except GitSyncError:
        raise
    except (OSError, ValueError) as error:
        raise GitSyncError("Git sync could not finish. Local files are saved; check Git and folder permissions, then retry.") from error
