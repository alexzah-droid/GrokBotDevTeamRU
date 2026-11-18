import argparse
import json
import sqlite3
import sys
import getpass
from pathlib import Path

from . import bundle, service
from .engine import Engine
from .release import metadata
from .store import TeamError


def read_json(path):
    return json.loads(Path(path).read_text()) if path != "-" else json.load(sys.stdin)


def parser():
    release = metadata(bundle.ROOT)
    p = argparse.ArgumentParser(description=f'{release["name"]} {release["version"]} — local controller + native tool bridge')
    p.add_argument("--workspace", default=str(Path.home() / ".local/state/grokbotdevteam"),
                   help="Runtime directory outside the release and project trees")
    p.add_argument("--actor", choices=["lead", "developer", "reviewer", "user", "installer", "controller"], default="lead")
    p.add_argument("--confirmation", help="Single-use human code; '-' reads it from stdin without echo on a terminal")
    sub = p.add_subparsers(dest="command", required=True)
    confirm = sub.add_parser("confirm")
    confirmations = confirm.add_subparsers(dest="action", required=True)
    describe = confirmations.add_parser("describe")
    describe.add_argument("--action", dest="human_action", required=True)
    describe.add_argument("--target", required=True)
    describe.add_argument("--file")
    issue = confirmations.add_parser("issue")
    issue.add_argument("--action", dest="human_action", required=True)
    issue.add_argument("--target", required=True)
    issue.add_argument("--hash", required=True)
    issue.add_argument("--ttl", type=int, default=300)
    issue.add_argument("--reviewed-diff")
    chat = confirmations.add_parser("chat")
    chat.add_argument("--action", dest="human_action", required=True)
    chat.add_argument("--target", required=True)
    chat.add_argument("--hash", required=True)
    chat.add_argument("--file", help="JSON with asked, answer and message_ref; '-' reads stdin")
    chat.add_argument("--message-ref")
    chat.add_argument("--answer")
    chat.add_argument("--asked")
    chat.add_argument("--ttl", type=int, default=300)
    sub.add_parser("status")
    sub.add_parser("release-check")
    ins = sub.add_parser("install")
    ins.add_argument("--lead-id", required=True)
    ins.add_argument("--mode", choices=["approval", "autonomous"], default="approval")
    ins.add_argument("--source", default=release["repository"])
    ins.add_argument("--mock", action="store_true")
    sub.add_parser("install-finish")
    bridge = sub.add_parser("bridge")
    bs = bridge.add_subparsers(dest="action", required=True)
    bs.add_parser("pending")
    begin = bs.add_parser("begin"); begin.add_argument("id")
    ack = bs.add_parser("ack"); ack.add_argument("id"); ack.add_argument("--file", required=True)
    role = sub.add_parser("role")
    role.add_argument("action", choices=["read", "probe", "smoke"])
    role.add_argument("role", choices=bundle.ROLES)
    policy = sub.add_parser("policy")
    ps = policy.add_subparsers(dest="action", required=True)
    ps.add_parser("show")
    setting = ps.add_parser("set")
    setting.add_argument("mode", choices=["approval", "autonomous", "inherit"])
    setting.add_argument("--role", choices=bundle.ROLES)
    setting.add_argument("--evidence", required=True)
    channel = ps.add_parser("channel")
    channel.add_argument("channel", choices=["chat", "terminal"])
    channel.add_argument("--evidence")
    project = sub.add_parser("project")
    projects = project.add_subparsers(dest="action", required=True)
    bind = projects.add_parser("bind"); bind.add_argument("--file", required=True)
    verify = projects.add_parser("verify"); verify.add_argument("--task")
    extra = projects.add_parser("add-check"); extra.add_argument("--file", required=True)
    task = sub.add_parser("task")
    ts = task.add_subparsers(dest="action", required=True)
    create = ts.add_parser("create"); create.add_argument("--file", required=True)
    show = ts.add_parser("show"); show.add_argument("id")
    ts.add_parser("list")
    for action in ("plan", "submit", "review", "demo", "accept", "block", "cancel"):
        item = ts.add_parser(action); item.add_argument("id"); item.add_argument("--file", required=True)
    ev = sub.add_parser("evaluation")
    ev.add_argument("role", choices=bundle.ROLES)
    ev.add_argument("--file", required=True); ev.add_argument("--evidence", required=True)
    improve = sub.add_parser("improve")
    ims = improve.add_subparsers(dest="action", required=True)
    proposal = ims.add_parser("propose")
    proposal.add_argument("role", choices=bundle.ROLES); proposal.add_argument("--body", required=True)
    proposal.add_argument("--hypothesis", required=True); proposal.add_argument("--evidence", action="append", required=True)
    for action in ("evaluate", "progress", "show"):
        item = ims.add_parser(action); item.add_argument("id")
    for action in ("approve", "accept", "cancel"):
        item = ims.add_parser(action); item.add_argument("id"); item.add_argument("--evidence", required=True)
    obs = ims.add_parser("observe"); obs.add_argument("id"); obs.add_argument("--file", required=True)
    history = sub.add_parser("history"); history.add_argument("role", choices=bundle.ROLES)
    version = sub.add_parser("version"); version.add_argument("id")
    diff = sub.add_parser("diff")
    diff.add_argument("before", help="Experiment ID or baseline version ID")
    diff.add_argument("after", nargs="?", help="Candidate version ID when comparing two versions")
    restore = sub.add_parser("restore"); restore.add_argument("role", choices=bundle.ROLES)
    restore.add_argument("version"); restore.add_argument("--evidence", required=True)
    events = sub.add_parser("events"); events.add_argument("--object"); events.add_argument("--limit", type=int, default=100)
    sub.add_parser("export")
    ws = sub.add_parser("watch"); ws.add_argument("--once", action="store_true")
    ws.add_argument("--interval", type=float, default=5); ws.add_argument("--token")
    svc = sub.add_parser("service"); svc.add_argument("action", choices=["start", "stop"])
    return p


