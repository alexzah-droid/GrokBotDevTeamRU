"""Bounded argv execution; no shell interpolation and no implicit cloud access."""
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time

from .store import require


def run(argv, cwd, timeout=60, stdin=None, env=None):
    require(isinstance(argv, list) and argv and all(isinstance(x, str) and x for x in argv),
            "Command must be a non-empty JSON argv array")
    require(0 < timeout <= 300, "Timeout must be 1..300 seconds")
    started = time.monotonic()
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        child = subprocess.Popen(argv, cwd=str(cwd), stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                                 stdout=output, stderr=errors, env=env, start_new_session=True)
        expired = False
        try:
            child.communicate(stdin.encode() if stdin is not None else None, timeout=timeout)
        except subprocess.TimeoutExpired:
            expired = True
            os.killpg(child.pid, signal.SIGKILL)
            child.communicate()
        output.seek(0)
        errors.seek(0)
        stdout, stderr = output.read(100001), errors.read(100001)
    return {"argv": argv, "returncode": child.returncode, "timeout": expired,
            "stdout": stdout[:100000].decode("utf-8", errors="replace"),
            "stderr": stderr[:100000].decode("utf-8", errors="replace"),
            "truncated": len(stdout) > 100000 or len(stderr) > 100000,
            "seconds": round(time.monotonic() - started, 4)}


def snapshot(repository):
    root = Path(repository).resolve()
    probe = run(["git", "rev-parse", "--show-toplevel"], root)
    require(probe["returncode"] == 0 and Path(probe["stdout"].strip()).resolve() == root,
            "Project must be the root of a Git repository")
    untracked = subprocess.run(["git", "ls-files", "--others", "--exclude-standard",
                                "--exclude=__pycache__/", "--exclude=*.pyc", "--exclude=.pytest_cache/", "-z"], cwd=str(root),
                               capture_output=True, check=True).stdout
    require(not untracked, "Untracked project files: stage source files or explicitly ignore generated output")
    result = subprocess.run(["git", "ls-files", "-z", "--cached"], cwd=str(root), capture_output=True, check=True)
    from .store import digest
    def node(path, ancestors=frozenset()):
        resolved = path.resolve()
        require(resolved.is_relative_to(root), "Symlink/path outside project: " + str(path.relative_to(root)))
        if not path.exists():
            return ["deleted"]
        if path.is_symlink():
            require(resolved not in ancestors, "Cyclic project symlink: " + str(path.relative_to(root)))
            return ["symlink", os.readlink(path), node(resolved, ancestors)]
        if path.is_dir():
            require(resolved not in ancestors, "Cyclic project directory link")
            return ["directory", [[p.name, node(p, ancestors | {resolved})] for p in sorted(path.iterdir()) if p.name != ".git"]]
        require(path.is_file() and path.stat().st_size <= 10_000_000, "Unsupported project file: " + str(path.relative_to(root)))
        import hashlib
        return [hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mode & 0o111]

    records = []
    for name in sorted(set(result.stdout.decode().split("\0")) - {""}):
        path = root / name
        records.append([name, node(path)])
    require(records, "Project has no tracked files; stage new source files before verification")
    return digest(records)


def verify_profile(repository, commands, timeout):
    """Shared real verification path for projects and installer smoke."""
    before = snapshot(repository)
    results = [run(command, repository, timeout) for command in commands]
    require(snapshot(repository) == before, "Verification changed tracked sources; inspect changes and run again")
    return {"snapshot": before, "results": results,
            "passed": all(r["returncode"] == 0 and not r["timeout"] and not r["truncated"] for r in results)}
