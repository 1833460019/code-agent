import asyncio
import json
from pathlib import Path

from repo_agent.models.base import Model
from repo_agent.schemas import ModelResponse
from tests.support import WorkspaceCase, call, observations


class TeamModel(Model):
    @property
    def name(self):
        return "team-observation-model"

    async def complete(self, *, system_prompt, messages, tools):
        await asyncio.sleep(0.03)
        if "Agent: worker" in system_prompt:
            if not observations(messages, "submit_plan"):
                return call("submit_plan", plan="Create work.txt and finish")
            waits = observations(messages, "plan_wait")
            if not waits or json.loads(waits[-1])["status"] != "approved":
                return call("plan_wait", timeout=1)
            if not observations(messages, "write_file"):
                return call("write_file", path="work.txt", content="team result\n")
            return call("finish", summary="Created isolated patch")
        if not observations(messages, "team_spawn"):
            return call("team_spawn", name="worker", prompt="Create work.txt", require_plan=True)
        if not observations(messages, "protocol_respond"):
            status = observations(messages, "team_status")
            if status and json.loads(status[-1])["plan_request"]:
                return call("protocol_respond", request_id=json.loads(status[-1])["plan_request"], approve=True)
            return call("team_status", name="worker")
        waits = observations(messages, "team_wait")
        if not waits or json.loads(waits[-1])["status"] != "stopped":
            return call("team_wait", name="worker", timeout=10)
        if not observations(messages, "worktree_merge"):
            return call("worktree_merge", name=json.loads(waits[-1])["worktree"])
        return call("finish", summary="Merged teammate changes")


class SubagentModel(Model):
    @property
    def name(self):
        return "subagent-observation-model"

    async def complete(self, *, system_prompt, messages, tools):
        if "Agent: sub-" in system_prompt:
            assert "subagent" not in {t["name"] for t in tools}
            if not observations(messages, "read_file"):
                return call("read_file", path="base.txt")
            return call("finish", summary="base.txt contains base")
        if not observations(messages, "subagent"):
            return call("subagent", description="Read base.txt and report its content", agent_type="explore")
        return call("finish", summary="Received subagent finding")


class AutonomousModel(Model):
    @property
    def name(self):
        return "autonomous-model"

    async def complete(self, *, system_prompt, messages, tools):
        if "Agent: worker" in system_prompt:
            if not observations(messages, "write_file"):
                filename = "second.txt" if "second" in messages[0].content else "first.txt"
                return call("write_file", path=filename, content="claimed and executed")
            return call("finish", summary="Task finished")
        created = observations(messages, "task_create")
        if not created:
            return call("task_create", subject="first")
        if len(created) == 1:
            return call("task_create", subject="second", blocked_by=[json.loads(created[0])["id"]])
        if not observations(messages, "team_spawn"):
            return call("team_spawn", name="worker", autonomous=True)
        waited = observations(messages, "team_wait")
        if not waited or json.loads(waited[-1])["status"] != "stopped":
            return call("team_wait", name="worker", timeout=10)
        if not observations(messages, "worktree_merge"):
            return call("worktree_merge", name=json.loads(waited[-1])["worktree"])
        return call("finish", summary="Task DAG executed")


class AgentIntegrationTests(WorkspaceCase):
    def test_subagent_has_independent_context_and_real_loop(self):
        agent = self.agent(SubagentModel(), profile="full")
        result = asyncio.run(agent.run("Explore using a subagent"))
        self.assertEqual(result.status, "success")
        children = list((Path(result.run_dir) / "children").glob("*/trajectory.json"))
        self.assertEqual(len(children), 1)
        child = json.loads(children[0].read_text())
        self.assertEqual([s["tool_calls"][0]["tool_name"] for s in child["steps"]], ["read_file", "finish"])
        self.assertEqual(len(agent.last_runtime.child_results), 1)

    def test_teammate_plan_approval_execution_and_merge(self):
        result = asyncio.run(self.agent(TeamModel(), profile="full", max_steps=40).run("Delegate work"))
        self.assertEqual(result.status, "success", result.error)
        self.assertEqual((self.repo / "work.txt").read_text(), "team result\n")
        self.assertIn("work.txt", result.model_patch)
        records = json.loads((self.root / "state" / "protocols.json").read_text())
        self.assertEqual(next(iter(records.values()))["status"], "approved")

    def test_autonomous_worker_claims_dependent_tasks(self):
        result = asyncio.run(self.agent(AutonomousModel(), profile="full", team_idle_timeout=0.1).run("Execute task graph"))
        self.assertEqual(result.status, "success", result.error)
        self.assertTrue((self.repo / "first.txt").exists())
        self.assertTrue((self.repo / "second.txt").exists())
        tasks = json.loads((self.root / "state" / "tasks.json").read_text())
        self.assertTrue(all(t["status"] == "completed" and t["owner"] == "worker" for t in tasks.values()))
