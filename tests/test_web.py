import asyncio
import json
import importlib
from unittest.mock import patch

from backend.app.core.agent import AgentKernel, _to_runtime
from backend.app.core.config import Settings
from tests.support import WorkspaceCase, SequenceModel, call


class WebTests(WorkspaceCase):
    def kernel(self, model, mode="ask"):
        settings = Settings(_env_file=None, AGENT_WORKSPACE=self.repo,
                            AGENT_STATE_DIR=self.root / "web", PERMISSION_MODE=mode)
        return AgentKernel(settings, model)

    def test_stream_approval_allows_real_write_and_persists_history(self):
        model = SequenceModel([call("write_file", path="approved.txt", content="approved"),
                               call("finish", summary="done"), call("finish", summary="second")])
        kernel = self.kernel(model)
        events = []
        async def run():
            async for event in kernel.run_turn(None, "Create approved.txt"):
                events.append(event)
                if event.type == "approval_required":
                    self.assertFalse((self.repo / "approved.txt").exists())
                    kernel.resolve_approval(event.data["request_id"], True)
            key = events[0].session_id
            async for event in kernel.run_turn(key, "What changed?"):
                pass
            return key
        key = asyncio.run(run())
        self.assertEqual((self.repo / "approved.txt").read_text(), "approved")
        self.assertEqual(next(e for e in events if e.type == "done").data["status"], "success")
        self.assertIn("Create approved.txt", [m.content for m in model.requests[-1]["messages"]])
        self.assertIn(key, self.kernel(SequenceModel([])).sessions)
        self.assertFalse(kernel.approvals)

    def test_denied_web_approval_prevents_write(self):
        kernel = self.kernel(SequenceModel([call("write_file", path="denied.txt", content="bad"),
                                            call("finish", summary="permission denied")]))
        async def run():
            result = []
            async for event in kernel.run_turn(None, "Write"):
                if event.type == "approval_required":
                    kernel.resolve_approval(event.data["request_id"], False)
                result.append(event)
            return result
        events = asyncio.run(run())
        self.assertFalse((self.repo / "denied.txt").exists())
        self.assertTrue(any(e.type == "tool_result" and e.is_error for e in events))

    def test_disconnected_approval_is_cancelled_and_run_saved(self):
        kernel = self.kernel(SequenceModel([call("write_file", path="never.txt", content="bad")]))
        async def run():
            stream = kernel.run_turn(None, "Write")
            async for event in stream:
                if event.type == "approval_required":
                    await stream.aclose()
                    break
        asyncio.run(run())
        self.assertFalse(kernel.approvals)
        self.assertFalse((self.repo / "never.txt").exists())
        results = list((self.root / "web" / "runs").glob("*/result.json"))
        self.assertEqual(json.loads(results[0].read_text())["termination_reason"], "cancelled")

    def test_two_web_turns_serialize_history(self):
        kernel = self.kernel(SequenceModel([call("finish", summary="first"), call("finish", summary="second")]))
        session = kernel.get_session()
        async def consume(text):
            return [event async for event in kernel.run_turn(session.session_id, text)]
        async def run():
            await asyncio.gather(consume("first question"), consume("second question"))
        asyncio.run(run())
        requests = kernel.model.requests
        self.assertIn("first question", [m.content for m in requests[1]["messages"]])
        self.assertEqual(sum(m.role == "user" for m in _to_runtime(session.messages)), 2)

    def test_web_rest_routes_and_todo_continuity(self):
        from fastapi.testclient import TestClient
        model = SequenceModel([call("TodoWrite", items=[{"content": "Verify", "status": "pending"}]),
                               call("finish", summary="planned"), call("finish", summary="continued")])
        kernel = self.kernel(model, "trusted")
        with patch("backend.app.core.config.get_settings", return_value=kernel.settings), \
             patch("backend.app.core.model.create_model_adapter", return_value=model):
            module = importlib.import_module("backend.app.main")
        with patch.object(module, "kernel", kernel), patch.object(module, "settings", kernel.settings):
            with TestClient(module.app) as client:
                self.assertEqual(client.get("/api/health").status_code, 200)
                first = client.post("/api/chat", json={"message": "Plan"})
                self.assertEqual(first.status_code, 200)
                key = first.json()["session_id"]
                second = client.post("/api/chat", json={"session_id": key, "message": "Continue"})
                self.assertEqual(second.status_code, 200)
                self.assertIn("Verify", model.requests[-1]["system_prompt"])
                self.assertEqual(client.get("/api/sessions").json()[0]["todos"][0]["content"], "Verify")
                self.assertEqual(client.get("/api/schedules").status_code, 200)
                runs = client.get("/api/runs")
                self.assertEqual(runs.status_code, 200)
                self.assertGreaterEqual(len(runs.json()), 2)
                run_id = runs.json()[0]["run_id"]
                detail = client.get(f"/api/runs/{run_id}")
                self.assertEqual(detail.status_code, 200)
                self.assertIn("trajectory", detail.json())
                self.assertIn("patch", detail.json())
                self.assertEqual(client.get("/api/runs/not-found").status_code, 404)
                self.assertEqual(client.post("/api/approvals/expired", json={"approved": True}).status_code, 404)
