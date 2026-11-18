"""Transactional local state. SQLite, rather than model memory, is authoritative."""
import contextlib
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid


class TeamError(ValueError):
    pass


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    if not isinstance(value, str):
        value = encode(value)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def uid(prefix):
    return prefix + "-" + uuid.uuid4().hex[:16]


def require(condition, message):
    if not condition:
        raise TeamError(message)


class Store:
    def __init__(self, workspace):
        self.root = Path(workspace).expanduser().absolute()
        require(not self.root.is_symlink(), "Workspace cannot be a symlink")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        for name in ("state.sqlite", ".controller.lock"):
            require(not (self.root / name).is_symlink(), "State paths cannot be symlinks")
        self.db = sqlite3.connect(str(self.root / "state.sqlite"), timeout=5, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS objects (
            kind TEXT NOT NULL, id TEXT NOT NULL, data TEXT NOT NULL,
            PRIMARY KEY(kind,id));
          CREATE TABLE IF NOT EXISTS events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL,
            kind TEXT NOT NULL, actor TEXT NOT NULL, object_id TEXT NOT NULL, data TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS versions (
            id TEXT PRIMARY KEY, role TEXT NOT NULL, parent TEXT, hash TEXT NOT NULL,
            prompt TEXT NOT NULL, at REAL NOT NULL, reason TEXT NOT NULL, experiment TEXT);
        """)
        os.chmod(self.root / "state.sqlite", 0o600)
        self.writing = False

    def close(self):
        self.db.close()

    @contextlib.contextmanager
    def transaction(self):
        require(not self.writing, "Nested transaction")
        with open(self.root / ".controller.lock", "a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise TeamError("Controller busy; retry after reading current status")
            self.db.execute("BEGIN IMMEDIATE")
            self.writing = True
            try:
                yield
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
            finally:
                self.writing = False
                fcntl.flock(lock, fcntl.LOCK_UN)

    def get(self, kind, key, default=None):
        row = self.db.execute("SELECT data FROM objects WHERE kind=? AND id=?", (kind, key)).fetchone()
        return json.loads(row[0]) if row else default

    def need(self, kind, key):
        value = self.get(kind, key)
        require(value is not None, "Unknown %s: %s" % (kind, key))
        return value

    def all(self, kind):
        return [json.loads(r[0]) for r in self.db.execute(
            "SELECT data FROM objects WHERE kind=? ORDER BY id", (kind,))]

    def put(self, kind, key, value):
        require(self.writing, "Write outside transaction")
        self.db.execute("INSERT INTO objects VALUES (?,?,?) ON CONFLICT(kind,id) DO UPDATE SET data=excluded.data",
                        (kind, key, encode(value)))

    def event(self, kind, actor, object_id, data):
        require(self.writing, "Event outside transaction")
        self.db.execute("INSERT INTO events(at,kind,actor,object_id,data) VALUES (?,?,?,?,?)",
                        (time.time(), kind, actor, object_id, encode(data)))

    def events(self, object_id=None, limit=100):
        require(0 < limit <= 10000, "Limit must be 1..10000")
        query = "SELECT * FROM events"
        args = []
        if object_id:
            query += " WHERE object_id=?"
            args.append(object_id)
        rows = self.db.execute(query + " ORDER BY sequence DESC LIMIT ?", args + [limit]).fetchall()
        return [dict(r, data=json.loads(r["data"])) for r in reversed(rows)]

    def version(self, role, prompt, reason, parent=None, experiment=None):
        require(self.writing, "Version outside transaction")
        key = uid("v")
        self.db.execute("INSERT INTO versions VALUES (?,?,?,?,?,?,?,?)",
                        (key, role, parent, digest(prompt), prompt, time.time(), reason, experiment))
        return key

    def version_get(self, key):
        row = self.db.execute("SELECT * FROM versions WHERE id=?", (key,)).fetchone()
        require(row is not None, "Unknown version: " + key)
        return dict(row)

    def history(self, role):
        return [dict(r) for r in self.db.execute(
            "SELECT id,role,parent,hash,at,reason,experiment FROM versions WHERE role=? ORDER BY rowid", (role,))]

    def export(self):
        return {"objects": [dict(r, data=json.loads(r["data"])) for r in self.db.execute(
                    "SELECT * FROM objects ORDER BY kind,id")],
                "versions": [dict(r) for r in self.db.execute("SELECT * FROM versions ORDER BY rowid")],
                "events": [dict(r, data=json.loads(r["data"])) for r in self.db.execute(
                    "SELECT * FROM events ORDER BY sequence")]}
