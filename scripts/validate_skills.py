#!/usr/bin/env python3
"""Validate our deliberately simple name/description frontmatter without a YAML dependency.

This is not a general YAML parser and not a claim of native private-skill registration.
"""
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def validate(root=ROOT):
    registry = json.loads((root / "skills/manifest.json").read_text())["skills"]
    for item in registry:
        path = root / "skills" / item["name"] / "SKILL.md"
        text = path.read_text()
        match = re.match(r"\A---\n(.*?)\n---\n", text, re.S)
        if not match:
            raise ValueError("Missing frontmatter: " + str(path))
        fields = {}
        for line in match.group(1).splitlines():
            key, sep, value = line.partition(": ")
            if not sep or key not in ("name", "description") or key in fields:
                raise ValueError("Unsupported simple frontmatter: " + str(path))
            fields[key] = value
        name, desc = fields.get("name", ""), fields.get("description", "")
        if (name != item["name"] or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) or len(name) > 64
                or not desc or len(desc) > 1024 or any(c in desc for c in ("<", ">", "\n"))
                or "[TODO:" in text):
            raise ValueError("Invalid skill metadata/scaffold: " + str(path))
    return {"validated_skills": len(registry), "format": "plain-name-description-frontmatter"}


if __name__ == "__main__":
    print(json.dumps(validate()))
