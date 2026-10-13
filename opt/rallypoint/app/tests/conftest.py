"""Keep legacy scoring scenarios deterministic; random tests override this draw."""
import pytest

@pytest.fixture(autouse=True)
def deterministic_first_serve(monkeypatch):
    monkeypatch.setattr("pickleball_scoreboard.config.secrets.choice", lambda teams: "A")
