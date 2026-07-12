"""Commander-scoped persistence for the engineering wishlist.

The pre-2.1 wishlist lived in the global settings JSON.  That was convenient
before Frameshift understood multiple commanders, but it could show one
commander's plans while another account (or the same name in Legacy) was
active.  This store keeps the normalized records in commander.db and adopts
the legacy list exactly once, inside a durable transaction.
"""

from __future__ import annotations

import json

from . import marketdb


SCHEMA = """
CREATE TABLE IF NOT EXISTS engineering_wishlist(
    commander_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY(commander_id, item_id)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS engineering_wishlist_order
    ON engineering_wishlist(commander_id, position, item_id);
"""

_MIGRATION_KEY = "engineering_wishlist_legacy_adopted_by"


def ensure_schema() -> None:
    marketdb.ensure_user_schema(SCHEMA)


def _commander_id(value) -> str:
    commander_id = str(value or "").strip()
    if not commander_id or commander_id == "default":
        raise ValueError("A resolved commander profile is required for the engineering wishlist.")
    return commander_id


def _encode(item: dict) -> str:
    return json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _decode(value: str) -> dict | None:
    try:
        item = json.loads(value)
    except (TypeError, ValueError):
        return None
    return item if isinstance(item, dict) else None


def _replace_locked(conn, commander_id: str, items) -> None:
    unique = {}
    for item in items:
        if isinstance(item, dict) and item.get("id"):
            # Keep the latest representation while retaining the first
            # insertion position. Corrupt/hand-edited legacy JSON containing
            # duplicate pins must not make the whole migration fail.
            unique[str(item["id"])] = dict(item)
    conn.execute("DELETE FROM engineering_wishlist WHERE commander_id = ?", (commander_id,))
    conn.executemany(
        "INSERT INTO engineering_wishlist(commander_id,item_id,position,payload)"
        " VALUES(?,?,?,?)",
        [
            (commander_id, item_id, position, _encode(item))
            for position, (item_id, item) in enumerate(unique.items())
        ],
    )


def load(commander_id, *, legacy_items=None) -> tuple[list[dict], bool]:
    """Load one profile's list and atomically adopt legacy global pins once.

    Returns ``(items, adopted_legacy)``.  The caller clears the old settings
    value only after ``adopted_legacy`` is true; if that JSON write fails, the
    marker in commander.db prevents another profile from stealing the pins.
    """
    commander_id = _commander_id(commander_id)
    ensure_schema()
    conn = marketdb.connect_user()
    adopted = False
    try:
        conn.execute("BEGIN IMMEDIATE")
        marker = conn.execute(
            "SELECT value FROM user_meta WHERE key = ?", (_MIGRATION_KEY,)
        ).fetchone()
        if not marker and legacy_items:
            existing = [
                item
                for (payload,) in conn.execute(
                    "SELECT payload FROM engineering_wishlist WHERE commander_id = ?"
                    " ORDER BY position,item_id",
                    (commander_id,),
                )
                if (item := _decode(payload)) is not None
            ]
            by_id = {str(item.get("id")): item for item in existing if item.get("id")}
            legacy_by_id = {}
            for item in legacy_items:
                if isinstance(item, dict) and item.get("id"):
                    legacy_by_id[str(item["id"])] = dict(item)
            for item_id, item in legacy_by_id.items():
                by_id.setdefault(item_id, item)
            _replace_locked(conn, commander_id, list(by_id.values()))
            conn.execute(
                "INSERT OR REPLACE INTO user_meta(key,value) VALUES(?,?)",
                (_MIGRATION_KEY, commander_id),
            )
            adopted = True
        rows = conn.execute(
            "SELECT payload FROM engineering_wishlist WHERE commander_id = ?"
            " ORDER BY position,item_id",
            (commander_id,),
        ).fetchall()
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return [item for (payload,) in rows if (item := _decode(payload)) is not None], adopted


def save(commander_id, items) -> list[dict]:
    commander_id = _commander_id(commander_id)
    values = [dict(item) for item in items if isinstance(item, dict) and item.get("id")]
    ensure_schema()
    conn = marketdb.connect_user()
    try:
        conn.execute("BEGIN IMMEDIATE")
        _replace_locked(conn, commander_id, values)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return values
