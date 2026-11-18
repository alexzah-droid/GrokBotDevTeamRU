import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

from grokteam import bundle
from grokteam.engine import Engine
from grokteam.process import snapshot
from grokteam.store import TeamError
from test_runtime import ROOT, RuntimeCase


class InstallationChoicesTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.engine = Engine(Path(directory.name) / "state", actor="installer")
        self.addCleanup(self.engine.close)
        self.cards = bundle.texts(ROOT)["install_cards"]

    def test_default_and_resumed_approval(self):
        e = self.engine
        first = e.install_options("lead")
        self.assertEqual(first["mode_card"], self.cards["mode"])
        resumed = e.install_options("lead")
        self.assertEqual(resumed["mode"], "approval")
        self.assertNotIn("mode_card", resumed)
        self.assertEqual(e.install("lead")["install_mode"], "approval")
        self.assertEqual(e.install_options("lead"), {"mode": "approval", "resume": True})

    def test_autonomous_requires_the_bound_second_card_and_grant(self):
        e = self.engine
        first = e.install_options("lead")
        e.install_options("lead", "autonomous")
        self.assertEqual(e.install_options("lead")["mode"], "autonomous")
        with self.assertRaisesRegex(TeamError, "Human confirmation code"):
            e.install("lead", mode="autonomous", section_id="folder")
        self.assertIsNone(e.store.get("config", "team"))
        payload = {"mode": "autonomous", "adapter": "native", "source": bundle.repository(ROOT), "section_id": "folder"}
        plan = e.confirmation_describe("install", "lead", payload)
        second = self.cards["autonomous"]
        self.assertTrue(plan["question"].startswith("\n".join([second["question"], *second["warnings"]])))
        self.assertEqual(plan["buttons"], second["buttons"])
        with self.assertRaisesRegex(TeamError, "Asked text"):
            e.confirmation_chat("install", "lead", plan["hash"], "click", second["buttons"]["yes"],
                                first["mode_card"]["question"])
        grant = e.confirmation_chat("install", "lead", plan["hash"], "click", second["buttons"]["yes"], plan["question"])
        e.confirmation_code = grant["code"]
        with self.assertRaisesRegex(TeamError, "binding mismatch"):
            e.install("lead", mode="autonomous", section_id="other")
        self.assertEqual(e.install("lead", mode="autonomous", section_id="folder")["install_mode"], "autonomous")

    def test_folder_is_forwarded_and_fallback_is_recorded(self):
        e = self.engine
        self.assertEqual(e.install("lead", section_id="folder")["section_id"], "folder")
        creates = [op for op in e.pending() if op["kind"] == "create"]
        self.assertEqual(len(creates), 2)
        self.assertTrue(all(op["data"]["section_id"] == "folder" for op in creates))
        self.assertEqual(e.install("lead", section_id="other")["section_id"], "folder")
        action = creates[0]
        e.bridge_begin(action["id"])
        e.bridge_ack(action["id"], {"agent_id": "agent", "external_ref": "create:agent", "section_id": None})
        self.assertIsNone(e.store.need("role", action["target"])["section_id"])

    def test_cli_records_choice_and_section(self):
        e = self.engine
        base = [sys.executable, "-B", str(ROOT / "teamctl.py"), "--workspace", str(e.store.root), "--actor", "installer"]
        def cli(*argv):
            return json.loads(subprocess.check_output(base + list(argv), text=True))
        self.assertIn("mode_card", cli("install-options", "--lead-id", "lead"))
        self.assertEqual(cli("install-options", "--lead-id", "lead", "--choice", "approval")["mode"], "approval")
        finished = cli("install", "--mock", "--lead-id", "lead", "--section-id", "section-42")
        self.assertEqual(finished["placements"], {"developer": "section-42", "reviewer": "section-42"})


class PilotProjectTests(RuntimeCase):
    def test_snapshot_ignores_untracked_python_caches_without_writing_config(self):
        root = self.project()
        before = snapshot(root)
        for name in ("__pycache__/code.cache", "nested/.pytest_cache/state", "verify.pyc"):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("generated")
        (root / ".gitignore").unlink()
        subprocess.run(["git", "add", "-u"], cwd=root, check=True)
        without_ignore = snapshot(root)
        self.assertNotEqual(before, without_ignore)
        files = subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard"], cwd=root, text=True)
        self.assertIn("verify.pyc", files)
        (root / "verify.pyc").write_text("new generated content")
        self.assertEqual(snapshot(root), without_ignore)
        (root / "new_source.py").write_text("x=2")
        with self.assertRaisesRegex(TeamError, "Untracked"):
            snapshot(root)

    def test_approval_binding_grant_is_exact_and_repeat_is_idempotent(self):
        root = self.project().resolve()
        e = self.as_role("lead")
        original = e.store.need("config", "project")
        self.assertEqual(e.project_bind(str(root), original["command"], "retry"), original)
        command = [sys.executable, "script with spaces.py", "--limit", "x y"]
        payload = {"repository": str(root), "command": command, "evidence": "user:changed"}
        with self.assertRaisesRegex(TeamError, "Human confirmation code"):
            e.project_bind(**payload)
        plan = e.confirmation_describe("project.bind", "project", payload)
        lines = plan["question"].splitlines()
        words = bundle.texts(ROOT)
        self.assertIn(f'{words["parameter_labels"]["repository"]}: {root}', lines)
        self.assertIn(f'{words["parameter_labels"]["command"]}: {shlex.join(command)}', lines)
        self.assertIn(words["card"]["hash"].format(hash=plan["hash"][:8]), lines)
        self.assertNotIn(plan["hash"], plan["question"])
        self.assertNotIn(json.dumps(command), plan["question"])
        result = e.confirmation_chat("project.bind", "project", plan["hash"], "reply", plan["buttons"]["yes"], plan["question"])
        e.confirmation_code = result["code"]
        with self.assertRaisesRegex(TeamError, "binding mismatch"):
            e.project_bind(**{**payload, "command": [sys.executable, "other.py"]})
        self.assertEqual(e.project_bind(**payload)["command"], command)
        e.confirmation_code = None
        self.assertEqual(e.project_bind(**payload)["command"], command)

    def test_submission_commit_must_match_the_verified_snapshot(self):
        root = self.project()
        task = self.task()
        e = self.as_role("developer")
        e.project_verify(task)
        data = {"summary": "Initial implementation", "event_id": "submit1", "commit": "bad",
                "docs": {kind: {"not_needed": "No interface change"} for kind in ("architecture", "user", "agent")}}
        with self.assertRaisesRegex(TeamError, "commit must match"):
            e.task_action(task, "submit", data)
        subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                        "-c", "commit.gpgsign=false", "commit", "-qm", "Task implementation"], cwd=root, check=True)
        data["commit"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
        self.assertEqual(e.task_action(task, "submit", data)["submission"]["commit"], data["commit"])
