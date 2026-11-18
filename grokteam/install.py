import time
import sys
import tempfile
from pathlib import Path
import shutil

from .process import run, verify_profile

from . import bundle
from .store import digest, require, uid


class Installation:
    def install_options(self, lead_id, choice=None):
        self.need_actor("installer", "lead")
        require(isinstance(lead_id, str) and lead_id.strip(), "Existing lead agent ID required")
        require(choice in (None, "approval", "autonomous"), "Invalid install choice")
        cards = bundle.texts(self.root)["install_cards"]
        with self.store.transaction():
            team = self.store.get("config", "team")
            if team:
                require(team["lead_id"] == lead_id, "This workspace belongs to another lead")
                return {"mode": team.get("install_mode", self.policy()["system_mode"]), "resume": True}
            state = self.store.get("config", "install_choice")
            require(not state or state["lead_id"] == lead_id, "This workspace belongs to another lead")
            if state and state["mode"] == "approval":
                require(choice != "autonomous", "Installation already chose approval")
            if not state or choice is not None:
                state = {"lead_id": lead_id, "mode": choice}
                self.store.put("config", "install_choice", state)
                if choice is None:
                    return {"mode": None, "mode_card": cards["mode"], "folder_card": cards["folder"]}
            return {"mode": state["mode"] or "approval", "resume": True, "folder_card": cards["folder"]}

    def install(self, lead_id, mode="approval", adapter="native", source=None, section_id=None):
        self.need_actor("installer", "user")
        require(mode in ("approval", "autonomous"), "Invalid mode")
        require(adapter in ("native", "mock"), "Adapter must be native or mock")
        require(isinstance(lead_id, str) and lead_id.strip(), "Existing lead agent ID required")
        require(section_id is None or isinstance(section_id, str) and section_id.strip(), "Invalid sidebar section ID")
        release = bundle.verify(self.root)
        if source is None:
            source = release["repository"]
        wording = bundle.texts(self.root)
        with self.store.transaction():
            old = self.store.get("config", "team")
            draft = self.store.get("config", "install_choice")
            require(not draft or draft["lead_id"] == lead_id, "This workspace belongs to another lead")
            consent = None
            if self.actor == "user" or (old is None and mode == "autonomous"):
                payload = {"mode": mode, "adapter": adapter, "source": source}
                if section_id is not None:
                    payload["section_id"] = section_id
                consent = self.require_confirmation("install", lead_id, payload)
            if old:
                require(old["lead_id"] == lead_id and old["adapter"] == adapter and old["release"] == release,
                        "This workspace belongs to a different installation/release; use a separate workspace")
                return old
            team_id = uid("team")
            team = {"id": team_id, "status": "installing", "lead_id": lead_id,
                    "adapter": adapter, "source": source, "release": release,
                    "install_mode": mode, "section_id": section_id,
                    "challenge": uid("probe"), "created": self.clock()}
            self.store.put("config", "team", team)
            self.store.put("config", "policy", {"revision": 1, "system_mode": mode, "bot_modes": {},
                                                "confirmation_channel": "chat"})
            for role in bundle.ROLES:
                body = (self.root / "bots" / role / "behavior.md").read_text()
                version = self.store.version(role, bundle.compile_prompt(role, body, self.root), "installation")
                value = {"role": role, "current": version, "agent_id": lead_id if role == "lead" else None,
                         "bound": False, "skills_hash": bundle.skills_hash(role, self.root)}
                self.store.put("role", role, value)
                if role != "lead":
                    role_name = wording["roles"][role]
                    data = {"name": role_name + " · " + team_id[-6:], "team_id": team_id,
                            "description": wording["create_description"].format(
                                role=role_name, team_id=team_id, lead_id=lead_id)}
                    if section_id is not None:
                        data["section_id"] = section_id
                    self._operation("create", role, data, key="install-create-" + role)
                else:
                    self._bind_operation(role, team)
            self.emit("install.started", team_id, {**team, "mode": mode, "confirmation": consent})
        return team

    def _bind_operation(self, role, team):
        row = self.store.need("role", role)
        self._operation("bind", role, {"agent_id": row["agent_id"],
                        "bootstrap": bundle.bootstrap(role, self.store.root, team["id"], self.root),
                        "expected": {"version": row["current"], "challenge": team["challenge"],
                                     "skills_hash": row["skills_hash"]}}, key="install-bind-" + role)

    def bridge_begin(self, op_id):
        self.need_actor("installer", "lead", "controller")
        with self.store.transaction():
            op = self.store.need("operation", op_id)
            require(op["status"] == "pending", "Already dispatched: reconcile native result, do not repeat blindly")
            op["status"] = "dispatched"
            self.store.put("operation", op_id, op)
            self.emit("operation.dispatched", op_id, {})
        return op

    def bridge_ack(self, op_id, receipt):
        self.need_actor("installer", "lead", "controller")
        require(isinstance(receipt, dict) and receipt.get("external_ref"), "Native tool/message reference required")
        with self.store.transaction():
            op = self.store.need("operation", op_id)
            if op["status"] == "done":
                require(op["receipt"] == receipt, "Conflicting duplicate receipt")
                return op
            require(op["status"] == "dispatched", "Dispatch before recording receipt")
            team = self.team()
            role = op["target"]
            if op["kind"] == "create":
                agent_id = receipt.get("agent_id")
                require(isinstance(agent_id, str) and agent_id.strip(), "Created agent ID required")
                require(agent_id not in [r["agent_id"] for r in self.store.all("role")], "Agent already assigned")
                row = self.store.need("role", role)
                row["agent_id"] = agent_id
                if "section_id" in receipt:
                    section_id = receipt["section_id"]
                    require(section_id is None or isinstance(section_id, str) and section_id.strip(),
                            "Invalid receipt section ID")
                    row["section_id"] = section_id
                self.store.put("role", role, row)
                self._bind_operation(role, team)
            elif op["kind"] == "bind":
                row = self.store.need("role", role)
                require(receipt.get("persistent") is True, "Durable bootstrap must be confirmed")
                require(receipt.get("agent_id") == row["agent_id"] and receipt.get("role") == role,
                        "Wrong agent/role in probe")
                for k, value in op["data"]["expected"].items():
                    require(receipt.get(k) == value, "Probe mismatch: " + k)
                require(receipt.get("hash") == self.store.version_get(row["current"])["hash"], "Prompt probe mismatch")
                row["bound"] = True
                row["binding_receipt"] = receipt
                self.store.put("role", role, row)
            elif op["kind"] == "smoke":
                require(receipt.get("role") == role and receipt.get("challenge") == team["challenge"], "Wrong smoke probe")
                smoke = self.store.need("smoke", role)
                require(smoke["passed"] is True and receipt.get("hash") == smoke["hash"], "Smoke not performed")
            elif op["kind"] == "send":
                require(receipt.get("agent_id") == self.store.need("role", role)["agent_id"], "Wrong recipient")
            op["status"], op["receipt"] = "done", receipt
            self.store.put("operation", op_id, op)
            self.emit("operation.completed", op_id, receipt)
            if all(r["bound"] for r in self.store.all("role")):
                self._operation("smoke", "developer", {"challenge": team["challenge"]}, key="install-smoke-developer")
                self._operation("smoke", "reviewer", {"challenge": team["challenge"]}, key="install-smoke-reviewer")
        return op

    def smoke(self, role):
        self.need_actor(role)
        require(role in ("developer", "reviewer"), "Smoke role must be developer/reviewer")
        team = self.team()
        require(self.store.need("role", role)["bound"], "Role must be bound")
        if role == "developer":
            repository = self.store.root / "installation-smoke"
            if repository.exists():
                require(self.store.get("fixture", "smoke", {}).get("repository") == str(repository), "Unowned smoke directory")
            else:
                repository.mkdir()
            require(not repository.is_symlink(), "Smoke directory cannot be a symlink")
            (repository / "smoke_app.py").write_text("def add(left, right):\n    return left + right\n")
            (repository / "verify.py").write_text("from smoke_app import add\nassert add(2,3)==5\nassert add(-2,-3)==-5\n")
            (repository / ".gitignore").write_text("__pycache__/\n")
            require(run(["git", "init", "-q"], repository)["returncode"] == 0, "Cannot initialize smoke repository")
            require(run(["git", "add", "--", "smoke_app.py", "verify.py", ".gitignore"], repository)["returncode"] == 0, "Cannot stage smoke files")
            verification = verify_profile(repository, [[sys.executable, "-B", "verify.py"]], 10)
            require(verification["passed"], "Developer smoke implementation failed real verification")
            artifact = {"challenge": team["challenge"], "repository": str(repository), "snapshot": verification["snapshot"]}
            value = {"role": role, "challenge": team["challenge"], "passed": True, "hash": digest(artifact), "positive": verification}
        else:
            artifact = self.store.need("fixture", "smoke")
            repository = Path(artifact["repository"])
            verification = verify_profile(repository, [[sys.executable, "-B", "verify.py"]], 10)
            require(verification["passed"] and verification["snapshot"] == artifact["snapshot"], "Reviewer detected broken/changed smoke implementation")
            with tempfile.TemporaryDirectory(prefix="negative-smoke-", dir=self.store.root) as temp:
                broken = Path(temp)
                for name in ("verify.py", ".gitignore"):
                    shutil.copyfile(repository / name, broken / name)
                (broken / "smoke_app.py").write_text("def add(left, right):\n    return left - right\n")
                require(run(["git", "init", "-q"], broken)["returncode"] == 0, "Negative smoke Git init failed")
                require(run(["git", "add", "."], broken)["returncode"] == 0, "Negative smoke Git stage failed")
                negative = verify_profile(broken, [[sys.executable, "-B", "verify.py"]], 10)
                require(not negative["passed"] and negative["results"][0]["returncode"] != 0,
                        "Negative smoke accepted a genuinely broken implementation")
            value = {"role": role, "challenge": team["challenge"], "passed": True, "hash": digest(artifact),
                     "positive": verification, "negative": negative}
        with self.store.transaction():
            if role == "developer":
                self.store.put("fixture", "smoke", artifact)
            self.store.put("smoke", role, value)
            self.emit("install.smoke", team["id"], value)
        return value

    def install_finish(self):
        self.need_actor("installer", "lead", "controller")
        with self.store.transaction():
            team = self.team()
            require(all(r["bound"] for r in self.store.all("role")), "Role binding incomplete")
            require(all(self.store.get("operation", "install-smoke-" + r, {}).get("status") == "done"
                        for r in ("developer", "reviewer")), "Native smoke messages not confirmed")
            if team["adapter"] == "native":
                require(self.watchdog_alive(), "Start and verify watchdog before ready")
            team["status"] = "ready"
            self.store.put("config", "team", team)
            self.emit("install.ready", team["id"], {"adapter": team["adapter"]})
            placement = {row["role"]: row.get("section_id", "unknown")
                         for row in self.store.all("role") if row["role"] != "lead"}
            return {**team, "placements": placement}

    def mock_install(self, lead_id="mock-primary", mode="approval", section_id=None):
        self.need_actor("installer")
        self.install(lead_id, mode=mode, adapter="mock", section_id=section_id)
        while True:
            pending = [p for p in self.pending() if p["id"].startswith("install-")]
            if not pending:
                break
            pending.sort(key=lambda p: (p["kind"] == "smoke", p["target"] == "reviewer"))
            op = pending[0]
            if op["status"] == "pending":
                self.bridge_begin(op["id"])
            receipt = {"external_ref": "mock:" + op["id"]}
            if op["kind"] == "create":
                receipt["agent_id"] = "mock-" + op["target"]
                receipt["section_id"] = op["data"].get("section_id")
            elif op["kind"] == "bind":
                old_actor = self.actor
                self.actor = op["target"]
                receipt.update(self.role_probe(op["target"]))
                self.actor = old_actor
                receipt["persistent"] = True
            elif op["kind"] == "smoke":
                old_actor = self.actor
                self.actor = op["target"]
                receipt.update(self.smoke(op["target"]))
                self.actor = old_actor
            self.bridge_ack(op["id"], receipt)
        return self.install_finish()
