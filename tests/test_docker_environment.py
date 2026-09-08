import os
import shutil
import subprocess
import unittest

from repo_agent.environment.docker import DockerEnvironment, DockerLimits
from tests.support import WorkspaceCase


class DockerConfigurationTests(unittest.TestCase):
    def test_limits_reject_invalid_resources(self):
        with self.assertRaises(ValueError):
            DockerLimits(cpus=0)
        with self.assertRaises(ValueError):
            DockerLimits(pids=1)

    def test_start_command_contains_security_boundaries(self):
        environment = DockerEnvironment.__new__(DockerEnvironment)
        environment.local = type("Local", (), {"workspace": __import__("pathlib").Path("C:/safe/repo")})()
        environment.image = "python:3.11-slim"
        environment.name = "lcc-agent-test"
        environment.limits = DockerLimits()
        environment.user = "1000:1000"
        command = environment._start_command()
        joined = " ".join(command)
        self.assertIn("--network none", joined)
        self.assertIn("--read-only", command)
        self.assertIn("--cap-drop ALL", joined)
        self.assertIn("no-new-privileges", joined)
        self.assertNotIn("docker.sock", joined)
        self.assertEqual(command[command.index("--user") + 1], "1000:1000")


@unittest.skipUnless(os.getenv("REPO_AGENT_DOCKER_TEST") == "1" and shutil.which("docker"),
                     "set REPO_AGENT_DOCKER_TEST=1 on a Docker host")
class DockerSmokeTests(WorkspaceCase):
    def test_shell_is_networkless_and_writes_only_workspace(self):
        subprocess.run(["docker", "pull", "alpine:3.20"], check=True, capture_output=True)
        environment = DockerEnvironment(self.repo, image="alpine:3.20", command_timeout=20)
        try:
            result = environment.execute("printf container > docker.txt && id -u")
            self.assertEqual(result.exit_code, 0, result.stderr)
            self.assertNotEqual(result.stdout.strip(), "0")
            self.assertEqual((self.repo / "docker.txt").read_text(), "container")
            inspect = subprocess.run(["docker", "inspect", environment.name], check=True,
                                     capture_output=True, text=True).stdout
            self.assertIn('"NetworkMode": "none"', inspect)
        finally:
            environment.cancel_all()
