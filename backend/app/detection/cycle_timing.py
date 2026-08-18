"""Optional wall-clock phase timing for a detection cycle.

Inactive unless ``record_cycle_timing()`` is entered (benchmarks). Production
cycles pay only a ContextVar get per phase.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from time import perf_counter

_TIMER: ContextVar[dict[str, float] | None] = ContextVar("cycle_timer", default=None)

PHASES = (
    "features",
    "layer1",
    "layer2",
    "fusion_merge",
    "peripheral",
    "persist",
    "persist_explain",
    "persist_history",
    "persist_alerts",
    "persist_commit",
    "total",
)


@contextmanager
def record_cycle_timing() -> Iterator[dict[str, float]]:
    spans: dict[str, float] = {name: 0.0 for name in PHASES}
    token = _TIMER.set(spans)
    t0 = perf_counter()
    try:
        yield spans
    finally:
        spans["total"] = perf_counter() - t0
        _TIMER.reset(token)


@contextmanager
def phase(name: str) -> Iterator[None]:
    spans = _TIMER.get()
    if spans is None:
        yield
        return
    t0 = perf_counter()
    try:
        yield
    finally:
        spans[name] = spans.get(name, 0.0) + (perf_counter() - t0)
