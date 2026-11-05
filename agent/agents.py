"""The UnderCover Agent's claim agent: a LangGraph graph that works one claim on its
own thread, waits for a handler's sign-off when the backend says so, pays an
approval, and carries on after a stop."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import Context
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock
from time import monotonic
from typing import Annotated, Any, TypedDict

from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langchain_core.runnables import Runnable
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode
from langgraph.runtime import Runtime
from langgraph.types import Command, interrupt

from agent import api, harness
from agent.tools import CLAIM_TOOLS

CLAIM_CALL_LIMIT = 30

working: set[str] = set()
working_lock = Lock()


class NoDecision(Exception):
    """The claim agent finished without deciding the claim."""


class ClaimState(TypedDict):
    """What the claim graph keeps for one claim."""

    messages: Annotated[list[AnyMessage], add_messages]
    claim_id: str
    model_calls: int
    status: str
    payout: float
    paid: bool
    signed_off_by: str


@dataclass
class ClaimContext:
    """What every node of the claim graph shares during one stretch of work: the
    model with the claim tools, the claim instructions, and the dispatch or
    evaluation it belongs to, if any. It's kept out of the graph's state, which is
    saved, so that a claim carried on in a later run charges that run."""

    model: Runnable[LanguageModelInput, AIMessage]
    instructions: SystemMessage
    run_id: str | None


TOOL_NODE = ToolNode(CLAIM_TOOLS, handle_tool_errors=harness.tool_error)


def record(claim_id: str, steps: list[harness.Step]) -> None:
    """Record steps on a claim."""
    for kind, text in steps:
        api.record_step(claim_id, kind, text)


def agent(state: ClaimState, runtime: Runtime[ClaimContext]) -> dict[str, Any]:
    """Call the model with the conversation so far, within the call limit."""
    if state["model_calls"] >= CLAIM_CALL_LIMIT:
        raise harness.CallLimitReached(
            f"The claim agent reached its limit of {CLAIM_CALL_LIMIT} model calls."
        )
    context = runtime.context
    started = monotonic()
    reply = context.model.invoke([context.instructions, *state["messages"]])
    api.record_model_call(
        state["claim_id"], context.run_id, harness.usage(reply), monotonic() - started
    )
    record(state["claim_id"], harness.reply_steps(reply))
    return {"messages": [reply], "model_calls": state["model_calls"] + 1}


def tools(state: ClaimState) -> dict[str, Any]:
    """Run the tools the model asked for, and record each result."""
    result = TOOL_NODE.invoke(state)
    record(state["claim_id"], [harness.result_step(m) for m in result["messages"]])
    return result


def outcome(state: ClaimState) -> dict[str, Any]:
    """Read where the claim stands from the backend."""
    claim = api.get_claim(state["claim_id"])
    decision = claim["decision"]
    return {
        "status": claim["status"],
        "payout": decision["payout"] if decision else 0.0,
        "paid": claim["payment"] is not None,
    }


def sign_off(state: ClaimState) -> dict[str, Any]:
    """Wait for a handler to sign off the payout."""
    answer = interrupt({"claim_id": state["claim_id"], "payout": state["payout"]})
    return {"signed_off_by": answer["handler_id"]}


def pay(state: ClaimState) -> None:
    """Pay the approval. A repeat after a restart counts as done."""
    api.pay_claim(state["claim_id"], state["payout"])


def after_agent(state: ClaimState) -> str:
    """Run the tools the model asked for, or see where the claim stands."""
    message = state["messages"][-1]
    calls = isinstance(message, AIMessage) and message.tool_calls
    return "tools" if calls else "outcome"


def after_outcome(state: ClaimState) -> str:
    """Wait for a sign-off when the backend says so, and pay an approval."""
    if state["status"] == "awaiting_sign_off":
        return "sign_off"
    if state["status"] == "in_progress" and state["payout"] and not state["paid"]:
        return "pay"
    return END


def claim_graph() -> CompiledStateGraph[ClaimState, ClaimContext]:
    """Build the claim agent's graph on this process's checkpointer."""
    builder = StateGraph(ClaimState, context_schema=ClaimContext)
    builder.add_node("agent", agent, retry_policy=harness.model_retry())
    builder.add_node("tools", tools, retry_policy=harness.tool_retry())
    builder.add_node("outcome", outcome)
    builder.add_node("sign_off", sign_off)
    builder.add_node("pay", pay)

    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", after_agent, ["tools", "outcome"])
    builder.add_edge("tools", "agent")
    builder.add_conditional_edges("outcome", after_outcome, ["sign_off", "pay", END])
    builder.add_edge("sign_off", "pay")
    builder.add_edge("pay", END)
    return builder.compile(checkpointer=harness.checkpointer())


def time_now() -> str:
    """Return the time now in UTC, for a record of work."""
    return datetime.now(UTC).isoformat(timespec="microseconds")


def run_thread(
    claim: dict, start: Any, llm: BaseChatModel | None, run_id: str | None
) -> str:
    """Run the claim graph on the claim's thread from `start` and return the
    agent's last reply, recording the stretch of work, so that a wait for a
    sign-off between two stretches is never counted. A rate limit or a timeout
    goes through as it is, for a retry to carry the claim on. Any other error,
    or an end without a decision, releases the claim, which counts a failure on
    it. A stop leaves it as it is."""
    claim_id = claim["claim_id"]
    context = ClaimContext(
        model=(llm or harness.model()).bind_tools(CLAIM_TOOLS),
        instructions=SystemMessage(api.get_instructions()),
        run_id=run_id,
    )
    config = harness.thread_config(claim["thread_id"], run_id)
    started_at, started = time_now(), monotonic()
    try:
        # A fresh context makes each claim a trace of its own in Langfuse, with
        # its session, even inside the dispatcher's graph.
        result = Context().run(
            claim_graph().invoke, start, config, context=context, durability="sync"
        )
        if api.get_claim(claim_id)["decision"] is None:
            raise NoDecision(f"{claim_id} ended without a decision.")
    except Exception as error:
        api.record_step(claim_id, "error", f"The agent stopped: {error}")
        if not isinstance(error, harness.RETRIED_ERRORS):
            api.release_claim(claim_id)
        raise
    finally:
        api.record_work(claim_id, run_id, started_at, time_now(), monotonic() - started)
    return str(result["messages"][-1].text)


@contextmanager
def holding(claim_id: str) -> Iterator[bool]:
    """Hold a claim while this process works it, yielding False if it's already
    held, so that a dispatch and a sign-off never work one claim at once."""
    with working_lock:
        free = claim_id not in working
        working.add(claim_id)
    try:
        yield free
    finally:
        if free:
            with working_lock:
                working.discard(claim_id)


def work_claim(
    claim_id: str, llm: BaseChatModel | None = None, run_id: str | None = None
) -> str:
    """Start a waiting claim on a new thread and work it from a clean start."""
    with holding(claim_id) as free:
        if not free:
            return f"{claim_id} is already being worked."
        claim = api.start_claim(claim_id)
        task = HumanMessage(f"Decide claim {claim_id}.")
        start = {"messages": [task], "claim_id": claim_id, "model_calls": 0}
        return run_thread(claim, start, llm, run_id)


def carry_on(
    claim_id: str, llm: BaseChatModel | None = None, run_id: str | None = None
) -> str:
    """Carry on a claim in progress from where its thread got to: with the
    handler's sign-off if it was waiting for one, from its last step if it was
    stopped, or from the start if it was stopped before its first step."""
    with holding(claim_id) as free:
        if not free:
            return f"{claim_id} is already being worked."
        claim = api.get_claim(claim_id)
        saved = claim_graph().get_state(harness.thread_config(claim["thread_id"]))
        if saved.next == ("sign_off",):
            if claim["sign_off"] is None:
                return f"{claim_id} is waiting for a handler's sign-off."
            start: Any = Command(resume={"handler_id": claim["sign_off"]["handler_id"]})
        elif saved.next:
            start = None
        elif not saved.values:
            task = HumanMessage(f"Decide claim {claim_id}.")
            start = {"messages": [task], "claim_id": claim_id, "model_calls": 0}
        else:
            return f"{claim_id} has nothing left to do."
        return run_thread(claim, start, llm, run_id)


def work(claim_id: str, run: dict) -> str:
    """Work one claim of a run: the only way the dispatcher reaches the claim
    agent. A waiting claim starts, a claim in progress carries on, and any other
    has nothing to do. An evaluation's copy can't be signed off, so an approval
    held for one counts as finished, and its saved progress is cleared."""
    status = api.get_claim(claim_id)["status"]
    if status == "waiting":
        result = work_claim(claim_id, run_id=run["run_id"])
    elif status == "in_progress":
        result = carry_on(claim_id, run_id=run["run_id"])
    else:
        return f"{claim_id} is {status}, so there's nothing to do."
    if run["kind"] == "evaluation":
        harness.clear_thread(api.get_claim(claim_id)["thread_id"])
    return result
