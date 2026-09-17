"""The web editor's HTTP server.

Deliberately built on the standard library: the only dependency this package
needs is NumPy, and a local single-user editor does not need a framework.

Security, because this opens a port on the machine:

* it binds to the loopback interface only;
* every ``/api`` request needs the token printed in the startup URL, so a web
  page you happen to have open cannot drive the editor;
* the ``Host`` header must be a loopback name, which blocks DNS rebinding;
* file paths coming from the browser are resolved and checked against the
  workspace and any ``--media`` folders before anything is opened.
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import secrets
import socket
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional, Pattern, Sequence, Tuple
from urllib.parse import parse_qs, unquote, urlparse

from . import __version__
from .detect import DetectParams, detect_rallies
from .errors import BdrError, ProjectError
from .ffutil import have_binaries
from .framing import PanParams, plan_frame
from .jobs import Job, JobQueue
from .motion import ProxyRequest, parse_roi
from .pipeline import (
    AnalyzeSettings,
    analyze,
    options_for_clip,
    render_clips,
    render_options_from_dict,
    render_options_to_dict,
    render_reel,
)
from .probe import probe
from .project import MIN_CLIP_DURATION, Project
from .render import extract_thumbnail
from .workspace import Workspace, default_root

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]", "0.0.0.0"}
CHUNK = 256 * 1024

# Fields of a clip the browser is allowed to change.
EDITABLE_CLIP_FIELDS = ("start", "end", "keep", "label", "notes")
OVERRIDE_CLIP_FIELDS = ("layout", "zoom", "pan", "speed")


class HttpError(BdrError):
    """An error with an HTTP status attached."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class Editor:
    """Application state shared by all requests."""

    def __init__(self, workspace: Workspace, token: str) -> None:
        self.workspace = workspace
        self.token = token
        self.jobs = JobQueue()
        self.started = time.time()
        self._save_lock = threading.Lock()

    # -- state ------------------------------------------------------------
    def state(self) -> Dict[str, Any]:
        return {
            "version": __version__,
            "ffmpeg": have_binaries(),
            "workspace": self.workspace.describe(),
            "projects": self.workspace.summaries(),
            "media": self.workspace.list_media(),
            "jobs": [job.to_dict() for job in self.jobs.recent()],
            "defaults": {
                "analysis": AnalyzeSettings().to_dict(),
                "render": render_options_to_dict(render_options_from_dict({})),
                "detect": DetectParams().to_dict(),
            },
            "min_clip_duration": MIN_CLIP_DURATION,
        }

    def project_payload(self, project_id: str) -> Dict[str, Any]:
        project = self.workspace.load(project_id)
        data = project.to_dict()
        data["id"] = project_id
        data["outputs"] = self.workspace.outputs(project_id)
        data["has_proxy"] = bool(
            project.preview_path and os.path.isfile(project.preview_path)
        )
        data["source_missing"] = not os.path.isfile(project.source_path)
        data["kept_duration"] = round(project.total_duration(), 3)
        return data

    # -- editing ----------------------------------------------------------
    def save(self, project: Project, project_id: str) -> None:
        with self._save_lock:
            self.workspace.save(project, project_id)

    def update_project(self, project_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        with self._save_lock:
            project = self.workspace.load(project_id)
            for entry in payload.get("clips") or []:
                self._apply_clip(project, entry)
            if isinstance(payload.get("render"), dict):
                # Round-trip through the dataclasses so bad values are rejected
                # here rather than at render time.
                merged = dict(project.render or {})
                merged.update(payload["render"])
                project.render = render_options_to_dict(render_options_from_dict(merged))
            if payload.get("renumber"):
                project.renumber()
            project.sort()
            self.workspace.save(project, project_id)
        return self.project_payload(project_id)

    def _apply_clip(self, project: Project, entry: Dict[str, Any]) -> None:
        if not isinstance(entry, dict) or "id" not in entry:
            raise HttpError(HTTPStatus.BAD_REQUEST, "each clip needs an id")
        clip = project.by_id(str(entry["id"]))
        if "start" in entry or "end" in entry:
            start = float(entry.get("start", clip.start))
            end = float(entry.get("end", clip.end))
            clip.set_range(start, end, limit=project.duration)
            project.rescore(clip)
        for field in EDITABLE_CLIP_FIELDS:
            if field in ("start", "end") or field not in entry:
                continue
            value = entry[field]
            if field == "keep":
                clip.keep = bool(value)
            else:
                setattr(clip, field, str(value)[:200])
        for field in OVERRIDE_CLIP_FIELDS:
            if field not in entry:
                continue
            value = entry[field]
            if value in (None, ""):
                setattr(clip, field, None)
            elif field in ("zoom", "speed"):
                number = float(value)
                if not 0.05 <= number <= 8.0:
                    raise HttpError(HTTPStatus.BAD_REQUEST, "%s out of range" % field)
                setattr(clip, field, number)
            else:
                setattr(clip, field, str(value))

    # -- framing preview --------------------------------------------------
    def clip_plan(self, project_id: str, clip_id: str, query: Dict[str, str]) -> Dict[str, Any]:
        """The exact crop path the renderer would use, for the overlay.

        The editor never re-implements the planner: it asks for the real one
        so the box on screen is the box in the file.
        """
        project = self.workspace.load(project_id)
        clip = project.by_id(clip_id)
        base = render_options_from_dict(project.render)
        options = options_for_clip(clip, base)
        if query.get("layout"):
            options.layout = query["layout"]
        if query.get("zoom"):
            options.pan = PanParams(**{**vars(options.pan), "zoom": float(query["zoom"])})
        if query.get("pan"):
            options.pan = PanParams(**{**vars(options.pan), "mode": query["pan"]})

        info = project.video_info()
        times, cx, cy = project.track_for(clip)
        speed = max(options.speed, 1e-6)
        plan = plan_frame(
            source_w=info.display_width,
            source_h=info.display_height,
            duration=clip.duration / speed,
            canvas=options.encode.canvas,
            layout=options.layout,
            pan=options.pan,
            sample_times=[t / speed for t in times],
            cx=cx,
            cy=cy,
            out_fps=options.encode.fps,
            command_rate=options.command_rate,
        )
        return {
            "clip": clip.id,
            "layout": plan.layout,
            "speed": options.speed,
            "canvas": {"width": plan.canvas.width, "height": plan.canvas.height},
            "source": {"width": info.display_width, "height": info.display_height},
            "blur_background": plan.blur_background,
            "panes": [
                {
                    "crop_w": pane.crop_w,
                    "crop_h": pane.crop_h,
                    "dest_x": pane.dest_x,
                    "dest_y": pane.dest_y,
                    "scaled_w": pane.scaled_w,
                    "scaled_h": pane.scaled_h,
                    "times": pane.times,
                    "xs": pane.xs,
                    "ys": pane.ys,
                    "tracking": pane.animated,
                }
                for pane in plan.panes
            ],
        }

    # -- thumbnails -------------------------------------------------------
    def thumbnail(self, project_id: str, clip_id: str) -> str:
        project = self.workspace.load(project_id)
        clip = project.by_id(clip_id)
        middle = clip.start + clip.duration / 2.0
        directory = self.workspace.thumb_dir(project_id)
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, "%s-%d.jpg" % (clip.id, int(middle * 4)))
        if os.path.isfile(path):
            return path

        # Prefer the proxy: seeking it is far cheaper than seeking 4K HEVC,
        # and it is already tone mapped.
        proxy = project.preview_path
        source, offset = project.video_info(), middle
        if proxy and os.path.isfile(proxy):
            source = probe(proxy)
        if not os.path.isfile(source.path):
            raise HttpError(HTTPStatus.NOT_FOUND, "source video not available")
        if not extract_thumbnail(source, offset, path, width=384):
            raise HttpError(HTTPStatus.INTERNAL_SERVER_ERROR, "could not read that frame")
        return path

    def preview_file(self, project_id: str) -> str:
        """What the browser plays: the proxy if there is one, else the source."""
        project = self.workspace.load(project_id)
        proxy = project.preview_path
        if proxy and os.path.isfile(proxy):
            return proxy
        if os.path.isfile(project.source_path):
            return project.source_path
        raise HttpError(HTTPStatus.NOT_FOUND, "no video available for this project")

    # -- jobs -------------------------------------------------------------
    def start_analysis(self, payload: Dict[str, Any]) -> Job:
        source = self.workspace.resolve_media(payload.get("path") or "")
        settings = _analysis_settings(payload.get("settings") or {})
        want_proxy = bool(payload.get("proxy", True))
        render_defaults = render_options_from_dict(payload.get("render") or {})
        name = os.path.basename(source)

        def work(job: Job) -> Dict[str, Any]:
            project_id = self.workspace.new_project_id(source)
            directory = self.workspace.project_dir(project_id)
            os.makedirs(directory, exist_ok=True)
            proxy = (
                ProxyRequest(path=os.path.join(directory, "proxy.mp4"))
                if want_proxy
                else None
            )
            result = analyze(
                source,
                settings,
                report=job.say,
                progress=job.set_progress,
                render_defaults=render_defaults,
                proxy=proxy,
            )
            self.save(result.project, project_id)
            job.say("%d rallies found" % len(result.project.clips))
            return {
                "project_id": project_id,
                "clips": len(result.project.clips),
                "impacts": int(result.audio.onsets.size),
            }

        return self.jobs.submit("analyze", "Analysing %s" % name, work)

    def restart_detection(self, project_id: str, payload: Dict[str, Any]) -> Job:
        """Re-run rally detection with new settings, reusing the analysis pass.

        Motion and impacts are already stored, so changing a threshold is
        instant: there is no need to decode the video again.
        """
        project = self.workspace.load(project_id)
        params = _detect_params(payload.get("detect") or {})

        def work(job: Job) -> Dict[str, Any]:
            job.say("re-detecting rallies from the stored signals")
            motion, audio = _replay_signals(project)
            segments, curve = detect_rallies(project.duration, motion, audio, params)
            from .project import curve_dict, make_clips

            fresh = self.workspace.load(project_id)
            fresh.clips = make_clips(segments)
            analysis = dict(fresh.analysis or {})
            analysis["curve"] = curve_dict(curve)
            analysis["enter_threshold"] = round(curve.enter_threshold, 4)
            analysis["exit_threshold"] = round(curve.exit_threshold, 4)
            settings = dict(analysis.get("settings") or {})
            settings["detect"] = params.to_dict()
            analysis["settings"] = settings
            fresh.analysis = analysis
            self.save(fresh, project_id)
            job.say("%d rallies" % len(segments))
            return {"project_id": project_id, "clips": len(segments)}

        return self.jobs.submit(
            "detect", "Re-detecting rallies", work, project_id=project_id
        )

    def start_render(self, project_id: str, payload: Dict[str, Any]) -> Job:
        project = self.workspace.load(project_id)
        mode = str(payload.get("mode") or "clips")
        if mode not in ("clips", "reel"):
            raise HttpError(HTTPStatus.BAD_REQUEST, "mode must be clips or reel")
        overrides = dict(project.render or {})
        overrides.update(payload.get("options") or {})
        options = render_options_from_dict(overrides)
        if options.music:
            # It reaches an ffmpeg -i, so hold it to the same rule as a source.
            options.music = self.workspace.resolve_media(options.music)
        labels = bool(payload.get("labels", mode == "reel"))
        only = [str(value) for value in (payload.get("only") or [])]
        top = payload.get("top")
        max_duration = payload.get("max_duration", 90.0)
        out_dir = self.workspace.output_dir(project_id)

        def work(job: Job) -> Dict[str, Any]:
            fresh = self.workspace.load(project_id)
            clips = [fresh.by_id(clip_id) for clip_id in only] if only else None
            if clips is not None:
                clips.sort(key=lambda clip: clip.start)
            os.makedirs(out_dir, exist_ok=True)
            if mode == "reel":
                target = os.path.join(out_dir, "reel.mp4")
                if clips is not None:
                    wanted = {clip.id for clip in clips}
                    for clip in fresh.clips:
                        clip.keep = clip.id in wanted
                path, chosen = render_reel(
                    fresh,
                    target,
                    options=options,
                    max_clips=int(top) if top else None,
                    max_duration=None
                    if max_duration in (None, 0)
                    else float(max_duration),
                    labels=labels,
                    report=job.say,
                    progress=job.set_progress,
                )
                return {
                    "files": [os.path.basename(path)],
                    "clips": [clip.id for clip in chosen],
                }
            written = render_clips(
                fresh,
                out_dir,
                options=options,
                clips=clips,
                labels=labels,
                report=job.say,
                progress=job.set_progress,
            )
            return {"files": [os.path.basename(path) for path in written]}

        title = "Rendering %s" % ("reel" if mode == "reel" else "clips")
        return self.jobs.submit("render", title, work, project_id=project_id)


