import json
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from backend import db
from backend.main import app

client = TestClient(app)

LIMIT_REASON = (
    "This payout is over the $1,000.00 approval limit "
    "and waits for a handler's sign-off."
)


def start(claim_id):
    return client.post(f"/api/claims/{claim_id}/start")


def decide(claim_id, decision="approve", payout=420.0, reasons="Covered."):
    return client.post(
        f"/api/claims/{claim_id}/decision",
        json={
            "decision": decision,
            "payout": payout,
            "reasons": reasons,
            "policy_section": "Section 3, Trip Delay",
        },
    )


def pay(claim_id, amount):
    return client.post(f"/api/claims/{claim_id}/payments", json={"amount": amount})


def sign_off(claim_id, handler_id="HND-01"):
    return client.post(
        f"/api/claims/{claim_id}/sign-off", json={"handler_id": handler_id}
    )


def refusal(response):
    assert response.status_code == 409
    return response.json()["detail"]


def test_start_claim():
    assert start("CLM-1001").json()["status"] == "in_progress"
    assert refusal(start("CLM-1001")) == (
        "CLM-1001 is in progress, so it can't be started."
    )


def test_release_claim():
    start("CLM-1001")
    assert client.post("/api/claims/CLM-1001/release").json()["status"] == "waiting"


def test_release_leaves_a_decided_claim():
    start("CLM-1001")
    decide("CLM-1001", "decline", 0)
    assert client.post("/api/claims/CLM-1001/release").json()["status"] == "decided"


def test_steps():
    for kind, text in [("tool_call", "get_claim(CLM-1001)"), ("reply", "Done.")]:
        step = client.post(
            "/api/claims/CLM-1001/steps", json={"kind": kind, "text": text}
        ).json()
        assert step["text"] == text
    steps = client.get("/api/claims/CLM-1001/steps").json()
    assert [step["kind"] for step in steps] == ["tool_call", "reply"]
    assert client.get("/api/claims/CLM-9999/steps").status_code == 404


def test_approve_under_the_limit_and_pay():
    start("CLM-1001")
    claim = decide("CLM-1001").json()
    assert claim["status"] == "decided"
    assert claim["decision"]["payout"] == 420.0
    claim = pay("CLM-1001", 420.0).json()
    assert claim["payment"]["amount"] == 420.0
    assert refusal(pay("CLM-1001", 420.0)) == "CLM-1001 has already been paid."


def test_approve_at_the_limit():
    start("CLM-1001")
    assert decide("CLM-1001", payout=1000.0).json()["status"] == "decided"
    assert pay("CLM-1001", 1000.0).status_code == 200


def test_payment_over_the_limit_waits_for_sign_off():
    start("CLM-1002")
    assert decide("CLM-1002", payout=1150.0).json()["status"] == "awaiting_sign_off"
    assert refusal(pay("CLM-1002", 1150.0)) == LIMIT_REASON
    assert refusal(pay("CLM-1002", 1000.0)) == LIMIT_REASON
    claim = client.get("/api/claims/CLM-1002").json()
    assert claim["status"] == "awaiting_sign_off"
    assert claim["payment"] is None


def test_sign_off_pays():
    start("CLM-1002")
    decide("CLM-1002", payout=1150.0)
    claim = sign_off("CLM-1002").json()
    assert claim["status"] == "decided"
    assert claim["payment"]["amount"] == 1150.0
    assert claim["sign_off"]["handler_id"] == "HND-01"
    assert refusal(pay("CLM-1002", 1150.0)) == "CLM-1002 has already been paid."


@pytest.mark.parametrize(
    ("decision", "payout", "reason"),
    [
        ("decline", 100.0, "A decline has a payout of $0.00."),
        ("refer", 100.0, "A refer has a payout of $0.00."),
        ("approve", 0.0, "An approval needs a payout above $0.00."),
    ],
)
def test_decision_and_payout_must_agree(decision, payout, reason):
    start("CLM-1001")
    assert refusal(decide("CLM-1001", decision, payout)) == reason


def test_decide_only_a_claim_in_progress():
    assert refusal(decide("CLM-1001")) == (
        "CLM-1001 is waiting, so it can't be decided."
    )
    start("CLM-1001")
    decide("CLM-1001")
    assert refusal(decide("CLM-1001")) == (
        "CLM-1001 is decided, so it can't be decided."
    )


@pytest.mark.parametrize(
    "change",
    [{"decision": "maybe"}, {"reasons": ""}, {"policy_section": ""}, {"payout": -1}],
)
def test_decision_shape(change):
    start("CLM-1001")
    decision = {
        "decision": "approve",
        "payout": 420.0,
        "reasons": "Covered.",
        "policy_section": "Section 3, Trip Delay",
    }
    response = client.post("/api/claims/CLM-1001/decision", json={**decision, **change})
    assert response.status_code == 422


def test_pay_only_an_approval():
    assert refusal(pay("CLM-1001", 420.0)) == "CLM-1001 has no approval to pay."
    start("CLM-1001")
    decide("CLM-1001", "decline", 0)
    assert refusal(pay("CLM-1001", 420.0)) == "CLM-1001 has no approval to pay."


def test_pay_the_approved_amount():
    start("CLM-1001")
    decide("CLM-1001")
    assert refusal(pay("CLM-1001", 400.0)) == (
        "The amount must be the approved payout, $420.00."
    )


def test_sign_off_only_a_claim_awaiting_it():
    start("CLM-1001")
    decide("CLM-1001")
    assert refusal(sign_off("CLM-1001")) == (
        "CLM-1001 is decided, so it can't be signed off."
    )


def test_sign_off_needs_a_known_handler():
    start("CLM-1002")
    decide("CLM-1002", payout=1150.0)
    assert sign_off("CLM-1002", "HND-99").status_code == 404


def test_sign_off_needs_a_handler_who_can_approve_over_the_limit():
    start("CLM-1002")
    decide("CLM-1002", payout=1150.0)
    with closing(db.connect()) as conn, conn:
        handler = json.loads(
            conn.execute(
                "SELECT data FROM handlers WHERE handler_id = 'HND-03'"
            ).fetchone()["data"]
        )
        handler["can_approve_over_limit"] = False
        conn.execute(
            "UPDATE handlers SET data = ? WHERE handler_id = 'HND-03'",
            (json.dumps(handler),),
        )
    assert refusal(sign_off("CLM-1002", "HND-03")) == (
        "Priya Raman can't sign off payouts over the limit."
    )
