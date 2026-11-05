"""The claim agent's tools for working one claim."""

import json
from typing import Literal

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from agent import api
from agent.api import Benefit, Document

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


class Line(BaseModel):
    """One covered item or expense on a claim."""

    description: str = Field(description="What the item or expense is")
    amount: float = Field(
        description="The total claimed for the line in US dollars, before any "
        "limits. For several items, the total for all of them"
    )
    quantity: int = Field(
        description="How many separate items the line holds, such as 3 for three "
        "shirts. A pair or a set is one item. 1 for anything else"
    )
    kind: Literal[
        "item", "electronics", "cash", "expense", "hotel", "meals_without_receipts"
    ] = Field(
        description="item: a belonging, or a pair or set that counts as one item. "
        "electronics: only a phone, tablet, laptop, camera or its lens, e-reader or "
        "smartwatch; any other device is an item. "
        "cash: cash that was lost or stolen. "
        "expense: any other cost with a receipt, such as a medical bill, a meal, a "
        "taxi, a fee or a prepaid trip cost. "
        "hotel: a hotel during a trip delay. "
        "meals_without_receipts: meals during a trip delay with no receipts."
    )
    nights: int = Field(description="For a hotel, the number of nights; otherwise 0")
    hours: float = Field(
        description="For meals without receipts, the length of the delay in hours; "
        "otherwise 0"
    )


@tool(parse_docstring=True)
def calculate_payout(
    policy_id: str, benefit: Benefit, lines: list[Line], paid_by_others: float
) -> str:
    """Work out the payout on an approved claim with UnderCover's payout rules.

    Args:
        policy_id: The customer's policy id
        benefit: The benefit the claim is paid under
        lines: The covered items and expenses, one for each line of the claim,
            with any excluded ones left out. A pair or a set counts as one item
        paid_by_others: Anything a carrier, hotel or other party has paid or
            refunded for the same loss, or 0
    """
    return json.dumps(
        api.calculate_payout(
            policy_id, benefit, [line.model_dump() for line in lines], paid_by_others
        )
    )


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


CLAIM_TOOLS = [
    get_claim,
    get_policy,
    get_claim_history,
    list_sections,
    read_section,
    calculate_payout,
    record_decision,
]
