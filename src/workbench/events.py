from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator


class EventStore:
    """Small durable event log used by SSE and run history."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._condition = asyncio.Condition()
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    agent_id TEXT,
                    stage TEXT,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS events_run_sequence ON events(run_id, sequence)"
            )

    def append(
        self,
        run_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        agent_id: str | None = None,
        stage: str | None = None,
    ) -> dict[str, Any]:
        created_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO events(run_id, event_type, agent_id, stage, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    event_type,
                    agent_id,
                    stage,
                    json.dumps(payload or {}, ensure_ascii=False),
                    created_at,
                ),
            )
            sequence = int(cursor.lastrowid)
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._notify())
        except RuntimeError:
            pass
        return {
            "sequence": sequence,
            "run_id": run_id,
            "type": event_type,
            "agent_id": agent_id,
            "stage": stage,
            "payload": payload or {},
            "created_at": created_at,
        }

    def list(self, run_id: str, after: int = 0) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT sequence, run_id, event_type, agent_id, stage, payload_json, created_at
                FROM events WHERE run_id = ? AND sequence > ? ORDER BY sequence
                """,
                (run_id, after),
            ).fetchall()
        return [
            {
                "sequence": row[0],
                "run_id": row[1],
                "type": row[2],
                "agent_id": row[3],
                "stage": row[4],
                "payload": json.loads(row[5]),
                "created_at": row[6],
            }
            for row in rows
        ]

    async def stream(self, run_id: str, after: int = 0) -> AsyncIterator[str]:
        cursor = after
        while True:
            events = self.list(run_id, cursor)
            if events:
                for event in events:
                    cursor = event["sequence"]
                    yield f"id: {cursor}\nevent: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                continue
            try:
                async with self._condition:
                    await asyncio.wait_for(self._condition.wait(), timeout=15)
            except TimeoutError:
                yield ": heartbeat\n\n"

    async def _notify(self) -> None:
        async with self._condition:
            self._condition.notify_all()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)
