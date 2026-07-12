"""ALTER-era commander tables are rebuilt with real compound keys."""

import os
import sqlite3
import sys
import tempfile
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
_tmp = tempfile.TemporaryDirectory()
os.environ["ET_DATA_DIR"] = _tmp.name
path = Path(_tmp.name) / "commander.db"

# Representative core and feature tables from early development builds. They
# have commander_id columns, but the old primary keys still make rows global.
conn = sqlite3.connect(path)
conn.executescript("""
CREATE TABLE user_meta(key TEXT PRIMARY KEY,value TEXT);
CREATE TABLE tracked_markets(
    market_id INTEGER PRIMARY KEY, added_ts INTEGER NOT NULL,
    commander_id TEXT NOT NULL DEFAULT 'default');
INSERT INTO tracked_markets VALUES(42,100,'default');
CREATE TABLE specialist_state(
    workflow TEXT PRIMARY KEY, state_json TEXT NOT NULL, updated_at TEXT NOT NULL,
    commander_id TEXT NOT NULL DEFAULT 'default');
INSERT INTO specialist_state VALUES('mining','{}','then','default');
CREATE TABLE timing_pending(
    activity TEXT NOT NULL, context_key TEXT NOT NULL DEFAULT '',
    started_at INTEGER NOT NULL, source_event TEXT,
    commander_id TEXT NOT NULL DEFAULT 'default',
    PRIMARY KEY(activity,context_key));
INSERT INTO timing_pending VALUES('docking','Port',100,'DockingRequested','default');
CREATE TABLE specialist_history(
    id INTEGER PRIMARY KEY AUTOINCREMENT, workflow TEXT NOT NULL,
    session_key TEXT NOT NULL, started_ts INTEGER, ended_ts INTEGER,
    summary_json TEXT NOT NULL, created_at TEXT NOT NULL,
    commander_id TEXT NOT NULL DEFAULT 'default', UNIQUE(workflow,session_key));
INSERT INTO specialist_history(
    workflow,session_key,summary_json,created_at,commander_id
) VALUES('mining','session-1','{}','then','default');
""")
conn.commit()
conn.close()

from elite import commanderdb  # noqa: E402

conn = commanderdb.connect(path)
assert tuple(
    row[1] for row in sorted(
        conn.execute("PRAGMA table_info(tracked_markets)"), key=lambda row: row[5]
    ) if row[5]
) == ("commander_id", "market_id")
assert tuple(
    row[1] for row in sorted(
        conn.execute("PRAGMA table_info(specialist_state)"), key=lambda row: row[5]
    ) if row[5]
) == ("commander_id", "workflow")
assert tuple(
    row[1] for row in sorted(
        conn.execute("PRAGMA table_info(timing_pending)"), key=lambda row: row[5]
    ) if row[5]
) == ("commander_id", "activity", "context_key")
unique_keys = {
    tuple(item[2] for item in conn.execute(f"PRAGMA index_info('{row[1]}')"))
    for row in conn.execute("PRAGMA index_list(specialist_history)") if row[2]
}
assert ("commander_id", "workflow", "session_key") in unique_keys

# The old row survives under quarantine and the same logical keys can now be
# stored independently for two actual commanders.
assert conn.execute(
    "SELECT added_ts FROM tracked_markets WHERE commander_id='default' AND market_id=42"
).fetchone()[0] == 100
conn.execute("INSERT INTO tracked_markets VALUES('alpha',42,101)")
conn.execute("INSERT INTO tracked_markets VALUES('beta',42,102)")
conn.execute("INSERT INTO specialist_state VALUES('alpha','mining','{}','now')")
conn.execute("INSERT INTO specialist_state VALUES('beta','mining','{}','now')")
conn.execute(
    "INSERT INTO specialist_history("
    "commander_id,workflow,session_key,summary_json,created_at)"
    " VALUES('alpha','mining','session-1','{}','now')"
)
conn.execute(
    "INSERT INTO specialist_history("
    "commander_id,workflow,session_key,summary_json,created_at)"
    " VALUES('beta','mining','session-1','{}','now')"
)
conn.commit()
assert conn.execute(
    "SELECT COUNT(*) FROM tracked_markets WHERE market_id=42"
).fetchone()[0] == 3
assert conn.execute(
    "SELECT COUNT(*) FROM specialist_state WHERE workflow='mining'"
).fetchone()[0] == 3
assert conn.execute(
    "SELECT COUNT(*) FROM specialist_history WHERE session_key='session-1'"
).fetchone()[0] == 3
conn.close()

print("commander key migration OK: old rows retained, profile keys rebuilt")
