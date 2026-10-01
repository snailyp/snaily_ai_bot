"""Transactional, single-process admin-push persistence (no import-time I/O).

Every public operation uses the same write transaction boundary.  The RLock
protects a shared connection; BEGIN IMMEDIATE also serializes other connections
so reference acquisition cannot race garbage collection or a delivery claim.
"""

import hashlib
import json
import math
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from . import PushError


TEMP_LIFETIME = 24 * 60 * 60
TERMINAL_LIFETIME = 3 * TEMP_LIFETIME
LATE_GRACE = 30 * 60
TERMINAL_STATES = frozenset(
    {"succeeded", "failed", "partial", "unknown", "canceled", "missed"}
)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
    id TEXT PRIMARY KEY, metadata TEXT NOT NULL, created_at REAL NOT NULL,
    state TEXT NOT NULL DEFAULT 'ready'
);
CREATE TABLE IF NOT EXISTS drafts (
    id TEXT PRIMARY KEY, version INTEGER NOT NULL, composition TEXT NOT NULL,
    created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY, version INTEGER NOT NULL, state TEXT NOT NULL,
    kind TEXT NOT NULL, composition TEXT NOT NULL, plan TEXT NOT NULL,
    targets TEXT NOT NULL, scheduled_at REAL, created_at REAL NOT NULL,
    first_started_at REAL, finished_at REAL, expires_at REAL, run_no INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS parts (
    id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    chat_id TEXT NOT NULL, position INTEGER NOT NULL, state TEXT NOT NULL,
    UNIQUE(task_id, chat_id, position)
);
CREATE TABLE IF NOT EXISTS attempts (
    id TEXT PRIMARY KEY, part_id TEXT NOT NULL REFERENCES parts(id) ON DELETE CASCADE,
    run_no INTEGER NOT NULL, state TEXT NOT NULL, started_at REAL NOT NULL,
    finished_at REAL, message_ids TEXT NOT NULL DEFAULT '[]', error TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS previews (
    id TEXT PRIMARY KEY, payload TEXT NOT NULL, source TEXT, kind TEXT NOT NULL,
    created_at REAL NOT NULL, expires_at REAL NOT NULL, consumed_task_id TEXT
);
CREATE TABLE IF NOT EXISTS asset_refs (
    owner_type TEXT NOT NULL, owner_id TEXT NOT NULL,
    asset_id TEXT NOT NULL REFERENCES assets(id),
    PRIMARY KEY(owner_type, owner_id, asset_id)
);
CREATE INDEX IF NOT EXISTS asset_refs_asset ON asset_refs(asset_id);
CREATE INDEX IF NOT EXISTS task_queue ON tasks(state, scheduled_at, created_at);
CREATE INDEX IF NOT EXISTS task_parts ON parts(task_id, chat_id, position);
CREATE INDEX IF NOT EXISTS part_attempts ON attempts(part_id, started_at);
CREATE TABLE IF NOT EXISTS idempotency (
    scope TEXT NOT NULL, key TEXT NOT NULL, request TEXT NOT NULL,
    receipt TEXT NOT NULL, expires_at REAL NOT NULL, PRIMARY KEY(scope, key)
);
CREATE TABLE IF NOT EXISTS tombstones (
    owner_type TEXT NOT NULL, owner_id TEXT NOT NULL, reason TEXT NOT NULL,
    PRIMARY KEY(owner_type, owner_id)
);
CREATE TABLE IF NOT EXISTS operations (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, input_data TEXT NOT NULL,
    editor_revision TEXT NOT NULL, state TEXT NOT NULL, result TEXT,
    error TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL,
    started_at REAL, finished_at REAL, expires_at REAL NOT NULL
);
"""


def _json(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise PushError("Request must contain valid JSON values") from exc


def _fingerprint(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _id():
    return uuid.uuid4().hex


def _error(message, code="state_conflict", status=409):
    raise PushError(message, code=code, status=status)


class PushStore:
    """Own a SQLite database below the injected ``data/admin_push`` directory."""

    def __init__(self, root, clock=time.time):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self._lock = threading.RLock()
        self._db = sqlite3.connect(
            str(self.root / "push.sqlite3"), timeout=30, isolation_level=None,
            check_same_thread=False,
        )
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.execute("PRAGMA journal_mode = WAL")
        self._db.executescript(_SCHEMA)

    def close(self):
        with self._lock:
            self._db.close()

    @contextmanager
    def _transaction(self):
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield self._db
                self._db.commit()
            except BaseException:
                self._db.rollback()
                raise

    def _now(self):
        return float(self.clock())

    def _missing(self, db, owner_type, owner_id):
        row = db.execute(
            "SELECT reason FROM tombstones WHERE owner_type=? AND owner_id=?",
            (owner_type, owner_id),
        ).fetchone()
        if row and row["reason"] == "expired":
            _error("This record has expired", "expired", 410)
        if row and row["reason"] == "draft_consumed":
            _error("This draft has already been published", "draft_consumed")
        _error("Record not found", "not_found", 404)

    def _tombstone(self, db, owner_type, owner_id, reason):
        db.execute(
            "INSERT OR REPLACE INTO tombstones VALUES (?, ?, ?)",
            (owner_type, owner_id, reason),
        )

    def _replay(self, db, scope, key, request):
        if not isinstance(key, str):
            raise PushError("Idempotency key must be a string")
        if not key:
            return None
        row = db.execute(
            "SELECT * FROM idempotency WHERE scope=? AND key=?", (scope, key)
        ).fetchone()
        if not row:
            return None
        if row["request"] != _fingerprint(request):
            _error("Idempotency key was already used for another request", "idempotency_conflict")
        if row["expires_at"] <= self._now():
            _error("This request receipt has expired", "expired", 410)
        return json.loads(row["receipt"])

    def _receipt(self, db, scope, key, request, result):
        if key:
            expires_at = self._now() + TERMINAL_LIFETIME
            if result.get("expires_at") is not None:
                expires_at = min(expires_at, result["expires_at"])
            db.execute(
                "INSERT INTO idempotency VALUES (?, ?, ?, ?, ?)",
                (scope, key, _fingerprint(request), _json(result), expires_at),
            )
        return result

    @staticmethod
    def _version(row, expected_version):
        if type(expected_version) is not int or row["version"] != expected_version:
            _error("The record changed; reload before trying again", "version_conflict")

    @staticmethod
    def _composition(composition):
        if not isinstance(composition, dict):
            raise PushError("Composition must be an object")
        if not isinstance(composition.get("text"), str):
            raise PushError("Composition text must be a string")
        if not isinstance(composition.get("settings"), dict):
            raise PushError("Composition settings must be an object")
        for field in ("asset_ids", "targets"):
            value = composition.get(field)
            if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
                raise PushError("Composition assets and targets must be lists of IDs")
            if len(set(value)) != len(value):
                raise PushError("Composition assets and targets cannot contain duplicate IDs")
        return json.loads(_json(composition))

    def _payload(self, payload):
        if not isinstance(payload, dict):
            raise PushError("Preview payload must be an object")
        value = json.loads(_json(payload))
        self._composition(value.get("composition"))
        plan, targets = value.get("plan"), value.get("targets")
        if not isinstance(plan, list) or not plan:
            raise PushError("A preview requires a nonempty delivery plan")
        for part in plan:
            if not isinstance(part, dict) or part.get("kind") not in {"text", "photo", "album"}:
                raise PushError("Invalid delivery plan part")
            if not isinstance(part.get("text"), str) or not isinstance(part.get("entities"), list):
                raise PushError("Invalid delivery text or entities")
            if any(not isinstance(entity, dict) for entity in part["entities"]):
                raise PushError("Invalid delivery entity")
            assets = part.get("asset_ids")
            if not isinstance(assets, list) or any(not isinstance(asset, str) or not asset for asset in assets):
                raise PushError("Invalid delivery assets")
        if not isinstance(targets, list) or not targets or any(not isinstance(t, str) or not t for t in targets):
            raise PushError("A preview requires target IDs")
        if len(set(targets)) != len(targets):
            raise PushError("Target IDs must be unique")
        scheduled = value.get("scheduled_at")
        if scheduled is not None:
            if isinstance(scheduled, bool) or not isinstance(scheduled, (int, float)) or not math.isfinite(scheduled):
                raise PushError("Scheduled time must be a UTC timestamp")
            value["scheduled_at"] = float(scheduled)
        else:
            value["scheduled_at"] = None
        return {key: value[key] for key in ("composition", "plan", "targets", "scheduled_at")}

    @staticmethod
    def _asset_ids(payload):
        ids = set(payload["composition"]["asset_ids"])
        for part in payload.get("plan", []):
            ids.update(part["asset_ids"])
        return ids

    def _durable_asset_ref(self, db, asset_id, now):
        return db.execute(
            """SELECT 1 FROM asset_refs r WHERE r.asset_id=? AND (
                (r.owner_type='draft' AND EXISTS(SELECT 1 FROM drafts d WHERE d.id=r.owner_id))
                OR (r.owner_type='task' AND EXISTS(SELECT 1 FROM tasks t WHERE t.id=r.owner_id
                    AND (t.expires_at IS NULL OR t.expires_at>?)))) LIMIT 1""",
            (asset_id, now),
        ).fetchone() is not None

    def _asset_deadline(self, db, asset_id, created_at, now):
        if self._durable_asset_ref(db, asset_id, now):
            return None
        preview = db.execute(
            """SELECT MAX(p.expires_at) AS expires_at FROM asset_refs r
               JOIN previews p ON p.id=r.owner_id
               WHERE r.owner_type='preview' AND r.asset_id=?""", (asset_id,),
        ).fetchone()
        # A preview made from a persistent owner keeps its frozen images usable
        # if that owner is later deleted. Further previews inherit, never extend,
        # this deadline unless a draft or active task acquires a durable reference.
        return max(created_at + TEMP_LIFETIME, preview["expires_at"] or 0)

    def _asset(self, db, asset_id):
        row = db.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
        if not row:
            self._missing(db, "asset", asset_id)
        deadline = self._asset_deadline(db, asset_id, row["created_at"], self._now())
        if row["state"] != "ready" or (deadline is not None and deadline <= self._now()):
            _error("This image has expired", "expired", 410)
        return json.loads(row["metadata"])

    def _refs(self, db, owner_type, owner_id, asset_ids):
        ids = set(asset_ids)
        # Validate before releasing the old references (old, persistent images
        # remain reusable when replacing the same owner's composition).
        for asset_id in ids:
            self._asset(db, asset_id)
        db.execute("DELETE FROM asset_refs WHERE owner_type=? AND owner_id=?", (owner_type, owner_id))
        db.executemany(
            "INSERT INTO asset_refs VALUES (?, ?, ?)",
            [(owner_type, owner_id, asset_id) for asset_id in ids],
        )

    def put_asset(self, metadata):
        value = json.loads(_json(metadata))
        required = {"id", "filename", "mime_type", "size", "width", "height", "sha256"}
        if not isinstance(value, dict) or not required.issubset(value):
            raise PushError("Incomplete asset metadata")
        if not isinstance(value["id"], str) or not value["id"]:
            raise PushError("Asset ID is required")
        value.setdefault("created_at", self._now())
        if isinstance(value["created_at"], bool) or not isinstance(value["created_at"], (int, float)):
            raise PushError("Asset creation time must be a timestamp")
        value["created_at"] = float(value["created_at"])
        with self._transaction() as db:
            if db.execute("SELECT 1 FROM assets WHERE id=?", (value["id"],)).fetchone() or db.execute(
                "SELECT 1 FROM tombstones WHERE owner_type='asset' AND owner_id=?", (value["id"],)
            ).fetchone():
                _error("Asset ID is already in use")
            db.execute(
                "INSERT INTO assets(id, metadata, created_at) VALUES (?, ?, ?)",
                (value["id"], _json(value), value["created_at"]),
            )
            return value

    def get_asset(self, id):
        with self._transaction() as db:
            return self._asset(db, id)

    def _draft(self, db, id):
        row = db.execute("SELECT * FROM drafts WHERE id=?", (id,)).fetchone()
        if not row:
            self._missing(db, "draft", id)
        result = dict(row)
        result["composition"] = json.loads(result["composition"])
        return result

    def list_drafts(self):
        with self._transaction() as db:
            return [self._draft(db, row["id"]) for row in db.execute(
                "SELECT id FROM drafts ORDER BY updated_at DESC, id"
            ).fetchall()]

    def get_draft(self, id):
        with self._transaction() as db:
            return self._draft(db, id)

    def _new_draft(self, db, composition):
        id, now = _id(), self._now()
        for asset_id in composition["asset_ids"]:
            self._asset(db, asset_id)
        db.execute("INSERT INTO drafts VALUES (?, 1, ?, ?, ?)", (id, _json(composition), now, now))
        self._refs(db, "draft", id, composition["asset_ids"])
        return self._draft(db, id)

    def save_draft(self, composition, draft_id=None, expected_version=None, idempotency_key=""):
        request = {"composition": composition, "draft_id": draft_id, "expected_version": expected_version}
        with self._transaction() as db:
            replay = self._replay(db, "save_draft", idempotency_key, request)
            if replay is not None:
                return replay
            value = self._composition(composition)
            if draft_id is None:
                if expected_version is not None:
                    raise PushError("New drafts do not have a version")
                result = self._new_draft(db, value)
            else:
                draft = self._draft(db, draft_id)
                self._version(draft, expected_version)
                self._refs(db, "draft", draft_id, value["asset_ids"])
                db.execute(
                    "UPDATE drafts SET version=version+1, composition=?, updated_at=? WHERE id=? AND version=?",
                    (_json(value), self._now(), draft_id, expected_version),
                )
                result = self._draft(db, draft_id)
            return self._receipt(db, "save_draft", idempotency_key, request, result)

    def _delete_owner(self, db, owner_type, id, reason):
        table = {"draft": "drafts", "task": "tasks", "preview": "previews", "operation": "operations"}[owner_type]
        db.execute("DELETE FROM asset_refs WHERE owner_type=? AND owner_id=?", (owner_type, id))
        db.execute("DELETE FROM " + table + " WHERE id=?", (id,))
        self._tombstone(db, owner_type, id, reason)

    def delete_draft(self, id, expected_version, idempotency_key):
        request = {"id": id, "expected_version": expected_version}
        with self._transaction() as db:
            replay = self._replay(db, "delete_draft", idempotency_key, request)
            if replay is not None:
                return replay
            self._version(self._draft(db, id), expected_version)
            self._delete_owner(db, "draft", id, "deleted")
            return self._receipt(db, "delete_draft", idempotency_key, request, {"id": id, "deleted": True})

    @staticmethod
    def _editable(task):
        if task["first_started_at"] is not None:
            _error("This task has already started sending", "already_started")
        if task["state"] not in {"queued", "scheduled"}:
            _error("This task can no longer be edited or canceled")

    def _source(self, db, source, kind):
        if source is None:
            return None
        if not isinstance(source, dict) or source.get("kind") not in {"draft", "task"} or not isinstance(source.get("id"), str):
            raise PushError("Invalid preview source")
        if source["kind"] == "draft":
            value = self._draft(db, source["id"])
        else:
            value = self._task(db, source["id"])
        if source["kind"] == "task" and kind == "formal":
            self._editable(value)
        self._version(value, source.get("version"))
        return value

    def create_preview(self, payload, source=None, kind="formal"):
        with self._transaction() as db:
            if kind not in {"formal", "test"}:
                raise PushError("Preview kind must be formal or test")
            value = self._payload(payload)
            if kind == "test" and value["scheduled_at"] is not None:
                raise PushError("Test sends cannot be scheduled")
            self._source(db, source, kind)
            now, id = self._now(), _id()
            expires = now + TEMP_LIFETIME
            for asset_id in self._asset_ids(value):
                asset = self._asset(db, asset_id)
                deadline = self._asset_deadline(db, asset_id, asset["created_at"], now)
                if deadline is not None:
                    expires = min(expires, deadline)
            db.execute(
                "INSERT INTO previews VALUES (?, ?, ?, ?, ?, ?, NULL)",
                (id, _json(value), _json(source) if source is not None else None, kind, now, expires),
            )
            self._refs(db, "preview", id, self._asset_ids(value))
            return {"id": id, "expires_at": expires, **value, "kind": kind}

    def _new_parts(self, db, task_id, payload):
        db.executemany(
            "INSERT INTO parts VALUES (?, ?, ?, ?, 'unattempted')",
            [(_id(), task_id, chat_id, position)
             for chat_id in payload["targets"] for position in range(len(payload["plan"]))],
        )

    def confirm_preview(self, id, idempotency_key):
        request = {"preview_id": id}
        with self._transaction() as db:
            replay = self._replay(db, "confirm_preview", idempotency_key, request)
            if replay is not None:
                return replay
            ticket = db.execute("SELECT * FROM previews WHERE id=?", (id,)).fetchone()
            if not ticket:
                self._missing(db, "preview", id)
            if ticket["expires_at"] <= self._now():
                _error("This preview has expired", "expired", 410)
            if ticket["consumed_task_id"]:
                result = self._task(db, ticket["consumed_task_id"])
                return self._receipt(db, "confirm_preview", idempotency_key, request, result)
            payload = json.loads(ticket["payload"])
            source = json.loads(ticket["source"]) if ticket["source"] else None
            self._source(db, source, ticket["kind"])
            assets = self._asset_ids(payload)
            for asset_id in assets:
                self._asset(db, asset_id)
            now = self._now()
            if payload["scheduled_at"] is not None and payload["scheduled_at"] <= now:
                _error("定时时间已过去，请重新预览并选择将来的时间。", "expired", 410)
            state = "scheduled" if payload["scheduled_at"] is not None else "queued"
            if source and source["kind"] == "task" and ticket["kind"] == "formal":
                task_id = source["id"]
                self._refs(db, "task", task_id, assets)
                db.execute(
                    """UPDATE tasks SET version=version+1, state=?, composition=?, plan=?,
                       targets=?, scheduled_at=? WHERE id=? AND version=? AND first_started_at IS NULL""",
                    (state, _json(payload["composition"]), _json(payload["plan"]), _json(payload["targets"]),
                     payload["scheduled_at"], task_id, source["version"]),
                )
                db.execute("DELETE FROM parts WHERE task_id=?", (task_id,))
            else:
                task_id = _id()
                db.execute(
                    "INSERT INTO tasks VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, 1)",
                    (task_id, state, ticket["kind"], _json(payload["composition"]), _json(payload["plan"]),
                     _json(payload["targets"]), payload["scheduled_at"], now),
                )
                self._refs(db, "task", task_id, assets)
            self._new_parts(db, task_id, payload)
            if source and source["kind"] == "draft" and ticket["kind"] == "formal":
                self._delete_owner(db, "draft", source["id"], "draft_consumed")
            db.execute("UPDATE previews SET consumed_task_id=? WHERE id=?", (task_id, id))
            db.execute("DELETE FROM asset_refs WHERE owner_type='preview' AND owner_id=?", (id,))
            result = self._task(db, task_id)
            return self._receipt(db, "confirm_preview", idempotency_key, request, result)

    def _task(self, db, id):
        row = db.execute("SELECT * FROM tasks WHERE id=?", (id,)).fetchone()
        if not row:
            self._missing(db, "task", id)
        if row["expires_at"] is not None and row["expires_at"] <= self._now():
            _error("This task has expired", "expired", 410)
        result = dict(row)
        for key in ("composition", "plan", "targets"):
            result[key] = json.loads(result[key])
        parts = []
        rows = db.execute("SELECT * FROM parts WHERE task_id=?", (id,)).fetchall()
        order = {chat_id: position for position, chat_id in enumerate(result["targets"])}
        for row in sorted(rows, key=lambda item: (order[item["chat_id"]], item["position"])):
            part = {key: row[key] for key in ("id", "chat_id", "position", "state")}
            attempts = []
            for attempt in db.execute(
                "SELECT * FROM attempts WHERE part_id=? ORDER BY started_at, rowid", (row["id"],)
            ).fetchall():
                value = dict(attempt)
                value.pop("part_id")
                value["message_ids"] = json.loads(value["message_ids"])
                attempts.append(value)
            part["attempts"] = attempts
            parts.append(part)
        result["parts"] = parts
        return result

    def list_tasks(self):
        with self._transaction() as db:
            return [self._task(db, row["id"]) for row in db.execute(
                "SELECT id FROM tasks WHERE expires_at IS NULL OR expires_at>? ORDER BY created_at DESC, id",
                (self._now(),),
            ).fetchall()]

    def get_task(self, id):
        with self._transaction() as db:
            return self._task(db, id)

    def _terminal(self, db, id, state):
        now = self._now()
        db.execute(
            "UPDATE tasks SET state=?, version=version+1, finished_at=?, expires_at=? WHERE id=?",
            (state, now, now + TERMINAL_LIFETIME, id),
        )

    def cancel_task(self, id, expected_version, idempotency_key):
        request = {"id": id, "expected_version": expected_version}
        with self._transaction() as db:
            replay = self._replay(db, "cancel_task", idempotency_key, request)
            if replay is not None:
                return replay
            task = self._task(db, id)
            self._editable(task)
            self._version(task, expected_version)
            self._terminal(db, id, "canceled")
            return self._receipt(db, "cancel_task", idempotency_key, request, self._task(db, id))

    def retry_failed_parts(self, id, expected_version, part_ids, idempotency_key):
        request = {"id": id, "expected_version": expected_version, "part_ids": part_ids}
        with self._transaction() as db:
            replay = self._replay(db, "retry_failed_parts", idempotency_key, request)
            if replay is not None:
                return replay
            task = self._task(db, id)
            self._version(task, expected_version)
            if task["state"] not in {"failed", "partial", "unknown"}:
                _error("Only finished tasks with explicit failures can be retried")
            if not isinstance(part_ids, list) or not part_ids or any(not isinstance(p, str) for p in part_ids):
                raise PushError("Select explicitly failed parts to retry")
            if len(set(part_ids)) != len(part_ids):
                raise PushError("Retry part IDs must be unique")
            by_id = {part["id"]: part for part in task["parts"]}
            if any(p not in by_id or by_id[p]["state"] != "failed" for p in part_ids):
                _error("Only explicitly failed parts may be retried")
            for asset_id in self._asset_ids(task):
                self._asset(db, asset_id)
            db.executemany("UPDATE parts SET state='unattempted' WHERE id=? AND state='failed'", [(p,) for p in part_ids])
            db.execute(
                """UPDATE tasks SET state='queued', version=version+1, run_no=run_no+1,
                   finished_at=NULL, expires_at=NULL WHERE id=? AND version=?""",
                (id, expected_version),
            )
            return self._receipt(db, "retry_failed_parts", idempotency_key, request, self._task(db, id))

    def copy_task_to_draft(self, id, expected_version, idempotency_key):
        request = {"id": id, "expected_version": expected_version}
        with self._transaction() as db:
            replay = self._replay(db, "copy_task_to_draft", idempotency_key, request)
            if replay is not None:
                return replay
            task = self._task(db, id)
            self._version(task, expected_version)
            result = self._new_draft(db, task["composition"])
            return self._receipt(db, "copy_task_to_draft", idempotency_key, request, result)

    def delete_task(self, id, expected_version, idempotency_key):
        request = {"id": id, "expected_version": expected_version}
        with self._transaction() as db:
            replay = self._replay(db, "delete_task", idempotency_key, request)
            if replay is not None:
                return replay
            task = self._task(db, id)
            self._version(task, expected_version)
            if task["state"] not in TERMINAL_STATES:
                _error("Cancel or finish an active task before deleting it")
            self._delete_owner(db, "task", id, "deleted")
            return self._receipt(db, "delete_task", idempotency_key, request, {"id": id, "deleted": True})

    @staticmethod
    def _eligible(db, task_id):
        return db.execute(
            """SELECT p.id FROM parts p WHERE p.task_id=? AND p.state='unattempted'
               AND NOT EXISTS (SELECT 1 FROM parts previous WHERE previous.task_id=p.task_id
                 AND previous.chat_id=p.chat_id AND previous.position<p.position AND previous.state!='sent')
               LIMIT 1""", (task_id,),
        ).fetchone() is not None

    def claim_next(self):
        with self._transaction() as db:
            now = self._now()
            rows = db.execute(
                """SELECT * FROM tasks WHERE state IN ('queued','scheduled')
                   AND (scheduled_at IS NULL OR scheduled_at<=? OR first_started_at IS NOT NULL)
                   ORDER BY COALESCE(scheduled_at, created_at), created_at, id""", (now,),
            ).fetchall()
            for row in rows:
                if row["first_started_at"] is None and row["scheduled_at"] is not None and now - row["scheduled_at"] > LATE_GRACE:
                    self._terminal(db, row["id"], "missed")
                    continue
                if not self._eligible(db, row["id"]):
                    self._finish_task(db, row["id"])
                    continue
                db.execute(
                    """UPDATE tasks SET state='sending', version=version+1,
                       first_started_at=COALESCE(first_started_at, ?) WHERE id=? AND version=?""",
                    (now, row["id"], row["version"]),
                )
                return self._task(db, row["id"])
            return None

    def begin_part(self, task_id, part_id):
        with self._transaction() as db:
            task = self._task(db, task_id)
            if task["state"] != "sending":
                _error("The task is not claimed for sending")
            row = db.execute("SELECT * FROM parts WHERE id=? AND task_id=?", (part_id, task_id)).fetchone()
            if not row:
                _error("Delivery part not found", "not_found", 404)
            if row["state"] != "unattempted":
                _error("This delivery part has already been attempted")
            if db.execute(
                "SELECT 1 FROM parts WHERE task_id=? AND chat_id=? AND position<? AND state!='sent' LIMIT 1",
                (task_id, row["chat_id"], row["position"]),
            ).fetchone():
                _error("Earlier delivery parts for this target must succeed first")
            id = _id()
            db.execute("UPDATE parts SET state='inflight' WHERE id=? AND state='unattempted'", (part_id,))
            db.execute(
                "INSERT INTO attempts(id, part_id, run_no, state, started_at) VALUES (?, ?, ?, 'inflight', ?)",
                (id, part_id, task["run_no"], self._now()),
            )
            db.execute("UPDATE tasks SET version=version+1 WHERE id=?", (task_id,))
            return id

    def finish_part(self, attempt_id, state, message_ids=None, error=""):
        if state not in {"sent", "failed", "unknown"}:
            raise PushError("Invalid delivery outcome")
        if message_ids is not None and (not isinstance(message_ids, list) or any(type(i) is not int for i in message_ids)):
            raise PushError("Message IDs must be a list of integers")
        with self._transaction() as db:
            row = db.execute(
                "SELECT a.*, p.task_id FROM attempts a JOIN parts p ON p.id=a.part_id WHERE a.id=?",
                (attempt_id,),
            ).fetchone()
            if not row or row["state"] != "inflight":
                return False
            changed = db.execute(
                """UPDATE attempts SET state=?, finished_at=?, message_ids=?, error=?
                   WHERE id=? AND state='inflight'""",
                (state, self._now(), _json(message_ids or []), str(error), attempt_id),
            ).rowcount
            if not changed:
                return False
            db.execute("UPDATE parts SET state=? WHERE id=? AND state='inflight'", (state, row["part_id"]))
            db.execute("UPDATE tasks SET version=version+1 WHERE id=?", (row["task_id"],))
            return True

    def _finish_task(self, db, id):
        task = self._task(db, id)
        if task["state"] in TERMINAL_STATES:
            return task
        states = {part["state"] for part in task["parts"]}
        if "inflight" in states or self._eligible(db, id):
            _error("The task still has delivery work in progress")
        if "unknown" in states:
            state = "unknown"
        elif "failed" in states:
            state = "partial" if "sent" in states else "failed"
        elif states == {"sent"}:
            state = "succeeded"
        else:
            _error("The task has unfinished delivery parts")
        self._terminal(db, id, state)
        return self._task(db, id)

    def finish_task(self, task_id):
        with self._transaction() as db:
            return self._finish_task(db, task_id)

    def recover(self):
        with self._transaction() as db:
            now = self._now()
            unknown = db.execute(
                "UPDATE attempts SET state='unknown', finished_at=?, error='Interrupted during delivery' WHERE state='inflight'",
                (now,),
            ).rowcount
            db.execute("UPDATE parts SET state='unknown' WHERE state='inflight'")
            recovered = missed = 0
            for row in db.execute("SELECT * FROM tasks WHERE state IN ('sending','queued','scheduled')").fetchall():
                if row["state"] == "sending":
                    if self._eligible(db, row["id"]):
                        db.execute("UPDATE tasks SET state='queued', version=version+1 WHERE id=?", (row["id"],))
                        recovered += 1
                    else:
                        self._finish_task(db, row["id"])
                elif row["first_started_at"] is None and row["scheduled_at"] is not None and row["scheduled_at"] <= now:
                    if now - row["scheduled_at"] > LATE_GRACE:
                        self._terminal(db, row["id"], "missed")
                        missed += 1
                    elif row["state"] == "scheduled":
                        db.execute("UPDATE tasks SET state='queued', version=version+1 WHERE id=?", (row["id"],))
                        recovered += 1
            interrupted = db.execute(
                """UPDATE operations SET state='failed', error='Interrupted by restart',
                   finished_at=? WHERE state IN ('pending','running')""", (now,),
            ).rowcount
            return {"unknown_attempts": unknown, "requeued_tasks": recovered, "missed_tasks": missed,
                    "interrupted_operations": interrupted}

    def _operation(self, db, id):
        row = db.execute("SELECT * FROM operations WHERE id=?", (id,)).fetchone()
        if not row:
            self._missing(db, "operation", id)
        if row["expires_at"] <= self._now():
            _error("This operation has expired", "expired", 410)
        value = dict(row)
        for key in ("input_data", "editor_revision", "result"):
            value[key] = json.loads(value[key]) if value[key] is not None else None
        return value

    def new_operation(self, kind, input_data, editor_revision, idempotency_key):
        request = {"kind": kind, "input_data": input_data, "editor_revision": editor_revision}
        with self._transaction() as db:
            replay = self._replay(db, "new_operation", idempotency_key, request)
            if replay is not None:
                return replay
            if not isinstance(kind, str) or not kind or not isinstance(input_data, dict):
                raise PushError("An operation requires a kind and input object")
            id, now = _id(), self._now()
            db.execute(
                """INSERT INTO operations(id,kind,input_data,editor_revision,state,created_at,expires_at)
                   VALUES (?, ?, ?, ?, 'pending', ?, ?)""",
                (id, kind, _json(input_data), _json(editor_revision), now, now + TEMP_LIFETIME),
            )
            return self._receipt(db, "new_operation", idempotency_key, request, self._operation(db, id))

    def claim_operation(self):
        with self._transaction() as db:
            row = db.execute(
                "SELECT id FROM operations WHERE state='pending' AND expires_at>? ORDER BY created_at, rowid LIMIT 1",
                (self._now(),),
            ).fetchone()
            if not row:
                return None
            db.execute("UPDATE operations SET state='running', started_at=? WHERE id=? AND state='pending'", (self._now(), row["id"]))
            return self._operation(db, row["id"])

    def finish_operation(self, id, result=None, error=""):
        with self._transaction() as db:
            operation = self._operation(db, id)
            # A late completion after recovery must not resurrect interrupted AI.
            if operation["state"] != "running":
                return operation
            db.execute(
                "UPDATE operations SET state=?, result=?, error=?, finished_at=? WHERE id=? AND state='running'",
                ("failed" if error else "succeeded", _json(result), str(error), self._now(), id),
            )
            return self._operation(db, id)

    def get_operation(self, id):
        with self._transaction() as db:
            return self._operation(db, id)

    def cleanup(self):
        """Expire owners and reserve orphan files; caller unlinks returned assets.

        A failed unlink is retried by subsequent calls.  Tombstones cannot gain
        new references, even if another thread attempts a clone or publish.
        """
        with self._transaction() as db:
            now = self._now()
            for owner_type, table in (("preview", "previews"), ("operation", "operations")):
                for row in db.execute("SELECT id FROM " + table + " WHERE expires_at<=?", (now,)).fetchall():
                    self._delete_owner(db, owner_type, row["id"], "expired")
            for row in db.execute(
                """SELECT id FROM tasks WHERE expires_at<=?
                   AND state IN ('succeeded','failed','partial','unknown','canceled','missed')""", (now,),
            ).fetchall():
                self._delete_owner(db, "task", row["id"], "expired")
            # Keep only request fingerprints after receipt retention, so an old
            # key cannot create new work yet task/AI content is actually purged.
            db.execute("UPDATE idempotency SET receipt='null' WHERE expires_at<=? AND receipt!='null'", (now,))
            db.execute(
                """UPDATE assets SET state='deleting' WHERE state='ready' AND created_at<=?
                   AND NOT EXISTS(SELECT 1 FROM asset_refs r WHERE r.asset_id=assets.id)""",
                (now - TEMP_LIFETIME,),
            )
            return [dict(json.loads(row["metadata"]), state="deleting") for row in db.execute(
                "SELECT metadata FROM assets WHERE state='deleting' ORDER BY created_at, id"
            ).fetchall()]

    def asset_deleted(self, id):
        with self._transaction() as db:
            row = db.execute("SELECT state FROM assets WHERE id=?", (id,)).fetchone()
            if not row:
                return
            if row["state"] != "deleting":
                _error("Only reserved orphan images may be removed")
            db.execute("DELETE FROM assets WHERE id=? AND state='deleting'", (id,))
            self._tombstone(db, "asset", id, "expired")
