from __future__ import annotations

import json
import re
import uuid
from dataclasses import replace
from pathlib import Path

from jsonschema import Draft202012Validator

from .background import BackgroundManager
from .features import Features
from .knowledge import MemoryStore, SkillCatalog
from .mcp import MCPManager
from .permissions import PermissionPolicy
from .prompts.builder import PromptBuilder
from .scheduler import CronScheduler
from .schemas import Message, ToolResult
from .tasks import TaskStore, TodoList
from .teams import TeamManager
from .tools.base import FunctionTool
from .tools.registry import create_coding_tools
from .worktrees import WorktreeManager


class Runtime:
    """Per-run services. CLI, Web, child agents and benchmarks all use this harness."""
    def __init__(self, agent, recorder, problem, *, name="lead", parent=None):
        self.agent, self.model, self.environment, self.config = agent, agent.model, agent.environment, agent.config
        self.recorder, self.problem, self.name, self.parent = recorder, problem, name, parent
        self.features = self.config.features or Features.profile(self.config.profile)
        self.root = agent.state_root
        self.hooks = agent.hooks
        self.policy = agent.policy
        self.tasks = TaskStore(self.root)
        self.todos = TodoList()
        self.skills = SkillCatalog([self.environment.workspace / "skills", *map(Path, self.config.skill_roots)])
        self.memory = MemoryStore(self.root)
        self.background = BackgroundManager(self.environment, self.root / "background", self.config.max_workers)
        self.scheduler = CronScheduler(self.root)
        self.worktrees = WorktreeManager(self.environment, self.root)
        self.teams = parent.teams if parent else TeamManager(
            self.root, self.tasks, self.worktrees, self._run_teammate,
            max_workers=self.config.max_workers, idle_timeout=self.config.team_idle_timeout)
        self.mcp = MCPManager(self.config.mcp_config, self.environment.workspace)
        self.prompt = PromptBuilder(self.config.system_prompt)
        self.compact_requested: str | None = None
        self.inbox_buffer: list[dict] = []
        self.child_results = []
        self.tools = self._tools()

    async def start(self):
        if self.features.mcp and self.config.mcp_config:
            self.tools.extend(await self.mcp.connect())
        self.by_name = {tool.name: tool for tool in self.tools}
        for tool in self.tools:
            Draft202012Validator.check_schema(tool.input_schema)

    async def dispatch(self, name, arguments):
        tool = self.by_name.get(name)
        if tool is None:
            raise KeyError(f"Unknown tool: {name}")
        Draft202012Validator(tool.input_schema).validate(arguments)
        await self.policy.check(name, arguments)
        if self.parent and not self.teams.can_write(self.name) and name not in {
            "read_file", "list_files", "grep_files", "inbox", "send_message", "submit_plan",
            "plan_wait", "protocol_respond", "team_status", "tool_help", "finish"}:
            raise PermissionError("An approved plan is required before executing this tool")
        payload = {"tool": name, "arguments": arguments, "runtime": self}
        await self.hooks.emit("before_tool", payload)
        if name == "finish" and (self.background.pending or (not self.parent and self.teams.pending)):
            return ToolResult(False, "Background commands or teammates are still running; wait or shut them down first")
        result = await tool.aexecute(arguments, self.environment)
        await self.hooks.emit("after_tool", payload | {"result": result})
        return result

    async def before_step(self, state):
        if self.features.background:
            for note in self.background.drain():
                self.recorder.event("background_notification", result=note)
                visible, _ = self.recorder.observation(json.dumps(note), self.config.tool_output_limit_chars)
                state.messages.append(Message(role="user", content="Background result: " + visible))
        if self.features.teams:
            notes = self.teams.bus.receive(self.name)
            self.inbox_buffer.extend(notes)
            for note in notes:
                if note["kind"] == "shutdown":
                    self.teams.protocols.respond(self.name, note["request_id"], True, "Stopping at a tool boundary")
                    return "shutdown"
                state.messages.append(Message(role="user", content="Team message: " + json.dumps(note)))
        if self.features.todo and self.todos.items and state.step - self.todos.last_update >= 3:
            state.messages.append(Message(role="user", content="Review and update your TodoWrite progress."))
            self.todos.last_update = state.step
        return None

    async def close(self):
        await self.background.close()
        if not self.parent:
            await self.teams.close()
        await self.mcp.close()
        if hasattr(self.environment, "cancel_all"):
            self.environment.cancel_all()

    async def _child(self, name, prompt, environment, *, readonly=False):
        from .agent.agent import RepoAgent
        config = replace(self.config, max_steps=self.config.child_max_steps,
                         runs_dir=self.recorder.run_dir / "children", state_dir=self.root,
                         auto_memory=False, permission_mode="readonly" if readonly else self.config.permission_mode)
        child = RepoAgent(model=self.model, environment=environment, config=config, hooks=self.hooks,
                          policy=PermissionPolicy("readonly") if readonly else self.policy)
        result = await child.run(prompt, instance_id=name + "-" + uuid.uuid4().hex[:8],
                                 name=name, parent_runtime=self)
        self.child_results.append(result.to_dict())
        self.recorder.event("child_finished", agent=name, result=result.to_dict())
        return result

    async def _run_teammate(self, name, prompt, environment):
        member = self.teams.members[name]
        if member["require_plan"]:
            prompt += "\nSubmit a plan with submit_plan and wait for lead approval with plan_wait before edits."
        return await self._child(name, prompt, environment)

    async def subagent(self, description, agent_type="explore"):
        if agent_type not in {"explore", "code"}:
            raise ValueError("agent_type must be explore or code")
        if agent_type == "explore":
            from .environment.local import LocalEnvironment
            env = LocalEnvironment(self.environment.workspace, command_timeout=self.environment.command_timeout)
            worktree = None
        else:
            worktree = self.worktrees.create("sub-" + uuid.uuid4().hex[:12])["name"]
            env = self.worktrees.environment_for(worktree)
        result = await self._child("sub-" + uuid.uuid4().hex[:8], description, env, readonly=agent_type == "explore")
        trajectory = json.loads((Path(result.run_dir) / "trajectory.json").read_text(encoding="utf-8"))
        summaries = [c["observation"] for step in trajectory["steps"] for c in step["tool_calls"] if c["tool_name"] == "finish"]
        return {"status": result.status, "summary": summaries[-1] if summaries else result.termination_reason,
                "worktree": worktree, "run_dir": result.run_dir}

    def artifact_read(self, artifact_id, offset=0, limit=10000):
        if not re.fullmatch(r"[a-f0-9]{32}", artifact_id):
            raise ValueError("Invalid artifact id")
        text = (self.recorder.run_dir / "outputs" / f"{artifact_id}.txt").read_text(encoding="utf-8")
        return text[max(0, offset):max(0, offset) + min(30000, max(1, limit))]

    def list_files(self, path=".", max_files=200):
        root = self.environment.resolve_path(path)
        found = []
        for item in sorted(root.rglob("*") if root.is_dir() else [root]):
            relative = item.relative_to(self.environment.workspace)
            if ".git" in relative.parts or item.is_symlink():
                continue
            if item.is_file() and item.resolve().is_relative_to(self.environment.workspace):
                found.append(str(relative))
            if len(found) >= max(1, min(2000, max_files)):
                break
        return found

    def grep_files(self, pattern, path=".", ignore_case=True):
        regex = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
        matches = []
        for filename in self.list_files(path, 2000):
            target = self.environment.resolve_path(filename)
            if target.stat().st_size > 2_000_000:
                continue
            for line_number, line in enumerate(self.environment.read_file(filename).splitlines(), 1):
                if regex.search(line):
                    matches.append(f"{filename}:{line_number}:{line[:500]}")
                    if len(matches) >= 200:
                        return "\n".join(matches)
        return "\n".join(matches) or "No matches"

    def _tools(self):
        tools = create_coding_tools(output_limit=self.config.tool_output_limit_chars)
        def add(name, description, props, required, handler):
            tools.append(FunctionTool(name, description, props, required, handler))
        s, n, b = {"type": "string"}, {"type": "integer"}, {"type": "boolean"}
        strings = {"type": "array", "items": s}
        add("list_files", "List workspace files, excluding .git.", {"path": s, "max_files": n}, [], self.list_files)
        add("grep_files", "Search workspace text with a regular expression.", {"pattern": s, "path": s, "ignore_case": b}, ["pattern"], self.grep_files)
        add("artifact_read", "Read full persisted tool output by artifact id.", {"artifact_id": s, "offset": n, "limit": n}, ["artifact_id"], self.artifact_read)
        add("tool_help", "List available runtime tools.", {}, [], lambda: [{"name": t.name, "description": t.description} for t in self.tools])
        def compact(focus=""):
            self.compact_requested = focus
            return "Compaction requested for the next model turn"
        add("compact", "Summarize completed work and retain recent tool pairs.", {"focus": s}, [], compact)
        if self.features.todo:
            add("TodoWrite", "Update the todo list; at most one in_progress item.",
                {"items": {"type": "array", "items": {"type": "object", "properties": {
                    "content": s, "status": {"enum": ["pending", "in_progress", "completed"]}, "activeForm": s},
                    "required": ["content", "status"]}}}, ["items"], self.todos.update)
        if self.features.skills:
            add("skill_list", "Discover skill metadata.", {}, [], self.skills.discover)
            add("load_skill", "Load a skill's complete instructions only when needed.", {"name": s}, ["name"], self.skills.load)
        if self.features.memory:
            add("memory_read", "Read a durable named memory.", {"name": s}, ["name"], self.memory.read)
            add("memory_write", "Save a durable project/user/feedback/reference memory.",
                {"name": s, "content": s, "kind": s, "description": s}, ["name", "content"], self.memory.write)
            add("memory_search", "Retrieve relevant memories.", {"query": s, "limit": n}, ["query"], self.memory.search)
            add("memory_consolidate", "Consolidate duplicate memories and rebuild index.", {}, [], self.memory.consolidate)
        if self.features.tasks:
            add("task_create", "Create a persistent task with dependencies.", {"subject": s, "description": s, "blocked_by": strings}, ["subject"], self.tasks.create)
            add("task_list", "List task graph and owners.", {}, [], self.tasks.list)
            add("task_get", "Read one task.", {"task_id": s}, ["task_id"], self.tasks.get)
            add("task_claim", "Atomically claim a ready task for yourself.", {"task_id": s}, [], lambda task_id=None: self.tasks.claim(self.name, task_id))
            add("task_update", "Update your task; dependency cycles and premature completion are rejected.",
                {"task_id": s, "status": s, "result": s, "blocked_by": strings}, ["task_id", "status"],
                lambda **a: self.tasks.update(owner=self.name, **a))
            add("task_delete", "Delete a non-running task without dependents.", {"task_id": s}, ["task_id"], self.tasks.delete)
        if self.features.background:
            add("background_run", "Start a command and continue working; completion is injected into context.",
                {"command": s, "cwd": s, "timeout": n}, ["command"], self.background.start)
            add("background_check", "Check background jobs.", {"job_id": s}, [], self.background.check)
            add("background_wait", "Wait up to 60 seconds for a background job.", {"job_id": s, "timeout": n}, ["job_id"], self.background.wait)
            add("background_cancel", "Cancel a background command and its process tree.", {"job_id": s}, ["job_id"], self.background.cancel)
        if self.features.cron and not self.parent:
            add("cron_schedule", "Schedule a bounded UTC cron prompt. Requires CLI --serve or Web CRON_ENABLED=true.",
                {"expression": s, "prompt": s, "max_runs": n, "durable": b}, ["expression", "prompt"], self.scheduler.schedule)
            add("cron_list", "List scheduled jobs and execution queue.", {}, [], self.scheduler.list)
            add("cron_cancel", "Cancel a scheduled job and queued executions.", {"job_id": s}, ["job_id"], self.scheduler.cancel)
        if self.features.subagents and not self.parent:
            add("subagent", "Run an independent agent: explore is read-only; code returns an isolated worktree patch.",
                {"description": s, "agent_type": {"enum": ["explore", "code"]}}, ["description"], self.subagent)
        if self.features.worktrees and not self.parent:
            add("worktree_create", "Create an isolated Git worktree at HEAD, optionally bound to a task.",
                {"name": s, "task_id": s}, ["name"], self.worktrees.create)
            add("worktree_list", "List managed worktrees.", {}, [], self.worktrees.list)
            add("worktree_diff", "Review a managed worktree patch.", {"name": s}, ["name"], self.worktrees.diff)
            add("worktree_merge", "Apply a completed worktree patch to the main workspace, refusing conflicts.",
                {"name": s}, ["name"], self._merge_worktree)
            add("worktree_keep", "Keep a managed worktree for later review.", {"name": s}, ["name"], self.worktrees.keep)
            add("worktree_remove", "Remove a clean managed worktree; dirty worktrees are refused.", {"name": s}, ["name"], self._remove_worktree)
        if self.features.teams:
            if not self.parent:
                add("team_spawn", "Start a real teammate in an isolated worktree; autonomous workers claim ready tasks.",
                    {"name": s, "prompt": s, "autonomous": b, "require_plan": b}, ["name"], self.teams.spawn)
            add("team_status", "Inspect teammates, results and worktrees.", {"name": s}, [], self.teams.status)
            add("team_wait", "Wait up to 60 seconds for a teammate.", {"name": s, "timeout": n}, ["name"], self.teams.wait)
            add("send_message", "Send a message to a teammate or lead.", {"recipient": s, "content": s}, ["recipient", "content"],
                lambda recipient, content: self.teams.bus.send(self.name, recipient, content))
            add("inbox", "Read messages delivered to this agent.", {}, [], self._inbox)
            add("protocol_request", "Request a teammate plan or graceful shutdown.",
                {"recipient": s, "kind": {"enum": ["shutdown", "plan"]}, "content": s}, ["recipient", "kind"],
                lambda recipient, kind, content="": self.teams.protocols.request(self.name, recipient, kind, content))
            add("protocol_respond", "Respond to a pending request addressed to you.",
                {"request_id": s, "approve": b, "content": s}, ["request_id", "approve"],
                lambda request_id, approve, content="": self.teams.protocols.respond(self.name, request_id, approve, content))
            if self.parent and self.name in self.teams.members:
                add("submit_plan", "Submit a plan for lead approval before modifications.", {"plan": s}, ["plan"],
                    lambda plan: self.teams.submit_plan(self.name, plan))
                add("plan_wait", "Wait for the lead to approve or reject your submitted plan.", {"timeout": n}, [],
                    lambda timeout=30: self.teams.plan_wait(self.name, timeout))
        return tools

    def _inbox(self):
        result, self.inbox_buffer = self.inbox_buffer, []
        return result + self.teams.bus.receive(self.name)

    def _check_worktree_idle(self, name):
        if any(m["worktree"] == name and not self.teams.workers[n].done() for n, m in self.teams.members.items()):
            raise ValueError("Wait for the teammate to stop before merging/removing its worktree")

    def _merge_worktree(self, name):
        self._check_worktree_idle(name)
        return self.worktrees.merge(name)

    def _remove_worktree(self, name):
        self._check_worktree_idle(name)
        return self.worktrees.remove(name)