def _replay_signals(project: Project):
    """Rebuild motion and impact tracks from what the project stored."""
    import numpy as np

    from .audio import AudioTrack
    from .motion import MotionTrack

    track = (project.analysis or {}).get("track") or {}
    rate = float(track.get("rate") or 0.0)
    cx = np.asarray(track.get("cx") or [], dtype=np.float64)
    cy = np.asarray(track.get("cy") or [], dtype=np.float64)
    curve = (project.analysis or {}).get("curve") or {}
    if rate <= 0 or cx.size == 0 or not curve.get("score"):
        raise HttpError(
            HTTPStatus.CONFLICT,
            "this project predates stored signals, so detection cannot be re-run "
            "without analysing the video again",
        )
    # The stored activity curve is the fused score; feeding it back as motion
    # energy reproduces the same detection maths at its own sample rate.
    score = np.asarray(curve["score"], dtype=np.float64)
    score_rate = float(curve.get("rate") or 4.0)
    times = np.arange(score.size) / score_rate
    motion_times = np.arange(cx.size) / rate
    energy = np.interp(motion_times, times, score)
    motion = MotionTrack(
        sample_rate=rate,
        energy=energy,
        cx=cx,
        cy=cy if cy.size == cx.size else np.full(cx.size, 0.5),
        heat=np.zeros((9, 16)),
        roi=tuple((project.analysis or {}).get("roi") or ()) or None,
    )
    impacts = np.asarray((project.analysis or {}).get("impact_times") or [], dtype=np.float64)
    audio = AudioTrack(
        sample_rate=16000,
        hop_rate=62.5 if impacts.size else 0.0,
        onsets=impacts,
        strength=np.ones(1) if impacts.size else np.zeros(0),
        loudness=np.zeros(1),
        duration=project.duration,
    )
    return motion, audio


