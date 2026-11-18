from pathlib import Path
import time

from . import bundle
from .process import run, snapshot, verify_profile
from .store import digest, encode, require, uid


class Tasks:
    def reviewer_checks(self, project_id):
        return self.store.get("reviewer_checks", project_id, {"commands": []})["commands"]

    def reviewer_check_add(self, command):
        self.need_actor("reviewer")
        require(isinstance(command, list) and command and all(isinstance(x, str) and x for x in command), "Extra check needs argv")
        with self.store.transaction():
            project = self.store.need("config", "project")
            checks = self.reviewer_checks(project["id"])
            if command not in checks:
                require(len(checks) < 8, "At most 8 supplemental checks")
                checks.append(command)
                self.store.put("reviewer_checks", project["id"], {"commands": checks})
                project["status"] = "unverified"
                self.store.put("config", "project", project)
                self.emit("reviewer.check_added", project["id"], {"command": command, "profile_hash": digest(checks)})
        return {"commands": checks, "profile_hash": digest(checks)}

    def project_bind(self, repository, command, evidence, timeout=60):
        self.need_actor("user", "lead")
        self.team(ready=True)
        require(evidence, "Project authorization/message reference required")
        root = Path(repository).expanduser().resolve()
        require(root != self.store.root and not self.store.root.is_relative_to(root),
                "Runtime workspace must be outside the project repository")
        fingerprint = snapshot(root)
        require(isinstance(command, list) and command and all(isinstance(x, str) for x in command), "JSON argv required")
        require(0 < timeout <= 300, "Invalid timeout")
        with self.store.transaction():
            self.require_idle("lead")
            if self.actor == "user":
                self.require_confirmation("project.bind", "project", {"repository": str(root), "command": command,
                                          "timeout": timeout, "evidence": evidence})
            project = {"repository": str(root), "command": command, "timeout": timeout,
                       "id": digest([str(root), command]), "status": "unverified", "evidence": evidence}
            self.store.put("config", "project", project)
            self.emit("project.bound", project["id"], {**project, "snapshot": fingerprint})
        return project

    def project_verify(self, task_id=None):
        self.need_actor("developer", "reviewer")
        self.team(ready=True)
        project = self.store.need("config", "project")
        if task_id:
            task = self.store.need("task", task_id)
            require(task["status"] in ("implementing", "changes_requested", "review"), "Task not in verification phase")
            require(task["project_id"] == project["id"], "Project changed")
            if self.actor == "reviewer":
                require(task["status"] == "review", "Reviewer verifies only submitted results")
        additional = self.reviewer_checks(project["id"]) if self.actor == "reviewer" else []
        verification = verify_profile(project["repository"], [project["command"], *additional], project["timeout"])
        before, result = verification["snapshot"], verification["results"][0]
        value = {"id": uid("run"), "role": self.actor, "project_id": project["id"],
                 "snapshot": before, "task_id": task_id, "result": result,
                 "passed": verification["passed"], "additional_results": verification["results"][1:],
                 "extra_profile_hash": digest(additional),
                 "instruction_version": self.store.need("role", self.actor)["current"], "at": self.clock()}
        with self.store.transaction():
            require(self.store.need("config", "project")["id"] == project["id"], "Project changed during verification")
            if self.actor == "reviewer":
                require(digest(self.reviewer_checks(project["id"])) == digest(additional), "Supplemental profile changed during verification")
            self.store.put("run", value["id"], value)
            self.emit("verification.completed", task_id or project["id"], value)
            checks = {r["role"] for r in self.store.all("run") if r["project_id"] == project["id"]
                      and r["snapshot"] == before and r["passed"] and (r["role"] != "reviewer" or
                          r.get("extra_profile_hash") == digest(self.reviewer_checks(project["id"])))}
            if checks == {"developer", "reviewer"}:
                project["status"] = "ready"
                self.store.put("config", "project", project)
        return value

    def task_create(self, request):
        self.need_actor("lead", "user")
        self.team(ready=True)
        project = self.store.need("config", "project")
        require(isinstance(request, dict), "Task must be an object")
        require(request.get("request_id") and request.get("user_ref"), "request_id and user_ref required")
        require(isinstance(request.get("goal"), str) and request["goal"].strip(), "Goal required")
        criteria = request.get("criteria")
        require(isinstance(criteria, list) and criteria, "Non-empty acceptance criteria required")
        require(all(isinstance(c, dict) and c.get("id") and c.get("text") for c in criteria), "Criteria need id/text")
        require(len({c["id"] for c in criteria}) == len(criteria), "Duplicate criterion IDs")
        max_rounds = request.get("max_rounds", 3)
        require(isinstance(max_rounds, int) and 1 <= max_rounds <= 10, "max_rounds must be 1..10")
        require(not request.get("auto_accept") or request.get("auto_accept_ref"), "Automatic acceptance needs explicit user reference")
        key = "task-" + digest(request["request_id"])[:16]
        with self.store.transaction():
            if request.get("auto_accept"):
                self.require_confirmation("task.auto_accept", key, request)
            elif self.actor == "user":
                self.require_confirmation("task.create", key, request)
            old = self.store.get("task", key)
            if old:
                require(old["request_hash"] == digest(request), "Duplicate request_id with different task")
                return old
            task = {"id": key, "request": request, "request_hash": digest(request),
                    "status": "received", "revision": 1, "requirements_revision": 1,
                    "project_id": project["id"], "rounds": 0, "max_rounds": max_rounds,
                    "created": self.clock()}
            self.store.put("task", key, task)
            self.emit("task.created", key, task)
        return task

    def _last_run(self, task, role, fingerprint):
        runs = [r for r in self.store.all("run") if r["role"] == role and r["task_id"] == task["id"]
                and r["snapshot"] == fingerprint and r["passed"] and r["project_id"] == task["project_id"]
                and (role != "reviewer" or r.get("extra_profile_hash") == digest(self.reviewer_checks(task["project_id"])))]
        require(runs, "Independent successful verification for this role/task/snapshot required")
        return max(runs, key=lambda r: r["at"])

    def task_action(self, task_id, action, data):
        self.team(ready=True)
        require(isinstance(data, dict), "Action payload must be an object")
        with self.store.transaction():
            task = self.store.need("task", task_id)
            previous = task["status"]
            project = self.store.need("config", "project")
            require(task["project_id"] == project["id"], "Project changed")
            event_id = data.get("event_id")
            require(event_id, "event_id required for idempotent action")
            old = self.store.get("task_action", event_id)
            signature = digest([task_id, action, data, self.actor])
            if self.actor == "user" and action in ("accept", "block", "cancel"):
                self.require_confirmation("task." + action, task_id, data)
            if old:
                require(old["signature"] == signature, "Action ID reused with different data")
                return self.store.need("task", task_id)
            target = None
            if action == "plan":
                self.need_actor("lead")
                require(previous in ("received", "blocked"), "Cannot plan from " + previous)
                require(data.get("steps") and isinstance(data["steps"], list), "Plan steps required")
                require(task["rounds"] < task["max_rounds"], "Task iteration budget exhausted")
                task["plan"] = data
                task["versions"] = {r: self.store.need("role", r)["current"] for r in bundle.ROLES}
                task["status"] = "implementing"
                target = "developer"
            elif action == "submit":
                self.need_actor("developer")
                require(previous in ("implementing", "changes_requested"), "Cannot submit from " + previous)
                require(task["rounds"] < task["max_rounds"], "Task iteration budget exhausted")
                current = snapshot(project["repository"])
                own_run = self._last_run(task, "developer", current)
                docs = data.get("docs", {})
                for kind in ("architecture", "user", "agent"):
                    entry = docs.get(kind, {})
                    require(bool(entry.get("path")) != bool(entry.get("not_needed")), "Documentation path OR reason required: " + kind)
                    if entry.get("path"):
                        p = Path(project["repository"]) / entry["path"]
                        require(p.resolve().is_relative_to(Path(project["repository"])) and p.is_file(), "Invalid documentation path")
                        listed = run(["git", "ls-files", "--error-unmatch", "--", entry["path"]], project["repository"])
                        require(listed["returncode"] == 0, "Documentation must be tracked/staged")
                require(data.get("summary"), "Implementation summary required")
                task["submission"] = {**data, "snapshot": current, "developer_run": own_run["id"]}
                task["rounds"] += 1
                task["status"] = "review"
                target = "reviewer"
            elif action == "review":
                self.need_actor("reviewer")
                require(previous == "review", "No submitted version to review")
                current = snapshot(project["repository"])
                require(current == task["submission"]["snapshot"], "Implementation changed since submission")
                verdict = data.get("verdict")
                require(verdict in ("pass", "changes_requested", "blocked"), "Invalid verdict")
                if verdict == "pass":
                    check = self._last_run(task, "reviewer", current)
                    ids = {c["id"] for c in task["request"]["criteria"]}
                    criteria = data.get("criteria", [])
                    require(len(criteria) == len(ids) and {c.get("id") for c in criteria} == ids,
                            "Review must cover every criterion exactly once")
                    require(all(c.get("passed") is True and c.get("evidence") for c in criteria), "Every criterion needs evidence")
                    task["review"] = {**data, "snapshot": current, "reviewer_run": check["id"]}
                    task["status"] = "ready_for_demo"
                    target = "lead"
                else:
                    require(data.get("findings"), "Findings/blocker required")
                    task["review"] = {**data, "snapshot": current}
                    task["status"] = "blocked" if verdict == "blocked" or task["rounds"] >= task["max_rounds"] else "changes_requested"
                    target = "lead" if task["status"] == "blocked" else "developer"
                    self._task_trial_observation(task, False, "review:" + event_id)
            elif action == "demo":
                self.need_actor("lead")
                require(previous == "ready_for_demo", "Technical pass required before demo")
                require(snapshot(project["repository"]) == task["review"]["snapshot"], "Reviewed version changed")
                require(data.get("steps") and data.get("usage"), "Reproducible demo and usage required")
                task["demo"] = data
                task["status"] = "awaiting_acceptance"
                if task["request"].get("auto_accept"):
                    task["acceptance"] = {"actor": "user-delegated", "evidence": task["request"]["auto_accept_ref"]}
                    task["status"] = "done"
                    self._task_trial_observation(task, True, "accept:" + event_id)
            elif action == "accept":
                self.need_actor("user")
                require(previous == "awaiting_acceptance" and data.get("evidence"), "Human acceptance reference required")
                require(snapshot(project["repository"]) == task["review"]["snapshot"], "Reviewed result changed")
                task["acceptance"] = data
                task["status"] = "done"
                self._task_trial_observation(task, True, "accept:" + event_id)
            elif action in ("block", "cancel"):
                self.need_actor("lead", "user")
                require(previous not in ("done", "cancelled"), "Task already closed")
                require(data.get("reason"), "Reason required")
                task["status"] = "blocked" if action == "block" else "cancelled"
                task["reason"] = data["reason"]
            else:
                require(False, "Unknown task action")
            if self.actor in bundle.ROLES and "versions" in task:
                task["versions"][self.actor] = self.store.need("role", self.actor)["current"]
            task["revision"] += 1
            self.store.put("task", task_id, task)
            self.store.put("task_action", event_id, {"signature": signature})
            self.emit("task." + action, task_id, {"from": previous, "to": task["status"], "payload": data})
            if target:
                self._handoff(task, target, action)
        return task

    def _task_trial_observation(self, task, success, evidence):
        for exp in self.store.all("experiment"):
            if exp["status"] == "trial" and task.get("versions", {}).get(exp["role"]) == exp["candidate"]:
                self._observe(exp, success, False, evidence, source="task")
