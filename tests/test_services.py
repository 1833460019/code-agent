import asyncio
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from repo_agent.background import BackgroundManager
from repo_agent.environment.local import LocalEnvironment
from repo_agent.knowledge import MemoryStore, SkillCatalog
from repo_agent.scheduler import CronScheduler, cron_matches
from repo_agent.tasks import TaskStore
from repo_agent.teams import MessageBus, Protocols
from repo_agent.worktrees import WorktreeManager
from tests.support import WorkspaceCase, SequenceModel, call


class ServicesTests(WorkspaceCase):
    def test_dependency_dag_and_atomic_claims(self):
        tasks = TaskStore(self.root / "state")
        first = tasks.create("first")
        second = tasks.create("second", blocked_by=[first["id"]])
        self.assertIsNone(tasks.claim("worker", second["id"]))
        with self.assertRaises(ValueError):
            tasks.update(first["id"], owner="worker", status="pending", blocked_by=[second["id"]])
        with ThreadPoolExecutor(4) as pool:
            claimed = list(pool.map(lambda name: tasks.claim(name, first["id"]), ["one", "two", "three", "four"]))
        self.assertEqual(sum(t is not None for t in claimed), 1)
        owner = next(t["owner"] for t in claimed if t)
        tasks.update(first["id"], owner=owner, status="completed")
        self.assertIsNotNone(tasks.claim("next", second["id"]))
        self.assertEqual(len(TaskStore(self.root / "state").list()), 2)

    def test_skill_and_memory_roundtrip(self):
        skill = self.repo / "skills" / "debug" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("---\nname: debug\ndescription: inspect failures\n---\nRead the failing test first.")
        catalog = SkillCatalog([self.repo / "skills"])
        self.assertEqual(catalog.discover()[0]["name"], "debug")
        self.assertIn("failing test", catalog.load("debug"))
        memory = MemoryStore(self.root / "state")
        memory.write("one", "Use Python 3.11", "project", "runtime")
        memory.write("two", "Use Python 3.11", "project", "duplicate")
        self.assertEqual(len(memory.consolidate()["duplicates_removed"]), 1)
        self.assertEqual(memory.search("Python")[0]["content"], "Use Python 3.11")
        with self.assertRaises(ValueError):
            memory.read("../escape")

    def test_background_process_and_notification(self):
        async def run():
            manager = BackgroundManager(LocalEnvironment(self.repo), self.root / "jobs")
            job = manager.start(f'"{sys.executable}" -c "print(42)"')
            result = await manager.wait(job["id"], 10)
            self.assertEqual(result["status"], "completed")
            self.assertIn("42", result["result"]["stdout"])
            self.assertEqual(len(manager.drain()), 1)
            self.assertEqual(manager.drain(), [])
            await manager.close()
        asyncio.run(run())

    def test_cron_queue_is_durable_and_deduplicated(self):
        root = self.root / "state"
        scheduler = CronScheduler(root)
        scheduler.schedule("*/5 * * * *", "run checks", max_runs=2)
        now = datetime(2026, 9, 6, 12, 5, tzinfo=timezone.utc)
        scheduler.tick(now)
        scheduler.tick(now)
        restarted = CronScheduler(root)
        self.assertEqual(len(restarted.list()["queue"]), 1)
        entry = restarted.claim()
        self.assertIsNone(restarted.claim())
        restarted.complete(entry["id"], {"status": "success"})
        self.assertEqual(restarted.list()["queue"][0]["status"], "completed")
        self.assertTrue(cron_matches("5 12 * * 0", now))
        with self.assertRaises(ValueError):
            cron_matches("*/0 * * * *", now)

    def test_scheduler_consumer_executes_real_agent(self):
        async def run():
            scheduler = CronScheduler(self.root / "scheduler")
            scheduler.schedule("* * * * *", "Create scheduled file", max_runs=1)
            stop = asyncio.Event()
            agent = self.agent(SequenceModel([call("write_file", path="scheduled.txt", content="ran"),
                                              call("finish", summary="done")]))
            async def consume(entry):
                result = await agent.run(entry["prompt"])
                stop.set()
                return result.to_dict()
            await asyncio.wait_for(scheduler.serve(consume, stop, interval=0.01), 15)
            self.assertEqual((self.repo / "scheduled.txt").read_text(), "ran")
            self.assertEqual(scheduler.list()["queue"][0]["status"], "completed")
        asyncio.run(run())

    def test_worktree_edit_review_merge_and_keep(self):
        trees = WorktreeManager(LocalEnvironment(self.repo), self.root / "state")
        trees.create("feature", "task-1")
        env = trees.environment_for("feature")
        env.write_file("new.txt", "isolated\n")
        self.assertFalse((self.repo / "new.txt").exists())
        self.assertIn("+isolated", trees.diff("feature"))
        trees.merge("feature")
        self.assertEqual((self.repo / "new.txt").read_text(), "isolated\n")
        with self.assertRaises(ValueError):
            trees.remove("feature")
        self.assertEqual(trees.keep("feature")["status"], "kept")
        trees.create("clean")
        trees.remove("clean")
        self.assertEqual(len(trees.list()), 1)

    def test_protocol_response_must_match_recipient(self):
        bus = MessageBus(self.root / "state")
        protocols = Protocols(self.root / "state", bus)
        request = protocols.request("lead", "worker", "shutdown")
        with self.assertRaises(ValueError):
            protocols.respond("intruder", request["id"], True)
        protocols.respond("worker", request["id"], True, "finished")
        self.assertEqual(protocols.get(request["id"])["status"], "approved")
        self.assertEqual(bus.receive("lead")[0]["kind"], "response")
        self.assertEqual(bus.receive("lead"), [])
