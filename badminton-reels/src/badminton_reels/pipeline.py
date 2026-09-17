"""High-level operations: analyse a match, render clips, build a reel."""

from __future__ import annotations

import copy
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .audio import AudioTrack, analyze_audio, empty_track
from .detect import ActivityCurve, DetectParams, Segment, detect_rallies, select_for_reel
from .errors import ProjectError
from .framing import Canvas, PanParams, plan_frame
from .motion import MotionTrack, Roi, analyze_motion
from .probe import VideoInfo, probe
from .project import Clip, Project, make_clips, source_dict
from .render import EncodeParams, RenderOptions, concat_clips, render_clip

Reporter = Optional[Callable[[str], None]]


@dataclass
class AnalyzeSettings:
    """Inputs to the analysis pass."""

    sample_rate: float = 12.0
    analysis_width: int = 192
    roi: Optional[Roi] = None
    auto_roi: bool = True
    onset_sensitivity: float = 1.0
    skip_audio: bool = False
    hwaccel: Optional[str] = None
    detect: DetectParams = field(default_factory=DetectParams)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sample_rate": self.sample_rate,
            "analysis_width": self.analysis_width,
            "roi": list(self.roi) if self.roi else None,
            "auto_roi": self.auto_roi,
            "onset_sensitivity": self.onset_sensitivity,
            "skip_audio": self.skip_audio,
            "hwaccel": self.hwaccel,
            "detect": self.detect.to_dict(),
        }


@dataclass
class AnalysisResult:
    """A project plus the raw signals, so callers can re-detect cheaply."""

    project: Project
    info: VideoInfo
    motion: MotionTrack
    audio: AudioTrack
    curve: ActivityCurve
    segments: List[Segment]


def analyze(
    path: str,
    settings: Optional[AnalyzeSettings] = None,
    *,
    report: Reporter = None,
    progress: Optional[Callable[[float], None]] = None,
    render_defaults: Optional["RenderOptions"] = None,
) -> AnalysisResult:
    """Probe, measure, detect rallies, and build a project for ``path``."""
    settings = settings or AnalyzeSettings()
    say = report or (lambda _msg: None)

    info = probe(path)
    say("source: %s" % info.describe())
    if info.is_portrait:
        say(
            "note: this clip is already portrait; the vertical layouts assume "
            "landscape footage and will mostly pass it through."
        )

    say("measuring motion...")
    motion = analyze_motion(
        info,
        sample_rate=settings.sample_rate,
        analysis_width=settings.analysis_width,
        roi=settings.roi,
        auto_roi=settings.auto_roi,
        hwaccel=settings.hwaccel,
        progress=progress,
    )
    if motion.roi:
        x, y, w, h = motion.roi
        say("court region: x=%.0f%% y=%.0f%% w=%.0f%% h=%.0f%% of frame" % (x * 100, y * 100, w * 100, h * 100))

    if settings.skip_audio or not info.has_audio:
        audio = empty_track()
        if not settings.skip_audio and not info.has_audio:
            say("no audio track: falling back to motion-only detection")
    else:
        say("listening for racket impacts...")
        audio = analyze_audio(info, onset_sensitivity=settings.onset_sensitivity)
        say("found %d impacts" % audio.onsets.size)

    segments, curve = detect_rallies(info.duration, motion, audio, settings.detect)
    say("detected %d rallies" % len(segments))

    project = Project(
        source=source_dict(info),
        clips=make_clips(segments, motion=motion, curve=curve, track_rate=settings.sample_rate),
        created=time.strftime("%Y-%m-%dT%H:%M:%S"),
        analysis={
            "settings": settings.to_dict(),
            "roi": [round(v, 4) for v in motion.roi] if motion.roi else None,
            "impacts": int(audio.onsets.size),
            "enter_threshold": round(curve.enter_threshold, 4),
            "exit_threshold": round(curve.exit_threshold, 4),
            "tool_version": _version(),
        },
        render=render_options_to_dict(render_defaults or RenderOptions()),
    )
    return AnalysisResult(project, info, motion, audio, curve, segments)


def _version() -> str:
    from . import __version__

    return __version__


# ---------------------------------------------------------------------------
# Render option plumbing: dataclasses <-> plain dicts for the CLI and JSON.
# ---------------------------------------------------------------------------

def render_options_to_dict(options: RenderOptions) -> Dict[str, Any]:
    return {
        "layout": options.layout,
        "pan": options.pan.mode,
        "zoom": options.pan.zoom,
        "pan_tau": options.pan.tau,
        "pan_max_speed": options.pan.max_speed,
        "center_bias": options.pan.center_bias,
        "track_y": options.pan.track_y,
        "width": options.encode.canvas.width,
        "height": options.encode.canvas.height,
        "fps": options.encode.fps,
        "crf": options.encode.crf,
        "preset": options.encode.preset,
        "speed": options.speed,
        "fade": options.fade,
        "mute": options.mute,
        "music": options.music,
        "music_volume": options.music_volume,
        "tonemap": options.tonemap,
        "blur_sigma": options.blur_sigma,
    }


