"""Small durable store for journal-derived specialist workflows.

The market cache is replaceable, while mining, combat, carrier, and surface
survey work belongs to a commander.  This module keeps those reducers in the
separate commander database and makes journal replay idempotent without
coupling them to the journal tailer or HTTP server.
"""

from __future__ import annotations

import copy
import hashlib
import json
import time
from datetime import datetime, timezone

from . import marketdb


SCHEMA = """
CREATE TABLE IF NOT EXISTS specialist_state(
    commander_id TEXT NOT NULL,
    workflow TEXT NOT NULL,
    state_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(commander_id, workflow)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS specialist_events(
    commander_id TEXT NOT NULL,
    workflow TEXT NOT NULL,
    event_uid TEXT NOT NULL,
    event_ts INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    PRIMARY KEY(commander_id, workflow, event_uid)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_specialist_events_time
    ON specialist_events(commander_id, workflow, event_ts);
CREATE TABLE IF NOT EXISTS specialist_history(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    commander_id TEXT NOT NULL,
    workflow TEXT NOT NULL,
    session_key TEXT NOT NULL,
    started_ts INTEGER,
    ended_ts INTEGER,
    summary_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(commander_id, workflow, session_key));
CREATE INDEX IF NOT EXISTS idx_specialist_history_time
    ON specialist_history(commander_id, workflow, ended_ts DESC);
"""


def ensure_schema() -> None:
    marketdb.ensure_user_schema(SCHEMA)


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def event_epoch_ms(value) -> int:
    """Convert a journal timestamp or numeric time to epoch milliseconds."""
    if isinstance(value, (int, float)):
        return int(value if value > 10_000_000_000 else value * 1000)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return int(parsed.timestamp() * 1000)
        except ValueError:
            pass
    return int(time.time() * 1000)


def event_uid(event: dict, explicit: str | None = None) -> str:
    """Stable replay key; callers should pass the ledger UID when available."""
    if explicit:
        return str(explicit)
    raw = json.dumps(
        event, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class WorkflowStore:
    """Commander-scoped JSON state with transactional event de-duplication."""

    def __init__(self, workflow: str, default_factory, commander_id: str | None = None):
        if not workflow or not callable(default_factory):
            raise ValueError("workflow and default_factory are required")
        ensure_schema()
        self.workflow = str(workflow)
        self.default_factory = default_factory
        self.commander_id = commander_id or marketdb.active_commander_id()

    def _decode(self, raw: str | None) -> dict:
        if raw is None:
            return self.default_factory()
        try:
            value = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return self.default_factory()
        return value if isinstance(value, dict) else self.default_factory()

    def load(self) -> dict:
        conn = marketdb.connect_user()
        try:
            row = conn.execute(
                "SELECT state_json FROM specialist_state WHERE commander_id=? AND workflow=?",
                (self.commander_id, self.workflow),
            ).fetchone()
            return self._decode(row[0] if row else None)
        finally:
            conn.close()

    @staticmethod
    def _json(value: dict) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def _write(self, conn, state: dict) -> None:
        conn.execute(
            "INSERT INTO specialist_state(commander_id,workflow,state_json,updated_at)"
            " VALUES(?,?,?,?) ON CONFLICT(commander_id,workflow) DO UPDATE SET"
            " state_json=excluded.state_json,updated_at=excluded.updated_at",
            (self.commander_id, self.workflow, self._json(state), utc_iso()),
        )

    def _archive_in_transaction(self, conn, summary: dict | None) -> bool:
        """Insert a completed session using the caller's open transaction."""
        if not summary:
            return False
        session_key = summary.get("session_key")
        if not session_key:
            raise ValueError("archived summary requires session_key")
        before = conn.total_changes
        conn.execute(
            "INSERT OR IGNORE INTO specialist_history("
            "commander_id,workflow,session_key,started_ts,ended_ts,summary_json,created_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (
                self.commander_id,
                self.workflow,
                str(session_key),
                summary.get("started_ts"),
                summary.get("ended_ts"),
                self._json(summary),
                utc_iso(),
            ),
        )
        return conn.total_changes > before

    def mutate(self, callback, *, archive=None) -> tuple[dict, bool]:
        """Atomically mutate a deep copy of current state.

        The callback returns truthy when it changed state.  Returning false
        leaves storage untouched and still returns the current value.
        """
        conn = marketdb.connect_user()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT state_json FROM specialist_state WHERE commander_id=? AND workflow=?",
                (self.commander_id, self.workflow),
            ).fetchone()
            state = self._decode(row[0] if row else None)
            candidate = copy.deepcopy(state)
            changed = bool(callback(candidate))
            if changed:
                self._write(conn, candidate)
                if archive is not None:
                    self._archive_in_transaction(conn, archive(candidate))
            conn.commit()
            return (candidate if changed else state), changed
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def apply_event(
        self, event: dict, reducer, uid: str | None = None, *, archive=None,
    ) -> tuple[dict, bool]:
        """Apply one relevant event exactly once and save the resulting state."""
        if not isinstance(event, dict) or not event.get("event"):
            raise ValueError("event must be an object with an event field")
        key = event_uid(event, uid)
        event_type = str(event["event"])
        event_ts = event_epoch_ms(event.get("timestamp"))
        conn = marketdb.connect_user()
        try:
            conn.execute("BEGIN IMMEDIATE")
            duplicate = conn.execute(
                "SELECT 1 FROM specialist_events WHERE commander_id=? AND workflow=? AND event_uid=?",
                (self.commander_id, self.workflow, key),
            ).fetchone()
            row = conn.execute(
                "SELECT state_json FROM specialist_state WHERE commander_id=? AND workflow=?",
                (self.commander_id, self.workflow),
            ).fetchone()
            state = self._decode(row[0] if row else None)
            if duplicate:
                conn.commit()
                return state, False

            candidate = copy.deepcopy(state)
            changed = bool(reducer(candidate, event, event_ts))
            if changed:
                conn.execute(
                    "INSERT INTO specialist_events(commander_id,workflow,event_uid,event_ts,event_type)"
                    " VALUES(?,?,?,?,?)",
                    (self.commander_id, self.workflow, key, event_ts, event_type),
                )
                self._write(conn, candidate)
                if archive is not None:
                    self._archive_in_transaction(conn, archive(candidate))
            conn.commit()
            return (candidate if changed else state), changed
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def archive(self, session_key: str, summary: dict) -> bool:
        """Persist one completed summary; repeated archive calls are harmless."""
        if not session_key:
            raise ValueError("session_key is required")
        conn = marketdb.connect_user()
        try:
            inserted = self._archive_in_transaction(conn, {**summary, "session_key": session_key})
            conn.commit()
            return inserted
        finally:
            conn.close()

    def history(self, limit: int = 20) -> list[dict]:
        limit = min(max(int(limit), 1), 200)
        conn = marketdb.connect_user()
        try:
            rows = conn.execute(
                "SELECT session_key,started_ts,ended_ts,summary_json FROM specialist_history"
                " WHERE commander_id=? AND workflow=? ORDER BY COALESCE(ended_ts,started_ts) DESC,id DESC"
                " LIMIT ?",
                (self.commander_id, self.workflow, limit),
            ).fetchall()
            result = []
            for key, started, ended, raw in rows:
                item = self._decode(raw)
                item.setdefault("session_key", key)
                item.setdefault("started_ts", started)
                item.setdefault("ended_ts", ended)
                result.append(item)
            return result
        finally:
            conn.close()
