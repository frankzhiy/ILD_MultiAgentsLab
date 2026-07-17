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
