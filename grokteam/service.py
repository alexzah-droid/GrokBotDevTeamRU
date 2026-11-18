"""A small local watchdog; no model calls or native messaging in this process."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import logging

from .store import TeamError, require, uid

_children = {}


def watch(engine, interval=5, once=False, token=None):
    engine.need_actor("controller")
    require(0.1 <= interval <= 30, "Watch interval must be 0.1..30")
    while True:
        try:
            if token:
                service = engine.store.get("config", "service", {})
                if service.get("token") != token or service.get("stop"):
                    return {"stopped": True}
            engine.sweep()
        except Exception:
            logging.exception("Watchdog iteration failed; retaining state and continuing")
            if once:
                return {"watchdog_error": "iteration failed; inspect watchdog.log"}
        if once:
            return {"watchdog": engine.store.get("config", "watchdog")}
        time.sleep(interval)


def start(engine):
    engine.need_actor("installer", "user", "lead", "controller")
    old = engine.store.get("config", "service", {})
    if engine.watchdog_alive():
        return {**old, "already_running": True}
    token = uid("watch")
    with engine.store.transaction():
        if engine.actor == "user":
            engine.require_confirmation("service.start", "service")
        engine.store.put("config", "service", {"token": token, "stop": False})
    argv = [sys.executable, "-B", str(engine.root / "teamctl.py"), "--workspace", str(engine.store.root),
            "--actor", "controller", "watch", "--token", token]
    with open(engine.store.root / "watchdog.log", "ab") as log:
        child = subprocess.Popen(argv, stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True)
    _children[child.pid] = child
    with engine.store.transaction():
        value = {"token": token, "stop": False, "pid": child.pid}
        engine.store.put("config", "service", value)
        engine.emit("watchdog.started", "service", {"pid": child.pid})
    return value


def stop(engine):
    engine.need_actor("user", "installer")
    require(not any(e["status"] == "trial" for e in engine.store.all("experiment")),
            "Cancel/finish active trials before stopping automatic rollback")
    with engine.store.transaction():
        if engine.actor == "user":
            engine.require_confirmation("service.stop", "service")
        value = engine.store.get("config", "service", {})
        value["stop"] = True
        engine.store.put("config", "service", value)
        engine.emit("watchdog.stop_requested", "service", {})
    child = _children.pop(value.get("pid"), None)
    if child is not None:
        try:
            child.wait(timeout=7)
        except subprocess.TimeoutExpired:
            _children[child.pid] = child
    return {"stop_requested": True}
