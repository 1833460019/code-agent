from __future__ import annotations

import unittest

from repo_agent.evaluation import classify_failure, paired_bootstrap, summarize


class EvaluationTests(unittest.TestCase):
    def test_failure_taxonomy_and_summary(self):
        rows = [
            {"instance_id": "a", "status": "success", "model_patch": "diff", "total_tokens": 10, "runtime": 2},
            {"instance_id": "b", "status": "terminated", "termination_reason": "max_steps", "model_patch": "diff", "total_tokens": 30, "runtime": 4},
            {"instance_id": "c", "status": "error", "termination_reason": "model_error", "model_patch": "", "total_tokens": 0, "runtime": 1},
        ]
        self.assertEqual(classify_failure(rows[1]), "budget_exhausted")
        self.assertEqual(classify_failure(rows[2]), "no_patch")
        report = summarize(rows)
        self.assertEqual(report["runs"], 3)
        self.assertEqual(report["mean_tokens"], 40 / 3)
        self.assertEqual(report["categories"]["candidate"], 1)

    def test_paired_bootstrap_is_reproducible(self):
        baseline = [
            {"instance_id": "a", "resolved": False},
            {"instance_id": "b", "resolved": False},
        ]
        candidate = [
            {"instance_id": "a", "resolved": True},
            {"instance_id": "b", "resolved": False},
        ]
        first = paired_bootstrap(baseline, candidate, samples=1000, seed=7)
        second = paired_bootstrap(baseline, candidate, samples=1000, seed=7)
        self.assertEqual(first, second)
        self.assertEqual(first["delta"], 0.5)


if __name__ == "__main__":
    unittest.main()
