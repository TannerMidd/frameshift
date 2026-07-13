"""Local market database (SQLite): seeded from the Spansh galaxy dump, kept
fresh by the EDDN listener, queried by the local route planner.

Keying: stations by in-game MarketID (the Spansh dump's station "id" is the
MarketID), commodities by lowercase symbol (matches EDDN commodity names)."""

import hashlib
import os
import re
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from . import commanderdb
from .errors import ValidationError


def _default_data_dir():
    if getattr(sys, "frozen", False):
        # PyInstaller bundle: keep the database next to the exe, not in the
        # temp extraction dir that vanishes on exit.
        return Path(sys.executable).resolve().parent / "data"
    return Path(__file__).resolve().parent.parent / "data"


DATA_DIR = Path(os.environ.get("ET_DATA_DIR") or _default_data_dir())
DB_PATH = DATA_DIR / "market.db"
USER_DB_PATH = DATA_DIR / "commander.db"
BACKUP_DIR = DATA_DIR / "backups"
USER_DB_ALIAS = commanderdb.ALIAS

CARRIER_TYPES = {"Drake-Class Carrier", "Fleet Carrier"}
CARRIER_NAME_RE = re.compile(r"^[A-Z0-9]{3}-[A-Z0-9]{3}$")

CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS systems(
    id64 INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    x REAL NOT NULL, y REAL NOT NULL, z REAL NOT NULL);
