"""Render vertical clips with ffmpeg.

Output targets Instagram Reels: 1080x1920, H.264 high profile, yuv420p,
bt709-tagged, 30 fps, two-second keyframe interval, AAC stereo at 48 kHz,
faststart. Reels needs an audio track, so silent sources get one.

iPhone footage arrives as 10-bit HLG HEVC. Converting that straight to H.264
without tone mapping is what makes exported clips look washed out and grey,
so HDR sources get a proper linear-light tone map when this ffmpeg build has
zscale.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from .errors import RenderError
from .ffutil import (
    escape_filter_path,
    escape_filter_value,
    ffmpeg_base,
    has_filter,
    join_filters,
    run,
)
from .framing import Canvas, FramePlan, PanParams, plan_frame, sendcmd_script
from .probe import VideoInfo

FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Helvetica.ttc",
    "/System/Library/Fonts/HelveticaNeue.ttc",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
)

# Reels puts its own UI over the top ~9% and bottom ~22% of the frame.
SAFE_TOP = 0.085
SAFE_BOTTOM = 0.24


@dataclass
class EncodeParams:
    """Encoder settings for a Reels-ready MP4."""

    canvas: Canvas = field(default_factory=Canvas)
    fps: float = 30.0
    crf: int = 20
    preset: str = "medium"
    audio_bitrate: str = "160k"
    audio_rate: int = 48000

    @property
    def gop(self) -> int:
        return max(2, int(round(self.fps * 2)))


@dataclass
class RenderOptions:
    """Creative choices for a render, all overridable per clip."""

    layout: str = "follow"
    pan: PanParams = field(default_factory=PanParams)
    encode: EncodeParams = field(default_factory=EncodeParams)
    speed: float = 1.0
    fade: float = 0.12
    label: Optional[str] = None
    label_size: float = 0.045          # fraction of canvas height
    mute: bool = False
    music: Optional[str] = None
    music_volume: float = 0.35
    source_volume: float = 1.0
    tonemap: Optional[bool] = None     # None means "decide from the source"
    tonemap_npl: float = 100.0
    blur_sigma: float = 26.0
    command_rate: float = 12.0

    def want_tonemap(self, info: VideoInfo) -> bool:
        return info.is_hdr if self.tonemap is None else bool(self.tonemap)


def find_font() -> Optional[str]:
    """First usable bold sans-serif font on this machine."""
    for path in FONT_CANDIDATES:
        if os.path.isfile(path):
            return path
    matcher = shutil.which("fc-match")
    if matcher:
        try:
            out = subprocess.run(
                [matcher, "-f", "%{file}", "sans:bold"],
                capture_output=True,
                text=True,
                check=False,
            ).stdout.strip()
            if out and os.path.isfile(out):
                return out
        except OSError:
            pass
    return None


def tonemap_chain(info: VideoInfo, npl: float = 100.0) -> Tuple[str, Optional[str]]:
    """Filter chain that converts an HDR source to SDR bt709.

    Returns the chain and, when the build cannot do it properly, a warning.
    """
    if has_filter("zscale"):
        transfer = info.color_transfer or "arib-std-b67"
        parts = ["zscale=tin=%s:t=linear:npl=%g" % (transfer, npl)]
        if info.color_primaries:
            parts[0] += ":pin=%s" % info.color_primaries
        if info.color_space:
            parts[0] += ":min=%s" % info.color_space
        parts += [
            "format=gbrpf32le",
            "tonemap=tonemap=hable:desat=0",
            "zscale=p=bt709:t=bt709:m=bt709:r=tv",
            "format=yuv420p",
        ]
        return join_filters(parts), None
    if has_filter("colorspace"):
        return (
            "colorspace=all=bt709:iall=bt2020-10:fast=1,format=yuv420p",
            "this ffmpeg has no zscale filter, so HDR tone mapping is approximate; "
            "colours may look flat. Install a full ffmpeg build for the proper curve.",
        )
    return (
        "format=yuv420p",
        "this ffmpeg can neither tone map nor convert colour spaces; HDR footage "
        "will look washed out. Install a full ffmpeg build, or shoot in SDR.",
    )


def _atempo_chain(speed: float) -> str:
    """atempo only covers 0.5-100x, so chain stages for stronger changes."""
    stages: List[str] = []
    remaining = speed
    while remaining < 0.5:
        stages.append("atempo=0.5")
        remaining /= 0.5
    while remaining > 2.0:
        stages.append("atempo=2.0")
        remaining /= 2.0
    if abs(remaining - 1.0) > 1e-3:
        stages.append("atempo=%.6g" % remaining)
    return join_filters(stages)


def _blur_base(canvas: Canvas, sigma: float) -> str:
    """Blurred, slightly darkened fill for the parts panes do not cover.

    Blurring a small copy and scaling it back up looks the same as blurring at
    full size and is far cheaper.
    """
    small_w, small_h = max(16, canvas.width // 4), max(16, canvas.height // 4)
    return join_filters([
        "scale=%d:%d:force_original_aspect_ratio=increase" % (small_w, small_h),
        "crop=%d:%d" % (small_w, small_h),
        "gblur=sigma=%.3g" % max(1.0, sigma / 4.0),
        "scale=%d:%d" % (canvas.width, canvas.height),
        "eq=brightness=-0.06:saturation=1.05",
        "setsar=1",
    ])


def build_video_graph(
    plan: FramePlan,
    info: VideoInfo,
    options: RenderOptions,
    *,
    duration: float,
    script_dir: str,
    label: Optional[str] = None,
) -> Tuple[List[str], List[str]]:
    """Build the filter_complex chain for one clip.

    Returns the list of graph statements (to be joined with ';') and any
    warnings worth showing the user.
    """
    warnings: List[str] = []
    canvas = plan.canvas
    statements: List[str] = []

    head: List[str] = []
    if options.want_tonemap(info):
        chain, warning = tonemap_chain(info, options.tonemap_npl)
        head.append(chain)
        if warning:
            warnings.append(warning)
    head.append("setpts=PTS-STARTPTS")
    if abs(options.speed - 1.0) > 1e-3:
        head.append("setpts=PTS/%.6g" % options.speed)
    head.append("fps=%.6g" % options.encode.fps)
    statements.append("[0:v]%s[src]" % join_filters(head))

    # One pane that covers the canvas needs no background and no overlay.
    single_pass = (
        len(plan.panes) == 1
        and plan.panes[0].dest_x == 0
        and plan.panes[0].dest_y == 0
        and plan.panes[0].scaled_w == canvas.width
        and plan.panes[0].scaled_h == canvas.height
    )
    # `--blur 0` fills the gaps with black instead of a blurred copy.
    use_blur = plan.blur_background and options.blur_sigma > 0 and not single_pass
    branches = len(plan.panes) + (1 if use_blur else 0)

    if branches > 1:
        labels = ["b%d" % i for i in range(branches)]
        statements.append("[src]split=%d%s" % (branches, "".join("[%s]" % n for n in labels)))
    else:
        labels = ["src"]

    pane_labels: List[str] = []
    for index, pane in enumerate(plan.panes):
        source = labels[index]
        name = "crop@p%d" % index
        steps: List[str] = []
        if pane.animated:
            script = os.path.join(script_dir, "pan%d.cmd" % index)
            with open(script, "w", encoding="utf-8") as handle:
                handle.write(sendcmd_script(pane, name))
            steps.append("sendcmd=f=%s" % escape_filter_path(script))
        steps.append(
            "%s=w=%d:h=%d:x=%d:y=%d"
            % (name, pane.crop_w, pane.crop_h, pane.start_x, pane.start_y)
        )
        steps.append("scale=%d:%d:flags=bicubic" % (pane.scaled_w, pane.scaled_h))
        steps.append("setsar=1")
        out = "v" if single_pass else "p%d" % index
        statements.append("[%s]%s[%s]" % (source, join_filters(steps), out))
        pane_labels.append(out)

    if not single_pass:
        if use_blur:
            statements.append(
                "[%s]%s[base]" % (labels[-1], _blur_base(canvas, options.blur_sigma))
            )
        else:
            statements.append(
                "color=c=black:s=%dx%d:r=%.6g:d=%.3f,setsar=1[base]"
                % (canvas.width, canvas.height, options.encode.fps, max(duration, 0.1))
            )
        current = "base"
        for index, pane_label in enumerate(pane_labels):
            pane = plan.panes[index]
            out = "ov%d" % index
            statements.append(
                "[%s][%s]overlay=x=%d:y=%d:eval=init:shortest=1[%s]"
                % (current, pane_label, pane.dest_x, pane.dest_y, out)
            )
            current = out
        statements.append("[%s]null[v]" % current)

    tail: List[str] = []
    if label:
        font = find_font()
        if font:
            size = max(18, int(canvas.height * options.label_size))
            tail.append(
                "drawtext=fontfile=%s:text='%s':fontcolor=white:fontsize=%d"
                ":box=1:boxcolor=black@0.42:boxborderw=%d:x=%d:y=%d"
                % (
                    escape_filter_path(font),
                    escape_filter_value(label),
                    size,
                    max(8, size // 3),
                    int(canvas.width * 0.05),
                    int(canvas.height * SAFE_TOP),
                )
            )
        else:
            warnings.append(
                "no usable font found for the on-screen label, so it was skipped"
            )
    if options.fade > 0 and duration > options.fade * 2.5:
        tail.append("fade=t=in:st=0:d=%.3f" % options.fade)
        tail.append(
            "fade=t=out:st=%.3f:d=%.3f" % (max(0.0, duration - options.fade), options.fade)
        )
    tail.append("format=yuv420p")
    statements.append("[v]%s[vout]" % join_filters(tail))
    return statements, warnings


def build_audio_graph(
    info: VideoInfo,
    options: RenderOptions,
    *,
    duration: float,
    music_input: Optional[int],
) -> List[str]:
    """Filter statements producing ``[aout]``, or an empty list for silence."""
    statements: List[str] = []
    sources: List[str] = []

    use_source = info.has_audio and not options.mute
    if use_source:
        steps = ["asetpts=PTS-STARTPTS"]
        tempo = _atempo_chain(options.speed) if abs(options.speed - 1.0) > 1e-3 else ""
        if tempo:
            steps.append(tempo)
        steps.append("aresample=%d:async=1:first_pts=0" % options.encode.audio_rate)
        if abs(options.source_volume - 1.0) > 1e-3:
            steps.append("volume=%.4g" % options.source_volume)
        statements.append("[0:a]%s[asrc]" % join_filters(steps))
        sources.append("asrc")

    if music_input is not None:
        steps = [
            "aresample=%d" % options.encode.audio_rate,
            "volume=%.4g" % max(0.0, options.music_volume),
            "atrim=duration=%.3f" % max(duration, 0.1),
            "asetpts=PTS-STARTPTS",
        ]
        statements.append("[%d:a]%s[amus]" % (music_input, join_filters(steps)))
        sources.append("amus")

    if not sources:
        return []
    if len(sources) == 1:
        mixed = "[%s]anull[apre]" % sources[0]
    else:
        mixed = "%samix=inputs=%d:duration=first:normalize=0:dropout_transition=0[apre]" % (
            "".join("[%s]" % s for s in sources),
            len(sources),
        )
    statements.append(mixed)

    tail = ["aformat=sample_fmts=fltp:sample_rates=%d:channel_layouts=stereo" % options.encode.audio_rate]
    if options.fade > 0 and duration > options.fade * 2.5:
        tail.append("afade=t=in:st=0:d=%.3f" % options.fade)
        tail.append(
            "afade=t=out:st=%.3f:d=%.3f" % (max(0.0, duration - options.fade), options.fade)
        )
    statements.append("[apre]%s[aout]" % join_filters(tail))
    return statements


def encode_args(options: RenderOptions, *, has_audio_graph: bool = True) -> List[str]:
    encode = options.encode
    args = [
        "-map",
        "[vout]",
        "-c:v",
        "libx264",
        "-preset",
        encode.preset,
        "-crf",
        str(encode.crf),
        "-profile:v",
        "high",
        "-level",
        "4.1",
        "-pix_fmt",
        "yuv420p",
        "-color_primaries",
        "bt709",
        "-color_trc",
        "bt709",
        "-colorspace",
        "bt709",
        "-g",
        str(encode.gop),
        "-keyint_min",
        str(encode.gop),
        "-r",
        "%.6g" % encode.fps,
    ]
    if has_audio_graph:
        args += ["-map", "[aout]"]
    args += [
        "-c:a",
        "aac",
        "-b:a",
        encode.audio_bitrate,
        "-ar",
        str(encode.audio_rate),
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        "-max_muxing_queue_size",
        "1024",
    ]
    return args


def render_clip(
    info: VideoInfo,
    *,
    start: float,
    end: float,
    out_path: str,
    options: Optional[RenderOptions] = None,
    plan: Optional[FramePlan] = None,
    track: Optional[Tuple[Sequence[float], Sequence[float], Sequence[float]]] = None,
    label: Optional[str] = None,
    quiet: bool = True,
) -> List[str]:
    """Render ``[start, end]`` of ``info`` as one vertical clip.

    ``track`` is ``(times, cx, cy)`` sampled inside the clip, with times
    relative to the clip start; it comes from the analysis pass so rendering
    never has to decode the source twice.
    """
    options = options or RenderOptions()
    duration = max(0.05, end - start)
    out_duration = duration / max(options.speed, 1e-6)

    if plan is None:
        times, cx, cy = track or ((), (), ())
        plan = plan_frame(
            source_w=info.display_width,
            source_h=info.display_height,
            duration=out_duration,
            canvas=options.encode.canvas,
            layout=options.layout,
            pan=options.pan,
            sample_times=times,
            cx=cx,
            cy=cy,
            out_fps=options.encode.fps,
            command_rate=options.command_rate,
        )

    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    script_dir = tempfile.mkdtemp(prefix="bdr-pan-")
    try:
        statements, warnings = build_video_graph(
            plan,
            info,
            options,
            duration=out_duration,
            script_dir=script_dir,
            label=label if label is not None else options.label,
        )

        args = ffmpeg_base(quiet=quiet)
        args += ["-ss", "%.3f" % max(0.0, start), "-t", "%.3f" % duration, "-i", info.path]
        music_input: Optional[int] = None
        if options.music:
            args += ["-stream_loop", "-1", "-i", options.music]
            music_input = 1

        audio_statements = build_audio_graph(
            info, options, duration=out_duration, music_input=music_input
        )
        if not audio_statements:
            # Reels rejects clips with no audio track.
            args += [
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=%d:cl=stereo" % options.encode.audio_rate,
            ]
            silent_index = 2 if music_input is not None else 1
            audio_statements = [
                "[%d:a]anull[aout]" % silent_index,
            ]

        args += ["-filter_complex", ";".join(statements + audio_statements)]
        args += encode_args(options, has_audio_graph=True)
        args += ["-t", "%.3f" % out_duration, out_path]

        run(args, capture=quiet)
        return warnings
    finally:
        shutil.rmtree(script_dir, ignore_errors=True)


def concat_clips(paths: Sequence[str], out_path: str, *, quiet: bool = True) -> None:
    """Join already-rendered clips without re-encoding."""
    paths = [p for p in paths if os.path.isfile(p)]
    if not paths:
        raise RenderError("nothing to concatenate")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    handle = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
    try:
        for path in paths:
            handle.write("file '%s'\n" % os.path.abspath(path).replace("'", "'\\''"))
        handle.close()
        args = ffmpeg_base(quiet=quiet)
        args += [
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            handle.name,
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            out_path,
        ]
        run(args, capture=quiet)
    finally:
        os.unlink(handle.name)


def extract_thumbnail(info: VideoInfo, time: float, out_path: str, *, width: int = 480) -> bool:
    """Grab one JPEG frame; used by the review page."""
    args = ffmpeg_base()
    args += ["-ss", "%.3f" % max(0.0, time), "-i", info.path, "-frames:v", "1"]
    chain = []
    if info.is_hdr and has_filter("zscale"):
        chain.append(tonemap_chain(info)[0])
    chain.append("scale=%d:-2" % width)
    args += ["-vf", join_filters(chain), "-q:v", "4", out_path]
    try:
        run(args)
        return os.path.isfile(out_path)
    except RenderError:
        return False
