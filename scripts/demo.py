#!/usr/bin/env python3
"""Offline end-to-end controller demonstration, not a live bot/model quality test."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from grokteam.engine import Engine
from examples.human_fixture import HumanFixture


def chat_grant(engine, action, target, message_ref, payload=None):
    """Simulate a person choosing the affirmative card button."""
    engine.actor = "lead"
    plan = engine.confirmation_describe(action, target, payload)
    response = {"asked": plan["question"], "message_ref": message_ref, "answer": plan["buttons"]["yes"]}
    argv = [sys.executable, "-B", str(ROOT / "teamctl.py"), "--workspace", str(engine.store.root),
            "--actor", "lead", "confirm", "chat", "--action", action, "--target", target, "--hash", plan["hash"],
            "--file", "-"]
    issued = subprocess.run(argv, input=json.dumps(response), capture_output=True, text=True)
    assert issued.returncode == 0, issued.stderr
    engine.confirmation_code = json.loads(issued.stdout)["code"]
    engine.actor = "user"


def relay_handoff(engine):
    engine.actor = "lead"
    for op in engine.pending():
        assert op["kind"] == "send" and op["status"] == "pending", op
        engine.bridge_begin(op["id"])
        recipient = engine.store.need("role", op["target"])
        engine.bridge_ack(op["id"], {"agent_id": recipient["agent_id"],
                                    "external_ref": "demo-delivery:" + op["id"]})


def commit_snapshot(project, message):
    subprocess.run(["git", "add", "."], cwd=project, check=True)
    subprocess.run(["git", "-c", "user.name=Demo", "-c", "user.email=demo@example.invalid",
                    "-c", "commit.gpgsign=false", "commit", "-qm", message], cwd=project, check=True)
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project, text=True).strip()


def demo():
    with tempfile.TemporaryDirectory(prefix="grok-team-demo-") as directory:
        base = Path(directory)
        e = Engine(base / "runtime", actor="installer")
        try:
            e.install_options("mock-primary")
            e.install_options("mock-primary", "approval")
            installed = e.mock_install(section_id="demo-folder")
            assert installed["placements"] == {"developer": "demo-folder", "reviewer": "demo-folder"}
            e.mock_install()  # idempotent resume, still exactly two created agents
            project = base / "project"
            project.mkdir()
            (project / "calc.py").write_text("def add(a,b): return abs(a+b)\n")
            (project / "verify.py").write_text("from calc import add\nassert add(2,3)==5\n")
            (project / ".gitignore").write_text("__pycache__/\n")
            subprocess.run(["git", "init", "-q", str(project)], check=True)
            subprocess.run(["git", "add", "."], cwd=str(project), check=True)
            e.actor = "lead"
            assert e.status()["team"]["status"] == "ready"
            binding = {"repository": str(project), "command": [sys.executable, "-B", "verify.py"],
                       "evidence": "demo:user-project"}
            chat_grant(e, "project.bind", "project", "demo:bind-approved", binding)
            e.project_bind(**binding)
            e.actor = "lead"
            task = e.task_create({"request_id": "demo:task", "user_ref": "demo:user-task",
                                  "goal": "Correct signed addition", "criteria": [
                                      {"id": "positive", "text": "2+3=5"},
                                      {"id": "negative", "text": "-2+-3=-5"}]})["id"]
            e.task_action(task, "plan", {"event_id": "plan", "steps": ["implement", "verify", "review"]})
            relay_handoff(e)
            docs = {k: {"not_needed": "Local arithmetic fixture has no documentation change"}
                    for k in ("architecture", "user", "agent")}
            e.actor = "developer"
            commit = commit_snapshot(project, "Implement addition")
            e.project_verify(task)
            e.task_action(task, "submit", {"event_id": "submit-1", "summary": "initial addition", "docs": docs, "commit": commit})
            relay_handoff(e)
            e.actor = "reviewer"
            e.reviewer_check_add([sys.executable, "-B", "-c", "from calc import add; assert add(-2,-3)==-5"])
            negative = e.project_verify(task)
            assert not negative["passed"] and negative["additional_results"][0]["returncode"] != 0
            e.task_action(task, "review", {"event_id": "review-1", "verdict": "changes_requested",
                                           "findings": ["negative criterion fails: abs returns positive"]})
            relay_handoff(e)
            (project / "calc.py").write_text("def add(a,b): return a+b\n")
            (project / "verify.py").write_text("from calc import add\nassert add(2,3)==5\nassert add(-2,-3)==-5\n")
            e.actor = "developer"
            commit = commit_snapshot(project, "Fix signed addition")
            e.project_verify(task)
            e.task_action(task, "submit", {"event_id": "submit-2", "summary": "signed arithmetic fixed", "docs": docs,
                                           "commit": commit})
            relay_handoff(e)
            e.actor = "reviewer"; check = e.project_verify(task)
            assert check["passed"]
            e.task_action(task, "review", {"event_id": "review-2", "verdict": "pass", "criteria": [
                {"id": "positive", "passed": True, "evidence": check["id"]},
                {"id": "negative", "passed": True, "evidence": check["id"]}]})
            relay_handoff(e)
            e.actor = "lead"
            e.task_action(task, "demo", {"event_id": "demo", "steps": ["Run signed examples"], "usage": "add(a,b)"})
            acceptance = {"event_id": "accept", "evidence": "demo:user-task"}
            chat_grant(e, "task.accept", task, "demo:user-acceptance", acceptance)
            e.task_action(task, "accept", acceptance)
            config = {"suite": json.loads((ROOT / "examples/fixture-suite.json").read_text()),
                      "command": [sys.executable, "-B", str(ROOT / "examples/fixture_runner.py")],
                      "cwd": str(ROOT), "model_id": "TEST-DOUBLE-NOT-A-MODEL", "environment_id": "demo-only"}
            HumanFixture(e).evaluation_configure("developer", config, "demo:user-fixture-authorized")
            body = (ROOT / "bots/developer/behavior.md").read_text() + "\nPRESERVE_NUMBERS"
            HumanFixture(e).policy_set("autonomous", evidence="demo:user-autonomy")
            e.actor = "developer"
            first = e.improve_propose("developer", body, "Preserve numbers", ["demo:failure"])
            e.actor = "reviewer"
            first = e.improve_evaluate(first["id"])
            assert first["status"] == "trial", (first["status"], first.get("reason"), first.get("partial_results"))
            first = e.improve_observe(first["id"], False, True, "demo:real-trial-failure")
            assert first["status"] == "rolled_back"
            e.actor = "user"; HumanFixture(e).policy_set("approval", evidence="demo:user-approval-mode")
            e.actor = "developer"
            second = e.improve_propose("developer", body, "Preserve numbers with approval", ["demo:failure"])
            e.actor = "reviewer"; second = e.improve_evaluate(second["id"])
            assert second["status"] == "awaiting_approval", (second["status"], second.get("reason"),
                                                              second.get("partial_results"))
            chat_grant(e, "improve.approve", second["id"], "demo:user-approved-exact-version")
            second = e.improve_approve(second["id"], "demo:user-approved-exact-version")
            e.actor = "reviewer"
            e.improve_observe(second["id"], True, False, "demo:trial-success-1")
            second = e.improve_observe(second["id"], True, False, "demo:trial-success-2")
            assert second["status"] == "trial" and second["awaiting_acceptance"]
            chat_grant(e, "improve.accept", second["id"], "demo:user-accepted-trial")
            second = e.improve_accept(second["id"], "demo:user-accepted-trial")
            assert second["status"] == "accepted"
            conversation = next(event["data"] for event in e.store.events(second["id"])
                                if event["kind"] == "confirmation.issued"
                                and event["data"]["action"] == "improve.approve")
            return {"kind": "offline-controller-demo", "live_platform": False, "real_model": False,
                    "roles": len(e.store.all("role")), "created_agents": 2,
                    "task": e.store.need("task", task)["status"], "review_rounds": 2,
                    "autonomous_experiment": first["status"], "approval_experiment": second["status"],
                    "chat_confirmation": {key: conversation[key] for key in
                                          ("message_ref", "asked", "answer", "intent_hash", "channel")},
                    "developer_instruction_versions": len(e.store.history("developer"))}
        finally:
            e.close()


if __name__ == "__main__":
    print(json.dumps(demo(), ensure_ascii=False, indent=2))
