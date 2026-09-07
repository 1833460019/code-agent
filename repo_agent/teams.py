from __future__ import annotations

import asyncio
import json
import time
import uuid

from .storage import atomic_json, file_lock, identifier


class MessageBus:
    def __init__(self, root):
        self.path, self.lock = root / "messages.json", root / "messages.lock"

    def _read(self):
        return json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else []

    def send(self, sender: str, recipient: str, content: str, kind="message", request_id=None):
        identifier(sender)
        identifier(recipient)
        message = dict(id=uuid.uuid4().hex, sender=sender, recipient=recipient,
                       content=content, kind=kind, request_id=request_id, read=False, timestamp=time.time())
        with file_lock(self.lock):
            data = self._read()
            data.append(message)
            atomic_json(self.path, data)
        return message

    def receive(self, recipient: str):
        with file_lock(self.lock):
            data = self._read()
            found = [m for m in data if m["recipient"] == recipient and not m["read"]]
            for message in found:
                message["read"] = True
            atomic_json(self.path, data)
            return found


class Protocols:
    def __init__(self, root, bus: MessageBus):
        self.path, self.lock, self.bus = root / "protocols.json", root / "protocols.lock", bus

    def _read(self):
        return json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}

    def request(self, sender, recipient, kind, content=""):
        if kind not in {"shutdown", "plan", "plan_approval"}:
            raise ValueError("Unknown protocol request")
        key = uuid.uuid4().hex[:12]
        record = dict(id=key, sender=sender, recipient=recipient, kind=kind, content=content,
                      status="pending", created_at=time.time())
        with file_lock(self.lock):
            data = self._read()
            data[key] = record
            atomic_json(self.path, data)
        self.bus.send(sender, recipient, content, kind, key)
        return record

    def respond(self, recipient, request_id, approve: bool, content=""):
        with file_lock(self.lock):
            data = self._read()
            record = data[identifier(request_id)]
            if record["recipient"] != recipient or record["status"] != "pending":
                raise ValueError("Response does not match a pending request for this agent")
            record.update(status="approved" if approve else "rejected", response=content)
            atomic_json(self.path, data)
        self.bus.send(recipient, record["sender"], content, "response", request_id)
        return record

    def get(self, request_id):
        with file_lock(self.lock):
            return self._read()[identifier(request_id)]


class TeamManager:
    """Real persistent teammates with isolated worktrees and independent model loops."""
    def __init__(self, root, tasks, worktrees, runner, max_workers=3, idle_timeout=30):
        self.root, self.tasks, self.worktrees, self.runner = root, tasks, worktrees, runner
        self.bus = MessageBus(root)
        self.protocols = Protocols(root, self.bus)
        self.max_workers, self.idle_timeout = max_workers, idle_timeout
        self.members: dict[str, dict] = {}
        self.workers: dict[str, asyncio.Task] = {}

    def spawn(self, name: str, prompt: str = "", autonomous: bool = False, require_plan: bool = False):
        identifier(name)
        if name == "lead" or name in self.members:
            raise ValueError("Choose a new teammate name")
        if sum(not worker.done() for worker in self.workers.values()) >= self.max_workers:
            raise ValueError("Team concurrency limit reached")
        tree_name = "team-" + uuid.uuid4().hex[:8] + "-" + name[:40]
        tree = self.worktrees.create(tree_name)
        member = dict(name=name, status="starting", worktree=tree_name, path=tree["path"],
                      autonomous=autonomous, require_plan=require_plan, plan_request=None, runs=[])
        self.members[name] = member
        self._save(name)
        self.workers[name] = asyncio.create_task(self._worker(name, prompt))
        return member.copy()

    def _save(self, name):
        atomic_json(self.root / "teammates" / f"{identifier(name)}.json", self.members[name])

    async def _worker(self, name, prompt):
        member = self.members[name]
        current_task = None
        try:
            count, idle_since = 0, time.monotonic()
            while count < 20:
                current_task = self.tasks.claim(name) if member["autonomous"] else None
                if current_task:
                    work = current_task["subject"] + "\n" + current_task["description"]
                    member["task_id"] = current_task["id"]
                elif prompt:
                    work, prompt = prompt, ""
                elif member["autonomous"] and time.monotonic() - idle_since < self.idle_timeout:
                    member["status"] = "idle"
                    self._save(name)
                    # An idle worker can complete the shutdown handshake without a model call.
                    inbox = self.bus.receive(name)
                    for message in inbox:
                        if message["kind"] == "shutdown":
                            self.protocols.respond(name, message["request_id"], True, "Idle worker stopped")
                            return
                        if message["kind"] == "message":
                            prompt += "\n" + message["content"]
                    await asyncio.sleep(0.1)
                    continue
                else:
                    break
                member["status"] = "working"
                self._save(name)
                result = await self.runner(name, work, self.worktrees.environment_for(member["worktree"]))
                member["runs"].append(result.to_dict())
                if current_task:
                    self.tasks.update(current_task["id"], owner=name,
                                      status="completed" if result.status == "success" else "failed",
                                      result=result.run_dir)
                    current_task = None
                self.bus.send(name, "lead", json.dumps({"status": result.status, "run_dir": result.run_dir,
                                                       "worktree": member["worktree"]}))
                count += 1
                idle_since = time.monotonic()
                if not member["autonomous"] or result.termination_reason == "shutdown":
                    break
        except asyncio.CancelledError:
            member["status"] = "cancelled"
            if current_task:
                self.tasks.update(current_task["id"], owner=name, status="pending", result="Worker cancelled")
        except Exception as exc:
            member.update(status="failed", error=str(exc))
            if current_task:
                self.tasks.update(current_task["id"], owner=name, status="failed", result=str(exc))
        finally:
            if member["status"] not in {"failed", "cancelled"}:
                member["status"] = "stopped"
            self._save(name)

    def status(self, name: str | None = None):
        def view(member):
            result = {key: value for key, value in member.items() if key != "runs"}
            result["run_count"] = len(member["runs"])
            if member["runs"]:
                result["last_run"] = {key: member["runs"][-1][key] for key in
                                      ("status", "termination_reason", "run_dir")}
            return result
        return view(self.members[name]) if name else [view(m) for m in self.members.values()]

    async def wait(self, name: str, timeout: float = 30):
        try:
            await asyncio.wait_for(asyncio.shield(self.workers[name]), min(60, max(0.01, timeout)))
        except TimeoutError:
            pass
        return self.status(name)

    def submit_plan(self, sender, plan):
        member = self.members[sender]
        request = self.protocols.request(sender, "lead", "plan_approval", plan)
        member["plan_request"] = request["id"]
        self._save(sender)
        return request

    def can_write(self, name):
        member = self.members.get(name)
        if not member or not member["require_plan"]:
            return True
        request_id = member["plan_request"]
        return bool(request_id and self.protocols.get(request_id)["status"] == "approved")

    async def plan_wait(self, name, timeout=30):
        request_id = self.members[name]["plan_request"]
        if not request_id:
            raise ValueError("Submit a plan first")
        deadline = time.monotonic() + min(60, timeout)
        while time.monotonic() < deadline:
            result = self.protocols.get(request_id)
            if result["status"] != "pending":
                return result
            await asyncio.sleep(0.05)
        return self.protocols.get(request_id)

    @property
    def pending(self):
        return any(not task.done() for task in self.workers.values())

    async def close(self):
        for task in self.workers.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.workers.values(), return_exceptions=True)
