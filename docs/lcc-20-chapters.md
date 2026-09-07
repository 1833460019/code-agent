# LCC 20 章功能验收

对照依据：本项目旁 `learn-claude-code/web/src/data/scenarios/s01.json` 至 `s20.json` 的 20 章顺序，而不是其 `agents/` 目录中旧版 12 个教学脚本。实现不是逐文件复制，而是将对应机制组合到同一个运行时。

| 章 | 功能 | 实现入口 | 验证证据 |
| --- | --- | --- | --- |
| 01 | Agent Loop | `agent/loop.py` | `test_e2e`：真实仓库先失败、修改、测试、diff、finish |
| 02 | Tool Use | `tools/`、`runtime.py` | 文件/Shell/搜索/finish；JSON Schema 错误作为 observation |
| 03 | Permission | `permissions.py` | deny 优先、ask 回调、Web 允许/拒绝、后台不可绕过 Shell deny |
| 04 | Hooks | `hooks.py` | 生命周期顺序、异步/同步回调、工具 veto |
| 05 | TodoWrite | `tasks.py:TodoList` | 状态校验、唯一 in_progress、动态提示与提醒、Web Todo 事件 |
| 06 | Subagent | `runtime.py:_child/subagent` | 独立上下文真实循环、只读探索、单独轨迹、不递归派生 |
| 07 | Skills | `knowledge.py:SkillCatalog` | 元数据发现、正文按需加载、越界和重复名称校验 |
| 08 | Context Compact | `context/manager.py` | 旧文本微压缩、LLM 摘要保留到下一轮、完整调用对、预算截断 |
| 09 | Memory | `knowledge.py:MemoryStore` | 类型化存储/索引、检索、精确去重、可选模型提取与 Token 计量 |
| 10 | System Prompt | `prompts/builder.py` | 稳定段缓存、身份/环境/权限、动态 Skills/Memory/Todo/任务图 |
| 11 | Error Recovery | `models/recovery.py` | 暂时失败重试、上下文错误压缩、输出截断扩容、fallback、超时 |
| 12 | Task System | `tasks.py:TaskStore` | JSON 持久化、依赖 DAG、拒绝循环、文件锁原子 claim/租约/所有权 |
| 13 | Background Tasks | `background.py` | 真子进程、主循环异步通知、wait/check/cancel、取消后无遗留写入 |
| 14 | Cron Scheduler | `scheduler.py` | 五字段 UTC、生产/消费分离、持久队列、去重、次数限制、失败恢复 |
| 15 | Agent Teams | `teams.py:TeamManager` | 队友独立循环/Worktree、消息盒、状态、等待及结果收集 |
| 16 | Team Protocols | `teams.py:Protocols` | 请求 ID/收件人匹配、计划审批前禁止修改、shutdown 握手 |
| 17 | Autonomous Agents | `teams.py:_worker` | 空闲扫描、原子领取依赖就绪任务、连续执行、完成后回到空闲 |
| 18 | Worktree Isolation | `worktrees.py` | 建树、任务 ID 元数据、隔离写入、diff、冲突检查合并、keep/安全删除 |
| 19 | MCP Tools | `mcp.py` | 真 stdio server initialize、分页发现、schema、call、超时/关闭 |
| 20 | Comprehensive Agent | `runtime.py`、`agent/agent.py` | full 组合测试；CLI/Web/SWE-bench/子循环共享入口 |

所有实现路径均相对于 `repo_agent/`。测试文件在 `tests/`，可执行 `python -m unittest discover -s tests -v` 重现。

## 完成的实现边界

- 团队与自主工作者是进程内 asyncio 任务，运行期间保持独立上下文；邮件/任务/结果落盘，但不是跨主机分布式系统，重启不会自动恢复原模型调用栈。
- 自动队友最多执行 20 个任务后退出，另受步数/总时间/并发预算约束。空闲超时默认 10 秒，可在 `RepoAgentConfig` 调整。任务 completed 表示子循环 finish，不表示测试通过。
- 计划审批是执行闸门，不只是提示词。Shell 和写文件在批准前会被拒绝；系统权限策略依旧独立生效。
- Worktree 是 Git 隔离，不是 OS 沙箱；保留有修改的树，拒绝强删。新树以 HEAD 为基准，不复制主树未提交修改；跨队友补丁需主 Agent 协调审查/合并。
- Cron 进程必须存活；无离线补跑、系统服务安装或跨时区 DST 规则。非 durable job 仅在当前进程生命周期有效；中断正在运行的 durable job 不自动重放。
- MCP 仅覆盖 stdio/tools capability，非完整全协议 SDK。服务配置由宿主传入，外部工具默认也要权限检查。
- Skills 支持常见简单 frontmatter 的 name/description，不是完整 YAML 引擎。Memory 使用词项检索及精确内容去重，未实现向量检索或语义级冲突消解。
- 上下文预算为字符近似，不是厂商精确 Token 计数；LLM 摘要调用失败会退回本地预算整理。完整原始轨迹与模型可见截断输出分开存储。
- Web 提供会话、SSE、Todo 和审批；没有为每个高级服务单独设计仪表板，仍可通过聊天工具调用与日志使用。Web Cron 需 `CRON_ENABLED=true`，无人值守审批失败关闭。
- Hooks 由 Python 嵌入方注册，未提供任意 Shell Hook 插件安装。未添加 LangGraph / LangChain / RAG / 自动 PR 等不在本次核心实现范围内的组件。

## 简历陈述建议

可以据实描述：实现可组合的 Coding Agent Runtime，覆盖权限与 Hooks、上下文压缩、持久任务 DAG、独立子 Agent/团队、Worktree 隔离及 MCP 接入；统一 CLI/Web/评测入口，并通过真实本地进程与 Git 的离线集成测试。

不要写“达到某个 SWE-bench 修复率”“完整商业级沙箱”“分布式生产多 Agent 平台”。这些需要真实评测或额外工程证据。本次交付未进行付费真实模型评测、官方容器验收或 23-task 批量 resolved-rate 实验。
