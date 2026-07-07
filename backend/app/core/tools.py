from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field, ValidationError

from .config import Settings


class ToolResult(BaseModel):
    ok: bool
    output: str
    data: Any | None = None


class ToolContext(BaseModel):
    workspace: Path
    settings: Settings
    todos: list[dict[str, str]]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any], ToolContext], ToolResult]


class BackgroundTask(BaseModel):
    task_id: str
    command: str
    cwd: str
    status: str = "running"
    output: str = ""
    return_code: int | None = None
    started_at: float = Field(default_factory=time.time)
    finished_at: float | None = None


class BackgroundManager:
    def __init__(self) -> None:
        self.tasks: dict[str, BackgroundTask] = {}
        self.notifications: list[str] = []
        self.lock = threading.Lock()

    def start(self, command: str, cwd: Path, settings: Settings) -> BackgroundTask:
        task = BackgroundTask(task_id=str(uuid.uuid4())[:8], command=command, cwd=str(cwd))
        with self.lock:
            self.tasks[task.task_id] = task
        thread = threading.Thread(target=self._run, args=(task.task_id, command, cwd, settings), daemon=True)
        thread.start()
        return task

    def _run(self, task_id: str, command: str, cwd: Path, settings: Settings) -> None:
        try:
            completed = subprocess.run(
                command,
                shell=True,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=max(settings.command_timeout_seconds * 5, settings.command_timeout_seconds),
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
            output = "\n".join(part for part in [completed.stdout, completed.stderr] if part).strip() or "(no output)"
            status = "completed" if completed.returncode == 0 else "failed"
            return_code = completed.returncode
        except subprocess.TimeoutExpired:
            output = "Background command timed out."
            status = "failed"
            return_code = None
        except Exception as exc:
            output = f"{type(exc).__name__}: {exc}"
            status = "failed"
            return_code = None
        with self.lock:
            task = self.tasks[task_id]
            task.status = status
            task.output = _truncate(output, settings.tool_output_limit_chars)
            task.return_code = return_code
            task.finished_at = time.time()
            self.notifications.append(f"[background:{task_id}] {status}: {task.output[:1200]}")

    def check(self, task_id: str | None = None) -> str:
        with self.lock:
            tasks = [self.tasks[task_id]] if task_id and task_id in self.tasks else list(self.tasks.values())
        if task_id and not tasks:
            return f"Unknown background task: {task_id}"
        if not tasks:
            return "No background tasks."
        lines: list[str] = []
        for task in tasks:
            lines.append(f"{task.task_id}: {task.status} rc={task.return_code} command={task.command}")
            if task.output:
                lines.append(task.output[:2400])
        return "\n".join(lines)

    def drain(self) -> list[str]:
        with self.lock:
            notes = list(self.notifications)
            self.notifications.clear()
        return notes


BACKGROUND = BackgroundManager()


def drain_background_notifications() -> list[str]:
    return BACKGROUND.drain()


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... ({len(text) - limit} chars truncated)"


def _safe_path(workspace: Path, raw_path: str) -> Path:
    candidate = (workspace / raw_path).resolve()
    workspace = workspace.resolve()
    if not candidate.is_relative_to(workspace):
        raise ValueError(f"Path escapes workspace: {raw_path}")
    return candidate


def _state_dir(context: ToolContext, name: str) -> Path:
    path = context.workspace / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _dangerous_shell(command: str) -> str | None:
    lowered = command.lower()
    blocked_fragments = ["rm -rf /", "format ", "shutdown", "reboot", "del /s", "rd /s", "> /dev/", "mkfs"]
    for fragment in blocked_fragments:
        if fragment in lowered:
            return fragment
    return None


def _run_command(input_data: dict[str, Any], context: ToolContext) -> ToolResult:
    command = str(input_data.get("command", "")).strip()
    cwd = str(input_data.get("cwd", ".")).strip() or "."
    if not command:
        return ToolResult(ok=False, output="Command is required.")
    blocked = _dangerous_shell(command)
    if blocked:
        return ToolResult(ok=False, output=f"Blocked dangerous command fragment: {blocked}")
    try:
        run_cwd = _safe_path(context.workspace, cwd)
        run_cwd.mkdir(parents=True, exist_ok=True)
        completed = subprocess.run(
            command,
            shell=True,
            cwd=run_cwd,
            capture_output=True,
            text=True,
            timeout=context.settings.command_timeout_seconds,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        output = "\n".join(part for part in [completed.stdout, completed.stderr] if part).strip() or "(no output)"
        return ToolResult(ok=completed.returncode == 0, output=_truncate(output, context.settings.tool_output_limit_chars))
    except subprocess.TimeoutExpired:
        return ToolResult(ok=False, output="Command timed out.")
    except Exception as exc:
        return ToolResult(ok=False, output=f"{type(exc).__name__}: {exc}")


def _background_run(input_data: dict[str, Any], context: ToolContext) -> ToolResult:
    command = str(input_data.get("command", "")).strip()
    cwd = str(input_data.get("cwd", ".")).strip() or "."
    if not command:
        return ToolResult(ok=False, output="Command is required.")
    blocked = _dangerous_shell(command)
    if blocked:
        return ToolResult(ok=False, output=f"Blocked dangerous command fragment: {blocked}")
    try:
        run_cwd = _safe_path(context.workspace, cwd)
        run_cwd.mkdir(parents=True, exist_ok=True)
        task = BACKGROUND.start(command, run_cwd, context.settings)
        return ToolResult(ok=True, output=f"Background task {task.task_id} started: {command}", data=task.model_dump())
    except Exception as exc:
        return ToolResult(ok=False, output=f"{type(exc).__name__}: {exc}")


def _background_check(input_data: dict[str, Any], context: ToolContext) -> ToolResult:
    task_id = input_data.get("task_id")
    return ToolResult(ok=True, output=BACKGROUND.check(str(task_id) if task_id else None))


def _read_file(input_data: dict[str, Any], context: ToolContext) -> ToolResult:
    try:
        path = _safe_path(context.workspace, str(input_data["path"]))
        offset = max(int(input_data.get("offset", 0)), 0)
        limit = input_data.get("limit")
        limit_int = int(limit) if limit is not None else None
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        selected = lines[offset:]
        if limit_int is not None and limit_int >= 0:
            selected = selected[:limit_int]
        return ToolResult(ok=True, output=_truncate("\n".join(selected), context.settings.tool_output_limit_chars))
    except Exception as exc:
        return ToolResult(ok=False, output=f"{type(exc).__name__}: {exc}")


def _write_file(input_data: dict[str, Any], context: ToolContext) -> ToolResult:
    try:
        path = _safe_path(context.workspace, str(input_data["path"]))
        content = str(input_data.get("content", ""))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return ToolResult(ok=True, output=f"Wrote {len(content)} chars to {path.relative_to(context.workspace)}")
    except Exception as exc:
        return ToolResult(ok=False, output=f"{type(exc).__name__}: {exc}")


def _edit_file(input_data: dict[str, Any], context: ToolContext) -> ToolResult:
    try:
        path = _safe_path(context.workspace, str(input_data["path"]))
        old_text = str(input_data["old_text"])
        new_text = str(input_data["new_text"])
        content = path.read_text(encoding="utf-8")
        if old_text not in content:
            return ToolResult(ok=False, output=f"Text not found in {path.relative_to(context.workspace)}")
        path.write_text(content.replace(old_text, new_text, 1), encoding="utf-8")
        return ToolResult(ok=True, output=f"Edited {path.relative_to(context.workspace)}")
    except Exception as exc:
        return ToolResult(ok=False, output=f"{type(exc).__name__}: {exc}")


def _list_files(input_data: dict[str, Any], context: ToolContext) -> ToolResult:
    try:
        raw_path = str(input_data.get("path", "."))
        max_files = int(input_data.get("max_files", 200))
        base = _safe_path(context.workspace, raw_path)
        if base.is_file():
            return ToolResult(ok=True, output=str(base.relative_to(context.workspace)))
        files: list[str] = []
        for path in sorted(base.rglob("*")):
            if path.is_file():
                files.append(str(path.relative_to(context.workspace)))
            if len(files) >= max_files:
                break
        return ToolResult(ok=True, output="\n".join(files) if files else "(no files)")
    except Exception as exc:
        return ToolResult(ok=False, output=f"{type(exc).__name__}: {exc}")


def _workspace_tree(input_data: dict[str, Any], context: ToolContext) -> ToolResult:
    try:
        raw_path = str(input_data.get("path", "."))
        max_depth = int(input_data.get("max_depth", 3))
        base = _safe_path(context.workspace, raw_path)
        lines: list[str] = [str(base.relative_to(context.workspace)) or "."]
        def walk(path: Path, depth: int, prefix: str) -> None:
            if depth >= max_depth or not path.is_dir():
                return
            children = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))[:80]
            for index, child in enumerate(children):
                branch = "└─ " if index == len(children) - 1 else "├─ "
                lines.append(prefix + branch + child.name + ("/" if child.is_dir() else ""))
                walk(child, depth + 1, prefix + ("   " if index == len(children) - 1 else "│  "))
        walk(base, 0, "")
        return ToolResult(ok=True, output="\n".join(lines))
    except Exception as exc:
        return ToolResult(ok=False, output=f"{type(exc).__name__}: {exc}")


