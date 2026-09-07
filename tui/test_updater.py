"""Updater boundaries, authentic release selection, and disposable swap recovery."""
from dataclasses import replace
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import zipfile

import pytest

from tui import updater as update


def digest(data):
    return hashlib.sha256(data).hexdigest()


def release_payload(version="3.2.0", system=None, machine=None):
    name = update.platform_asset(version, system, machine)
    base = f"https://github.com/{update.REPOSITORY}/releases/download/v{version}"
    return {"tag_name": f"v{version}", "draft": False, "prerelease": False,
            "html_url": f"{update.RELEASES_URL}/tag/v{version}", "body": "Useful improvements",
            "assets": [{"name": filename, "browser_download_url": f"{base}/{filename}", "state": "uploaded",
                        "size": 12, "digest": "sha256:" + "a" * 64} for filename in (name, "SHA256SUMS.txt")]}


def check(payload, current="3.1.0", **kwargs):
    return update.check_for_update(current, transport=lambda *args: json.dumps(payload).encode(), **kwargs)


@pytest.mark.parametrize("current,available", [("3.1.0", True), ("3.2.0.dev0", True), ("3.2.0rc1", True), ("3.2.0", False), ("3.3.0.dev0", False), ("4.0.0", False)])
def test_versions_never_downgrade_or_offer_equal_release(current, available):
    assert bool(check(release_payload(), current)) is available


@pytest.mark.parametrize("system,machine,suffix", [("Windows", "AMD64", "windows-x64"), ("Linux", "x86_64", "linux-x64"), ("Darwin", "arm64", "macos-arm64")])
def test_exact_platform_asset(system, machine, suffix):
    release = check(release_payload(system=system, machine=machine), system=system, machine=machine)
    assert release.asset_name == f"taskman-3.2.0-{suffix}.zip"
    assert release.body == "Useful improvements"


@pytest.mark.parametrize("mutation", [
    lambda data: data.update(draft=True),
    lambda data: data.update(prerelease=True),
    lambda data: data.update(tag_name="v3.2.0rc1"),
    lambda data: data.update(html_url="https://github.com/other/repo/releases/tag/v3.2.0"),
    lambda data: data["assets"][0].update(browser_download_url="https://evil.example/update.zip"),
    lambda data: data["assets"][0].update(digest=None),
    lambda data: data["assets"][0].update(size=update.MAX_ARCHIVE + 1),
    lambda data: data["assets"][0].update(state="new"),
    lambda data: data["assets"].append(data["assets"][0].copy()),
    lambda data: data["assets"].pop(0),
])
def test_invalid_release_metadata_refused(mutation):
    payload = release_payload()
    mutation(payload)
    with pytest.raises(update.UpdateError):
        check(payload)


@pytest.mark.parametrize("url", ["http://github.com/a", "https://github.com.evil.example/a", "https://github.com@evil.example/a", "https://user@github.com/a", "https://github.com:444/a", "https://github.com:bad/a", "file:///C:/example", "https://raw.githubusercontent.com/a", "https://github.com/a#fragment"])
def test_untrusted_download_and_redirect_hosts_refused(url):
    with pytest.raises(update.UpdateError):
        update._safe_url(url)
    with pytest.raises(update.UpdateError):
        update._Redirects().redirect_request(None, None, 302, "", {}, url)


def test_offline_and_rate_limit_errors_are_actionable(monkeypatch):
    class Offline:
        def open(self, *args, **kwargs):
            raise update.URLError("offline")
    monkeypatch.setattr(update, "build_opener", lambda *args: Offline())
    with pytest.raises(update.UpdateError, match="connection"):
        update.check_for_update()


def test_network_reads_are_bounded_and_final_host_checked(monkeypatch):
    class Response:
        headers = {}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def geturl(self): return update.API_URL
        def read(self, size): return b"x" * size
    class Opener:
        def open(self, request, timeout):
            assert timeout == 20
            assert request.get_header("User-agent").startswith("Taskman/")
            return Response()
    monkeypatch.setattr(update, "build_opener", lambda *args: Opener())
    with pytest.raises(update.UpdateError, match="size limit"):
        update._request(update.API_URL, 10)


