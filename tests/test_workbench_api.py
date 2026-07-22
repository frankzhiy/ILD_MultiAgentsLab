from fastapi.testclient import TestClient

from src.workbench.app import app, orchestrator


RUN_ID = "20260716_163006_86-IPF_step2_step3"


def test_workbench_exposes_real_run_and_semantic_projection():
    client = TestClient(app)
    runs = client.get("/api/runs").json()
    assert any(item["id"] == RUN_ID for item in runs)

    semantic = client.get(f"/api/runs/{RUN_ID}/semantic").json()
    assert semantic["case_id"] == "86-IPF"
    assert semantic["summary"]["segment_count"] > 0
    first = semantic["segments"][0]["units"][0]
    assert first["graph_unit_id"]
    assert first["local_graph"]["nodes"]


def test_workbench_exposes_unique_unit_routing_and_marks_legacy_specialty_outputs():
    client = TestClient(app)
    routing = client.get(f"/api/runs/{RUN_ID}/routing").json()
    assert len(routing["specialties"]) == 4
    assert routing["summary"]["unit_count"] == len(routing["units"])
    assert all(unit["mdt_specialty"] for unit in routing["units"])

    specialties = client.get(f"/api/runs/{RUN_ID}/specialties").json()["results"]
    assert len(specialties) == 4
    assert all(item["legacy"] for item in specialties)
    assert all("trace" not in item and "internal_state" not in item for item in specialties)


def test_workbench_exposes_chair_endpoint_and_model():
    client = TestClient(app)

    response = client.get(f"/api/runs/{RUN_ID}/chair")
    assert response.status_code == 200
    assert response.json()["status"] == "unavailable"
    assert "mdt_chair" in {
        item["agent_id"] for item in client.get("/api/models").json()["agents"]
    }


def test_chair_endpoint_starts_only_the_chair_stage(monkeypatch):
    started = []
    monkeypatch.setattr(orchestrator, "start_chair", started.append)

    response = TestClient(app).post(f"/api/runs/{RUN_ID}/chair")

    assert response.status_code == 202
    assert response.json()["status"] == "running"
    assert started == [RUN_ID]


def test_workbench_rejects_path_traversal():
    client = TestClient(app)
    response = client.get(f"/api/runs/{RUN_ID}/artifacts/../../pyproject.toml")
    assert response.status_code == 404


def test_workbench_exposes_failure_artifacts_without_hiding_completed_run():
    client = TestClient(app)
    run = client.get(f"/api/runs/{RUN_ID}").json()
    errors = client.get(f"/api/runs/{RUN_ID}/errors").json()
    assert run["status"] == "completed"
    assert run["has_error_artifact"] is True
    assert errors
    assert all(item["artifact"].endswith(".json") for item in errors)


def test_create_run_rejects_unsafe_case_id_before_starting_agent():
    client = TestClient(app)
    response = client.post(
        "/api/runs",
        json={"source": "paste", "case_id": "../escape", "raw_text": "case"},
    )
    assert response.status_code == 422
