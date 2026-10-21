"""The UnderCover Agent's queue agent, which runs the queue and hands each claim
to a fresh claim agent."""

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict
from uuid import uuid4

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, AgentState, hook_config
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool, tool
from langgraph.runtime import Runtime

from agent import main, tools
from agent.middleware import resilience


class TodoItem(TypedDict):
    text: str
    status: Literal["todo", "in_progress", "done", "left_for_handler"]


@dataclass
class Run:
    """One run over the queue, and the claims it has handed out so far."""

    run_id: int
    cap: int
    llm: BaseChatModel | None
    worked: set[str] = field(default_factory=set)


def call_limit(cap: int) -> int:
    """Return the queue agent's limit of model calls for a run with this cap."""
    return 10 + 5 * cap


def money(amount: float) -> str:
    """Format an amount in US dollars, such as $1,250.00."""
    return f"${amount:,.2f}"


def report(claim: dict, error: Exception | None) -> str:
    """Describe in one line where a claim stands after a claim agent worked it."""
    claim_id, status, decision = claim["claim_id"], claim["status"], claim["decision"]
    why = str(error).rstrip(".") if error else "the run ended without a decision"
    if status == "left_for_handler":
        return f"{claim_id}: failed twice ({why}), left for a handler."
    if status == "waiting":
        return f"{claim_id}: failed ({why}), back in the queue."
    if decision["decision"] == "decline":
        return f"{claim_id}: declined."
    if decision["decision"] == "refer":
        return f"{claim_id}: referred."
    payout = money(decision["payout"])
    if status == "awaiting_sign_off":
        return f"{claim_id}: approved, {payout}, waiting for sign-off."
    return (
        f"{claim_id}: approved, {payout}, {'paid' if claim['payment'] else 'not paid'}."
    )


def work(run: Run, claim_id: str) -> str:
    """Hand a waiting claim to a fresh claim agent within the run's cap, and
    report on it. A claim that fails is reported, and never stops the run."""
    with tools.backend() as client:
        response = client.get(f"/api/claims/{claim_id}")
    if not response.is_success:
        return tools.reply(response)
    status = response.json()["status"]
    if status != "waiting":
        state = status.replace("_", " ")
        return f"Refused: {claim_id} is {state}, so it can't be worked."
    if claim_id not in run.worked and len(run.worked) >= run.cap:
        return (
            f"Refused: this run is capped at {run.cap} claims "
            f"and has worked {len(run.worked)}."
        )
    run.worked.add(claim_id)
    error = None
    try:
        main.run(claim_id, str(uuid4()), run.llm)
    except Exception as failure:
        error = failure
    with tools.backend() as client:
        return report(client.get(f"/api/claims/{claim_id}").json(), error)


def queue_tools(run: Run) -> list[BaseTool]:
    """Return the queue agent's tools for one run."""

    @tool
    def list_waiting_claims() -> str:
        """List the claims waiting in the queue, and how much of this run's cap
        is left."""
        claims = tools.waiting_claims()
        lines = [
            f"{len(claims)} claims waiting. This run is capped at {run.cap} claims "
            f"and has worked {len(run.worked)}."
        ]
        for claim in claims:
            tried = (
                " (back in the queue after a failure)"
                if claim["claim_id"] in run.worked
                else ""
            )
            lines.append(
                f"{claim['claim_id']}, submitted {claim['submitted_on']}, "
                f"{claim['claim_type']}, {money(claim['amount_claimed'])} claimed: "
                f"{claim['summary']}{tried}"
            )
        return "\n".join(lines)

    @tool
    def write_todos(todos: list[TodoItem]) -> str:
        """Replace this run's to-do list with every item, in order. Each item is a
        line of text and a status: todo, in_progress, done or left_for_handler."""
        with tools.backend() as client:
            response = client.put(
                f"/api/runs/{run.run_id}/todos", json={"todos": todos}
            )
        return "Saved." if response.is_success else tools.reply(response)

    @tool
    def work_claim(claim_id: str) -> str:
        """Hand one waiting claim to a fresh claim agent, which works it from a
        clean start, and return its one-line report."""
        return work(run, claim_id)

    return [list_waiting_claims, write_todos, work_claim]


def missed_claims(run: Run) -> list[str]:
    """Return the waiting claims the run can still work: those it has tried that
    are back in the queue, and new ones up to its cap."""
    waiting = [claim["claim_id"] for claim in tools.waiting_claims()]
    tried = [claim_id for claim_id in waiting if claim_id in run.worked]
    untried = [claim_id for claim_id in waiting if claim_id not in run.worked]
    return tried + untried[: run.cap - len(run.worked)]


class MissedClaimCheck(AgentMiddleware):
    """Sends the queue agent back to work if it tries to finish while a claim it
    can still work is waiting."""

    def __init__(self, run: Run) -> None:
        super().__init__()
        self.run = run

    @hook_config(can_jump_to=["model"])
    def after_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        message = state["messages"][-1]
        if not isinstance(message, AIMessage) or message.tool_calls:
            return None
        missed = missed_claims(self.run)
        if not missed:
            return None
        verb = "is" if len(missed) == 1 else "are"
        reminder = (
            f"Before you finish: {', '.join(missed)} {verb} still waiting and within "
            "this run's cap. Work them and keep your to-do list up to date, then "
            "finish with your summary."
        )
        return {"messages": [HumanMessage(reminder)], "jump_to": "model"}


def run_queue(
    cap: int, thread_id: str, llm: BaseChatModel | None = None
) -> Iterator[str]:
    """Run the queue agent over at most `cap` claims, yielding each claim's report
    as it comes and then the summary, which also finishes the run."""
    with tools.backend() as client:
        instructions = client.get("/api/instructions/queue").text
        started = client.post("/api/runs", json={"cap": cap}).raise_for_status()
    run = Run(started.json()["run_id"], cap, llm)
    agent = create_agent(
        llm or main.model(),
        tools=queue_tools(run),
        system_prompt=instructions,
        middleware=[MissedClaimCheck(run), *resilience(call_limit(cap))],
    )
    task = {
        "role": "user",
        "content": f"Work the waiting claims. This run is capped at {cap} claims.",
    }
    summary = ""
    try:
        for update in agent.stream(
            {"messages": [task]},
            config={"configurable": {"thread_id": thread_id}},
            stream_mode="updates",
        ):
            for node in update.values():
                for message in (node or {}).get("messages", []):
                    if (
                        isinstance(message, ToolMessage)
                        and message.name == "work_claim"
                    ):
                        yield str(message.text)
                    elif isinstance(message, AIMessage):
                        summary = str(message.text)
    except Exception as error:
        summary = f"The run stopped: {error}"
    summary = summary or "The run finished without a summary."
    with tools.backend() as client:
        client.post(f"/api/runs/{run.run_id}/finish", json={"summary": summary})
    yield summary
