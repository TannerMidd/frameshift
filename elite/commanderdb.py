"""Durable commander-owned storage.

The galaxy market cache is intentionally disposable: it is periodically
rebuilt from Spansh and then refreshed by EDDN.  Commander history, watches,
and any future planning/operations data are not disposable, so they live in a
separate SQLite database and are never part of a market-cache file swap.

This module contains only storage primitives.  ``marketdb.connect()`` attaches
this database as ``commander`` so legacy unqualified queries (``trade_log``,
``watches``, etc.) continue to work without a flag day migration.
"""

from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


ALIAS = "commander"
SCHEMA_VERSION = 4

# These tables are part of the durable commander contract.  New features must
# add their tables here (or call ensure_schema with their own idempotent DDL),
# never to the disposable market cache.
USER_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS commander_profiles(
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    galaxy_mode TEXT NOT NULL DEFAULT 'live',
    created_at TEXT NOT NULL,
    last_seen_at TEXT,
    is_active INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS trade_log(
    commander_id TEXT NOT NULL DEFAULT 'default',
    ts INTEGER NOT NULL,
    event TEXT NOT NULL,
    symbol TEXT NOT NULL,
    name TEXT,
    count INTEGER NOT NULL DEFAULT 0,
    price INTEGER NOT NULL DEFAULT 0,
    total INTEGER NOT NULL DEFAULT 0,
    profit INTEGER,
    PRIMARY KEY(commander_id, ts, event, symbol, total)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS balance_log(
    commander_id TEXT NOT NULL DEFAULT 'default',
    ts INTEGER NOT NULL,
    balance INTEGER NOT NULL,
    PRIMARY KEY(commander_id, ts)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS income_log(
    commander_id TEXT NOT NULL DEFAULT 'default',
    ts INTEGER NOT NULL,
    category TEXT NOT NULL,
    detail TEXT,
    amount INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(commander_id, ts, category, detail, amount)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS imported_journals(
    commander_id TEXT NOT NULL DEFAULT 'default',
    filename TEXT NOT NULL,
    PRIMARY KEY(commander_id, filename)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS price_history(
    market_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    ts INTEGER NOT NULL,
    buy_price INTEGER NOT NULL DEFAULT 0,
    sell_price INTEGER NOT NULL DEFAULT 0,
    supply INTEGER NOT NULL DEFAULT 0,
    demand INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(market_id, symbol, ts)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS tracked_markets(
    commander_id TEXT NOT NULL DEFAULT 'default',
    market_id INTEGER NOT NULL,
    added_ts INTEGER NOT NULL,
    PRIMARY KEY(commander_id, market_id)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS watches(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    commander_id TEXT NOT NULL DEFAULT 'default',
    created TEXT NOT NULL,
    payload TEXT NOT NULL);
"""

KNOWN_USER_TABLES = frozenset(
    {
        "user_meta",
        "commander_profiles",
        "trade_log",
        "balance_log",
        "income_log",
        "imported_journals",
        "price_history",
        "tracked_markets",
        "watches",
    }
)

PROFILE_SCOPED_TABLES = frozenset(
    {
        "trade_log", "balance_log", "income_log", "imported_journals",
        "tracked_markets", "watches",
        # Optional feature schemas are created lazily by their owning modules,
        # but data entered before the first journal Commander event still
        # belongs to that first real profile.  Keeping the adoption contract
        # here prevents pre-game plans/workflows from being stranded forever
        # under the temporary ``default`` identity.
        "commander_objectives", "commander_alerts",
        "ledger_events", "ledger_journal_files",
        "timing_observations", "timing_pending",
        "specialist_state", "specialist_events", "specialist_history",
        "engineering_wishlist",
    }
)

# Rows in these tables are user-authored local preferences created before a
# journal identity was available, so the first real profile may safely claim
# them. Journal-derived analytics are deliberately excluded: an old v2.0
# database can contain several commanders mixed under ``default`` and those
# rows must instead remain quarantined while the journal sweep reconstructs
# correctly attributed copies.
DEFAULT_PROFILE_ADOPTABLE_TABLES = frozenset(
    {
        "tracked_markets", "watches", "commander_objectives", "commander_alerts",
        "engineering_wishlist",
    }
)


# Early development databases gained commander_id with ALTER TABLE. SQLite
# cannot alter a primary key, so those databases still rejected the same
# logical key for a second commander. Canonical definitions let startup
# transactionally rebuild only the affected direct-key tables.
_PROFILE_KEY_SCHEMAS = {
    "trade_log": (("commander_id", "ts", "event", "symbol", "total"), """
        CREATE TABLE {name}(
            commander_id TEXT NOT NULL DEFAULT 'default', ts INTEGER NOT NULL,
            event TEXT NOT NULL, symbol TEXT NOT NULL, name TEXT,
            count INTEGER NOT NULL DEFAULT 0, price INTEGER NOT NULL DEFAULT 0,
            total INTEGER NOT NULL DEFAULT 0, profit INTEGER,
            PRIMARY KEY(commander_id,ts,event,symbol,total)) WITHOUT ROWID
    """),
    "balance_log": (("commander_id", "ts"), """
        CREATE TABLE {name}(
            commander_id TEXT NOT NULL DEFAULT 'default', ts INTEGER NOT NULL,
            balance INTEGER NOT NULL, PRIMARY KEY(commander_id,ts)) WITHOUT ROWID
    """),
    "income_log": (("commander_id", "ts", "category", "detail", "amount"), """
        CREATE TABLE {name}(
            commander_id TEXT NOT NULL DEFAULT 'default', ts INTEGER NOT NULL,
            category TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
            amount INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(commander_id,ts,category,detail,amount)) WITHOUT ROWID
    """),
    "imported_journals": (("commander_id", "filename"), """
        CREATE TABLE {name}(
            commander_id TEXT NOT NULL DEFAULT 'default', filename TEXT NOT NULL,
            PRIMARY KEY(commander_id,filename)) WITHOUT ROWID
    """),
    "tracked_markets": (("commander_id", "market_id"), """
        CREATE TABLE {name}(
            commander_id TEXT NOT NULL DEFAULT 'default', market_id INTEGER NOT NULL,
            added_ts INTEGER NOT NULL, PRIMARY KEY(commander_id,market_id)) WITHOUT ROWID
    """),
    "specialist_state": (("commander_id", "workflow"), """
        CREATE TABLE {name}(
            commander_id TEXT NOT NULL, workflow TEXT NOT NULL,
            state_json TEXT NOT NULL, updated_at TEXT NOT NULL,
            PRIMARY KEY(commander_id,workflow)) WITHOUT ROWID
    """),
    "specialist_events": (("commander_id", "workflow", "event_uid"), """
        CREATE TABLE {name}(
            commander_id TEXT NOT NULL, workflow TEXT NOT NULL,
            event_uid TEXT NOT NULL, event_ts INTEGER NOT NULL, event_type TEXT NOT NULL,
            PRIMARY KEY(commander_id,workflow,event_uid)) WITHOUT ROWID
    """),
    "ledger_journal_files": (("commander_id", "file_key"), """
        CREATE TABLE {name}(
            commander_id TEXT NOT NULL, file_key TEXT NOT NULL, size_bytes INTEGER,
            mtime_ns INTEGER, content_hash TEXT, last_line INTEGER NOT NULL DEFAULT 0,
            event_count INTEGER NOT NULL DEFAULT 0, first_event_ts INTEGER,
            last_event_ts INTEGER, complete INTEGER NOT NULL DEFAULT 0,
            imported_at TEXT, error TEXT, PRIMARY KEY(commander_id,file_key)) WITHOUT ROWID
    """),
    "timing_pending": (("commander_id", "activity", "context_key"), """
        CREATE TABLE {name}(
            commander_id TEXT NOT NULL, activity TEXT NOT NULL,
            context_key TEXT NOT NULL DEFAULT '', started_at INTEGER NOT NULL,
            source_event TEXT, PRIMARY KEY(commander_id,activity,context_key)) WITHOUT ROWID
    """),
}

_PROFILE_UNIQUE_SCHEMAS = {
    "ledger_events": (("commander_id", "event_uid"), """
        CREATE TABLE {name}(
            id INTEGER PRIMARY KEY AUTOINCREMENT, commander_id TEXT NOT NULL,
            event_uid TEXT NOT NULL, event_ts INTEGER NOT NULL, timestamp TEXT,
            event_type TEXT NOT NULL, category TEXT NOT NULL, system TEXT, body TEXT,
            station TEXT, source_file TEXT, source_line INTEGER, payload BLOB NOT NULL,
            payload_size INTEGER NOT NULL, stored_size INTEGER NOT NULL,
            created_at TEXT NOT NULL, UNIQUE(commander_id,event_uid))
    """),
    "timing_observations": ((
        "commander_id", "activity", "context_key", "started_at", "ended_at",
    ), """
        CREATE TABLE {name}(
            id INTEGER PRIMARY KEY AUTOINCREMENT, commander_id TEXT NOT NULL,
            activity TEXT NOT NULL, context_key TEXT NOT NULL DEFAULT '',
            started_at INTEGER NOT NULL, ended_at INTEGER NOT NULL,
            duration_s REAL NOT NULL, source TEXT NOT NULL DEFAULT 'journal',
            created_at TEXT NOT NULL,
            UNIQUE(commander_id,activity,context_key,started_at,ended_at))
    """),
    "specialist_history": (("commander_id", "workflow", "session_key"), """
        CREATE TABLE {name}(
            id INTEGER PRIMARY KEY AUTOINCREMENT, commander_id TEXT NOT NULL,
            workflow TEXT NOT NULL, session_key TEXT NOT NULL, started_ts INTEGER,
            ended_ts INTEGER, summary_json TEXT NOT NULL, created_at TEXT NOT NULL,
            UNIQUE(commander_id,workflow,session_key))
    """),
    "commander_objectives": (("commander_id", "source", "source_ref"), """
        CREATE TABLE {name}(
            id TEXT PRIMARY KEY, commander_id TEXT NOT NULL, source TEXT NOT NULL,
            source_ref TEXT, title TEXT NOT NULL, category TEXT NOT NULL DEFAULT 'other',
            status TEXT NOT NULL DEFAULT 'open', priority INTEGER NOT NULL DEFAULT 50,
            system TEXT, station TEXT, body TEXT, estimated_seconds INTEGER,
            deadline INTEGER, reward INTEGER, risk TEXT, payload TEXT NOT NULL DEFAULT '{}',
            dependencies TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL, UNIQUE(commander_id,source,source_ref))
    """),
}


class CommanderDatabaseError(RuntimeError):
    """The durable commander database could not be validated or migrated."""


def quote_identifier(value):
    """Quote an SQLite identifier (identifiers cannot be bound parameters)."""
    return '"' + str(value).replace('"', '""') + '"'


def _configure(conn, *, wal=True):
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA synchronous = FULL")
    if wal:
        conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")


def _primary_key(conn, table):
    rows = conn.execute(f"PRAGMA table_info({quote_identifier(table)})").fetchall()
    return tuple(row[1] for row in sorted(rows, key=lambda item: item[5]) if row[5])


def _unique_keys(conn, table):
    result = set()
    for row in conn.execute(f"PRAGMA index_list({quote_identifier(table)})"):
        if not row[2]:
            continue
        columns = tuple(
            item[2] for item in conn.execute(
                f"PRAGMA index_info({quote_identifier(row[1])})"
            )
        )
        result.add(columns)
    return result


def _rebuild_profile_key(conn, table, create_sql):
    """Replace an ALTER-upgraded table with its real profile-aware key."""
    temporary = f"__profile_key_v{SCHEMA_VERSION}_{table}"
    conn.execute(f"DROP TABLE IF EXISTS {quote_identifier(temporary)}")
    conn.execute(create_sql.format(name=quote_identifier(temporary)))
    source_info = conn.execute(
        f"PRAGMA table_info({quote_identifier(table)})"
    ).fetchall()
    target_info = conn.execute(
        f"PRAGMA table_info({quote_identifier(temporary)})"
    ).fetchall()
    source_columns = {row[1] for row in source_info}
    insert_columns = []
    select_values = []
    for row in target_info:
        column = row[1]
        if column in source_columns:
            insert_columns.append(quote_identifier(column))
            select_values.append(
                f"COALESCE({quote_identifier(column)}, '')"
                if table == "income_log" and column == "detail"
                else quote_identifier(column)
            )
        elif column == "commander_id":
            insert_columns.append(quote_identifier(column))
            select_values.append("'default'")
        elif row[3] and row[4] is None:
            raise CommanderDatabaseError(
                f"cannot rebuild {table}: required legacy column is missing: {column}"
            )
    conn.execute(
        f"INSERT INTO {quote_identifier(temporary)} ({', '.join(insert_columns)})"
        f" SELECT {', '.join(select_values)} FROM {quote_identifier(table)}"
    )
    conn.execute(f"DROP TABLE {quote_identifier(table)}")
    conn.execute(
        f"ALTER TABLE {quote_identifier(temporary)} RENAME TO {quote_identifier(table)}"
    )


def _ensure_profile_columns(conn):
    """Upgrade an early/pre-release commander.db without losing its rows.

    Fresh v2 databases have compound profile-aware primary keys.  Databases
    created by development builds may have older primary/unique keys. Add the
    discriminator, then transactionally rebuild known tables whose SQLite key
    could not be changed in place. The production market.db migration always
    targets the fresh compound-key schema.
    """
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    if "commander_profiles" in tables:
        profile_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(commander_profiles)")
        }
        if "galaxy_mode" not in profile_columns:
            conn.execute(
                "ALTER TABLE commander_profiles"
                " ADD COLUMN galaxy_mode TEXT NOT NULL DEFAULT 'live'"
            )
    for table in PROFILE_SCOPED_TABLES & tables:
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({quote_identifier(table)})")}
        if "commander_id" not in columns:
            conn.execute(
                f"ALTER TABLE {quote_identifier(table)}"
                " ADD COLUMN commander_id TEXT NOT NULL DEFAULT 'default'"
            )
    # Adding the discriminator above does not change an existing primary key.
    # Rebuild after every table has the column so all retained rows are copied
    # under the quarantined default identity in one startup transaction.
    for table, (expected_key, create_sql) in _PROFILE_KEY_SCHEMAS.items():
        if table in tables and _primary_key(conn, table) != expected_key:
            _rebuild_profile_key(conn, table, create_sql)
    for table, (expected_key, create_sql) in _PROFILE_UNIQUE_SCHEMAS.items():
        if table in tables and expected_key not in _unique_keys(conn, table):
            _rebuild_profile_key(conn, table, create_sql)


def _ensure_default_profile(conn):
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT OR IGNORE INTO commander_profiles"
        "(id, name, galaxy_mode, created_at, last_seen_at, is_active)"
        " VALUES('default', ?, 'live', ?, ?, 1)",
        ("Default commander", now, now),
    )
    if not conn.execute("SELECT 1 FROM commander_profiles WHERE is_active = 1 LIMIT 1").fetchone():
        conn.execute("UPDATE commander_profiles SET is_active = 1 WHERE id = 'default'")
    conn.execute(
        "INSERT OR IGNORE INTO user_meta(key, value) VALUES('active_commander_id', 'default')"
    )


def connect(path):
    """Open and initialise a standalone commander database connection."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    try:
        _configure(conn)
        conn.executescript(USER_SCHEMA)
        # SQLite cannot retrofit compound keys with ALTER TABLE.  Keep the
        # create/copy/drop/rename repair and its schema marker in one explicit
        # transaction so a power loss cannot strand a half-rebuilt user DB.
        conn.execute("BEGIN IMMEDIATE")
        _ensure_profile_columns(conn)
        _ensure_default_profile(conn)
        conn.execute(
            "INSERT OR REPLACE INTO user_meta(key, value) VALUES(?, ?)",
            ("storage_schema_version", str(SCHEMA_VERSION)),
        )
        conn.commit()
        return conn
    except Exception:
        conn.rollback()
        conn.close()
        raise


def ensure_schema(path, sql):
    """Apply idempotent feature DDL to commander.db.

    Feature modules can use this during startup without importing market-cache
    internals.  DDL should use unqualified names because this is a direct
    connection whose main database *is* commander.db.
    """
    conn = connect(path)
    try:
        conn.executescript(sql)
        conn.commit()
    finally:
        conn.close()


def validate(path, required_tables=KNOWN_USER_TABLES):
    """Validate integrity and the durable schema, returning table row counts."""
    path = Path(path)
    if not path.exists():
        raise CommanderDatabaseError(f"commander database does not exist: {path}")
    conn = sqlite3.connect(path, timeout=30)
    try:
        result = conn.execute("PRAGMA quick_check(1)").fetchone()
        if not result or result[0] != "ok":
            raise CommanderDatabaseError(f"commander database integrity check failed: {result}")
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        missing = set(required_tables) - tables
        if missing:
            raise CommanderDatabaseError(
                "commander database is missing tables: " + ", ".join(sorted(missing))
            )
        counts = {}
        for table in sorted(tables):
            counts[table] = conn.execute(
                f"SELECT COUNT(*) FROM {quote_identifier(table)}"
            ).fetchone()[0]
        return counts
    finally:
        conn.close()


def _safe_reason(reason):
    value = re.sub(r"[^a-z0-9_-]+", "-", str(reason).lower()).strip("-")
    return value or "snapshot"


def _prune_backups(backup_dir, retain):
    if retain is None or retain < 1:
        return
    snapshots = sorted(
        Path(backup_dir).glob("commander-*.db"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for stale in snapshots[retain:]:
        stale.unlink(missing_ok=True)


def backup(source_path, backup_dir, reason="manual", retain=5):
    """Create a transactionally consistent, validated commander snapshot."""
    source_path, backup_dir = Path(source_path), Path(backup_dir)
    if not source_path.exists():
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    destination = backup_dir / f"commander-{stamp}-{_safe_reason(reason)}.db"
    source = sqlite3.connect(source_path, timeout=30)
    target = sqlite3.connect(destination, timeout=30)
    try:
        source.backup(target)
        target.commit()
    except Exception:
        target.close()
        source.close()
        destination.unlink(missing_ok=True)
        raise
    else:
        target.close()
        source.close()
    validate(destination)
    _prune_backups(backup_dir, retain)
    return destination


def _copy_database(source_path, destination_path):
    """Copy an existing SQLite database through the online backup API."""
    source_path, destination_path = Path(source_path), Path(destination_path)
    source = sqlite3.connect(source_path, timeout=30)
    destination = sqlite3.connect(destination_path, timeout=30)
    try:
        source.backup(destination)
        destination.commit()
    finally:
        destination.close()
        source.close()


def _table_columns(conn, schema, table):
    return [
        row[1]
        for row in conn.execute(
            f"PRAGMA {quote_identifier(schema)}.table_info({quote_identifier(table)})"
        )
    ]


def _create_unknown_table(conn, table, create_sql):
    """Recreate a future/user-added table in the commander candidate."""
    if not create_sql:
        raise CommanderDatabaseError(f"cannot migrate table without schema: {table}")
    # sqlite_master normally stores an unqualified CREATE statement.  Strip an
    # explicit source schema if one was used so the table lands in candidate
    # main rather than referring to a non-existent legacy schema.
    sql = re.sub(
        r"(?i)^(\s*CREATE\s+(?:VIRTUAL\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?)"
        r"(?:main|legacy)\.",
        r"\1",
        create_sql,
        count=1,
    )
    conn.execute(sql)


def migrate_from_market(
    market_conn,
    market_path,
    user_path,
    *,
    cache_tables,
    cache_meta_keys,
    backup_dir,
):
    """Move every non-cache table from a legacy market.db to commander.db.

    Migration is staged rather than destructive:

    1. Copy the existing commander.db (if any) into a candidate.
    2. Merge every legacy/future user table and user-owned metadata.
    3. Validate and atomically promote the commander candidate.
    4. Only then remove the duplicated legacy tables from market.db.

    A crash before step 3 leaves market.db untouched.  A crash between steps
    3 and 4 is harmless: startup repeats an idempotent merge before cleanup.
    """
    market_path, user_path = Path(market_path), Path(user_path)
    rows = market_conn.execute(
        "SELECT name, sql FROM sqlite_master"
        " WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    legacy = {name: sql for name, sql in rows if name not in set(cache_tables)}
    user_meta = []
    if "meta" in {name for name, _ in rows}:
        marks = ",".join("?" for _ in cache_meta_keys)
        if marks:
            user_meta = market_conn.execute(
                f"SELECT key, value FROM meta WHERE key NOT IN ({marks})",
                tuple(sorted(cache_meta_keys)),
            ).fetchall()
        else:
            user_meta = market_conn.execute("SELECT key, value FROM meta").fetchall()

    if not legacy and not user_meta:
        conn = connect(user_path)
        conn.close()
        return {"migrated_tables": [], "migrated_rows": 0, "backup": None}

    candidate = user_path.with_name(user_path.name + ".migrating")
    for path in (candidate, Path(str(candidate) + "-wal"), Path(str(candidate) + "-shm")):
        path.unlink(missing_ok=True)
    if user_path.exists():
        _copy_database(user_path, candidate)

    destination = sqlite3.connect(candidate, timeout=30)
    migrated_rows = 0
    source_counts = {}
    try:
        destination.execute("PRAGMA busy_timeout = 30000")
        destination.execute("PRAGMA synchronous = FULL")
        destination.execute("PRAGMA journal_mode = DELETE")
        destination.execute("PRAGMA foreign_keys = OFF")
        destination.executescript(USER_SCHEMA)
        _ensure_profile_columns(destination)
        _ensure_default_profile(destination)
        destination.commit()
        destination.execute("ATTACH DATABASE ? AS legacy", (str(market_path),))
        destination.execute("BEGIN IMMEDIATE")

        existing = {
            row[0]
            for row in destination.execute(
                "SELECT name FROM main.sqlite_master WHERE type = 'table'"
            )
        }
        for table, create_sql in legacy.items():
            if table not in existing:
                _create_unknown_table(destination, table, create_sql)
                existing.add(table)
            source_columns = _table_columns(destination, "legacy", table)
            target_columns = set(_table_columns(destination, "main", table))
            columns = [column for column in source_columns if column in target_columns]
            if not columns:
                raise CommanderDatabaseError(f"no compatible columns while migrating {table}")
            quoted = ", ".join(quote_identifier(column) for column in columns)
            source_count = destination.execute(
                f"SELECT COUNT(*) FROM legacy.{quote_identifier(table)}"
            ).fetchone()[0]
            source_counts[table] = source_count
            destination.execute(
                f"INSERT OR REPLACE INTO main.{quote_identifier(table)} ({quoted})"
                f" SELECT {quoted} FROM legacy.{quote_identifier(table)}"
            )
            migrated_rows += source_count

        destination.executemany(
            "INSERT OR REPLACE INTO user_meta(key, value) VALUES(?, ?)", user_meta
        )
        destination.execute(
            "INSERT OR REPLACE INTO user_meta(key, value) VALUES(?, ?)",
            ("storage_schema_version", str(SCHEMA_VERSION)),
        )
        destination.execute(
            "INSERT OR REPLACE INTO user_meta(key, value) VALUES(?, ?)",
            ("migrated_from_market_db", datetime.now(timezone.utc).isoformat()),
        )

        # Preserve explicit indexes/triggers added to future user tables.
        objects = destination.execute(
            "SELECT type, name, sql FROM legacy.sqlite_master"
            " WHERE type IN ('index', 'trigger') AND tbl_name NOT IN ("
            + ",".join("?" for _ in cache_tables)
            + ") AND sql IS NOT NULL",
            tuple(sorted(cache_tables)),
        ).fetchall()
        for kind, name, sql in objects:
            found = destination.execute(
                "SELECT 1 FROM main.sqlite_master WHERE type = ? AND name = ?", (kind, name)
            ).fetchone()
            if not found:
                destination.execute(sql)

        destination.commit()
        destination.execute("DETACH DATABASE legacy")

        check = destination.execute("PRAGMA quick_check(1)").fetchone()
        if not check or check[0] != "ok":
            raise CommanderDatabaseError(f"migration candidate integrity check failed: {check}")
        for table, source_count in source_counts.items():
            copied = destination.execute(
                f"SELECT COUNT(*) FROM {quote_identifier(table)}"
            ).fetchone()[0]
            if copied < source_count:
                raise CommanderDatabaseError(
                    f"migration lost rows in {table}: source={source_count}, candidate={copied}"
                )
        destination.commit()
    except Exception:
        try:
            destination.rollback()
        except Exception:
            pass
        destination.close()
        for path in (candidate, Path(str(candidate) + "-wal"), Path(str(candidate) + "-shm")):
            path.unlink(missing_ok=True)
        raise
    else:
        destination.close()

    validate(candidate)
    snapshot = None
    if migrated_rows or user_meta:
        # This compact, user-only snapshot is the rollback point for the
        # one-time split.  It deliberately excludes the multi-gigabyte cache.
        snapshot = backup(candidate, backup_dir, reason="storage-migration")

    # No commander connection is open during first-connect migration.  Remove
    # any retired WAL sidecars before changing the main file name so SQLite can
    # never mistake an old WAL for part of the promoted candidate.
    for suffix in ("-wal", "-shm"):
        Path(str(user_path) + suffix).unlink(missing_ok=True)
    os.replace(candidate, user_path)
    for suffix in ("-wal", "-shm"):
        Path(str(candidate) + suffix).unlink(missing_ok=True)

    # Destructive cleanup occurs only after the durable destination was fully
    # validated and promoted.  Re-running after an interrupted cleanup is safe.
    try:
        market_conn.execute("BEGIN IMMEDIATE")
        for table in legacy:
            market_conn.execute(f"DROP TABLE IF EXISTS {quote_identifier(table)}")
        if user_meta:
            market_conn.executemany("DELETE FROM meta WHERE key = ?", [(key,) for key, _ in user_meta])
        market_conn.commit()
    except Exception:
        market_conn.rollback()
        raise

    return {
        "migrated_tables": sorted(legacy),
        "migrated_rows": migrated_rows,
        "backup": str(snapshot) if snapshot else None,
    }
