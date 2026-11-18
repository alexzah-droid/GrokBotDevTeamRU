"""Evaluation, immutable candidates, policy gates, trial observations and rollback."""
import json
import math
from pathlib import Path

from . import bundle
from .process import run
from .store import digest, encode, require, uid

TERMINAL = {"accepted", "rejected", "inconclusive", "rolled_back", "cancelled", "blocked"}


def validate_suite(suite):
    require(isinstance(suite, dict), "Suite must be an object")
    cases = suite.get("cases")
    require(isinstance(cases, list) and 3 <= len(cases) <= 100, "Suite needs 3..100 held-out cases")
    require({c.get("group") for c in cases} == {"target", "regression", "boundary"}, "All three groups required")
    require(len({c.get("id") for c in cases}) == len(cases) and all(c.get("id") for c in cases), "Unique case IDs required")
    for case in cases:
        require(isinstance(case.get("input"), str), "Case input must be text")
        require(any(k in case for k in ("equals", "contains", "excludes")), "Deterministic assertions required")
        for field in ("contains", "excludes"):
            require(field not in case or (isinstance(case[field], list) and all(isinstance(x, str) for x in case[field])), "Invalid assertion")
        require("equals" not in case or isinstance(case["equals"], str), "equals must be text")
    require(type(suite.get("repeats", 1)) is int and 1 <= suite.get("repeats", 1) <= 5, "repeats must be 1..5")
    for field in ("min_gain", "regression_tolerance"):
        value = suite.get(field, 0.05 if field == "min_gain" else 0)
        require(type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1, "Invalid metric threshold")
    require(suite.get("min_gain", 0.05) > 0, "Positive target gain required")
    require(1 <= suite.get("trial_successes", 3) <= 100, "trial_successes must be 1..100")
    require(1 <= suite.get("trial_seconds", 3600) <= 604800, "trial_seconds must be 1..604800")
    return suite


def judge(answer, case):
    return (("equals" not in case or answer == case["equals"])
            and all(x in answer for x in case.get("contains", []))
            and all(x not in answer for x in case.get("excludes", [])))


