from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from agent import agents, service

client = TestClient(service.app)


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_work_claim_runs_the_claim_agent_in_the_background(monkeypatch):
    worked = []
    monkeypatch.setattr(agents, "work_claim", worked.append)
    response = client.post("/claims/CLM-1001/work")
    assert response.status_code == 202
    assert response.json() == {"claim_id": "CLM-1001", "status": "accepted"}
    assert worked == ["CLM-1001"]


def test_carry_on_claim_carries_the_claim_on_in_the_background(monkeypatch):
    carried = []
    monkeypatch.setattr(agents, "carry_on", carried.append)
    response = client.post("/claims/CLM-1002/carry-on")
    assert response.status_code == 202
    assert carried == ["CLM-1002"]


def test_every_operation_follows_the_api_rules():
    schema = service.app.openapi()
    routes = [route for route in service.app.routes if isinstance(route, APIRoute)]
    assert {route.operation_id for route in routes} == {
        "health",
        "work_claim",
        "carry_on_claim",
    }
    for route in routes:
        assert route.description
        assert "\n" not in route.description.strip()
    for path in schema["paths"].values():
        for operation in path.values():
            assert "requestBody" not in operation
            for parameter in operation.get("parameters", []):
                assert parameter["required"], parameter