def _grep_files(input_data: dict[str, Any], context: ToolContext) -> ToolResult:
    try:
        pattern = str(input_data["pattern"])
        raw_path = str(input_data.get("path", "."))
        regex = re.compile(pattern, re.IGNORECASE if input_data.get("ignore_case", True) else 0)
        base = _safe_path(context.workspace, raw_path)
        matches: list[str] = []
        paths = [base] if base.is_file() else [p for p in base.rglob("*") if p.is_file()]
        for path in paths:
            try:
                for line_no, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                    if regex.search(line):
                        rel = path.relative_to(context.workspace)
                        matches.append(f"{rel}:{line_no}: {line[:240]}")
                        if len(matches) >= 200:
                            return ToolResult(ok=True, output="\n".join(matches))
            except OSError:
                continue
        return ToolResult(ok=True, output="\n".join(matches) if matches else "(no matches)")
    except Exception as exc:
        return ToolResult(ok=False, output=f"{type(exc).__name__}: {exc}")


class TodoItem(BaseModel):
    content: str
    status: str = Field(pattern="^(pending|in_progress|completed)$")
    activeForm: str = ""


def _todo_write(input_data: dict[str, Any], context: ToolContext) -> ToolResult:
    raw_items = input_data.get("items", input_data.get("todos", []))
    if not isinstance(raw_items, list):
        return ToolResult(ok=False, output="items must be a list")
    try:
        items = [TodoItem.model_validate(item).model_dump() for item in raw_items]
    except ValidationError as exc:
        return ToolResult(ok=False, output=exc.json())
    if sum(1 for item in items if item["status"] == "in_progress") > 1:
        return ToolResult(ok=False, output="Only one todo can be in_progress.")
    context.todos.clear()
    context.todos.extend(items)
    lines = []
    for item in items:
        marker = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]"}[item["status"]]
        suffix = f" <- {item['activeForm']}" if item.get("activeForm") and item["status"] == "in_progress" else ""
        lines.append(f"{marker} {item['content']}{suffix}")
    return ToolResult(ok=True, output="\n".join(lines) or "No todos.", data=items)


