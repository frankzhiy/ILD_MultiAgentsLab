from __future__ import annotations

from contextlib import contextmanager
from threading import Condition
from typing import Iterator


class RequestThrottle:
    """Process-wide cap for in-flight model requests during batch work."""

    def __init__(self) -> None:
        self._condition = Condition()
        self._limits: dict[str, int] = {}
        self._active = 0

    def register(self, batch_id: str, limit: int) -> None:
        with self._condition:
            self._limits[batch_id] = limit
            self._condition.notify_all()

    def unregister(self, batch_id: str) -> None:
        with self._condition:
            self._limits.pop(batch_id, None)
            self._condition.notify_all()

    @contextmanager
    def slot(self) -> Iterator[None]:
        with self._condition:
            while self._limits and self._active >= min(self._limits.values()):
                self._condition.wait()
            self._active += 1
        try:
            yield
        finally:
            with self._condition:
                self._active -= 1
                self._condition.notify_all()


request_throttle = RequestThrottle()
