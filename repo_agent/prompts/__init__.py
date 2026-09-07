from pathlib import Path


def load_coding_agent_prompt() -> str:
    return (Path(__file__).with_name("coding_agent.txt")).read_text(encoding="utf-8").strip()


__all__ = ["load_coding_agent_prompt"]