def _task_path(context: ToolContext, task_id: str) -> Path:
    return _state_dir(context, ".tasks") / f"{task_id}.json"


def _load_task(context: ToolContext, task_id: str) -> dict[str, Any]:
    return json.loads(_task_path(context, task_id).read_text(encoding="utf-8"))


def _save_task(context: ToolContext, task: dict[str, Any]) -> None:
    _task_path(context, task["id"]).write_text(json.dumps(task, indent=2, ensure_ascii=False), encoding="utf-8")


def _task_create(input_data: dict[str, Any], context: ToolContext) -> ToolResult:
    subject = str(input_data.get("subject", "")).strip()
    if not subject:
        return ToolResult(ok=False, output="subject is required")
    task = {
        "id": f"task_{int(time.time())}_{uuid.uuid4().hex[:6]}",
        "subject": subject,
        "description": str(input_data.get("description", "")),
        "status": "pending",
        "owner": input_data.get("owner"),
        "blockedBy": input_data.get("blockedBy", []) or [],
        "createdAt": time.time(),
        "updatedAt": time.time(),
    }
    _save_task(context, task)
    return ToolResult(ok=True, output=json.dumps(task, indent=2, ensure_ascii=False), data=task)


def _task_list(input_data: dict[str, Any], context: ToolContext) -> ToolResult:
    tasks_dir = _state_dir(context, ".tasks")
    tasks = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(tasks_dir.glob("task_*.json"))]
    status_filter = input_data.get("status")
    if status_filter:
        tasks = [task for task in tasks if task.get("status") == status_filter]
    if not tasks:
        return ToolResult(ok=True, output="No tasks.", data=[])
    lines = []
    for task in tasks:
        marker = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]", "blocked": "[!]"}.get(task.get("status"), "[?]")
        deps = f" blockedBy={task.get('blockedBy')}" if task.get("blockedBy") else ""
        owner = f" @{task.get('owner')}" if task.get("owner") else ""
        lines.append(f"{marker} {task['id']}: {task['subject']}{owner}{deps}")
    return ToolResult(ok=True, output="\n".join(lines), data=tasks)


