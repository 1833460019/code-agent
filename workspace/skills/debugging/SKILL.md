---
name: debugging
description: Use for diagnosing failures, reading logs, reproducing bugs, and iterating on fixes.
---

# Debugging Skill

Use this workflow when something fails or behaves unexpectedly.

1. Reproduce or inspect the failure with `run_command`, `read_file`, or `grep_files`.
2. Separate symptoms from suspected causes.
3. Make the smallest fix that addresses the observed failure.
4. Re-run the failing command or a targeted check.
5. Capture durable lessons with `memory_write` when useful.