def _analysis_settings(data: Dict[str, Any]) -> AnalyzeSettings:
    settings = AnalyzeSettings()
    if data.get("sample_rate"):
        settings.sample_rate = max(2.0, min(30.0, float(data["sample_rate"])))
    if data.get("analysis_width"):
        settings.analysis_width = max(64, min(640, int(data["analysis_width"])))
    if data.get("region"):
        settings.roi = parse_roi(str(data["region"]))
    if "auto_roi" in data:
        settings.auto_roi = bool(data["auto_roi"])
    if data.get("sensitivity"):
        settings.onset_sensitivity = max(0.1, min(5.0, float(data["sensitivity"])))
    if "skip_audio" in data:
        settings.skip_audio = bool(data["skip_audio"])
    if data.get("hwaccel"):
        settings.hwaccel = str(data["hwaccel"])[:32]
    settings.detect = _detect_params(data.get("detect") or {})
    return settings


def _detect_params(data: Dict[str, Any]) -> DetectParams:
    params = DetectParams()
    for key, value in (data or {}).items():
        if not hasattr(params, key) or value is None:
            continue
        current = getattr(params, key)
        try:
            setattr(params, key, int(value) if isinstance(current, int) else float(value))
        except (TypeError, ValueError):
            raise HttpError(HTTPStatus.BAD_REQUEST, "bad value for %s" % key)
    return params


