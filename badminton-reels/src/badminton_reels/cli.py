"""Command line interface.

    bdr analyze match.mov            # find the rallies, write project.json
    bdr review project.json          # open the review page, drop the duds
    bdr render project.json -o clips # one vertical MP4 per rally
    bdr reel   project.json -o reel.mp4
    bdr auto   match.mov -o out      # all of the above in one go
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional, Sequence

from . import __version__
from .detect import DetectParams
from .errors import BdrError
from .framing import LAYOUTS, PAN_MODES, Canvas
from .motion import parse_roi
from .pipeline import (
    AnalyzeSettings,
    analyze,
    clip_filename,
    render_clips,
    render_options_from_dict,
    render_options_to_dict,
    render_reel,
)
from .probe import format_duration, parse_timecode, probe
from .project import Project
from .render import RenderOptions
from .review import build_review

PROGRAM = "bdr"


# ---------------------------------------------------------------------------
# small output helpers
# ---------------------------------------------------------------------------

class Console:
    def __init__(self, verbose: bool = True) -> None:
        self.verbose = verbose
        self._progress_active = False

    def say(self, message: str) -> None:
        if not self.verbose:
            return
        self._clear_progress()
        print(message, file=sys.stderr)

    def out(self, message: str = "") -> None:
        print(message)

    def progress(self, fraction: float) -> None:
        if not self.verbose or not sys.stderr.isatty():
            return
        width = 28
        filled = int(round(min(max(fraction, 0.0), 1.0) * width))
        sys.stderr.write("\r  [%s%s] %3d%%" % ("#" * filled, "." * (width - filled), round(fraction * 100)))
        sys.stderr.flush()
        self._progress_active = True

    def _clear_progress(self) -> None:
        if self._progress_active:
            sys.stderr.write("\r" + " " * 40 + "\r")
            sys.stderr.flush()
            self._progress_active = False


def parse_ids(text: Optional[str]) -> List[str]:
    """Parse ``r01,r03`` or ``1,3`` into canonical clip ids."""
    if not text:
        return []
    ids: List[str] = []
    for chunk in str(text).replace(" ", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        ids.append("r%02d" % int(chunk) if chunk.isdigit() else chunk)
    return ids


def parse_size(text: str) -> Canvas:
    parts = str(text).lower().replace("*", "x").split("x")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("size must look like 1080x1920")
    try:
        return Canvas(width=int(parts[0]), height=int(parts[1]))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("size must look like 1080x1920") from exc


# ---------------------------------------------------------------------------
# argument groups
# ---------------------------------------------------------------------------

def add_detect_args(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("rally detection")
    group.add_argument("--min-rally", type=float, default=None, metavar="SEC",
                       help="discard anything shorter than this (default 2.0)")
    group.add_argument("--max-rally", type=float, default=None, metavar="SEC",
                       help="split anything longer at its quietest point (default 45)")
    group.add_argument("--min-shots", type=int, default=None, metavar="N",
                       help="racket impacts a rally must contain (default 2)")
    group.add_argument("--hang", type=float, default=None, metavar="SEC",
                       help="quiet time before a rally is considered over (default 1.2)")
    group.add_argument("--merge-gap", type=float, default=None, metavar="SEC",
                       help="join detections closer together than this (default 1.5)")
    group.add_argument("--pre", type=float, default=None, metavar="SEC",
                       help="handle before each rally (default 0.8)")
    group.add_argument("--post", type=float, default=None, metavar="SEC",
                       help="handle after each rally (default 1.2)")
    group.add_argument("--enter", type=float, default=None, metavar="0-1",
                       help="activity level that starts a rally (default 0.45)")
    group.add_argument("--exit", type=float, default=None, dest="exit_level", metavar="0-1",
                       help="activity level that ends a rally (default 0.25)")


def add_analysis_args(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("analysis")
    group.add_argument("--sample-rate", type=float, default=12.0, metavar="FPS",
                       help="motion samples per second (default 12)")
    group.add_argument("--analysis-width", type=int, default=192, metavar="PX",
                       help="width frames are scaled to for analysis (default 192)")
    group.add_argument("--region", type=str, default=None, metavar="X,Y,W,H",
                       help="only look at this part of the frame, as fractions or percentages")
    group.add_argument("--no-auto-region", action="store_true",
                       help="do not guess the court area from accumulated motion")
    group.add_argument("--no-audio", action="store_true",
                       help="ignore the audio track (motion-only detection)")
    group.add_argument("--sensitivity", type=float, default=1.0, metavar="X",
                       help="impact detector sensitivity; >1 finds more (default 1)")
    group.add_argument("--hwaccel", type=str, default=None, metavar="NAME",
                       help="hardware decoder for the analysis pass, e.g. videotoolbox "
                            "on a Mac; speeds up 4K HEVC considerably")


def add_framing_args(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("vertical framing")
    group.add_argument("--layout", choices=LAYOUTS, default=None,
                       help="follow: tracked 9:16 crop; fit: whole court on a blurred bed; "
                            "stack: court on top, close-up below (default follow)")
    group.add_argument("--zoom", type=float, default=None, metavar="X",
                       help=">1 tightens the crop, <1 widens it past 9:16 (default 1)")
    group.add_argument("--pan", choices=PAN_MODES, default=None,
                       help="smooth: track the action; none: hold a fixed crop")
    group.add_argument("--pan-tau", type=float, default=None, metavar="SEC",
                       help="pan smoothing time constant; larger is calmer (default 0.7)")
    group.add_argument("--pan-speed", type=float, default=None, metavar="X",
                       help="max pan speed in crop widths per second (default 0.6)")
    group.add_argument("--center-bias", type=float, default=None, metavar="0-1",
                       help="pull the crop toward the middle of the frame (default 0.15)")
    group.add_argument("--no-track-y", action="store_true",
                       help="never move the crop vertically")


def add_encode_args(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("output")
    group.add_argument("--size", type=parse_size, default=None, metavar="WxH",
                       help="canvas size (default 1080x1920)")
    group.add_argument("--fps", type=float, default=None, help="output frame rate (default 30)")
    group.add_argument("--crf", type=int, default=None, help="x264 quality, lower is better (default 20)")
    group.add_argument("--preset", type=str, default=None, help="x264 preset (default medium)")
    group.add_argument("--speed", type=float, default=None, metavar="X",
                       help="playback speed; 0.5 is half-speed slow motion (default 1)")
    group.add_argument("--fade", type=float, default=None, metavar="SEC",
                       help="fade in/out on each clip (default 0.12)")
    group.add_argument("--music", type=str, default=None, metavar="FILE",
                       help="mix a music bed under the original audio")
    group.add_argument("--music-volume", type=float, default=None, metavar="X",
                       help="music level (default 0.35)")
    group.add_argument("--mute", action="store_true", help="drop the original audio")
    group.add_argument("--labels", action="store_true",
                       help="burn a 'Rally N · shots' badge into the clip")
    group.add_argument("--no-labels", action="store_true", help="never burn labels in")
    group.add_argument("--tonemap", dest="tonemap", action="store_true", default=None,
                       help="force HDR tone mapping on")
    group.add_argument("--no-tonemap", dest="tonemap", action="store_false",
                       help="force HDR tone mapping off")
    group.add_argument("--blur", type=float, default=None, metavar="X",
                       help="background blur for letterboxed layouts; 0 gives plain "
                            "black bars (default 26)")


def detect_params_from_args(args: argparse.Namespace) -> DetectParams:
    params = DetectParams()
    mapping = {
        "min_rally": "min_duration",
        "max_rally": "max_duration",
        "min_shots": "min_shots",
        "hang": "hang",
        "merge_gap": "merge_gap",
        "pre": "pre_roll",
        "post": "post_roll",
        "enter": "enter_level",
        "exit_level": "exit_level",
    }
    for arg_name, field_name in mapping.items():
        value = getattr(args, arg_name, None)
        if value is not None:
            setattr(params, field_name, value)
    return params


def render_options_from_args(
    args: argparse.Namespace, base: Optional[RenderOptions] = None
) -> RenderOptions:
    """Start from the project's stored defaults, then apply any flags given."""
    data = render_options_to_dict(base or RenderOptions())
    simple = {
        "layout": "layout",
        "zoom": "zoom",
        "pan": "pan",
        "pan_tau": "pan_tau",
        "pan_speed": "pan_max_speed",
        "center_bias": "center_bias",
        "fps": "fps",
        "crf": "crf",
        "preset": "preset",
        "speed": "speed",
        "fade": "fade",
        "music": "music",
        "music_volume": "music_volume",
        "blur": "blur_sigma",
    }
    for arg_name, key in simple.items():
        value = getattr(args, arg_name, None)
        if value is not None:
            data[key] = value
    if getattr(args, "size", None) is not None:
        data["width"], data["height"] = args.size.width, args.size.height
    if getattr(args, "mute", False):
        data["mute"] = True
    if getattr(args, "no_track_y", False):
        data["track_y"] = False
    if getattr(args, "tonemap", None) is not None:
        data["tonemap"] = args.tonemap
    return render_options_from_dict(data)