def _task_get(input_data: dict[str, Any], context: ToolContext) -> ToolResult:
    try:
        task = _load_task(context, str(input_data["task_id"]))
        return ToolResult(ok=True, output=json.dumps(task, indent=2, ensure_ascii=False), data=task)
    except Exception as exc:
        return ToolResult(ok=False, output=f"{type(exc).__name__}: {exc}")


def _task_update(input_data: dict[str, Any], context: ToolContext) -> ToolResult:
    try:
        task = _load_task(context, str(input_data["task_id"]))
        for key in ["subject", "description", "status", "owner"]:
            if key in input_data and input_data[key] is not None:
                task[key] = input_data[key]
        if "blockedBy" in input_data and input_data["blockedBy"] is not None:
            task["blockedBy"] = input_data["blockedBy"]
        task["updatedAt"] = time.time()
        _save_task(context, task)
        return ToolResult(ok=True, output=json.dumps(task, indent=2, ensure_ascii=False), data=task)
    except Exception as exc:
        return ToolResult(ok=False, output=f"{type(exc).__name__}: {exc}")


def _task_delete(input_data: dict[str, Any], context: ToolContext) -> ToolResult:
    try:
        path = _task_path(context, str(input_data["task_id"]))
        if not path.exists():
            return ToolResult(ok=False, output="Task not found")
        path.unlink()
        return ToolResult(ok=True, output=f"Deleted {input_data['task_id']}")
    except Exception as exc:
        return ToolResult(ok=False, output=f"{type(exc).__name__}: {exc}")


def _memory_path(context: ToolContext) -> Path:
    memory_dir = _state_dir(context, ".memory")
    return memory_dir / "MEMORY.md"


def _memory_read(input_data: dict[str, Any], context: ToolContext) -> ToolResult:
    path = _memory_path(context)
    if not path.exists():
        return ToolResult(ok=True, output="No memory yet.")
    return ToolResult(ok=True, output=_truncate(path.read_text(encoding="utf-8"), context.settings.tool_output_limit_chars))


def _memory_write(input_data: dict[str, Any], context: ToolContext) -> ToolResult:
    note = str(input_data.get("note", "")).strip()
    if not note:
        return ToolResult(ok=False, output="note is required")
    path = _memory_path(context)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"\n- {time.strftime('%Y-%m-%d %H:%M:%S')} {note}\n")
    return ToolResult(ok=True, output="Memory saved.")


def _memory_search(input_data: dict[str, Any], context: ToolContext) -> ToolResult:
    query = str(input_data.get("query", "")).strip().lower()
    path = _memory_path(context)
    if not path.exists():
        return ToolResult(ok=True, output="No memory yet.")
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if query in line.lower()]
    return ToolResult(ok=True, output="\n".join(lines) if lines else "(no matches)")


def _parse_skill_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    meta: dict[str, str] = {}
    for line in parts[1].splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip().strip('"')
    return meta, parts[2].strip()


