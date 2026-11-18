import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from grokteam import bundle
from grokteam.engine import Engine
from grokteam.process import snapshot, run
from grokteam.store import Store, TeamError
from examples.human_fixture import HumanFixture, grant

ROOT = Path(__file__).resolve().parents[1]


class RuntimeCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="grok-team-test-")
        self.base = Path(self.tmp.name)
        self.engine = Engine(self.base / "runtime", actor="installer")
        self.engine.mock_install()

    def tearDown(self):
        self.engine.close()
        self.tmp.cleanup()

    def as_role(self, role):
        self.engine.actor = role
        return HumanFixture(self.engine) if role == "user" else self.engine

    def project(self):
        root = self.base / "project"
        root.mkdir()
        (root / "calc.py").write_text("def add(a,b): return a+b\n")
        (root / "verify.py").write_text("from calc import add\nassert add(2,3)==5\nprint('verified')\n")
        (root / ".gitignore").write_text("__pycache__/\n")
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(["git", "add", "."], cwd=str(root), check=True)
        self.as_role("lead").project_bind(str(root), [sys.executable, "-B", "verify.py"], "user:project")
        return root

    def task(self, auto=False):
        request = {"request_id": "user:task1", "user_ref": "user:task1", "goal": "Add two numbers",
                   "criteria": [{"id": "c1", "text": "2+3=5"}], "max_rounds": 2}
        if auto:
            request.update(auto_accept=True, auto_accept_ref="user:explicit-auto")
            grant(self.engine, "task.auto_accept", "task-" + bundle.digest(request["request_id"])[:16], request)
        task = self.as_role("lead").task_create(request)
        self.engine.task_action(task["id"], "plan", {"event_id": "plan1", "steps": ["implement", "verify"]})
        return task["id"]

    def submit(self, task):
        check = self.as_role("developer").project_verify(task)
        self.assertTrue(check["passed"])
        data = {"event_id": "submit1", "summary": "Arithmetic implemented", "docs": {
            kind: {"not_needed": "No public interface change"} for kind in ("architecture", "user", "agent")}}
        return self.engine.task_action(task, "submit", data)

    def review(self, task):
        self.as_role("reviewer").project_verify(task)
        return self.engine.task_action(task, "review", {"event_id": "review1", "verdict": "pass",
                "criteria": [{"id": "c1", "passed": True, "evidence": "independent verification 2+3=5"}]})

    def configure(self, role="developer"):
        suite = json.loads((ROOT / "examples/fixture-suite.json").read_text())
        config = {"suite": suite, "command": [sys.executable, "-B", str(ROOT / "examples/fixture_runner.py")],
                  "cwd": str(ROOT), "model_id": "TEST-DOUBLE-NOT-A-MODEL", "environment_id": "isolated-fixture"}
        self.as_role("user").evaluation_configure(role, config, "user:authorize-fixture-only")
        return config

    def propose(self, role="developer", extra="PRESERVE_NUMBERS"):
        self.configure(role)
        body = (ROOT / "bots" / role / "behavior.md").read_text() + "\n" + extra
        return self.as_role(role).improve_propose(role, body, "Preserve numeric inputs", ["task:failure1", "task:failure2"])

    def evaluated(self, mode="approval", role="developer"):
        if mode != "approval":
            self.as_role("user").policy_set(mode, evidence="user:mode")
        exp = self.propose(role)
        return self.as_role("lead" if role == "reviewer" else "reviewer").improve_evaluate(exp["id"])