# ---------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------

Route = Tuple[str, Pattern, str]


def _routes() -> List[Route]:
    def rx(pattern: str) -> Pattern:
        return re.compile("^" + pattern + "$")

    project = r"/api/projects/(?P<pid>[A-Za-z0-9][A-Za-z0-9._-]{0,80})"
    clip = project + r"/clips/(?P<cid>[A-Za-z0-9_-]{1,40})"
    return [
        ("GET", rx(r"/api/state"), "get_state"),
        ("GET", rx(r"/api/jobs"), "get_jobs"),
        ("GET", rx(r"/api/jobs/(?P<jid>[a-f0-9]{1,32})"), "get_job"),
        ("POST", rx(r"/api/upload"), "post_upload"),
        ("POST", rx(r"/api/analyze"), "post_analyze"),
        ("GET", rx(project), "get_project"),
        ("PUT", rx(project), "put_project"),
        ("DELETE", rx(project), "delete_project"),
        ("POST", rx(project + r"/detect"), "post_detect"),
        ("POST", rx(project + r"/render"), "post_render"),
        ("POST", rx(project + r"/clips"), "post_clip"),
        ("POST", rx(clip + r"/split"), "post_split"),
        ("DELETE", rx(clip), "delete_clip"),
        ("GET", rx(clip + r"/plan"), "get_plan"),
        ("GET", rx(clip + r"/thumb"), "get_thumb"),
        ("GET", rx(project + r"/preview"), "get_preview"),
        ("GET", rx(project + r"/outputs/(?P<name>[^/]+)"), "get_output"),
    ]