def _discover_skills(context: ToolContext) -> list[dict[str, str]]:
    skills_dir = context.workspace / "skills"
    if not skills_dir.exists():
        return []
    skills: list[dict[str, str]] = []
    for path in sorted(skills_dir.rglob("SKILL.md")):
        text = path.read_text(encoding="utf-8", errors="replace")
        meta, body = _parse_skill_frontmatter(text)
        name = meta.get("name") or path.parent.name
        description = meta.get("description") or body.splitlines()[0].lstrip("# ") if body else ""
        skills.append({"name": name, "description": description, "path": str(path.relative_to(context.workspace))})
    return skills


def _skill_list(input_data: dict[str, Any], context: ToolContext) -> ToolResult:
    skills = _discover_skills(context)
    if not skills:
        return ToolResult(ok=True, output="No skills found. Add skills/<name>/SKILL.md in the workspace.", data=[])
    return ToolResult(ok=True, output="\n".join(f"- {s['name']}: {s['description']} ({s['path']})" for s in skills), data=skills)


def _skill_load(input_data: dict[str, Any], context: ToolContext) -> ToolResult:
    wanted = str(input_data.get("name", "")).strip()
    for skill in _discover_skills(context):
        if skill["name"] == wanted or Path(skill["path"]).parent.name == wanted:
            path = _safe_path(context.workspace, skill["path"])
            return ToolResult(ok=True, output=path.read_text(encoding="utf-8", errors="replace"))
    return ToolResult(ok=False, output=f"Skill not found: {wanted}")


def _plan_update(input_data: dict[str, Any], context: ToolContext) -> ToolResult:
    steps = input_data.get("steps", [])
    if not isinstance(steps, list):
        return ToolResult(ok=False, output="steps must be a list")
    normalized = []
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            return ToolResult(ok=False, output=f"steps[{index}] must be an object")
        text = str(step.get("step", "")).strip()
        status = str(step.get("status", "pending"))
        if not text or status not in {"pending", "in_progress", "completed"}:
            return ToolResult(ok=False, output=f"Invalid step at index {index}")
        normalized.append({"step": text, "status": status})
    plan_path = _state_dir(context, ".plans") / "current_plan.json"
    plan_path.write_text(json.dumps({"steps": normalized, "updatedAt": time.time()}, indent=2, ensure_ascii=False), encoding="utf-8")
    return ToolResult(ok=True, output="\n".join(f"[{s['status']}] {s['step']}" for s in normalized), data=normalized)


def _plan_get(input_data: dict[str, Any], context: ToolContext) -> ToolResult:
    plan_path = _state_dir(context, ".plans") / "current_plan.json"
    if not plan_path.exists():
        return ToolResult(ok=True, output="No active plan.")
    return ToolResult(ok=True, output=plan_path.read_text(encoding="utf-8"))


def _subagent(input_data: dict[str, Any], context: ToolContext) -> ToolResult:
    description = str(input_data.get("description", input_data.get("prompt", ""))).strip()
    agent_type = str(input_data.get("agent_type", "explore"))
    if not description:
        return ToolResult(ok=False, output="description is required")
    task_input = {
        "subject": f"Subagent/{agent_type}: {description[:80]}",
        "description": description,
        "owner": f"subagent:{agent_type}",
    }
    created = _task_create(task_input, context)
    return ToolResult(
        ok=created.ok,
        output=(
            "Subagent work item created. This MVP records delegated work as a persistent task; "
            "a future version can attach an isolated model loop/worktree.\n" + created.output
        ),
        data=created.data,
    )


def _compact(input_data: dict[str, Any], context: ToolContext) -> ToolResult:
    focus = str(input_data.get("focus", "")).strip()
    return ToolResult(ok=True, output=f"Manual compaction requested. Focus: {focus or '(none)'}")


def _tool_help(input_data: dict[str, Any], context: ToolContext) -> ToolResult:
    tools = create_tool_registry()
    return ToolResult(ok=True, output="\n".join(f"- {tool.name}: {tool.description}" for tool in tools))


def _tool(name: str, description: str, properties: dict[str, Any], required: list[str], handler: Callable[[dict[str, Any], ToolContext], ToolResult]) -> ToolDefinition:
    return ToolDefinition(name=name, description=description, input_schema={"type": "object", "properties": properties, "required": required}, handler=handler)