def analysis_settings_from_args(args: argparse.Namespace) -> AnalyzeSettings:
    return AnalyzeSettings(
        sample_rate=args.sample_rate,
        analysis_width=args.analysis_width,
        roi=parse_roi(args.region) if args.region else None,
        auto_roi=not args.no_auto_region,
        onset_sensitivity=args.sensitivity,
        skip_audio=args.no_audio,
        hwaccel=args.hwaccel,
        detect=detect_params_from_args(args),
    )


def wants_labels(args: argparse.Namespace, default: bool) -> bool:
    if getattr(args, "no_labels", False):
        return False
    if getattr(args, "labels", False):
        return True
    return default


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def cmd_info(args: argparse.Namespace, console: Console) -> int:
    info = probe(args.video)
    console.out(info.describe())
    console.out("")
    console.out("  display size   %dx%d (%s)" % (
        info.display_width, info.display_height,
        "portrait" if info.is_portrait else "landscape",
    ))
    console.out("  duration       %s" % format_duration(info.duration))
    console.out("  frame rate     %.4g fps" % info.fps)
    console.out("  codec          %s / %s" % (info.codec, info.pix_fmt))
    console.out("  colour         trc=%s primaries=%s matrix=%s%s" % (
        info.color_transfer or "-", info.color_primaries or "-", info.color_space or "-",
        "  (HDR: will be tone mapped)" if info.is_hdr else "",
    ))
    if info.has_audio:
        console.out("  audio          %s Hz, %s channel(s)" % (
            info.audio_sample_rate or "?", info.audio_channels or "?"))
    else:
        console.out("  audio          none — rally detection will use motion only")
    crop_w = int(min(info.display_width, info.display_height * 9 / 16))
    console.out("")
    console.out("  a 9:16 crop keeps %d of %d pixels across (%.0f%% of the width)" % (
        crop_w, info.display_width, 100.0 * crop_w / max(info.display_width, 1)))
    return 0