ROUTES = _routes()


class Handler(BaseHTTPRequestHandler):
    """Routes requests to the Editor and serves the single-page app."""

    server_version = "badminton-reels/" + __version__
    protocol_version = "HTTP/1.1"
    editor: Editor  # set on the server instance

    # -- entry points -----------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 - http.server API
        self._dispatch("GET")

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch("HEAD")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    def log_message(self, fmt: str, *args: Any) -> None:
        if os.environ.get("BDR_HTTP_LOG"):
            super().log_message(fmt, *args)

    # -- dispatch ---------------------------------------------------------
    def _dispatch(self, method: str) -> None:
        try:
            self._check_host()
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            self.query = {
                key: values[0] for key, values in parse_qs(parsed.query).items()
            }
            if path.startswith("/api/"):
                self._check_token()
                self._route(method, path)
            elif path.startswith("/media/") or path.startswith("/static/"):
                self._serve_asset(path, method)
            elif path in ("/", "/index.html"):
                self._serve_asset("/static/index.html", method)
            else:
                raise HttpError(HTTPStatus.NOT_FOUND, "not found")
        except HttpError as exc:
            self._send_json({"error": str(exc)}, status=exc.status)
        except ProjectError as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
        except BdrError as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
        except BrokenPipeError:
            pass  # the browser navigated away mid-download
        except Exception as exc:  # noqa: BLE001
            self._send_json(
                {"error": "%s: %s" % (exc.__class__.__name__, exc)},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def _route(self, method: str, path: str) -> None:
        allowed: List[str] = []
        for route_method, pattern, name in ROUTES:
            match = pattern.match(path)
            if not match:
                continue
            if route_method != method and not (method == "HEAD" and route_method == "GET"):
                allowed.append(route_method)
                continue
            getattr(self, "api_" + name)(**match.groupdict())
            return
        if allowed:
            raise HttpError(
                HTTPStatus.METHOD_NOT_ALLOWED, "use %s here" % ", ".join(sorted(set(allowed)))
            )
        raise HttpError(HTTPStatus.NOT_FOUND, "no such endpoint: %s" % path)

    def _check_host(self) -> None:
        """Only loopback names, so a rebound DNS name cannot reach the API."""
        host = (self.headers.get("Host") or "").strip()
        name = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
        name = name.strip("[]") or "localhost"
        if name.lower() not in {h.strip("[]") for h in LOOPBACK_HOSTS}:
            raise HttpError(HTTPStatus.FORBIDDEN, "this editor only answers on localhost")

    def _check_token(self) -> None:
        supplied = self.headers.get("X-BDR-Token") or self.query.get("t") or ""
        if not secrets.compare_digest(str(supplied), self.editor.token):
            raise HttpError(
                HTTPStatus.UNAUTHORIZED,
                "missing or wrong token; open the URL that `bdr serve` printed",
            )

    # -- API handlers -----------------------------------------------------
    def api_get_state(self) -> None:
        self._send_json(self.editor.state())

    def api_get_jobs(self) -> None:
        self._send_json({"jobs": [job.to_dict() for job in self.editor.jobs.recent()]})

    def api_get_job(self, jid: str) -> None:
        job = self.editor.jobs.get(jid)
        if job is None:
            raise HttpError(HTTPStatus.NOT_FOUND, "no such job")
        self._send_json(job.to_dict())

    def api_post_upload(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            raise HttpError(HTTPStatus.LENGTH_REQUIRED, "upload needs a Content-Length")
        name = self.query.get("name") or "upload.mp4"
        path = self.editor.workspace.import_upload(name, self.rfile, length)
        self._send_json({"path": path, "name": os.path.basename(path)})

    def api_post_analyze(self) -> None:
        job = self.editor.start_analysis(self._json_body())
        self._send_json(job.to_dict(), status=HTTPStatus.ACCEPTED)

    def api_get_project(self, pid: str) -> None:
        self._send_json(self.editor.project_payload(pid))

    def api_put_project(self, pid: str) -> None:
        self._send_json(self.editor.update_project(pid, self._json_body()))

    def api_delete_project(self, pid: str) -> None:
        self.editor.workspace.delete(pid)
        self._send_json({"deleted": pid})

    def api_post_detect(self, pid: str) -> None:
        job = self.editor.restart_detection(pid, self._json_body())
        self._send_json(job.to_dict(), status=HTTPStatus.ACCEPTED)

    def api_post_render(self, pid: str) -> None:
        job = self.editor.start_render(pid, self._json_body())
        self._send_json(job.to_dict(), status=HTTPStatus.ACCEPTED)

    def api_post_clip(self, pid: str) -> None:
        body = self._json_body()
        with self.editor._save_lock:
            project = self.editor.workspace.load(pid)
            clip = project.add_clip(
                float(body.get("start", 0.0)),
                float(body.get("end", 0.0)),
                label=body.get("label"),
            )
            self.editor.workspace.save(project, pid)
        self._send_json({"clip": clip.to_dict(), "project": self.editor.project_payload(pid)})

    def api_post_split(self, pid: str, cid: str) -> None:
        at = float(self._json_body().get("at", 0.0))
        with self.editor._save_lock:
            project = self.editor.workspace.load(pid)
            head, tail = project.split_clip(cid, at)
            self.editor.workspace.save(project, pid)
        self._send_json({
            "head": head.to_dict(),
            "tail": tail.to_dict(),
            "project": self.editor.project_payload(pid),
        })

    def api_delete_clip(self, pid: str, cid: str) -> None:
        with self.editor._save_lock:
            project = self.editor.workspace.load(pid)
            project.remove_clip(cid)
            self.editor.workspace.save(project, pid)
        self._send_json({"deleted": cid, "project": self.editor.project_payload(pid)})

    def api_get_plan(self, pid: str, cid: str) -> None:
        self._send_json(self.editor.clip_plan(pid, cid, self.query))

    def api_get_thumb(self, pid: str, cid: str) -> None:
        self._send_file(self.editor.thumbnail(pid, cid), "image/jpeg", cache=3600)

    def api_get_preview(self, pid: str) -> None:
        path = self.editor.preview_file(pid)
        self._send_file(path, _media_type(path), ranges=True)

    def api_get_output(self, pid: str, name: str) -> None:
        path = self.editor.workspace.output_file(pid, name)
        disposition = "attachment" if self.query.get("download") else None
        self._send_file(path, _media_type(path), ranges=True, disposition=disposition, name=name)

    # -- static -----------------------------------------------------------
    def _serve_asset(self, path: str, method: str) -> None:
        name = os.path.basename(path)
        if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
            raise HttpError(HTTPStatus.NOT_FOUND, "not found")
        candidate = os.path.join(WEB_DIR, name)
        if not os.path.isfile(candidate):
            raise HttpError(HTTPStatus.NOT_FOUND, "not found")
        self._send_file(candidate, _media_type(candidate), head_only=method == "HEAD")

    # -- responses --------------------------------------------------------
    def _json_body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > 64 * 1024 * 1024:
            raise HttpError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "body too large")
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HttpError(HTTPStatus.BAD_REQUEST, "invalid JSON: %s" % exc)
        if not isinstance(body, dict):
            raise HttpError(HTTPStatus.BAD_REQUEST, "expected a JSON object")
        return body

    def _send_json(self, payload: Dict[str, Any], status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_file(
        self,
        path: str,
        content_type: str,
        *,
        ranges: bool = False,
        cache: int = 0,
        head_only: bool = False,
        disposition: Optional[str] = None,
        name: Optional[str] = None,
    ) -> None:
        size = os.path.getsize(path)
        start, end = 0, size - 1
        status = HTTPStatus.OK
        requested = self.headers.get("Range") if ranges else None
        if requested:
            parsed = _parse_range(requested, size)
            if parsed is None:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", "bytes */%d" % size)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            start, end = parsed
            status = HTTPStatus.PARTIAL_CONTENT

        length = max(0, end - start + 1)
        self.send_response(int(status))
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        if ranges:
            self.send_header("Accept-Ranges", "bytes")
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        self.send_header(
            "Cache-Control", "private, max-age=%d" % cache if cache else "no-store"
        )
        if disposition:
            self.send_header(
                "Content-Disposition",
                '%s; filename="%s"' % (disposition, os.path.basename(name or path)),
            )
        self.end_headers()
        if head_only or self.command == "HEAD":
            return
        with open(path, "rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining > 0:
                block = handle.read(min(CHUNK, remaining))
                if not block:
                    break
                self.wfile.write(block)
                remaining -= len(block)


def _parse_range(header: str, size: int) -> Optional[Tuple[int, int]]:
    """Parse a single byte range, the only form browsers send for media."""
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", (header or "").strip())
    if not match or size <= 0:
        return None
    first, last = match.group(1), match.group(2)
    if first == "" and last == "":
        return None
    if first == "":                      # bytes=-500, the final 500 bytes
        length = min(int(last), size)
        if length <= 0:
            return None
        return size - length, size - 1
    start = int(first)
    if start >= size:
        return None
    end = min(int(last), size - 1) if last else size - 1
    if end < start:
        return None
    return start, end


def _media_type(path: str) -> str:
    guessed, _ = mimetypes.guess_type(path)
    if guessed:
        return guessed
    suffix = os.path.splitext(path)[1].lower()
    return {
        ".mov": "video/quicktime",
        ".m4v": "video/x-m4v",
        ".mkv": "video/x-matroska",
        ".mts": "video/mp2t",
        ".m2ts": "video/mp2t",
    }.get(suffix, "application/octet-stream")


class EditorServer(ThreadingHTTPServer):
    """Loopback-only threading server holding the shared Editor."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: Tuple[str, int], editor: Editor) -> None:
        handler = type("BoundHandler", (Handler,), {"editor": editor})
        super().__init__(address, handler)
        self.editor = editor

    def server_bind(self) -> None:
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        super().server_bind()


def create_server(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    workspace_root: Optional[str] = None,
    media: Sequence[str] = (),
    token: Optional[str] = None,
) -> Tuple[EditorServer, str]:
    """Build a server and return it with its URL (token included)."""
    workspace = Workspace(workspace_root or default_root(), extra_media=tuple(media))
    token = token or os.environ.get("BDR_TOKEN") or secrets.token_urlsafe(16)
    editor = Editor(workspace, token)
    server = EditorServer((host, port), editor)
    url = "http://%s:%d/?t=%s" % (host, server.server_address[1], token)
    return server, url


def serve(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    workspace_root: Optional[str] = None,
    media: Sequence[str] = (),
    open_browser: bool = True,
    report: Optional[Callable[[str], None]] = None,
) -> None:
    """Run the editor until interrupted."""
    say = report or (lambda message: print(message))
    server, url = create_server(
        host=host, port=port, workspace_root=workspace_root, media=media
    )
    say("badminton-reels editor: %s" % url)
    say("workspace: %s" % server.editor.workspace.root)
    if not have_binaries():
        say("warning: ffmpeg was not found, so nothing can be analysed or rendered")
    if open_browser:
        import webbrowser

        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        say("")
        say("stopping")
    finally:
        server.editor.jobs.stop()
        server.shutdown()
        server.server_close()
