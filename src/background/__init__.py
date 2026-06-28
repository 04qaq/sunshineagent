"""Background job manager for subagent tasks."""

import asyncio
from dataclasses import dataclass
from enum import Enum

from ulid import ULID


class JobStatus(Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Job:
    id: str
    session_id: str
    task: asyncio.Task
    status: JobStatus = JobStatus.ACTIVE
    result: str | None = None
    error: str | None = None


class BackgroundJobManager:
    def __init__(self):
        self._jobs: dict[str, Job] = {}

    async def start(self, session_id: str, coro) -> Job:
        task = asyncio.create_task(coro)
        job = Job(
            id=f"job_{ULID()}",
            session_id=session_id,
            task=task,
        )
        self._jobs[job.id] = job

        def done_callback(t: asyncio.Task):
            if job.status in (JobStatus.CANCELLED,):
                return
            if t.exception():
                job.status = JobStatus.FAILED
                job.error = str(t.exception())
            else:
                job.status = JobStatus.COMPLETED
                job.result = str(t.result()) if t.result() else None

        task.add_done_callback(done_callback)
        return job

    async def wait(self, job_id: str, timeout: float | None = None) -> str | None:
        job = self._jobs[job_id]
        try:
            return await asyncio.wait_for(job.task, timeout=timeout)
        except TimeoutError:
            return None

    async def cancel(self, job_id: str):
        job = self._jobs[job_id]
        job.task.cancel()
        job.status = JobStatus.CANCELLED

    async def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)
