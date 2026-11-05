import pytest

from backend import agent_client


@pytest.fixture(autouse=True)
def database(tmp_path, monkeypatch):
    """Give each test a fresh SQLite database seeded from data/, never Supabase."""
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "undercover.db"))


@pytest.fixture(autouse=True)
def agent_calls(monkeypatch):
    """Record each call the backend makes to the agent's service, in place of
    the service, and return them."""
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        agent_client, "request", lambda path, body: calls.append((path, body))
    )
    return calls
