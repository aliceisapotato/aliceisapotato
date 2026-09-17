"""Turn the motion and audio signals into rally segments.

The rules encode how badminton actually looks on video:

* A rally is a continuous stretch of high activity, 2-40 seconds long.
* Activity dips mid-rally (a high clear, a long lift) so the detector has to
  hang on for a beat before declaring the rally over.
* Between rallies players walk, pick up the shuttle and towel off: there is
  motion, but almost no racket impacts. Requiring a couple of impacts throws
  out warm-up and dead time without throwing out real rallies.
* Thresholds are derived from each file's own distribution, because a phone
  leaning on a bag three metres from the court and one on a tripod at the
  back of the hall produce wildly different absolute numbers.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import List, Optional, Sequence, Tuple

import numpy as np

from .audio import AudioTrack
from .motion import MotionTrack


@dataclass
class DetectParams:
    """Every knob of the detector, kept together so it can be serialised."""

    grid: float = 0.1
    motion_weight: float = 0.6
    audio_weight: float = 0.4
    impact_window: float = 1.0
    enter_level: float = 0.45
    exit_level: float = 0.25
    hang: float = 1.2
    merge_gap: float = 1.5
    min_duration: float = 2.0
    max_duration: float = 45.0
    min_shots: int = 2
    pre_roll: float = 0.8
    post_roll: float = 1.2

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "DetectParams":
        if not data:
            return cls()
        known = {f for f in cls().to_dict()}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class Segment:
    """One detected rally, in source-timeline seconds."""

    start: float
    end: float
    detect_start: float
    detect_end: float
    shots: int = 0
    score: float = 0.0
    peak_activity: float = 0.0
    mean_activity: float = 0.0

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def core_duration(self) -> float:
        return max(0.0, self.detect_end - self.detect_start)

    @property
    def shot_rate(self) -> float:
        return self.shots / self.core_duration if self.core_duration > 0 else 0.0


@dataclass
class ActivityCurve:
    """The fused activity score on a regular grid, kept for review plots."""

    times: np.ndarray
    score: np.ndarray = field(repr=False)
    motion: np.ndarray = field(repr=False)
    impacts: np.ndarray = field(repr=False)
    enter_threshold: float = 0.0
    exit_threshold: float = 0.0

    def slice(self, start: float, end: float) -> Tuple[np.ndarray, np.ndarray]:
        if self.times.size == 0:
            return np.zeros(0), np.zeros(0)
        lo = int(np.searchsorted(self.times, start, side="left"))
        hi = int(np.searchsorted(self.times, end, side="right"))
        return self.times[lo:hi], self.score[lo:hi]


def _normalise(values: np.ndarray, floor: float = 1e-6, pct: float = 92.0) -> np.ndarray:
    """Scale so that a busy moment sits near 1.0, robust to outliers."""
    if values.size == 0:
        return values
    scale = float(np.percentile(values, pct))
    if scale <= floor:
        scale = float(values.max())
    if scale <= floor:
        return np.zeros_like(values)
    return np.clip(values / scale, 0.0, 1.5)


def build_activity(
    duration: float,
    motion: MotionTrack,
    audio: AudioTrack,
    params: DetectParams,
) -> ActivityCurve:
    """Resample both signals onto one grid and fuse them into a score."""
    step = max(0.02, params.grid)
    times = np.arange(0.0, max(duration, step), step)

    motion_raw = np.interp(times, motion.times, motion.energy) if motion.energy.size else np.zeros_like(times)
    motion_n = _normalise(motion_raw)

    if audio.available and audio.onsets.size >= 4:
        density = audio.impact_density(times, params.impact_window)
        scale = max(float(np.percentile(density, 92)), 1.0)
        impacts_n = np.clip(density / scale, 0.0, 1.5)
        w_m, w_a = params.motion_weight, params.audio_weight
    else:
        # No usable audio (muted clip, wind noise only): motion carries it.
        impacts_n = np.zeros_like(times)
        w_m, w_a = 1.0, 0.0

    total = w_m + w_a or 1.0
    score = (w_m * motion_n + w_a * impacts_n) / total
    score = _smooth(score, max(1, int(round(0.3 / step))))

    low = float(np.percentile(score, 20))
    high = float(np.percentile(score, 85))
    spread = high - low
    if spread < 0.05:
        # Nearly uniform footage (all rally, or all idle): fall back to
        # absolute levels rather than amplifying noise into "rallies".
        enter, exit_ = 0.35, 0.22
    else:
        enter = low + params.enter_level * spread
        exit_ = low + params.exit_level * spread
    return ActivityCurve(times, score, motion_n, impacts_n, enter, exit_)


def _smooth(values: np.ndarray, width: int) -> np.ndarray:
    if width <= 1 or values.size == 0:
        return values
    width = int(width) | 1
    kernel = np.ones(width, dtype=np.float64) / width
    padded = np.pad(values, (width // 2, width // 2), mode="edge")
    return np.convolve(padded, kernel, mode="valid")[: values.size]


def _hysteresis(curve: ActivityCurve, params: DetectParams) -> List[Tuple[int, int]]:
    """Index ranges where the score sustains above the enter threshold."""
    score = curve.score
    hang_steps = max(1, int(round(params.hang / max(params.grid, 1e-6))))
    spans: List[Tuple[int, int]] = []
    active = False
    start = 0
    below_since: Optional[int] = None

    for i in range(score.size):
        value = score[i]
        if not active:
            if value >= curve.enter_threshold:
                j = i
                while j > 0 and score[j - 1] >= curve.exit_threshold:
                    j -= 1
                start, active, below_since = j, True, None
            continue
        if value < curve.exit_threshold:
            if below_since is None:
                below_since = i
            elif i - below_since >= hang_steps:
                spans.append((start, below_since))
                active, below_since = False, None
        else:
            below_since = None

    if active:
        spans.append((start, score.size - 1))
    return spans


def _merge(spans: Sequence[Tuple[float, float]], gap: float) -> List[Tuple[float, float]]:
    merged: List[Tuple[float, float]] = []
    for start, end in sorted(spans):
        if merged and start - merged[-1][1] <= gap:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _split_long(
    span: Tuple[float, float], curve: ActivityCurve, params: DetectParams
) -> List[Tuple[float, float]]:
    """Cut a span that is too long at its quietest interior moment."""
    start, end = span
    if end - start <= params.max_duration or params.max_duration <= 0:
        return [span]
    times, score = curve.slice(start, end)
    if times.size < 8:
        return [span]
    # Only consider cut points that leave both halves long enough to stand
    # alone as clips.
    guard = max(params.min_duration, 1.0)
    usable = (times >= start + guard) & (times <= end - guard)
    if not usable.any():
        return [span]
    idx = int(np.flatnonzero(usable)[np.argmin(score[usable])])
    cut = float(times[idx])
    return _split_long((start, cut), curve, params) + _split_long((cut, end), curve, params)


def _pad(
    spans: Sequence[Tuple[float, float]], duration: float, params: DetectParams
) -> List[Tuple[float, float]]:
    """Add pre/post roll without letting neighbours overlap."""
    padded: List[Tuple[float, float]] = []
    for i, (start, end) in enumerate(spans):
        prev_end = spans[i - 1][1] if i > 0 else -1e9
        next_start = spans[i + 1][0] if i + 1 < len(spans) else 1e9
        pre = min(params.pre_roll, max(0.0, (start - prev_end) / 2.0))
        post = min(params.post_roll, max(0.0, (next_start - end) / 2.0))
        padded.append((max(0.0, start - pre), min(duration, end + post)))
    return padded


def highlight_score(segment: Segment, *, shot_target: float = 12.0, duration_target: float = 18.0) -> float:
    """Rank rallies for a highlight reel: shot count first, then length."""
    shots = min(segment.shots / max(shot_target, 1.0), 1.0)
    length = min(segment.core_duration / max(duration_target, 1.0), 1.0)
    intensity = min(segment.mean_activity, 1.0)
    return round(0.45 * shots + 0.30 * length + 0.25 * intensity, 4)


def detect_rallies(
    duration: float,
    motion: MotionTrack,
    audio: AudioTrack,
    params: Optional[DetectParams] = None,
) -> Tuple[List[Segment], ActivityCurve]:
    """Detect rallies and return them with the curve they came from."""
    params = params or DetectParams()
    curve = build_activity(duration, motion, audio, params)

    spans = [
        (float(curve.times[a]), float(curve.times[min(b, curve.times.size - 1)]))
        for a, b in _hysteresis(curve, params)
    ]
    spans = _merge(spans, params.merge_gap)

    split: List[Tuple[float, float]] = []
    for span in spans:
        split.extend(_split_long(span, curve, params))
    spans = split

    kept: List[Tuple[float, float]] = []
    for start, end in spans:
        if end - start < params.min_duration:
            continue
        if audio.available and params.min_shots > 0:
            if audio.count_between(start, end) < params.min_shots:
                continue
        kept.append((start, end))

    padded = _pad(kept, duration, params)
    segments: List[Segment] = []
    for (core_start, core_end), (start, end) in zip(kept, padded):
        _, score_slice = curve.slice(core_start, core_end)
        segment = Segment(
            start=round(start, 3),
            end=round(end, 3),
            detect_start=round(core_start, 3),
            detect_end=round(core_end, 3),
            shots=audio.count_between(core_start, core_end) if audio.available else 0,
            peak_activity=round(float(score_slice.max()) if score_slice.size else 0.0, 4),
            mean_activity=round(float(score_slice.mean()) if score_slice.size else 0.0, 4),
        )
        segment.score = highlight_score(segment)
        segments.append(segment)
    return segments, curve


def select_for_reel(
    segments: Sequence[Segment],
    *,
    max_clips: Optional[int] = None,
    max_total: Optional[float] = None,
    chronological: bool = True,
) -> List[Segment]:
    """Pick the best rallies that fit inside a Reels-length budget."""
    ranked = sorted(segments, key=lambda s: (-s.score, s.start))
    chosen: List[Segment] = []
    total = 0.0
    for segment in ranked:
        if max_clips is not None and len(chosen) >= max_clips:
            break
        if max_total is not None and total + segment.duration > max_total:
            continue
        chosen.append(segment)
        total += segment.duration
    if chronological:
        chosen.sort(key=lambda s: s.start)
    return chosen
