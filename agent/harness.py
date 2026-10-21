"""The agents' harness: the model, and the middleware every agent runs inside."""

import os
from collections.abc import Callable
from typing import Any

import httpx
from dotenv import load_dotenv
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
from langchain_openai import ChatOpenAI
from langgraph.runtime import Runtime
from langgraph.types import Command
from pydantic import SecretStr

from agent.api import Refused

load_dotenv(override=True)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash"
MODEL_RETRIES = 3
TOOL_RETRIES = 2
RETRY_DELAY = 1.0

ToolHandler = Callable[[ToolCallRequest], ToolMessage | Command[Any]]


def model() -> ChatOpenAI:
    """Return the model named in MODEL_NAME, or DeepSeek V4.1 Flash by default.

    The client doesn't retry, so that ModelRetryMiddleware is the one retry policy.
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


class StepRecorder(AgentMiddleware):
    """Records each reply, tool call and tool result as a step. A tool records its
    result as a step of another kind by returning a (kind, text) artifact."""

    def __init__(self, record: Callable[[str, str], None]) -> None:
        super().__init__()
        self.record = record

    def after_model(self, state: AgentState, runtime: Runtime) -> None:
        message = state["messages"][-1]
        if not isinstance(message, AIMessage):
            return
        if message.text:
            self.record("reply", str(message.text))
        for call in message.tool_calls:
            args = ", ".join(
                f"{name}={value!r}" for name, value in call["args"].items()
            )
            self.record("tool_call", f"{call['name']}({args})")

    def wrap_tool_call(
        self, request: ToolCallRequest, handler: ToolHandler
    ) -> ToolMessage | Command[Any]:
        result = handler(request)
        if isinstance(result, ToolMessage):
            self.record(*(result.artifact or ("tool_result", str(result.text))))
        return result


class Refusals(AgentMiddleware):
    """Tells the model why a tool refused, so that it can carry on."""

    def wrap_tool_call(
        self, request: ToolCallRequest, handler: ToolHandler
    ) -> ToolMessage | Command[Any]:
        try:
            return handler(request)
        except Refused as refusal:
            call = request.tool_call
            return ToolMessage(
                f"Refused: {refusal}", tool_call_id=call["id"], name=call["name"]
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
    """Return the middleware every agent runs inside: refusals passed to the model,
    failed model calls and tool calls that can't reach the backend retried, and a
    stop at `call_limit` model calls."""
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
