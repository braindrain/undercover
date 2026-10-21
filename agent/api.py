"""The UnderCover backend's API: every call the agent makes to the backend."""

from typing import Any, Literal

import httpx

BACKEND_URL = "http://localhost:8000"

Document = Literal["policy_wording", "claims_manual"]


class Refused(Exception):
    """The backend refused a request, with its reason."""


def client() -> httpx.Client:
    """Return a client for the backend."""
    return httpx.Client(base_url=BACKEND_URL, timeout=30)


def request(method: str, path: str, **kwargs: Any) -> httpx.Response:
    """Make one request, raising Refused with the backend's reason if it refuses."""
    with client() as http:
        response = http.request(method, path, **kwargs)
    if response.is_client_error:
        raise Refused(response.json()["detail"])
    return response.raise_for_status()


def list_claims() -> list[dict]:
    return request("GET", "/api/claims").json()


def waiting_claims() -> list[dict]:
    return [claim for claim in list_claims() if claim["status"] == "waiting"]


def get_claim(claim_id: str) -> dict:
    return request("GET", f"/api/claims/{claim_id}").json()


def get_policy(policy_id: str) -> dict:
    return request("GET", f"/api/policies/{policy_id}").json()


def get_claim_history(customer_id: str) -> list[dict]:
    return request("GET", f"/api/customers/{customer_id}/claims").json()


def list_sections(document: Document) -> dict:
    return request("GET", f"/api/documents/{document}/sections").json()


def read_section(document: Document, number: int) -> str:
    return request("GET", f"/api/documents/{document}/sections/{number}").text


def get_instructions(name: Literal["queue", "claim"]) -> str:
    return request("GET", f"/api/instructions/{name}").text


def start_claim(claim_id: str) -> None:
    request("POST", f"/api/claims/{claim_id}/start")


def release_claim(claim_id: str) -> None:
    request("POST", f"/api/claims/{claim_id}/release")


def record_step(claim_id: str, kind: str, text: str) -> None:
    request("POST", f"/api/claims/{claim_id}/steps", json={"kind": kind, "text": text})


def record_decision(
    claim_id: str, decision: str, payout: float, reasons: str, policy_section: str
) -> dict:
    body = {
        "decision": decision,
        "payout": payout,
        "reasons": reasons,
        "policy_section": policy_section,
    }
    return request("POST", f"/api/claims/{claim_id}/decision", json=body).json()


def pay_claim(claim_id: str, amount: float) -> dict:
    path = f"/api/claims/{claim_id}/payments"
    return request("POST", path, json={"amount": amount}).json()


def start_run(cap: int) -> int:
    return request("POST", "/api/runs", json={"cap": cap}).json()["run_id"]


def set_todos(run_id: int, todos: list[dict]) -> None:
    request("PUT", f"/api/runs/{run_id}/todos", json={"todos": todos})


def finish_run(run_id: int, summary: str) -> None:
    request("POST", f"/api/runs/{run_id}/finish", json={"summary": summary})