class Improvements:
    def evaluation_configure(self, role, config, evidence):
        self.need_actor("user")
        require(role in bundle.ROLES and evidence, "Role and human authorization required")
        require(isinstance(config, dict), "Evaluation configuration must be an object")
        validate_suite(config.get("suite"))
        require(isinstance(config.get("command"), list) and config["command"]
                and all(isinstance(a, str) and a for a in config["command"]), "Runner argv required")
        require(Path(config.get("cwd", "")).is_dir(), "Runner cwd required")
        require(1 <= config.get("timeout", 30) <= 300, "Runner timeout must be 1..300")
        require(config.get("model_id") and config.get("environment_id"), "Model and environment identifiers required")
        require(type(config.get("max_calls", 40)) is int and 1 <= config.get("max_calls", 40) <= 200, "max_calls must be 1..200")
        require(1 <= config.get("max_seconds", 120) <= 600, "max_seconds must be 1..600")
        require(len(config["suite"]["cases"]) * config["suite"].get("repeats", 1) * 2 <= config.get("max_calls", 40),
                "Evaluation exceeds authorized call budget")
        value = {**config, "role": role, "evidence": evidence}
        value["hash"] = digest(value)
        with self.store.transaction():
            self.require_confirmation("evaluation.configure", role, config)
            self.store.put("evaluation_config", role, value)
            self.emit("evaluation.configured", role, {"hash": value["hash"], "evidence": evidence})
        return {"role": role, "hash": value["hash"]}

    def improve_propose(self, role, body, hypothesis, evidence):
        self.team(ready=True)
        self.need_actor(role, "lead")
        require(hypothesis and evidence and all(isinstance(x, str) and x for x in evidence), "Hypothesis and evidence required")
        require(role in bundle.ROLES, "Unknown role")
        config = self.store.need("evaluation_config", role)
        with self.store.transaction():
            require(not any(e["role"] == role and e["status"] not in TERMINAL
                            for e in self.store.all("experiment")), "This role already has an active experiment")
            row = self.store.need("role", role)
            require(not row.get("blocked"), "Role is blocked; restore before new experiments")
            baseline = self.store.version_get(row["current"])
            prompt = bundle.compile_prompt(role, body, self.root)
            require(digest(prompt) != baseline["hash"], "Candidate is unchanged")
            key = uid("exp")
            candidate = self.store.version(role, prompt, "candidate", parent=baseline["id"], experiment=key)
            evaluator = next(r for r in ("reviewer", "lead", "developer") if r not in (role, self.actor))
            require(self.actor != evaluator, "Author cannot be the evaluator")
            exp = {"id": key, "role": role, "author": self.actor, "evaluator": evaluator,
                   "status": "proposed", "baseline": baseline["id"], "baseline_hash": baseline["hash"],
                   "candidate": candidate, "candidate_hash": digest(prompt), "hypothesis": hypothesis,
                   "evidence": evidence, "config_hash": config["hash"], "created": self.clock(),
                   "observations": [], "runs": 0, "approval": None}
            self.store.put("experiment", key, exp)
            self.emit("improvement.proposed", key, exp)
        return exp

    def improve_evaluate(self, exp_id):
        exp = self.store.need("experiment", exp_id)
        self.need_actor(exp["evaluator"])
        require(self.actor != exp["author"], "Independent evaluator required")
        config = self.store.need("evaluation_config", exp["role"])
        require(config["hash"] == exp["config_hash"], "Evaluation policy changed; propose a new experiment")
        with self.store.transaction():
            exp = self.store.need("experiment", exp_id)
            require(exp["status"] == "proposed", "Evaluation already started; do not repeat paid calls blindly")
            require(self.store.need("role", exp["role"])["current"] == exp["baseline"], "Stale baseline")
            exp["status"] = "testing"
            self.store.put("experiment", exp_id, exp)
            self.emit("evaluation.started", exp_id, {"config_hash": config["hash"]})
        suite = config["suite"]
        results = []
        import time
        started = time.monotonic()
        try:
            # Only the input is sent to a runner. Expected answers stay in the evaluator.
            # A configured backend receives a role instruction as system context, never rubric control.
            pairs = [("baseline", exp["baseline"]), ("candidate", exp["candidate"])]
            for repeat in range(suite.get("repeats", 1)):
                for case in suite["cases"]:
                    for label, version_id in pairs if repeat % 2 == 0 else reversed(pairs):
                        remaining = config.get("max_seconds", 120) - (time.monotonic() - started)
                        require(remaining > 0, "Evaluation time budget exhausted")
                        version = self.store.version_get(version_id)
                        payload = {"instruction": version["prompt"], "input": case["input"],
                                   "model_id": config["model_id"], "environment_id": config["environment_id"]}
                        with self.store.transaction():
                            live = self.store.need("experiment", exp_id)
                            require(live["status"] == "testing", "Evaluation cancelled while running")
                            require(self.store.need("evaluation_config", exp["role"])["hash"] == config["hash"],
                                    "Evaluation policy changed during execution")
                            self.emit("evaluation.call_started", exp_id, {"case": case["id"], "variant": label,
                                      "repeat": repeat, "request_hash": digest(payload)})
                        result = run(config["command"], config["cwd"], min(config.get("timeout", 30), remaining), stdin=encode(payload))
                        require(result["returncode"] == 0 and not result["timeout"] and not result["truncated"],
                                "Evaluation runner failed; see recorded evaluation error")
                        response = json.loads(result["stdout"])
                        require(isinstance(response.get("answer"), str), "Runner must return JSON answer text")
                        results.append({"case": case["id"], "group": case["group"], "variant": label,
                                        "passed": judge(response["answer"], case), "answer_hash": digest(response["answer"]),
                                        "seconds": result["seconds"], "repeat": repeat})
                        with self.store.transaction():
                            live = self.store.need("experiment", exp_id)
                            require(live["status"] == "testing", "Evaluation cancelled while running")
                            live["partial_results"] = results
                            self.store.put("experiment", exp_id, live)
                            self.emit("evaluation.call_completed", exp_id, results[-1])
            scores = {}
            for label, _ in pairs:
                scores[label] = {}
                for group in ("target", "regression", "boundary"):
                    subset = [r for r in results if r["variant"] == label and r["group"] == group]
                    scores[label][group] = sum(r["passed"] for r in subset) / len(subset)
            base, candidate = scores["baseline"], scores["candidate"]
            passed = (candidate["target"] - base["target"] + 1e-10 >= suite.get("min_gain", 0.05)
                      and candidate["regression"] + suite.get("regression_tolerance", 0) + 1e-10 >= base["regression"]
                      and candidate["boundary"] == 1)
            report = {"scores": scores, "passed": passed, "results": results, "config_hash": config["hash"],
                      "model_id": config["model_id"], "environment_id": config["environment_id"],
                      "evaluator": self.actor, "candidate_hash": exp["candidate_hash"], "at": self.clock()}
            report["hash"] = digest(report)
        except (Exception,) as exc:
            with self.store.transaction():
                exp = self.store.need("experiment", exp_id)
                if exp["status"] != "testing":
                    return exp
                exp["status"], exp["reason"] = "inconclusive", str(exc)
                exp["partial_results"] = results
                self.store.put("experiment", exp_id, exp)
                self.emit("evaluation.inconclusive", exp_id, {"reason": str(exc), "completed_calls": len(results)})
            return exp
        with self.store.transaction():
            exp = self.store.need("experiment", exp_id)
            require(exp["status"] == "testing", "Evaluation cancelled while running")
            exp["report"] = report
            exp["status"] = "evaluated" if passed else "rejected"
            exp["runs"] = len(results)
            self.store.put("experiment", exp_id, exp)
            self.emit("evaluation.completed", exp_id, report)
        return self.improve_progress(exp_id) if passed else exp

    def _approval_fingerprint(self, exp):
        return digest([exp["baseline_hash"], exp["candidate_hash"], exp["report"]["hash"], self.policy()["revision"],
                       self.confirmation_intent("improve.approve", exp["id"])["diff_hash"]])

    def improve_approve(self, exp_id, evidence):
        self.need_actor("user")
        require(evidence and evidence.strip(), "Human message reference required")
        with self.store.transaction():
            exp = self.store.need("experiment", exp_id)
            require(exp["status"] in ("evaluated", "awaiting_approval"), "Candidate is not awaiting application")
            require(exp.get("report", {}).get("passed"), "Passing evaluation required")
            consent = self.require_confirmation("improve.approve", exp_id)
            exp["approval"] = {"fingerprint": self._approval_fingerprint(exp), "evidence": evidence,
                               "at": self.clock(), "confirmation": consent}
            exp["status"] = "evaluated"
            self.store.put("experiment", exp_id, exp)
            self.emit("improvement.approved", exp_id, exp["approval"])
        return self.improve_progress(exp_id)

    def improve_progress(self, exp_id):
        exp = self.store.need("experiment", exp_id)
        require(self.actor in ("controller", "user", exp["evaluator"]), "Only evaluator/controller can progress a candidate")
        with self.store.transaction():
            exp = self.store.need("experiment", exp_id)
            require(exp["status"] in ("evaluated", "awaiting_approval"), "No evaluated candidate to apply")
            require(exp["report"]["passed"], "Evaluation failed")
            config = self.store.need("evaluation_config", exp["role"])
            require(config["hash"] == exp["config_hash"], "Evaluation configuration changed")
            row = self.store.need("role", exp["role"])
            require(row["current"] == exp["baseline"], "Instruction changed externally; refusing stale update")
            mode = self.mode(exp["role"])
            if mode["mode"] == "approval" and (not exp["approval"] or
                    exp["approval"]["fingerprint"] != self._approval_fingerprint(exp)):
                exp["status"] = "awaiting_approval"
                self.store.put("experiment", exp_id, exp)
                self.emit("improvement.awaiting_approval", exp_id, {"mode": mode})
                return exp
            if self.team()["adapter"] == "native":
                require(self.watchdog_alive(), "Watchdog unavailable; automatic rollback must be running before application")
            self.require_idle(exp["role"])
            candidate = self.store.version_get(exp["candidate"])
            require(candidate["hash"] == exp["candidate_hash"], "Candidate content changed")
            # The active pointer and application event commit atomically. Native bots always read it
            # at task entry; no undocumented Personal Bot description endpoint is used.
            row["current"] = exp["candidate"]
            self.store.put("role", exp["role"], row)
            require(self.store.version_get(row["current"])["hash"] == exp["candidate_hash"], "Write check failed")
            exp["status"] = "trial"
            exp["applied_mode"] = mode
            exp["applied_at"] = self.clock()
            exp["deadline"] = self.clock() + config["suite"].get("trial_seconds", 3600)
            exp["required_successes"] = config["suite"].get("trial_successes", 3)
            self.store.put("experiment", exp_id, exp)
            self.emit("improvement.applied", exp_id, {"version": exp["candidate"], "mode": mode, "deadline": exp["deadline"]})
        return exp

    def _rollback(self, exp, reason):
        exp.pop("awaiting_acceptance", None)
        row = self.store.need("role", exp["role"])
        if row["current"] != exp["candidate"]:
            row["blocked"] = True
            self.store.put("role", exp["role"], row)
            exp["status"], exp["reason"] = "blocked", "Rollback conflict: active instruction changed"
            self.store.put("experiment", exp["id"], exp)
            self.emit("rollback.blocked", exp["id"], {"active": row["current"]})
            return exp
        baseline = self.store.version_get(exp["baseline"])
        restored = self.store.version(exp["role"], baseline["prompt"], "rollback: " + reason,
                                      parent=row["current"], experiment=exp["id"])
        row["current"] = restored
        self.store.put("role", exp["role"], row)
        require(self.store.version_get(restored)["hash"] == exp["baseline_hash"], "Rollback read-back failed")
        exp["status"], exp["reason"], exp["restored_version"] = "rolled_back", reason, restored
        self.store.put("experiment", exp["id"], exp)
        self.emit("improvement.rolled_back", exp["id"], {"reason": reason, "restored": restored})
        return exp

    def _observe(self, exp, success, critical, evidence, source="independent"):
        key = digest([evidence, source])
        old = next((o for o in exp["observations"] if o["id"] == key), None)
        if old:
            require(old["success"] == success and old["critical"] == critical, "Conflicting observation")
            return exp
        require(exp["status"] == "trial", "Experiment not in trial")
        if self.clock() >= exp["deadline"]:
            return self._rollback(exp, "trial expired without sufficient evidence")
        exp["observations"].append({"id": key, "success": success, "critical": critical,
                                    "evidence": evidence, "source": source, "at": self.clock()})
        self.emit("trial.observed", exp["id"], exp["observations"][-1])
        if critical or not success:
            return self._rollback(exp, "critical violation" if critical else "trial regression")
        if len(exp["observations"]) >= exp["required_successes"]:
            if self._acceptance_needs_confirmation(exp):
                if not exp.get("awaiting_acceptance"):
                    exp["awaiting_acceptance"] = True
                    self.emit("improvement.awaiting_acceptance", exp["id"], {"version": exp["candidate"]})
            else:
                return self._accept_trial(exp)
        self.store.put("experiment", exp["id"], exp)
        return exp

    def _acceptance_needs_confirmation(self, exp):
        return (exp.get("applied_mode", {}).get("mode") != "autonomous"
                or self.mode(exp["role"])["mode"] != "autonomous")

    def _accept_trial(self, exp, confirmation=None, evidence=None):
        require(self.store.need("role", exp["role"])["current"] == exp["candidate"],
                "Active instruction has changed")
        exp["status"] = "accepted"
        exp.pop("awaiting_acceptance", None)
        if confirmation:
            exp["acceptance"] = {"confirmation": confirmation, "evidence": evidence}
        self.emit("improvement.accepted", exp["id"], {"version": exp["candidate"],
                  "acceptance": exp.get("acceptance")})
        self.store.put("experiment", exp["id"], exp)
        return exp

    def improve_accept(self, exp_id, evidence):
        self.need_actor("user")
        require(isinstance(evidence, str) and evidence.strip(), "Human acceptance reference required")
        with self.store.transaction():
            exp = self.store.need("experiment", exp_id)
            require(exp["status"] == "trial" and exp.get("awaiting_acceptance"), "Trial not awaiting human acceptance")
            require(self.clock() < exp["deadline"], "Trial deadline passed; watchdog will roll back")
            consent = self.require_confirmation("improve.accept", exp_id)
            return self._accept_trial(exp, consent, evidence)

    def improve_observe(self, exp_id, success, critical, evidence):
        exp = self.store.need("experiment", exp_id)
        self.need_actor(exp["evaluator"], "user")
        require(type(success) is bool and type(critical) is bool and evidence, "Boolean outcome and evidence required")
        with self.store.transaction():
            exp = self.store.need("experiment", exp_id)
            if self.actor == "user":
                self.require_confirmation("improve.observe", exp_id, {"success": success, "critical": critical, "evidence": evidence})
            return self._observe(exp, success, critical, evidence)

    def improve_cancel(self, exp_id, evidence):
        self.need_actor("user")
        require(evidence, "Human reason/reference required")
        with self.store.transaction():
            exp = self.store.need("experiment", exp_id)
            require(exp["status"] not in TERMINAL, "Experiment already closed")
            self.require_confirmation("improve.cancel", exp_id)
            if exp["status"] == "trial":
                return self._rollback(exp, "cancelled by user: " + evidence)
            exp["status"], exp["reason"] = "cancelled", evidence
            self.store.put("experiment", exp_id, exp)
            self.emit("improvement.cancelled", exp_id, {"evidence": evidence})
            return exp

    def restore_version(self, role, version_id, evidence):
        self.need_actor("user")
        require(evidence, "Human restoration request required")
        with self.store.transaction():
            self.require_idle(role)
            require(not any(e["role"] == role and e["status"] not in TERMINAL
                            for e in self.store.all("experiment")), "Close current experiment first")
            row = self.store.need("role", role)
            version = self.store.version_get(version_id)
            require(version["role"] == role, "Version belongs to another bot")
            self.require_confirmation("instruction.restore", role, {"version": version_id})
            current_contract = (self.root / "bots" / role / "contract.md").read_text().strip()
            require(version["prompt"].startswith(current_contract + "\n\n"), "Historic version has incompatible contract")
            new = self.store.version(role, version["prompt"], "user restore: " + evidence, parent=row["current"])
            row["current"] = new
            row["blocked"] = False
            self.store.put("role", role, row)
            self.emit("instruction.restored", role, {"version": new, "source": version_id, "evidence": evidence})
            return {"role": role, "version": new, "hash": version["hash"]}

    def sweep(self):
        self.need_actor("controller")
        actions = []
        for exp in self.store.all("experiment"):
            if exp["status"] == "trial" and self.clock() >= exp["deadline"]:
                with self.store.transaction():
                    live = self.store.need("experiment", exp["id"])
                    if live["status"] == "trial":
                        actions.append(self._rollback(live, "trial expired without sufficient evidence"))
            elif exp["status"] in ("evaluated", "awaiting_approval"):
                # approval mode is checked again at write time; revoked/missing consent cannot leak through.
                try:
                    if self.mode(exp["role"])["mode"] == "autonomous" or exp.get("approval"):
                        actions.append(self.improve_progress(exp["id"]))
                except Exception as exc:
                    actions.append({"id": exp["id"], "deferred": str(exc)})
        with self.store.transaction():
            import os
            service = self.store.get("config", "service", {})
            self.store.put("config", "watchdog", {"heartbeat": self.clock(), "pid": os.getpid(), "token": service.get("token")})
        return actions
