"""Single-use, expiring action grants. This is not OS/user identity isolation."""
import difflib
import re
import secrets

from . import bundle
from .store import digest, encode, require


class Confirmations:
    def instruction_diff(self, before_id, after_id):
        before = self.store.version_get(before_id)["prompt"]
        after = self.store.version_get(after_id)["prompt"]
        return "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                            fromfile=before_id, tofile=after_id))

    def confirmation_channel(self):
        if self.store.get("config", "policy") is None:
            return "chat"
        return self.policy()["confirmation_channel"]

    def confirmation_intent(self, action, target, payload=None):
        payload = payload or {}
        require(isinstance(payload, dict), "Confirmation payload must be an object")
        state = {}
        diff = None
        if action == "policy.channel":
            require(target == "system" and payload == {"channel": "chat"}, "Invalid channel confirmation")
            state = self.policy()
        elif action == "policy.set":
            require(target in ("system", "lead", "developer", "reviewer"), "Unknown policy target")
            require(payload.get("mode") in ("approval", "autonomous", "inherit"), "Invalid requested mode")
            state = self.policy()
        elif action == "evaluation.configure":
            require(target in ("lead", "developer", "reviewer"), "Unknown role")
            state = self.store.get("evaluation_config", target, {})
        elif action in ("improve.approve", "improve.accept", "improve.cancel", "improve.observe"):
            exp = self.store.need("experiment", target)
            state = {k: exp.get(k) for k in ("status", "baseline_hash", "candidate_hash", "config_hash")}
            state["active_version"] = self.store.need("role", exp["role"])["current"]
            if action == "improve.accept":
                state.update(deadline=exp.get("deadline"), observations_hash=digest(exp["observations"]),
                             successes=len(exp["observations"]), required=exp.get("required_successes"))
            if action == "improve.approve":
                diff = self.instruction_diff(exp["baseline"], exp["candidate"])
                state.update(report_hash=exp.get("report", {}).get("hash"),
                             diff_hash=digest(diff), policy_revision=self.policy()["revision"])
        elif action == "instruction.restore":
            state = {"current": self.store.need("role", target)["current"],
                     "version": self.store.version_get(payload.get("version", ""))["hash"]}
        elif action == "task.auto_accept":
            state = {"project": self.store.need("config", "project")["id"]}
        elif action in ("task.accept", "task.block", "task.cancel"):
            task = self.store.need("task", target)
            state = {"revision": task["revision"], "request_hash": task["request_hash"],
                     "review": task.get("review"), "demo": task.get("demo")}
        elif action == "project.bind":
            state = self.store.get("config", "project", {})
        elif action in ("service.start", "service.stop", "install"):
            state = {"team_id": self.store.get("config", "team", {}).get("id")}
        elif action == "task.create":
            state = {"project": self.store.need("config", "project")["id"]}
        else:
            require(False, "Unknown confirmation action: " + action)
        policy = self.store.get("config", "policy", {})
        context = {"action": action, "target": target, "payload": payload, "state": state,
                   "policy_revision": policy.get("revision"),
                   "workspace": str(self.store.root)}
        result = {"action": action, "target": target, "hash": digest(context), "payload": payload,
                  "diff_hash": state.get("diff_hash"), "context": context}
        if diff is not None:
            result["diff"] = diff
        subject = target
        if action.startswith("improve."):
            subject = f'{target} ({exp["role"]})'
        wording = bundle.texts(self.root)
        card = wording["card"]
        details = []
        if action == "improve.approve":
            report = exp.get("report", {})
            if report.get("scores"):
                scores = report["scores"]
                details.append(card["metric"].format(baseline=scores["baseline"]["target"],
                                                     candidate=scores["candidate"]["target"]))
            lines = diff.splitlines(keepends=True)
            first_lines = "".join(lines[:40])
            preview = first_lines[:2000]
            details.append(card["changes"] + "\n" + preview.rstrip("\n"))
            if len(preview) < len(diff):
                count = card["counter"].format(shown=len(preview.splitlines()), total=len(lines))
                if len(preview) < len(first_lines) and not preview.endswith("\n"):
                    count += card["line_truncated"]
                details.append(count + ".")
            details.append(card["diff"].format(target=target))
        if action == "improve.accept":
            details.append(card["observations"].format(successes=state["successes"], version=exp["candidate"]))
        if payload:
            summary = encode(payload)
            if len(summary) > 400:
                summary = summary[:400] + card["parameters_more"]
            details.append(card["parameters"].format(summary=summary))
        result["question"] = "\n".join([
            card["question"].format(action=wording["questions"][action], subject=subject),
            *details, card["consequences"].format(details=wording["consequences"][action]),
            card["hash"].format(hash=result["hash"]),
        ])
        result["buttons"] = wording["buttons"]
        return result

    def confirmation_describe(self, action, target, payload=None):
        intent = self.confirmation_intent(action, target, payload)
        with self.store.transaction():
            self.store.put("confirmation_plan", intent["hash"], intent)
        return intent

    def confirmation_issue(self, action, target, intent_hash, ttl=300, reviewed_diff=None):
        """Interactive terminal path. The caller and its TTY are not proof of identity."""
        self.need_actor("user")
        return self._issue_grant(action, target, intent_hash, ttl, reviewed_diff=reviewed_diff)

    def confirmation_chat(self, action, target, intent_hash, message_ref, answer, asked, ttl=300):
        self.need_actor("lead", "installer")
        require(isinstance(message_ref, str) and 0 < len(message_ref.strip()) <= 512, "User message reference required")
        require(isinstance(answer, str) and answer.strip().casefold() in bundle.texts(self.root)["chat_answers"],
                "Explicit affirmative answer required")
        audit = {"message_ref": message_ref, "asked": asked, "answer": answer}
        return self._issue_grant(action, target, intent_hash, ttl, chat=audit)

    def _issue_grant(self, action, target, intent_hash, ttl, reviewed_diff=None, chat=None):
        require(type(ttl) is int and 1 <= ttl <= 3600, "Confirmation TTL must be 1..3600 seconds")
        require(isinstance(intent_hash, str) and re.fullmatch(r"[0-9a-f]{64}", intent_hash), "Expected intent SHA-256")
        with self.store.transaction():
            plan = self.store.need("confirmation_plan", intent_hash)
            require(plan["action"] == action and plan["target"] == target, "Confirmation action/target mismatch")
            live = self.confirmation_intent(action, target, plan["payload"])
            require(live["hash"] == intent_hash, "Action changed since description; review it again")
            if chat is not None:
                require(self.confirmation_channel() == "chat", "Chat confirmation disabled in terminal mode")
                require(chat["asked"] == live["question"], "Asked text must match the described question and hash")
                message_key = digest(chat["message_ref"].strip())
                require(self.store.get("confirmation_message", message_key) is None, "Chat response already used")
                self.store.put("confirmation_message", message_key, {"intent_hash": intent_hash})
            elif action == "improve.approve":
                require(reviewed_diff == live["diff_hash"], "Review the displayed diff and supply its exact --reviewed-diff hash")
            code = secrets.token_urlsafe(24)
            key = digest(code)
            value = {"action": action, "target": target, "intent_hash": intent_hash,
                     "expires_at": self.clock() + ttl, "used": False, "diff_hash": live["diff_hash"],
                     "channel": "chat" if chat is not None else "terminal"}
            self.store.put("confirmation", key, value)
            self.emit("confirmation.issued", target, {**value, "code_hash": key, **(chat or {})})
        return {"code": code, "action": action, "target": target, "intent_hash": intent_hash,
                "expires_at": value["expires_at"], "channel": value["channel"]}

    def require_confirmation(self, action, target, payload=None):
        require(self.store.writing, "Confirmation must be consumed atomically with its action")
        code = self.confirmation_code
        require(isinstance(code, str) and code, "Human confirmation code required; --actor user and --evidence are not authorization")
        key = digest(code)
        grant = self.store.get("confirmation", key)
        require(grant is not None, "Unknown confirmation code (or code belongs to another workspace)")
        require(not grant["used"], "Confirmation code already used")
        require(self.clock() < grant["expires_at"], "Confirmation code expired")
        require(grant["action"] == action and grant["target"] == target, "Code issued for another action or target")
        require(grant.get("channel", "terminal") == "terminal" or self.confirmation_channel() == "chat",
                "Chat grant disabled in terminal mode")
        live = self.confirmation_intent(action, target, payload)
        require(secrets.compare_digest(grant["intent_hash"], live["hash"]), "Action/diff changed; confirmation binding mismatch")
        grant["used"] = True
        self.store.put("confirmation", key, grant)
        self.emit("confirmation.consumed", target, {"action": action, "code_hash": key, "intent_hash": live["hash"]})
        # Never persist the plaintext code in events, task data, receipts or reports.
        return {"code_hash": key, "intent_hash": live["hash"], "diff_hash": grant["diff_hash"]}