def bundle(root, version, content=b"native fixture"):
    root.mkdir()
    exe_name = "taskman.exe" if os.name == "nt" else "taskman"
    (root / exe_name).write_bytes(content)
    (root / "LICENSE").write_text("MIT", encoding="utf-8")
    (root / "RELEASE_NOTES.md").write_text(version, encoding="utf-8")
    (root / "_internal").mkdir()
    (root / "_internal" / "runtime.bin").write_bytes(b"runtime")
    update.write_install_manifest(root, version)
    return root / exe_name


def archive_for(root):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in root.rglob("*"):
            if path.is_file():
                archive.write(path, "taskman/" + path.relative_to(root).as_posix())
    return output.getvalue()


@pytest.fixture
def installation(tmp_path, monkeypatch):
    # Runtime executable naming follows the actual OS; only support policy is
    # patched to exercise file replacement tests on all CI platforms.
    monkeypatch.setattr(update.platform, "system", lambda: "Windows")
    old_exe = bundle(tmp_path / "app", "3.1.0")
    new_exe = bundle(tmp_path / "build", "3.2.0", b"new native fixture")
    payload = release_payload()
    data = archive_for(new_exe.parent)
    checksums = f"{digest(data)}  {payload['assets'][0]['name']}\n".encode()
    payload["assets"][0].update(size=len(data), digest="sha256:" + digest(data))
    payload["assets"][1].update(size=len(checksums), digest="sha256:" + digest(checksums))
    release = check(payload)
    transport = lambda url, limit, progress=None: checksums if url.endswith("SHA256SUMS.txt") else data
    monkeypatch.setattr(update, "_probe", lambda *args: None)
    return old_exe, release, transport


def prepare(installation, **kwargs):
    exe, release, transport = installation
    return update.prepare_update(release, executable=exe, frozen=True, transport=transport, **kwargs)


def wait_plan(prepared):
    plan = json.loads(prepared.plan_path.read_bytes())
    plan.update(state="waiting", original_pid=100000)
    prepared.plan_path.write_text(json.dumps(plan), encoding="utf-8")


def apply(prepared, **kwargs):
    return update.apply_update(prepared.plan_path, helper_executable=prepared.helper_executable, wait=lambda pid: None, relaunch=False, **kwargs)


def test_source_and_unmanaged_installs_have_explicit_manual_route(tmp_path):
    assert "source" in update.installation_support(frozen=False).reason
    exe = tmp_path / ("taskman.exe" if os.name == "nt" else "taskman")
    exe.write_bytes(b"unmanaged")
    assert not update.installation_support(exe, frozen=True).supported


def test_additional_user_file_and_empty_folder_refuse_replacement(installation):
    exe, _, _ = installation
    extra = exe.parent / "my-notes.md"
    extra.write_text("Preserve me", encoding="utf-8")
    with pytest.raises(update.UpdateError, match="additional files"):
        prepare(installation)
    assert extra.read_text() == "Preserve me"
    extra.unlink()
    (exe.parent / "MyVault").mkdir()
    with pytest.raises(update.UpdateError, match="additional folders"):
        prepare(installation)


def test_checksum_archive_size_and_api_digest_all_required(installation):
    exe, release, transport = installation
    for bad in (replace(release, sha256="b" * 64), replace(release, checksum_sha256="b" * 64), replace(release, asset_size=release.asset_size + 1)):
        with pytest.raises(update.UpdateError, match="checksum|digest|SHA-256"):
            update.prepare_update(bad, executable=exe, frozen=True, transport=transport)
    assert list((exe.parent.parent / ".taskman-updates").iterdir()) == []


def test_manifest_wrong_version_fails_before_execution(installation, monkeypatch):
    exe, release, transport = installation
    data = transport(release.download_url, update.MAX_ARCHIVE)
    source = exe.parent.parent / "build"
    update.write_install_manifest(source, "9.0.0")
    data = archive_for(source)
    checksums = f"{digest(data)}  {release.asset_name}\n".encode()
    release = replace(release, sha256=digest(data), checksum_sha256=digest(checksums), asset_size=len(data))
    def probe(path, *args):
        assert path == exe, "unvalidated incoming executable must not run"
    monkeypatch.setattr(update, "_probe", probe)
    with pytest.raises(update.UpdateError, match="version does not match"):
        update.prepare_update(release, executable=exe, frozen=True, transport=lambda url, *_: checksums if url.endswith(".txt") else data)