def create_tool_registry() -> list[ToolDefinition]:
    return [
        _tool("run_command", "Run a development shell command inside the workspace.", {"command": {"type": "string"}, "cwd": {"type": "string"}}, ["command"], _run_command),
        _tool("background_run", "Run a longer shell command in the background; check later with background_check.", {"command": {"type": "string"}, "cwd": {"type": "string"}}, ["command"], _background_run),
        _tool("background_check", "Check one or all background command results.", {"task_id": {"type": "string"}}, [], _background_check),
        _tool("read_file", "Read a UTF-8 text file from the workspace.", {"path": {"type": "string"}, "offset": {"type": "integer"}, "limit": {"type": "integer"}}, ["path"], _read_file),
        _tool("write_file", "Write a file in the workspace, creating parent directories as needed.", {"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"], _write_file),
        _tool("edit_file", "Replace the first exact text occurrence in a workspace file.", {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, ["path", "old_text", "new_text"], _edit_file),
        _tool("list_files", "List files under a workspace path.", {"path": {"type": "string"}, "max_files": {"type": "integer"}}, [], _list_files),
        _tool("workspace_tree", "Show a compact tree of the workspace.", {"path": {"type": "string"}, "max_depth": {"type": "integer"}}, [], _workspace_tree),
        _tool("grep_files", "Search workspace files with a regular expression.", {"pattern": {"type": "string"}, "path": {"type": "string"}, "ignore_case": {"type": "boolean"}}, ["pattern"], _grep_files),
        _tool("TodoWrite", "Update the current session todo list. Keep at most one item in_progress.", {"items": {"type": "array", "items": {"type": "object", "properties": {"content": {"type": "string"}, "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]}, "activeForm": {"type": "string"}}, "required": ["content", "status"]}}}, ["items"], _todo_write),
        _tool("task_create", "Create a persistent task record in .tasks.", {"subject": {"type": "string"}, "description": {"type": "string"}, "owner": {"type": "string"}, "blockedBy": {"type": "array", "items": {"type": "string"}}}, ["subject"], _task_create),
        _tool("task_list", "List persistent tasks, optionally filtered by status.", {"status": {"type": "string", "enum": ["pending", "in_progress", "completed", "blocked"]}}, [], _task_list),
        _tool("task_get", "Get one persistent task by id.", {"task_id": {"type": "string"}}, ["task_id"], _task_get),
        _tool("task_update", "Update a persistent task.", {"task_id": {"type": "string"}, "subject": {"type": "string"}, "description": {"type": "string"}, "status": {"type": "string", "enum": ["pending", "in_progress", "completed", "blocked"]}, "owner": {"type": "string"}, "blockedBy": {"type": "array", "items": {"type": "string"}}}, ["task_id"], _task_update),
        _tool("task_delete", "Delete a persistent task by id.", {"task_id": {"type": "string"}}, ["task_id"], _task_delete),
        _tool("memory_read", "Read durable project memory from .memory/MEMORY.md.", {}, [], _memory_read),
        _tool("memory_write", "Append a durable project memory note.", {"note": {"type": "string"}}, ["note"], _memory_write),
        _tool("memory_search", "Search durable project memory.", {"query": {"type": "string"}}, ["query"], _memory_search),
        _tool("skill_list", "List available skills from workspace/skills/*/SKILL.md.", {}, [], _skill_list),
        _tool("load_skill", "Load a skill by name from workspace/skills.", {"name": {"type": "string"}}, ["name"], _skill_load),
        _tool("plan_update", "Record or update the current multi-step plan.", {"steps": {"type": "array", "items": {"type": "object", "properties": {"step": {"type": "string"}, "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]}}, "required": ["step", "status"]}}}, ["steps"], _plan_update),
        _tool("plan_get", "Read the current recorded plan.", {}, [], _plan_get),
        _tool("subagent", "Delegate focused work as a persistent subagent task record.", {"description": {"type": "string"}, "agent_type": {"type": "string"}}, ["description"], _subagent),
        _tool("compact", "Request conversation compaction before continuing.", {"focus": {"type": "string"}}, [], _compact),
        _tool("tool_help", "List all available tools and what they do.", {}, [], _tool_help),
    ]


def shell_quote_command(command: str) -> list[str]:
    return shlex.split(command)
