"""The backend client and the claim agent's tools."""

from typing import Literal

import httpx
from langchain_core.tools import tool

BACKEND_URL = "http://localhost:8000"

Document = Literal["policy_wording", "claims_manual"]
DOCUMENT_TITLES: dict[str, str] = {
    "policy_wording": "Policy wording",
    "claims_manual": "Claims manual",
}


def backend() -> httpx.Client:
    """Return a client for the UnderCover backend."""
    return httpx.Client(base_url=BACKEND_URL, timeout=30)


def reply(response: httpx.Response) -> str:
    """Return the backend's reply, or its reason for refusing."""
    if response.is_success:
        return response.text
    return f"Refused: {response.json()['detail']}"


def waiting_claims() -> list[dict]:
    """Return the claims waiting in the queue, as the queue lists them."""
    with backend() as client:
        claims = client.get("/api/claims").json()
    return [claim for claim in claims if claim["status"] == "waiting"]


@tool
def get_claim(claim_id: str) -> str:
    """Look up a claim: the story, the items claimed and the documents."""
    with backend() as client:
        return reply(client.get(f"/api/claims/{claim_id}"))


@tool
def get_policy(policy_id: str) -> str:
    """Look up a policy: the plan, the add-ons, the trip and the travelers."""
    with backend() as client:
        return reply(client.get(f"/api/policies/{policy_id}"))


@tool
def get_claim_history(customer_id: str) -> str:
    """Look up a customer's earlier claims."""
    with backend() as client:
        return reply(client.get(f"/api/customers/{customer_id}/claims"))


@tool
def list_sections(document: Document) -> str:
    """List a document's contents: its title, its opening lines with its version,
    and its numbered sections."""
    with backend() as client:
        return reply(client.get(f"/api/documents/{document}/sections"))


@tool
def read_section(document: Document, number: int) -> str:
    """Read one numbered section of the policy wording or the claims manual."""
    with backend() as client:
        return reply(client.get(f"/api/documents/{document}/sections/{number}"))


@tool
def record_decision(
    claim_id: str,
    decision: Literal["approve", "decline", "refer"],
    payout: float,
    reasons: str,
    policy_section: str,
) -> str:
    """Record the decision on a claim, with the payout (0 unless approved), the
    reasons in two or three sentences and the relevant section of the policy."""
    with backend() as client:
        return reply(
            client.post(
                f"/api/claims/{claim_id}/decision",
                json={
                    "decision": decision,
                    "payout": payout,
                    "reasons": reasons,
                    "policy_section": policy_section,
                },
            )
        )


@tool
def pay_claim(claim_id: str, amount: float) -> str:
    """Pay an approved claim its payout."""
    with backend() as client:
        return reply(
            client.post(f"/api/claims/{claim_id}/payments", json={"amount": amount})
        )


CLAIM_TOOLS = [
    get_claim,
    get_policy,
    get_claim_history,
    list_sections,
    read_section,
    record_decision,
    pay_claim,
]
