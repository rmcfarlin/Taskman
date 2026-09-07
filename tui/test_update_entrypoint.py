"""Updater helper invocation remains separate from vault commands and the UI."""
from types import SimpleNamespace

import pytest

from tui import __main__ as entrypoint, updater


def test_helper_routes_only_its_plan_without_ui_or_vault(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(updater, "apply_update", lambda path: calls.append(path) or 7)
    plan = tmp_path / "plan.json"
    assert entrypoint.main(["--apply-update", str(plan)]) == 7
    assert calls == [plan]
    assert entrypoint.main(["--apply-update", str(plan), "--vault", str(tmp_path)]) == 2
    assert calls == [plan]


def test_bad_helper_plan_returns_failure_without_launching_app(monkeypatch, capsys):
    def fail(_path):
        raise updater.UpdateError("Update plan does not belong to this app")
    monkeypatch.setattr(updater, "apply_update", fail)
    assert entrypoint.main(["--apply-update", "bad-plan.json"]) == 1
    assert "does not belong" in capsys.readouterr().err


@pytest.mark.parametrize("available", [False, True])
def test_cli_checks_updates_without_loading_a_vault(monkeypatch, capsys, available):
    release = SimpleNamespace(version="9.0.0", html_url="https://github.com/rmcfarlin/Taskman/releases/tag/v9.0.0")
    monkeypatch.setattr(updater, "check_for_update", lambda _version: release if available else None)
    assert entrypoint.main(["--check-updates"]) == 0
    message = capsys.readouterr().out
    assert ("9.0.0 is available" if available else "no newer stable release") in message


def test_cli_update_network_failure_is_reported(monkeypatch, capsys):
    def fail(_version):
        raise updater.UpdateError("GitHub is unavailable")
    monkeypatch.setattr(updater, "check_for_update", fail)
    assert entrypoint.main(["--check-updates"]) == 1
    assert "GitHub is unavailable" in capsys.readouterr().err


@pytest.mark.parametrize("extra", [["--vault", "."], ["--init", "."], ["--plain", "all"], ["--theme", "list"]])
def test_cli_update_cannot_mix_with_vault_operations(monkeypatch, extra):
    monkeypatch.setattr(updater, "check_for_update", lambda *_: pytest.fail("Should not contact GitHub"))
    assert entrypoint.main(["--check-updates", *extra]) == 2