@pytest.mark.parametrize("name", ["../escape", "taskman/../../escape", "/absolute", "taskman/C:stream", "taskman/CON.txt", "taskman/foo.", "taskman/foo ", "taskman/a\\b", "taskman//b", "taskman/a\x00b", "other/taskman.exe"])
def test_unsafe_archive_paths_refused(tmp_path, name):
    if "\x00" in name:
        with pytest.raises(update.UpdateError):
            update._member(name)
        return
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        info = zipfile.ZipInfo(name)
        info.filename = name  # retain malicious raw separators on Windows
        archive.writestr(info, b"payload")
    with pytest.raises(update.UpdateError):
        update._extract(output.getvalue(), tmp_path / "output")
    assert not (tmp_path / "escape").exists()


@pytest.mark.parametrize("names", [["taskman/a", "taskman/A"], ["taskman/a", "taskman/a/b"], ["taskman/A/x", "taskman/a/y"]])
def test_archive_collisions_refused(tmp_path, names):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name in names:
            archive.writestr(name, b"payload")
    with pytest.raises(update.UpdateError):
        update._extract(output.getvalue(), tmp_path / "output")


def test_symlink_archive_refused(tmp_path):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        info = zipfile.ZipInfo("taskman/link")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, "../../user-notes")
    with pytest.raises(update.UpdateError, match="link"):
        update._extract(output.getvalue(), tmp_path / "output")


def test_extraction_limits_enforced(tmp_path, monkeypatch):
    monkeypatch.setattr(update, "MAX_FILE", 4)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("taskman/big", b"12345")
    with pytest.raises(update.UpdateError, match="limits"):
        update._extract(output.getvalue(), tmp_path / "output")


def test_cancelled_download_removes_only_own_stage(installation):
    exe, _, _ = installation
    def cancel(message):
        raise update.UpdateCancelled("Closed")
    with pytest.raises(update.UpdateCancelled):
        prepare(installation, progress=cancel)
    assert exe.exists()
    assert not list((exe.parent.parent / ".taskman-updates").iterdir())


def test_discard_only_prepared_update_and_keep_launched(installation):
    first = prepare(installation)
    update.discard_update(first)
    assert not first.plan_path.parent.exists()
    second = prepare(installation)
    wait_plan(second)
    with pytest.raises(update.UpdateError, match="started"):
        update.discard_update(second)
    assert second.plan_path.exists()


def test_real_disposable_folder_swap_retains_backup_vault_and_receipt(installation):
    exe, _, _ = installation
    vault = exe.parent.parent / "Vault"
    vault.mkdir()
    note = vault / "Personal.md"
    note.write_bytes(b"private exact bytes\r\n")
    original = update._tree(exe.parent)
    prepared = prepare(installation)
    wait_plan(prepared)
    assert apply(prepared) == 0
    assert update._manifest(exe.parent)["version"] == "3.2.0"
    assert update._tree(prepared.plan_path.parent / "previous-app") == original
    assert note.read_bytes() == b"private exact bytes\r\n"
    receipt = update.read_update_result(exe, consume=True)
    assert receipt["status"] == "success"
    assert update.read_update_result(exe) is None


def test_failed_post_swap_probe_restores_previous_exact_bytes(installation):
    exe, _, _ = installation
    original = update._tree(exe.parent)
    prepared = prepare(installation)
    wait_plan(prepared)
    def probe(path, version, work):
        if path == exe:
            raise update.UpdateError("simulated startup failure")
    assert apply(prepared, probe=probe) == 1
    assert update._tree(exe.parent) == original
    assert (prepared.plan_path.parent / "failed-app").is_dir()
    assert "restored" in update.read_update_result(exe)["message"]


def test_failed_incoming_move_rolls_back_without_deleting_user_data(installation, monkeypatch):
    exe, _, _ = installation
    prepared = prepare(installation)
    wait_plan(prepared)
    original = update._tree(exe.parent)
    rename = Path.rename
    def fail(self, target):
        if self == prepared.plan_path.parent / "incoming":
            raise PermissionError("simulated file lock")
        return rename(self, target)
    monkeypatch.setattr(Path, "rename", fail)
    assert apply(prepared) == 1
    assert update._tree(exe.parent) == original


