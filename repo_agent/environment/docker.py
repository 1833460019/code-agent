from __future__ import annotations

import os
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from ..schemas import CommandResult
from .base import Environment, EnvironmentError
from .local import LocalEnvironment, stop_process


@dataclass(frozen=True, slots=True)
class DockerLimits:
    cpus: float = 2.0
    memory: str = "4g"
    pids: int = 256
    tmpfs_size: str = "512m"

    def __post_init__(self):
        if self.cpus <= 0 or self.pids < 16:
            raise ValueError("Docker CPU and PID limits must be positive")
        if not self.memory or not self.tmpfs_size:
            raise ValueError("Docker memory and tmpfs limits are required")


class DockerEnvironment(Environment):
    """Run target commands in one resource-limited, network-disabled container.

    File tools remain host-side and use LocalEnvironment containment checks. The target
    workspace is the only writable bind mount. No Docker socket or host credentials are
    passed into the container.
    """

    def __init__(
        self,
        workspace: str | Path,
        *,
        image: str,
        command_timeout: float = 120,
        limits: DockerLimits | None = None,
        user: str | None = None,
    ):
        if not image.strip():
            raise ValueError("A trusted Docker image is required")
        self.local = LocalEnvironment(workspace, command_timeout=command_timeout)
        self.image = image
        self.command_timeout = command_timeout
        self.limits = limits or DockerLimits()
        self.user = user or _host_container_user()
        self.name = "lcc-agent-" + uuid.uuid4().hex[:16]
        self.base_commit = self.local.base_commit
        self._lock = threading.Lock()
        self._processes: set[subprocess.Popen] = set()
        self._running = False
        self._start()

    @property
    def workspace(self) -> Path:
        return self.local.workspace

    def _start(self):
        try:
            check = subprocess.run(
                ["docker", "version", "--format", "{{.Server.Version}}"],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise EnvironmentError(f"Docker is unavailable: {exc}") from exc
        if check.returncode:
            raise EnvironmentError("Docker daemon is unavailable: " + check.stderr[-1000:])
        try:
            result = subprocess.run(
                self._start_command(), capture_output=True, text=True, timeout=120
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise EnvironmentError(f"Unable to start Docker environment: {exc}") from exc
        if result.returncode:
            raise EnvironmentError("Unable to start Docker environment: " + result.stderr[-2000:])
        self._running = True

    def _start_command(self) -> list[str]:
        mount = f"type=bind,src={self.workspace},dst=/workspace"
        return [
            "docker", "run", "--detach", "--name", self.name,
            "--network", "none", "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--pids-limit", str(self.limits.pids),
            "--memory", self.limits.memory, "--cpus", str(self.limits.cpus),
            "--tmpfs", f"/tmp:rw,nosuid,nodev,size={self.limits.tmpfs_size}",
            "--mount", mount, "--workdir", "/workspace", "--user", self.user,
            "--env", "HOME=/tmp", "--env", "PYTHONIOENCODING=utf-8",
            "--env", "GIT_TERMINAL_PROMPT=0", "--env", "REPO_AGENT_WORKSPACE=/workspace",
            "--entrypoint", "/bin/sh", self.image, "-c", "while :; do sleep 3600; done",
        ]

    def execute(self, command: str, *, cwd: str | Path = ".", timeout: float | None = None) -> CommandResult:
        if not command.strip():
            raise EnvironmentError("Command must not be empty")
        host_cwd = self.local.resolve_path(cwd)
        if not host_cwd.is_dir():
            raise EnvironmentError(f"Command cwd is not a directory: {cwd}")
        if not self._running:
            raise EnvironmentError("Docker environment is not running")
        relative = host_cwd.relative_to(self.workspace).as_posix()
        container_cwd = "/workspace" if relative == "." else "/workspace/" + relative
        limit = self.command_timeout if timeout is None else min(timeout, self.command_timeout)
        if limit <= 0:
            raise EnvironmentError("timeout must be positive")
        argv = ["docker", "exec", "--interactive", "--workdir", container_cwd,
                self.name, "/bin/sh", "-lc", command]
        started = time.perf_counter()
        process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, encoding="utf-8", errors="replace")
        with self._lock:
            self._processes.add(process)
        try:
            stdout, stderr = process.communicate(timeout=limit)
            return CommandResult(command=command, stdout=stdout, stderr=stderr,
                                 exit_code=process.returncode, duration_seconds=time.perf_counter() - started)
        except subprocess.TimeoutExpired:
            self.cancel_all()
            stdout, stderr = process.communicate()
            return CommandResult(command=command, stdout=stdout, stderr=stderr, exit_code=124,
                                 duration_seconds=time.perf_counter() - started, timed_out=True)
        finally:
            with self._lock:
                self._processes.discard(process)

    def cancel_all(self) -> None:
        with self._lock:
            for process in tuple(self._processes):
                stop_process(process)
        if self._running:
            subprocess.run(["docker", "rm", "--force", self.name], capture_output=True, timeout=30)
            self._running = False

    def reset_cancellation(self) -> None:
        if not self._running:
            self.name = "lcc-agent-" + uuid.uuid4().hex[:16]
            self._start()

    def clone_for_workspace(self, workspace: str | Path) -> "DockerEnvironment":
        return DockerEnvironment(workspace, image=self.image, command_timeout=self.command_timeout,
                                 limits=self.limits, user=self.user)

    def resolve_path(self, path: str | Path) -> Path:
        return self.local.resolve_path(path)

    def read_file(self, path: str | Path) -> str:
        return self.local.read_file(path)

    def write_file(self, path: str | Path, content: str) -> None:
        self.local.write_file(path, content)

    def edit_file(self, path: str | Path, old_text: str, new_text: str) -> None:
        self.local.edit_file(path, old_text, new_text)

    def get_diff(self) -> str:
        self.local.base_commit = self.base_commit
        return self.local.get_diff()

    def git(self, *args: str) -> subprocess.CompletedProcess:
        return self.local.git(*args)


def _host_container_user() -> str:
    if os.name != "nt" and hasattr(os, "getuid"):
        return f"{os.getuid()}:{os.getgid()}"
    return "1000:1000"
