"""Inspect a source file with ffprobe."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .errors import ProbeError
from .ffutil import FFPROBE, require_binaries, run

# Transfer characteristics that mean the file carries HDR and therefore needs
# tone mapping before it can become an SDR H.264 Reel. iPhones shoot HLG.
HDR_TRANSFERS = {"arib-std-b67", "smpte2084", "smpte428", "bt2020-10", "bt2020-12"}


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _ratio(value: Any, default: float = 0.0) -> float:
    """Parse ffprobe rationals such as '60000/1001'."""
    if not value:
        return default
    text = str(value)
    if "/" in text:
        num, _, den = text.partition("/")
        num_f, den_f = _to_float(num), _to_float(den)
        return num_f / den_f if den_f else default
    return _to_float(text, default)


@dataclass
class VideoInfo:
    """Everything the rest of the pipeline needs to know about the source."""

    path: str
    duration: float
    width: int
    height: int
    fps: float
    rotation: int = 0
    codec: str = ""
    pix_fmt: str = ""
    color_transfer: Optional[str] = None
    color_primaries: Optional[str] = None
    color_space: Optional[str] = None
    has_audio: bool = False
    audio_sample_rate: Optional[int] = None
    audio_channels: Optional[int] = None
    bit_rate: Optional[int] = None
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def rotated(self) -> bool:
        """True when the display orientation swaps the coded dimensions."""
        return abs(self.rotation) % 180 == 90

    @property
    def display_width(self) -> int:
        """Frame width as ffmpeg's filters see it (rotation already applied)."""
        return self.height if self.rotated else self.width

    @property
    def display_height(self) -> int:
        return self.width if self.rotated else self.height

    @property
    def aspect(self) -> float:
        return self.display_width / float(self.display_height or 1)

    @property
    def is_portrait(self) -> bool:
        return self.display_height > self.display_width

    @property
    def is_hdr(self) -> bool:
        return (self.color_transfer or "").lower() in HDR_TRANSFERS

    def describe(self) -> str:
        bits = [
            os.path.basename(self.path),
            "%dx%d" % (self.display_width, self.display_height),
            "%.3g fps" % self.fps,
            "%s" % format_duration(self.duration),
            self.codec or "?",
        ]
        if self.is_hdr:
            bits.append("HDR (%s)" % self.color_transfer)
        if self.rotation:
            bits.append("rotation %d" % self.rotation)
        bits.append("audio: %s" % ("yes" if self.has_audio else "none"))
        return " | ".join(bits)


def format_duration(seconds: float) -> str:
    """Format seconds as m:ss.s (or h:mm:ss.s past an hour)."""
    seconds = max(0.0, float(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return "%d:%02d:%04.1f" % (int(hours), int(minutes), secs)
    return "%d:%04.1f" % (int(minutes), secs)


def parse_timecode(text: str) -> float:
    """Parse '12', '1:23', '1:02:03.5' or '90s' into seconds."""
    text = str(text).strip().lower()
    if text.endswith("s"):
        text = text[:-1]
    parts = text.split(":")
    try:
        values = [float(p) for p in parts]
    except ValueError as exc:
        raise ValueError("cannot parse timecode %r" % text) from exc
    total = 0.0
    for value in values:
        total = total * 60.0 + value
    return total


def _rotation_from_stream(stream: Dict[str, Any]) -> int:
    for side in stream.get("side_data_list") or []:
        if "rotation" in side:
            try:
                return int(round(float(side["rotation"])))
            except (TypeError, ValueError):
                continue
    tags = stream.get("tags") or {}
    if "rotate" in tags:
        try:
            return int(round(float(tags["rotate"])))
        except (TypeError, ValueError):
            pass
    return 0


def probe(path: str) -> VideoInfo:
    """Return a VideoInfo for ``path`` or raise ProbeError."""
    require_binaries()
    if not os.path.isfile(path):
        raise ProbeError("no such file: %s" % path)
    proc = run(
        [
            FFPROBE,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            path,
        ],
        check=False,
    )
    if proc.returncode != 0:
        raise ProbeError(
            "ffprobe could not read %s\n%s"
            % (path, (proc.stderr or b"").decode("utf-8", "replace").strip())
        )
    try:
        data = json.loads(proc.stdout.decode("utf-8", "replace"))
    except json.JSONDecodeError as exc:
        raise ProbeError("ffprobe returned invalid JSON for %s" % path) from exc

    streams: List[Dict[str, Any]] = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None:
        raise ProbeError("%s has no video stream" % path)

    fmt = data.get("format") or {}
    duration = _to_float(fmt.get("duration")) or _to_float(video.get("duration"))
    fps = _ratio(video.get("avg_frame_rate")) or _ratio(video.get("r_frame_rate"), 30.0)
    if duration <= 0:
        frames = _to_float(video.get("nb_frames"))
        duration = frames / fps if frames and fps else 0.0
    if duration <= 0:
        raise ProbeError("could not determine the duration of %s" % path)

    bit_rate = fmt.get("bit_rate") or video.get("bit_rate")
    return VideoInfo(
        path=os.path.abspath(path),
        duration=duration,
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        fps=fps if fps > 0 else 30.0,
        rotation=_rotation_from_stream(video),
        codec=str(video.get("codec_name") or ""),
        pix_fmt=str(video.get("pix_fmt") or ""),
        color_transfer=video.get("color_transfer"),
        color_primaries=video.get("color_primaries"),
        color_space=video.get("color_space"),
        has_audio=audio is not None,
        audio_sample_rate=int(_to_float((audio or {}).get("sample_rate"))) or None,
        audio_channels=int(_to_float((audio or {}).get("channels"))) or None,
        bit_rate=int(_to_float(bit_rate)) or None,
        raw=data,
    )