def test_external_edit_while_waiting_aborts_without_replacement(installation):
    exe, _, _ = installation
    prepared = prepare(installation)
    wait_plan(prepared)
    def wait(pid):
        (exe.parent / "User.md").write_bytes(b"external edit")
    assert update.apply_update(prepared.plan_path, helper_executable=prepared.helper_executable, wait=wait, relaunch=False) == 1
    assert (exe.parent / "User.md").read_bytes() == b"external edit"
    assert not (prepared.plan_path.parent / "previous-app").exists()


def test_tampered_plan_cannot_target_other_folder_or_run_other_helper(installation):
    exe, _, _ = installation
    prepared = prepare(installation)
    # Use a regular fixture file; the host Python executable may be a symlink,
    # which would exercise link rejection before the bootstrap ownership check.
    with pytest.raises(update.UpdateError, match="bootstrap"):
        update.apply_update(prepared.plan_path, helper_executable=exe, relaunch=False)
    data = json.loads(prepared.plan_path.read_bytes())
    data["install_dir"] = str(prepared.plan_path.parent.parent / "other")
    prepared.plan_path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(update.UpdateError, match="original application"):
        apply(prepared)


def sys_executable():
    import sys
    return sys.executable


def test_launch_is_argv_no_shell_and_cannot_relaunch_arbitrary_commands(installation, monkeypatch):
    prepared = prepare(installation)
    calls = []
    class Process:
        pid = 20000
        def poll(self): return None
    def popen(*args, **kwargs):
        calls.append((args, kwargs))
        update._atomic_json(prepared.plan_path.parent / "ready.json", {"app_id": update.APP_ID, "pid": 20000, "original_pid": 12345, "version": prepared.version})
        return Process()
    monkeypatch.setattr(subprocess, "Popen", popen)
    update.launch_update(prepared, original_pid=12345, relaunch_args=["--vault", str(prepared.plan_path.parent.parent / "Vault")])
    assert calls[0][0][0] == [str(prepared.helper_executable), "--apply-update", str(prepared.plan_path)]
    assert not calls[0][1].get("shell")
    second = prepare(installation)
    with pytest.raises(update.UpdateError, match="unsupported launch arguments"):
        update.launch_update(second, original_pid=12345, relaunch_args=["--add", "bad"])
    assert json.loads(second.plan_path.read_bytes())["state"] == "prepared"


def test_wait_does_not_force_close_a_process():
    process = subprocess.Popen([sys_executable(), "-c", "import time; time.sleep(0.4)"],
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    try:
        with pytest.raises(update.UpdateError, match="did not close"):
            update._wait_for_exit(process.pid, timeout=0.01)
        assert process.poll() is None
        process.wait(timeout=10)
        update._wait_for_exit(process.pid, timeout=0.1)
    finally:
        process.wait(timeout=10)


def test_current_version_probe_rejects_modified_manifest_version(installation, monkeypatch):
    exe, _, _ = installation
    def probe(path, version, work):
        if path == exe:
            raise update.UpdateError("The updated executable did not report the expected version.")
    monkeypatch.setattr(update, "_probe", probe)
    with pytest.raises(update.UpdateError, match="expected version"):
        prepare(installation)
    assert update._manifest(exe.parent)["version"] == "3.1.0"


def test_wait_timeout_never_relaunches_a_duplicate_app(installation, monkeypatch):
    prepared = prepare(installation)
    wait_plan(prepared)
    def wait(pid):
        raise update.UpdateError("Taskman did not close in time")
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: pytest.fail("Original app is still open"))
    assert update.apply_update(prepared.plan_path, helper_executable=prepared.helper_executable, wait=wait) == 1


@pytest.mark.parametrize("exited", [True, False])
def test_helper_startup_failure_or_timeout_keeps_original_application(installation, monkeypatch, exited):
    exe, _, _ = installation
    prepared = prepare(installation)
    original = update._tree(exe.parent)
    class Process:
        pid = 20000
        def poll(self): return 1 if exited else None
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: Process())
    monkeypatch.setattr(update, "HELPER_READY_TIMEOUT", 0.01)
    with pytest.raises(update.UpdateError, match="Taskman has stayed open"):
        update.launch_update(prepared, original_pid=12345)
    assert update._tree(exe.parent) == original
    assert json.loads(prepared.plan_path.read_bytes())["state"] == "cancelled"


