import json
from pathlib import Path
import sys

from .store import digest, require

_CHECKOUT = Path(__file__).resolve().parent.parent
ROOT = _CHECKOUT if (_CHECKOUT / "MANIFEST.json").is_file() else Path(__file__).resolve().parent / "_release"
ROLES = ("lead", "developer", "reviewer")


def texts(root=ROOT):
    package = Path(root) / "grokteam"
    selected = package / "texts.json"
    if not selected.is_file() and (package / "texts").is_dir():
        selected = package / "texts" / "ru.json"
    return json.loads(selected.read_text(encoding="utf-8"))


def repository(root=ROOT):
    return json.loads((Path(root) / "release.json").read_text(encoding="utf-8"))["repository"]


def verify(root=ROOT):
    from .release import check
    try:
        manifest = check(root)
    except ValueError as exc:
        require(False, str(exc))
    return {**{key: manifest[key] for key in ("name", "language", "repository", "version", "channel", "live_verified")},
            "files": len(manifest["files"]), "hash": digest(manifest)}


def skills(role, root=ROOT):
    registry = json.loads((Path(root) / "skills/manifest.json").read_text())
    require(role in ROLES, "Unknown role")
    return [{**item, "path": str(Path(root) / "skills" / item["name"] / "SKILL.md")}
            for item in registry["skills"] if role in item["roles"]]


def skills_hash(role, root=ROOT):
    return digest({x["name"]: (Path(x["path"]).read_text()) for x in skills(role, root)})


def compile_prompt(role, body, root=ROOT):
    require(role in ROLES and isinstance(body, str) and 20 <= len(body) <= 50000, "Invalid role/body")
    contract = (Path(root) / "bots" / role / "contract.md").read_text().strip()
    return contract + "\n\n<editable-behavior>\n" + body.strip() + "\n</editable-behavior>\n"


def bootstrap(role, workspace, team_id, root=ROOT):
    command = [sys.executable, "-B", str(Path(root).resolve() / "teamctl.py"), "--workspace", str(Path(workspace).resolve()),
               "--actor", role, "role", "read", role]
    rule = texts(root)["bootstrap"]
    notify = rule["notify_user"] if role == "lead" else rule["notify"]
    body = " ".join((rule["scope"], rule["read"], rule["stop"], notify, rule["stale"]))
    return (rule["header"].format(team_id=team_id, role=role) + "\n" + body + "\nargv="
            + json.dumps(command, ensure_ascii=False, separators=(",", ":")))
