"""Account-free shared operations boards with deterministic JSON merging."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone

from . import marketdb


FORMAT = "frameshift.operations"
FORMAT_VERSION = 1
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS operation_boards(
    id TEXT PRIMARY KEY,
    owner_commander_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    revision INTEGER NOT NULL,
    updated_by TEXT NOT NULL,
    version_hash TEXT NOT NULL,
    deleted_at TEXT);
CREATE INDEX IF NOT EXISTS idx_operation_boards_status
    ON operation_boards(status,updated_at DESC);

CREATE TABLE IF NOT EXISTS operation_objectives(
    id TEXT PRIMARY KEY,
    board_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open',
    priority INTEGER NOT NULL DEFAULT 50,
    system TEXT,
    station TEXT,
    deadline INTEGER,
    payload TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    revision INTEGER NOT NULL,
    updated_by TEXT NOT NULL,
    version_hash TEXT NOT NULL,
    deleted_at TEXT,
    FOREIGN KEY(board_id) REFERENCES operation_boards(id));
CREATE INDEX IF NOT EXISTS idx_operation_objectives_board
    ON operation_objectives(board_id,status,priority DESC);

CREATE TABLE IF NOT EXISTS operation_assignments(
    id TEXT PRIMARY KEY,
    board_id TEXT NOT NULL,
    objective_id TEXT,
    assignee TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'assigned',
    payload TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    revision INTEGER NOT NULL,
    updated_by TEXT NOT NULL,
    version_hash TEXT NOT NULL,
    deleted_at TEXT,
    FOREIGN KEY(board_id) REFERENCES operation_boards(id),
    FOREIGN KEY(objective_id) REFERENCES operation_objectives(id));
CREATE INDEX IF NOT EXISTS idx_operation_assignments_board
    ON operation_assignments(board_id,objective_id,assignee);

CREATE TABLE IF NOT EXISTS operation_reservations(
    id TEXT PRIMARY KEY,
    board_id TEXT NOT NULL,
    objective_id TEXT,
    resource_type TEXT NOT NULL,
    resource_key TEXT NOT NULL,
    amount REAL NOT NULL,
    unit TEXT NOT NULL DEFAULT '',
    assignee TEXT,
    status TEXT NOT NULL DEFAULT 'reserved',
    payload TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    revision INTEGER NOT NULL,
    updated_by TEXT NOT NULL,
    version_hash TEXT NOT NULL,
    deleted_at TEXT,
    FOREIGN KEY(board_id) REFERENCES operation_boards(id),
    FOREIGN KEY(objective_id) REFERENCES operation_objectives(id));
CREATE INDEX IF NOT EXISTS idx_operation_reservations_board
    ON operation_reservations(board_id,resource_type,resource_key,status);

CREATE TABLE IF NOT EXISTS operation_contributions(
    id TEXT PRIMARY KEY,
    board_id TEXT NOT NULL,
    objective_id TEXT,
    contributor TEXT NOT NULL,
    kind TEXT NOT NULL,
    amount REAL NOT NULL,
    unit TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    evidence TEXT,
    payload TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    revision INTEGER NOT NULL,
    updated_by TEXT NOT NULL,
    version_hash TEXT NOT NULL,
    deleted_at TEXT,
    FOREIGN KEY(board_id) REFERENCES operation_boards(id),
    FOREIGN KEY(objective_id) REFERENCES operation_objectives(id));
CREATE INDEX IF NOT EXISTS idx_operation_contributions_board
    ON operation_contributions(board_id,objective_id,contributor,kind);

CREATE TABLE IF NOT EXISTS operation_conflicts(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    table_name TEXT NOT NULL,
    record_id TEXT NOT NULL,
    board_id TEXT,
    local_version TEXT,
    incoming_version TEXT,
    losing_payload TEXT NOT NULL,
    detected_at TEXT NOT NULL);
"""


