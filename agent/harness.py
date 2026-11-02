"""The agents' harness: the model, the tracing, the checkpointer, the retry policies
and steps for graph nodes, and the middleware the queue agent runs inside."""

import os
from collections.abc import Callable
from functools import cache
from typing import Any

import httpx
from langchain.agents.middleware import (
    AgentMiddleware,
    AgentState,
    ModelCallLimitMiddleware,
    ModelRetryMiddleware,
    ToolCallRequest,
    ToolRetryMiddleware,
    hook_config,
)
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
from langfuse import get_client
from langfuse.langchain import CallbackHandler
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.prebuilt.tool_node import ToolInvocationError
from langgraph.runtime import Runtime
from langgraph.types import Command, RetryPolicy
from psycopg import Connection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import ConnectionPool
from pydantic import SecretStr

from agent.api import Refused

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash"
MODEL_RETRIES = 3
TOOL_RETRIES = 2
RETRY_DELAY = 1.0
SCHEMA = "undercover"
POOL_SIZE = 4

ToolHandler = Callable[[ToolCallRequest], ToolMessage | Command[Any]]
Step = tuple[str, str]


def model() -> ChatOpenAI:
    """Return the model named in MODEL_NAME, or DeepSeek V4.1 Flash by default.

    The client doesn't retry, so that the harness's retries are the one policy.
    """
    name = os.getenv("MODEL_NAME")
    return ChatOpenAI(
        model=name or DEFAULT_MODEL,
        base_url=OPENROUTER_BASE_URL,
        api_key=SecretStr(os.environ["OPENROUTER_API_KEY"]),
        reasoning_effort=None if name else "low",
        timeout=120,
        max_retries=0,
    )


def tracing_on() -> bool:
    """Return whether the Langfuse keys are set."""
    return bool(os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"))


def thread_config(thread_id: str) -> RunnableConfig:
    """Return the config that runs a graph on a thread, traced in Langfuse in a
    session named after the thread when the Langfuse keys are set."""
    config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
    if tracing_on():
        config["callbacks"] = [CallbackHandler()]
        config["metadata"] = {"langfuse_session_id": thread_id}
    return config


def flush_traces() -> None:
    """Send any traces still waiting to Langfuse."""
    if tracing_on():
        get_client().flush()


def connection_pool(url: str) -> ConnectionPool[Connection[DictRow]]:
    """Return a pool, not yet opened, of at most POOL_SIZE connections to the
    database at `url`, in our schema, with the settings the checkpointer needs."""
    return ConnectionPool[Connection[DictRow]](
        url,
        min_size=1,
        max_size=POOL_SIZE,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
            "options": f"-c search_path={SCHEMA}",
        },
        open=False,
    )


@cache
def checkpointer() -> BaseCheckpointSaver:
    """Return this process's checkpointer: in Postgres when DATABASE_URL is set,
    with its schema and tables set up, and in memory otherwise."""
    url = os.getenv("DATABASE_URL")
    if not url:
        return InMemorySaver()
    pool = connection_pool(url)
    pool.open(wait=True)
    with pool.connection() as conn:
        conn.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    saver = PostgresSaver(pool)
    saver.setup()
    return saver


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


class Refusals(AgentMiddleware):
    """Tells the model why a tool refused, so that it can carry on."""

    def wrap_tool_call(
        self, request: ToolCallRequest, handler: ToolHandler
    ) -> ToolMessage | Command[Any]:
        try:
            return handler(request)
        except Refused as error:
            call = request.tool_call
            return ToolMessage(
                refusal(error), tool_call_id=call["id"], name=call["name"]
            )


class FinishCheck(AgentMiddleware):
    """Sends the agent back to work when it tries to finish and `check` returns a
    reminder for it."""

    def __init__(self, check: Callable[[Any], str | None]) -> None:
        super().__init__()
        self.check = check

    @hook_config(can_jump_to=["model"])
    def after_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        message = state["messages"][-1]
        if not isinstance(message, AIMessage) or message.tool_calls:
            return None
        reminder = self.check(runtime.context)
        if reminder is None:
            return None
        return {"messages": [HumanMessage(reminder)], "jump_to": "model"}


def middleware(call_limit: int) -> list[AgentMiddleware[Any, Any, Any]]:
    """Return the middleware the queue agent runs inside: refusals passed to the
    model, failed model calls and tool calls that can't reach the backend retried,
    and a stop at `call_limit` model calls."""
    return [
        Refusals(),
        ModelRetryMiddleware(
            max_retries=MODEL_RETRIES, on_failure="error", initial_delay=RETRY_DELAY
        ),
        ToolRetryMiddleware(
            max_retries=TOOL_RETRIES,
            retry_on=(httpx.TransportError,),
            initial_delay=RETRY_DELAY,
        ),
        ModelCallLimitMiddleware(run_limit=call_limit, exit_behavior="error"),
    ]
