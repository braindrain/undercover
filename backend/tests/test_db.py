import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import sleep

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
    assert created[0]["pool_timeout"] == 120
    assert created[0]["connect_args"] == {"options": "-c search_path=undercover"}


def test_many_threads_at_once_create_one_engine(monkeypatch):
    built = []
    real = db.create

    def create(url):
        built.append(url)
        sleep(0.01)
        return real(url)

    monkeypatch.setattr(db, "create", create)
    db.engine_for.cache_clear()
    with ThreadPoolExecutor(25) as pool:
        engines = list(pool.map(lambda _: db.engine(), range(25)))
    assert len(built) == 1
    assert len(set(map(id, engines))) == 1


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
        "run_claims",
        "held_out_claims",
        "handler_decisions",
    }


@pytest.mark.parametrize(
    "source", sorted(Path(db.__file__).parent.glob("*.py")), ids=lambda path: path.name
)
def test_no_query_is_an_sql_string(source):
    text = source.read_text(encoding="utf-8")
    assert not re.search(r"[\"'][^\"'\n]*\b(SELECT|INSERT|UPDATE|DELETE)\b", text)
