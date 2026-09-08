import asyncio
import json
import sys
from pathlib import Path

from repo_agent.context.manager import ContextManager
from repo_agent.permissions import PermissionPolicy, PermissionRule
from repo_agent.schemas import Message, ToolCall
from tests.support import WorkspaceCase, SequenceModel, call


class RuntimeCoreTests(WorkspaceCase):
    def test_staged_and_committed_changes_are_in_patch(self):
        env = self.agent(SequenceModel([])).environment
        env.write_file("base.txt", "changed\n")
        self.git("add", "base.txt")
        staged = env.get_diff()
        self.assertIn("+changed", staged)
        self.git("commit", "-m", "agent accidentally committed")
        self.assertEqual(staged, env.get_diff())

    def test_patch_preserves_trailing_context_blank_lines(self):
        (self.repo / "base.txt").write_text("one\ntwo\n\n", encoding="utf-8", newline="")
        self.git("add", ".")
        self.git("commit", "-m", "blank context")
        env = self.agent(SequenceModel([])).environment
        env.edit_file("base.txt", "one", "ONE")
        self.git("apply", "--reverse", "--check", "-", input=env.get_diff())

    def test_failed_commands_are_visible_and_recoverable(self):
        model = SequenceModel([call("shell", command=f'"{sys.executable}" -c "raise SystemExit(7)"'),
                               call("write_file", path="fixed.txt", content="fixed"), call("finish", summary="done")])
        result = asyncio.run(self.agent(model).run("Fix issue"))
        trajectory = json.loads((Path(result.run_dir) / "trajectory.json").read_text(encoding="utf-8"))
        self.assertFalse(trajectory["steps"][0]["tool_calls"][0]["ok"])
        self.assertIn("exit_code=7", model.requests[1]["messages"][-1].content)
        self.assertTrue((self.repo / "fixed.txt").exists())

    def test_callback_failure_does_not_lose_patch(self):
        def broken(event):
            raise BrokenPipeError("closed")
        result = asyncio.run(self.agent(SequenceModel([call("write_file", path="new.txt", content="ok"),
                                                      call("finish", summary="done")])).run("Fix", event_callback=broken))
        self.assertTrue((Path(result.run_dir) / "result.json").is_file())
        self.assertIn("+ok", (Path(result.run_dir) / "patch.diff").read_text())

    def test_cancellation_saves_partial_patch(self):
        class Waiting(SequenceModel):
            async def complete(self, **kwargs):
                if self.requests:
                    raise asyncio.CancelledError
                return await super().complete(**kwargs)
        agent = self.agent(Waiting([call("write_file", path="partial.txt", content="partial")]))
        with self.assertRaises(asyncio.CancelledError):
            asyncio.run(agent.run("Fix"))
        result = json.loads((self.root / "runs" / "manual" / "result.json").read_text())
        self.assertEqual(result["termination_reason"], "cancelled")
        self.assertIn("partial.txt", result["model_patch"])

    def test_hooks_and_permission_denial_prevent_edits(self):
        model = SequenceModel([call("write_file", path="denied.txt", content="bad"), call("finish", summary="blocked")])
        agent = self.agent(model)
        seen = []
        agent.hooks.register("before_tool", lambda payload: seen.append(payload["tool"]))
        agent.policy = PermissionPolicy("trusted", [PermissionRule("write_file", "deny")])
        asyncio.run(agent.run("Do work"))
        self.assertFalse((self.repo / "denied.txt").exists())
        self.assertEqual(seen, ["finish"])
        async def ask_test():
            approved = []
            policy = PermissionPolicy("ask", approver=lambda name, args: approved.append(name) or True)
            await policy.check("write_file", {})
            self.assertEqual(approved, ["write_file"])
        asyncio.run(ask_test())

    def test_hook_veto_is_an_observation(self):
        agent = self.agent(SequenceModel([call("write_file", path="x", content="bad"), call("finish", summary="done")]))
        agent.hooks.register("before_tool", lambda p: False if p["tool"] == "write_file" else None)
        asyncio.run(agent.run("Do work"))
        self.assertFalse((self.repo / "x").exists())

    def test_full_output_is_saved_separately_from_observation(self):
        (self.repo / "large.txt").write_text("x" * 3000)
        model = SequenceModel([call("read_file", path="large.txt"), call("finish", summary="done")])
        result = asyncio.run(self.agent(model, tool_output_limit_chars=500).run("Read"))
        trajectory = json.loads((Path(result.run_dir) / "trajectory.json").read_text())
        entry = trajectory["steps"][0]["tool_calls"][0]
        self.assertGreater(len(entry["observation"]), 3000)
        self.assertLess(len(entry["model_observation"]), 500)
        self.assertTrue((Path(result.run_dir) / "outputs" / (entry["artifact_id"] + ".txt")).exists())

    def test_context_budget_and_pairs(self):
        context = ContextManager(soft_limit_chars=1500)
        messages = [Message(role="user", content="issue")]
        for index in range(20):
            messages.extend([Message(role="assistant", tool_calls=[ToolCall(str(index), "read_file", {"path": "x"})]),
                             Message(role="tool", content="x"*5000, tool_call_id=str(index))])
        prepared = context.prepare(messages)
        self.assertLessEqual(context._size(prepared), 1500)
        calls = {c.id for m in prepared for c in m.tool_calls}
        self.assertTrue(all(m.tool_call_id in calls for m in prepared if m.role == "tool"))
        self.assertEqual(messages[-1].content, "x"*5000)

    def test_schema_errors_are_observations(self):
        result = asyncio.run(self.agent(SequenceModel([call("write_file", path=123),
                                                      call("finish", summary="done")])).run("Fix"))
        trajectory = json.loads((Path(result.run_dir) / "trajectory.json").read_text())
        self.assertFalse(trajectory["steps"][0]["tool_calls"][0]["ok"])

    def test_model_recovery_retry_and_fallback(self):
        primary = SequenceModel([ConnectionError("temporary"), ValueError("provider failure")])
        fallback = SequenceModel([call("finish", summary="fallback done")])
        agent = self.agent(primary)
        agent.fallback_model = fallback
        result = asyncio.run(agent.run("Fix"))
        self.assertEqual(result.status, "success")
        self.assertEqual(len(fallback.requests), 1)
