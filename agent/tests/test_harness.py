import importlib
import os

from langfuse.langchain import CallbackHandler

from agent import harness


def test_no_tracing_without_the_langfuse_keys():
    assert harness.tracing("CLM-1001-3f9a2c1d") == {}
    harness.flush_traces()


def test_tracing_puts_a_claim_thread_in_one_session(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    config = harness.tracing("CLM-1001-3f9a2c1d")
    callbacks = config.get("callbacks")
    assert isinstance(callbacks, list)
    assert [type(handler) for handler in callbacks] == [CallbackHandler]
    assert config.get("metadata") == {"langfuse_session_id": "CLM-1001-3f9a2c1d"}


def test_importing_the_agent_loads_no_env_file(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    for name in ["agent.api", "agent.harness", "agent.tools", "agent.agents"]:
        importlib.reload(importlib.import_module(name))
    assert "DATABASE_URL" not in os.environ
