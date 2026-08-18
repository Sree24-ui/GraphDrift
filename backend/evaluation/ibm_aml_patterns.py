"""Parse IBM HI-Small Patterns.txt and derive the eval time-compression factor.

Labeled typology instances (FAN-OUT, CYCLE, GATHER-SCATTER, …) are the ground
truth for how long a laundering ring actually lasts in this corpus — not PaySim.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

DEFAULT_PATTERNS = Path(__file__).resolve().parent / "data" / "HI-Small_Patterns.txt"

BEGIN_RE = re.compile(r"^BEGIN LAUNDERING ATTEMPT - ([A-Z0-9\- ]+?)(?:\s*:.*)?\s*$")
END_RE = re.compile(r"^END LAUNDERING ATTEMPT")

# Simulator attacks vs multi-scale windows (minutes).
SYNTHETIC_FAST_DURATION_MIN = 2.5  # fan-in / fan-out ~2–3 min
SYNTHETIC_SLOW_DURATION_MIN = 13.5  # slow-drip ~12–15 min
FAST_WINDOW_MIN = 15
SLOW_WINDOW_MIN = 60


@dataclass(frozen=True)
class PatternInstance:
    typology: str
    n_tx: int
    first_ts: datetime
    last_ts: datetime

    @property
    def duration_minutes(self) -> float:
        return (self.last_ts - self.first_ts).total_seconds() / 60.0


def parse_pattern_timestamp(value: str) -> datetime:
    return datetime.strptime(value.strip(), "%Y/%m/%d %H:%M")


def load_pattern_instances(path: Path | None = None) -> list[PatternInstance]:
    patterns_path = path or DEFAULT_PATTERNS
    instances: list[PatternInstance] = []
    typology: str | None = None
    stamps: list[datetime] = []
    for line in patterns_path.read_text(errors="replace").splitlines():
        begin = BEGIN_RE.match(line)
        if begin:
            typology = begin.group(1).strip()
            stamps = []
            continue
        if END_RE.match(line):
            if typology and stamps:
                instances.append(
                    PatternInstance(
                        typology=typology,
                        n_tx=len(stamps),
                        first_ts=min(stamps),
                        last_ts=max(stamps),
                    )
                )
            typology = None
            stamps = []
            continue
        if typology and line.strip():
            raw_ts = line.split(",", 1)[0]
            try:
                stamps.append(parse_pattern_timestamp(raw_ts))
            except ValueError:
                continue
    return instances


def duration_summary(minutes: list[float]) -> dict[str, float]:
    arr = np.asarray(minutes, dtype=float)
    pct = np.percentile(arr, [0, 25, 50, 75, 90, 95, 100])
    return {
        "n": float(len(arr)),
        "min_min": float(pct[0]),
        "p25_min": float(pct[1]),
        "median_min": float(pct[2]),
        "p75_min": float(pct[3]),
        "p90_min": float(pct[4]),
        "p95_min": float(pct[5]),
        "max_min": float(pct[6]),
        "mean_min": float(arr.mean()),
    }


def summarize_by_typology(instances: list[PatternInstance]) -> dict[str, dict[str, float]]:
    by_type: dict[str, list[float]] = defaultdict(list)
    for inst in instances:
        by_type[inst.typology].append(inst.duration_minutes)
    out = {"ALL": duration_summary([i.duration_minutes for i in instances])}
    for name in sorted(by_type):
        out[name] = duration_summary(by_type[name])
    return out


def recommended_time_scale(median_real_minutes: float) -> float:
    """Map median IBM ring duration onto the slow-drip slot in the 60-min window.

    IBM typologies unfold over days (not 2–3 minutes), so the analog is
    slow-drip occupying ~13.5 / 60 of the slow detection window — not
    fan-in/fan-out occupying ~2.5 / 15 of the fast window.
    """
    target = SYNTHETIC_SLOW_DURATION_MIN
    if median_real_minutes <= 0:
        return 1.0
    return float(round(median_real_minutes / target))
