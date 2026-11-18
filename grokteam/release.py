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


def validate_channel(data, source):
    channel = data.get("channel")
    verified = data.get("live_verified")
    if (channel == "prerelease" and verified is False) or (channel == "stable" and verified is True):
        return
    raise ValueError(
        f"{source}: expected channel=prerelease with live_verified=false or channel=stable with live_verified=true"
    )


def metadata(root):
    root = Path(root)
    data = json.loads((root / "release.json").read_text())
    if data.get("version") != (root / "VERSION").read_text().strip():
        raise ValueError("release.json version differs from VERSION")
    validate_channel(data, "release.json")
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
    manifest = {key: data[key] for key in RELEASE_FIELDS}
    if "validation" in data:
        manifest["validation"] = data["validation"]
    return {**manifest, "files": source_files(root)}


def check(root):
    root = Path(root)
    expected = json.loads((root / "MANIFEST.json").read_text())
    validate_channel(expected, "MANIFEST.json")
    actual = render(root)
    if actual != expected:
        a, b = actual["files"], expected["files"]
        raise ValueError("Manifest mismatch: missing=%r extra=%r changed=%r" % (
            sorted(set(b)-set(a)), sorted(set(a)-set(b)), sorted(k for k in set(a)&set(b) if a[k] != b[k])))
    return actual