def test_receipt_write_failure_still_relaunches_verified_app(installation, monkeypatch):
    exe, _, _ = installation
    prepared = prepare(installation)
    wait_plan(prepared)
    write = update._atomic_json
    def broken(path, value):
        if path.name == "update-result.json" or path.name.startswith("last-"):
            raise OSError("simulated status disk failure")
        return write(path, value)
    monkeypatch.setattr(update, "_atomic_json", broken)
    launches = []
    monkeypatch.setattr(subprocess, "Popen", lambda args, **kwargs: launches.append(args))
    assert update.apply_update(prepared.plan_path, helper_executable=prepared.helper_executable, wait=lambda pid: None) == 0
    assert launches == [[str(exe)]]
    assert update._manifest(exe.parent)["version"] == "3.2.0"


@pytest.mark.parametrize("before_wait", [True, False])
def test_cancelled_helper_never_relaunches_or_overwrites_newer_receipt(installation, monkeypatch, before_wait):
    exe, _, _ = installation
    prepared = prepare(installation)
    wait_plan(prepared)
    receipt = {"app_id": update.APP_ID, "install_dir": str(exe.parent), "version": "3.3.0", "status": "success", "message": "Newer success", "shown": False}
    update._save_receipt(exe.parent, receipt)
    def cancel(pid=None):
        plan = json.loads(prepared.plan_path.read_bytes())
        plan["state"] = "cancelled"
        update._atomic_json(prepared.plan_path, plan)
    if before_wait:
        cancel()
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: pytest.fail("Cancelled helper must stay silent"))
    assert update.apply_update(prepared.plan_path, helper_executable=prepared.helper_executable, wait=cancel) == 1
    assert update.read_update_result(exe) == receipt
    assert update._manifest(exe.parent)["version"] == "3.1.0"


def test_older_failure_cannot_replace_a_newer_success_receipt(installation):
    exe, _, _ = installation
    (exe.parent.parent / ".taskman-updates").mkdir()
    newer = {"app_id": update.APP_ID, "install_dir": str(exe.parent), "version": "3.3.0", "status": "success", "message": "Updated", "shown": False}
    update._save_receipt(exe.parent, newer)
    update._save_receipt(exe.parent, {**newer, "version": "3.2.0", "status": "failed", "message": "Old attempt failed"})
    assert update.read_update_result(exe) == newer


def test_wait_can_cancel_without_closing_original_process():
    process = subprocess.Popen([sys_executable(), "-c", "import time; time.sleep(0.4)"],
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    try:
        with pytest.raises(update.UpdateCancelled):
            update._wait_for_exit(process.pid, cancelled=lambda: True)
        assert process.poll() is None
    finally:
        process.wait(timeout=10)


def test_build_manifest_handles_framework_aliases_without_following_them(tmp_path):
    exe = bundle(tmp_path / "bundle", "3.2.0")
    internal = exe.parent / "_internal"
    canonical = internal / "FrameworkVersion"
    canonical.mkdir()
    (canonical / "Python").write_bytes(b"framework runtime")
    alias = internal / "Current"
    try:
        alias.symlink_to(canonical, target_is_directory=True)
        (internal / "Python").symlink_to(canonical / "Python")
    except OSError:
        pytest.skip("Creating symbolic links needs privileges on this Windows host")
    manifest = update.write_install_manifest(exe.parent, "3.2.0")
    assert "_internal/FrameworkVersion/Python" in manifest["files"]
    assert "_internal/Python" in manifest["files"]
    assert not any(name.startswith("_internal/Current/") for name in manifest["files"])
    with pytest.raises(update.UpdateError, match="links|junctions"):
        update._manifest(exe.parent)


def test_modified_new_application_is_preserved_during_rollback(installation):
    exe, _, _ = installation
    prepared = prepare(installation)
    wait_plan(prepared)
    def probe(path, version, work):
        if path == exe:
            (exe.parent / "external.md").write_bytes(b"new external content")
    assert apply(prepared, probe=probe) == 1
    assert (prepared.plan_path.parent / "failed-app" / "external.md").read_bytes() == b"new external content"
    assert update._manifest(exe.parent)["version"] == "3.1.0"
