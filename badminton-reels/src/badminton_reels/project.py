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

from .errors import ProjectError
from .probe import VideoInfo, format_duration

PROJECT_VERSION = 1


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
    # Sampled action position: [[t_relative, cx, cy], ...]
    track: List[List[float]] = field(default_factory=list, repr=False)
    # Sampled activity score for the review page: [[t_relative, score], ...]
    curve: List[List[float]] = field(default_factory=list, repr=False)

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def core_duration(self) -> float:
        if self.detect_start is None or self.detect_end is None:
            return self.duration
        return max(0.0, self.detect_end - self.detect_start)

    def track_arrays(self) -> Tuple[List[float], List[float], List[float]]:
        times = [float(p[0]) for p in self.track]
        cx = [float(p[1]) for p in self.track]
        cy = [float(p[2]) if len(p) > 2 else 0.5 for p in self.track]
        return times, cx, cy

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


def make_clips(
    segments: Sequence[Any],
    *,
    motion: Any = None,
    curve: Any = None,
    track_rate: float = 12.0,
    curve_rate: float = 5.0,
) -> List[Clip]:
    """Build timeline clips from detected segments, attaching their tracks."""
    clips: List[Clip] = []
    for index, segment in enumerate(segments, start=1):
        clip = Clip(
            id=clip_id(index),
            start=round(float(segment.start), 3),
            end=round(float(segment.end), 3),
            label="Rally %d" % index,
            score=round(float(segment.score), 4),
            shots=int(segment.shots),
            detect_start=round(float(segment.detect_start), 3),
            detect_end=round(float(segment.detect_end), 3),
        )
        if motion is not None:
            times, cx, cy = motion.window(clip.start, clip.end)
            step = max(1, int(round(motion.sample_rate / max(track_rate, 0.5))))
            track: List[List[float]] = []
            for t, x, y in zip(times[::step], cx[::step], cy[::step]):
                # The window includes the sample straddling the in-point, so
                # clamp to the clip and keep the times strictly increasing.
                relative = round(max(0.0, float(t) - clip.start), 3)
                if track and relative <= track[-1][0]:
                    track[-1] = [relative, round(float(x), 4), round(float(y), 4)]
                    continue
                track.append([relative, round(float(x), 4), round(float(y), 4)])
            clip.track = track
        if curve is not None:
            times, score = curve.slice(clip.start, clip.end)
            if len(times):
                grid = times[1] - times[0] if len(times) > 1 else 0.1
                step = max(1, int(round((1.0 / max(curve_rate, 0.5)) / max(grid, 1e-6))))
                clip.curve = [
                    [round(float(t) - clip.start, 2), round(float(s), 3)]
                    for t, s in zip(times[::step], score[::step])
                ]
        clips.append(clip)
    return clips
