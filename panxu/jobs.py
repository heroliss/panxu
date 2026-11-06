"""Background jobs with progress, cancellation and polling-friendly status."""
from __future__ import annotations

import logging
import threading
import time
import traceback
import uuid
from contextlib import contextmanager

from . import winapi
from .fsops import Cancelled

log = logging.getLogger(__name__)


class UserError(Exception):
    """An error whose message is meant for the user (Chinese, actionable)."""


class BusyError(UserError):
    def __init__(self, job):
        self.job = job.snapshot()
        super().__init__('首页一键体检正在进行，本页会复用体检结果，无需重复检查。' if job.kind == 'checkup'
                         else '另一项检查或处理正在进行，请等它完成后再开始；已有结果仍可查看。')


class Job:
    def __init__(self, kind: str, group: str | None, synchronized: bool = False):
        self.id = uuid.uuid4().hex[:10]
        self.kind = kind
        self.group = group
        self.synchronized = synchronized
        self.status = 'running'
        self.progress: dict = {}
        self.result = None
        self.error: str | None = None
        self.cancel = threading.Event()
        self.started = time.time()
        self.finished: float | None = None
        self._lock = threading.Lock()

    def update(self, **values):
        with self._lock:
            self.progress.update(values)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                'id': self.id, 'kind': self.kind, 'status': self.status,
                'synchronized': self.synchronized,
                'progress': dict(self.progress), 'error': self.error,
                'elapsed': round((self.finished or time.time()) - self.started, 1),
                'result': self.result if self.status == 'done' else None,
            }


class JobManager:
    KEEP_SECONDS = 600

    def __init__(self, synchronize: bool = False):
        self._jobs: dict[str, Job] = {}
        self._lock = threading.RLock()
        self.synchronize = synchronize

    def _check_busy(self):
        for job in self._jobs.values():
            if job.synchronized and job.status == 'running':
                raise BusyError(job)

    @contextmanager
    def submission(self):
        """Keep input reads and job registration atomic with respect to other submissions."""
        with self._lock:
            self._check_busy()
            yield

    def start(self, kind: str, fn, *args, group: str | None = None, **kwargs) -> str:
        """Run ``fn(job, *args, **kwargs)`` in a thread. ``group`` serialises
        jobs that must not overlap (e.g. anything that deletes)."""
        with self._lock:
            self._prune()
            synchronized = self.synchronize and kind != 'ai_ask'
            if synchronized:
                self._check_busy()
            if group:
                for other in self._jobs.values():
                    if other.group == group and other.status == 'running':
                        raise UserError('另一项清理操作正在进行，请等它完成后再试。')
            job = Job(kind, group, synchronized)
            self._jobs[job.id] = job

        def runner():
            winapi.co_initialize()
            try:
                result = fn(job, *args, **kwargs)
                with job._lock:
                    job.result = result
                    job.status = 'cancelled' if job.cancel.is_set() and result is None else 'done'
            except Cancelled:
                with job._lock:
                    job.status = 'cancelled'
            except UserError as exc:
                with job._lock:
                    job.status, job.error = 'error', str(exc)
            except Exception as exc:  # noqa: BLE001 - surface unexpected errors to the UI
                log.error('job %s failed: %s', kind, traceback.format_exc())
                with job._lock:
                    job.status, job.error = 'error', f'内部错误：{exc}'
            finally:
                job.finished = time.time()

        threading.Thread(target=runner, name=f'job-{kind}', daemon=True).start()
        return job.id

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def status(self, job_id: str) -> dict:
        job = self.get(job_id)
        if job is None:
            return {'id': job_id, 'status': 'missing'}
        return job.snapshot()

    def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        if job and job.status == 'running':
            job.cancel.set()
            return True
        return False

    def running(self, group: str | None = None) -> list[dict]:
        with self._lock:
            return [j.snapshot() for j in self._jobs.values() if j.status == 'running' and (group is None or j.group == group)]

    def stop(self, group: str | None = None, timeout: float = 8.0) -> bool:
        """Ask running jobs to stop and wait until they did (they finish the current file and write their
        record first). True when all of them stopped in time."""
        with self._lock:
            jobs = [j for j in self._jobs.values() if j.status == 'running' and (group is None or j.group == group)]
        for j in jobs:
            j.cancel.set()
        end = time.time() + timeout
        while time.time() < end and any(j.finished is None for j in jobs):
            time.sleep(0.05)
        return all(j.finished is not None for j in jobs)

    def _prune(self):
        now = time.time()
        for jid in [j.id for j in self._jobs.values() if j.finished and now - j.finished > self.KEEP_SECONDS]:
            del self._jobs[jid]
