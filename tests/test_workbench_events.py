from src.workbench.events import EventStore


def test_event_store_orders_and_filters_durable_events(tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    first = store.append("run-1", "run_started", {"case_id": "case-1"})
    second = store.append(
        "run-1",
        "stage_started",
        {"message": "working"},
        agent_id="pulmonology",
        stage="initial_assessment",
    )
    store.append("run-2", "run_started", {})

    assert first["sequence"] < second["sequence"]
    assert [item["type"] for item in store.list("run-1")] == [
        "run_started",
        "stage_started",
    ]
    assert store.list("run-1", after=first["sequence"])[0]["agent_id"] == "pulmonology"


def test_progress_tracks_stages_specialties_rounds_and_resets_on_rerun(tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    store.append("run-1", "run_started")
    store.append("run-1", "stage_started", stage="initial_consult")
    for specialty in ("pulmonology", "pathology", "rheumatology"):
        store.append("run-1", "stage_completed", agent_id=specialty, stage="initial_consult")
    assert len(store.progress("run-1")["completed_specialties"]) == 3
    store.append("run-1", "stage_started", stage="team_discussion")
    store.append("run-1", "discussion_round_started", {"round_number": 2})
    store.append("run-1", "agent_event", {"event": "llm_attempt_started"}, stage="answer")
    assert store.progress("run-1")["stage"] == "team_discussion"
    assert store.progress("run-1")["round_number"] == 2
    store.append("run-1", "synthesis_acceptance_started")
    assert store.progress("run-1")["stage"] == "synthesis_acceptance"
    store.append("run-1", "stage_started", stage="final_report")
    ended = store.append("run-1", "agent_error", stage="final_report")["created_at"]
    assert store.progress("run-1")["stage"] == "final_report"
    store.append("run-1", "run_started")
    assert store.progress("run-1") == {"stage": None, "completed_specialties": [], "round_number": None}
    assert store.progress("run-1", before=ended)["stage"] == "final_report"