_TABLES = {
    "boards": {
        "table": "operation_boards", "prefix": "board",
        "fields": (
            "id", "owner_commander_id", "title", "description", "status",
            "created_at", "updated_at", "revision", "updated_by", "version_hash", "deleted_at",
        ),
    },
    "objectives": {
        "table": "operation_objectives", "prefix": "opobj",
        "fields": (
            "id", "board_id", "title", "description", "status", "priority", "system",
            "station", "deadline", "payload", "created_at", "updated_at", "revision",
            "updated_by", "version_hash", "deleted_at",
        ),
    },
    "assignments": {
        "table": "operation_assignments", "prefix": "assign",
        "fields": (
            "id", "board_id", "objective_id", "assignee", "role", "status", "payload",
            "created_at", "updated_at", "revision", "updated_by", "version_hash", "deleted_at",
        ),
    },
    "reservations": {
        "table": "operation_reservations", "prefix": "reserve",
        "fields": (
            "id", "board_id", "objective_id", "resource_type", "resource_key", "amount",
            "unit", "assignee", "status", "payload", "created_at", "updated_at", "revision",
            "updated_by", "version_hash", "deleted_at",
        ),
    },
    "contributions": {
        "table": "operation_contributions", "prefix": "contrib",
        "fields": (
            "id", "board_id", "objective_id", "contributor", "kind", "amount", "unit",
            "note", "evidence", "payload", "created_at", "updated_at", "revision",
            "updated_by", "version_hash", "deleted_at",
        ),
    },
}


def ensure_schema() -> None:
    marketdb.ensure_user_schema(SCHEMA)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def _text(value, limit=1000) -> str:
    return str(value or "").strip()[:limit]


def _json(value) -> str:
    return json.dumps(value if value is not None else {}, sort_keys=True, separators=(",", ":"), default=str)


def _loads(value):
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return {}


def _version_hash(record: dict) -> str:
    ignored = {"updated_at", "revision", "updated_by", "version_hash"}
    stable = {key: record.get(key) for key in sorted(record) if key not in ignored}
    return hashlib.sha256(_json(stable).encode("utf-8")).hexdigest()[:24]


def _version_token(record: dict) -> str:
    return f"{int(record.get('revision') or 0)}:{record.get('updated_at') or ''}:{record.get('updated_by') or ''}:{record.get('version_hash') or ''}"


def _connect():
    conn = marketdb.connect_user()
    conn.row_factory = sqlite3.Row
    return conn


def _node_id() -> str:
    conn = _connect()
    try:
        row = conn.execute("SELECT value FROM user_meta WHERE key='operations_node_id'").fetchone()
        if row:
            return row[0]
        value = "node-" + uuid.uuid4().hex
        conn.execute(
            "INSERT OR IGNORE INTO user_meta(key,value) VALUES('operations_node_id',?)", (value,)
        )
        conn.commit()
        row = conn.execute("SELECT value FROM user_meta WHERE key='operations_node_id'").fetchone()
        return row[0]
    finally:
        conn.close()


def _decode_record(row) -> dict:
    value = dict(row)
    if "payload" in value:
        value["payload"] = _loads(value["payload"])
    return value


