"""Shared release inventory and metadata rules, stdlib only."""
import hashlib
import json
import re
from fnmatch import fnmatchcase
from pathlib import Path

RELEASE_FIELDS = ("name", "language", "repository", "distribution", "version", "channel", "live_verified")

IGNORED_DIRS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".venv", "dist", "build"}


def ignored(relative):
    return (any(p in IGNORED_DIRS or p.endswith(".egg-info") or p.startswith("._") for p in relative.parts)
            or relative.name in {".DS_Store", "MANIFEST.json"}
            or fnmatchcase(relative.name, "FIX-*.md")
            or relative.suffix in (".pyc", ".pyo"))


def metadata(root):
    root = Path(root)
    data = json.loads((root / "release.json").read_text())
    if data.get("version") != (root / "VERSION").read_text().strip():
        raise ValueError("release.json version differs from VERSION")
    if data.get("channel") != "prerelease" or data.get("live_verified") is not False:
        raise ValueError("Pilot package requires channel=prerelease and live_verified=false")
    if not re.fullmatch(r"[A-Za-z0-9]+", data.get("name", "")) or data.get("language") not in ("ru", "en"):
        raise ValueError("Invalid release name or language")
    if not data.get("repository", "").endswith("/" + data["name"]) or not re.fullmatch(
            r"[a-z0-9]+(?:-[a-z0-9]+)*", data.get("distribution", "")):
        raise ValueError("Invalid repository or Python distribution")
    return data


def source_files(root):
    root = Path(root).resolve()
    files = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if ignored(rel):
            continue
        if ".runtime" in rel.parts:
            raise ValueError("Runtime directory forbidden in release tree: " + rel.as_posix())
        if path.is_symlink():
            raise ValueError("Release cannot contain symlink: " + rel.as_posix())
        if path.is_file():
            if path.name == ".env" or ".sqlite" in path.name or path.suffix == ".log":
                raise ValueError("Runtime/secret file in release scope: " + rel.as_posix())
            files[rel.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return files


def render(root):
    data = metadata(root)
    return {**{key: data[key] for key in RELEASE_FIELDS}, "files": source_files(root)}


def check(root):
    root = Path(root)
    expected = json.loads((root / "MANIFEST.json").read_text())
    actual = render(root)
    if expected.get("live_verified") is not False or expected.get("channel") != "prerelease":
        raise ValueError("Manifest must explicitly contain live_verified=false and channel=prerelease")
    if actual != expected:
        a, b = actual["files"], expected["files"]
        raise ValueError("Manifest mismatch: missing=%r extra=%r changed=%r" % (
            sorted(set(b)-set(a)), sorted(set(a)-set(b)), sorted(k for k in set(a)&set(b) if a[k] != b[k])))
    return actual