def render_options_from_dict(data: Optional[Dict[str, Any]]) -> RenderOptions:
    data = dict(data or {})
    canvas = Canvas(
        width=int(data.get("width") or 1080),
        height=int(data.get("height") or 1920),
    )
    encode = EncodeParams(
        canvas=canvas,
        fps=float(data.get("fps") or 30.0),
        crf=int(data.get("crf") if data.get("crf") is not None else 20),
        preset=str(data.get("preset") or "medium"),
    )
    pan = PanParams(
        mode=str(data.get("pan") or "smooth"),
        zoom=float(data.get("zoom") or 1.0),
        tau=float(data.get("pan_tau") or 0.7),
        max_speed=float(data.get("pan_max_speed") or 0.6),
        center_bias=float(data.get("center_bias", 0.15) or 0.0),
        track_y=bool(data.get("track_y", True)),
    )
    return RenderOptions(
        layout=str(data.get("layout") or "follow"),
        pan=pan,
        encode=encode,
        speed=float(data.get("speed") or 1.0),
        fade=float(data.get("fade", 0.12) or 0.0),
        mute=bool(data.get("mute")),
        music=data.get("music"),
        music_volume=float(data.get("music_volume", 0.35) or 0.0),
        tonemap=data.get("tonemap"),
        blur_sigma=float(data.get("blur_sigma") or 26.0),
    )


def options_for_clip(clip: Clip, base: RenderOptions) -> RenderOptions:
    """Apply a clip's per-clip overrides on top of the project defaults."""
    pan = replace(base.pan)
    if clip.pan:
        pan.mode = clip.pan
    if clip.zoom:
        pan.zoom = float(clip.zoom)
    options = copy.copy(base)
    options.pan = pan
    options.encode = replace(base.encode, canvas=base.encode.canvas)
    if clip.layout:
        options.layout = clip.layout
    if clip.speed:
        options.speed = float(clip.speed)
    return options


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return slug or "clip"


def clip_filename(clip: Clip, index: int) -> str:
    return "%02d-%s.mp4" % (index, _slug(clip.label or clip.id))


def auto_label(clip: Clip) -> str:
    """The badge drawn on a clip when labels are switched on."""
    name = clip.label or clip.id
    if clip.shots > 0:
        return "%s  ·  %d shots" % (name, clip.shots)
    return name


def _plan_for(clip: Clip, info: VideoInfo, options: RenderOptions) -> Any:
    times, cx, cy = clip.track_arrays()
    speed = max(options.speed, 1e-6)
    if speed != 1.0:
        times = [t / speed for t in times]
    return plan_frame(
        source_w=info.display_width,
        source_h=info.display_height,
        duration=clip.duration / speed,
        canvas=options.encode.canvas,
        layout=options.layout,
        pan=options.pan,
        sample_times=times,
        cx=cx,
        cy=cy,
        out_fps=options.encode.fps,
        command_rate=options.command_rate,
    )


def render_clips(
    project: Project,
    out_dir: str,
    *,
    options: Optional[RenderOptions] = None,
    clips: Optional[Sequence[Clip]] = None,
    labels: bool = False,
    report: Reporter = None,
    quiet: bool = True,
) -> List[str]:
    """Render each selected clip as its own vertical MP4."""
    say = report or (lambda _msg: None)
    base = options or render_options_from_dict(project.render)
    info = project.video_info()
    _require_source(info)
    selected = list(clips if clips is not None else project.kept())
    if not selected:
        raise ProjectError("no clips selected to render")

    os.makedirs(out_dir, exist_ok=True)
    written: List[str] = []
    seen_warnings: set = set()
    for index, clip in enumerate(selected, start=1):
        clip_options = options_for_clip(clip, base)
        out_path = os.path.join(out_dir, clip_filename(clip, index))
        say(
            "[%d/%d] %s  %.1fs  %s"
            % (index, len(selected), clip.label or clip.id, clip.duration, clip_options.layout)
        )
        warnings = render_clip(
            info,
            start=clip.start,
            end=clip.end,
            out_path=out_path,
            options=clip_options,
            plan=_plan_for(clip, info, clip_options),
            label=auto_label(clip) if labels else None,
            quiet=quiet,
        )
        for warning in warnings:
            if warning not in seen_warnings:
                seen_warnings.add(warning)
                say("warning: %s" % warning)
        written.append(out_path)
    return written


def render_reel(
    project: Project,
    out_path: str,
    *,
    options: Optional[RenderOptions] = None,
    max_clips: Optional[int] = None,
    max_duration: Optional[float] = 90.0,
    labels: bool = True,
    report: Reporter = None,
    quiet: bool = True,
    keep_parts: Optional[str] = None,
) -> Tuple[str, List[Clip]]:
    """Render the best rallies and join them into one reel."""
    say = report or (lambda _msg: None)
    base = options or render_options_from_dict(project.render)
    candidates = project.kept()
    if not candidates:
        raise ProjectError("no clips kept; nothing to build a reel from")

    chosen = select_for_reel(
        candidates, max_clips=max_clips, max_total=max_duration, chronological=True
    )
    if not chosen:
        raise ProjectError(
            "no rally fits inside the %.0fs limit; raise --max-duration" % (max_duration or 0)
        )
    total = sum(clip.duration for clip in chosen) / max(base.speed, 1e-6)
    say("reel: %d rallies, about %.1fs" % (len(chosen), total))

    work_dir = keep_parts or tempfile.mkdtemp(prefix="bdr-reel-")
    try:
        parts = render_clips(
            project,
            work_dir,
            options=base,
            clips=chosen,
            labels=labels,
            report=report,
            quiet=quiet,
        )
        concat_clips(parts, out_path, quiet=quiet)
    finally:
        if keep_parts is None:
            shutil.rmtree(work_dir, ignore_errors=True)
    return out_path, chosen


def _require_source(info: VideoInfo) -> None:
    if not info.path or not os.path.isfile(info.path):
        raise ProjectError(
            "the source video is missing: %s\n"
            "Move it back, or edit the \"source.path\" field in the project file." % info.path
        )
