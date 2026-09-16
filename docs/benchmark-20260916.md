# SWE-bench diagnostic run — 2026-09-16

Instance: `sqlfluff__sqlfluff-1625`. Provider/model: SiliconFlow,
`deepseek-ai/DeepSeek-V4-Pro`, with thinking enabled and `reasoning_effort=xhigh`.

## Corrections before the run

- Preserve tool results while the conversation remains within its context budget.
  Previously older observations were cut to 500 characters even below the limit.
- Preserve and replay the provider's `reasoning_content` across tool calls.
- Disable the exploration/edit gate by default. Earlier runs showed that this
  gate encouraged scratch files and comment edits rather than a valid fix.
- Bound the run by 80 steps, 2,700 seconds, 300 seconds per model attempt and
  2,000,000 cumulative reported input/output tokens. The token limit is checked
  between steps, so the final request can exceed it. Per-response output limit:
  16,384 tokens. Context soft limit: 800,000 characters.

## Invalidated attempt

The first attempt exposed future commits through the full Git checkout. The
agent inspected the official fix using `git show`. The attempt was stopped and
must not be counted in benchmark scores. This also invalidates claims that the
older checkout arrangement was suitable for uncontaminated SWE-bench evaluation.

Workspace preparation now archives previous attempts and fetches only the base
commit with `--depth=1 --no-tags`. A regression test confirms that a future commit
in the repository cache cannot be accessed from the target checkout. This is Git
history isolation, not a network or host security sandbox.

## Fresh shallow-checkout attempt

- 30 model responses completed; request at step 31 failed.
- Reported total tokens: 734,153.
- Runtime: 293.27 seconds.
- Final patch: empty.
- Termination: `model_error`, HTTP 402 / provider code 30001,
  `Sorry, your account balance is insufficient`.
- No step, time or total-token limit was reached.
- No official SWE-bench evaluator was run; no resolved result is claimed.

The final failure was provider balance exhaustion. Earlier poor performance
cannot be attributed solely to the model because the previous runtime discarded
context and the checkout exposed future solutions. A funded continuation is
needed before drawing a stronger conclusion about task-solving performance.

Local regression suite: 62 tests run, 1 Docker-only skip, 82% coverage. The
subsequent shallow-checkout regression suite also passed. Cross-platform CI for
commit `3662528` passed, including Docker smoke and frontend build.
