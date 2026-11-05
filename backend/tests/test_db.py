import re
from pathlib import Path

import pytest
from sqlalchemy import QueuePool

from backend import db

SUPABASE_URL = (
    "postgresql://postgres.abcdefgh:secret@aws-1-eu-west-1.pooler.supabase.com"
    ":5432/postgres"
)


def test_sqlite_without_a_database_url(tmp_path):
    assert db.database_url() == f"sqlite:///{tmp_path / 'undercover.db'}"
    assert db.engine().dialect.name == "sqlite"


def test_supabase_with_a_database_url(monkeypatch):
    created = []
    real = db.create_engine
    monkeypatch.setattr(
        db, "create_engine", lambda *args, **kw: created.append(kw) or real(*args, **kw)
    )
    monkeypatch.setenv("DATABASE_URL", SUPABASE_URL)
    engine = db.create(db.database_url())
    assert engine.url.drivername == "postgresql+psycopg"
    assert engine.url.host == "aws-1-eu-west-1.pooler.supabase.com"
    assert isinstance(engine.pool, QueuePool)
    assert engine.pool.size() == db.POOL_SIZE == 4
    assert created[0]["max_overflow"] == 0
    assert created[0]["connect_args"] == {"options": "-c search_path=undercover"}


def test_every_table_is_in_the_metadata():
    assert set(db.metadata.tables) == {
        "claims",
        "policies",
        "customers",
        "handlers",
        "steps",
        "decisions",
        "payments",
        "sign_offs",
        "model_calls",
        "work_times",
        "runs",
        "todos",
    }


@pytest.mark.parametrize(
    "source", sorted(Path(db.__file__).parent.glob("*.py")), ids=lambda path: path.name
)
def test_no_query_is_an_sql_string(source):
    text = source.read_text(encoding="utf-8")
    assert not re.search(r"[\"'][^\"'\n]*\b(SELECT|INSERT|UPDATE|DELETE)\b", text)
