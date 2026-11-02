"""The UnderCover Agent's service: every call the backend makes to it."""

import os

import httpx

DEFAULT_URL = "http://127.0.0.1:8002"


def client() -> httpx.Client:
    """Return a client for the agent's service, at UNDERCOVER_AGENT or the
    default address."""
    return httpx.Client(
        base_url=os.getenv("UNDERCOVER_AGENT") or DEFAULT_URL, timeout=10
    )


def request(path: str) -> None:
    """Ask the agent's service to do something, which it does in the background."""
    with client() as http:
        http.post(path).raise_for_status()


def work(claim_id: str) -> None:
    request(f"/claims/{claim_id}/work")


def carry_on(claim_id: str) -> None:
    request(f"/claims/{claim_id}/carry-on")
