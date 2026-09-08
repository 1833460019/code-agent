from __future__ import annotations

import json
import os
import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from repo_agent.storage import atomic_json


class StorageTest(unittest.TestCase):
    def test_atomic_json_retries_transient_permission_errors(self) -> None:
        root = Path(__file__).resolve().parent / ".work" / uuid.uuid4().hex
        root.mkdir(parents=True)
        target = root / "state.json"
        real_replace = os.replace
        calls = 0

        def flaky_replace(source, destination):
            nonlocal calls
            calls += 1
            if calls < 3:
                raise PermissionError("temporary Windows file lock")
            return real_replace(source, destination)

        try:
            with patch("repo_agent.storage.os.replace", side_effect=flaky_replace):
                atomic_json(target, {"status": "saved"})
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"status": "saved"})
            self.assertEqual(calls, 3)
            self.assertEqual(list(root.glob("*.tmp")), [])
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
