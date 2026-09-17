"""The editable timeline.

Analysis is expensive and framing is cheap, so the pipeline splits in two: a
single analysis pass writes a project file, and every later operation - review,
trimming, reordering, rendering, re-rendering with a different layout - reads
and rewrites that file. The project is plain JSON on purpose: it is meant to
be edited by the review page, by the ``bdr edit`` subcommand, or by hand.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .detect import score_rally
from .errors import ProjectError
from .probe import VideoInfo, format_duration

PROJECT_VERSION = 1

# Shorter than this and there is nothing to watch.
MIN_CLIP_DURATION = 0.4


@dataclass
class Clip:
    """One rally on the timeline."""

    id: str
    start: float
    end: float
    keep: bool = True
    label: str = ""
    score: float = 0.0
    shots: int = 0
    detect_start: Optional[float] = None
    detect_end: Optional[float] = None
    notes: str = ""
    # Per-clip overrides; None means "use the project default".
    layout: Optional[str] = None
    zoom: Optional[float] = None
    pan: Optional[str] = None
    speed: Optional[float] = None
    # Optional per-clip override of the action track, as
    # ``[[t_relative, cx, cy], ...]``. Normally left empty: the track is
    # sliced out of the whole-match track in ``Project.track_for`` so that
    # moving a clip's in-point cannot leave its pan behind.
    track: List[List[float]] = field(default_factory=list, repr=False)

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def core_duration(self) -> float:
        if self.detect_start is None or self.detect_end is None:
            return self.duration
        return max(0.0, self.detect_end - self.detect_start)

    def track_arrays(self) -> Tuple[List[float], List[float], List[float]]:
        """The per-clip track override, if one was stored."""
        times = [float(p[0]) for p in self.track]
        cx = [float(p[1]) for p in self.track]
        cy = [float(p[2]) if len(p) > 2 else 0.5 for p in self.track]
        return times, cx, cy

    def set_range(self, start: float, end: float, *, limit: float = 0.0) -> None:
        """Move both cut points, keeping the clip inside the source."""
        start = max(0.0, float(start))
        end = float(end)
        if limit > 0:
            end = min(end, limit)
        if end - start < MIN_CLIP_DURATION:
            raise ProjectError(
                "a clip must be at least %.1fs long" % MIN_CLIP_DURATION
            )
        self.start, self.end = round(start, 3), round(end, 3)

    def to_dict(self) -> Dict[str, Any]:
        """Drop unset overrides so hand-editing a project file stays pleasant."""
        always = ("id", "start", "end", "keep", "label")
        data = asdict(self)
        return {
            key: value
            for key, value in data.items()
            if key in always or not (value is None or value == "" or value == [])
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Clip":
        known = set(cls("x", 0.0, 0.0).__dict__)
        kwargs = {k: v for k, v in data.items() if k in known}
        if "id" not in kwargs:
            raise ProjectError("a clip entry has no id")
        return cls(**kwargs)


@dataclass
class Project:
    """A source file plus the rally timeline detected in it."""

    source: Dict[str, Any]
    clips: List[Clip] = field(default_factory=list)
    version: int = PROJECT_VERSION
    created: str = ""
    analysis: Dict[str, Any] = field(default_factory=dict)
    render: Dict[str, Any] = field(default_factory=dict)
    path: Optional[str] = None

    # -- source helpers ---------------------------------------------------
    @property
    def source_path(self) -> str:
        return str(self.source.get("path", ""))

    @property
    def duration(self) -> float:
        return float(self.source.get("duration") or 0.0)

    def video_info(self) -> VideoInfo:
        """Rebuild a VideoInfo from the stored fields (no ffprobe call)."""
        source = self.source
        return VideoInfo(
            path=str(source.get("path", "")),
            duration=float(source.get("duration") or 0.0),
            width=int(source.get("width") or 0),
            height=int(source.get("height") or 0),
            fps=float(source.get("fps") or 30.0),
            rotation=int(source.get("rotation") or 0),
            codec=str(source.get("codec") or ""),
            pix_fmt=str(source.get("pix_fmt") or ""),
            color_transfer=source.get("color_transfer"),
            color_primaries=source.get("color_primaries"),
            color_space=source.get("color_space"),
            has_audio=bool(source.get("has_audio")),
            audio_sample_rate=source.get("audio_sample_rate"),
            audio_channels=source.get("audio_channels"),
        )

    # -- whole-match signals ---------------------------------------------
    @property
    def preview_path(self) -> Optional[str]:
        """The scrub proxy, resolved next to the project file."""
        name = (self.analysis.get("proxy") or "") if self.analysis else ""
        if not name:
            return None
        if os.path.isabs(name):
            return name
        base = os.path.dirname(os.path.abspath(self.path)) if self.path else "."
        return os.path.join(base, name)

    def track_for(self, clip: Clip) -> Tuple[List[float], List[float], List[float]]:
        """Action track for ``clip``, with times relative to its in-point.

        Sliced out of the whole-match track, so a clip that has just been
        trimmed or dragged gets the pan for where it now sits.
        """
        if clip.track:
            return clip.track_arrays()
        track = (self.analysis or {}).get("track") or {}
        rate = float(track.get("rate") or 0.0)
        cx_all, cy_all = track.get("cx") or [], track.get("cy") or []
        if rate <= 0 or not cx_all:
            return [], [], []
        count = len(cx_all)
        first = max(0, int(clip.start * rate))
        last = min(count - 1, int(clip.end * rate) + 1)
        if last < first:
            first = last = min(max(first, 0), count - 1)
        times, cx, cy = [], [], []
        for index in range(first, last + 1):
            times.append(round(max(0.0, index / rate - clip.start), 3))
            cx.append(float(cx_all[index]))
            cy.append(float(cy_all[index]) if index < len(cy_all) else 0.5)
        return times, cx, cy

    # -- clip helpers -----------------------------------------------------
    def kept(self) -> List[Clip]:
        return [clip for clip in self.clips if clip.keep]

    def by_id(self, clip_id: str) -> Clip:
        for clip in self.clips:
            if clip.id == clip_id:
                return clip
        raise ProjectError("no clip with id %r (have: %s)" % (clip_id, ", ".join(c.id for c in self.clips)))

    def select(self, ids: Optional[Iterable[str]] = None) -> List[Clip]:
        if not ids:
            return list(self.clips)
        return [self.by_id(clip_id) for clip_id in ids]

    def renumber(self, *, template: str = "Rally %d") -> None:
        """Relabel kept clips 1..n in timeline order.

        Dropped clips are labelled by id so a stale number cannot collide
        with a kept rally's.
        """
        index = 1
        for clip in sorted(self.clips, key=lambda c: c.start):
            if clip.keep:
                clip.label = template % index
                index += 1
            else:
                clip.label = "%s (dropped)" % clip.id

    def trim(self, clip_id: str, *, head: float = 0.0, tail: float = 0.0) -> Clip:
        """Move a clip's in/out points. Positive values lengthen the clip."""
        clip = self.by_id(clip_id)
        start = max(0.0, clip.start - head)
        end = min(self.duration or clip.end + tail, clip.end + tail)
        if end - start < 0.2:
            raise ProjectError("trimming %s would leave less than 0.2s" % clip_id)
        clip.start, clip.end = round(start, 3), round(end, 3)
        return clip

    def next_id(self) -> str:
        """The next free clip id, so ids stay unique after edits."""
        used = {clip.id for clip in self.clips}
        index = len(self.clips) + 1
        while clip_id(index) in used:
            index += 1
        return clip_id(index)

    def add_clip(
        self, start: float, end: float, *, label: Optional[str] = None
    ) -> Clip:
        """Add a rally the detector missed."""
        clip = Clip(id=self.next_id(), start=0.0, end=1.0, keep=True)
        clip.set_range(start, end, limit=self.duration)
        clip.label = label or "Rally"
        self.rescore(clip)
        self.clips.append(clip)
        self.sort()
        return clip

    def split_clip(self, clip_id_: str, at: float) -> Tuple[Clip, Clip]:
        """Cut one clip in two at ``at`` seconds on the source timeline."""
        clip = self.by_id(clip_id_)
        if not clip.start + MIN_CLIP_DURATION <= at <= clip.end - MIN_CLIP_DURATION:
            raise ProjectError(
                "%.2fs is not far enough inside %s (%.2f-%.2f) to split it"
                % (at, clip_id_, clip.start, clip.end)
            )
        tail = Clip(
            id=self.next_id(),
            start=round(at, 3),
            end=clip.end,
            keep=clip.keep,
            label=(clip.label or clip.id) + " b",
            shots=0,
            layout=clip.layout,
            zoom=clip.zoom,
            pan=clip.pan,
            speed=clip.speed,
        )
        clip.end = round(at, 3)
        # The counts and score belonged to the original span; redo both halves.
        self.rescore(clip)
        self.rescore(tail)
        clip.detect_start = clip.detect_end = None
        self.clips.append(tail)
        self.sort()
        return clip, tail

    def remove_clip(self, clip_id_: str) -> Clip:
        clip = self.by_id(clip_id_)
        self.clips.remove(clip)
        return clip

    def count_impacts(self, start: float, end: float) -> int:
        """Racket impacts inside a span, from the stored impact times."""
        impacts = (self.analysis or {}).get("impact_times") or []
        return sum(1 for t in impacts if start <= t <= end)

    def mean_activity(self, start: float, end: float) -> float:
        """Average fused activity over a span, from the stored curve."""
        curve = (self.analysis or {}).get("curve") or {}
        scores = curve.get("score") or []
        rate = float(curve.get("rate") or 0.0)
        if not scores or rate <= 0 or end <= start:
            return 0.0
        first = max(0, int(start * rate))
        last = min(len(scores) - 1, int(end * rate))
        if last < first:
            return float(scores[first]) if first < len(scores) else 0.0
        window = scores[first : last + 1]
        return sum(float(v) for v in window) / len(window)

    def rescore(self, clip: Clip) -> Clip:
        """Recount impacts and re-rank a clip after its range changed.

        Without this a hand-trimmed or split rally would keep a score that
        describes a span it no longer covers - and "keep the best eight"
        would quietly rank it last.
        """
        clip.shots = self.count_impacts(clip.start, clip.end)
        clip.score = score_rally(
            clip.shots, clip.duration, self.mean_activity(clip.start, clip.end)
        )
        return clip

    def sort(self) -> None:
        self.clips.sort(key=lambda clip: (clip.start, clip.id))

    def total_duration(self, *, only_kept: bool = True) -> float:
        clips = self.kept() if only_kept else self.clips
        return sum(clip.duration for clip in clips)

    def summary(self) -> str:
        kept = self.kept()
        return "%d rallies (%d kept, %s of footage)" % (
            len(self.clips),
            len(kept),
            format_duration(self.total_duration()),
        )

    # -- serialisation ----------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "created": self.created or time.strftime("%Y-%m-%dT%H:%M:%S"),
            "source": self.source,
            "analysis": self.analysis,
            "render": self.render,
            "clips": [clip.to_dict() for clip in self.clips],
        }

    def save(self, path: Optional[str] = None) -> str:
        target = path or self.path
        if not target:
            raise ProjectError("no path to save the project to")
        directory = os.path.dirname(os.path.abspath(target))
        os.makedirs(directory, exist_ok=True)
        # Write beside the target then move, so an interrupted save cannot
        # destroy an edit session's only copy of the timeline.
        temporary = target + ".tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(self.to_dict(), handle, indent=1, sort_keys=False)
            handle.write("\n")
        os.replace(temporary, target)
        self.path = target
        return target

    @classmethod
    def from_dict(cls, data: Dict[str, Any], *, path: Optional[str] = None) -> "Project":
        if not isinstance(data, dict) or "source" not in data:
            raise ProjectError("not a badminton-reels project file")
        version = int(data.get("version") or PROJECT_VERSION)
        if version > PROJECT_VERSION:
            raise ProjectError(
                "project was written by a newer version (%d > %d)" % (version, PROJECT_VERSION)
            )
        clips = [Clip.from_dict(entry) for entry in data.get("clips") or []]
        return cls(
            source=dict(data.get("source") or {}),
            clips=clips,
            version=version,
            created=str(data.get("created") or ""),
            analysis=dict(data.get("analysis") or {}),
            render=dict(data.get("render") or {}),
            path=path,
        )

    @classmethod
    def load(cls, path: str) -> "Project":
        if not os.path.isfile(path):
            raise ProjectError("no such project file: %s" % path)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except json.JSONDecodeError as exc:
            raise ProjectError("%s is not valid JSON: %s" % (path, exc)) from exc
        return cls.from_dict(data, path=path)


