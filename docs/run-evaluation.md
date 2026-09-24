# Coding Agent 运行评测

参考 [DeepResearch Agent Harness 的系统评测](https://github.com/SichengLong26/deepresearch_agent_harness/blob/main/evals/system/README.md) 将指标分为可从持久化轨迹确定性计算、需要额外埋点、需要外部标签三类。本项目不复用研究报告特有的引用和检索指标。

```bash
python scripts/evaluate_runs.py ../swebench-output-v4pro-shallow-20260916/runs
python scripts/evaluate_runs.py ../my-runs --output ../my-runs/evaluation.json
```

只读取已完成运行的 `result.json`、可选的 `trajectory.json` 和 `verification.json`，不调用模型，也不把 prompt、工具输出或 API Key 写进汇总。每个比例都输出 `numerator` 和 `denominator`；分母为零时 `value=null`，表示没有证据，而不是 0% 成功率。

| 指标 | 定义与限制 |
| --- | --- |
| Patch rate | 非空补丁运行数 / 总运行数；不等于问题解决率。 |
| Finish gate pass rate | 成功结束且至少一次 `finish` 验收通过 / 留有验收记录的运行数。 |
| First-pass finish gate rate | 首次 `finish` 就通过且成功结束 / 留有验收记录的运行数。 |
| Command verification pass rate | 成功结束、门禁通过、并有全部配置命令成功执行证据的运行数 / 配置了命令且留有验收记录的运行数。只检查退出码与超时；命令本身是否充分仍需人工审查。 |
| Recovery success rate | 出现 `recovery` 事件后最终成功结束的运行数 / 出现 `recovery` 事件的运行数；它不是因果提升估计。 |
| Tool error rate | 轨迹中 `ok=false` 的工具调用数 / 有布尔 `ok` 记录的调用数。 |
| 延迟与开销 | 运行时长、模型调用时长和 Token 的 P50/P95；另列通过 finish 门禁的 Token 分布和平均工具调用数。 |

`prefix_cache_hit_rate`、`checkpoint_integrity` 暂无完整埋点，明确标为 unavailable。语义正确率需要独立 Judge 或人工标签。SWE-bench `resolved` 仍仅由官方 harness 判定，原有 SWE-bench 流程和 `scripts/summarize_runs.py` 不变。跨模型比较应固定任务集、模型参数与预算，并分别报告样本数、成本及失败原因；这些指标不能单独证明 Agent 性能优于基线。
