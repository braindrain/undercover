import pytest
from fastapi.testclient import TestClient

from agent import api, harness
from backend import agent_client
from backend.main import app


@pytest.fixture(autouse=True)
def database(tmp_path, monkeypatch):
    """Give each test a fresh SQLite database seeded from data/, never Supabase,
    and no tracing."""
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "undercover.db"))
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "")


@pytest.fixture(autouse=True)
def agent_calls(monkeypatch):
    """Record each call the backend makes to the agent's service, in place of
    the service, and return them."""
    calls: list[str] = []
    monkeypatch.setattr(agent_client, "request", calls.append)
    return calls


@pytest.fixture(autouse=True)
def local_backend(monkeypatch):
    """Reach the backend in-process, and retry without waiting."""
    monkeypatch.setattr(api, "client", lambda: TestClient(app))
    monkeypatch.setattr(harness, "RETRY_DELAY", 0)
    monkeypatch.setattr(harness, "WORK_RETRY_DELAY", 0)
    monkeypatch.setattr(harness, "WORK_RETRY_JITTER", False)


@pytest.fixture(autouse=True)
def checkpointer():
    """Give each test an empty in-memory checkpointer. A restart in a test is a
    new graph on the same checkpointer."""
    harness.memory_saver.cache_clear()
    yield
    harness.memory_saver.cache_clear()