class InstallTests(RuntimeCase):
    def test_exactly_two_created_and_resume_idempotent(self):
        e = self.as_role("installer")
        self.assertEqual(len(e.store.all("role")), 3)
        self.assertEqual(len([x for x in e.store.all("operation") if x["kind"] == "create"]), 2)
        versions = sum(len(e.store.history(r)) for r in bundle.ROLES)
        e.mock_install()
        self.assertEqual(versions, sum(len(e.store.history(r)) for r in bundle.ROLES))
        self.assertEqual(e.team()["lead_id"], "mock-primary")

    def test_existing_workspace_rejects_other_lead(self):
        with self.assertRaises(TeamError): self.as_role("installer").mock_install("another-primary")

    def test_native_operations_require_real_receipt_and_reconcile(self):
        e = Engine(self.base / "native", actor="installer")
        try:
            e.install("native-primary")
            op = e.bridge_begin("install-create-developer")
            with self.assertRaises(TeamError): e.bridge_begin(op["id"])
            with self.assertRaises(TeamError): e.bridge_ack(op["id"], {"agent_id": "d"})
            receipt = {"agent_id": "native-dev", "external_ref": "native-tool:123"}
            e.bridge_ack(op["id"], receipt)
            e.bridge_ack(op["id"], receipt)
            self.assertEqual(e.store.need("role", "developer")["agent_id"], "native-dev")
            with self.assertRaises(TeamError): e.install_finish()
        finally: e.close()

    def test_bind_rejects_mismatched_prompt(self):
        e = Engine(self.base / "native", actor="installer")
        try:
            e.install("native-primary")
            e.bridge_begin("install-bind-lead")
            e.actor = "lead"; probe = e.role_probe("lead"); e.actor = "installer"
            probe.update(external_ref="tool:probe", persistent=True, hash="wrong")
            with self.assertRaises(TeamError): e.bridge_ack("install-bind-lead", probe)
            self.assertFalse(e.store.need("role", "lead")["bound"])
        finally: e.close()

    def test_reviewer_negative_smoke(self):
        smoke = self.engine.store.need("smoke", "reviewer")
        self.assertTrue(smoke["positive"]["passed"])
        self.assertFalse(smoke["negative"]["passed"])
        self.assertNotEqual(smoke["negative"]["results"][0]["returncode"], 0)
        self.assertTrue(smoke["passed"])

    def test_role_probe_and_skills(self):
        data = self.as_role("developer").role_read("developer")
        self.assertEqual(len(data["skills"]), 5)
        self.assertIn("project-verification", {s["name"] for s in data["skills"]})
        with self.assertRaises(TeamError): self.engine.role_read("reviewer")


class PolicyTests(RuntimeCase):
    def test_corrupt_policy_cannot_fall_through_to_autonomy(self):
        with self.engine.store.transaction():
            self.engine.store.put("config", "policy", {"revision": 99, "system_mode": "broken", "bot_modes": {}})
        with self.assertRaises(TeamError): self.engine.mode("developer")

    def test_global_overrides_and_inherit(self):
        e = self.as_role("user")
        e.policy_set("autonomous", evidence="user:1")
        e.policy_set("approval", "reviewer", "user:2")
        self.assertEqual(e.mode("developer")["mode"], "autonomous")
        self.assertEqual(e.mode("reviewer")["mode"], "approval")
        e.policy_set("approval", evidence="user:3")
        e.policy_set("autonomous", "developer", "user:4")
        self.assertEqual(e.mode("developer")["source"], "bot")
        e.policy_set("inherit", "developer", "user:5")
        self.assertEqual(e.mode("developer")["mode"], "approval")
        self.assertEqual(e.mode("developer")["source"], "system")

    def test_bot_cannot_change_mode(self):
        with self.assertRaises(TeamError): self.as_role("lead").policy_set("autonomous", evidence="bot:says-yes")
        with self.assertRaises(TeamError): self.as_role("user").policy_set("invalid", evidence="user")


