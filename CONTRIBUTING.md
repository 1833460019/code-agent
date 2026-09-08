# Contributing

Use Python 3.11 or newer and Node.js 22. Keep changes focused and preserve the shared
`RepoAgent` execution path used by CLI, Web, child agents, and benchmark adapters.

Before opening a pull request:

```powershell
python -m pip install -e ".[dev]" -r backend/requirements.txt
python -m ruff check .
python -m coverage run -m unittest discover -s tests -q
python -m coverage report --fail-under=70
cd frontend
npm ci
npm run build
```

Tests that call a model must use a deterministic test model by default. Put paid or
credentialed evaluation behind an explicit command and never commit `.env`, API keys,
run artifacts, target repositories, or benchmark caches.

When adding a runtime capability, document its failure behavior and security boundary.
Do not call local process execution a sandbox.