def cmd_analyze(args: argparse.Namespace, console: Console) -> int:
    settings = analysis_settings_from_args(args)
    defaults = render_options_from_args(args)
    result = analyze(
        args.video,
        settings,
        report=console.say,
        progress=console.progress,
        render_defaults=defaults,
    )
    project = result.project
    out_path = args.output or _default_project_path(args.video)
    project.save(out_path)
    console.say("project: %s" % out_path)
    print_timeline(project, console)
    if not project.clips:
        console.say(
            "no rallies found. Try --sensitivity 1.5, --min-shots 1, or --enter 0.35; "
            "`bdr info` will tell you whether the file even has audio."
        )
    if args.review:
        page = os.path.splitext(out_path)[0] + "-review.html"
        build_review(project, page, thumbnails=not args.no_thumbs, report=console.say)
        console.out(page)
    return 0


def cmd_list(args: argparse.Namespace, console: Console) -> int:
    project = Project.load(args.project)
    print_timeline(project, console, show_all=True, force=True)
    return 0


def cmd_review(args: argparse.Namespace, console: Console) -> int:
    project = Project.load(args.project)
    out_path = args.output or os.path.splitext(args.project)[0] + "-review.html"
    build_review(project, out_path, thumbnails=not args.no_thumbs, report=console.say)
    console.out(out_path)
    console.say("open it in a browser, then load the video file when it asks")
    return 0


