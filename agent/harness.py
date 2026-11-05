"""The agent's harness, which knows nothing about claims: the model and its usage,
the tracing, the checkpointer, the retry policies, and steps for graph nodes."""

import os
from functools import cache
from threading import Lock
from typing import Any

import httpx
from langchain_core.exceptions import ModelRateLimitError, ModelTimeoutError
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
from langfuse import get_client
from langfuse.langchain import CallbackHandler
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.prebuilt.tool_node import ToolInvocationError
from langgraph.types import RetryPolicy
from psycopg import Connection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import ConnectionPool
from pydantic import SecretStr

from agent.api import Refused

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "openai/gpt-6-luna"
DEFAULT_EFFORT = "low"
NO_EFFORT = "none"
MODEL_RETRIES = 3
TOOL_RETRIES = 2
RETRY_DELAY = 1.0
WORK_RETRIES = 3
WORK_RETRY_DELAY = 5.0
WORK_RETRY_JITTER = True
RETRIED_ERRORS = (ModelRateLimitError, ModelTimeoutError)
SCHEMA = "undercover"
POOL_SIZE = 4
POOL_WAIT = 120

Step = tuple[str, str]
pool_lock = Lock()


def model_settings() -> tuple[str, str]:
    """Return the model's name and reasoning effort: the one in MODEL_NAME with no
    effort set, or GPT-6 Luna with low effort by default."""
    name = os.getenv("MODEL_NAME")
    return (name, NO_EFFORT) if name else (DEFAULT_MODEL, DEFAULT_EFFORT)


def model() -> ChatOpenAI:
    """Return the model in `model_settings`.

    The client doesn't retry, so that the harness's retries are the one policy.
    """
    name, effort = model_settings()
    return ChatOpenAI(
        model=name,
        base_url=OPENROUTER_BASE_URL,
        api_key=SecretStr(os.environ["OPENROUTER_API_KEY"]),
        reasoning_effort=None if effort == NO_EFFORT else effort,
        timeout=120,
        max_retries=0,
    )


def usage(reply: AIMessage) -> dict[str, Any]:
    """Return the tokens and the cost OpenRouter reported on a model's reply. A
    model that reports none, as a free one may, counts as zero."""
    reported = reply.response_metadata.get("token_usage") or {}
    return {
        "input_tokens": reported.get("prompt_tokens") or 0,
        "output_tokens": reported.get("completion_tokens") or 0,
        "cost": reported.get("cost") or 0.0,
    }


def tracing_on() -> bool:
    """Return whether the Langfuse keys are set."""
    return bool(os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"))


def thread_config(thread_id: str, tag: str | None = None) -> RunnableConfig:
    """Return the config that runs a graph on a thread, traced in Langfuse in a
    session named after the thread, with `tag` if it has one, when the Langfuse
    keys are set."""
    config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
    if tracing_on():
        config["callbacks"] = [CallbackHandler()]
        config["metadata"] = {
            "langfuse_session_id": thread_id,
            **({"langfuse_tags": [tag]} if tag else {}),
        }
    return config


def flush_traces() -> None:
    """Send any traces still waiting to Langfuse."""
    if tracing_on():
        get_client().flush()


def connection_pool(url: str) -> ConnectionPool[Connection[DictRow]]:
    """Return a pool, not yet opened, of at most POOL_SIZE connections to the
    database at `url`, in our schema, with the settings the checkpointer needs.
    Whoever needs a connection waits for one rather than fail."""
    return ConnectionPool[Connection[DictRow]](
        url,
        min_size=1,
        max_size=POOL_SIZE,
        timeout=POOL_WAIT,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
            "options": f"-c search_path={SCHEMA}",
        },
        open=False,
    )


@cache
def shared_pool(url: str) -> ConnectionPool[Connection[DictRow]]:
    """Return this process's pool, open, with the schema and the checkpointer's
    tables set up."""
    pool = connection_pool(url)
    pool.open(wait=True)
    with pool.connection() as conn:
        conn.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    PostgresSaver(pool).setup()
    return pool


@cache
def memory_saver() -> InMemorySaver:
    """Return this process's in-memory checkpointer."""
    return InMemorySaver()


def checkpointer() -> BaseCheckpointSaver:
    """Return a checkpointer: in Postgres when DATABASE_URL is set, and in memory
    otherwise. `PostgresSaver` holds a lock around every read and write, so each
    claim run gets a saver of its own on the shared pool, and the lock stops
    threads racing to open that pool, as `cache` alone wouldn't."""
    url = os.getenv("DATABASE_URL")
    if not url:
        return memory_saver()
    with pool_lock:
        pool = shared_pool(url)
    return PostgresSaver(pool)


def clear_thread(thread_id: str) -> None:
    """Delete everything saved on a thread."""
    checkpointer().delete_thread(thread_id)


class CallLimitReached(Exception):
    """A graph reached its limit of model calls."""


def model_retry() -> RetryPolicy:
    """Return the retry policy for a node that calls the model: any failure but
    the call limit is tried again."""
    return RetryPolicy(
        max_attempts=MODEL_RETRIES + 1,
        initial_interval=RETRY_DELAY,
        jitter=False,
        retry_on=lambda error: not isinstance(error, CallLimitReached),
    )


def tool_retry() -> RetryPolicy:
    """Return the retry policy for a node that runs tools: a call that can't reach
    the backend is tried again."""
    return RetryPolicy(
        max_attempts=TOOL_RETRIES + 1,
        initial_interval=RETRY_DELAY,
        jitter=False,
        retry_on=httpx.TransportError,
    )


def work_retry() -> RetryPolicy:
    """Return the retry policy for a node that does a whole stretch of work: a
    rate limit or a timeout waits, with jitter so that a swarm doesn't all come
    back at once, then carries on."""
    return RetryPolicy(
        max_attempts=WORK_RETRIES + 1,
        initial_interval=WORK_RETRY_DELAY,
        jitter=WORK_RETRY_JITTER,
        retry_on=RETRIED_ERRORS,
    )


def reply_steps(message: AIMessage) -> list[Step]:
    """Return a model's reply as steps: its text, then each tool call."""
    steps = [("reply", str(message.text))] if message.text else []
    for call in message.tool_calls:
        args = ", ".join(f"{name}={value!r}" for name, value in call["args"].items())
        steps.append(("tool_call", f"{call['name']}({args})"))
    return steps


def result_step(message: ToolMessage) -> Step:
    """Return a tool's result as a step. A tool records its result as a step of
    another kind by returning a (kind, text) artifact."""
    return message.artifact or ("tool_result", str(message.text))


def refusal(error: Refused) -> str:
    """Tell the model why a tool refused, so that it can carry on."""
    return f"Refused: {error}"


def tool_error(error: Refused | ToolInvocationError) -> str:
    """Tell the model about a refusal or a tool call with the wrong arguments. Any
    other error in a tool stops the run."""
    if isinstance(error, ToolInvocationError):
        return error.message
    return refusal(error)
