import re
from pathlib import Path
from threading import Event

import httpx
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from agent import agents, api, dispatcher, service
from agent.tools import CLAIM_TOOLS
from backend.main import app as backend

client = TestClient(service.app)
backend_client = TestClient(backend)


def latest():
    return backend_client.get("/api/dispatches/latest").json()


@pytest.fixture
def held_dispatch(monkeypatch):
    """Replace the dispatcher with one that works until the test lets it go."""
    started = []
    release = Event()
    done = Event()

    def dispatch(agents_at_once):
        started.append(agents_at_once)
        release.wait(5)
        done.set()

    monkeypatch.setattr(dispatcher, "dispatch", dispatch)
    yield started, release, done
    release.set()


def free_slot():
    """Wait for the run slot, then free it."""
    assert service.run_slot.acquire(timeout=5)
    service.run_slot.release()


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


def test_a_start_answers_at_once_and_a_second_is_refused(held_dispatch):
    started, release, done = held_dispatch
    response = client.post("/dispatches", json={"agents_at_once": 7})
    assert response.status_code == 202
    assert not done.is_set()
    second = client.post("/dispatches", json={"agents_at_once": 3})
    assert second.status_code == 409
    assert second.json()["detail"] == service.RUN_GOING
    release.set()
    assert done.wait(5)
    free_slot()
    assert started == [7]


def test_the_slot_is_free_again_after_a_run(held_dispatch):
    started, release, _ = held_dispatch
    release.set()
    client.post("/dispatches", json={"agents_at_once": 1})
    free_slot()
    assert client.post("/dispatches", json={"agents_at_once": 2}).status_code == 202
    free_slot()
    assert started == [1, 2]


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_the_slot_is_free_again_after_a_run_fails(monkeypatch):
    def fails(agents_at_once):
        raise RuntimeError("The dispatch failed.")

    monkeypatch.setattr(dispatcher, "dispatch", fails)
    assert client.post("/dispatches", json={"agents_at_once": 1}).status_code == 202
    free_slot()


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_a_failure_in_the_background_marks_the_run_as_stopped(monkeypatch):
    def fails(run_id):
        raise RuntimeError("The backend failed.")

    monkeypatch.setattr(api, "list_run_claims", fails)
    assert client.post("/dispatches", json={"agents_at_once": 1}).status_code == 202
    free_slot()
    assert latest()["status"] == "stopped"


def create_dispatch():
    return api.create_dispatch(3, "openai/gpt-6-luna", "low")


def test_the_start_marks_any_running_dispatch_as_stopped():
    run = create_dispatch()
    with TestClient(service.app):
        pass
    assert latest()["run_id"] == run["run_id"]
    assert latest()["status"] == "stopped"


def test_the_start_keeps_trying_until_the_backend_comes(monkeypatch):
    run = create_dispatch()
    attempts = iter([httpx.ConnectError("Down."), httpx.ConnectError("Down.")])
    waits = []
    real = api.stop_running_runs

    def stop():
        failure = next(attempts, None)
        if failure:
            raise failure
        return real()

    monkeypatch.setattr(api, "stop_running_runs", stop)
    monkeypatch.setattr(service, "sleep", waits.append)
    service.stop_old_runs()
    assert waits == [1, 1]
    assert latest()["run_id"] == run["run_id"]
    assert latest()["status"] == "stopped"


def test_the_start_fails_clearly_when_the_backend_never_comes(monkeypatch):
    def down():
        raise httpx.ConnectError("Down.")

    monkeypatch.setattr(api, "stop_running_runs", down)
    monkeypatch.setattr(service, "sleep", lambda seconds: None)
    with pytest.raises(RuntimeError, match="The backend didn't start within 60"):
        service.stop_old_runs()


def test_every_operation_follows_the_api_rules():
    schema = service.app.openapi()
    routes = [route for route in service.app.routes if isinstance(route, APIRoute)]
    assert {route.operation_id for route in routes} == {
        "health",
        "work_claim",
        "carry_on_claim",
        "start_dispatch",
    }
    for route in routes:
        assert route.description
        assert "\n" not in route.description.strip()
    for path in schema["paths"].values():
        for operation in path.values():
            for parameter in operation.get("parameters", []):
                assert parameter["required"], parameter
            body = operation.get("requestBody")
            if body:
                ref = body["content"]["application/json"]["schema"]["$ref"]
                model = schema["components"]["schemas"][ref.split("/")[-1]]
                assert set(model["required"]) == set(model["properties"]), ref


def test_the_claim_agent_has_the_same_seven_tools():
    assert [tool.name for tool in CLAIM_TOOLS] == [
        "get_claim",
        "get_policy",
        "get_claim_history",
        "list_sections",
        "read_section",
        "calculate_payout",
        "record_decision",
    ]


def test_nothing_in_the_agent_stops_it_running_on_windows():
    for source in Path(agents.__file__).parent.glob("*.py"):
        text = source.read_text(encoding="utf-8")
        assert not re.search(
            r"\bimport signal\b|os\.fork|AsyncPostgresSaver|AsyncConnectionPool", text
        ), source
