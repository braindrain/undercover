import json

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from backend import db
from backend.main import app

client = TestClient(app)


def test_list_claims_is_the_queue():
    claims = client.get("/api/claims").json()
    queue = json.loads(db.read_data("claims.json"))
    assert [claim["claim_id"] for claim in claims] == [
        claim["claim_id"] for claim in queue
    ]
    assert {claim["status"] for claim in claims} == {"waiting"}
    assert claims[0]["customer_name"] == "Maya Patel"
    assert claims[0]["decision"] is None


def test_seed_reads_nothing_from_evals(monkeypatch):
    read = []
    monkeypatch.setattr(db, "read_data", lambda name: read.append(name) or "[]")
    db.reset()
    assert read == ["claims.json", "policies.json", "customers.json", "handlers.json"]


def test_reset_starts_again(tmp_path):
    db.reset()
    assert db.database_path() == tmp_path / "undercover.db"
    assert db.database_path().exists()


def test_get_claim():
    claim = client.get("/api/claims/CLM-1001").json()
    assert claim["policy_id"] == "POL-2001"
    assert len(claim["documents"]) == 4
    assert claim["status"] == "waiting"
    assert claim["decision"] is None
    assert claim["payment"] is None
    assert claim["sign_off"] is None


def test_get_policy():
    policy = client.get("/api/policies/POL-2001").json()
    assert policy["plan"] == "premium"
    assert policy["travelers"] == ["Maya Patel"]


def test_get_customer():
    customer = client.get("/api/customers/CUS-3001").json()
    assert customer["name"] == "Maya Patel"
    assert customer["address"]["city"] == "Jersey City"


def test_get_claim_history_leaves_out_contact_details():
    history = client.get("/api/customers/CUS-3001/claims").json()
    assert [claim["claim_id"] for claim in history] == ["CLM-0412"]
    assert "Maya Patel" not in json.dumps(history)


@pytest.mark.parametrize(
    "path",
    [
        "/api/claims/CLM-9999",
        "/api/policies/POL-9999",
        "/api/customers/CUS-9999",
        "/api/customers/CUS-9999/claims",
    ],
)
def test_unknown_record(path):
    response = client.get(path)
    assert response.status_code == 404
    assert response.json()["detail"] == f"There is no record {path.split('/')[3]}."


def test_get_policy_wording():
    response = client.get("/api/policy-wording")
    assert response.headers["content-type"].startswith("text/markdown")
    assert response.text.startswith("# UnderCover Travel Insurance: Policy Wording")


def test_get_instructions():
    assert client.get("/api/instructions").text.startswith("# The UnderCover Agent")


def test_list_handlers():
    handlers = client.get("/api/handlers").json()
    assert [handler["handler_id"] for handler in handlers] == [
        "HND-01",
        "HND-02",
        "HND-03",
    ]


def test_every_operation_follows_the_api_rules():
    schema = app.openapi()
    routes = [route for route in app.routes if isinstance(route, APIRoute)]
    for route in routes:
        assert route.operation_id, route.path
        assert route.description, route.path
        assert "\n" not in route.description.strip(), route.path
    for path in schema["paths"].values():
        for operation in path.values():
            for parameter in operation.get("parameters", []):
                assert parameter["required"], parameter
            body = operation.get("requestBody")
            if body:
                ref = body["content"]["application/json"]["schema"]["$ref"]
                model = schema["components"]["schemas"][ref.split("/")[-1]]
                assert set(model["required"]) == set(model["properties"]), ref
