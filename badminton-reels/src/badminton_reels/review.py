"""Build a self-contained review page for a project.

The page is a single HTML file with the timeline and thumbnails inlined, so it
works from ``file://`` with no server and no network. It is where the actual
editing happens: watch each detected rally, nudge its in/out points, drop the
service errors, then download the project file and render.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import tempfile
from typing import Callable, Dict, Optional

from .probe import VideoInfo
from .project import Project
from .render import extract_thumbnail

TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "templates", "review.html")


def _thumb_time(clip) -> float:
    """A frame from the middle of the rally, where the players are in position."""
    start = clip.detect_start if clip.detect_start is not None else clip.start
    end = clip.detect_end if clip.detect_end is not None else clip.end
    return max(0.0, start + (end - start) / 2.0)


def collect_thumbnails(
    project: Project,
    info: VideoInfo,
    *,
    width: int = 384,
    report: Optional[Callable[[str], None]] = None,
) -> Dict[str, str]:
    """Grab one base64 JPEG per clip. Missing frames are simply skipped."""
    say = report or (lambda _msg: None)
    if not os.path.isfile(info.path):
        say("source video not found, building the page without thumbnails")
        return {}
    out: Dict[str, str] = {}
    work = tempfile.mkdtemp(prefix="bdr-thumbs-")
    try:
        for index, clip in enumerate(project.clips, start=1):
            target = os.path.join(work, "%s.jpg" % clip.id)
            if extract_thumbnail(info, _thumb_time(clip), target, width=width):
                with open(target, "rb") as handle:
                    out[clip.id] = base64.b64encode(handle.read()).decode("ascii")
            if index % 10 == 0:
                say("thumbnails: %d/%d" % (index, len(project.clips)))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return out


def build_review(
    project: Project,
    out_path: str,
    *,
    thumbnails: bool = True,
    thumb_width: int = 384,
    report: Optional[Callable[[str], None]] = None,
) -> str:
    """Write the review page for ``project`` to ``out_path``."""
    say = report or (lambda _msg: None)
    info = project.video_info()

    data = project.to_dict()
    data["source"] = dict(data.get("source") or {})
    data["source"]["basename"] = os.path.basename(info.path) or "source"
    # The whole project goes in, not a summary of it: the page's "Download
    # project.json" button hands back a file the CLI can render directly, and
    # the pan track and activity curve drive the crop preview and sparklines.
    payload = {
        "project": data,
        "project_name": os.path.basename(project.path or "project.json"),
        "thumbs": collect_thumbnails(project, info, width=thumb_width, report=report)
        if thumbnails
        else {},
    }

    with open(TEMPLATE_PATH, "r", encoding="utf-8") as handle:
        template = handle.read()
    title = "Rally review — %s" % (os.path.basename(info.path) or "badminton-reels")
    html = template.replace("__TITLE__", _escape(title)).replace(
        "__DATA__", json.dumps(payload, separators=(",", ":"))
    )

    directory = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(directory, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as handle:
        handle.write(html)
    say("review page: %s (%.1f MB)" % (out_path, os.path.getsize(out_path) / 1e6))
    return out_path


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
