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
  --provider siliconflow --model deepseek-ai/DeepSeek-V4-Flash --limit 1 \
  --max-exploration-steps 20
```

支持硅基流动推理模式参数，例如 V4-Pro Think Max 使用 `--enable-thinking --reasoning-effort xhigh`；具体支持范围和计费以供应商当前文档为准。

批处理为每个实例准备独立、精确定位到 `base_commit` 的干净 checkout；`manifest.json` 原子记录状态，重启后自动跳过已完成任务，`predictions.jsonl` 可直接交给官方 SWE-bench harness。
SWE-bench 默认不强制限制探索阶段；`--max-exploration-steps` 是可选实验开关，可能诱导模型提前做无效修改。批处理通过 `--max-total-tokens`（默认 200 万累计输入输出 Token）、`--max-runtime` 和 `--max-steps` 控制总预算；Token 在模型调用边界检查，最后一次请求可能超过门限。`--model-timeout` 默认 300 秒，`--context-soft-limit-chars` 默认 80 万字符。预算内保留完整工具结果，并在工具调用间回传供应商的推理字段。

CLI 默认 `full + ask`：读取工具直接执行，变更、Shell 和外部工具在终端逐次审批。非交互输入无法审批时默认拒绝。只有你明确信任目标仓库与命令时才使用 `--permission-mode trusted`。

Web 后端只从明确的 `backend/.env` 读取本地默认值，且启动时传入的系统/容器环境变量优先，便于安全地覆盖 workspace、模型和运行预算。

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
npm run dev -- --host 127.0.0.1 --port 5173
```

打开 `http://127.0.0.1:5173`。开发服务器默认把 `/api` 代理到 `http://127.0.0.1:18002`；可用前端环境变量 `AGENT_API_TARGET` 改代理目标，或在前后端不同域部署时通过 `VITE_API_BASE` 设置公开后端地址。聊天中会显示工具审批卡片，点击允许一次或拒绝；流断开时会取消运行并保留轨迹。不同 Web 会话的工作区写入按回合串行化，避免直接并发修改主工作区。

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

单任务输出 `prediction.json`；批量模式会下载任务清单、准备 23 个隔离 checkout，并增量生成官方格式 `predictions.jsonl`。目标依赖安装、官方容器 evaluator 和 resolved rate 仍由 SWE-bench harness 负责，项目不会把“生成了 patch”误报为 resolved。

运行结果可按失败类型、patch rate、token 和时延汇总；两组同实例实验会给出固定随机种子的配对 bootstrap 95% CI：

```bash
python scripts/summarize_runs.py ../baseline-results --compare ../full-results
```

另可对持久化运行轨迹生成完成门禁、首次通过、命令验证、恢复、工具错误和 P50/P95 时延等可复核指标；定义及分母见 [运行评测说明](docs/run-evaluation.md)：

```bash
python scripts/evaluate_runs.py ../baseline-results
```

`configs/ablation/baseline.json` 与 `configs/ablation/full.json` 固定两套实验口径。

Web 控制台左侧的 **Runs & evidence** 可打开历史运行，查看 result、逐步 trajectory、finish 验证证据和最终 patch；对应只读接口为 `GET /api/runs` 与 `GET /api/runs/{run_id}`。

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

离线测试使用确定性的观察驱动模型，但真实执行本地 Git、文件操作、Shell、后台子进程、Worktree、团队子循环和 MCP stdio 子进程；另覆盖 Web 审批、断开取消及多轮历史。下方记录一次使用真实模型的本机实验，不能替代官方 SWE-bench 评测。

### 2026-09-24 本机实测

环境：Windows、Python 3.13.5、Node.js 24.14.0；真实模型为 SiliconFlow 的 `deepseek-ai/DeepSeek-V4-Pro`。API Key 仅放在 Git 忽略的本地 `backend/.env`，未纳入仓库。以下模型调用使用付费 API；运行记录位于项目目录外，未上传。

| 范围 | 方法与结果 | 结论 |
| --- | --- | --- |
| 离线回归 | `python -m unittest discover -s tests -p 'test_*.py'`：65 项，64 通过、1 跳过；`coverage` 总行覆盖率 83%；`ruff check` 通过。 | 覆盖 20 章核心机制的确定性测试；本机没有 Docker，Docker 专项未执行。 |
| 前端构建 | `frontend/` 下 `npm run build` 成功。 | TypeScript 检查与生产打包通过。 |
| 模型连通 | 用限制为 128 输出 Token 的真实请求得到预期 `OK`，供应商返回 90 输入、27 输出 Token。 | 密钥、模型 ID 与 SiliconFlow 兼容接口可用；不等于 Agent 任务成功。 |
| Web 与浏览器 | 独立临时 Git 仓库中，通过 `/api/chat`、`/api/chat/stream` 和浏览器页面完成 3 轮真实对话；SSE 返回 `session/user/assistant/tool_start/tool_result/done`，历史会话、工具轨迹、运行证据与验证信息可见。3 轮均调用 `finish`。 | 前后端链路可用，但第一轮精确任务**未通过**：要求 `hello.txt` 以 LF 结尾，实际文件只有 `HELLO_AGENT` 的 11 字节、末尾无 LF；Agent 仍口头声称已验证。Web 默认 finish 门禁没有测试命令，不能把 3/3 finish 当作任务正确率。 |
| 强制验证与恢复 | 另一独立仓库提供 `test_exact.py`，断言文件字节等于 `b"HELLO_AGENT\n"`，CLI 设置 `--require-patch --verify-command "python -m unittest -q"`。首次运行在 8 步上限终止；从 checkpoint 恢复后第 11 步修正文件，1 项测试通过，finish 门禁记录命令退出码 0 并接受。 | 验证门禁能阻止未经测试的口头完成；该临时仓库缺少 `.gitignore`，运行生成的 `__pycache__` 也进入演示补丁，补丁本身不应直接交付。 |
| SWE-bench Lite dev 单例 | `sqlfluff__sqlfluff-2419`，baseline，V4-Pro，25 步/900 秒/50 万累计 Token 上限。实际 25 步、212,735 Token、240.68 秒；修改 `src/sqlfluff/rules/L060.py`，`git diff --check` 通过，独立重跑 L060 现有测试为 3 通过、725 未选。 | 状态为 `terminated/max_steps`，没有 finish，也没有官方容器 evaluator；**不计为 resolved**。单例只用于诊断，不能推出 23 题修复率。 |

两处统计陷阱：同一 Web 工作区第一轮留下未提交文件，后续只读回合的 `model_patch` 仍是同一份 diff，因此这些回合的非空 patch 不能算新修复；finish 门禁通过也只说明通过了当时配置的检查，不说明需求语义正确。真实运行可用 `python scripts/evaluate_runs.py <runs目录>` 重算过程指标，SWE-bench `resolved` 仍须由官方 harness 给出。下一步应在干净隔离仓库中为 Web 任务加入命令级验收，再固定预算扩大样本；本机缺少 Docker，尚未完成官方判题。
