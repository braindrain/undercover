"""The middleware around the agents: step recording, retries and limits."""

from collections.abc import Callable
from typing import Any

import httpx
from langchain.agents.middleware import (
    AgentMiddleware,
    AgentState,
    ModelCallLimitMiddleware,
    ModelRetryMiddleware,
    ToolCallRequest,
    ToolRetryMiddleware,
)
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.messages.tool import ToolCall
from langgraph.runtime import Runtime
from langgraph.types import Command

from agent.tools import DOCUMENT_TITLES

MODEL_RETRIES = 3
TOOL_RETRIES = 2
RETRY_DELAY = 1.0

Record = Callable[[str, str], None]


def describe_reply(message: AIMessage) -> list[tuple[str, str]]:
    """Describe a reply from the model as steps: its text, then each tool call."""
    text = str(message.text)
    described = [("reply", text)] if text else []
    for call in message.tool_calls:
        args = ", ".join(f"{name}={value!r}" for name, value in call["args"].items())
        described.append(("tool_call", f"{call['name']}({args})"))
    return described


def describe_result(call: ToolCall, text: str) -> tuple[str, str]:
    """Describe a tool's result as a step, with a section read as a read step."""
    if call["name"] == "read_section" and text.startswith("## "):
        heading = text.partition("\n")[0].removeprefix("## ")
        title = DOCUMENT_TITLES[call["args"]["document"]]
        return "read", f"{title}: {heading}\n\n{text}"
    return "tool_result", text


class StepRecorder(AgentMiddleware):
    """Records each reply, tool call and tool result as a step on the claim."""

    def __init__(self, record: Record) -> None:
        super().__init__()
        self.record = record

    def after_model(self, state: AgentState, runtime: Runtime) -> None:
        message = state["messages"][-1]
        if isinstance(message, AIMessage):
            for kind, text in describe_reply(message):
                self.record(kind, text)

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        result = handler(request)
        if isinstance(result, ToolMessage):
            self.record(*describe_result(request.tool_call, str(result.text)))
        return result


def resilience(call_limit: int) -> list[AgentMiddleware[Any, Any, Any]]:
    """Retry failed model calls and tool calls that can't reach the backend, and
    stop an agent that goes past its limit of model calls."""
    return [
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
