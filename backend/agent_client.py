"""The UnderCover Agent's service: every call the backend makes to it."""

import os

import httpx

DEFAULT_URL = "http://127.0.0.1:8002"


class Refused(Exception):
    """The agent's service refused a request, with its reason."""


def client() -> httpx.Client:
    """Return a client for the agent's service, at UNDERCOVER_AGENT or the
    default address."""
    return httpx.Client(
        base_url=os.getenv("UNDERCOVER_AGENT") or DEFAULT_URL, timeout=10
    )


def request(path: str, body: dict) -> None:
    """Ask the agent's service to do something, which it does in the background,
    raising Refused with its reason if it refuses."""
    with client() as http:
        response = http.post(path, json=body)
    if response.status_code == httpx.codes.CONFLICT:
        raise Refused(response.json()["detail"])
    response.raise_for_status()


def work(claim_id: str) -> None:
    request(f"/claims/{claim_id}/work", {})


def carry_on(claim_id: str) -> None:
    request(f"/claims/{claim_id}/carry-on", {})


def start_dispatch(agents_at_once: int) -> None:
    request("/dispatches", {"agents_at_once": agents_at_once})


def start_evaluation(agents_at_once: int, claim_set: str, tiny: bool) -> None:
    body = {"agents_at_once": agents_at_once, "claim_set": claim_set, "tiny": tiny}
    request("/evaluations", body)
