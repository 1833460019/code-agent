from __future__ import annotations

import os
import re
import signal
import subprocess
import threading
import time
from pathlib import Path

from ..schemas import CommandResult
from .base import Environment, EnvironmentError


class LocalEnvironment(Environment):
    """Run tools in one repository checkout.

    File operations and explicit command working directories are containment checked.
    A local subprocess is not an OS security boundary; untrusted benchmark code should
    eventually use a DockerEnvironment implementing the same interface.
    """

    def __init__(self, workspace: str | Path, *, command_timeout: float = 120.0):
        root = Path(workspace).expanduser().resolve()
        if not root.is_dir():
            raise EnvironmentError(f"Workspace does not exist or is not a directory: {root}")
        self._workspace = root
        self.command_timeout = command_timeout
        self._processes: set[subprocess.Popen] = set()
        self._lock = threading.Lock()
        self._cancelled = False
        head = self.git("rev-parse", "--verify", "HEAD")
        self.base_commit = head.stdout.strip() if head.returncode == 0 else None

    def git(self, *args: str) -> subprocess.CompletedProcess:
        result = subprocess.run(["git", "-c", "core.quotepath=false", *args], cwd=self.workspace,
                                capture_output=True, timeout=30, env=process_environment(), input=b"")
        return subprocess.CompletedProcess(result.args, result.returncode,
                                           result.stdout.decode("utf-8"), result.stderr.decode("utf-8", errors="replace"))

    @property
    def workspace(self) -> Path:
        return self._workspace

    def resolve_path(self, path: str | Path) -> Path:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.workspace / candidate
        candidate = candidate.resolve(strict=False)
        try:
            candidate.relative_to(self.workspace)
        except ValueError as exc:
            raise EnvironmentError(f"Path escapes workspace: {path}") from exc
        return candidate

    def execute(
        self,
        command: str,
        *,
        cwd: str | Path = ".",
        timeout: float | None = None,
    ) -> CommandResult:
        if not command.strip():
            raise EnvironmentError("Command must not be empty")
        command_cwd = self.resolve_path(cwd)
        if not command_cwd.is_dir():
            raise EnvironmentError(f"Command cwd is not a directory: {cwd}")
        limit = self.command_timeout if timeout is None else min(timeout, self.command_timeout)
        if limit <= 0:
            raise EnvironmentError("timeout must be positive")
        started = time.perf_counter()
        with self._lock:
            if self._cancelled:
                raise EnvironmentError("Environment execution cancelled")
            process = subprocess.Popen(
                command,
                shell=True,
                cwd=command_cwd,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                encoding="utf-8",
                errors="replace",
                env={**process_environment(), "REPO_AGENT_WORKSPACE": str(self.workspace)},
                start_new_session=os.name != "nt",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            self._processes.add(process)
        try:
            stdout, stderr = process.communicate(timeout=limit)
            return CommandResult(
                command=command,
                stdout=stdout,
                stderr=stderr,
                exit_code=process.returncode,
                duration_seconds=time.perf_counter() - started,
            )
        except subprocess.TimeoutExpired:
            stop_process(process)
            stdout, stderr = process.communicate()
            return CommandResult(
                command=command,
                stdout=stdout,
                stderr=stderr,
                exit_code=124,
                duration_seconds=time.perf_counter() - started,
                timed_out=True,
            )
        finally:
            with self._lock:
                self._processes.discard(process)

    def cancel_all(self) -> None:
        with self._lock:
            self._cancelled = True
            for process in tuple(self._processes):
                stop_process(process)

    def reset_cancellation(self) -> None:
        with self._lock:
            if self._processes:
                raise EnvironmentError("Previous commands are still running")
            self._cancelled = False

    def clone_for_workspace(self, workspace: str | Path) -> "LocalEnvironment":
        return LocalEnvironment(workspace, command_timeout=self.command_timeout)

    def read_file(self, path: str | Path) -> str:
        target = self.resolve_path(path)
        if not target.is_file():
            raise EnvironmentError(f"File does not exist: {path}")
        return target.read_text(encoding="utf-8", errors="replace")

    def write_file(self, path: str | Path, content: str) -> None:
        target = self.resolve_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="")
        self._invalidate_python_bytecode(target)

    def edit_file(self, path: str | Path, old_text: str, new_text: str) -> None:
        if not old_text:
            raise EnvironmentError("old_text must not be empty")
        target = self.resolve_path(path)
        with target.open(encoding="utf-8", newline="") as handle:
            content = handle.read()
        if "\r\n" in content:
            old_text = old_text.replace("\r\n", "\n").replace("\n", "\r\n")
            new_text = new_text.replace("\r\n", "\n").replace("\n", "\r\n")
        occurrences = content.count(old_text)
        if occurrences != 1:
            raise EnvironmentError(
                f"Expected exactly one occurrence in {path}, found {occurrences}"
            )
        target.write_text(content.replace(old_text, new_text, 1), encoding="utf-8", newline="")
        self._invalidate_python_bytecode(target)

    def _invalidate_python_bytecode(self, target: Path) -> None:
        """Prevent same-second, same-size Python edits from reusing stale code."""
        if target.suffix.lower() != ".py":
            return
        candidates = [target.with_suffix(".pyc")]
        cache_dir = target.parent / "__pycache__"
        if cache_dir.is_dir():
            candidates.extend(cache_dir.glob(f"{target.stem}.*.pyc"))
        for candidate in candidates:
            try:
                candidate.resolve(strict=False).relative_to(self.workspace)
                candidate.unlink(missing_ok=True)
            except (OSError, ValueError):
                # Cache cleanup must never turn a successful source edit into a failure.
                continue

    def get_diff(self) -> str:
        check = self.git("rev-parse", "--is-inside-work-tree")
        if check.returncode != 0 or check.stdout.strip() != "true":
            raise EnvironmentError("Workspace must be a Git working tree to produce a patch")

        base = self.base_commit
        if base is None:
            base = self.git("hash-object", "-t", "tree", "--stdin").stdout.strip()
        tracked = self.git("diff", "--binary", "--no-ext-diff", "--no-textconv", base, "--", ".")
        if tracked.returncode != 0:
            raise EnvironmentError(f"git diff failed: {tracked.stderr}")

        untracked_result = self.git("ls-files", "--others", "--exclude-standard", "-z")
        if untracked_result.returncode != 0:
            raise EnvironmentError(f"Unable to list untracked files: {untracked_result.stderr}")

        parts = [tracked.stdout] if tracked.stdout else []
        for relative_path in untracked_result.stdout.split("\0"):
            if not relative_path.strip():
                continue
            self.resolve_path(relative_path)
            added = self.git("diff", "--no-index", "--binary", "--no-ext-diff", "--no-textconv", "--", "/dev/null", relative_path)
            if added.returncode not in (0, 1):
                raise EnvironmentError(f"Unable to diff untracked file {relative_path}: {added.stderr}")
            if added.stdout.strip():
                parts.append(added.stdout)
        return "".join(parts)


def process_environment() -> dict[str, str]:
    """Do not propagate API credentials or Git overrides to repository commands."""
    return {k: v for k, v in os.environ.items()
            if not re.search(r"TOKEN|SECRET|PASSWORD|API_KEY|CREDENTIAL|^GIT_", k, re.I)} | {
                "PYTHONIOENCODING": "utf-8", "GIT_TERMINAL_PROMPT": "0"}


def stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _decode_timeout_stream(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