class OperationsBoard:
    """Local board repository suitable for file/clipboard/QR-style exchange."""

    def __init__(self, commander_id: str | None = None):
        ensure_schema()
        self.commander_id = commander_id or marketdb.active_commander_id()
        self.node_id = _node_id()

    def _insert(self, kind: str, record: dict) -> dict:
        spec = _TABLES[kind]
        record["revision"] = int(record.get("revision") or 1)
        record["updated_by"] = record.get("updated_by") or self.node_id
        record["version_hash"] = _version_hash(record)
        fields = spec["fields"]
        conn = _connect()
        try:
            conn.execute(
                f"INSERT INTO {spec['table']}(" + ",".join(fields) + ") VALUES("
                + ",".join("?" for _ in fields) + ")",
                tuple(record.get(field) for field in fields),
            )
            conn.commit()
            return _decode_record(conn.execute(
                f"SELECT * FROM {spec['table']} WHERE id=?", (record["id"],)
            ).fetchone())
        finally:
            conn.close()

    def create_board(self, title: str, description="") -> dict:
        title = _text(title, 240)
        if not title:
            raise ValueError("board title is required")
        now = _now()
        return self._insert("boards", {
            "id": _new_id("board"), "owner_commander_id": self.commander_id,
            "title": title, "description": _text(description, 4000), "status": "active",
            "created_at": now, "updated_at": now, "deleted_at": None,
        })

    def add_objective(
        self, board_id: str, title: str, *, description="", priority=50,
        system=None, station=None, deadline=None, payload=None,
    ) -> dict:
        self._require_board(board_id)
        title = _text(title, 240)
        if not title:
            raise ValueError("objective title is required")
        now = _now()
        return self._insert("objectives", {
            "id": _new_id("opobj"), "board_id": board_id, "title": title,
            "description": _text(description, 4000), "status": "open",
            "priority": max(0, min(int(priority), 100)), "system": _text(system, 160) or None,
            "station": _text(station, 160) or None,
            "deadline": int(deadline) if deadline is not None else None,
            "payload": _json(payload), "created_at": now, "updated_at": now,
            "deleted_at": None,
        })

    def assign(
        self, board_id: str, assignee: str, *, objective_id=None, role="", payload=None,
    ) -> dict:
        self._require_board(board_id)
        self._require_objective(board_id, objective_id)
        assignee = _text(assignee, 160)
        if not assignee:
            raise ValueError("assignee is required")
        now = _now()
        return self._insert("assignments", {
            "id": _new_id("assign"), "board_id": board_id, "objective_id": objective_id,
            "assignee": assignee, "role": _text(role, 160), "status": "assigned",
            "payload": _json(payload), "created_at": now, "updated_at": now,
            "deleted_at": None,
        })

    def reserve(
        self, board_id: str, resource_type: str, resource_key: str, amount,
        *, objective_id=None, unit="", assignee=None, payload=None,
    ) -> dict:
        self._require_board(board_id)
        self._require_objective(board_id, objective_id)
        amount = float(amount)
        if amount <= 0:
            raise ValueError("reservation amount must be positive")
        if not _text(resource_type, 80) or not _text(resource_key, 200):
            raise ValueError("resource type and key are required")
        now = _now()
        return self._insert("reservations", {
            "id": _new_id("reserve"), "board_id": board_id, "objective_id": objective_id,
            "resource_type": _text(resource_type, 80), "resource_key": _text(resource_key, 200),
            "amount": amount, "unit": _text(unit, 40), "assignee": _text(assignee, 160) or None,
            "status": "reserved", "payload": _json(payload), "created_at": now,
            "updated_at": now, "deleted_at": None,
        })

    def contribute(
        self, board_id: str, contributor: str, kind: str, amount,
        *, objective_id=None, unit="", note="", evidence=None, payload=None,
    ) -> dict:
        self._require_board(board_id)
        self._require_objective(board_id, objective_id)
        contributor, kind = _text(contributor, 160), _text(kind, 100)
        amount = float(amount)
        if not contributor or not kind:
            raise ValueError("contributor and contribution kind are required")
        if amount <= 0:
            raise ValueError("contribution amount must be positive")
        now = _now()
        return self._insert("contributions", {
            "id": _new_id("contrib"), "board_id": board_id, "objective_id": objective_id,
            "contributor": contributor, "kind": kind, "amount": amount,
            "unit": _text(unit, 40), "note": _text(note, 2000),
            "evidence": _text(evidence, 1000) or None, "payload": _json(payload),
            "created_at": now, "updated_at": now, "deleted_at": None,
        })

    def _require_board(self, board_id):
        if not board_id:
            raise ValueError("board id is required")
        conn = _connect()
        try:
            if not conn.execute(
                "SELECT 1 FROM operation_boards WHERE id=? AND deleted_at IS NULL", (board_id,)
            ).fetchone():
                raise KeyError("unknown operations board")
        finally:
            conn.close()

    def _require_objective(self, board_id, objective_id):
        if objective_id is None:
            return
        conn = _connect()
        try:
            if not conn.execute(
                "SELECT 1 FROM operation_objectives WHERE id=? AND board_id=? AND deleted_at IS NULL",
                (objective_id, board_id),
            ).fetchone():
                raise KeyError("unknown objective for operations board")
        finally:
            conn.close()

    def update(self, kind: str, record_id: str, **changes) -> dict:
        if kind not in _TABLES:
            raise ValueError("unsupported operations record kind")
        spec = _TABLES[kind]
        immutable = {
            "id", "board_id", "owner_commander_id", "objective_id", "created_at",
            "revision", "updated_at", "updated_by", "version_hash",
        }
        allowed = set(spec["fields"]) - immutable
        changes = {key: value for key, value in changes.items() if key in allowed}
        for key in ("title", "description", "status", "system", "station", "assignee", "role",
                    "resource_type", "resource_key", "unit", "contributor", "kind", "note", "evidence"):
            if key in changes:
                changes[key] = _text(changes[key], 4000)
        if "priority" in changes:
            changes["priority"] = max(0, min(int(changes["priority"]), 100))
        if "amount" in changes:
            changes["amount"] = float(changes["amount"])
            if changes["amount"] <= 0:
                raise ValueError("amount must be positive")
        if "payload" in changes:
            changes["payload"] = _json(changes["payload"])
        conn = _connect()
        try:
            # The HTTP server is intentionally threaded and a board is often
            # open on several paired devices.  Acquire the write reservation
            # before reading the current revision so concurrent field edits
            # serialize against the latest committed record instead of both
            # producing revision N+1 and silently losing the first writer.
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(f"SELECT * FROM {spec['table']} WHERE id=?", (record_id,)).fetchone()
            if not row:
                raise KeyError("unknown operations record")
            record = dict(row)
            record.update(changes)
            record["revision"] = int(record["revision"]) + 1
            record["updated_at"] = _now()
            record["updated_by"] = self.node_id
            record["version_hash"] = _version_hash(record)
            fields = [field for field in spec["fields"] if field != "id"]
            conn.execute(
                f"UPDATE {spec['table']} SET " + ",".join(f"{field}=?" for field in fields)
                + " WHERE id=?",
                tuple(record.get(field) for field in fields) + (record_id,),
            )
            conn.commit()
            return _decode_record(conn.execute(
                f"SELECT * FROM {spec['table']} WHERE id=?", (record_id,)
            ).fetchone())
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def remove(self, kind: str, record_id: str) -> dict:
        return self.update(kind, record_id, deleted_at=_now())

    def list_boards(self, *, include_deleted=False) -> list[dict]:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT * FROM operation_boards"
                + ("" if include_deleted else " WHERE deleted_at IS NULL")
                + " ORDER BY updated_at DESC"
            ).fetchall()
            return [_decode_record(row) for row in rows]
        finally:
            conn.close()

    def snapshot(self, board_id: str, *, include_deleted=False) -> dict:
        conn = _connect()
        try:
            board_row = conn.execute("SELECT * FROM operation_boards WHERE id=?", (board_id,)).fetchone()
            if not board_row or (board_row["deleted_at"] and not include_deleted):
                raise KeyError("unknown operations board")
            result = {"board": _decode_record(board_row)}
            for kind, spec in _TABLES.items():
                if kind == "boards":
                    continue
                rows = conn.execute(
                    f"SELECT * FROM {spec['table']} WHERE board_id=?"
                    + ("" if include_deleted else " AND deleted_at IS NULL")
                    + " ORDER BY created_at,id",
                    (board_id,),
                ).fetchall()
                result[kind] = [_decode_record(row) for row in rows]
            return result
        finally:
            conn.close()

    def export_data(self, board_id: str | None = None) -> dict:
        conn = _connect()
        try:
            if board_id:
                boards = conn.execute("SELECT * FROM operation_boards WHERE id=?", (board_id,)).fetchall()
                if not boards:
                    raise KeyError("unknown operations board")
            else:
                boards = conn.execute("SELECT * FROM operation_boards ORDER BY id").fetchall()
            board_ids = [row["id"] for row in boards]
            records = {"boards": [_decode_record(row) for row in boards]}
            for kind, spec in _TABLES.items():
                if kind == "boards":
                    continue
                if board_ids:
                    marks = ",".join("?" for _ in board_ids)
                    rows = conn.execute(
                        f"SELECT * FROM {spec['table']} WHERE board_id IN ({marks}) ORDER BY id",
                        tuple(board_ids),
                    ).fetchall()
                else:
                    rows = []
                records[kind] = [_decode_record(row) for row in rows]
        finally:
            conn.close()
        return {
            "format": FORMAT, "version": FORMAT_VERSION, "exported_at": _now(),
            "node_id": self.node_id, "records": records,
        }

    def export_json(self, board_id: str | None = None, *, indent=2) -> str:
        return json.dumps(self.export_data(board_id), ensure_ascii=False, indent=indent, sort_keys=True)

    @staticmethod
    def _normalise_import(kind: str, raw: dict) -> dict:
        spec = _TABLES[kind]
        if not isinstance(raw, dict):
            raise ValueError(f"{kind} record must be an object")
        record = {field: raw.get(field) for field in spec["fields"]}
        if not _ID_RE.fullmatch(str(record.get("id") or "")):
            raise ValueError(f"invalid {kind} record id")
        if kind != "boards" and not _ID_RE.fullmatch(str(record.get("board_id") or "")):
            raise ValueError(f"invalid board id on {kind} record")
        record["revision"] = max(1, int(record.get("revision") or 1))
        record["updated_at"] = _text(record.get("updated_at"), 80) or _now()
        record["created_at"] = _text(record.get("created_at"), 80) or record["updated_at"]
        record["updated_by"] = _text(record.get("updated_by"), 128) or "unknown-node"
        record["deleted_at"] = _text(record.get("deleted_at"), 80) or None
        if isinstance(record.get("payload"), (dict, list)):
            record["payload"] = _json(record["payload"])
        elif "payload" in record:
            # Re-encode to reject malformed/non-object JSON while retaining
            # portable values from older versions.
            record["payload"] = _json(_loads(record.get("payload")))
        incoming_hash = _text(record.get("version_hash"), 64)
        calculated = _version_hash(record)
        record["version_hash"] = incoming_hash or calculated
        return record

    def _merge_record(self, conn, kind: str, incoming: dict) -> tuple[str, bool]:
        spec = _TABLES[kind]
        local_row = conn.execute(
            f"SELECT * FROM {spec['table']} WHERE id=?", (incoming["id"],)
        ).fetchone()
        if not local_row:
            fields = spec["fields"]
            conn.execute(
                f"INSERT INTO {spec['table']}(" + ",".join(fields) + ") VALUES("
                + ",".join("?" for _ in fields) + ")",
                tuple(incoming.get(field) for field in fields),
            )
            return "inserted", False
        local = dict(local_row)
        if local.get("version_hash") == incoming.get("version_hash"):
            return "unchanged", False
        local_revision, incoming_revision = int(local["revision"]), int(incoming["revision"])
        conflict = local_revision == incoming_revision
        incoming_wins = (
            incoming_revision > local_revision
            or conflict and _version_token(incoming) > _version_token(local)
        )
        if conflict:
            loser = local if incoming_wins else incoming
            conn.execute(
                "INSERT INTO operation_conflicts("
                "table_name,record_id,board_id,local_version,incoming_version,losing_payload,detected_at)"
                " VALUES(?,?,?,?,?,?,?)",
                (
                    spec["table"], incoming["id"], incoming.get("board_id") or incoming.get("id"),
                    _version_token(local), _version_token(incoming), _json(loser), _now(),
                ),
            )
        if incoming_wins:
            fields = [field for field in spec["fields"] if field != "id"]
            conn.execute(
                f"UPDATE {spec['table']} SET " + ",".join(f"{field}=?" for field in fields)
                + " WHERE id=?",
                tuple(incoming.get(field) for field in fields) + (incoming["id"],),
            )
            return "updated", conflict
        return "kept_local", conflict

    def import_json(self, value) -> dict:
        if isinstance(value, str):
            if len(value.encode("utf-8")) > 20 * 1024 * 1024:
                raise ValueError("operations import is too large")
            try:
                value = json.loads(value)
            except ValueError as exc:
                raise ValueError("invalid operations JSON") from exc
        if not isinstance(value, dict) or value.get("format") != FORMAT:
            raise ValueError("not a Frameshift operations export")
        if int(value.get("version") or 0) != FORMAT_VERSION:
            raise ValueError("unsupported operations export version")
        records = value.get("records")
        if not isinstance(records, dict):
            raise ValueError("operations export has no records")
        total = sum(len(records.get(kind) or []) for kind in _TABLES)
        if total > 50_000:
            raise ValueError("operations export has too many records")
        report = {"inserted": 0, "updated": 0, "unchanged": 0, "kept_local": 0, "conflicts": 0}
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            for kind in ("boards", "objectives", "assignments", "reservations", "contributions"):
                rows = records.get(kind) or []
                if not isinstance(rows, list):
                    raise ValueError(f"operations {kind} must be a list")
                for raw in rows:
                    incoming = self._normalise_import(kind, raw)
                    result, conflict = self._merge_record(conn, kind, incoming)
                    report[result] += 1
                    report["conflicts"] += int(conflict)
            conn.commit()
            return report
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def conflicts(self, board_id=None, limit=200) -> list[dict]:
        conn = _connect()
        try:
            if board_id:
                rows = conn.execute(
                    "SELECT * FROM operation_conflicts WHERE board_id=? ORDER BY id DESC LIMIT ?",
                    (board_id, max(1, min(int(limit), 5000))),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM operation_conflicts ORDER BY id DESC LIMIT ?",
                    (max(1, min(int(limit), 5000)),),
                ).fetchall()
            values = []
            for row in rows:
                item = dict(row)
                item["losing_payload"] = _loads(item["losing_payload"])
                values.append(item)
            return values
        finally:
            conn.close()
