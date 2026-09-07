"""Stable handles and machine-readable commands against disposable Markdown vaults."""
import datetime as dt
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

import pytest

from tui import __main__ as entrypoint
from tui import taskman as tm


ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def vault(tmp_path):
    root = tmp_path / "vault"
    (root / "Tasks").mkdir(parents=True)
    (root / "Tasks" / "Inbox.md").write_text("# Inbox\n\n- [ ] Legacy task\n", encoding="utf-8")
    return root


def invoke(vault, capsys, *args):
    result = entrypoint.main(["--vault", str(vault), "--json", *args])
    captured = capsys.readouterr()
    assert captured.err == ""
    return result, json.loads(captured.out)


def test_add_returns_id_and_dates_then_survives_line_shift_and_file_move(vault, capsys):
    result, added = invoke(vault, capsys, "--add", "Close Ω", "--project", "Finance",
                           "--due", "2026-09-30", "--scheduled", "2026-09-28")
    assert result == 0 and added["changed"]
    task = added["task"]
    assert re.fullmatch(r"[0-9a-f]{32}", task["id"])
    assert task["description"] == "Close Ω" and task["project"] == "Finance"
    assert (task["due"], task["scheduled"]) == ("2026-09-30", "2026-09-28")
    source = vault / task["file"]
    source.write_text("- [ ] Other task\n\n" + source.read_text(encoding="utf-8"), encoding="utf-8")
    moved = source.with_name("Moved.md")
    source.rename(moved)
    result, updated = invoke(vault, capsys, "--id", task["id"], "--due", "+7")
    assert result == 0
    assert updated["task"]["file"] == "Projects/Moved.md"
    assert updated["task"]["id"] == task["id"]
    assert updated["task"]["due"] == (dt.date.today() + dt.timedelta(days=7)).isoformat()
    assert updated["task"]["scheduled"] == "2026-09-28"
    assert next(t for t in tm.load_all(vault) if t.description == "Other task").due is None


def test_complete_is_idempotent_and_preserves_the_completed_bytes(vault, capsys):
    _, created = invoke(vault, capsys, "--add", "Finish")
    identifier = created["task"]["id"]
    result, first = invoke(vault, capsys, "--complete", identifier)
    assert result == 0 and first["changed"] and first["task"]["status"] == "x"
    path = vault / first["task"]["file"]
    before = path.read_bytes()
    result, second = invoke(vault, capsys, "--complete", identifier)
    assert result == 0 and second["changed"] is False
    assert second["task"]["done_date"] == dt.date.today().isoformat()
    assert path.read_bytes() == before