class TaskTests(RuntimeCase):
    def test_full_cycle_with_human_acceptance(self):
        self.project(); task = self.task()
        self.submit(task); reviewed = self.review(task)
        self.assertEqual(reviewed["status"], "ready_for_demo")
        demo = self.as_role("lead").task_action(task, "demo", {"event_id": "demo1", "steps": ["run verifier"], "usage": "add(2,3)"})
        self.assertEqual(demo["status"], "awaiting_acceptance")
        with self.assertRaises(TeamError): self.engine.task_action(task, "accept", {"event_id": "accept1", "evidence": "bot:yes"})
        accepted = self.as_role("user").task_action(task, "accept", {"event_id": "accept1", "evidence": "user:yes"})
        self.assertEqual(accepted["status"], "done")
        self.assertEqual(self.as_role("user").task_action(task, "accept", {"event_id": "accept1", "evidence": "user:yes"})["status"], "done")

    def test_auto_accept_requires_explicit_user_reference(self):
        self.project(); task = self.task(auto=True)
        self.submit(task); self.review(task)
        result = self.as_role("lead").task_action(task, "demo", {"event_id": "demo1", "steps": ["verify"], "usage": "add"})
        self.assertEqual(result["status"], "done")

    def test_developer_cannot_approve(self):
        self.project(); task = self.task(); self.submit(task)
        with self.assertRaises(TeamError): self.as_role("developer").task_action(task, "review", {"event_id": "bad", "verdict": "pass"})

    def test_reviewer_needs_own_run(self):
        self.project(); task = self.task(); self.submit(task)
        with self.assertRaises(TeamError): self.as_role("reviewer").task_action(task, "review", {
            "event_id": "review1", "verdict": "pass", "criteria": [{"id": "c1", "passed": True, "evidence": "developer said"}]})

    def test_changed_source_invalidates_review(self):
        project = self.project(); task = self.task(); self.submit(task)
        (project / "calc.py").write_text("def add(a,b): return a-b\n")
        self.as_role("reviewer").project_verify(task)
        with self.assertRaises(TeamError): self.engine.task_action(task, "review", {"event_id": "review1", "verdict": "pass"})

    def test_changed_after_review_invalidates_demo(self):
        project = self.project(); task = self.task(); self.submit(task); self.review(task)
        (project / "calc.py").write_text("def add(a,b): return 0\n")
        with self.assertRaises(TeamError): self.as_role("lead").task_action(task, "demo", {"event_id": "demo", "steps": ["run"], "usage": "x"})

    def test_failing_verification_is_not_pass(self):
        project = self.project(); task = self.task()
        (project / "calc.py").write_text("def add(a,b): return 0\n")
        self.assertFalse(self.as_role("developer").project_verify(task)["passed"])
        with self.assertRaises(TeamError): self.engine.task_action(task, "submit", {"event_id": "bad"})

    def test_untracked_files_are_not_invisible(self):
        project = self.project()
        (project / "hidden.py").write_text("raise RuntimeError('untracked')")
        with self.assertRaises(TeamError): snapshot(project)

    def test_idempotent_request_rejects_content_change(self):
        self.project(); task = self.task()
        request = self.engine.store.need("task", task)["request"]
        self.assertEqual(self.as_role("lead").task_create(request)["id"], task)
        with self.assertRaises(TeamError): self.engine.task_create({**request, "goal": "different"})

    def test_missing_documentation_blocks_submission(self):
        self.project(); task = self.task(); self.as_role("developer").project_verify(task)
        with self.assertRaises(TeamError): self.engine.task_action(task, "submit", {"event_id": "bad", "summary": "done", "docs": {}})


