import re
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend import agent_client, db
from backend.main import app

client = TestClient(app)
REAL_REQUEST = agent_client.request


def unreachable(path):
    raise httpx.ConnectError("Connection refused")


def await_sign_off(claim_id="CLM-1002"):
    client.post(f"/api/claims/{claim_id}/start")
    client.post(
        f"/api/claims/{claim_id}/decision",
        json={
            "decision": "approve",
            "payout": 1150.0,
            "reasons": "Covered.",
            "policy_section": "Section 5, Baggage",
        },
    )


def sign_off(claim_id="CLM-1002"):
    return client.post(
        f"/api/claims/{claim_id}/sign-off", json={"handler_id": "HND-01"}
    )


def test_a_sign_off_asks_the_agent_to_carry_on_once_it_is_saved(monkeypatch):
    seen = []

    def stand_in(path):
        with db.engine().connect() as conn:
            saved = conn.execute(
                select(db.sign_offs.c.handler_id, db.claims.c.status).join(
                    db.claims, db.claims.c.claim_id == db.sign_offs.c.claim_id
                )
            ).all()
        seen.append((path, saved))

    monkeypatch.setattr(agent_client, "request", stand_in)
    await_sign_off()
    assert sign_off().status_code == 200
    assert seen == [("/claims/CLM-1002/carry-on", [("HND-01", "in_progress")])]


def test_a_sign_off_stands_when_the_agent_cant_be_reached(monkeypatch):
    monkeypatch.setattr(agent_client, "request", unreachable)
    await_sign_off()
    response = sign_off()
    assert response.status_code == 200
    claim = client.get("/api/claims/CLM-1002").json()
    assert claim["status"] == "in_progress"
    assert claim["sign_off"]["handler_id"] == "HND-01"


def test_run_agent_asks_the_agent_to_work_the_claim(agent_calls):
    response = client.post("/api/claims/CLM-1001/run")
    assert response.status_code == 202
    assert response.json()["status"] == "waiting"
    assert agent_calls == ["/claims/CLM-1001/work"]


def test_run_agent_when_the_agent_cant_be_reached(monkeypatch):
    monkeypatch.setattr(agent_client, "request", unreachable)
    response = client.post("/api/claims/CLM-1001/run")
    assert response.status_code == 503
    assert response.json()["detail"] == "The agent's service isn't running."
    assert client.get("/api/claims/CLM-1001").json()["status"] == "waiting"


def test_run_agent_needs_a_waiting_claim(agent_calls):
    client.post("/api/claims/CLM-1001/start")
    response = client.post("/api/claims/CLM-1001/run")
    assert response.status_code == 409
    assert response.json()["detail"] == "CLM-1001 is in progress, so it can't be run."
    assert agent_calls == []


def test_the_client_reaches_the_service_at_its_address(monkeypatch):
    reached = []

    def service(request):
        reached.append(str(request.url))
        return httpx.Response(202)

    real_client = httpx.Client
    monkeypatch.setattr(agent_client, "request", REAL_REQUEST)
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(service)),
    )
    monkeypatch.delenv("UNDERCOVER_AGENT", raising=False)
    agent_client.work("CLM-1001")
    monkeypatch.setenv("UNDERCOVER_AGENT", "http://127.0.0.1:8003")
    agent_client.carry_on("CLM-1002")
    assert reached == [
        "http://127.0.0.1:8002/claims/CLM-1001/work",
        "http://127.0.0.1:8003/claims/CLM-1002/carry-on",
    ]


BACKEND = Path(db.__file__).parent


@pytest.mark.parametrize(
    "source", sorted(BACKEND.rglob("*.py")), ids=lambda path: path.name
)
def test_only_the_agent_client_names_the_agents_service(source):
    if source.name in {"agent_client.py", "test_agent_client.py"}:
        return
    text = source.read_text(encoding="utf-8")
    assert not re.search(r"[\"']/claims/|UNDERCOVER_AGENT|:8002", text)
    assert not re.search(r"^\s*(from|import) agent\b", text, re.MULTILINE)