def source_dict(info: VideoInfo) -> Dict[str, Any]:
    """The part of a VideoInfo worth keeping in a project file."""
    data: Dict[str, Any] = {
        "path": info.path,
        "duration": round(info.duration, 3),
        "width": info.width,
        "height": info.height,
        "display_width": info.display_width,
        "display_height": info.display_height,
        "fps": round(info.fps, 6),
        "rotation": info.rotation,
        "codec": info.codec,
        "pix_fmt": info.pix_fmt,
        "has_audio": info.has_audio,
        "hdr": info.is_hdr,
    }
    for key in ("color_transfer", "color_primaries", "color_space"):
        value = getattr(info, key)
        if value:
            data[key] = value
    if info.audio_sample_rate:
        data["audio_sample_rate"] = info.audio_sample_rate
    if info.audio_channels:
        data["audio_channels"] = info.audio_channels
    try:
        data["size"] = os.path.getsize(info.path)
    except OSError:
        pass
    return data


def clip_id(index: int) -> str:
    return "r%02d" % index


def make_clips(segments: Sequence[Any]) -> List[Clip]:
    """Build timeline clips from detected segments."""
    clips: List[Clip] = []
    for index, segment in enumerate(segments, start=1):
        clips.append(
            Clip(
                id=clip_id(index),
                start=round(float(segment.start), 3),
                end=round(float(segment.end), 3),
                label="Rally %d" % index,
                score=round(float(segment.score), 4),
                shots=int(segment.shots),
                detect_start=round(float(segment.detect_start), 3),
                detect_end=round(float(segment.detect_end), 3),
            )
        )
    return clips


