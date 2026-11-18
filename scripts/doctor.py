#!/usr/bin/env python3
"""Read-only preflight; no dependency installation or network access."""
import importlib.util
import json
import shutil
import sys

checks = {"python_3_10": sys.version_info >= (3, 10), "sqlite3": importlib.util.find_spec("sqlite3") is not None,
          "fcntl": importlib.util.find_spec("fcntl") is not None, "git": shutil.which("git") is not None}
print(json.dumps({"checks": checks, "ready": all(checks.values())}, indent=2))
raise SystemExit(0 if all(checks.values()) else 2)
