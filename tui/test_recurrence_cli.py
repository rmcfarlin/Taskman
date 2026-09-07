"""Recurrence commands share the safe task mutation path and structured results."""
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tui import __main__ as entrypoint
from tui import taskman as tm


@pytest.fixture
def vault(tmp_path):
    root = tmp_path / "vault"
    (root / "Tasks").mkdir(parents=True)
    return root


def invoke(vault, capsys, *args):
    code = entrypoint.main(["--vault", str(vault), "--json", *args])
    output = capsys.readouterr()
    assert output.err == ""
    return code, json.loads(output.out)


def markdown(vault):
    return {p.relative_to(vault).as_posix(): p.read_bytes() for p in vault.rglob("*.md")}


def test_add_repeat_and_completion_return_fresh_successor_once(vault, capsys):
    code, added = invoke(vault, capsys, "--add", "Daily reference Ω", "--repeat", "daily",
                         "--due", "2096-09-07", "--scheduled", "2096-09-05")
    assert code == 0 and added["task"]["recurrence_status"] == "active"
    original = added["task"]
    assert original["recurrence"] and original["recurrence_warning"] is None
    code, completed = invoke(vault, capsys, "--complete", original["id"])
    assert code == 0 and completed["changed"] and completed["warning"] is None
    successor = completed["next_task"]
    assert successor["id"] != original["id"]
    assert successor["description"] == "Daily reference Ω"
    assert successor["status"] == " "
    assert successor["recurrence"] == original["recurrence"]
    assert successor["due"] == "2096-09-08" and successor["scheduled"] == "2096-09-06"
    assert completed["task"]["recurrence_next"] == successor["id"]
    assert completed["task"]["recurrence_status"] == "next-created"
    before = markdown(vault)
    code, repeated = invoke(vault, capsys, "--complete", original["id"])
    assert code == 0 and not repeated["changed"]
    assert repeated["next_task"]["id"] == successor["id"] and repeated["warning"] is None
    assert markdown(vault) == before and len(tm.load_all(vault)) == 2


def test_repeat_rule_and_dates_update_together_and_clear(vault, capsys):
    _, added = invoke(vault, capsys, "--add", "Weekly review")
    identifier = added["task"]["id"]
    code, updated = invoke(vault, capsys, "--id", identifier, "--repeat", "weekly",
                           "--due", "2096-09-07", "--scheduled", "2096-09-05")
    assert code == 0 and updated["changed"] and updated["task"]["recurrence_status"] == "active"
    code, unchanged = invoke(vault, capsys, "--id", identifier, "--repeat", "weekly")
    assert code == 0 and not unchanged["changed"]
    before = markdown(vault)
    code, error = invoke(vault, capsys, "--id", identifier, "--repeat", "every lunar eclipse",
                         "--due", "2096-10-07")
    assert code == 2 and not error["ok"] and markdown(vault) == before
    code, error = invoke(vault, capsys, "--id", identifier, "--repeat", "daily",
                         "--due", "clear", "--scheduled", "clear")
    assert code == 2 and not error["ok"] and markdown(vault) == before
    code, cleared = invoke(vault, capsys, "--id", identifier, "--repeat", "clear",
                           "--due", "clear", "--scheduled", "clear")
    assert code == 0 and cleared["changed"]
    assert cleared["task"]["recurrence"] is None and cleared["task"]["recurrence_status"] == "none"
    assert cleared["task"]["due"] is None and cleared["task"]["scheduled"] is None


def test_repeat_clear_overrides_inline_rule_when_adding(vault, capsys):
    code, added = invoke(vault, capsys, "--add", "Local task 🔁 every day", "--repeat", "clear")
    assert code == 0 and added["task"]["recurrence"] is None
    assert "🔁" not in (vault / added["task"]["file"]).read_text(encoding="utf-8")


def test_subtask_repeat_uses_same_creation_validation(vault, capsys):
    _, added = invoke(vault, capsys, "--add", "Reference folder")
    code, child = invoke(vault, capsys, "--sub", "Weekly detail", "--under", added["task"]["id"],
                         "--repeat", "weekly", "--scheduled", "2096-09-07")
    assert code == 0 and child["task"]["depth"] == 1
    assert child["task"]["recurrence_status"] == "active"
    code, completed = invoke(vault, capsys, "--complete", child["task"]["id"])
    assert code == 0 and completed["next_task"]["depth"] == 1
    assert completed["next_task"]["scheduled"] == "2096-09-14"


@pytest.mark.parametrize("rule", ["every lunar eclipse", "every day"])
def test_imported_blocked_rule_stays_readable_and_completes_with_json_warning(vault, capsys, rule):
    path = vault / "Tasks/Imported.md"
    path.write_text(f"- [ ] Imported routine 🔁 {rule}\n", encoding="utf-8")
    before = path.read_bytes()
    code, listing = invoke(vault, capsys)
    assert code == 0 and len(listing["tasks"]) == 1
    task = listing["tasks"][0]
    assert task["recurrence"] == rule and task["recurrence_status"] == "blocked"
    assert task["recurrence_warning"] and task["id"] is None
    assert path.read_bytes() == before
    code, completed = invoke(vault, capsys, "--complete", task["location"])
    assert code == 0 and completed["task"]["status"] == "x"
    assert completed["warning"] and completed["next_task"] is None
    assert completed["task"]["recurrence"] == rule and len(tm.load_all(vault)) == 1


def test_plain_completion_warning_goes_to_stderr(vault, capsys):
    path = vault / "Tasks/Imported.md"
    path.write_text("- [ ] Needs a date 🔁 every day\n", encoding="utf-8")
    code = entrypoint.main(["--vault", str(vault), "--complete", "Tasks/Imported.md:1"])
    output = capsys.readouterr()
    assert code == 0 and output.out.startswith("complete: ")
    assert "date" in output.err.lower() and "next:" not in output.out


@pytest.mark.parametrize("args", [
    ("--repeat", "daily"),
    ("--repeat", "daily", "--plain", "all"),
    ("--add", "Bad rule", "--repeat", "unknown", "--due", "today"),
    ("--add", "Bad date", "--repeat", "daily", "--due", "2026-02-30"),
])
def test_invalid_repeat_command_does_not_create_vault(tmp_path, capsys, args):
    missing = tmp_path / "missing vault"
    code, result = invoke(missing, capsys, *args)
    assert code == 2 and not result["ok"] and not missing.exists()


def test_recurrence_cli_uses_only_standard_library(tmp_path):
    root = Path(__file__).resolve().parent.parent
    vault = tmp_path / "stdlib vault Ω"
    vault.mkdir()
    env = dict(os.environ, TASKMAN_CONFIG_DIR=str(tmp_path / "profile"))
    command = [sys.executable, "-S", "-X", "utf8", "-m", "tui", "--vault", str(vault), "--json"]
    added = subprocess.run([*command, "--add", "Read Ω", "--repeat", "daily", "--due", "today"],
                           cwd=root, env=env, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert added.returncode == 0 and added.stderr == "", added.stdout + added.stderr
    task = json.loads(added.stdout)["task"]
    completed = subprocess.run([*command, "--complete", task["id"]], cwd=root, env=env,
                               capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert completed.returncode == 0 and completed.stderr == "", completed.stdout + completed.stderr
    next_task = json.loads(completed.stdout)["next_task"]
    assert next_task["due"] == (dt.date.today() + dt.timedelta(days=1)).isoformat()