CREATE INDEX IF NOT EXISTS idx_systems_name ON systems(name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_systems_x ON systems(x);
CREATE TABLE IF NOT EXISTS stations(
    market_id INTEGER PRIMARY KEY,
    system_id64 INTEGER NOT NULL,
    name TEXT NOT NULL,
    type TEXT,
    dist_ls REAL,
    large_pad INTEGER NOT NULL DEFAULT 0,
    updated_at INTEGER);
CREATE INDEX IF NOT EXISTS idx_stations_system ON stations(system_id64);
CREATE TABLE IF NOT EXISTS commodities(
    market_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    buy_price INTEGER NOT NULL DEFAULT 0,
    sell_price INTEGER NOT NULL DEFAULT 0,
    supply INTEGER NOT NULL DEFAULT 0,
    demand INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(market_id, symbol)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS commodity_names(
    symbol TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT);
"""

# Ownership is deliberately a whitelist: everything not listed here is
# commander-owned and must survive a cache rebuild, including tables added by
# future releases or extensions.
CACHE_TABLES = frozenset({"meta", "systems", "stations", "commodities", "commodity_names"})
CACHE_META_KEYS = frozenset(
    {
        "seeded_at",
        "seed_source",
        "seed_systems",
        "seed_stations",
        "seed_commodities",
        "seed_include_carriers",
        "eddn_replayed_at",
        "eddn_replayed_markets",
    }
)
USER_TABLES = commanderdb.KNOWN_USER_TABLES
USER_SCHEMA = commanderdb.USER_SCHEMA

# Backward-compatible name for the market schema.  Durable tables are
# intentionally excluded; callers that need feature/user DDL use
# ensure_user_schema().
SCHEMA = CACHE_SCHEMA


def log_trade(ts, event, symbol, name, count, price, total, profit=None, commander_id=None):
    if not ts or not symbol:
        return
    commander_id = resolve_commander_id(commander_id)
    conn = connect()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO trade_log"
            "(commander_id, ts, event, symbol, name, count, price, total, profit)"
            " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (commander_id, ts, event, symbol, name,
             count or 0, price or 0, total or 0, profit),
        )
        conn.commit()
    finally:
        conn.close()


def log_balance(ts, balance, commander_id=None):
    if not ts or balance is None:
        return
    commander_id = resolve_commander_id(commander_id)
    conn = connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO balance_log(commander_id, ts, balance) VALUES(?, ?, ?)",
            (commander_id, ts, balance),
        )
        conn.commit()
    finally:
        conn.close()


# Non-trade income sources, categorised for the earnings breakdown. Trade
# profit lives in trade_log; everything realised here is credits actually
# received (voucher redemptions, not the accrued Bounty/Bond events), so the
# two never double-count a credit.
INCOME_CATEGORIES = ("mission", "exploration", "exobiology", "bounty", "other")


def log_income(ts, category, amount, detail=None, commander_id=None):
    if not ts or not amount or not category:
        return
    commander_id = resolve_commander_id(commander_id)
    conn = connect()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO income_log"
            "(commander_id, ts, category, detail, amount) VALUES(?, ?, ?, ?, ?)",
            (commander_id, ts, category, detail or "", int(amount)),
        )
        conn.commit()
    finally:
        conn.close()

_init_lock = threading.Lock()
_initialized = False
_migration_report = None
# Held for the whole file swap in swap_in(); connect() grabs it briefly so no
# new connection can open (and recover a stale WAL) mid-swap.
_swap_lock = threading.Lock()


def _attach_commander(conn):
    databases = {row[1] for row in conn.execute("PRAGMA database_list")}
    if USER_DB_ALIAS not in databases:
        conn.execute(
            f"ATTACH DATABASE ? AS {commanderdb.quote_identifier(USER_DB_ALIAS)}",
            (str(USER_DB_PATH),),
        )
    # WAL + NORMAL, matching commanderdb._configure (see rationale there).
    conn.execute(f"PRAGMA {commanderdb.quote_identifier(USER_DB_ALIAS)}.synchronous = NORMAL")


def _initialize_storage(conn):
    """Initialise cache storage and transparently split legacy user data."""
    global _migration_report
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(CACHE_SCHEMA)
    conn.commit()
    _migration_report = commanderdb.migrate_from_market(
        conn,
        DB_PATH,
        USER_DB_PATH,
        cache_tables=CACHE_TABLES,
        cache_meta_keys=CACHE_META_KEYS,
        backup_dir=BACKUP_DIR,
    )
    # migrate_from_market may promote a DELETE-mode candidate.  Reopen it once
    # directly to enable normal WAL operation before attaching it everywhere.
    user_conn = commanderdb.connect(USER_DB_PATH)
    user_conn.close()


def connect(path=None):
    """New connection (SQLite connections are not shared across threads here).
    Pass `path` to open a standalone database file (the seeder builds the
    re-seed into a sidecar this way); only the disposable cache schema is
    applied there.  Normal connections attach durable commander.db as the
    ``commander`` schema, preserving compatibility with existing unqualified
    queries for trade_log, watches, and the other user tables."""
    global _initialized
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if path is not None:
        conn = sqlite3.connect(path, timeout=30)
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(CACHE_SCHEMA)
        conn.commit()
        return conn

    # Hold the swap gate until the returned connection has both database files
    # open.  A promotion can therefore never land between opening market.db and
    # attaching commander.db.
    with _swap_lock:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        try:
            conn.execute("PRAGMA busy_timeout = 30000")
            conn.execute("PRAGMA synchronous = NORMAL")
            with _init_lock:
                if not _initialized:
                    _initialize_storage(conn)
                    _initialized = True
            _attach_commander(conn)
            return conn
        except Exception:
            conn.close()
            raise


_commander_session = threading.local()


class _BorrowedConnection:
    """A commander connection on loan from an enclosing commander_session().

    Call sites treat it exactly like the private connection they used to open
    (execute/commit/rollback/close); only close() is a no-op, because the
    owning session outlives each borrower.
    """

    __slots__ = ("_conn",)

    def __init__(self, conn):
        self._conn = conn

    def close(self):
        pass

    def __getattr__(self, name):
        return getattr(self._conn, name)


def connect_user():
    """Connection to durable commander.db for feature-owned data.

    Inside a commander_session() this borrows the session's single
    connection: journal replay drives per-event reducers that would otherwise
    open (and re-validate) thousands of short-lived connections.
    """
    shared = getattr(_commander_session, "conn", None)
    if shared is not None:
        return _BorrowedConnection(shared)
    return commanderdb.connect(USER_DB_PATH)


class commander_session:
    """Reuse one commander.db connection across every connect_user() call
    made by this thread while the context is active. Nesting reuses the
    outermost session. Individual operations keep their own transaction
    boundaries — this shares the connection, not a transaction."""

    def __enter__(self):
        self._owned = getattr(_commander_session, "conn", None) is None
        if self._owned:
            _commander_session.conn = commanderdb.connect(USER_DB_PATH)
        return _commander_session.conn

    def __exit__(self, exc_type, exc, tb):
        if self._owned:
            conn = _commander_session.conn
            _commander_session.conn = None
            try:
                if conn.in_transaction:
                    conn.rollback()
            finally:
                conn.close()
        return False


def ensure_user_schema(sql):
    """Apply idempotent DDL for a feature's commander-owned tables."""
    return commanderdb.ensure_schema(USER_DB_PATH, sql)


def backup_commander_data(reason="manual", retain=5):
    """Create a compact, validated backup that excludes the galaxy cache."""
    # Ensure migration has happened before taking a snapshot.
    conn = connect()
    conn.close()
    return commanderdb.backup(USER_DB_PATH, BACKUP_DIR, reason=reason, retain=retain)


def commander_profile_id(name, galaxy_mode=None):
    """Stable local key; Legacy is isolated from the same name in Live."""
    normalized = " ".join(str(name or "").strip().casefold().split())
    if not normalized:
        return "default"
    if str(galaxy_mode or "live").casefold() == "legacy":
        normalized += "|legacy"
    return "cmdr-" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]


def _move_commander_rows(conn, from_id, to_id, tables):
    """Re-own every row in ``tables`` from one profile id to another.

    Compound-key tables are copied under the new discriminator before their
    old rows are removed. ``INSERT OR REPLACE`` makes an interrupted move
    idempotent. Row-id tables (globally unique integer keys, e.g. watches)
    are updated in place so their ids survive; a collision with an existing
    row of the target owner keeps the target's authoritative copy.

    Returns {table: rows_moved}. Caller owns the transaction.
    """
    moved = {}
    existing = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    for table in sorted(set(tables) & existing):
        quoted_table = commanderdb.quote_identifier(table)
        info = conn.execute(f"PRAGMA table_info({quoted_table})").fetchall()
        columns = [row[1] for row in info]
        if "commander_id" not in columns:
            continue
        count = conn.execute(
            f"SELECT COUNT(*) FROM {quoted_table} WHERE commander_id = ?", (from_id,)
        ).fetchone()[0]
        if not count:
            continue
        primary_key = {row[1] for row in info if row[5]}
        if "commander_id" not in primary_key:
            conn.execute(
                f"UPDATE OR IGNORE {quoted_table} SET commander_id = ?"
                " WHERE commander_id = ?",
                (to_id, from_id),
            )
            conn.execute(
                f"DELETE FROM {quoted_table} WHERE commander_id = ?", (from_id,)
            )
        else:
            quoted_columns = ", ".join(commanderdb.quote_identifier(col) for col in columns)
            selected = ", ".join(
                "?" if col == "commander_id" else commanderdb.quote_identifier(col)
                for col in columns
            )
            conn.execute(
                f"INSERT OR REPLACE INTO {quoted_table} ({quoted_columns})"
                f" SELECT {selected} FROM {quoted_table} WHERE commander_id = ?",
                (to_id, from_id),
            )
            conn.execute(
                f"DELETE FROM {quoted_table} WHERE commander_id = ?", (from_id,)
            )
        moved[table] = count
    return moved


def _adopt_default_profile_rows(conn, commander_id):
    """Move pre-profile user preferences to the first real commander once.

    Early releases necessarily wrote durable rows under ``default`` because
    no commander identity existed in storage yet. User-authored preferences
    belong to the first commander loaded by the journal. Analytics do not:
    multi-account v2.0 installations mixed several pilots in that bucket, so
    those rows remain quarantined and are rebuilt from each journal's owner.
    """
    if commander_id == "default":
        return False
    marker = conn.execute(
        "SELECT value FROM user_meta WHERE key = 'default_profile_adopted_by'"
    ).fetchone()
    # A prior release may already have adopted the core tables before a newer
    # feature registered its own commander-scoped tables.  Re-running for the
    # *same* recorded owner is therefore intentional and idempotent; a later
    # commander must never steal those rows.
    if marker and marker[0] != commander_id:
        return False

    migrated_legacy = conn.execute(
        "SELECT 1 FROM user_meta WHERE key = 'migrated_from_market_db'"
    ).fetchone()
    # A fresh v2.1 installation can legitimately create a manual objective,
    # timing sample, or specialist session before Elite first identifies the
    # pilot; all such rows have one owner. Only a migrated v2.0 database has
    # the multi-account ambiguity that requires derived-history quarantine.
    adoptable = (
        commanderdb.DEFAULT_PROFILE_ADOPTABLE_TABLES
        if migrated_legacy else commanderdb.PROFILE_SCOPED_TABLES
    )
    _move_commander_rows(conn, "default", commander_id, adoptable)
    conn.execute(
        "INSERT OR REPLACE INTO user_meta(key, value)"
        " VALUES('default_profile_adopted_by', ?)",
        (commander_id,),
    )
    return True


def ensure_commander_profile(name, commander_id=None, make_active=True, galaxy_mode="live"):
    """Create/update a profile and optionally make it the active commander."""
    galaxy_mode = "legacy" if str(galaxy_mode).casefold() == "legacy" else "live"
    commander_id = commander_id or commander_profile_id(name, galaxy_mode)
    now = utc_now_iso()
    conn = connect_user()
    try:
        conn.execute("BEGIN IMMEDIATE")
        if make_active:
            conn.execute("UPDATE commander_profiles SET is_active = 0")
        conn.execute(
            "INSERT INTO commander_profiles"
            "(id, name, galaxy_mode, created_at, last_seen_at, is_active)"
            " VALUES(?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET name = excluded.name,"
            " galaxy_mode = excluded.galaxy_mode,"
            " last_seen_at = excluded.last_seen_at,"
            " is_active = CASE WHEN excluded.is_active = 1 THEN 1"
            "                  ELSE commander_profiles.is_active END",
            (commander_id, str(name or "Unknown"), galaxy_mode, now, now, int(bool(make_active))),
        )
        if make_active:
            _adopt_default_profile_rows(conn, commander_id)
        if make_active:
            conn.execute(
                "INSERT OR REPLACE INTO user_meta(key, value)"
                " VALUES('active_commander_id', ?)",
                (commander_id,),
            )
        conn.commit()
        return commander_id
    finally:
        conn.close()


def active_commander_id():
    conn = connect_user()
    try:
        row = conn.execute(
            "SELECT value FROM user_meta WHERE key = 'active_commander_id'"
        ).fetchone()
        return row[0] if row else "default"
    finally:
        conn.close()


def resolve_commander_id(commander_id=None):
    """Return an explicit profile id or the currently active local profile.

    Empty/omitted values must never silently fall back to the shared legacy
    bucket once a real commander is active.
    """
    value = str(commander_id or "").strip()
    return value or active_commander_id()


def profile_overview():
    """Profiles, per-profile data footprints, and the unattributed bucket.

    Powers the Settings repair card: lets a commander see which local profile
    owns what (including pre-v2.1 history stranded under ``default``) before
    assigning, activating, or deleting anything.
    """
    conn = connect_user()
    try:
        active = conn.execute(
            "SELECT value FROM user_meta WHERE key = 'active_commander_id'"
        ).fetchone()
        active_id = active[0] if active else "default"
        adopted = conn.execute(
            "SELECT value FROM user_meta WHERE key = 'default_profile_adopted_by'"
        ).fetchone()
        existing = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        counts = {}
        # File-processing markers are internal bookkeeping, not commander
        # history: they are never assignable and would otherwise show up as
        # permanent phantom "records" in the unassigned bucket.
        countable = (
            set(commanderdb.PROFILE_SCOPED_TABLES)
            - set(commanderdb.PER_FILE_BOOKKEEPING_TABLES)
        )
        for table in sorted(countable & existing):
            quoted = commanderdb.quote_identifier(table)
            info = conn.execute(f"PRAGMA table_info({quoted})").fetchall()
            if "commander_id" not in {row[1] for row in info}:
                continue
            for owner, n in conn.execute(
                f"SELECT commander_id, COUNT(*) FROM {quoted} GROUP BY commander_id"
            ):
                counts.setdefault(owner, {})[table] = n
        profiles = []
        for pid, name, galaxy_mode, created_at, last_seen_at, is_active in conn.execute(
            "SELECT id, name, galaxy_mode, created_at, last_seen_at, is_active"
            " FROM commander_profiles ORDER BY is_active DESC, last_seen_at DESC"
        ):
            owned = counts.get(pid, {})
            profiles.append({
                "id": pid,
                "name": name,
                "galaxy_mode": galaxy_mode,
                "created_at": created_at,
                "last_seen_at": last_seen_at,
                "active": bool(is_active),
                "tables": owned,
                "rows": sum(owned.values()),
            })
        unattributed = counts.get("default", {})
        return {
            "profiles": profiles,
            "active_commander_id": active_id,
            "adopted_by": adopted[0] if adopted else None,
            "unattributed": {
                "tables": unattributed,
                "rows": sum(unattributed.values()),
            },
        }
    finally:
        conn.close()


def _require_profile(conn, commander_id):
    row = conn.execute(
        "SELECT id, name, is_active FROM commander_profiles WHERE id = ?",
        (commander_id,),
    ).fetchone()
    if not row:
        raise ValidationError("That commander profile does not exist.")
    return row


def assign_unattributed_history(commander_id):
    """Explicitly hand the quarantined ``default`` bucket to one commander.

    The automatic path never does this for journal-derived analytics because a
    migrated v2.0 database may mix several pilots. This deliberate action is
    the single-commander answer: idempotent, collision-tolerant, and it seals
    the adoption marker so later commanders cannot re-claim the bucket.
    """
    commander_id = str(commander_id or "").strip()
    if not commander_id or commander_id == "default":
        raise ValidationError("Choose the commander that should own this history.")
    conn = connect_user()
    try:
        conn.execute("BEGIN IMMEDIATE")
        _require_profile(conn, commander_id)
        # Bookkeeping markers stay under the identity that processed the file;
        # see PER_FILE_BOOKKEEPING_TABLES for why moving them loops forever.
        moved = _move_commander_rows(
            conn, "default", commander_id,
            set(commanderdb.PROFILE_SCOPED_TABLES)
            - set(commanderdb.PER_FILE_BOOKKEEPING_TABLES))
        conn.execute(
            "INSERT OR REPLACE INTO user_meta(key, value)"
            " VALUES('default_profile_adopted_by', ?)",
            (commander_id,),
        )
        conn.commit()
        return {"moved": moved, "rows": sum(moved.values())}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def delete_commander_profile(commander_id):
    """Remove a profile and every row it owns (test/stale identities).

    The active profile and the ``default`` bucket are protected; deleting a
    profile recorded as the adoption owner clears that marker so a legitimate
    commander can adopt the bucket later.
    """
    commander_id = str(commander_id or "").strip()
    if not commander_id or commander_id == "default":
        raise ValidationError("The unattributed bucket cannot be deleted.")
    conn = connect_user()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = _require_profile(conn, commander_id)
        if row[2]:
            raise ValidationError(
                "That profile is active. Activate another profile first.")
        existing = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        removed = {}
        for table in sorted(set(commanderdb.PROFILE_SCOPED_TABLES) & existing):
            quoted = commanderdb.quote_identifier(table)
            info = conn.execute(f"PRAGMA table_info({quoted})").fetchall()
            if "commander_id" not in {r[1] for r in info}:
                continue
            n = conn.execute(
                f"SELECT COUNT(*) FROM {quoted} WHERE commander_id = ?",
                (commander_id,),
            ).fetchone()[0]
            if n:
                conn.execute(
                    f"DELETE FROM {quoted} WHERE commander_id = ?", (commander_id,))
                removed[table] = n
        conn.execute("DELETE FROM commander_profiles WHERE id = ?", (commander_id,))
        adopted = conn.execute(
            "SELECT value FROM user_meta WHERE key = 'default_profile_adopted_by'"
        ).fetchone()
        if adopted and adopted[0] == commander_id:
            conn.execute(
                "DELETE FROM user_meta WHERE key = 'default_profile_adopted_by'")
        conn.commit()
        return {"removed": removed, "rows": sum(removed.values())}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def activate_commander_profile(commander_id):
    """Make a profile the stored active commander (the journal can override
    it again at the next Commander event — that is the desired behavior)."""
    commander_id = str(commander_id or "").strip()
    if not commander_id:
        raise ValidationError("Choose a commander profile to activate.")
    conn = connect_user()
    try:
        conn.execute("BEGIN IMMEDIATE")
        _require_profile(conn, commander_id)
        conn.execute("UPDATE commander_profiles SET is_active = 0")
        conn.execute(
            "UPDATE commander_profiles SET is_active = 1, last_seen_at = ?"
            " WHERE id = ?",
            (utc_now_iso(), commander_id),
        )
        conn.execute(
            "INSERT OR REPLACE INTO user_meta(key, value)"
            " VALUES('active_commander_id', ?)",
            (commander_id,),
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def build_path():
    """Sidecar file a full re-seed is built into before swap_in()."""
    return DB_PATH.parent / (DB_PATH.name + ".building")


class CandidateValidationError(RuntimeError):
    """A rebuilt market cache failed safety checks and was not promoted."""


def _cache_counts(conn):
    return {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("systems", "stations", "commodities")
    }


def _merge_live_freshness(candidate_path, live_path=DB_PATH):
    """Replay cache rows newer than the dump into a completed candidate.

    EDDN continues updating the old cache during the lengthy dump import.  The
    listener is paused immediately before this runs, so comparing station
    timestamps captures those updates without an unbounded in-memory buffer.
    """
    candidate_path, live_path = Path(candidate_path), Path(live_path)
    if not live_path.exists() or candidate_path.resolve() == live_path.resolve():
        return 0
    conn = sqlite3.connect(candidate_path, timeout=30)
    replayed = 0
    attached = False
    try:
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("ATTACH DATABASE ? AS live", (str(live_path),))
        attached = True
        live_tables = {
            row[0]
            for row in conn.execute("SELECT name FROM live.sqlite_master WHERE type = 'table'")
        }
        if not {"systems", "stations", "commodities"}.issubset(live_tables):
            return 0
        # BEGIN IMMEDIATE reserves both attached databases, yielding a stable
        # source snapshot and preventing a final EDDN write from racing copy.
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "CREATE TEMP TABLE fresher_markets(market_id INTEGER PRIMARY KEY) WITHOUT ROWID"
        )
        conn.execute(
            "INSERT INTO fresher_markets(market_id)"
            " SELECT old.market_id FROM live.stations old"
            " LEFT JOIN main.stations new ON new.market_id = old.market_id"
            " WHERE new.market_id IS NULL"
            "    OR COALESCE(old.updated_at, 0) > COALESCE(new.updated_at, 0)"
        )
        replayed = conn.execute("SELECT COUNT(*) FROM fresher_markets").fetchone()[0]
        if replayed:
            conn.execute(
                "INSERT OR IGNORE INTO main.systems(id64, name, x, y, z)"
                " SELECT sy.id64, sy.name, sy.x, sy.y, sy.z FROM live.systems sy"
                " WHERE sy.id64 IN ("
                "   SELECT st.system_id64 FROM live.stations st"
                "   JOIN fresher_markets f ON f.market_id = st.market_id)"
            )
            conn.execute(
                "INSERT OR REPLACE INTO main.stations"
                "(market_id, system_id64, name, type, dist_ls, large_pad, updated_at)"
                " SELECT st.market_id, st.system_id64, st.name, st.type, st.dist_ls,"
                "        st.large_pad, st.updated_at"
                " FROM live.stations st JOIN fresher_markets f ON f.market_id = st.market_id"
            )
            conn.execute(
                "DELETE FROM main.commodities"
                " WHERE market_id IN (SELECT market_id FROM fresher_markets)"
            )
            conn.execute(
                "INSERT OR REPLACE INTO main.commodities"
                "(market_id, symbol, buy_price, sell_price, supply, demand)"
                " SELECT c.market_id, c.symbol, c.buy_price, c.sell_price, c.supply, c.demand"
                " FROM live.commodities c"
                " JOIN fresher_markets f ON f.market_id = c.market_id"
            )
            if "commodity_names" in live_tables:
                conn.execute(
                    "INSERT OR IGNORE INTO main.commodity_names(symbol, name, category)"
                    " SELECT symbol, name, category FROM live.commodity_names"
                )
        conn.execute(
            "INSERT OR REPLACE INTO main.meta(key, value) VALUES('eddn_replayed_at', ?)",
            (utc_now_iso(),),
        )
        conn.execute(
            "INSERT OR REPLACE INTO main.meta(key, value)"
            " VALUES('eddn_replayed_markets', ?)",
            (str(replayed),),
        )
        conn.commit()
        conn.execute("DETACH DATABASE live")
        attached = False
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return replayed
    except Exception:
        conn.rollback()
        raise
    finally:
        if attached:
            try:
                conn.execute("DETACH DATABASE live")
            except Exception:
                pass
        conn.close()


def validate_candidate(candidate_path, baseline_path=DB_PATH, minimum_counts=None,
                       baseline_ratio=0.5, check_integrity=True):
    """Run structural, integrity, metadata, and count checks before promotion."""
    candidate_path = Path(candidate_path)
    if not candidate_path.exists():
        raise CandidateValidationError(f"market candidate does not exist: {candidate_path}")
    conn = sqlite3.connect(candidate_path, timeout=30)
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        missing = CACHE_TABLES - tables
        if missing:
            raise CandidateValidationError(
                "market candidate is missing tables: " + ", ".join(sorted(missing))
            )
        unexpected = tables - CACHE_TABLES
        if unexpected:
            raise CandidateValidationError(
                "market candidate contains commander-owned tables: "
                + ", ".join(sorted(unexpected))
            )
        if check_integrity:
            check = conn.execute("PRAGMA quick_check(1)").fetchone()
            if not check or check[0] != "ok":
                raise CandidateValidationError(f"market candidate integrity check failed: {check}")
        counts = _cache_counts(conn)
        for table, count in counts.items():
            if count <= 0:
                raise CandidateValidationError(f"market candidate has no {table} rows")
        metadata = dict(
            conn.execute(
                "SELECT key, value FROM meta WHERE key IN ('seeded_at', 'seed_source')"
            )
        )
        if not metadata.get("seeded_at") or not metadata.get("seed_source"):
            raise CandidateValidationError("market candidate lacks seed provenance metadata")
        for table, expected in (minimum_counts or {}).items():
            if table in counts and counts[table] < int(expected):
                raise CandidateValidationError(
                    f"market candidate {table} count {counts[table]:,} is below"
                    f" import minimum {int(expected):,}"
                )
    except sqlite3.DatabaseError as exc:
        raise CandidateValidationError(f"market candidate is unreadable: {exc}") from exc
    finally:
        conn.close()

    # A syntactically valid truncated download must not replace a healthy full
    # cache.  Carrier-policy changes can alter counts, hence a conservative 50%
    # threshold rather than equality.
    baseline_path = Path(baseline_path) if baseline_path else None
    baseline_counts = None
    if baseline_path and baseline_path.exists() and baseline_path.resolve() != candidate_path.resolve():
        baseline = sqlite3.connect(baseline_path, timeout=30)
        try:
            baseline_tables = {
                row[0]
                for row in baseline.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            if {"systems", "stations", "commodities"}.issubset(baseline_tables):
                baseline_counts = _cache_counts(baseline)
        except sqlite3.DatabaseError:
            baseline_counts = None  # a valid candidate is allowed to repair a corrupt old cache
        finally:
            baseline.close()
    if baseline_counts:
        for table, old_count in baseline_counts.items():
            floor = int(old_count * baseline_ratio)
            if old_count and counts[table] < max(1, floor):
                raise CandidateValidationError(
                    f"market candidate {table} count collapsed from {old_count:,}"
                    f" to {counts[table]:,}"
                )
    return {"counts": counts, "baseline_counts": baseline_counts}


def swap_in(new_path, timeout_s=60, minimum_counts=None):
    """Atomically promote a freshly built database file to be THE database.

    The old market.db stays fully usable until the single os.replace, so a
    crashed or cancelled rebuild can no longer leave the app with a gutted
    database. Commander data is stored separately and is never swapped.
    Callers must get the EDDN listener's long-lived connection closed first;
    its final updates are replayed by timestamp before validation/promotion."""
    new_path = Path(new_path)
    # A rebuild can be started very early in process startup.  Force the
    # one-time v2 legacy split before anything can replace market.db.
    live = connect()
    live.close()
    # Reject an empty/truncated import *before* replay.  Otherwise every row in
    # the old cache would look newer/absent and could disguise a bad download.
    validate_candidate(
        new_path,
        minimum_counts=minimum_counts,
        check_integrity=False,
    )
    replayed = _merge_live_freshness(new_path)
    report = validate_candidate(
        new_path,
        baseline_path=None,
        minimum_counts=minimum_counts,
        check_integrity=True,
    )
    # All committed candidate content must be in the main file before it is
    # renamed; its build-name WAL cannot follow it to market.db.
    checkpoint = sqlite3.connect(new_path, timeout=30)
    try:
        checkpoint.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        checkpoint.close()
    for suffix in ("-wal", "-shm"):
        Path(str(new_path) + suffix).unlink(missing_ok=True)

    deadline = time.time() + timeout_s
    last_exc = None
    while time.time() < deadline:
        with _swap_lock:
            try:
                os.replace(new_path, DB_PATH)
                for suffix in ("-wal", "-shm"):
                    try:
                        os.remove(str(DB_PATH) + suffix)
                    except OSError:
                        pass
                invalidate_status_cache()
                report["eddn_replayed_markets"] = replayed
                return report
            except OSError as exc:
                last_exc = exc
        time.sleep(1)
    raise RuntimeError(f"could not swap in the new database: {last_exc}")


def is_carrier(station_type, station_name):
    if station_type in CARRIER_TYPES:
        return True
    return station_type is None and bool(CARRIER_NAME_RE.match(station_name or ""))


# Surface / on-foot stations you must land at (some pilots avoid these).
SURFACE_TYPES = {
    "Planetary Outpost", "Planetary Port", "Settlement", "Odyssey Settlement",
    "On Foot Settlement", "Planetary Construction Depot",
}


def is_surface(station_type):
    return station_type in SURFACE_TYPES


def parse_update_time(value):
    """Spansh '2026-01-21 03:55:54+00' or EDDN '2026-07-05T20:50:21Z' -> epoch."""
    if not value:
        return None
    text = str(value).strip().replace(" ", "T").replace("Z", "+00:00")
    if text.endswith("+00"):
        text += ":00"
    try:
        return int(datetime.fromisoformat(text).timestamp())
    except ValueError:
        return None


def get_meta(conn, key, default=None):
    # Cache provenance remains in market.db.  Commander/import bookkeeping was
    # historically mixed into that table and now lives in user_meta.
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    if row:
        return row[0]
    databases = {item[1] for item in conn.execute("PRAGMA database_list")}
    if USER_DB_ALIAS in databases:
        row = conn.execute(
            f"SELECT value FROM {commanderdb.quote_identifier(USER_DB_ALIAS)}.user_meta"
            " WHERE key = ?",
            (key,),
        ).fetchone()
    else:
        row = None  # standalone build connection has no commander attachment
    return row[0] if row else default


def set_meta(conn, key, value):
    if key in CACHE_META_KEYS:
        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", (key, str(value)))
        return
    databases = {item[1] for item in conn.execute("PRAGMA database_list")}
    if USER_DB_ALIAS in databases:
        conn.execute(
            f"INSERT OR REPLACE INTO {commanderdb.quote_identifier(USER_DB_ALIAS)}.user_meta"
            "(key, value) VALUES(?, ?)",
            (key, str(value)),
        )
    else:
        # A standalone cache-build connection intentionally has no user DB.
        # Retain compatibility for callers constructing test/migration files;
        # normal application connections always take the durable path above.
        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", (key, str(value)))


def keep_commodity(buy, sell, supply, demand):
    """Only rows usable for trading: buyable somewhere or sellable with demand."""
    return (supply > 0 and buy > 0) or (demand > 0 and sell > 0)


def replace_market(conn, market_id, rows):
    """rows: iterable of (symbol, buy, sell, supply, demand)."""
    conn.execute("DELETE FROM commodities WHERE market_id = ?", (market_id,))
    conn.executemany(
        "INSERT OR REPLACE INTO commodities(market_id, symbol, buy_price, sell_price, supply, demand)"
        " VALUES(?, ?, ?, ?, ?, ?)",
        [(market_id, s, b, sl, sp, d) for (s, b, sl, sp, d) in rows],
    )


def find_system(conn, name):
    return conn.execute(
        "SELECT id64, name, x, y, z FROM systems WHERE name = ? COLLATE NOCASE", (name,)
    ).fetchone()


def stations_near(conn, x, y, z, radius, min_updated=0, require_large_pad=False, max_dist_ls=None,
                  exclude_carriers=None, exclude_surface=None):
    """Stations with markets within `radius` ly of (x,y,z), with exact-sphere
    filtering done in Python after a bounding-box query. Carrier/surface
    exclusion defaults to the user's saved settings unless passed explicitly."""
    if exclude_carriers is None or exclude_surface is None:
        from . import settings  # lazy: avoids an import cycle with settings.py
        if exclude_carriers is None:
            exclude_carriers = settings.get("exclude_carriers", True)
        if exclude_surface is None:
            exclude_surface = settings.get("exclude_surface", False)
    rows = conn.execute(
        """SELECT st.market_id, st.name, st.type, st.dist_ls, st.large_pad, st.updated_at,
                  sy.id64, sy.name, sy.x, sy.y, sy.z
           FROM stations st JOIN systems sy ON sy.id64 = st.system_id64
           WHERE sy.x BETWEEN ? AND ? AND sy.y BETWEEN ? AND ? AND sy.z BETWEEN ?  AND ?
             AND st.updated_at >= ?""",
        (x - radius, x + radius, y - radius, y + radius, z - radius, z + radius, min_updated),
    ).fetchall()
    r2 = radius * radius
    out = []
    for m in rows:
        dx, dy, dz = m[8] - x, m[9] - y, m[10] - z
        if dx * dx + dy * dy + dz * dz > r2:
            continue
        if require_large_pad and not m[4]:
            continue
        if max_dist_ls is not None and m[3] is not None and m[3] > max_dist_ls:
            continue
        if exclude_carriers and is_carrier(m[2], m[1]):
            continue
        if exclude_surface and is_surface(m[2]):
            continue
        out.append(
            {
                "market_id": m[0], "station": m[1], "type": m[2], "dist_ls": m[3],
                "large_pad": bool(m[4]), "updated_at": m[5],
                "system_id64": m[6], "system": m[7], "x": m[8], "y": m[9], "z": m[10],
            }
        )
    return out


# ---------- price history (tracked markets only) ----------
# Recording every EDDN update galaxy-wide would grow by millions of rows a
# day, so history is kept only for markets the player cares about: stations
# they dock at and stations in watched routes.

HISTORY_KEEP_DAYS = 45
TRACKED_CAP = 60           # most-recently docked markets kept in history
_tracked_cache = {}        # commander_id -> set of tracked market_ids
_tracked_lock = threading.Lock()
_prune_counter = 0


def tracked_ids(commander_id=None):
    """Market ids tracked by one commander (the active commander by default)."""
    global _tracked_cache
    commander_id = resolve_commander_id(commander_id)
    with _tracked_lock:
        # Compatibility with development/test code that invalidated the old
        # singleton cache by assigning None.
        if not isinstance(_tracked_cache, dict):
            _tracked_cache = {}
        if commander_id not in _tracked_cache:
            conn = connect()
            try:
                _tracked_cache[commander_id] = {
                    row[0]
                    for row in conn.execute(
                        "SELECT market_id FROM tracked_markets WHERE commander_id = ?",
                        (commander_id,),
                    )
                }
            finally:
                conn.close()
        return set(_tracked_cache[commander_id])


def track_market(market_id, commander_id=None):
    """Mark a market as history-worthy (called when the player docks)."""
    global _tracked_cache
    if not market_id:
        return
    commander_id = resolve_commander_id(commander_id)
    conn = connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO tracked_markets(commander_id, market_id, added_ts)"
            " VALUES(?, ?, ?)",
            (commander_id, market_id, now_epoch()),
        )
        # Cap to the most recent; drop history rows along with the tracking.
        stale = conn.execute(
            "SELECT market_id FROM tracked_markets WHERE commander_id = ?"
            " ORDER BY added_ts DESC LIMIT -1 OFFSET ?",
            (commander_id, TRACKED_CAP),
        ).fetchall()
        for (mid,) in stale:
            conn.execute(
                "DELETE FROM tracked_markets WHERE commander_id = ? AND market_id = ?",
                (commander_id, mid),
            )
            still_tracked = conn.execute(
                "SELECT 1 FROM tracked_markets WHERE market_id = ? LIMIT 1", (mid,)
            ).fetchone()
            if not still_tracked:
                conn.execute("DELETE FROM price_history WHERE market_id = ?", (mid,))
        conn.commit()
    finally:
        conn.close()
    with _tracked_lock:
        if isinstance(_tracked_cache, dict):
            _tracked_cache.pop(commander_id, None)
        else:
            _tracked_cache = {}


def record_price_history(conn, market_id, rows, ts=None):
    """Append one observation per commodity. rows: (symbol, buy, sell, supply, demand)."""
    global _prune_counter
    ts = ts or now_epoch()
    conn.executemany(
        "INSERT OR IGNORE INTO price_history(market_id, symbol, ts, buy_price, sell_price, supply, demand)"
        " VALUES(?, ?, ?, ?, ?, ?, ?)",
        [(market_id, s, ts, b, sl, sp, d) for (s, b, sl, sp, d) in rows],
    )
    _prune_counter += 1
    if _prune_counter % 200 == 0:
        conn.execute(
            "DELETE FROM price_history WHERE ts < ?",
            (now_epoch() - HISTORY_KEEP_DAYS * 86400,),
        )


def price_history(market_id, days=HISTORY_KEEP_DAYS):
    """{symbol: [[ts, sell, buy, demand, supply], ...]} oldest first."""
    if not market_id:
        return {}
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT symbol, ts, sell_price, buy_price, demand, supply FROM price_history"
            " WHERE market_id = ? AND ts >= ? ORDER BY ts",
            (market_id, now_epoch() - days * 86400),
        ).fetchall()
    finally:
        conn.close()
    out = {}
    for sym, ts, sell, buy, demand, supply in rows:
        out.setdefault(sym, []).append([ts, sell, buy, demand, supply])
    return out


