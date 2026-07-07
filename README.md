# code-agent

A Claude Code inspired coding-agent playground built from the ideas in `learn-claude-code` and `MiniCode`.

## Stack

- Backend: FastAPI + Anthropic-compatible Messages API adapter.
- Frontend: Vue 3 + Vite + TypeScript.
- Workspace: files touched by the agent are constrained to `workspace/` by default.

## Agent Kernel Features

The backend is more than a chat proxy. It has a real agent loop and a growing tool system:

- Tool-result feedback loop with Anthropic `tool_use` / `tool_result` history.
- Workspace file tools: `read_file`, `write_file`, `edit_file`, `list_files`, `workspace_tree`, `grep_files`.
- Command tools: `run_command`, `background_run`, `background_check`.
- Session todos: `TodoWrite`.
- Persistent tasks in `workspace/.tasks`: `task_create`, `task_list`, `task_get`, `task_update`, `task_delete`.
- Durable memory in `workspace/.memory/MEMORY.md`: `memory_read`, `memory_write`, `memory_search`.
- Skill loading from `workspace/skills/*/SKILL.md`: `skill_list`, `load_skill`.
- Plan tracking: `plan_update`, `plan_get`.
- Subagent placeholder: `subagent` records delegated work as a persistent task.
- Context compaction trigger: `compact`.
- Tool catalog: `tool_help`.

## Setup

Backend:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
uvicorn app.main:app --reload --host 127.0.0.1 --port 8002
```

Frontend:

```powershell
cd frontend
npm install
npm run dev -- --port 5174
```

Open http://127.0.0.1:5174.

Without `ANTHROPIC_API_KEY` or `ANTHROPIC_BASE_URL`, the backend uses a mock model so the UI can still run. Fill `backend/.env` and restart the API for real tool-using model behavior.
