import time
import os
from pathlib import Path

from . import bundle
from .store import Store, TeamError, digest, require, uid
from .install import Installation
from .tasks import Tasks
from .improvement import Improvements
from .confirmation import Confirmations


class Engine(Installation, Tasks, Improvements, Confirmations):
    def __init__(self, workspace, actor="lead", root=bundle.ROOT, clock=time.time, confirmation_code=None):
        self.store = Store(workspace)
        self.actor = actor
        self.root = Path(root).resolve()
        self.clock = clock
        self.confirmation_code = confirmation_code

    def close(self):
        self.store.close()

    def need_actor(self, *allowed):
        require(self.actor in allowed, "Actor %s cannot perform this operation (expected %s)" % (self.actor, allowed))

    def team(self, ready=False):
        team = self.store.need("config", "team")
        if ready:
            require(team["status"] == "ready", "Team not ready; inspect install status and bridge pending")
        return team

    def emit(self, kind, key, data):
        self.store.event(kind, self.actor, key, data)

    def role_read(self, role):
        require(role in bundle.ROLES, "Unknown role")
        self.need_actor(role, "user", "installer", "controller")
        row = self.store.need("role", role)
        require(not row.get("blocked"), "Role blocked by instruction conflict; user restoration required")
        version = self.store.version_get(row["current"])
        team = self.team()
        require(bundle.verify(self.root) == team["release"], "Installed release changed; restore the pinned release")
        return {**row, "prompt": version["prompt"], "hash": version["hash"],
                "version": version["id"], "mode": self.mode(role),
                "skills": bundle.skills(role, self.root), "skills_hash": bundle.skills_hash(role, self.root),
                "team_status": team["status"], "workspace": str(self.store.root)}

    def role_probe(self, role):
        value = self.role_read(role)
        return {"agent_id": value.get("agent_id"), "role": role, "version": value["version"],
                "hash": value["hash"], "skills_hash": value["skills_hash"],
                "challenge": self.team()["challenge"]}

    def _operation(self, kind, target, data, key=None):
        key = key or uid("op")
        old = self.store.get("operation", key)
        if old:
            require(old["kind"] == kind and old["target"] == target and old["data"] == data,
                    "Idempotency key reused with different content")
            return old
        op = {"id": key, "kind": kind, "target": target, "data": data, "status": "pending",
              "created": self.clock()}
        self.store.put("operation", key, op)
        self.emit("operation.queued", key, {"kind": kind, "target": target})
        return op

    def pending(self):
        return [o for o in self.store.all("operation") if o["status"] != "done"]

    def policy(self):
        value = self.store.need("config", "policy")
        require(value.get("system_mode") in ("autonomous", "approval"), "Corrupt policy: invalid system mode")
        require(type(value.get("revision")) is int and value["revision"] > 0, "Corrupt policy revision")
        require(isinstance(value.get("bot_modes"), dict) and all(
            r in bundle.ROLES and m in ("autonomous", "approval") for r, m in value["bot_modes"].items()),
            "Corrupt per-bot policy")
        value.setdefault("confirmation_channel", "chat")
        require(value["confirmation_channel"] in ("chat", "terminal"), "Corrupt confirmation channel")
        return value

    def policy_channel_set(self, channel, evidence=None):
        self.need_actor("user", "lead")
        require(channel in ("chat", "terminal"), "Invalid confirmation channel")
        with self.store.transaction():
            policy = self.policy()
            if channel == policy["confirmation_channel"]:
                return policy
            if channel == "chat":
                self.need_actor("user")
                self.require_confirmation("policy.channel", "system", {"channel": channel})
            policy["confirmation_channel"] = channel
            policy["revision"] += 1
            self.store.put("config", "policy", policy)
            self.emit("policy.channel_changed", "policy", {"channel": channel, "evidence": evidence})
        return policy

    def watchdog_alive(self):
        service = self.store.get("config", "service", {})
        beat = self.store.get("config", "watchdog", {})
        if (not service.get("pid") or service.get("stop") or not service.get("token")
                or beat.get("pid") != service["pid"] or beat.get("token") != service["token"]
                or not 0 <= time.time() - beat.get("heartbeat", 0) < 30):
            return False
        try:
            os.kill(service["pid"], 0)
            return True
        except OSError:
            return False

    def mode(self, role):
        require(role in bundle.ROLES, "Unknown role")
        policy = self.policy()
        overrides = policy["bot_modes"]
        return {"mode": overrides.get(role, policy["system_mode"]),
                "source": "bot" if role in overrides else "system", "revision": policy["revision"]}

    def policy_set(self, mode, role=None, evidence=None):
        self.need_actor("user")
        require(evidence and evidence.strip(), "Human message reference required")
        require(mode in ("autonomous", "approval", "inherit"), "Invalid mode")
        require(mode != "inherit" or role, "Only a bot can inherit")
        require(role is None or role in bundle.ROLES, "Unknown role")
        with self.store.transaction():
            policy = self.policy()
            self.require_confirmation("policy.set", role or "system", {"mode": mode})
            if role:
                if mode == "inherit":
                    policy["bot_modes"].pop(role, None)
                else:
                    policy["bot_modes"][role] = mode
            else:
                policy["system_mode"] = mode
            policy["revision"] += 1
            self.store.put("config", "policy", policy)
            self.emit("policy.changed", "policy", {"policy": policy, "evidence": evidence})
        return {"policy": policy, "effective": {r: self.mode(r) for r in bundle.ROLES}}

    def status(self):
        team = self.store.get("config", "team")
        if not team:
            return {"status": "not_installed"}
        return {"team": team, "roles": self.store.all("role"),
                "modes": {r: self.mode(r) for r in bundle.ROLES},
                "confirmation_channel": self.confirmation_channel(),
                "pending_operations": len(self.pending()),
                "project": self.store.get("config", "project"),
                "watchdog": self.store.get("config", "watchdog"),
                "watchdog_alive": self.watchdog_alive(),
                "experiments": [{k: e.get(k) for k in ("id", "role", "status", "reason", "awaiting_acceptance")}
                                for e in self.store.all("experiment")]}

    def _handoff(self, task, target, reason):
        self._operation("send", target, {"task_id": task["id"], "revision": task["revision"],
                        "status": task["status"], "reason": reason,
                        "instruction": "Read your current role, then task show " + task["id"]},
                        key="handoff-%s-%s" % (task["id"], task["revision"]))

    def require_idle(self, role):
        busy = [t["id"] for t in self.store.all("task")
                if t["status"] not in ("done", "cancelled", "blocked", "received")]
        require(not busy, "Instruction changes wait for idle team: " + ",".join(busy))
