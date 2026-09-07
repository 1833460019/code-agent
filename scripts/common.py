from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNS_DIR = PROJECT_ROOT / "runs"


def ensure_external_workspace(workspace: str | Path) -> None:
    """Keep CLI runs from targeting this agent's own source checkout."""
    target = Path(workspace).expanduser().resolve()
    if not (target.is_relative_to(PROJECT_ROOT) or PROJECT_ROOT.is_relative_to(target)):
        return
    raise ValueError(
        f"Refusing to use the agent project as a target workspace: {target}. "
        "Use a separate repository checkout."
    )
