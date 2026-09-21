"""Structured run events.

Every stage emits events through one sink. The CLI renders them with Rich, the web UI polls them,
and all of them are persisted as JSON Lines next to the outputs for auditing.
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Event:
    stage: str
    message: str
    level: str = "info"  # info | warn | error | success
    data: dict = field(default_factory=dict)
    ts: float = field(default_factory=time.time)


class EventLog:
    def __init__(self, listeners: list[Callable[[Event], None]] | None = None):
        self.events: list[Event] = []
        self.listeners = list(listeners or [])
        self.timings: dict[str, float] = {}
        self._lock = threading.Lock()

    def emit(self, stage: str, message: str, level: str = "info", **data) -> None:
        ev = Event(stage, message, level, data)
        with self._lock:
            self.events.append(ev)
        for fn in self.listeners:
            with contextlib.suppress(Exception):  # a broken listener must never break a run
                fn(ev)

    @contextmanager
    def stage(self, name: str):
        t0 = time.perf_counter()
        try:
            yield
        finally:
            dt = time.perf_counter() - t0
            with self._lock:
                self.timings[name] = round(self.timings.get(name, 0.0) + dt, 3)

    def write_jsonl(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            for ev in self.events:
                f.write(json.dumps(asdict(ev), ensure_ascii=False) + "\n")
