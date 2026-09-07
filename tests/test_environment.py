from __future__ import annotations

import shutil
import subprocess
import unittest
import uuid
from pathlib import Path

from repo_agent.environment import EnvironmentError, LocalEnvironment


class LocalEnvironmentTest(unittest.TestCase):
    def test_file_operations_cannot_escape_workspace(self) -> None:
        test_root = Path(__file__).resolve().parent / ".work" / uuid.uuid4().hex
        test_root.mkdir(parents=True)
        try:
            environment = LocalEnvironment(test_root)
            environment.write_file("nested/example.txt", "before")
            self.assertEqual(environment.read_file("nested/example.txt"), "before")
            environment.edit_file("nested/example.txt", "before", "after")
            self.assertEqual(environment.read_file("nested/example.txt"), "after")
            with self.assertRaises(EnvironmentError):
                environment.read_file("../outside.txt")
            with self.assertRaises(EnvironmentError):
                environment.execute("echo nope", cwd="..")
        finally:
            shutil.rmtree(test_root, ignore_errors=True)

    def test_diff_includes_untracked_files(self) -> None:
        test_root = Path(__file__).resolve().parent / ".work" / uuid.uuid4().hex
        test_root.mkdir(parents=True)
        try:
            subprocess.run(["git", "init"], cwd=test_root, check=True, capture_output=True)
            environment = LocalEnvironment(test_root)
            environment.write_file("new_module.py", "VALUE = 42\n")
            patch = environment.get_diff()
            self.assertIn("new file mode", patch)
            self.assertIn("VALUE = 42", patch)
            self.assertIn("new_module.py", patch)
        finally:
            shutil.rmtree(test_root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
