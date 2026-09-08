from __future__ import annotations

import asyncio
import uuid
from dataclasses import asdict

from .storage import atomic_json


class BackgroundManager:
    def __init__(self, environment, root, max_jobs=4):
        self.environment, self.root, self.max_jobs = environment, root, max_jobs
        self.jobs: dict[str, dict] = {}
        self.workers: dict[str, asyncio.Task] = {}
        self.envs = {}
        self.notifications: list[dict] = []

    def start(self, command: str, cwd: str = ".", timeout: float = 300) -> dict:
        if sum(not t.done() for t in self.workers.values()) >= self.max_jobs:
            raise ValueError("Background concurrency limit reached")
        key = uuid.uuid4().hex[:12]
        self.jobs[key] = dict(id=key, command=command, status="running")
        if hasattr(self.environment, "clone_for_workspace"):
            env = self.environment.clone_for_workspace(self.environment.workspace)
        else:
            env = self.environment
        self.envs[key] = env
        atomic_json(self.root / f"{key}.json", self.jobs[key])
        self.workers[key] = asyncio.create_task(self._run(key, env, command, cwd, timeout))
        return self.jobs[key].copy()

    async def _run(self, key, env, command, cwd, timeout):
        worker = asyncio.create_task(asyncio.to_thread(env.execute, command, cwd=cwd, timeout=timeout))
        try:
            result = await asyncio.shield(worker)
            self.jobs[key].update(status="completed" if result.exit_code == 0 else "failed", result=asdict(result))
        except asyncio.CancelledError:
            if hasattr(env, "cancel_all"):
                env.cancel_all()
            await asyncio.gather(worker, return_exceptions=True)
            self.jobs[key].update(status="cancelled")
        except Exception as exc:
            self.jobs[key].update(status="failed", error=str(exc))
        finally:
            atomic_json(self.root / f"{key}.json", self.jobs[key])
            self.notifications.append(self.jobs[key].copy())

    def check(self, job_id: str | None = None):
        return self.jobs[job_id] if job_id else list(self.jobs.values())

    async def wait(self, job_id: str, timeout: float = 30):
        try:
            await asyncio.wait_for(asyncio.shield(self.workers[job_id]), min(60, max(0.01, timeout)))
        except TimeoutError:
            pass
        return self.jobs[job_id]

    async def cancel(self, job_id: str):
        self.workers[job_id].cancel()
        await asyncio.gather(self.workers[job_id], return_exceptions=True)
        if self.jobs[job_id]["status"] == "running":
            self.jobs[job_id]["status"] = "cancelled"
            atomic_json(self.root / f"{job_id}.json", self.jobs[job_id])
        return self.jobs[job_id]

    def drain(self):
        notes, self.notifications = self.notifications, []
        return notes

    async def close(self):
        await asyncio.gather(*(self.cancel(key) for key, task in self.workers.items() if not task.done()))

    @property
    def pending(self):
        return any(not task.done() for task in self.workers.values())