def system_station_markets(conn, system_name):
    """{market_id: updated_at} for the stations of one system in the local DB."""
    system = find_system(conn, system_name)
    if not system:
        return {}
    rows = conn.execute(
        "SELECT market_id, updated_at FROM stations WHERE system_id64 = ?", (system[0],)
    ).fetchall()
    return {mid: upd for mid, upd in rows}


def station_market(market_id):
    """Full commodity table for one station from the local DB (seed + EDDN),
    with display names and categories."""
    if not market_id:
        return None
    conn = connect()
    try:
        st = conn.execute(
            "SELECT name, updated_at FROM stations WHERE market_id = ?", (market_id,)
        ).fetchone()
        rows = conn.execute(
            """SELECT c.symbol, COALESCE(n.name, c.symbol), COALESCE(n.category, ''),
                      c.buy_price, c.sell_price, c.supply, c.demand
               FROM commodities c LEFT JOIN commodity_names n ON n.symbol = c.symbol
               WHERE c.market_id = ?
               ORDER BY COALESCE(n.category, ''), COALESCE(n.name, c.symbol)""",
            (market_id,),
        ).fetchall()
    finally:
        conn.close()
    if not st:
        return None
    return {
        "market_id": market_id,
        "station": st[0],
        "updated_at": st[1],
        "items": [
            {"symbol": sym, "name": name, "category": cat,
             "buy": buy, "sell": sell, "stock": supply, "demand": demand}
            for sym, name, cat, buy, sell, supply, demand in rows
        ],
    }


