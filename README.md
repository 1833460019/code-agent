# LCC Repo Agent

基于 learn-claude-code 思想的轻量 Coding Agent Runtime。CLI、Web、子 Agent、团队成员和 SWE-bench Adapter 共用 `RepoAgent → AgentLoop → Tool → Environment`，不依赖 LangChain / LangGraph / CrewAI。

[![CI](https://github.com/1833460019/code-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/1833460019/code-agent/actions/workflows/ci.yml)

提供两个配置：

- `full`：覆盖本地 LCC Web 教程的 20 章能力，包括权限、Hooks、任务图、真实子 Agent / 团队、Skills、记忆、上下文压缩、后台任务、Cron、Worktree 和 MCP。
- `baseline`：关闭扩展服务，只保留仓库编码工具、恢复机制、上下文预算和轨迹记录，方便做基线及消融实验。SWE-bench 入口固定使用此配置。

逐章实现位置、测试证据及限制见 [20 章功能验收表](docs/lcc-20-chapters.md)。这里的“覆盖”指核心机制实现并有离线测试，不代表完全复刻商业 Coding Agent 或已经取得 SWE-bench 成绩。

## 安装与运行

需要 Python 3.11+、Git。Web 前端另需 Node.js/npm。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
$env:ANTHROPIC_API_KEY = "你的密钥"
python scripts/run_agent.py --workspace C:/repos/example --task "修复失败的测试，验证后结束" --model YOUR_MODEL_ID --profile full
```

`--workspace` 应为单独的 Git checkout，不能是 Agent 项目自身或包含它的父目录。Linux/macOS 激活虚拟环境用 `source .venv/bin/activate`。支持 Anthropic、OpenAI-compatible 与 SiliconFlow；SiliconFlow 使用 `--provider siliconflow --model MODEL_ID` 以及本地 `SILICONFLOW_API_KEY`，默认 Base URL 为 `https://api.siliconflow.cn/v1`。也可通过 `Model` 接口扩展其他供应商。

可要求 Agent 必须产生真实补丁且验证命令通过后才能结束：

```bash
python scripts/run_agent.py --workspace /path/to/repo --task "Fix the bug" --model MODEL_ID \
  --require-patch --verify-command "python -m unittest" --verify-command "git diff --check"
```

每次 `finish` 尝试都会写入 `verification.json`，记录 diff 哈希、命令退出码和有界输出；验证失败时 Agent 会继续修复，而不是把口头完成当作成功。

运行过程在每个工具边界写入 SQLite checkpoint，并用可续期 lease 防止同一个实例被重复执行；进程中断后可用相同的 `--instance-id` 加 `--resume` 从下一步继续。

SWE-bench Lite dev 可批量抓取并断点续跑（先用 `--limit 1` 控制费用）：

```bash
python scripts/fetch_swebench_tasks.py --split dev --output swebench-lite-dev.json
python scripts/run_swebench_batch.py --task-file swebench-lite-dev.json \
  --workspace-root ../swebench-workspaces --output-dir ../swebench-results \
  --provider siliconflow --model deepseek-ai/DeepSeek-V4-Flash --limit 1
```

批处理为每个实例准备独立、精确定位到 `base_commit` 的干净 checkout；`manifest.json` 原子记录状态，重启后自动跳过已完成任务，`predictions.jsonl` 可直接交给官方 SWE-bench harness。

CLI 默认 `full + ask`：读取工具直接执行，变更、Shell 和外部工具在终端逐次审批。非交互输入无法审批时默认拒绝。只有你明确信任目标仓库与命令时才使用 `--permission-mode trusted`。

常用选项：

| 选项 | 用途 |
| --- | --- |
| `--profile baseline` | 关闭可选扩展，保持单 Agent 基线 |
| `--permission-mode ask/readonly/trusted` | 审批、只读或可信执行 |
| `--permission-rules examples/permissions.json` | 按工具名及命令 glob 配置 allow/deny/ask，deny 优先 |
| `--max-steps 50 --max-runtime 1800` | 单次运行的步数与总时间预算 |
| `--environment docker --docker-image IMAGE` | 在禁网、非 root、资源受限容器中执行目标命令 |
| `--model-timeout 120 --command-timeout 120` | 单次模型及命令超时 |
| `--max-workers 3 --child-max-steps 20` | 后台/团队并发与子循环预算 |
| `--runs-dir PATH --state-dir PATH` | 轨迹与持久化服务目录，必须在目标 workspace 外 |
| `--skill-root PATH` | 添加 Skills 根目录，可多次传入 |
| `--auto-memory` | 结束后额外调用模型提取记忆，默认关闭 |
| `--mcp-config FILE` | 读取由操作者配置的 stdio MCP servers |
| `--fallback-model MODEL_ID` | 模型请求失败时使用备用模型 |
| `--serve` | 初始任务结束后继续消费 Cron 队列 |

## 全功能使用

可直接给 Agent 自然语言任务，例如：

> 先用 TodoWrite 规划；读取相关 Skills；创建带依赖的任务，让队友在独立 Worktree 修改代码并先提交计划。批准计划后收集测试结果，审查 worktree_diff，再合并补丁并结束。

子 Agent 不再是一次模型调用的占位符：它运行同一循环，拥有独立上下文和预算。`explore` 子 Agent 只读；`code` 子 Agent 与所有队友在独立 Git Worktree 中工作，结果需要主 Agent 显式 `worktree_merge`。已有工作区的未提交修改不会自动复制到新 Worktree；新树起点是 HEAD。冲突时拒绝合并，不强制覆盖。

Skills 从 `<workspace>/skills/*/SKILL.md` 与额外 roots 发现，系统提示只注入名称/描述，`load_skill` 才返回正文。Memory 使用有类型的本地 JSON + 索引、词项检索和精确去重，不是向量 RAG。

Cron 使用五字段 UTC 表达式，持久化“定义 → 队列 → 执行结果”。例如 `0 1 * * 1-5` 是北京时间工作日 09:00。必须保持 CLI `--serve` 或 Web 调度消费者运行；它不是系统级定时服务，停机期间不补发错过的分钟。`max_runs` 限制触发次数，重复分钟去重；中断中的任务标为失败，不自动重放可能产生副作用的操作。

MCP 配置示例：

```json
{
  "mcpServers": {
    "example": {
      "command": "ABSOLUTE_PATH_TO_SERVER_EXECUTABLE",
      "args": ["SERVER_ARGUMENT"],
      "env": {},
      "timeout": 30
    }
  }
}
```

仅操作者选择服务器进程；模型不能自行指定 MCP 启动命令。实现了 stdio JSON-RPC 初始化、分页工具发现、Schema 校验、工具调用、超时取消与关闭；不包含 HTTP transport、OAuth、资源或提示模板 API。

Hooks 为 Python 扩展点，示例见 [examples/hooks.py](examples/hooks.py)。支持 `user_prompt / before_model / after_model / before_tool / after_tool / error / stop`，同步/异步回调、按注册顺序运行以及 `False` 拒绝操作。Hooks 是可信宿主代码，不是可由模型临时安装的脚本。

## Web

后端是统一内核的会话/SSE 适配层，原先的重复循环与占位工具已移除。

```powershell
pip install -r backend/requirements.txt
$env:AGENT_WORKSPACE = "C:/repos/example"
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 18002
```

另一个终端：

```powershell
cd frontend
npm install
npm run dev -- --host 127.0.0.1 --port 5174
```

打开 `http://127.0.0.1:5174`。聊天中会显示工具审批卡片，点击允许一次或拒绝；流断开时会取消运行并保留轨迹。不同 Web 会话的工作区写入按回合串行化，避免直接并发修改主工作区。

Web 环境变量除模型配置外还包括 `PERMISSION_MODE`（默认 ask）、`AGENT_STATE_DIR`、`MCP_CONFIG_FILE`、`SKILL_ROOTS`（JSON 字符串数组）、`AUTO_MEMORY` 和 `CRON_ENABLED`（默认 false）。定时任务结果可从 `GET /api/schedules` 和轨迹查看；无人值守任务没有交互审批渠道，ask 策略会拒绝需要批准的工具，不会自动提权。服务仅适合可信本地使用，尚无登录鉴权，不应直接公开到公网。

无 API key 时 Web 使用 MockModel，仅用于检查交互与连通性，不代表真实模型能力。非 Git 的 Web workspace 能读写，但不能使用 Worktree/团队或得到有效 Git patch。

## SWE-bench 基线

准备 JSON task 与 `HEAD == base_commit` 的干净 checkout：

```json
{
  "instance_id": "project__project-123",
  "repo": "owner/project",
  "base_commit": "0123456789abcdef0123456789abcdef01234567",
  "problem_statement": "..."
}
```

```powershell
python scripts/run_swebench.py --task-file tasks.json --instance-id project__project-123 --workspace C:/repos/task-123 --model YOUR_MODEL_ID
```

输出 `prediction.json`：`instance_id / model_name_or_path / model_patch`，可作为官方 evaluator 的预测输入。Adapter 只验证 checkout、调用同一 Agent 并导出预测；不会自动下载数据集、批量准备 23 个环境、安装目标依赖、启动官方容器评测或计算 resolved rate。

## 轨迹与结果

每次运行创建唯一目录，不覆盖既有结果：

- `trajectory.json`：实际每次模型请求、动态 system prompt、assistant/tool 全量记录、压缩事件、错误恢复及计时。
- `run.log`：增量 JSONL 事件。
- `result.json`：状态、结束原因、步数、工具/Token 统计、耗时、补丁、子 Agent 结果及聚合 Token 数。
- `patch.diff`：包含 tracked / untracked / binary / staged 变更，并相对于环境创建时的固定基准提交生成。
- `outputs/*.txt`：超长工具原始输出，可由 `artifact_read` 分段读取。
- `children/*`：子循环独立轨迹和补丁。

`success` 只表示接受了 `finish`，不等于测试通过或 Issue 已解决。`terminated` 包含步数/时间预算耗尽、取消、未调用 finish 的自然停止等。最终修复是否成功必须由测试与官方 evaluator 判定。SIGKILL/断电无法保证最后补丁已写入，但此前增量日志保留。

## 安全边界与验证

显式文件路径及命令 cwd 有 workspace 越界检查，Shell 默认过滤 API 凭据类环境变量，权限规则在执行前校验。`LocalEnvironment` 不是操作系统沙箱；`DockerEnvironment` 才会将目标命令放入默认禁网、只读 root、cap-drop、非 root、CPU/内存/PID 受限的容器。目标 workspace 是唯一可写 bind mount，镜像必须由操作者信任。MCP server 仍运行在宿主进程中，不在目标 DockerEnvironment 内。

```powershell
pip install -r backend/requirements.txt
python -m unittest discover -s tests -v
cd frontend
npm run build
```

测试使用确定性的观察驱动模型，但真实执行本地 Git、文件操作、Shell、后台子进程、Worktree、团队子循环和 MCP stdio 子进程；另覆盖 Web 审批、断开取消及多轮历史。没有消耗真实模型 API，也没有声称任何 SWE-bench resolved rate。建议下一阶段固定真实模型/预算，运行官方基线并基于失败轨迹做消融实验。