def cmd_edit(args: argparse.Namespace, console: Console) -> int:
    project = Project.load(args.project)
    changed = 0

    if args.only:
        wanted = set(parse_ids(args.only))
        for clip in project.clips:
            clip.keep = clip.id in wanted
        changed += 1
    for clip_id in parse_ids(args.keep):
        project.by_id(clip_id).keep = True
        changed += 1
    for clip_id in parse_ids(args.drop):
        project.by_id(clip_id).keep = False
        changed += 1
    if args.top:
        ranked = sorted(project.clips, key=lambda c: -c.score)[: args.top]
        best = {clip.id for clip in ranked}
        for clip in project.clips:
            clip.keep = clip.id in best
        changed += 1

    for assignment in args.trim or []:
        clip_id, _, deltas = assignment.partition("=")
        parts = [p.strip() for p in deltas.split(",")]
        if len(parts) != 2:
            raise BdrError("--trim wants ID=HEAD,TAIL (for example r03=+0.5,-0.2)")
        project.trim(clip_id.strip(), head=float(parts[0]), tail=float(parts[1]))
        changed += 1
    for assignment in args.set or []:
        clip_id, _, rest = assignment.partition("=")
        key, _, value = rest.partition(":")
        if not key or not value:
            raise BdrError("--set wants ID=FIELD:VALUE (for example r03=layout:stack)")
        _set_clip_field(project, clip_id.strip(), key.strip(), value.strip())
        changed += 1
    for assignment in args.label or []:
        clip_id, _, text = assignment.partition("=")
        project.by_id(clip_id.strip()).label = text.strip()
        changed += 1
    if args.renumber:
        project.renumber()
        changed += 1

    if not changed:
        console.say("nothing to change; see `bdr edit --help`")
    else:
        project.save()
        console.say("saved %s" % project.path)
    print_timeline(project, console, show_all=True)
    return 0


def _set_clip_field(project: Project, clip_id: str, key: str, value: str) -> None:
    clip = project.by_id(clip_id)
    if key == "layout":
        if value not in LAYOUTS:
            raise BdrError("layout must be one of %s" % ", ".join(LAYOUTS))
        clip.layout = value
    elif key == "pan":
        if value not in PAN_MODES:
            raise BdrError("pan must be one of %s" % ", ".join(PAN_MODES))
        clip.pan = value
    elif key in ("zoom", "speed"):
        setattr(clip, key, float(value))
    elif key == "notes":
        clip.notes = value
    elif key in ("start", "end"):
        setattr(clip, key, parse_timecode(value))
    else:
        raise BdrError("unknown field %r (layout, pan, zoom, speed, start, end, notes)" % key)


def _selection(project: Project, only: Optional[str]) -> Optional[List]:
    ids = parse_ids(only)
    if not ids:
        return None
    chosen = [project.by_id(clip_id) for clip_id in ids]
    chosen.sort(key=lambda clip: clip.start)
    return chosen


def cmd_render(args: argparse.Namespace, console: Console) -> int:
    project = Project.load(args.project)
    base = render_options_from_args(args, render_options_from_dict(project.render))
    clips = _selection(project, args.only)
    out_dir = args.output or os.path.join(os.path.dirname(os.path.abspath(args.project)), "clips")

    if args.dry_run:
        selected = clips if clips is not None else project.kept()
        for index, clip in enumerate(selected, start=1):
            console.out("%s  %s  %5.1fs  ->  %s" % (
                clip.id, format_duration(clip.start), clip.duration,
                os.path.join(out_dir, clip_filename(clip, index)),
            ))
        return 0

    written = render_clips(
        project,
        out_dir,
        options=base,
        clips=clips,
        labels=wants_labels(args, default=False),
        report=console.say,
        quiet=not args.ffmpeg_output,
    )
    for path in written:
        console.out(path)
    console.say("%d clip(s) written to %s" % (len(written), out_dir))
    return 0


def cmd_reel(args: argparse.Namespace, console: Console) -> int:
    project = Project.load(args.project)
    base = render_options_from_args(args, render_options_from_dict(project.render))
    if args.only:
        wanted = {clip.id for clip in _selection(project, args.only) or []}
        for clip in project.clips:
            clip.keep = clip.id in wanted
    out_path = args.output or os.path.join(
        os.path.dirname(os.path.abspath(args.project)), "reel.mp4"
    )
    path, chosen = render_reel(
        project,
        out_path,
        options=base,
        max_clips=args.top,
        max_duration=None if args.max_duration <= 0 else args.max_duration,
        labels=wants_labels(args, default=True),
        report=console.say,
        quiet=not args.ffmpeg_output,
    )
    console.out(path)
    console.say("%d rallies: %s" % (len(chosen), ", ".join(clip.id for clip in chosen)))
    return 0