class ImprovementTests(RuntimeCase):
    def test_each_evaluation_call_is_journalled(self):
        exp = self.evaluated()
        events = self.engine.store.events(exp["id"])
        started = [x for x in events if x["kind"] == "evaluation.call_started"]
        completed = [x for x in events if x["kind"] == "evaluation.call_completed"]
        self.assertEqual(len(started), 8)
        self.assertEqual(len(completed), 8)
        self.assertEqual(len(exp["partial_results"]), 8)

    def test_late_observation_cannot_save_expired_trial(self):
        exp = self.evaluated("autonomous")
        self.engine.clock = lambda: exp["deadline"] + 1
        result = self.as_role("reviewer").improve_observe(exp["id"], True, False, "late:success")
        self.assertEqual(result["status"], "rolled_back")

    def test_evaluation_call_budget_is_checked_before_running(self):
        config = self.configure()
        config["max_calls"] = 1
        with self.assertRaises(TeamError): self.as_role("user").evaluation_configure("developer", config, "user:budget")

    def test_repeated_final_observation_is_idempotent(self):
        exp = self.evaluated("autonomous")
        self.as_role("reviewer").improve_observe(exp["id"], True, False, "success:1")
        self.engine.improve_observe(exp["id"], True, False, "success:2")
        result = self.engine.improve_observe(exp["id"], True, False, "success:2")
        self.assertEqual(result["status"], "accepted")

    def test_approval_then_trial_and_acceptance(self):
        exp = self.evaluated()
        self.assertEqual(exp["status"], "awaiting_approval")
        self.assertEqual(self.engine.store.need("role", "developer")["current"], exp["baseline"])
        with self.assertRaises(TeamError): self.as_role("reviewer").improve_approve(exp["id"], "bot:yes")
        exp = self.as_role("user").improve_approve(exp["id"], "user:yes")
        self.assertEqual(exp["status"], "trial")
        self.as_role("reviewer").improve_observe(exp["id"], True, False, "task:1")
        waiting = self.engine.improve_observe(exp["id"], True, False, "task:2")
        self.assertEqual(waiting["status"], "trial")
        self.assertTrue(waiting["awaiting_acceptance"])
        accepted = self.as_role("user").improve_accept(exp["id"], "user:accept-trial")
        self.assertEqual(accepted["status"], "accepted")

    def test_autonomous_does_not_ask_for_approval(self):
        exp = self.evaluated("autonomous")
        self.assertEqual(exp["status"], "trial")
        self.assertIsNone(exp["approval"])

    def test_individual_approval_overrides_global_autonomous(self):
        self.as_role("user").policy_set("autonomous", evidence="user:global")
        self.as_role("user").policy_set("approval", "developer", "user:override")
        self.assertEqual(self.evaluated()["status"], "awaiting_approval")

    def test_individual_autonomy_overrides_global_approval(self):
        self.as_role("user").policy_set("autonomous", "developer", "user:override")
        self.assertEqual(self.evaluated()["status"], "trial")

    def test_critical_violation_rolls_back_exact_prompt(self):
        exp = self.evaluated("autonomous")
        restored = self.as_role("reviewer").improve_observe(exp["id"], False, True, "task:critical")
        self.assertEqual(restored["status"], "rolled_back")
        current = self.engine.store.version_get(self.engine.store.need("role", "developer")["current"])
        self.assertEqual(current["hash"], exp["baseline_hash"])
        self.assertEqual(len(self.engine.store.history("developer")), 3)

    def test_trial_timeout_survives_restart(self):
        exp = self.evaluated("autonomous")
        workspace = self.engine.store.root
        self.engine.close()
        self.engine = Engine(workspace, actor="controller", clock=lambda: exp["deadline"] + 1)
        self.engine.sweep()
        self.assertEqual(self.engine.store.need("experiment", exp["id"])["status"], "rolled_back")

    def test_regression_rejects_before_application(self):
        self.as_role("user").policy_set("autonomous", evidence="user:mode")
        exp = self.propose(extra="PRESERVE_NUMBERS BREAK_BOUNDARY")
        result = self.as_role("reviewer").improve_evaluate(exp["id"])
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(self.engine.store.need("role", "developer")["current"], exp["baseline"])

    def test_author_cannot_evaluate_self(self):
        exp = self.propose()
        with self.assertRaises(TeamError): self.as_role("developer").improve_evaluate(exp["id"])

    def test_reviewer_instruction_has_independent_evaluator(self):
        exp = self.evaluated("autonomous", role="reviewer")
        self.assertEqual(exp["evaluator"], "lead")
        self.assertEqual(exp["status"], "trial")

    def test_duplicate_observations_do_not_accept(self):
        exp = self.evaluated("autonomous")
        self.as_role("reviewer").improve_observe(exp["id"], True, False, "task:same")
        value = self.engine.improve_observe(exp["id"], True, False, "task:same")
        self.assertEqual(len(value["observations"]), 1)
        self.assertEqual(value["status"], "trial")

    def test_changed_policy_requires_new_approval(self):
        self.project(); task = self.task()
        exp = self.evaluated()
        with self.assertRaises(TeamError): self.as_role("user").improve_approve(exp["id"], "user:old-yes")
        # The recorded approval survives an idle deferral but becomes stale after policy revision.
        self.as_role("user").policy_set("approval", evidence="user:new-policy")
        self.as_role("lead").task_action(task, "cancel", {"event_id": "cancel", "reason": "pause"})
        value = self.as_role("controller").improve_progress(exp["id"])
        self.assertEqual(value["status"], "awaiting_approval")

    def test_cancel_trial_rolls_back(self):
        exp = self.evaluated("autonomous")
        self.assertEqual(self.as_role("user").improve_cancel(exp["id"], "user:stop")["status"], "rolled_back")

    def test_restore_historic_version_creates_history_entry(self):
        exp = self.evaluated("autonomous")
        self.as_role("user").improve_cancel(exp["id"], "user:stop")
        value = self.as_role("user").restore_version("developer", exp["baseline"], "user:restore")
        self.assertNotEqual(value["version"], exp["baseline"])
        self.assertEqual(value["hash"], exp["baseline_hash"])

    def test_external_instruction_conflict_not_overwritten(self):
        exp = self.evaluated("autonomous")
        with self.engine.store.transaction():
            row = self.engine.store.need("role", "developer")
            row["current"] = self.engine.store.version("developer", "external", "external mutation")
            self.engine.store.put("role", "developer", row)
        value = self.as_role("reviewer").improve_observe(exp["id"], False, True, "critical")
        self.assertEqual(value["status"], "blocked")
        with self.assertRaises(TeamError): self.as_role("developer").role_read("developer")

    def test_failed_runner_is_inconclusive_without_update(self):
        config = self.configure()
        config["command"] = [sys.executable, "-c", "raise RuntimeError('fail')"]
        self.as_role("user").evaluation_configure("developer", config, "user:new-runner")
        body = (ROOT / "bots/developer/behavior.md").read_text() + "\nPRESERVE_NUMBERS"
        exp = self.as_role("developer").improve_propose("developer", body, "numbers", ["failure"])
        value = self.as_role("reviewer").improve_evaluate(exp["id"])
        self.assertEqual(value["status"], "inconclusive")


