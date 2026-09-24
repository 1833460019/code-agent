from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from repo_agent.run_metrics import load_runs, summarize_runs


class RunMetricsTests(unittest.TestCase):
    def test_metrics_distinguish_gate_from_command_verification(self) -> None:
        runs = [
            {
                "result": {"status": "success", "model_patch": "diff", "runtime": 10,
                           "total_tokens": 100, "total_tool_calls": 2},
                "verification": {"policy": {"commands": ["pytest"]}, "attempts": [
                    {"accepted": False, "commands": [{"command": "pytest", "exit_code": 1}]},
                    {"accepted": True, "commands": [{"command": "pytest", "exit_code": 0}]},
                ]},
                "trajectory": {"events": [{"type": "recovery"}], "steps": [
                    {"latency": 2, "tool_calls": [{"ok": True}, {"ok": False}]}
                ]},
            },
            {
                "result": {"status": "success", "model_patch": "diff", "runtime": 20,
                           "total_tokens": 200, "total_tool_calls": 1},
                "verification": {"policy": {"commands": []}, "attempts": [
                    {"accepted": True, "commands": []}
                ]},
                "trajectory": {"events": [], "steps": [{"latency": 4, "tool_calls": [{"ok": True}]}]},
            },
            {
                "result": {"status": "error", "model_patch": "", "runtime": 30,
                           "total_tokens": 300, "total_tool_calls": 0},
                "verification": None,
                "trajectory": {"events": [{"type": "recovery"}], "steps": []},
            },
        ]
        report = summarize_runs(runs)
        outcomes = report["outcomes"]
        self.assertEqual(outcomes["patch_rate"], {"value": 2 / 3, "numerator": 2, "denominator": 3})
        self.assertEqual(outcomes["finish_gate_pass_rate"]["value"], 1)
        self.assertEqual(outcomes["first_pass_finish_gate_rate"]["value"], 0.5)
        self.assertEqual(outcomes["command_verification_pass_rate"]["value"], 1)
        self.assertEqual(outcomes["recovery_success_rate"]["value"], 0.5)
        self.assertEqual(outcomes["tool_error_rate"]["value"], 1 / 3)
        self.assertEqual(report["efficiency"]["run_latency_seconds"]["p50"], 20)
        self.assertEqual(report["efficiency"]["run_latency_seconds"]["p95"], 29)
        self.assertEqual(report["efficiency"]["tokens_per_finish_gate_pass"]["count"], 2)
        self.assertEqual(report["efficiency"]["tool_calls_per_run"]["value"], 1)

    def test_absent_artifacts_are_unknown_not_failed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "case"
            run_dir.mkdir()
            (run_dir / "result.json").write_text(json.dumps({"status": "error"}), encoding="utf-8")
            report = summarize_runs(load_runs(Path(directory)))
        self.assertIsNone(report["outcomes"]["finish_gate_pass_rate"]["value"])
        self.assertEqual(report["outcomes"]["finish_gate_pass_rate"]["denominator"], 0)
        self.assertIsNone(report["outcomes"]["tool_error_rate"]["value"])
        self.assertIsNone(report["efficiency"]["run_latency_seconds"]["p50"])

    def test_command_without_evidence_is_not_verified(self) -> None:
        run = {
            "result": {"status": "success", "verification": {"accepted": True}},
            "verification": {"policy": {"commands": ["pytest"]},
                             "attempts": [{"accepted": True, "commands": []}]},
            "trajectory": None,
        }
        report = summarize_runs([run])
        self.assertEqual(report["outcomes"]["command_verification_pass_rate"]["value"], 0)


if __name__ == "__main__":
    unittest.main()
