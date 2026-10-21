"""The agents' tools: the claim agent's tools for working one claim, and the queue
agent's tools for running the queue."""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, TypedDict

from langchain.tools import ToolRuntime
from langchain_core.tools import tool

from agent import api
from agent.api import Document, Refused

DOCUMENT_TITLES: dict[Document, str] = {
    "policy_wording": "Policy wording",
    "claims_manual": "Claims manual",
}


@tool
def get_claim(claim_id: str) -> str:
    """Look up a claim: the story, the items claimed and the documents."""
    return json.dumps(api.get_claim(claim_id))


@tool
def get_policy(policy_id: str) -> str:
    """Look up a policy: the plan, the add-ons, the trip and the travelers."""
    return json.dumps(api.get_policy(policy_id))


@tool
def get_claim_history(customer_id: str) -> str:
    """Look up a customer's earlier claims."""
    return json.dumps(api.get_claim_history(customer_id))


@tool
def list_sections(document: Document) -> str:
    """List a document's contents: its title, its opening lines with its version,
    and its numbered sections."""
    return json.dumps(api.list_sections(document))


@tool(response_format="content_and_artifact")
def read_section(document: Document, number: int) -> tuple[str, tuple[str, str]]:
    """Read one numbered section of the policy wording or the claims manual."""
    section = api.read_section(document, number)
    heading = section.partition("\n")[0].removeprefix("## ")
    return section, ("read", f"{DOCUMENT_TITLES[document]}: {heading}\n\n{section}")


@tool
def record_decision(
    claim_id: str,
    decision: Literal["approve", "decline", "refer"],
    payout: float,
    reasons: str,
    policy_section: str,
) -> str:
    """Record the decision on a claim, once. The payout is 0 unless approved. The
    reasons are two or three plain sentences for the handlers, and the policy
    section is the one the decision relies on, such as "Section 3, Trip Delay"."""
    return json.dumps(
        api.record_decision(claim_id, decision, payout, reasons, policy_section)
    )


@tool
def pay_claim(claim_id: str, amount: float) -> str:
    """Pay an approved claim its payout. A payout over the approval limit is
    refused until a handler signs it off, which pays it."""
    return json.dumps(api.pay_claim(claim_id, amount))


CLAIM_TOOLS = [
    get_claim,
    get_policy,
    get_claim_history,
    list_sections,
    read_section,
    record_decision,
    pay_claim,
]


class TodoItem(TypedDict):
    text: str
    status: Literal["todo", "in_progress", "done", "left_for_handler"]


@dataclass
class RunContext:
    """One run over the queue: its cap, the claim agent each claim is handed to,
    and the claims handed out so far."""

    run_id: int
    cap: int
    claim_agent: Callable[[str], object]
    worked: set[str] = field(default_factory=set)


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


@tool
def list_waiting_claims(runtime: ToolRuntime[RunContext]) -> str:
    """List the claims waiting in the queue, and how much of this run's cap is
    left."""
    run = runtime.context
    claims = api.waiting_claims()
    lines = [
        f"{len(claims)} claims waiting. This run is capped at {run.cap} claims "
        f"and has worked {len(run.worked)}."
    ]
    for claim in claims:
        retry = (
            " (back in the queue after a failure)"
            if claim["claim_id"] in run.worked
            else ""
        )
        lines.append(
            f"{claim['claim_id']}, submitted {claim['submitted_on']}, "
            f"{claim['claim_type']}, {money(claim['amount_claimed'])} claimed: "
            f"{claim['summary']}{retry}"
        )
    return "\n".join(lines)


@tool
def write_todos(todos: list[TodoItem], runtime: ToolRuntime[RunContext]) -> str:
    """Replace this run's to-do list, which the claims team watches, with every
    item in order. An item is todo, in_progress, done or left_for_handler."""
    api.set_todos(runtime.context.run_id, [dict(todo) for todo in todos])
    return "Saved."


@tool
def work_claim(claim_id: str, runtime: ToolRuntime[RunContext]) -> str:
    """Hand one waiting claim to a fresh claim agent, which works it from a clean
    start, and return its one-line report. A claim that fails goes back in the
    queue to be tried again, and one that fails twice is left for a handler.
    Trying a claim again doesn't count against the cap."""
    run = runtime.context
    status = api.get_claim(claim_id)["status"]
    if status != "waiting":
        raise Refused(
            f"{claim_id} is {status.replace('_', ' ')}, so it can't be worked."
        )
    if claim_id not in run.worked and len(run.worked) >= run.cap:
        raise Refused(
            f"this run is capped at {run.cap} claims and has worked {len(run.worked)}."
        )
    run.worked.add(claim_id)
    error = None
    try:
        run.claim_agent(claim_id)
    except Exception as failure:
        error = failure
    return report(api.get_claim(claim_id), error)


QUEUE_TOOLS = [list_waiting_claims, write_todos, work_claim]
