import pytest
from fastapi.testclient import TestClient

from agent import middleware, tools
from backend.main import app


@pytest.fixture(autouse=True)
def database(tmp_path, monkeypatch):
    """Give each test a fresh database seeded from data/."""
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "undercover.db"))


@pytest.fixture(autouse=True)
def local_backend(monkeypatch):
    """Reach the backend in-process, and retry without waiting."""
    monkeypatch.setattr(tools, "backend", lambda: TestClient(app))
    monkeypatch.setattr(middleware, "RETRY_DELAY", 0)
