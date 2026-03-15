"""Background job runner with in-memory job store.

Pipelines run in daemon threads.  Each job has a UUID, status, progress
float, and optional result/error.  The frontend polls ``GET /api/jobs/{id}``
to track progress.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger("stemforge.jobs")


@dataclass
class JobState:
    job_id: str
    job_type: str
    user: str = "local"
    status: str = "pending"       # pending | running | done | error
    progress: float = 0.0        # 0.0–1.0
    stage: str = ""              # human-readable stage label
    result: Any = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)


class JobManager:
    """Thread-safe in-memory job store + background runner."""

    def __init__(self) -> None:
        self._jobs: dict[str, JobState] = {}
        self._lock = threading.Lock()

    def create_job(self, job_type: str, user: str = "local") -> str:
        job_id = uuid.uuid4().hex[:12]
        job = JobState(job_id=job_id, job_type=job_type, user=user)
        with self._lock:
            self._jobs[job_id] = job
        return job_id

    def run_job(
        self,
        job_id: str,
        target_fn: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """Spawn a daemon thread that runs *target_fn* and updates job state."""

        def _worker() -> None:
            with self._lock:
                job = self._jobs[job_id]
                job.status = "running"
            try:
                result = target_fn(*args, **kwargs)
                with self._lock:
                    job.status = "done"
                    job.progress = 1.0
                    job.result = result
            except Exception as exc:
                log.exception("Job %s failed", job_id)
                with self._lock:
                    job.status = "error"
                    job.error = str(exc)

        t = threading.Thread(target=_worker, daemon=True, name=f"job-{job_id}")
        t.start()

    def update_progress(self, job_id: str, progress: float, stage: str = "") -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.progress = max(0.0, min(1.0, progress))
                if stage:
                    job.stage = stage

    def get_job(self, job_id: str) -> JobState | None:
        with self._lock:
            return self._jobs.get(job_id)

    def user_job_count(self, user: str, statuses: set[str] | None = None) -> int:
        """Count jobs for *user*, optionally filtered by status."""
        with self._lock:
            return sum(
                1 for j in self._jobs.values()
                if j.user == user
                and (statuses is None or j.status in statuses)
            )

    def expire_jobs(self, ttl_seconds: float) -> int:
        """Remove completed/errored jobs older than *ttl_seconds*. Returns count."""
        cutoff = time.time() - ttl_seconds
        with self._lock:
            expired = [
                jid for jid, j in self._jobs.items()
                if j.status in ("done", "error") and j.created_at < cutoff
            ]
            for jid in expired:
                del self._jobs[jid]
            return len(expired)

    def make_progress_callback(self, job_id: str) -> Callable[[float, str], None]:
        """Return a callback suitable for pipeline progress reporting."""
        def _cb(progress: float, stage: str = "") -> None:
            self.update_progress(job_id, progress, stage)
        return _cb

    def to_dict(self, job_id: str) -> dict[str, Any] | None:
        job = self.get_job(job_id)
        if not job:
            return None
        return {
            "job_id": job.job_id,
            "job_type": job.job_type,
            "status": job.status,
            "progress": job.progress,
            "stage": job.stage,
            "result": job.result,
            "error": job.error,
        }


# Module-level singleton
job_manager = JobManager()