def cmd_auto(args: argparse.Namespace, console: Console) -> int:
    settings = analysis_settings_from_args(args)
    defaults = render_options_from_args(args)
    result = analyze(
        args.video,
        settings,
        report=console.say,
        progress=console.progress,
        render_defaults=defaults,
    )
    project = result.project
    out_dir = args.output or os.path.splitext(os.path.basename(args.video))[0] + "-reels"
    os.makedirs(out_dir, exist_ok=True)
    project_path = project.save(os.path.join(out_dir, "project.json"))
    print_timeline(project, console)
    if not project.clips:
        console.say("no rallies detected, so nothing was rendered")
        return 1

    if not args.no_review:
        build_review(
            project,
            os.path.join(out_dir, "review.html"),
            thumbnails=not args.no_thumbs,
            report=console.say,
        )

    base = render_options_from_args(args, render_options_from_dict(project.render))
    outputs: List[str] = []
    if not args.no_clips:
        outputs += render_clips(
            project,
            os.path.join(out_dir, "clips"),
            options=base,
            labels=wants_labels(args, default=False),
            report=console.say,
            quiet=not args.ffmpeg_output,
        )
    if not args.no_reel:
        reel_path, chosen = render_reel(
            project,
            os.path.join(out_dir, "reel.mp4"),
            options=base,
            max_clips=args.top,
            max_duration=None if args.max_duration <= 0 else args.max_duration,
            labels=wants_labels(args, default=True),
            report=console.say,
            quiet=not args.ffmpeg_output,
        )
        outputs.append(reel_path)

    console.out(out_dir)
    console.say("")
    console.say("wrote %d file(s). Project: %s" % (len(outputs), project_path))
    console.say("review and re-cut with:  %s review %s" % (PROGRAM, project_path))
    return 0


def print_timeline(
    project: Project, console: Console, *, show_all: bool = False, force: bool = False
) -> None:
    if not force and not console.verbose:
        return
    if not project.clips:
        console.out("no rallies detected")
        return
    console.out("")
    console.out("  id    start      end      len   hits  score  keep")
    for clip in project.clips:
        if not show_all and not clip.keep:
            continue
        console.out("  %-5s %-9s %-9s %5.1fs %5d  %5.2f  %s" % (
            clip.id,
            format_duration(clip.start),
            format_duration(clip.end),
            clip.duration,
            clip.shots,
            clip.score,
            "yes" if clip.keep else "no",
        ))
    console.out("")
    console.out("  %s" % project.summary())


def _default_project_path(video: str) -> str:
    stem = os.path.splitext(os.path.basename(video))[0]
    return "%s-rallies.json" % stem


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------

