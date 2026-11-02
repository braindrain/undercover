"""The UnderCover Agent: a queue agent that runs the queue, and a claim agent, a
LangGraph graph that works one claim on its own thread, waits for a handler's
sign-off when the backend says so, pays an approval, and carries on after a stop."""

from collections.abc import Iterator
from dataclasses import dataclass
from functools import partial
from typing import Annotated, Any, TypedDict

from langchain.agents import create_agent
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
from agent.tools import CLAIM_TOOLS, QUEUE_TOOLS, RunContext, report

CLAIM_CALL_LIMIT = 30
STOPPED_RUN = (
    "This run was stopped before it finished. The next run carried on with the "
    "claims it had in progress."
)


class ClaimState(TypedDict):
    """What the claim graph keeps for one claim."""

    messages: Annotated[list[AnyMessage], add_messages]
    claim_id: str
    model_calls: int
    status: str
    payout: float
    paid: bool
    signed_off_by: str


def queue_call_limit(cap: int) -> int:
    """Return the queue agent's limit of model calls for a run with this cap."""
    return 10 + 5 * cap


@dataclass
class ClaimContext:
    """What every node of the claim graph shares during one run: the model with
    the claim tools, and the claim instructions."""

    model: Runnable[LanguageModelInput, AIMessage]
    instructions: SystemMessage


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
    reply = context.model.invoke([context.instructions, *state["messages"]])
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


def run_thread(claim: dict, start: Any, llm: BaseChatModel | None) -> str:
    """Run the claim graph on the claim's thread from `start` and return the
    agent's last reply. A claim that fails with an error, or ends without a
    decision, is released, which counts a failure on it; a stop leaves it as is."""
    claim_id = claim["claim_id"]
    context = ClaimContext(
        model=(llm or harness.model()).bind_tools(CLAIM_TOOLS),
        instructions=SystemMessage(api.get_instructions("claim")),
    )
    config = harness.thread_config(claim["thread_id"])
    try:
        result = claim_graph().invoke(start, config, context=context, durability="sync")
    except Exception as error:
        api.record_step(claim_id, "error", f"The agent stopped: {error}")
        api.release_claim(claim_id)
        raise
    if api.get_claim(claim_id)["decision"] is None:
        api.release_claim(claim_id)
    return str(result["messages"][-1].text)


def work_claim(claim_id: str, llm: BaseChatModel | None = None) -> str:
    """Start a waiting claim on a new thread and work it from a clean start."""
    claim = api.start_claim(claim_id)
    task = HumanMessage(f"Decide claim {claim_id}.")
    start = {"messages": [task], "claim_id": claim_id, "model_calls": 0}
    return run_thread(claim, start, llm)


def carry_on(claim: dict, llm: BaseChatModel | None = None) -> str:
    """Carry on a claim in progress from where its thread got to: with the
    handler's sign-off if it was waiting for one, from its last step if it was
    stopped, or from the start if it was stopped before its first step."""
    saved = claim_graph().get_state(harness.thread_config(claim["thread_id"]))
    if saved.next == ("sign_off",):
        if claim["sign_off"] is None:
            return f"{claim['claim_id']} is waiting for a handler's sign-off."
        start: Any = Command(resume={"handler_id": claim["sign_off"]["handler_id"]})
    elif saved.next:
        start = None
    elif not saved.values:
        task = HumanMessage(f"Decide claim {claim['claim_id']}.")
        start = {"messages": [task], "claim_id": claim["claim_id"], "model_calls": 0}
    else:
        return f"{claim['claim_id']} has nothing left to do."
    return run_thread(claim, start, llm)


def carry_on_stopped(llm: BaseChatModel | None = None) -> Iterator[str]:
    """Carry on every claim in progress, yielding each one's report, then finish
    the latest run if it was stopped while still running."""
    for claim in api.claims_in_progress():
        error = None
        try:
            carry_on(claim, llm)
        except Exception as failure:
            error = failure
        yield report(api.get_claim(claim["claim_id"]), error)
    latest = api.get_latest_run()
    if latest and latest["status"] == "running":
        api.finish_run(latest["run_id"], STOPPED_RUN)


def missed_claims(run: RunContext) -> str | None:
    """Remind the queue agent of the waiting claims it can still work, if any:
    those it tried that are back in the queue, and new ones within its cap."""
    waiting = [claim["claim_id"] for claim in api.waiting_claims()]
    tried = [claim_id for claim_id in waiting if claim_id in run.worked]
    untried = [claim_id for claim_id in waiting if claim_id not in run.worked]
    missed = tried + untried[: run.cap - len(run.worked)]
    if not missed:
        return None
    verb = "is" if len(missed) == 1 else "are"
    return (
        f"Before you finish: {', '.join(missed)} {verb} still waiting and within "
        "this run's cap. Work them and keep your to-do list up to date, then "
        "finish with your summary."
    )


def run_queue(cap: int, llm: BaseChatModel | None = None) -> Iterator[str]:
    """Carry on the claims in progress, then run the queue agent over at most
    `cap` claims, yielding each claim's report as it comes and then the summary,
    which also finishes the run."""
    llm = llm or harness.model()
    yield from carry_on_stopped(llm)
    run = RunContext(api.start_run(cap), cap, partial(work_claim, llm=llm))
    agent = create_agent(
        llm,
        tools=QUEUE_TOOLS,
        system_prompt=api.get_instructions("queue"),
        context_schema=RunContext,
        middleware=[
            harness.FinishCheck(missed_claims),
            *harness.middleware(queue_call_limit(cap)),
        ],
    )
    task = {"role": "user", "content": f"Work the queue, capped at {cap} claims."}
    updates = agent.stream({"messages": [task]}, context=run, stream_mode="updates")
    summary = ""
    try:
        for update in updates:
            for message in update.get("tools", {}).get("messages", []):
                if message.name == "work_claim":
                    yield str(message.text)
            for message in update.get("model", {}).get("messages", []):
                summary = str(message.text)
    except Exception as error:
        summary = f"The run stopped: {error}"
    summary = summary or "The run finished without a summary."
    api.finish_run(run.run_id, summary)
    yield summary