def dispatch(e, a):
    c = a.command
    if c == "confirm":
        if a.action == "describe":
            return e.confirmation_describe(a.human_action, a.target, read_json(a.file) if a.file else {})
        if a.action == "chat":
            values = {"asked": a.asked, "answer": a.answer, "message_ref": a.message_ref}
            if a.file is not None:
                if any(value is not None for value in values.values()):
                    raise TeamError("Use --file or the three confirmation fields, not both")
                payload = read_json(a.file)
                if not isinstance(payload, dict) or payload.keys() != values.keys():
                    raise TeamError("Chat JSON requires asked, answer and message_ref")
                values = payload
            elif any(value is None for value in values.values()):
                raise TeamError("Use --file or pass --asked, --answer and --message-ref")
            return e.confirmation_chat(a.human_action, a.target, a.hash, ttl=a.ttl, **values)
        e.need_actor("user")
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise TeamError("confirm issue is human-terminal-only; bots must not issue their own approval codes")
        plan = e.store.need("confirmation_plan", a.hash)
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        if input("Review the action/diff above. Type ISSUE to authorize: ").strip() != "ISSUE":
            raise TeamError("Human confirmation issuance cancelled")
        return e.confirmation_issue(a.human_action, a.target, a.hash, a.ttl, a.reviewed_diff)
    if c == "status": return e.status()
    if c == "release-check": return bundle.verify()
    if c == "install":
        return e.mock_install(a.lead_id, a.mode) if a.mock else e.install(a.lead_id, a.mode, source=a.source)
    if c == "install-finish": return e.install_finish()
    if c == "bridge":
        if a.action == "pending": return e.pending()
        if a.action == "begin": return e.bridge_begin(a.id)
        return e.bridge_ack(a.id, read_json(a.file))
    if c == "role":
        if a.action == "read": return e.role_read(a.role)
        if a.action == "probe": return e.role_probe(a.role)
        return e.smoke(a.role)
    if c == "policy":
        if a.action == "channel":
            return e.policy_channel_set(a.channel, a.evidence)
        return {"policy": e.policy(), "effective": {r: e.mode(r) for r in bundle.ROLES}} if a.action == "show" else e.policy_set(a.mode, a.role, a.evidence)
    if c == "project":
        if a.action == "verify": return e.project_verify(a.task)
        if a.action == "add-check": return e.reviewer_check_add(read_json(a.file))
        return e.project_bind(**read_json(a.file))
    if c == "task":
        if a.action == "create": return e.task_create(read_json(a.file))
        if a.action == "show": return e.store.need("task", a.id)
        if a.action == "list": return e.store.all("task")
        return e.task_action(a.id, a.action, read_json(a.file))
    if c == "evaluation": return e.evaluation_configure(a.role, read_json(a.file), a.evidence)
    if c == "improve":
        if a.action == "propose": return e.improve_propose(a.role, Path(a.body).read_text(), a.hypothesis, a.evidence)
        if a.action == "show": return e.store.need("experiment", a.id)
        if a.action == "evaluate": return e.improve_evaluate(a.id)
        if a.action == "progress": return e.improve_progress(a.id)
        if a.action == "approve": return e.improve_approve(a.id, a.evidence)
        if a.action == "accept": return e.improve_accept(a.id, a.evidence)
        if a.action == "cancel": return e.improve_cancel(a.id, a.evidence)
        return e.improve_observe(a.id, **read_json(a.file))
    if c == "history": return e.store.history(a.role)
    if c == "version": return e.store.version_get(a.id)
    if c == "diff":
        if a.after is None:
            exp = e.store.need("experiment", a.before)
            return {"diff": e.instruction_diff(exp["baseline"], exp["candidate"])}
        return {"diff": e.instruction_diff(a.before, a.after)}
    if c == "restore": return e.restore_version(a.role, a.version, a.evidence)
    if c == "events": return e.store.events(a.object, a.limit)
    if c == "export": return e.store.export()
    if c == "watch": return service.watch(e, a.interval, a.once, a.token)
    if c == "service": return service.start(e) if a.action == "start" else service.stop(e)
    raise TeamError("Unknown command")


def main(argv=None):
    a = parser().parse_args(argv)
    engine = None
    try:
        if sys.version_info < (3, 10):
            raise TeamError("Python 3.10 or newer is required")
        if a.command == "release-check":
            print(json.dumps(bundle.verify(), ensure_ascii=False, indent=2))
            return
        code = a.confirmation
        if code == "-":
            code = getpass.getpass("Confirmation code: ") if sys.stdin.isatty() else sys.stdin.readline().strip()
        engine = Engine(a.workspace, actor=a.actor, confirmation_code=code)
        result = dispatch(engine, a)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (TeamError, OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)
    finally:
        if engine:
            engine.close()
