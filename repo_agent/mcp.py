"""MCP stdio client: real JSON-RPC initialization, discovery and tool execution.

Implements the tools capability of the 2025-06-18 protocol. Servers are configured
by the host user; the model cannot choose a process to launch.
"""
from __future__ import annotations

import asyncio
import json
import re
import os
import signal
import subprocess
from pathlib import Path

from .environment.local import process_environment
from .schemas import ToolResult
from .tools.base import FunctionTool


class MCPClient:
    def __init__(self, name: str, command: list[str], *, cwd: Path, env: dict | None = None, timeout=30):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,24}", name) or not command:
            raise ValueError("MCP needs a valid server name and command argv")
        self.name, self.command, self.cwd = name, command, cwd
        self.env, self.timeout = env or {}, timeout
        self.process = None
        self._serial = 0
        self._lock = asyncio.Lock()
        self.stderr = ""
        self._stderr_task = None

    async def start(self):
        self.process = await asyncio.create_subprocess_exec(
            *self.command, cwd=self.cwd, env=process_environment() | self.env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=4 * 1024 * 1024,
            start_new_session=os.name != "nt",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        self._stderr_task = asyncio.create_task(self._drain_stderr())
        try:
            result = await self.request("initialize", {
                "protocolVersion": "2025-06-18", "capabilities": {"roots": {"listChanged": False}},
                "clientInfo": {"name": "lcc-repo-agent", "version": "0.2.0"}})
            if result.get("protocolVersion") not in {"2024-11-05", "2025-03-26", "2025-06-18"}:
                raise ValueError("Unsupported MCP protocol version")
            await self._write({"jsonrpc": "2.0", "method": "notifications/initialized"})
            return result
        except BaseException:
            await self.close()
            raise

    async def _drain_stderr(self):
        while True:
            block = await self.process.stderr.read(4096)
            if not block:
                break
            self.stderr = (self.stderr + block.decode("utf-8", errors="replace"))[-32000:]

    async def _write(self, payload):
        self.process.stdin.write(json.dumps(payload, ensure_ascii=False).encode() + b"\n")
        await self.process.stdin.drain()

    async def request(self, method: str, params: dict | None = None):
        async with self._lock:
            self._serial += 1
            key = self._serial
            await self._write({"jsonrpc": "2.0", "id": key, "method": method, "params": params or {}})
            try:
                return await asyncio.wait_for(self._response(key), self.timeout)
            except (TimeoutError, asyncio.CancelledError):
                try:
                    await self._write({"jsonrpc": "2.0", "method": "notifications/cancelled",
                                       "params": {"requestId": key, "reason": "Client deadline or cancellation"}})
                except (BrokenPipeError, ConnectionError):
                    pass
                raise

    async def _response(self, key):
        while True:
            line = await self.process.stdout.readline()
            if not line:
                raise ConnectionError(f"MCP server {self.name} closed stdout: {self.stderr[-1000:]}")
            message = json.loads(line)
            if "method" in message:
                if "id" in message:
                    if message["method"] == "roots/list":
                        await self._write({"jsonrpc": "2.0", "id": message["id"],
                                           "result": {"roots": [{"uri": self.cwd.as_uri(), "name": "workspace"}]}})
                    elif message["method"] == "ping":
                        await self._write({"jsonrpc": "2.0", "id": message["id"], "result": {}})
                    else:
                        await self._write({"jsonrpc": "2.0", "id": message["id"],
                                           "error": {"code": -32601, "message": "Capability not supported"}})
                continue
            if message.get("id") != key:
                continue
            if "error" in message:
                raise RuntimeError(f"MCP error: {message['error']}")
            return message["result"]

    async def tools(self):
        definitions, cursor, seen = [], None, set()
        for _ in range(100):
            result = await self.request("tools/list", {"cursor": cursor} if cursor else {})
            definitions.extend(result.get("tools", []))
            cursor = result.get("nextCursor")
            if not cursor:
                return definitions
            if cursor in seen:
                raise ValueError("MCP tools/list repeated cursor")
            seen.add(cursor)
        raise ValueError("MCP tool discovery exceeded page budget")

    async def call(self, name, arguments):
        result = await self.request("tools/call", {"name": name, "arguments": arguments})
        return ToolResult(ok=not result.get("isError", False),
                          output=json.dumps(result, ensure_ascii=False), metadata={"mcp_server": self.name})

    async def close(self):
        if self.process and self.process.returncode is None:
            self.process.stdin.close()
            try:
                await asyncio.wait_for(self.process.wait(), 2)
            except TimeoutError:
                if os.name == "nt":
                    await asyncio.to_thread(subprocess.run,
                        ["taskkill", "/PID", str(self.process.pid), "/T", "/F"],
                        capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
                else:
                    try:
                        os.killpg(self.process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                if self.process.returncode is None:
                    self.process.kill()
                await self.process.wait()
        if self._stderr_task:
            try:
                await asyncio.wait_for(asyncio.shield(self._stderr_task), 2)
            except TimeoutError:
                self._stderr_task.cancel()
            await asyncio.gather(self._stderr_task, return_exceptions=True)


class MCPManager:
    def __init__(self, config: dict, workspace: Path):
        self.config, self.workspace = config, workspace
        self.clients: list[MCPClient] = []

    async def connect(self):
        tools, names = [], set()
        try:
            for name, settings in self.config.get("mcpServers", {}).items():
                client = MCPClient(name, [settings["command"], *settings.get("args", [])],
                                   cwd=self.workspace, env=settings.get("env"), timeout=settings.get("timeout", 30))
                self.clients.append(client)
                await client.start()
                for definition in await client.tools():
                    tool_name = "mcp_" + name + "_" + re.sub(r"[^a-zA-Z0-9_-]", "_", definition["name"])
                    if tool_name in names or len(tool_name) > 64:
                        raise ValueError("MCP tool name collision or excessive length")
                    names.add(tool_name)
                    def bind(selected_client, selected_name):
                        async def handler(**arguments):
                            return await selected_client.call(selected_name, arguments)
                        return handler
                    handler = bind(client, definition["name"])
                    tool = FunctionTool(tool_name, definition.get("description", "External MCP tool"), {}, [], handler)
                    tool.input_schema = definition["inputSchema"]
                    tools.append(tool)
            return tools
        except BaseException:
            await self.close()
            raise

    async def close(self):
        await asyncio.gather(*(client.close() for client in self.clients), return_exceptions=True)
