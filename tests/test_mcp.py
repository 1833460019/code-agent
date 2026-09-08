import asyncio
import sys
from pathlib import Path

from tests.support import WorkspaceCase, SequenceModel, call


class MCPTests(WorkspaceCase):
    def test_mcp_process_discovery_and_call_through_agent_loop(self):
        config = {"mcpServers": {"fixture": {
            "command": sys.executable, "args": [str(Path(__file__).parent / "fixtures" / "mcp_server.py")]}}}
        model = SequenceModel([call("mcp_fixture_echo", text="from MCP"), call("finish", summary="done")])
        result = asyncio.run(self.agent(model, profile="full", mcp_config=config).run("Call echo"))
        self.assertEqual(result.status, "success")
        self.assertIn("from MCP", model.requests[1]["messages"][-1].content)
        self.assertIn("mcp_fixture_echo", {t["name"] for t in model.requests[0]["tools"]})
