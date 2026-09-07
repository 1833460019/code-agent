from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone

from .storage import atomic_json, file_lock, identifier

PROCESS_ID = uuid.uuid4().hex


def cron_field(expr: str, low: int, high: int) -> set[int]:
    values = set()
    for part in expr.split(","):
        pieces = part.split("/")
        if len(pieces) > 2:
            raise ValueError("Invalid cron step")
        span = pieces[0]
        step = int(pieces[1]) if len(pieces) == 2 else 1
        if step <= 0:
            raise ValueError("Cron step must be positive")
        if span == "*":
            start, end = low, high
        elif "-" in span:
            start, end = map(int, span.split("-"))
        else:
            start = int(span)
            end = high if len(pieces) == 2 else start
        if not low <= start <= end <= high:
            raise ValueError("Cron field out of range")
        values.update(range(start, end + 1, step))
    return values


def cron_matches(expression: str, now: datetime) -> bool:
    parts = expression.split()
    if len(parts) != 5:
        raise ValueError("Cron needs five fields: minute hour day month weekday (UTC)")
    ranges = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]
    parsed = [cron_field(p, *r) for p, r in zip(parts, ranges)]
    dow = (now.weekday() + 1) % 7
    day = now.day in parsed[2]
    weekday = dow in parsed[4] or (dow == 0 and 7 in parsed[4])
    days_match = (day or weekday) if parts[2] != "*" and parts[4] != "*" else day and weekday
    return now.minute in parsed[0] and now.hour in parsed[1] and now.month in parsed[3] and days_match


class CronScheduler:
    """UTC cron produces persisted queue entries; a separate consumer runs the agent."""
    def __init__(self, root):
        self.path, self.lock = root / "cron.json", root / "cron.lock"

    def _read(self):
        return json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {"jobs": {}, "queue": []}

    def schedule(self, expression: str, prompt: str, max_runs: int = 1, durable: bool = True) -> dict:
        cron_matches(expression, datetime.now(timezone.utc))
        if not prompt.strip() or not 1 <= max_runs <= 10000:
            raise ValueError("A prompt and max_runs in 1..10000 are required")
        with file_lock(self.lock):
            data = self._read()
            if sum(j["status"] == "active" for j in data["jobs"].values()) >= 32:
                raise ValueError("Cron job limit reached")
            key = uuid.uuid4().hex[:12]
            job = dict(id=key, expression=expression, prompt=prompt, max_runs=max_runs, runs=0,
                       durable=durable, process_id=PROCESS_ID, status="active", last_tick=None)
            data["jobs"][key] = job
            atomic_json(self.path, data)
            return job

    def list(self):
        with file_lock(self.lock):
            return self._read()

    def cancel(self, job_id: str):
        with file_lock(self.lock):
            data = self._read()
            data["jobs"][identifier(job_id)]["status"] = "cancelled"
            for entry in data["queue"]:
                if entry["job_id"] == job_id and entry["status"] == "queued":
                    entry["status"] = "cancelled"
            atomic_json(self.path, data)
        return "Cancelled"

    def tick(self, now: datetime | None = None):
        now = now or datetime.now(timezone.utc)
        stamp = now.strftime("%Y-%m-%dT%H:%M")
        with file_lock(self.lock):
            data = self._read()
            for job in data["jobs"].values():
                if job["status"] != "active" or job["last_tick"] == stamp or not cron_matches(job["expression"], now):
                    continue
                job["last_tick"], job["runs"] = stamp, job["runs"] + 1
                data["queue"].append(dict(id=uuid.uuid4().hex, job_id=job["id"], prompt=job["prompt"], status="queued"))
                if job["runs"] >= job["max_runs"]:
                    job["status"] = "completed"
            atomic_json(self.path, data)

    def claim(self):
        with file_lock(self.lock):
            data = self._read()
            for entry in data["queue"]:
                if entry["status"] == "queued":
                    entry["status"] = "running"
                    atomic_json(self.path, data)
                    return entry.copy()
        return None

    def complete(self, entry_id, result):
        with file_lock(self.lock):
            data = self._read()
            entry = next(e for e in data["queue"] if e["id"] == entry_id)
            entry.update(status="completed" if result.get("status") == "success" else "failed", result=result)
            atomic_json(self.path, data)

    def recover_interrupted(self):
        with file_lock(self.lock):
            data = self._read()
            for entry in data["queue"]:
                if entry["status"] == "running":
                    entry.update(status="failed", result={"error": "scheduler interrupted; explicit rescheduling required"})
            for job in data["jobs"].values():
                if not job["durable"] and job.get("process_id") != PROCESS_ID:
                    job["status"] = "cancelled"
                    for entry in data["queue"]:
                        if entry["job_id"] == job["id"] and entry["status"] == "queued":
                            entry["status"] = "cancelled"
            atomic_json(self.path, data)

    async def serve(self, run, stop: asyncio.Event, *, interval=1):
        # Only one consumer may recover and execute a given queue at a time.
        with file_lock(self.path.with_suffix(".consumer.lock"), timeout=0.2):
            self.recover_interrupted()
            async def pause():
                try:
                    await asyncio.wait_for(stop.wait(), max(0.01, interval))
                except TimeoutError:
                    pass

            async def produce():
                while not stop.is_set():
                    self.tick()
                    await pause()

            async def consume():
                while not stop.is_set():
                    entry = self.claim()
                    if not entry:
                        await pause()
                        continue
                    try:
                        result = await run(entry)
                    except asyncio.CancelledError:
                        self.complete(entry["id"], {"status": "terminated", "error": "consumer cancelled"})
                        raise
                    except Exception as exc:
                        result = {"status": "error", "error": str(exc)}
                    self.complete(entry["id"], result)

            async with asyncio.TaskGroup() as group:
                group.create_task(produce())
                group.create_task(consume())
