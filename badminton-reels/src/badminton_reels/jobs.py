"""A small background job queue.

Analysis and rendering take minutes, so the browser starts them and then
polls for progress. Jobs run one at a time: ffmpeg already uses every core,
so overlapping two renders only makes both slower.
"""

from __future__ import annotations

import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

QUEUED, RUNNING, DONE, ERROR = "queued", "running", "done", "error"

MAX_LOG_LINES = 200
MAX_HISTORY = 40


@dataclass
class Job:
    """One unit of background work, as the browser sees it."""

    id: str
    kind: str
    title: str
    project_id: Optional[str] = None
    status: str = QUEUED
    progress: float = 0.0
    message: str = "queued"
    log: List[str] = field(default_factory=list)
    result: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    created: float = field(default_factory=time.time)
    started: Optional[float] = None
    finished: Optional[float] = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # -- progress reporting, called from the worker thread ---------------
    def say(self, message: str) -> None:
        with self._lock:
            self.message = str(message)
            self.log.append(str(message))
            del self.log[:-MAX_LOG_LINES]

    def set_progress(self, fraction: float) -> None:
        with self._lock:
            self.progress = max(0.0, min(1.0, float(fraction)))

    def step(self, index: int, total: int, message: str = "") -> None:
        """Progress for work that comes in ``total`` equal pieces."""
        if total > 0:
            self.set_progress(index / float(total))
        if message:
            self.say(message)

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "id": self.id,
                "kind": self.kind,
                "title": self.title,
                "project_id": self.project_id,
                "status": self.status,
                "progress": round(self.progress, 4),
                "message": self.message,
                "log": list(self.log[-24:]),
                "result": dict(self.result),
                "error": self.error,
                "created": self.created,
                "started": self.started,
                "finished": self.finished,
                "elapsed": round((self.finished or time.time()) - (self.started or self.created), 2),
            }

    @property
    def active(self) -> bool:
        return self.status in (QUEUED, RUNNING)


class JobQueue:
    """Runs submitted callables on a single worker thread."""

    def __init__(self) -> None:
        self._jobs: Dict[str, Job] = {}
        self._order: List[str] = []
        self._pending: List[Job] = []
        self._lock = threading.Lock()
        self._wake = threading.Condition(self._lock)
        self._worker: Optional[threading.Thread] = None
        self._stopping = False

    def submit(
        self,
        kind: str,
        title: str,
        work: Callable[[Job], Optional[Dict[str, Any]]],
        *,
        project_id: Optional[str] = None,
    ) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], kind=kind, title=title, project_id=project_id)
        with self._lock:
            self._jobs[job.id] = job
            self._order.append(job.id)
            self._pending.append(job)
            job._work = work  # type: ignore[attr-defined]
            self._trim_history()
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(
                    target=self._run, name="bdr-jobs", daemon=True
                )
                self._worker.start()
            self._wake.notify_all()
        return job

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def recent(self, limit: int = 12) -> List[Job]:
        with self._lock:
            ids = self._order[-limit:]
            return [self._jobs[job_id] for job_id in ids if job_id in self._jobs]

    def active(self) -> List[Job]:
        with self._lock:
            return [job for job in self._jobs.values() if job.active]

    def stop(self) -> None:
        with self._lock:
            self._stopping = True
            self._wake.notify_all()

    def _trim_history(self) -> None:
        while len(self._order) > MAX_HISTORY:
            old = self._order.pop(0)
            job = self._jobs.get(old)
            if job is not None and job.active:
                # Never forget a job that is still running.
                self._order.append(old)
                return
            self._jobs.pop(old, None)

    def _run(self) -> None:
        while True:
            with self._lock:
                while not self._pending and not self._stopping:
                    self._wake.wait(timeout=30.0)
                    if not self._pending:
                        # Idle: let the thread go and start a fresh one later.
                        self._worker = None
                        return
                if self._stopping:
                    self._worker = None
                    return
                job = self._pending.pop(0)
            self._execute(job)

    @staticmethod
    def _execute(job: Job) -> None:
        job.status = RUNNING
        job.started = time.time()
        job.say("started")
        try:
            result = job._work(job) or {}  # type: ignore[attr-defined]
            job.result = result if isinstance(result, dict) else {"value": result}
            job.status = DONE
            job.set_progress(1.0)
            job.say("finished")
        except Exception as exc:  # noqa: BLE001 - surfaced to the browser
            job.status = ERROR
            job.error = str(exc) or exc.__class__.__name__
            job.say("failed: %s" % job.error)
            job.result.setdefault("traceback", traceback.format_exc(limit=6))
        finally:
            job.finished = time.time()
