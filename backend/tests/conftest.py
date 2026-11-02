import pytest


@pytest.fixture(autouse=True)
def database(tmp_path, monkeypatch):
    """Give each test a fresh SQLite database seeded from data/, never Supabase."""
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "undercover.db"))
