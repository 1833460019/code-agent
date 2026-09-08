import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone

from repo_agent.background import BackgroundManager
from repo_agent.environment.local import LocalEnvironment
from repo_agent.logging.trajectory import TrajectoryRecorder
from repo_agent.permissions import PermissionPolicy, PermissionRule
from repo_agent.scheduler import CronScheduler
from repo_agent.schemas import Message, ModelResponse, Usage
from repo_agent.teams import TeamManager
from repo_agent.tasks import TaskStore
from repo_agent.worktrees import WorktreeManager
from tests.support import WorkspaceCase, SequenceModel, call


class ExtendedFeaturesTests(WorkspaceCase):
    def test_full_profile_composes_skills_todos_memory_tasks_and_hooks(self):
        skill = self.repo / "skills" / "verify" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("---\nname: verify\ndescription: verify changes\n---\nUNIQUE_SKILL_BODY", encoding="utf-8")
        model = SequenceModel([
            call("TodoWrite", items=[{"content": "Inspect", "status": "in_progress"}]),
            call("load_skill", name="verify"),
            call("memory_write", name="runtime", content="Use Python", kind="project"),
            call("task_create", subject="Verify code"),
            call("read_file", path="base.txt"),
            call("TodoWrite", items=[{"content": "Inspect", "status": "completed"}]),
            call("finish", summary="inspected")])
        agent = self.agent(model, profile="full")
        seen = []
        for event in ("user_prompt", "before_model", "after_model", "before_tool", "after_tool", "stop"):
            agent.hooks.register(event, lambda p, event=event: seen.append(event))
        result = asyncio.run(agent.run("Inspect Python project"))
        self.assertEqual(result.status, "success", result.error)
        self.assertNotIn("UNIQUE_SKILL_BODY", model.requests[0]["system_prompt"])
        self.assertIn("UNIQUE_SKILL_BODY", model.requests[2]["messages"][-1].content)
        self.assertIn("Use Python", model.requests[-1]["system_prompt"])
        self.assertIn("Verify code", model.requests[-1]["system_prompt"])
        self.assertIn("Shell command syntax:", model.requests[0]["system_prompt"])
        self.assertIn("already starts in the workspace root", model.requests[0]["system_prompt"])
        self.assertEqual(seen[0], "user_prompt")
        self.assertEqual(seen[-1], "stop")
        self.assertEqual(seen.count("before_tool"), 7)
        self.assertEqual(len(agent.last_runtime.prompt._cache), 1)
        self.assertEqual(agent.last_runtime.todos.items[0]["status"], "completed")

    def test_auto_memory_extraction_is_persisted_and_metered(self):
        model = SequenceModel([call("finish", summary="done"), ModelResponse(content=json.dumps([
            {"name": "test-command", "kind": "project", "description": "Tests", "content": "Use unittest"}
        ]), usage=Usage(12, 6))])
        agent = self.agent(model, profile="full", auto_memory=True)
        result = asyncio.run(agent.run("Remember the test command"))
        self.assertEqual(result.input_tokens, 22)
        self.assertEqual(agent.last_runtime.memory.read("test-command")["content"], "Use unittest")
        with self.assertRaises(ValueError):
            agent.last_runtime.memory.write("index", "invalid")

    def test_compaction_summary_is_retained_in_next_turn(self):
        history = [Message(role="user", content="original issue")]
        history += [Message(role="user", content="old context " + str(i)) for i in range(10)]
        model = SequenceModel([call("compact", focus="tests"), ModelResponse(content="Saved test findings"),
                               call("read_file", path="base.txt"), call("finish", summary="done")])
        agent = self.agent(model, profile="full")
        result = asyncio.run(agent.run("Continue", history=history))
        self.assertEqual(result.status, "success", result.error)
        self.assertTrue(any(m.role == "summary" and m.content == "Saved test findings"
                            for m in model.requests[-1]["messages"]))
        self.assertLess(len(agent.last_messages), len(history) + 6)

    def test_truncated_model_response_increases_budget_before_tools(self):
        model = SequenceModel([ModelResponse(stop_reason="max_tokens", tool_calls=call("write_file", path="bad", content="bad").tool_calls),
                               call("finish", summary="retried")])
        model.max_tokens = 512
        result = asyncio.run(self.agent(model).run("Retry incomplete response"))
        self.assertEqual(result.status, "success", result.error)
        self.assertEqual(model.max_tokens, 1024)
        self.assertFalse((self.repo / "bad").exists())

    def test_context_error_recovers_without_orphan_tool_results(self):
        history = [Message(role="user", content="issue")] + [Message(role="user", content="x" * 600) for _ in range(8)]
        model = SequenceModel([ValueError("context length exceeded"), call("finish", summary="recovered")])
        result = asyncio.run(self.agent(model).run("Continue", history=history))
        self.assertEqual(result.status, "success", result.error)
        self.assertLess(len(model.requests[1]["messages"]), len(model.requests[0]["messages"]))

    def test_tiny_observation_limit_never_returns_entire_output(self):
        recorder = TrajectoryRecorder(runs_dir=self.root / "runs", instance_id="tiny", model_name="test",
                                      system_prompt="", problem_statement="")
        visible, artifact = recorder.observation("x" * 10000, 10)
        self.assertLess(len(visible), 150)
        self.assertIsNotNone(artifact)

    def test_background_cancel_joins_process_and_blocks_late_write(self):
        async def run():
            manager = BackgroundManager(LocalEnvironment(self.repo), self.root / "jobs")
            job = manager.start(f'"{sys.executable}" -c "import time; time.sleep(10); open(\'late.txt\',\'w\').write(\'bad\')"')
            await asyncio.sleep(0.1)
            result = await asyncio.wait_for(manager.cancel(job["id"]), 5)
            self.assertEqual(result["status"], "cancelled")
            self.assertFalse(manager.envs[job["id"]]._processes)
            self.assertFalse((self.repo / "late.txt").exists())
            immediate = manager.start(f'"{sys.executable}" -c "print(1)"')
            self.assertEqual((await manager.cancel(immediate["id"]))["status"], "cancelled")
            await manager.close()
        asyncio.run(run())

    def test_background_cannot_bypass_shell_deny_rule(self):
        policy = PermissionPolicy("trusted", [PermissionRule("shell", "deny", "*")])
        with self.assertRaises(PermissionError):
            asyncio.run(policy.check("background_run", {"command": "echo bad"}))

    def test_scheduler_producer_continues_during_slow_agent_run(self):
        class TickClockScheduler(CronScheduler):
            minute = 0
            def tick(self, now=None):
                self.minute += 1
                super().tick(datetime(2026, 9, 7, tzinfo=timezone.utc) + timedelta(minutes=self.minute))
        async def run():
            scheduler = TickClockScheduler(self.root / "scheduler")
            scheduler.schedule("* * * * *", "check", max_runs=3, durable=False)
            stop = asyncio.Event()
            async def consume(entry):
                await asyncio.sleep(0.1)
                self.assertEqual(len(scheduler.list()["queue"]), 3)
                stop.set()
                return {"status": "success"}
            await scheduler.serve(consume, stop, interval=0.01)
        asyncio.run(run())

    def test_idle_teammate_shutdown_handshake(self):
        async def run():
            root = self.root / "team"
            async def unexpected(*args):
                raise AssertionError("Idle worker should not call model")
            team = TeamManager(root, TaskStore(root), WorktreeManager(LocalEnvironment(self.repo), root),
                               unexpected, idle_timeout=5)
            team.spawn("worker", autonomous=True)
            request = team.protocols.request("lead", "worker", "shutdown")
            result = await team.wait("worker", 5)
            self.assertEqual(result["status"], "stopped")
            self.assertEqual(team.protocols.get(request["id"])["status"], "approved")
            await team.close()
        asyncio.run(run())
