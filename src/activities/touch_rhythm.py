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

    def latest_interval_ms(self, min_gap_ms: float = 0.0) -> float | None:
        """Return the latest non-duplicate interval, if one is available."""
        for interval_ms in reversed(self.intervals_ms):
            if interval_ms >= min_gap_ms:
                return interval_ms
        return None

    def latest_frequency_hz(self, min_gap_ms: float = 0.0) -> float | None:
        """Return the latest usable cadence as presses per second."""
        interval_ms = self.latest_interval_ms(min_gap_ms)
        return None if interval_ms is None or interval_ms <= 0 else 1000.0 / interval_ms

    def has_matching_intervals(self, min_gap_ms: float,
                               required_intervals: int) -> bool:
        """Return whether this stream has enough usable intervals."""
        usable = sum(interval >= min_gap_ms for interval in self.intervals_ms)
        return usable >= max(1, int(required_intervals))


@dataclass
class MagnitudeCompressionTracker:
    """Turn a continuous magnitude stream into compression onsets.

    A rising crossing of ``enter`` records one beat. A held signal normally stays
    active until it falls below ``exit``; a later sharp rise can also record a
    beat, which handles sensors whose magnetic baseline does not fully recover
    between compressions.
    """

    enter: float
    exit: float
    active: bool = False
    previous: float | None = None
    spike_delta: float = 0.0

    def reset(self) -> None:
        self.active = False
        self.previous = None

    def update(self, magnitude: float) -> bool:
        magnitude = max(0.0, float(magnitude))
        previous = self.previous
        self.previous = magnitude
        spike_delta = self.spike_delta or max(20.0, self.enter * 0.25)
        if self.active:
            if magnitude <= self.exit:
                self.active = False
                return False
            # Count a distinct fast rise even when the signal remains above the
            # release threshold after the previous compression.
            return (previous is not None
                    and magnitude >= self.enter
                    and magnitude - previous >= spike_delta)
        if magnitude >= self.enter:
            self.active = True
            return True
        return False