def track_dict(motion: Any, *, rate: float = 12.0) -> Dict[str, Any]:
    """The whole-match action track, decimated and rounded for JSON.

    Stored once for the file rather than per clip: the editor needs it to
    draw the timeline, and clips slice it as they are moved about.
    """
    if motion is None or motion.sample_rate <= 0 or motion.energy.size == 0:
        return {}
    step = max(1, int(round(motion.sample_rate / max(rate, 0.5))))
    return {
        "rate": round(motion.sample_rate / step, 6),
        "cx": [round(float(v), 3) for v in motion.cx[::step]],
        "cy": [round(float(v), 3) for v in motion.cy[::step]],
    }


def curve_dict(curve: Any, *, rate: float = 4.0) -> Dict[str, Any]:
    """The activity score for the whole match, for the timeline graph."""
    if curve is None or len(curve.times) == 0:
        return {}
    grid = float(curve.times[1] - curve.times[0]) if len(curve.times) > 1 else 0.1
    step = max(1, int(round((1.0 / max(rate, 0.5)) / max(grid, 1e-6))))
    return {
        "rate": round(1.0 / (grid * step), 6),
        "score": [round(float(v), 3) for v in curve.score[::step]],
        "enter": round(float(curve.enter_threshold), 4),
        "exit": round(float(curve.exit_threshold), 4),
    }
