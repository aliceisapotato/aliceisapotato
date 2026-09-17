"""Where the web editor keeps its files.

    <workspace>/
      media/                  uploaded or linked source videos
      projects/<id>/
        project.json          the timeline, same format the CLI reads
        proxy.mp4             small H.264 copy the browser scrubs
        thumbs/<clip>.jpg     cached rally thumbnails
        out/                  rendered clips and reels

Nothing here is private to the server: every project is a plain project file
the CLI can open, and every rendered file is an ordinary MP4.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Sequence

from .errors import ProjectError
from .project import Project

VIDEO_SUFFIXES = (".mov", ".mp4", ".m4v", ".avi", ".mkv", ".mts", ".m2ts", ".webm")
MAX_MEDIA_ENTRIES = 500


def slug(text: str, fallback: str = "match") -> str:
    out = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return out[:40] or fallback


@dataclass
class Workspace:
    """The directory tree the editor reads and writes."""

    root: str
    extra_media: Sequence[str] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        self.root = os.path.abspath(os.path.expanduser(self.root))
        os.makedirs(self.media_dir, exist_ok=True)
        os.makedirs(self.projects_dir, exist_ok=True)

    # -- layout -----------------------------------------------------------
    @property
    def media_dir(self) -> str:
        return os.path.join(self.root, "media")

    @property
    def projects_dir(self) -> str:
        return os.path.join(self.root, "projects")

    @property
    def media_roots(self) -> List[str]:
        roots = [self.media_dir]
        for extra in self.extra_media:
            path = os.path.abspath(os.path.expanduser(extra))
            if os.path.isdir(path) and path not in roots:
                roots.append(path)
        return roots

    def project_dir(self, project_id: str) -> str:
        return os.path.join(self.projects_dir, self._safe_id(project_id))

    def project_file(self, project_id: str) -> str:
        return os.path.join(self.project_dir(project_id), "project.json")

    def output_dir(self, project_id: str) -> str:
        return os.path.join(self.project_dir(project_id), "out")

    def thumb_dir(self, project_id: str) -> str:
        return os.path.join(self.project_dir(project_id), "thumbs")

    # -- ids --------------------------------------------------------------
    @staticmethod
    def _safe_id(project_id: str) -> str:
        """Reject anything that could escape the projects directory."""
        cleaned = str(project_id or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}", cleaned) or ".." in cleaned:
            raise ProjectError("bad project id: %r" % project_id)
        return cleaned

    def new_project_id(self, source_path: str) -> str:
        """A readable id derived from the file name, kept unique."""
        stem = os.path.splitext(os.path.basename(source_path))[0]
        digest = hashlib.sha1(os.path.abspath(source_path).encode("utf-8")).hexdigest()[:6]
        base = "%s-%s" % (slug(stem), digest)
        candidate, counter = base, 2
        while os.path.exists(self.project_dir(candidate)):
            candidate = "%s-%d" % (base, counter)
            counter += 1
        return candidate

    # -- projects ---------------------------------------------------------
    def load(self, project_id: str) -> Project:
        return Project.load(self.project_file(project_id))

    def save(self, project: Project, project_id: str) -> str:
        directory = self.project_dir(project_id)
        os.makedirs(directory, exist_ok=True)
        return project.save(os.path.join(directory, "project.json"))

    def delete(self, project_id: str) -> None:
        shutil.rmtree(self.project_dir(project_id), ignore_errors=True)

    def project_ids(self) -> List[str]:
        if not os.path.isdir(self.projects_dir):
            return []
        return sorted(
            name
            for name in os.listdir(self.projects_dir)
            if os.path.isfile(os.path.join(self.projects_dir, name, "project.json"))
        )

    def summaries(self) -> List[Dict[str, Any]]:
        """Light descriptions for the project picker, newest first."""
        out: List[Dict[str, Any]] = []
        for project_id in self.project_ids():
            path = self.project_file(project_id)
            try:
                project = Project.load(path)
            except ProjectError:
                continue
            source = project.source_path
            out.append({
                "id": project_id,
                "name": os.path.basename(source) or project_id,
                "source": source,
                "source_missing": bool(source) and not os.path.isfile(source),
                "duration": project.duration,
                "clips": len(project.clips),
                "kept": len(project.kept()),
                "kept_duration": round(project.total_duration(), 2),
                "has_proxy": bool(project.preview_path and os.path.isfile(project.preview_path)),
                "outputs": len(self.outputs(project_id)),
                "updated": os.path.getmtime(path),
            })
        out.sort(key=lambda entry: entry["updated"], reverse=True)
        return out

    # -- media ------------------------------------------------------------
    def list_media(self) -> List[Dict[str, Any]]:
        """Video files the server is allowed to open."""
        found: List[Dict[str, Any]] = []
        seen = set()
        for root in self.media_roots:
            for directory, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                for name in sorted(filenames):
                    if not name.lower().endswith(VIDEO_SUFFIXES) or name.startswith("."):
                        continue
                    path = os.path.join(directory, name)
                    real = os.path.realpath(path)
                    if real in seen:
                        continue
                    seen.add(real)
                    try:
                        size = os.path.getsize(path)
                        modified = os.path.getmtime(path)
                    except OSError:
                        continue
                    found.append({
                        "path": path,
                        "name": name,
                        "directory": directory,
                        "size": size,
                        "modified": modified,
                    })
                    if len(found) >= MAX_MEDIA_ENTRIES:
                        found.sort(key=lambda entry: entry["modified"], reverse=True)
                        return found
        found.sort(key=lambda entry: entry["modified"], reverse=True)
        return found

    def resolve_media(self, path: str) -> str:
        """Validate a client-supplied source path against the allowed roots."""
        if not path:
            raise ProjectError("no source path given")
        candidate = os.path.realpath(os.path.abspath(os.path.expanduser(str(path))))
        if not os.path.isfile(candidate):
            raise ProjectError("no such file: %s" % path)
        for root in self.media_roots:
            if _is_within(candidate, root):
                return candidate
        raise ProjectError(
            "%s is outside the folders this server may read. Upload it, put it in %s, "
            "or restart with --media pointing at its folder."
            % (os.path.basename(str(path)), self.media_dir)
        )

    def import_upload(self, filename: str, stream, length: int, *, chunk: int = 1 << 20) -> str:
        """Stream an uploaded file into the media directory."""
        name = os.path.basename(str(filename or "upload.mp4")).strip() or "upload.mp4"
        name = re.sub(r"[^A-Za-z0-9._ -]", "_", name)
        if not name.lower().endswith(VIDEO_SUFFIXES):
            name += ".mp4"
        target = os.path.join(self.media_dir, name)
        stem, suffix = os.path.splitext(target)
        counter = 2
        while os.path.exists(target):
            target = "%s-%d%s" % (stem, counter, suffix)
            counter += 1
        remaining = int(length)
        partial = target + ".part"
        try:
            with open(partial, "wb") as handle:
                while remaining > 0:
                    block = stream.read(min(chunk, remaining))
                    if not block:
                        break
                    handle.write(block)
                    remaining -= len(block)
            if remaining > 0:
                raise ProjectError("the upload ended early; nothing was saved")
            os.replace(partial, target)
        finally:
            if os.path.exists(partial):
                os.unlink(partial)
        return target

    # -- outputs ----------------------------------------------------------
    def outputs(self, project_id: str) -> List[Dict[str, Any]]:
        directory = self.output_dir(project_id)
        if not os.path.isdir(directory):
            return []
        entries: List[Dict[str, Any]] = []
        for name in sorted(os.listdir(directory)):
            path = os.path.join(directory, name)
            if not os.path.isfile(path) or not name.lower().endswith(".mp4"):
                continue
            entries.append({
                "name": name,
                "size": os.path.getsize(path),
                "modified": os.path.getmtime(path),
                "kind": "reel" if "reel" in name.lower() else "clip",
            })
        entries.sort(key=lambda entry: (entry["kind"] != "reel", entry["name"]))
        return entries

    def output_file(self, project_id: str, name: str) -> str:
        safe = os.path.basename(str(name or ""))
        if not safe or safe.startswith(".") or not safe.lower().endswith(".mp4"):
            raise ProjectError("bad output name: %r" % name)
        path = os.path.join(self.output_dir(project_id), safe)
        if not os.path.isfile(path):
            raise ProjectError("no such output: %s" % safe)
        return path

    def describe(self) -> Dict[str, Any]:
        return {
            "root": self.root,
            "media": self.media_dir,
            "media_roots": self.media_roots,
            "projects": self.projects_dir,
        }


def _is_within(path: str, root: str) -> bool:
    root = os.path.realpath(os.path.abspath(root))
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:  # different drives on Windows
        return False


def default_root() -> str:
    """Where the workspace lives unless told otherwise."""
    return os.environ.get("BDR_WORKSPACE") or os.path.join(
        os.path.expanduser("~"), "badminton-reels"
    )


def human_size(value: float) -> str:
    for unit in ("B", "kB", "MB", "GB", "TB"):
        if abs(value) < 1024.0 or unit == "TB":
            return "%.0f %s" % (value, unit) if unit == "B" else "%.1f %s" % (value, unit)
        value /= 1024.0
    return "%.1f TB" % value


def now() -> float:
    return time.time()
