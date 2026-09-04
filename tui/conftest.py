"""Every test owns its preferences and cannot remember a user's real vault."""
import pytest


@pytest.fixture(autouse=True)
def isolated_preferences(tmp_path, monkeypatch):
    monkeypatch.setenv("TASKMAN_CONFIG_DIR", str(tmp_path / "user-settings"))
    monkeypatch.delenv("TASKMAN_VAULT", raising=False)
