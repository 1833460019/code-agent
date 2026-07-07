---
name: code-change
description: Use for implementing focused code changes with inspection, edit, and verification steps.
---

# Code Change Skill

Use this workflow when the user asks to add, modify, or refactor code.

1. Inspect relevant files before editing.
2. Record a short plan with `plan_update` for multi-step changes.
3. Use exact, scoped edits. Avoid unrelated rewrites.
4. Run the narrowest useful verification command.
5. Summarize changed files and verification results.
