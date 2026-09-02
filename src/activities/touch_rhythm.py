"""Reusable touch-rhythm detection for declarative activities."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TouchRhythmTracker:
    """Track consecutive touch intervals close to a target cadence."""

    last_press_ms: float | None = None
    intervals_ms: list[float] = field(default_factory=list)

    def reset(self) -> None:
        self.last_press_ms = None
        self.intervals_ms.clear()

    def record(self, timestamp_ms: float) -> None:
        """Record one press and retain its interval for condition evaluation."""
        if self.last_press_ms is None:
            self.last_press_ms = timestamp_ms
            return

        interval_ms = timestamp_ms - self.last_press_ms
        self.last_press_ms = timestamp_ms
        self.intervals_ms.append(interval_ms)

    def matches(self, target_interval_ms: float, tolerance_ms: float,
                min_gap_ms: float, required_intervals: int) -> bool:
        """Return whether the latest intervals match the requested cadence."""
        matching = 0
        for interval_ms in reversed(self.intervals_ms):
            if interval_ms < min_gap_ms:
                continue
            if abs(interval_ms - target_interval_ms) > tolerance_ms:
                break
            matching += 1
        return matching >= max(1, int(required_intervals))