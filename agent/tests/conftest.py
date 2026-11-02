import pytest
from fastapi.testclient import TestClient

from agent import api, harness
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
def local_backend(monkeypatch):
    """Reach the backend in-process, and retry without waiting."""
    monkeypatch.setattr(api, "client", lambda: TestClient(app))
    monkeypatch.setattr(harness, "RETRY_DELAY", 0)
