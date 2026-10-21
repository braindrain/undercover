from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)


def start_run(cap=5):
    return client.post("/api/runs", json={"cap": cap})


def set_todos(run_id, todos):
    return client.put(f"/api/runs/{run_id}/todos", json={"todos": todos})


def finish(run_id, summary="Worked five claims."):
    return client.post(f"/api/runs/{run_id}/finish", json={"summary": summary})


def test_start_run():
    run = start_run().json()
    assert run["cap"] == 5
    assert run["status"] == "running"
    assert run["todos"] == []
    assert run["summary"] is None
    assert run["finished_at"] is None


def test_a_run_needs_a_cap_of_at_least_one():
    assert start_run(0).status_code == 422


def test_set_todos_replaces_the_list_in_order():
    run_id = start_run().json()["run_id"]
    set_todos(run_id, [{"text": "Work CLM-1001", "status": "todo"}])
    todos = [
        {"text": "Work CLM-1002", "status": "done"},
        {"text": "Work CLM-1001", "status": "in_progress"},
        {"text": "Work CLM-1003", "status": "left_for_handler"},
    ]
    assert set_todos(run_id, todos).json()["todos"] == todos
    assert client.get("/api/runs/latest").json()["todos"] == todos


def test_a_todo_needs_text_and_a_known_status():
    run_id = start_run().json()["run_id"]
    assert set_todos(run_id, [{"text": "", "status": "todo"}]).status_code == 422
    assert set_todos(run_id, [{"text": "Work", "status": "maybe"}]).status_code == 422


def test_finish_run():
    run_id = start_run().json()["run_id"]
    run = finish(run_id).json()
    assert run["status"] == "finished"
    assert run["summary"] == "Worked five claims."
    assert run["finished_at"] is not None


def test_a_finished_run_cant_be_changed():
    run_id = start_run().json()["run_id"]
    finish(run_id)
    for response in [set_todos(run_id, []), finish(run_id)]:
        assert response.status_code == 409
        assert response.json()["detail"] == f"Run {run_id} has finished."


def test_an_unknown_run():
    assert set_todos(99, []).status_code == 404


def test_get_latest_run():
    assert client.get("/api/runs/latest").json() is None
    start_run(5)
    newest = start_run(2).json()
    assert client.get("/api/runs/latest").json() == newest
