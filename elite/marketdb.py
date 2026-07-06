"""Local market database (SQLite): seeded from the Spansh galaxy dump, kept
fresh by the EDDN listener, queried by the local route planner.

Keying: stations by in-game MarketID (the Spansh dump's station "id" is the
MarketID), commodities by lowercase symbol (matches EDDN commodity names)."""

import os
import re
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path


def _default_data_dir():
    if getattr(sys, "frozen", False):
        # PyInstaller bundle: keep the database next to the exe, not in the
        # temp extraction dir that vanishes on exit.
        return Path(sys.executable).resolve().parent / "data"
    return Path(__file__).resolve().parent.parent / "data"


DATA_DIR = Path(os.environ.get("ET_DATA_DIR") or _default_data_dir())
DB_PATH = DATA_DIR / "market.db"

CARRIER_TYPES = {"Drake-Class Carrier", "Fleet Carrier"}
CARRIER_NAME_RE = re.compile(r"^[A-Z0-9]{3}-[A-Z0-9]{3}$")

SCHEMA = """
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

_init_lock = threading.Lock()
_initialized = False


def connect():
    """New connection (SQLite connections are not shared across threads here)."""
    global _initialized
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA synchronous = NORMAL")
    with _init_lock:
        if not _initialized:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(SCHEMA)
            conn.commit()
            _initialized = True
    return conn


def is_carrier(station_type, station_name):
    if station_type in CARRIER_TYPES:
        return True
    return station_type is None and bool(CARRIER_NAME_RE.match(station_name or ""))


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
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def set_meta(conn, key, value):
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


def stations_near(conn, x, y, z, radius, min_updated=0, require_large_pad=False, max_dist_ls=None):
    """Stations with markets within `radius` ly of (x,y,z), with exact-sphere
    filtering done in Python after a bounding-box query."""
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
        out.append(
            {
                "market_id": m[0], "station": m[1], "type": m[2], "dist_ls": m[3],
                "large_pad": bool(m[4]), "updated_at": m[5],
                "system_id64": m[6], "system": m[7], "x": m[8], "y": m[9], "z": m[10],
            }
        )
    return out


def commodity_display_names(conn, symbols):
    if not symbols:
        return {}
    marks = ",".join("?" for _ in symbols)
    rows = conn.execute(
        f"SELECT symbol, name FROM commodity_names WHERE symbol IN ({marks})", list(symbols)
    ).fetchall()
    return dict(rows)


def status(conn):
    counts = {}
    for table in ("systems", "stations", "commodities"):
        counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    return {
        "db_path": str(DB_PATH),
        "db_size_mb": round(DB_PATH.stat().st_size / 1e6, 1) if DB_PATH.exists() else 0,
        "systems": counts["systems"],
        "stations": counts["stations"],
        "commodity_rows": counts["commodities"],
        "seeded_at": get_meta(conn, "seeded_at"),
        "ready": counts["stations"] > 0,
    }


def now_epoch():
    return int(time.time())


def utc_now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