class DurabilityTests(RuntimeCase):
    def test_one_off_sweep_is_not_a_running_watchdog(self):
        self.as_role("controller").sweep()
        self.assertFalse(self.engine.watchdog_alive())

    def test_real_watchdog_start_idempotent_and_stop(self):
        from grokteam import service
        import time
        self.as_role("installer")
        process = service.start(self.engine)
        try:
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline and not self.engine.watchdog_alive():
                time.sleep(0.05)
            self.assertTrue(self.engine.watchdog_alive())
            self.assertEqual(service.start(self.engine)["pid"], process["pid"])
        finally:
            service.stop(self.engine)
            deadline = time.monotonic() + 7
            while time.monotonic() < deadline:
                try:
                    finished, _ = os.waitpid(process["pid"], os.WNOHANG)
                    if finished: break
                except ChildProcessError: break
                time.sleep(0.05)

    def test_failed_transaction_does_not_leave_partial_state(self):
        with self.assertRaises(RuntimeError):
            with self.engine.store.transaction():
                self.engine.store.put("test", "partial", {"x": 1})
                self.engine.emit("test", "partial", {})
                raise RuntimeError("power loss")
        self.assertIsNone(self.engine.store.get("test", "partial"))
        self.assertEqual(self.engine.store.events("partial"), [])

    def test_second_writer_fails_without_overwrite(self):
        other = Store(self.engine.store.root)
        try:
            with self.engine.store.transaction():
                with self.assertRaises(TeamError):
                    with other.transaction(): pass
        finally: other.close()

    def test_process_timeout_is_failure(self):
        result = run([sys.executable, "-c", "import time; time.sleep(3)"], self.base, timeout=0.1)
        self.assertTrue(result["timeout"])
        self.assertNotEqual(result["returncode"], 0)


if __name__ == "__main__":
    unittest.main()