def test_concurrent_completion_commands_succeed_once(vault, tmp_path, capsys):
    _, created = invoke(vault, capsys, "--add", "Concurrent completion")
    identifier = created["task"]["id"]
    start = tmp_path / "start"
    code = """
from pathlib import Path
import sys
import time
from tui.__main__ import main
ready, start = Path(sys.argv[1]), Path(sys.argv[2])
ready.write_text('ready', encoding='utf-8')
deadline = time.monotonic() + 20
while not start.exists():
    if time.monotonic() > deadline:
        raise RuntimeError('test start barrier timed out')
    time.sleep(0.01)
raise SystemExit(main(sys.argv[3:]))
"""
    env = dict(os.environ, TASKMAN_CONFIG_DIR=str(tmp_path / "preferences"))
    ready = [tmp_path / f"ready-{index}" for index in range(2)]
    processes = []
    try:
        for flag in ready:
            processes.append(subprocess.Popen(
                [sys.executable, "-S", "-c", code, str(flag), str(start), "--vault", str(vault),
                 "--json", "--complete", identifier], cwd=ROOT, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8"))
        deadline = time.monotonic() + 20
        while not all(flag.exists() for flag in ready):
            assert time.monotonic() < deadline, "CLI test processes did not become ready"
            time.sleep(0.01)
        start.write_text("go", encoding="utf-8")
        results = []
        for process in processes:
            output, error = process.communicate(timeout=30)
            assert process.returncode == 0 and error == "", output + error
            results.append(json.loads(output))
        assert sorted(result["changed"] for result in results) == [False, True]
        assert all(result["task"]["id"] == identifier and result["task"]["status"] == "x"
                   for result in results)
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=10)


def test_json_read_never_stamps_legacy_tasks(vault, capsys):
    path = vault / "Tasks/Inbox.md"
    before = path.read_bytes()
    result, listing = invoke(vault, capsys)
    assert result == 0 and listing["view"] == "all"
    legacy = listing["tasks"][0]
    assert legacy["id"] is None and legacy["location"] == "Tasks/Inbox.md:3"
    result, single = invoke(vault, capsys, "--id", legacy["location"])
    assert result == 0 and single["task"]["id"] is None
    assert path.read_bytes() == before


def test_ensure_id_is_explicit_and_repeatable(vault, capsys):
    result, first = invoke(vault, capsys, "--ensure-id", "Tasks/Inbox.md:3")
    assert result == 0 and first["changed"]
    identifier = first["task"]["id"]
    assert re.fullmatch(r"[0-9a-f]{32}", identifier)
    path = vault / first["task"]["file"]
    before = path.read_bytes()
    result, second = invoke(vault, capsys, "--ensure-id", identifier)
    assert result == 0 and not second["changed"]
    assert second["task"]["id"] == identifier and path.read_bytes() == before


@pytest.mark.parametrize("stable", [False, True])
def test_sub_and_note_accept_stable_or_legacy_parent(vault, capsys, stable):
    identifier = "Tasks/Inbox.md:3"
    if stable:
        _, ensured = invoke(vault, capsys, "--ensure-id", identifier)
        identifier = ensured["task"]["id"]
    result, child = invoke(vault, capsys, "--sub", "Child", "--under", identifier,
                           "--due", "tomorrow", "--scheduled", "today")
    assert result == 0 and re.fullmatch(r"[0-9a-f]{32}", child["task"]["id"])
    assert child["task"]["depth"] == 1
    assert child["task"]["scheduled"] == dt.date.today().isoformat()
    result, note = invoke(vault, capsys, "--note", "Remember this", "--under", identifier)
    assert result == 0 and note["task"]["note"] == "Remember this"
    result, cleared = invoke(vault, capsys, "--note", "", "--under", identifier)
    assert result == 0 and cleared["task"]["note"] == ""


def test_date_clear_overrides_inline_dates_at_creation(vault, capsys):
    result, added = invoke(vault, capsys, "--add", "Work 📅 2026-09-30 ⏳ 2026-09-28",
                           "--due", "clear", "--scheduled", "clear")
    assert result == 0
    assert added["task"]["due"] is None and added["task"]["scheduled"] is None
    result, changed = invoke(vault, capsys, "--id", added["task"]["id"], "--scheduled", "tomorrow")
    assert result == 0 and changed["task"]["due"] is None
    result, cleared = invoke(vault, capsys, "--id", added["task"]["id"], "--scheduled", "clear")
    assert result == 0 and cleared["task"]["scheduled"] is None


def test_now_alias_and_scheduled_tasks_are_machine_readable(vault, capsys):
    _, created = invoke(vault, capsys, "--add", "Planned", "--scheduled", "today")
    result, now = invoke(vault, capsys, "--plain", "now")
    assert result == 0
    result, today = invoke(vault, capsys, "--plain", "today")
    assert result == 0 and now == today
    assert [t["id"] for t in now["tasks"]] == [created["task"]["id"]]


@pytest.mark.parametrize("view", ["all", "now", "project"])
def test_listing_project_filter_keeps_only_matches_and_required_ancestors(vault, capsys, view):
    path = vault / "Tasks/Inbox.md"
    day = dt.date.today().isoformat()
    path.write_text(f"- [ ] Parent #project/Other\n  - [ ] Child #project/Finance ⏳ {day}\n"
                    f"- [ ] Unrelated #project/Other ⏳ {day}\n", encoding="utf-8")
    result, listing = invoke(vault, capsys, "--plain", view, "--project", "Finance")
    assert result == 0
    assert [task["description"] for task in listing["tasks"]] == ["Parent", "Child"]
    assert listing["tasks"][0]["context"] is True
    assert listing["tasks"][1]["context"] is False
    assert entrypoint.main(["--vault", str(vault), "--plain", view, "--project", "Finance"]) == 0
    plain = capsys.readouterr()
    assert plain.err == "" and "Child" in plain.out and "Unrelated" not in plain.out


@pytest.mark.parametrize("args", [
    ("--add", "Work", "--complete", "a" * 32),
    ("--id", "a" * 32, "--plain", "all"),
    ("--due", "today"),
    ("--under", "Tasks/Inbox.md:3"),
    ("--note", "No target"),
    ("--sub", "No parent"),
    ("--search", "term"),
    ("--check", "--project", "Project"),
    ("--plain", "typo"),
    ("--plain", "projects", "--project", "Project"),
    ("--add", "Work", "--due", "bad date"),
    ("--add", "Work", "--scheduled", "2026-02-30"),
    ("--add", ""),
    ("--id", ""),
    ("--theme", "taskman-teal", "--add", "Work"),
    ("--mistyped-option",),
    ("--scheduled",),
])
def test_bad_commands_return_only_json_and_leave_vault_unchanged(vault, capsys, args):
    before = {p.relative_to(vault): p.read_bytes() for p in vault.rglob("*.md")}
    result, error = invoke(vault, capsys, *args)
    assert result == 2 and error["ok"] is False and error["error"]
    assert {p.relative_to(vault): p.read_bytes() for p in vault.rglob("*.md")} == before


def test_missing_and_ambiguous_ids_fail_without_writing(vault, capsys):
    result, error = invoke(vault, capsys, "--complete", "a" * 32)
    assert result != 0 and not error["ok"]
    path = vault / "Tasks/Inbox.md"
    path.write_text((f"- [ ] One <!-- taskman:id={'a' * 32} -->\n"
                     f"- [ ] Two <!-- taskman:id={'a' * 32} -->\n"), encoding="utf-8")
    before = path.read_bytes()
    result, error = invoke(vault, capsys, "--id", "a" * 32, "--due", "today")
    assert result != 0 and "duplicate" in error["error"].lower()
    assert path.read_bytes() == before


@pytest.mark.parametrize("target", [["-m", "tui"], [str(ROOT / "tui/__main__.py")],
                                    ["-m", "tui.taskman"]])
def test_real_cli_json_round_trip_needs_only_stdlib(vault, tmp_path, target):
    env = dict(os.environ, TASKMAN_CONFIG_DIR=str(tmp_path / "settings"))
    command = [sys.executable, "-S", *target, "--vault", str(vault), "--json"]
    added = subprocess.run([*command, "--add", "Unicode Ω", "--scheduled", "today"],
                           cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert added.returncode == 0 and added.stderr == "", added.stdout + added.stderr
    task = json.loads(added.stdout)["task"]
    assert task["description"] == "Unicode Ω"
    done = subprocess.run([*command, "--complete", task["id"]], cwd=ROOT, env=env,
                          capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert done.returncode == 0 and done.stderr == "", done.stdout + done.stderr
    assert json.loads(done.stdout)["task"]["status"] == "x"
    bad = subprocess.run([*command, "--unknown"], cwd=ROOT, env=env,
                         capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert bad.returncode == 2 and bad.stderr == ""
    assert json.loads(bad.stdout)["ok"] is False


def test_plain_creation_prints_only_stable_id(vault, capsys):
    assert entrypoint.main(["--vault", str(vault), "--add", "Use this ID"]) == 0
    output = capsys.readouterr()
    assert output.err == "" and re.fullmatch(r"[0-9a-f]{32}\n", output.out)
