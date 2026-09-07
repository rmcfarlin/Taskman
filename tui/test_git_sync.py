"""Real Git integration against disposable local bare remotes only."""
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys

import pytest

from tui import git_sync as sync


pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="Git is not installed")


def git(root, *args):
    result = subprocess.run([shutil.which("git"), "-C", str(root), *args],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    return result.stdout.decode("utf-8", errors="replace").strip()


@pytest.fixture(autouse=True)
def isolated_git(monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")


def repo(tmp_path, *, remote=True, name="vault", remote_name="origin"):
    root = tmp_path / name
    root.mkdir()
    git(root, "init", "--initial-branch=main")
    git(root, "config", "user.name", "Taskman Test")
    git(root, "config", "user.email", "taskman@example.invalid")
    (root / "Task.md").write_text("- [ ] Original\n", encoding="utf-8")
    git(root, "add", "Task.md")
    git(root, "commit", "-m", "Initial")
    destination = None
    if remote:
        destination = bare(tmp_path, name + "-remote.git")
        git(root, "remote", "add", remote_name, str(destination))
    return root, destination


def bare(tmp_path, name):
    path = tmp_path / name
    path.mkdir()
    git(path, "init", "--bare", "--initial-branch=main")
    return path


def snapshot(root):
    return {str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob("*") if path.is_file()}


def test_push_commits_whole_vault_changes_attachments_deletions_and_respects_ignore(tmp_path):
    root, destination = repo(tmp_path)
    (root / "Delete.md").write_text("Remove me\n", encoding="utf-8")
    git(root, "add", "Delete.md")
    git(root, "commit", "-m", "Seed deletion")
    (root / "Delete.md").unlink()
    (root / "Task.md").write_text("- [x] Complete\n", encoding="utf-8")
    (root / "Reference.bin").write_bytes(bytes(range(256)))
    (root / ".gitignore").write_text("private.txt\n", encoding="utf-8")
    (root / "private.txt").write_text("Stay local", encoding="utf-8")
    result = sync.push_vault(root)
    assert result.committed and result.pushed
    head = git(root, "rev-parse", "HEAD")
    assert git(destination, "rev-parse", "refs/heads/main") == head
    files = git(destination, "ls-tree", "-r", "--name-only", head).splitlines()
    assert set(files) == {"Task.md", "Reference.bin", ".gitignore"}
    assert ".taskman/write.lock" not in files
    assert git(root, "diff", "--cached", "--name-only") == ""
    assert git(root, "diff", "--name-only") == ""


def test_no_change_retry_pushes_existing_commit_without_an_extra_commit(tmp_path):
    root, destination = repo(tmp_path)
    head = git(root, "rev-parse", "HEAD")
    first = sync.push_vault(root)
    second = sync.push_vault(root)
    assert first.pushed and second.pushed and not first.committed and not second.committed
    assert git(root, "rev-parse", "HEAD") == head
    assert git(destination, "rev-parse", "main") == head
    assert first.message == f"Pushed to Git remote (commit {head[:8]})."
    assert second.message == f"Git remote is up to date (commit {head[:8]})."


@pytest.mark.parametrize("destination,label", [
    ("https://user:secret@github.com/owner/private.git", "GitHub"),
    ("git@github.com:owner/private.git", "GitHub"),
    ("ssh://git@ssh.github.com:443/owner/private.git", "GitHub"),
    ("https://github.com.example.org/owner/private.git", "Git remote"),
    ("C:/vault/remote.git", "Git remote"),
])
def test_success_message_identifies_github_without_exposing_destination(destination, label):
    assert sync._success_message(destination, "1234567890abcdef", up_to_date=False) == (
        f"Pushed to {label} (commit 12345678).")
    assert sync._success_message(destination, "1234567890abcdef", up_to_date=True) == (
        f"{label} is up to date (commit 12345678).")


def test_same_size_racy_clean_edit_is_included_in_snapshot(tmp_path):
    root, destination = repo(tmp_path)
    git(root, "config", "core.trustctime", "false")
    task = root / "Task.md"
    original_stat = task.stat()
    index = root / ".git/index"
    os.utime(index, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    changed = task.read_bytes().replace(b"[ ]", b"[x]")
    assert len(changed) == task.stat().st_size
    task.write_bytes(changed)
    os.utime(task, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    result = sync.push_vault(root)
    assert result.committed and result.pushed
    assert git(destination, "show", "main:Task.md") == "- [x] Original"


def test_no_remote_is_local_only_and_changes_no_files_or_git_metadata(tmp_path):
    root, _ = repo(tmp_path, remote=False)
    (root / "Task.md").write_text("- [ ] Edited\n", encoding="utf-8")
    before = snapshot(root)
    result = sync.push_vault(root)
    assert not result.committed and not result.pushed and "locally" in result.message
    assert snapshot(root) == before
    assert not (root / ".taskman").exists()


def test_parent_repository_is_rejected_without_changes(tmp_path):
    root, _ = repo(tmp_path)
    child = root / "Notes"
    child.mkdir()
    (child / "Reference.md").write_text("Notes", encoding="utf-8")
    before = snapshot(root)
    with pytest.raises(sync.GitSyncError, match="parent folder"):
        sync.push_vault(child)
    assert snapshot(root) == before


def test_detached_head_is_rejected_without_changes(tmp_path):
    root, _ = repo(tmp_path)
    git(root, "checkout", "--detach")
    before = snapshot(root)
    with pytest.raises(sync.GitSyncError, match="detached"):
        sync.push_vault(root)
    assert snapshot(root) == before


def test_staged_changes_are_preserved_and_refused(tmp_path):
    root, _ = repo(tmp_path)
    (root / "Staged.txt").write_text("User staging\n", encoding="utf-8")
    git(root, "add", "Staged.txt")
    (root / "Task.md").write_text("Unstaged edit\n", encoding="utf-8")
    before = snapshot(root)
    with pytest.raises(sync.GitSyncError, match="staged changes"):
        sync.push_vault(root)
    assert snapshot(root) == before


@pytest.mark.parametrize("driver,field", [("local", "clean"), ("local", "process"), ("lfs", "clean")])
def test_applicable_filters_are_refused_without_execution_or_any_mutation(tmp_path, driver, field):
    root, _ = repo(tmp_path)
    script = root / "filter.py"
    script.write_text("from pathlib import Path\nPath('filter-ran').write_text('bad')\n", encoding="utf-8")
    command = f'"{sys.executable}" "{script}"'
    git(root, "config", f"filter.{driver}.{field}", command)
    (root / ".gitattributes").write_text(f"*.filtered filter={driver}\n", encoding="utf-8")
    (root / "Attachment.filtered").write_text("Reference\n", encoding="utf-8")
    before = snapshot(root)
    with pytest.raises(sync.GitSyncError, match="ordinary Git"):
        sync.push_vault(root)
    assert snapshot(root) == before
    assert not (root / "filter-ran").exists()


def test_unused_filter_configuration_does_not_block_normal_sync(tmp_path):
    root, _ = repo(tmp_path)
    git(root, "config", "filter.lfs.clean", "git-lfs clean -- %f")
    (root / ".gitattributes").write_text("*.filtered filter=lfs\n", encoding="utf-8")
    (root / "Task.md").write_text("Normal Markdown edit\n", encoding="utf-8")
    assert sync.push_vault(root).pushed


def test_unchanged_filtered_files_are_refused_without_execution(tmp_path):
    root, _ = repo(tmp_path)
    script = tmp_path / "identity-filter.py"
    marker = tmp_path / "filter-ran"
    script.write_text("from pathlib import Path\nimport sys\n"
                      f"Path({str(marker)!r}).write_text('ran')\n"
                      "sys.stdout.buffer.write(sys.stdin.buffer.read())\n", encoding="utf-8")
    git(root, "config", "filter.local.clean", f'"{sys.executable}" "{script}"')
    (root / ".gitattributes").write_text("*.filtered filter=local\n", encoding="utf-8")
    (root / "Kept.filtered").write_text("Unchanged attachment\n", encoding="utf-8")
    git(root, "add", ".gitattributes", "Kept.filtered")
    git(root, "commit", "-m", "Existing filtered attachment")
    marker.unlink(missing_ok=True)
    (root / "Task.md").write_text("Markdown edit\n", encoding="utf-8")
    before = snapshot(root)
    with pytest.raises(sync.GitSyncError, match="ordinary Git"):
        sync.push_vault(root)
    assert snapshot(root) == before
    assert not marker.exists()


def test_intent_to_add_is_preserved_and_refused(tmp_path):
    root, _ = repo(tmp_path)
    (root / "Intent.txt").write_text("Intent\n", encoding="utf-8")
    git(root, "add", "--intent-to-add", "Intent.txt")
    index = (root / ".git/index").read_bytes()
    with pytest.raises(sync.GitSyncError, match="staged changes"):
        sync.push_vault(root)
    assert (root / ".git/index").read_bytes() == index


@pytest.mark.parametrize("marker", ["MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply"])
def test_in_progress_git_work_is_refused(tmp_path, marker):
    root, _ = repo(tmp_path)
    path = root / ".git" / marker
    path.mkdir() if marker.startswith("rebase") else path.write_text(git(root, "rev-parse", "HEAD"))
    before = snapshot(root)
    with pytest.raises(sync.GitSyncError, match="Finish"):
        sync.push_vault(root)
    assert snapshot(root) == before


def test_tracked_writer_lock_is_not_untracked_or_committed(tmp_path):
    root, _ = repo(tmp_path)
    (root / ".taskman").mkdir()
    (root / ".taskman/write.lock").write_text("User content", encoding="utf-8")
    git(root, "add", ".taskman/write.lock")
    git(root, "commit", "-m", "Old tracked lock")
    before = snapshot(root)
    with pytest.raises(sync.GitSyncError, match="writer lock is tracked"):
        sync.push_vault(root)
    assert snapshot(root) == before


def test_sole_custom_remote_and_upstream_branch_are_used(tmp_path):
    root, destination = repo(tmp_path, remote_name="backup")
    git(root, "config", "branch.main.remote", "backup")
    git(root, "config", "branch.main.merge", "refs/heads/vault-notes")
    assert sync.push_vault(root).pushed
    assert git(destination, "rev-parse", "vault-notes") == git(root, "rev-parse", "HEAD")
    assert git(root, "rev-parse", "refs/remotes/backup/vault-notes") == git(root, "rev-parse", "HEAD")


@pytest.mark.parametrize("configuration", ["branch", "default", "upstream", "origin", "sole"])
def test_remote_preference_and_current_branch_fallback(tmp_path, configuration):
    root, origin = repo(tmp_path)
    backup = bare(tmp_path, "backup.git")
    git(root, "remote", "add", "backup", str(backup))
    git(root, "config", "branch.main.remote", "origin")
    git(root, "config", "branch.main.merge", "refs/heads/upstream-main")
    chosen, target = origin, "upstream-main"
    if configuration == "branch":
        git(root, "config", "branch.main.pushRemote", "backup")
        git(root, "config", "remote.pushDefault", "origin")
        chosen, target = backup, "main"
    elif configuration == "default":
        git(root, "config", "remote.pushDefault", "backup")
        chosen, target = backup, "main"
    elif configuration in {"origin", "sole"}:
        git(root, "config", "--unset", "branch.main.remote")
        git(root, "config", "--unset", "branch.main.merge")
        target = "main"
        if configuration == "sole":
            git(root, "remote", "remove", "origin")
            chosen = backup
    assert sync.push_vault(root).pushed
    assert git(chosen, "rev-parse", target) == git(root, "rev-parse", "HEAD")


def test_ambiguous_or_invalid_remote_configuration_never_guesses(tmp_path):
    root, _ = repo(tmp_path, remote_name="one")
    git(root, "remote", "add", "two", str(bare(tmp_path, "two.git")))
    before = snapshot(root)
    with pytest.raises(sync.GitSyncError, match="Several"):
        sync.push_vault(root)
    assert snapshot(root) == before
    git(root, "config", "branch.main.pushRemote", "missing")
    before = snapshot(root)
    with pytest.raises(sync.GitSyncError, match="unavailable"):
        sync.push_vault(root)
    assert snapshot(root) == before


@pytest.mark.parametrize("value", ["true", "yes", "on", "1"])
def test_mirror_remote_boolean_aliases_are_refused_before_mutation(tmp_path, value):
    root, _ = repo(tmp_path)
    git(root, "config", "remote.origin.mirror", value)
    before = snapshot(root)
    with pytest.raises(sync.GitSyncError, match="mirror"):
        sync.push_vault(root)
    assert snapshot(root) == before


def test_multiple_push_destinations_are_refused_before_mutation(tmp_path):
    root, destination = repo(tmp_path)
    git(root, "config", "--add", "remote.origin.pushurl", str(destination))
    git(root, "config", "--add", "remote.origin.pushurl", str(bare(tmp_path, "second.git")))
    before = snapshot(root)
    with pytest.raises(sync.GitSyncError, match="several push destinations"):
        sync.push_vault(root)
    assert snapshot(root) == before


def test_changed_destination_during_staging_aborts_with_original_index(tmp_path, monkeypatch):
    root, _ = repo(tmp_path)
    other = bare(tmp_path, "other.git")
    (root / "Task.md").write_text("Changed\n", encoding="utf-8")
    index = (root / ".git/index").read_bytes()
    head = git(root, "rev-parse", "HEAD")
    original_run = sync._Git.run

    def change_destination(self, *arguments, **kwargs):
        result = original_run(self, *arguments, **kwargs)
        if arguments[0] == "add":
            git(root, "remote", "set-url", "origin", str(other))
        return result

    monkeypatch.setattr(sync._Git, "run", change_destination)
    with pytest.raises(sync.GitSyncError, match="destination changed"):
        sync.push_vault(root)
    assert (root / ".git/index").read_bytes() == index
    assert git(root, "rev-parse", "HEAD") == head


def test_destination_change_at_push_cannot_redirect_captured_snapshot(tmp_path, monkeypatch):
    root, destination = repo(tmp_path)
    other = bare(tmp_path, "other.git")
    original_run = sync._Git.run

    def change_destination(self, *arguments, **kwargs):
        if arguments[0] == "push":
            git(root, "remote", "set-url", "origin", str(other))
        return original_run(self, *arguments, **kwargs)

    monkeypatch.setattr(sync._Git, "run", change_destination)
    assert sync.push_vault(root).pushed
    assert git(destination, "rev-parse", "main") == git(root, "rev-parse", "HEAD")
    assert git(other, "for-each-ref", "refs/heads") == ""


@pytest.mark.parametrize("source", ["environment", "configuration"])
def test_openssh_keeps_custom_key_and_proxy_and_forces_batch_first(tmp_path, monkeypatch, source):
    command = 'ssh -i "$HOME/.ssh/vault key" -o ProxyJump=jump -o BatchMode=no'
    client = sync._Git(tmp_path)
    monkeypatch.delenv("GIT_SSH_VARIANT", raising=False)
    monkeypatch.delenv("GIT_SSH_COMMAND", raising=False)
    values = {"core.sshCommand": command} if source == "configuration" else {}
    monkeypatch.setattr(client, "config", lambda key: values.get(key))
    if source == "environment":
        monkeypatch.setenv("GIT_SSH_COMMAND", command)
    client.configure_transport("git@example.invalid:vault.git")
    assert client.ssh_command == 'ssh -o BatchMode=yes -i "$HOME/.ssh/vault key" -o ProxyJump=jump -o BatchMode=no'
    assert client.ssh_variant == "ssh"


def test_plink_and_git_ssh_paths_are_preserved(tmp_path, monkeypatch):
    client = sync._Git(tmp_path)
    monkeypatch.delenv("GIT_SSH_COMMAND", raising=False)
    monkeypatch.delenv("GIT_SSH_VARIANT", raising=False)
    monkeypatch.setenv("GIT_SSH", "C:/Program Files/PuTTY/plink.exe")
    monkeypatch.setattr(client, "config", lambda key: None)
    client.configure_transport("ssh://git@example.invalid/vault.git")
    assert shlex.split(client.ssh_command) == ["C:/Program Files/PuTTY/plink.exe", "-batch"]
    assert client.ssh_variant == "plink"


def test_unrecognized_ssh_wrapper_is_refused_before_any_mutation(tmp_path, monkeypatch):
    root, _ = repo(tmp_path)
    git(root, "remote", "set-url", "origin", "ssh://git@example.invalid/vault.git")
    monkeypatch.setenv("GIT_SSH_COMMAND", "custom-ssh-wrapper --key private")
    monkeypatch.delenv("GIT_SSH_VARIANT", raising=False)
    before = snapshot(root)
    with pytest.raises(sync.GitSyncError, match="noninteractive"):
        sync.push_vault(root)
    assert snapshot(root) == before


def test_git_commands_have_no_stdin_or_credential_prompts(tmp_path, monkeypatch):
    client = sync._Git(tmp_path)
    captured = {}

    def capture(command, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", capture)
    client.run("status")
    assert captured["stdin"] == subprocess.DEVNULL
    env = captured["env"]
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GCM_INTERACTIVE"] == "never"
    assert env["GCM_GUI_PROMPT"] == "0"
    assert env["SSH_ASKPASS_REQUIRE"] == "never"


def test_ipv6_ssh_destination_is_valid_without_network_access(tmp_path, monkeypatch):
    root, _ = repo(tmp_path)
    git(root, "remote", "set-url", "origin", "ssh://git@[::1]/vault.git")
    monkeypatch.delenv("GIT_SSH_COMMAND", raising=False)
    monkeypatch.delenv("GIT_SSH_VARIANT", raising=False)
    assert sync._inspect(sync._Git(root)).destination == "ssh://git@[::1]/vault.git"


def test_writer_lock_is_excluded_case_insensitively(tmp_path):
    root, destination = repo(tmp_path)
    (root / ".Taskman").mkdir()
    (root / ".Taskman/Write.Lock").write_text("Lock", encoding="utf-8")
    (root / "Task.md").write_text("Updated\n", encoding="utf-8")
    assert sync.push_vault(root).pushed
    assert "write.lock" not in git(destination, "ls-tree", "-r", "--name-only", "main").lower()


def test_tracking_ref_changed_by_fetch_is_not_overwritten(tmp_path, monkeypatch):
    root, destination = repo(tmp_path)
    assert sync.push_vault(root).pushed
    other = git(root, "commit-tree", git(root, "rev-parse", "HEAD^{tree}"), "-m", "Different remote state")
    (root / "Task.md").write_text("Local edit\n", encoding="utf-8")
    original_run = sync._Git.run

    def fetched_during_push(self, *arguments, **kwargs):
        if arguments[0] == "push":
            git(root, "update-ref", "refs/remotes/origin/main", other)
        return original_run(self, *arguments, **kwargs)

    monkeypatch.setattr(sync._Git, "run", fetched_during_push)
    assert sync.push_vault(root).pushed
    assert git(root, "rev-parse", "refs/remotes/origin/main") == other
    assert git(destination, "rev-parse", "main") == git(root, "rev-parse", "HEAD")


def test_tracking_mappings_never_change_local_branches(tmp_path):
    root, _ = repo(tmp_path)
    git(root, "config", "remote.origin.fetch", "+refs/heads/*:refs/heads/mapped/*")
    assert sync.push_vault(root).pushed
    assert git(root, "for-each-ref", "--format=%(refname)", "refs/heads") == "refs/heads/main"


@pytest.mark.parametrize("concurrent", [False, True])
def test_symbolic_tracking_ref_never_updates_its_local_branch_target(tmp_path, monkeypatch, concurrent):
    root, _ = repo(tmp_path)
    assert sync.push_vault(root).pushed
    original = git(root, "rev-parse", "HEAD")
    git(root, "branch", "protected", original)
    reference = "refs/remotes/origin/main"
    if not concurrent:
        git(root, "symbolic-ref", reference, "refs/heads/protected")
    (root / "Task.md").write_text("New snapshot\n", encoding="utf-8")
    original_run = sync._Git.run

    def replace_tracking(self, *arguments, **kwargs):
        if concurrent and arguments[0] == "push":
            git(root, "symbolic-ref", reference, "refs/heads/protected")
        return original_run(self, *arguments, **kwargs)

    monkeypatch.setattr(sync._Git, "run", replace_tracking)
    assert sync.push_vault(root).pushed
    assert git(root, "rev-parse", "refs/heads/protected") == original
    assert git(root, "symbolic-ref", reference) == "refs/heads/protected"


def test_non_fast_forward_push_keeps_both_histories(tmp_path):
    root, destination = repo(tmp_path)
    assert sync.push_vault(root).pushed
    git(tmp_path, "clone", str(destination), "other-clone")
    other = tmp_path / "other-clone"
    git(other, "config", "user.name", "Other writer")
    git(other, "config", "user.email", "other@example.invalid")
    (other / "Task.md").write_text("Remote edit\n", encoding="utf-8")
    git(other, "add", "Task.md")
    git(other, "commit", "-m", "Remote edit")
    git(other, "push", "origin", "main")
    remote_head = git(destination, "rev-parse", "main")
    (root / "Task.md").write_text("Local edit\n", encoding="utf-8")
    with pytest.raises(sync.GitSyncError, match="Local files and commits are saved"):
        sync.push_vault(root)
    assert git(destination, "rev-parse", "main") == remote_head
    assert git(root, "rev-parse", "HEAD") != remote_head
    assert (root / "Task.md").read_text() == "Local edit\n"
    assert git(root, "diff", "--cached", "--name-only") == ""


def test_failed_push_keeps_commit_and_index_ready_for_retry(tmp_path):
    root, destination = repo(tmp_path)
    original_head = git(root, "rev-parse", "HEAD")
    unavailable = tmp_path / "missing-remote.git"
    git(root, "remote", "set-url", "origin", str(unavailable))
    (root / "Task.md").write_text("Saved edit\n", encoding="utf-8")
    with pytest.raises(sync.GitSyncError, match="Local files and commits are saved"):
        sync.push_vault(root)
    committed = git(root, "rev-parse", "HEAD")
    assert committed != original_head
    assert git(root, "diff", "--cached", "--name-only") == ""
    assert git(root, "diff", "--name-only") == ""
    git(root, "remote", "set-url", "origin", str(destination))
    result = sync.push_vault(root)
    assert result.pushed and not result.committed
    assert git(destination, "rev-parse", "main") == committed


def test_failed_commit_preserves_original_index_and_unstaged_files(tmp_path):
    root, _ = repo(tmp_path)
    git(root, "config", "--unset", "user.name")
    git(root, "config", "--unset", "user.email")
    git(root, "config", "user.useConfigOnly", "true")
    (root / "Task.md").write_text("Unsaved to Git\n", encoding="utf-8")
    index = (root / ".git/index").read_bytes()
    head = git(root, "rev-parse", "HEAD")
    with pytest.raises(sync.GitSyncError, match="user.name"):
        sync.push_vault(root)
    assert (root / ".git/index").read_bytes() == index
    assert git(root, "rev-parse", "HEAD") == head
    assert (root / "Task.md").read_text() == "Unsaved to Git\n"
    assert not (root / ".git/index.lock").exists()
    assert not list((root / ".git").glob(".taskman-*-index-*"))


def test_failed_branch_compare_and_swap_restores_index(tmp_path, monkeypatch):
    root, _ = repo(tmp_path)
    (root / "Task.md").write_text("Local edit\n", encoding="utf-8")
    index = (root / ".git/index").read_bytes()
    head = git(root, "rev-parse", "HEAD")
    original_run = sync._Git.run

    def fail_update(self, *arguments, **kwargs):
        if arguments[0] == "update-ref":
            return subprocess.CompletedProcess(arguments, 1, b"", b"")
        return original_run(self, *arguments, **kwargs)

    monkeypatch.setattr(sync._Git, "run", fail_update)
    with pytest.raises(sync.GitSyncError, match="branch changed"):
        sync.push_vault(root)
    assert (root / ".git/index").read_bytes() == index
    assert git(root, "rev-parse", "HEAD") == head
    assert not (root / ".git/index.lock").exists()


def test_hooks_vault_scripts_and_tags_are_not_run_or_pushed(tmp_path):
    root, destination = repo(tmp_path)
    for hook in ("pre-commit", "commit-msg", "post-commit", "pre-push"):
        path = root / ".git/hooks" / hook
        path.write_text("#!/bin/sh\nprintf bad > hook-ran\nexit 1\n", encoding="utf-8")
        path.chmod(0o755)
    (root / "push.ps1").write_text("throw 'Do not run vault scripts'\n", encoding="utf-8")
    (root / "Task.md").write_text("Edit\n", encoding="utf-8")
    git(root, "tag", "-a", "private-tag", "-m", "Private annotated tag")
    git(root, "config", "push.followTags", "true")
    assert sync.push_vault(root).pushed
    assert not (root / "hook-ran").exists()
    assert git(destination, "tag", "--list") == ""


def test_push_uses_captured_commit_and_releases_writer_lock(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from tui.taskman import add_task

    root, destination = repo(tmp_path)
    original_run = sync._Git.run
    captured = []

    def edit_during_push(self, *arguments, **kwargs):
        if arguments[0] == "push":
            captured.append(arguments[-1].split(":", 1)[0])
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(add_task, root, "Edit while pushing").result(timeout=5)
        return original_run(self, *arguments, **kwargs)

    monkeypatch.setattr(sync._Git, "run", edit_during_push)
    result = sync.push_vault(root)
    assert result.pushed
    assert git(destination, "rev-parse", "main") == captured[0]
    assert (root / "Tasks/Inbox.md").exists()
    assert "Tasks/Inbox.md" not in git(destination, "ls-tree", "-r", "--name-only", "main")