def _common_parser() -> argparse.ArgumentParser:
    """Flags accepted either before or after the subcommand.

    The subcommand copies suppress their defaults so that leaving a flag off
    keeps whatever the top-level parser saw.
    """
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-q", "--quiet", action="store_true", default=argparse.SUPPRESS,
                        help="only print result paths")
    common.add_argument("--ffmpeg-output", action="store_true", default=argparse.SUPPRESS,
                        help="show ffmpeg's own progress and warnings")
    return common


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description="Split badminton footage into rally highlights and render them "
                    "as vertical clips for Instagram Reels.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "typical session:\n"
            "  bdr auto match.mov -o out            one-shot: detect, review page, clips, reel\n"
            "  bdr analyze match.mov --review       detect and build a review page\n"
            "  bdr edit out/project.json --drop r04 --renumber\n"
            "  bdr reel out/project.json --top 8 -o reel.mp4\n"
        ),
    )
    parser.add_argument("--version", action="version", version="%(prog)s " + __version__)
    parser.add_argument("-q", "--quiet", action="store_true", help="only print result paths")
    parser.add_argument("--ffmpeg-output", action="store_true",
                        help="show ffmpeg's own progress and warnings")
    common = _common_parser()
    subparsers = parser.add_subparsers(dest="command", metavar="command")

    info_parser = subparsers.add_parser("info", help="show what ffprobe knows about a file", parents=[common])
    info_parser.add_argument("video")
    info_parser.set_defaults(func=cmd_info)

    analyze_parser = subparsers.add_parser("analyze", help="detect rallies and write a project file", parents=[common])
    analyze_parser.add_argument("video")
    analyze_parser.add_argument("-o", "--output", metavar="FILE", help="project file to write")
    analyze_parser.add_argument("--review", action="store_true", help="also build the review page")
    analyze_parser.add_argument("--no-thumbs", action="store_true", help="skip review thumbnails")
    add_analysis_args(analyze_parser)
    add_detect_args(analyze_parser)
    add_framing_args(analyze_parser)
    add_encode_args(analyze_parser)
    analyze_parser.set_defaults(func=cmd_analyze)

    list_parser = subparsers.add_parser("list", help="print a project's timeline", parents=[common])
    list_parser.add_argument("project")
    list_parser.set_defaults(func=cmd_list)

    review_parser = subparsers.add_parser("review", help="build the offline review page", parents=[common])
    review_parser.add_argument("project")
    review_parser.add_argument("-o", "--output", metavar="FILE")
    review_parser.add_argument("--no-thumbs", action="store_true")
    review_parser.set_defaults(func=cmd_review)

    edit_parser = subparsers.add_parser("edit", help="change a project from the command line", parents=[common])
    edit_parser.add_argument("project")
    edit_parser.add_argument("--keep", metavar="IDS", help="mark these rallies as kept")
    edit_parser.add_argument("--drop", metavar="IDS", help="mark these rallies as dropped")
    edit_parser.add_argument("--only", metavar="IDS", help="keep exactly these rallies")
    edit_parser.add_argument("--top", type=int, metavar="N", help="keep the N best-scoring rallies")
    edit_parser.add_argument("--trim", action="append", metavar="ID=HEAD,TAIL",
                             help="lengthen (+) or shorten (-) a rally, in seconds")
    edit_parser.add_argument("--set", action="append", metavar="ID=FIELD:VALUE",
                             help="override layout, pan, zoom, speed, start, end or notes")
    edit_parser.add_argument("--label", action="append", metavar="ID=TEXT", help="rename a rally")
    edit_parser.add_argument("--renumber", action="store_true", help="relabel kept rallies 1..n")
    edit_parser.set_defaults(func=cmd_edit)

    render_parser = subparsers.add_parser("render", help="render one vertical clip per rally", parents=[common])
    render_parser.add_argument("project")
    render_parser.add_argument("-o", "--output", metavar="DIR", help="output directory")
    render_parser.add_argument("--only", metavar="IDS", help="render just these rallies")
    render_parser.add_argument("--dry-run", action="store_true", help="list what would be rendered")
    add_framing_args(render_parser)
    add_encode_args(render_parser)
    render_parser.set_defaults(func=cmd_render)

    reel_parser = subparsers.add_parser("reel", help="render the best rallies as one reel", parents=[common])
    reel_parser.add_argument("project")
    reel_parser.add_argument("-o", "--output", metavar="FILE")
    reel_parser.add_argument("--only", metavar="IDS", help="use exactly these rallies")
    reel_parser.add_argument("--top", type=int, default=None, metavar="N",
                             help="at most N rallies, best first (default: as many as fit)")
    reel_parser.add_argument("--max-duration", type=float, default=90.0, metavar="SEC",
                             help="reel length budget; 0 removes the limit (default 90)")
    add_framing_args(reel_parser)
    add_encode_args(reel_parser)
    reel_parser.set_defaults(func=cmd_reel)

    auto_parser = subparsers.add_parser("auto", help="analyze, review page, clips and reel in one go", parents=[common])
    auto_parser.add_argument("video")
    auto_parser.add_argument("-o", "--output", metavar="DIR", help="output directory")
    auto_parser.add_argument("--top", type=int, default=None, metavar="N",
                             help="rallies in the reel (default: as many as fit)")
    auto_parser.add_argument("--max-duration", type=float, default=90.0, metavar="SEC")
    auto_parser.add_argument("--no-clips", action="store_true", help="skip the per-rally clips")
    auto_parser.add_argument("--no-reel", action="store_true", help="skip the compilation")
    auto_parser.add_argument("--no-review", action="store_true", help="skip the review page")
    auto_parser.add_argument("--no-thumbs", action="store_true", help="skip review thumbnails")
    add_analysis_args(auto_parser)
    add_detect_args(auto_parser)
    add_framing_args(auto_parser)
    add_encode_args(auto_parser)
    auto_parser.set_defaults(func=cmd_auto)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    console = Console(verbose=not args.quiet)
    try:
        return args.func(args, console)
    except BdrError as exc:
        print("%s: error: %s" % (PROGRAM, exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
