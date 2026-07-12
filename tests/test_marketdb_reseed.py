"""Commander DB split + lossless/validated atomic market re-seeding."""

import os
import sqlite3
import sys
import tempfile
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_tmp = tempfile.TemporaryDirectory()
os.environ["ET_DATA_DIR"] = _tmp.name

from elite import commanderdb, marketdb  # noqa: E402

LEGACY_USER_SCHEMA = """
CREATE TABLE trade_log(
 ts INTEGER NOT NULL, event TEXT NOT NULL, symbol TEXT NOT NULL, name TEXT,
 count INTEGER NOT NULL DEFAULT 0, price INTEGER NOT NULL DEFAULT 0,
 total INTEGER NOT NULL DEFAULT 0, profit INTEGER,
 PRIMARY KEY(ts,event,symbol,total)) WITHOUT ROWID;
CREATE TABLE balance_log(ts INTEGER PRIMARY KEY, balance INTEGER NOT NULL);
CREATE TABLE income_log(
 ts INTEGER NOT NULL, category TEXT NOT NULL, detail TEXT,
 amount INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(ts,category,detail,amount)) WITHOUT ROWID;
CREATE TABLE imported_journals(filename TEXT PRIMARY KEY);
CREATE TABLE price_history(
 market_id INTEGER NOT NULL, symbol TEXT NOT NULL, ts INTEGER NOT NULL,
 buy_price INTEGER NOT NULL DEFAULT 0, sell_price INTEGER NOT NULL DEFAULT 0,
 supply INTEGER NOT NULL DEFAULT 0, demand INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(market_id,symbol,ts)) WITHOUT ROWID;
CREATE TABLE tracked_markets(market_id INTEGER PRIMARY KEY, added_ts INTEGER NOT NULL);
CREATE TABLE watches(
 id INTEGER PRIMARY KEY AUTOINCREMENT, created TEXT NOT NULL, payload TEXT NOT NULL);
"""


def cache_row(conn, system_id, system, market_id, station, updated, sell):
    conn.execute(
        "INSERT OR REPLACE INTO systems(id64, name, x, y, z) VALUES(?, ?, 0, 0, 0)",
        (system_id, system),
    )
    conn.execute(
        "INSERT OR REPLACE INTO stations"
        "(market_id, system_id64, name, type, dist_ls, large_pad, updated_at)"
        " VALUES(?, ?, ?, 'Coriolis Starport', 100, 1, ?)",
        (market_id, system_id, station, updated),
    )
    conn.execute(
        "INSERT OR REPLACE INTO commodities"
        "(market_id, symbol, buy_price, sell_price, supply, demand)"
        " VALUES(?, 'gold', 10, ?, 100, 100)",
        (market_id, sell),
    )
    conn.execute(
        "INSERT OR IGNORE INTO commodity_names(symbol, name, category)"
        " VALUES('gold', 'Gold', 'Metals')"
    )


# ---------- transparent migration from the v2.0 all-in-one market.db ----------

legacy = sqlite3.connect(marketdb.DB_PATH)
legacy.executescript(marketdb.CACHE_SCHEMA)
legacy.executescript(LEGACY_USER_SCHEMA)
cache_row(legacy, 1, "Alpha", 101, "Alpha Port", 200, 999)
legacy.execute("INSERT INTO meta VALUES('seeded_at', 'old-seed')")
legacy.execute("INSERT INTO meta VALUES('seed_source', 'old-source')")
legacy.execute("INSERT INTO meta VALUES('history_version', '7')")
legacy.execute(
    "INSERT INTO trade_log(ts, event, symbol, name, count, price, total, profit)"
    " VALUES(1, 'sell', 'gold', 'Gold', 2, 100, 200, 50)"
)
legacy.execute("INSERT INTO balance_log(ts, balance) VALUES(1, 123456)")
legacy.execute("INSERT INTO income_log(ts, category, detail, amount) VALUES(1, 'mission', '', 10)")
legacy.execute("INSERT INTO imported_journals(filename) VALUES('Journal.01.log')")
legacy.execute(
    "INSERT INTO price_history VALUES(101, 'gold', 1, 10, 100, 20, 30)"
)
legacy.execute("INSERT INTO tracked_markets(market_id, added_ts) VALUES(101, 1)")
legacy.execute("INSERT INTO watches(created, payload) VALUES('now', '{\"route\":1}')")
# Anything not on the explicit cache whitelist is durable, including a table
# introduced by a future release/extension and its index.
legacy.execute("CREATE TABLE commander_notes(note_id INTEGER PRIMARY KEY, note TEXT NOT NULL)")
legacy.execute("CREATE INDEX idx_commander_notes_text ON commander_notes(note)")
legacy.execute("INSERT INTO commander_notes VALUES(42, 'Never sell the Python')")
legacy.commit()
legacy.close()

