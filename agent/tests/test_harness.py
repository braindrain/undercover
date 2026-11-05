import importlib
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from time import sleep
from types import SimpleNamespace

import pytest
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


def test_a_trace_is_tagged_with_its_run(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    config = harness.thread_config("CLM-1001-3f9a2c1d", "dispatch-3f9a2c1d")
    assert config.get("metadata") == {
        "langfuse_session_id": "CLM-1001-3f9a2c1d",
        "langfuse_tags": ["dispatch-3f9a2c1d"],
    }


def test_the_checkpointer_is_in_memory_without_a_database_url():
    assert isinstance(harness.checkpointer(), InMemorySaver)
    assert harness.checkpointer() is harness.checkpointer()


def test_the_checkpointer_pool_has_the_labs_settings():
    pool = harness.connection_pool(SUPABASE_URL)
    assert pool.closed
    assert pool.max_size == harness.POOL_SIZE == 4
    assert pool.timeout == harness.POOL_WAIT == 120
    assert pool.kwargs == {
        "autocommit": True,
        "prepare_threshold": 0,
        "row_factory": dict_row,
        "options": "-c search_path=undercover",
    }
    assert isinstance(PostgresSaver(pool), PostgresSaver)


@pytest.fixture
def postgres(monkeypatch):
    """Stand in for a Postgres pool, returning what happens to it."""
    opened = []

    class Pool:
        def __init__(self, url):
            opened.append(url)

        def open(self, wait):
            sleep(0.01)
            opened.append(wait)

        @contextmanager
        def connection(self):
            yield SimpleNamespace(execute=opened.append)

    class Saver:
        def __init__(self, pool):
            self.pool = pool

        def setup(self):
            opened.append("setup")

    monkeypatch.setenv("DATABASE_URL", SUPABASE_URL)
    monkeypatch.setattr(harness, "connection_pool", Pool)
    monkeypatch.setattr(harness, "PostgresSaver", Saver)
    harness.shared_pool.cache_clear()
    yield opened
    harness.shared_pool.cache_clear()


def test_postgres_checkpointer_with_a_database_url(postgres):
    harness.checkpointer()
    assert postgres == [
        SUPABASE_URL,
        True,
        "CREATE SCHEMA IF NOT EXISTS undercover",
        "setup",
    ]


def test_each_claim_run_gets_a_saver_of_its_own_on_one_pool(postgres):
    first, second = harness.checkpointer(), harness.checkpointer()
    assert first is not second
    assert postgres.count(SUPABASE_URL) == 1
    assert postgres.count("setup") == 1


def test_many_threads_at_once_open_one_pool(postgres):
    with ThreadPoolExecutor(25) as threads:
        list(threads.map(lambda _: harness.checkpointer(), range(25)))
    assert postgres.count(SUPABASE_URL) == 1


def test_the_work_retry_waits_5_10_and_20_seconds_with_jitter(monkeypatch):
    monkeypatch.undo()
    policy = harness.work_retry()
    assert policy.max_attempts == 4
    assert policy.initial_interval == 5
    assert policy.backoff_factor == 2
    assert policy.jitter
    assert policy.retry_on == harness.RETRIED_ERRORS


def test_importing_the_agent_loads_no_env_file(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    for name in [
        "agent.api",
        "agent.harness",
        "agent.tools",
        "agent.agents",
        "agent.dispatcher",
    ]:
        importlib.reload(importlib.import_module(name))
    assert "DATABASE_URL" not in os.environ
