"""FastAPI backend for code-agent."""
import sys
from pathlib import Path

# Support the existing `cd backend; uvicorn app.main:app` launch convention.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
