"""A simulated human for offline tests/demo ONLY, deliberately rejected on native teams.

Production callers must use the interactive confirmation issuer, not this fixture.
"""
import inspect

from grokteam.store import digest, require


def grant(engine, action, target, payload=None, ttl=300):
    require(engine.team()["adapter"] == "mock", "Human fixture is forbidden for native teams")
    previous = engine.actor
    engine.actor = "user"
    try:
        plan = engine.confirmation_describe(action, target, payload)
        issued = engine.confirmation_issue(action, target, plan["hash"], ttl=ttl, reviewed_diff=plan["diff_hash"])
        engine.confirmation_code = issued["code"]
        return issued["code"]
    finally:
        engine.actor = previous


class HumanFixture:
    """Preserve existing positive test scenarios while supplying a real, consumed mock grant."""
    def __init__(self, engine):
        self.engine = engine

    def __getattr__(self, name):
        value = getattr(self.engine, name)
        if name not in ("policy_set", "evaluation_configure", "improve_approve", "improve_accept", "improve_cancel",
                        "restore_version", "task_action", "task_create"):
            return value

        def invoke(*args, **kwargs):
            bound = inspect.signature(value).bind(*args, **kwargs)
            bound.apply_defaults()
            data = bound.arguments
            if name == "policy_set":
                action, target, payload = "policy.set", data["role"] or "system", {"mode": data["mode"]}
            elif name == "evaluation_configure":
                action, target, payload = "evaluation.configure", data["role"], data["config"]
            elif name in ("improve_approve", "improve_accept", "improve_cancel"):
                action, target, payload = "improve." + name.split("_")[1], data["exp_id"], {}
            elif name == "restore_version":
                action, target, payload = "instruction.restore", data["role"], {"version": data["version_id"]}
            elif name == "task_create":
                request = data["request"]
                action = "task.auto_accept" if request.get("auto_accept") else "task.create"
                target, payload = "task-" + digest(request["request_id"])[:16], request
            else:
                action, target, payload = "task." + data["action"], data["task_id"], data["data"]
            grant(self.engine, action, target, payload)
            return value(*args, **kwargs)
        return invoke
