import importlib
import os
from contextlib import contextmanager
from types import SimpleNamespace

from langfuse.langchain import CallbackHandler
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row

from agent import harness

SUPABASE_URL = (
    "postgresql://postgres.abcdefgh:secret@aws-1-eu-west-1.pooler.supabase.com"
    ":5432/postgres"
)


def test_no_tracing_without_the_langfuse_keys():
    assert harness.thread_config("CLM-1001-3f9a2c1d") == {
        "configurable": {"thread_id": "CLM-1001-3f9a2c1d"}
    }
    harness.flush_traces()


def test_tracing_puts_a_claim_thread_in_one_session(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    config = harness.thread_config("CLM-1001-3f9a2c1d")
    callbacks = config.get("callbacks")
    assert isinstance(callbacks, list)
    assert [type(handler) for handler in callbacks] == [CallbackHandler]
    assert config.get("metadata") == {"langfuse_session_id": "CLM-1001-3f9a2c1d"}


def test_the_checkpointer_is_in_memory_without_a_database_url():
    assert isinstance(harness.checkpointer(), InMemorySaver)
    assert harness.checkpointer() is harness.checkpointer()


def test_the_checkpointer_pool_has_the_labs_settings():
    pool = harness.connection_pool(SUPABASE_URL)
    assert pool.closed
    assert pool.max_size == harness.POOL_SIZE == 4
    assert pool.kwargs == {
        "autocommit": True,
        "prepare_threshold": 0,
        "row_factory": dict_row,
        "options": "-c search_path=undercover",
    }
    assert isinstance(PostgresSaver(pool), PostgresSaver)


def test_postgres_checkpointer_with_a_database_url(monkeypatch):
    opened = []

    class Pool:
        def __init__(self, url):
            opened.append(url)

        def open(self, wait):
            opened.append(wait)

        @contextmanager
        def connection(self):
            yield SimpleNamespace(execute=opened.append)

    monkeypatch.setenv("DATABASE_URL", SUPABASE_URL)
    monkeypatch.setattr(harness, "connection_pool", Pool)
    monkeypatch.setattr(
        harness,
        "PostgresSaver",
        lambda pool: SimpleNamespace(setup=lambda: opened.append("setup")),
    )
    harness.checkpointer()
    assert opened == [
        SUPABASE_URL,
        True,
        "CREATE SCHEMA IF NOT EXISTS undercover",
        "setup",
    ]


def test_importing_the_agent_loads_no_env_file(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    for name in ["agent.api", "agent.harness", "agent.tools", "agent.agents"]:
        importlib.reload(importlib.import_module(name))
    assert "DATABASE_URL" not in os.environ
