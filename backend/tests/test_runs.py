from fastapi.testclient import TestClient
from sqlalchemy import update

from backend import db
from backend.main import app

client = TestClient(app)


def create(agents_at_once=3):
    return client.post(
        "/api/dispatches",
        json={
            "agents_at_once": agents_at_once,
            "model": "openai/gpt-6-luna",
            "reasoning_effort": "low",
        },
    )


def finish(run_id, status="finished"):
    return client.post(f"/api/runs/{run_id}/finish", json={"status": status})


def claims_of(run_id):
    return client.get(f"/api/runs/{run_id}/claims").json()


def test_create_a_dispatch():
    run = create(7).json()
    assert run["run_id"].startswith("dispatch-")
    assert run["kind"] == "dispatch"
    assert run["agents_at_once"] == 7
    assert run["model"] == "openai/gpt-6-luna"
    assert run["reasoning_effort"] == "low"
    assert run["status"] == "running"
    assert run["finished_at"] is None


def test_a_dispatch_id_is_new_every_time():
    assert create().json()["run_id"] != create().json()["run_id"]


def test_a_dispatch_works_from_1_to_25_at_once():
    assert create(0).status_code == 422
    assert create(26).status_code == 422
    assert create(1).status_code == 200
    assert create(25).status_code == 200


def test_a_dispatch_chooses_claims_oldest_first_with_ties_by_id():
    queue = client.get("/api/claims").json()
    expected = sorted(queue, key=lambda c: (c["submitted_on"], c["claim_id"]))
    run_id = create().json()["run_id"]
    assert claims_of(run_id) == [claim["claim_id"] for claim in expected]


def test_a_dispatch_includes_claims_in_progress_and_leaves_the_rest():
    client.post("/api/claims/CLM-1001/start")
    with db.engine().begin() as conn:
        conn.execute(
            update(db.claims)
            .where(db.claims.c.claim_id.in_(["CLM-1002", "CLM-1003", "CLM-1004"]))
            .values(status="decided")
        )
        conn.execute(
            update(db.claims)
            .where(db.claims.c.claim_id == "CLM-1005")
            .values(status="awaiting_sign_off")
        )
        conn.execute(
            update(db.claims)
            .where(db.claims.c.claim_id == "CLM-1006")
            .values(status="left_for_handler")
        )
    chosen = claims_of(create().json()["run_id"])
    assert "CLM-1001" in chosen
    assert len(chosen) == 20
    assert not {"CLM-1002", "CLM-1005", "CLM-1006"} & set(chosen)


def test_the_claims_of_an_unknown_run():
    assert client.get("/api/runs/dispatch-0/claims").status_code == 404


def test_finish_a_run():
    run_id = create().json()["run_id"]
    run = finish(run_id).json()
    assert run["status"] == "finished"
    assert run["finished_at"] is not None


def test_a_run_can_end_as_stopped_and_nothing_else():
    run_id = create().json()["run_id"]
    assert finish(run_id, "running").status_code == 422
    assert finish(run_id, "stopped").json()["status"] == "stopped"


def test_a_run_that_has_ended_cant_end_again():
    run_id = create().json()["run_id"]
    finish(run_id)
    response = finish(run_id)
    assert response.status_code == 409
    assert response.json()["detail"] == f"Run {run_id} has already ended."


def test_an_unknown_run_cant_finish():
    assert finish("dispatch-0").status_code == 404


def test_stop_running_runs():
    ended = create().json()["run_id"]
    finish(ended)
    running = create().json()["run_id"]
    assert client.post("/api/runs/stop").json() == [running]
    latest = client.get("/api/dispatches/latest").json()
    assert latest["run_id"] == running
    assert latest["status"] == "stopped"
    assert latest["finished_at"] is not None
    assert client.post("/api/runs/stop").json() == []


def test_get_latest_dispatch():
    assert client.get("/api/dispatches/latest").json() is None
    create()
    newest = create().json()
    client.post("/api/claims/CLM-1001/start")
    latest = client.get("/api/dispatches/latest").json()
    assert latest["run_id"] == newest["run_id"]
    assert len(latest["claims"]) == 25
    [first] = [c for c in latest["claims"] if c["claim_id"] == "CLM-1001"]
    assert first["status"] == "in_progress"
    assert first["summary"].startswith("Flight home canceled")
    assert first["decision"] is None