def station_prices(market_id):
    """Last-known sell/buy per commodity symbol for one station, from the local
    DB (seed + EDDN). Used to show price trend vs the live station market."""
    if not market_id:
        return {}
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT symbol, sell_price, buy_price FROM commodities WHERE market_id = ?",
            (market_id,),
        ).fetchall()
    finally:
        conn.close()
    return {sym: (sell, buy) for sym, sell, buy in rows}


def commodity_display_names(conn, symbols):
    if not symbols:
        return {}
    marks = ",".join("?" for _ in symbols)
    rows = conn.execute(
        f"SELECT symbol, name FROM commodity_names WHERE symbol IN ({marks})", list(symbols)
    ).fetchall()
    return dict(rows)


def is_ready(conn):
    """Cheap 'is the local DB usable?' check — O(1), unlike status()'s counts.
    Route code must use this, never status()['ready']: a COUNT(*) over the
    36M-row commodities table costs seconds of CPU."""
    return conn.execute("SELECT EXISTS(SELECT 1 FROM stations)").fetchone()[0] == 1


_status_cache = {"ts": 0.0, "counts": None}


def invalidate_status_cache():
    _status_cache["ts"] = 0.0


def status(conn, max_age=300):
    """DB stats for the Database page. The row counts are informational, and
    counting the commodities table is a multi-second full scan — so they're
    cached (default 5 min; pass a smaller max_age while seeding). Profiled
    2026-07-10: uncached, two devices polling every 5s kept ~50% of a core
    busy doing nothing but this."""
    now = time.time()
    if _status_cache["counts"] is None or now - _status_cache["ts"] > max_age:
        counts = {}
        for table in ("systems", "stations", "commodities"):
            counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        _status_cache.update(ts=now, counts=counts)
    counts = _status_cache["counts"]
    return {
        "db_path": str(DB_PATH),
        "db_size_mb": round(DB_PATH.stat().st_size / 1e6, 1) if DB_PATH.exists() else 0,
        "systems": counts["systems"],
        "stations": counts["stations"],
        "commodity_rows": counts["commodities"],
        "seeded_at": get_meta(conn, "seeded_at"),
        "ready": counts["stations"] > 0 or is_ready(conn),
    }


def now_epoch():
    return int(time.time())


def utc_now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