conn = marketdb.connect()
databases = {row[1] for row in conn.execute("PRAGMA database_list")}
assert "commander" in databases, databases
main_tables = {
    row[0]
    for row in conn.execute(
        "SELECT name FROM main.sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    )
}
assert main_tables == set(marketdb.CACHE_TABLES), main_tables
assert conn.execute("SELECT profit FROM trade_log").fetchone()[0] == 50
assert conn.execute("SELECT balance FROM balance_log").fetchone()[0] == 123456
assert conn.execute("SELECT note FROM commander_notes").fetchone()[0].startswith("Never")
assert marketdb.get_meta(conn, "history_version") == "7"
assert conn.execute("SELECT value FROM main.meta WHERE key = 'history_version'").fetchone() is None
assert conn.execute("SELECT commander_id FROM trade_log").fetchone()[0] == "default"
assert conn.execute("SELECT commander_id FROM watches").fetchone()[0] == "default"
conn.close()

user = marketdb.connect_user()
assert user.execute(
    "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = 'idx_commander_notes_text'"
).fetchone()
assert user.execute("PRAGMA quick_check").fetchone()[0] == "ok"
user.close()
assert list(marketdb.BACKUP_DIR.glob("commander-*-storage-migration.db"))

print("storage migration OK: all user tables/meta moved, future table/index preserved, backup made")


# ---------- profile-aware keys stay backward compatible ----------

profile = marketdb.ensure_commander_profile("Test Commander")
assert profile.startswith("cmdr-") and marketdb.active_commander_id() == profile
marketdb.log_trade(1, "sell", "gold", "Gold", 3, 100, 300, 75, commander_id=profile)
profiles = marketdb.connect_user()
assert profiles.execute("SELECT COUNT(DISTINCT commander_id) FROM trade_log").fetchone()[0] == 2
assert profiles.execute("SELECT COUNT(*) FROM trade_log WHERE commander_id = 'default'").fetchone()[0] == 1
assert profiles.execute(
    "SELECT COUNT(*) FROM trade_log WHERE commander_id = ?", (profile,)
).fetchone()[0] == 1
assert profiles.execute(
    "SELECT is_active FROM commander_profiles WHERE id = ?", (profile,)
).fetchone()[0] == 1
profiles.close()

print("profiles OK: ambiguous v2 history quarantined + compound-key commander history")


# ---------- new dump promotion replays EDDN freshness, preserves user DB ----------

build = marketdb.build_path()
candidate = marketdb.connect(path=build)
# Dump's Alpha is older than the live/EDDN row and must be replaced at promote.
cache_row(candidate, 1, "Alpha", 101, "Alpha Port", 100, 111)
cache_row(candidate, 2, "Beta", 202, "Beta Hub", 150, 222)
marketdb.set_meta(candidate, "seeded_at", "new-seed")
marketdb.set_meta(candidate, "seed_source", "test-dump")
candidate.commit()
candidate.execute("PRAGMA wal_checkpoint(TRUNCATE)")
candidate.close()

report = marketdb.swap_in(
    build,
    timeout_s=5,
    minimum_counts={"systems": 2, "stations": 2, "commodities": 2},
)
assert report["eddn_replayed_markets"] == 1, report
after = marketdb.connect()
assert after.execute(
    "SELECT sell_price FROM commodities WHERE market_id = 101 AND symbol = 'gold'"
).fetchone()[0] == 999
assert after.execute(
    "SELECT sell_price FROM commodities WHERE market_id = 202 AND symbol = 'gold'"
).fetchone()[0] == 222
assert marketdb.get_meta(after, "seeded_at") == "new-seed"
assert marketdb.get_meta(after, "history_version") == "7"
assert after.execute("SELECT COUNT(*) FROM watches").fetchone()[0] == 1
assert after.execute("SELECT note FROM commander_notes").fetchone()[0].startswith("Never")
after.close()

print("atomic reseed OK: EDDN replayed, new dump promoted, commander data untouched")


# ---------- invalid/truncated candidates never replace a healthy cache ----------

bad = marketdb.build_path()
bad.unlink(missing_ok=True)
bad_conn = marketdb.connect(path=bad)
marketdb.set_meta(bad_conn, "seeded_at", "bad")
marketdb.set_meta(bad_conn, "seed_source", "truncated")
bad_conn.commit()
bad_conn.close()

try:
    marketdb.swap_in(bad, timeout_s=1)
    raise AssertionError("empty candidate was promoted")
except marketdb.CandidateValidationError:
    pass

healthy = marketdb.connect()
assert healthy.execute("SELECT COUNT(*) FROM systems").fetchone()[0] == 2
assert healthy.execute("SELECT COUNT(*) FROM trade_log").fetchone()[0] == 2
healthy.close()

# A manual user-only snapshot is independently valid and excludes cache tables.
snapshot = marketdb.backup_commander_data("test", retain=5)
snapshot_tables = commanderdb.validate(snapshot)
assert snapshot_tables["trade_log"] == 2
snap_conn = sqlite3.connect(snapshot)
assert not snap_conn.execute(
    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'commodities'"
).fetchone()
snap_conn.close()

print("failure/backup OK: truncated build rejected, live cache preserved, user snapshot valid")
