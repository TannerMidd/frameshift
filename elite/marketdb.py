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
CREATE TABLE IF NOT EXISTS trade_log(
    ts INTEGER NOT NULL,
    event TEXT NOT NULL,
    symbol TEXT NOT NULL,
    name TEXT,
    count INTEGER NOT NULL DEFAULT 0,
    price INTEGER NOT NULL DEFAULT 0,
    total INTEGER NOT NULL DEFAULT 0,
    profit INTEGER,
    PRIMARY KEY(ts, event, symbol, total)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS balance_log(
    ts INTEGER PRIMARY KEY,
    balance INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS income_log(
    ts INTEGER NOT NULL,
    category TEXT NOT NULL,
    detail TEXT,
    amount INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(ts, category, detail, amount)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS imported_journals(filename TEXT PRIMARY KEY);
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
    market_id INTEGER PRIMARY KEY,
    added_ts INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS watches(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created TEXT NOT NULL,
    payload TEXT NOT NULL);
"""


def log_trade(ts, event, symbol, name, count, price, total, profit=None):
    if not ts or not symbol:
        return
    conn = connect()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO trade_log(ts, event, symbol, name, count, price, total, profit)"
            " VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            (ts, event, symbol, name, count or 0, price or 0, total or 0, profit),
        )
        conn.commit()
    finally:
        conn.close()


def log_balance(ts, balance):
    if not ts or balance is None:
        return
    conn = connect()
    try:
        conn.execute("INSERT OR REPLACE INTO balance_log(ts, balance) VALUES(?, ?)", (ts, balance))
        conn.commit()
    finally:
        conn.close()


# Non-trade income sources, categorised for the earnings breakdown. Trade
# profit lives in trade_log; everything realised here is credits actually
# received (voucher redemptions, not the accrued Bounty/Bond events), so the
# two never double-count a credit.
INCOME_CATEGORIES = ("mission", "exploration", "exobiology", "bounty", "other")


def log_income(ts, category, amount, detail=None):
    if not ts or not amount or not category:
        return
    conn = connect()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO income_log(ts, category, detail, amount) VALUES(?, ?, ?, ?)",
            (ts, category, detail or "", int(amount)),
        )
        conn.commit()
    finally:
        conn.close()

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
_tracked_cache = None      # set of tracked market_ids (refreshed on change)
_tracked_lock = threading.Lock()
_prune_counter = 0


def tracked_ids():
    global _tracked_cache
    with _tracked_lock:
        if _tracked_cache is None:
            conn = connect()
            try:
                _tracked_cache = {r[0] for r in conn.execute("SELECT market_id FROM tracked_markets")}
            finally:
                conn.close()
        return set(_tracked_cache)


def track_market(market_id):
    """Mark a market as history-worthy (called when the player docks)."""
    global _tracked_cache
    if not market_id:
        return
    conn = connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO tracked_markets(market_id, added_ts) VALUES(?, ?)",
            (market_id, now_epoch()),
        )
        # Cap to the most recent; drop history rows along with the tracking.
        stale = conn.execute(
            "SELECT market_id FROM tracked_markets ORDER BY added_ts DESC LIMIT -1 OFFSET ?",
            (TRACKED_CAP,),
        ).fetchall()
        for (mid,) in stale:
            conn.execute("DELETE FROM tracked_markets WHERE market_id = ?", (mid,))
            conn.execute("DELETE FROM price_history WHERE market_id = ?", (mid,))
        conn.commit()
    finally:
        conn.close()
    with _tracked_lock:
        _tracked_cache = None


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
