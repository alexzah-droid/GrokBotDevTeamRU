#!/usr/bin/env python3
"""Estimate instruction sizes with the same logical install paths across snapshots."""
import argparse
import json
from pathlib import Path
import re
import sys


def size(text):
    cyrillic = len(re.findall(r"[\u0400-\u052f\u2de0-\u2dff\ua640-\ua69f]", text))
    return {"characters": len(text), "words": len(text.split()),
            "estimated_tokens": round(cyrillic / 3.5 + (len(text) - cyrillic) / 4, 2)}


def measure(root):
    sys.path.insert(0, str(root))
    from grokteam import bundle

    roles = {}
    for role in bundle.ROLES:
        rule = bundle.bootstrap(role, "/opt/grokteam/runtime", "team-measurement", root)
        heading, argv = rule.split("argv=", 1)
        command = json.loads(argv)
        command[2] = "/opt/grokteam/release/teamctl.py"
        rule = heading + "argv=" + json.dumps(command, ensure_ascii=False, separators=(",", ":"))
        contract = (root / "bots" / role / "contract.md").read_text().strip()
        behavior = (root / "bots" / role / "behavior.md").read_text().strip()
        prompt = bundle.compile_prompt(role, behavior, root)
        skills = {item["name"]: Path(item["path"]).read_text() for item in bundle.skills(role, root)}
        roles[role] = {"bootstrap": size(rule), "contract": size(contract), "behavior": size(behavior),
                       "skills": {name: size(body) for name, body in skills.items()},
                       "bootstrap_and_role_read_prompt": size(rule + prompt),
                       "total": size(rule + prompt + "".join(skills.values()))}

    prompt = (root / "install-prompt.txt").read_text()
    install = (root / "INSTALL.md").read_text()
    guide = re.search(r"\]\((docs/CONFIRMATIONS(?:\.ru)?\.md)\)", install)
    confirmations_path = root / guide.group(1)
    confirmations = confirmations_path.read_text()
    routine = (root / "routines/watchdog-restart.md").read_text()
    return {"method": "Cyrillic characters / 3.5 + other characters / 4", "roles": roles,
            "installer": {"install_prompt": size(prompt), "install_md": size(install),
                          "default_total": size(prompt + install),
                          "with_optional_autonomy_and_routine": size(prompt + install + confirmations + routine)}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    print(json.dumps(measure(parser.parse_args().root.resolve()), ensure_ascii=False, indent=2))
