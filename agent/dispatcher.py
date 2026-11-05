"""The dispatcher: a LangGraph graph with no model that works a run's claims, with
up to N claim agents at once. It saves no progress of its own: each claim saves
its own on its own thread, and the next dispatch finishes what a stopped one left."""

from typing import Any, TypedDict, cast

from langgraph.errors import NodeError
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, Send

from agent import agents, api, harness


class RunState(TypedDict):
    """What the dispatcher keeps for one run."""

    run: dict
    claims: list[str]


class ClaimTask(TypedDict):
    """One claim for the worker, and whether this is its second try."""

    claim_id: str
    run: dict
    retried: bool


def list_claims(state: RunState) -> dict[str, list[str]]:
    """List the claims the backend chose for this run."""
    return {"claims": api.list_run_claims(state["run"]["run_id"])}


def fan_out(state: RunState) -> list[Send]:
    """Send each claim to a worker of its own."""
    return [
        Send(
            "work_claim", ClaimTask(claim_id=claim_id, run=state["run"], retried=False)
        )
        for claim_id in state["claims"]
    ]


def work_claim(state: ClaimTask) -> None:
    """Work one claim."""
    agents.work(state["claim_id"], state["run"])


def claim_failed(state: ClaimTask, error: NodeError) -> Command | None:
    """Keep the dispatch going when a claim's work fails. A rate limit or a timeout
    leaves the claim in progress for the next dispatch, and any other error sends
    it back for one more try. This asks the backend nothing, because an error here
    would stop the run: a claim already left for a handler has nothing to do."""
    if isinstance(error.error, harness.RETRIED_ERRORS) or state["retried"]:
        return None
    return Command(goto=[Send("work_claim", {**state, "retried": True})])


def run_graph() -> CompiledStateGraph:
    """Build the dispatcher's graph, with no checkpointer."""
    builder = StateGraph(RunState)
    builder.add_node("list_claims", list_claims)
    builder.add_node(
        "work_claim",
        work_claim,
        retry_policy=harness.work_retry(),
        # LangGraph documents a handler with a NodeError, but its types leave it out.
        error_handler=cast("Any", claim_failed),
        destinations=("work_claim",),
    )
    builder.add_edge(START, "list_claims")
    builder.add_conditional_edges("list_claims", fan_out, ["work_claim"])
    builder.add_edge("work_claim", END)
    return builder.compile()


def work_run(run: dict) -> None:
    """Work a run's claims and finish it. A run that fails outright is stopped."""
    state = RunState(run=run, claims=[])
    try:
        run_graph().invoke(state, {"max_concurrency": run["agents_at_once"]})
    except Exception:
        api.finish_run(run["run_id"], "stopped")
        raise
    api.finish_run(run["run_id"], "finished")
    harness.flush_traces()


def dispatch(agents_at_once: int) -> None:
    """Create a dispatch through the backend and work it."""
    work_run(api.create_dispatch(agents_at_once, *harness.model_settings()))
