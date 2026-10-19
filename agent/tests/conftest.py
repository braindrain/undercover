import pytest


@pytest.fixture(autouse=True)
def database(tmp_path, monkeypatch):
    """Give each test a fresh database seeded from data/."""
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "undercover.db"))
