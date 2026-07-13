"""Profile repair: overview, explicit bucket assignment, delete, activate."""

import os
import sys
import tempfile
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_tmp = tempfile.TemporaryDirectory()
os.environ["ET_DATA_DIR"] = _tmp.name

from elite import marketdb  # noqa: E402
from elite.errors import ValidationError  # noqa: E402

# A migrated v2.0 database: real history quarantined under `default`, plus a
# stale test identity that grabbed a few rows of its own.
conn = marketdb.connect()
conn.execute(
    "INSERT OR REPLACE INTO user_meta(key,value)"
    " VALUES('migrated_from_market_db','v2.0 test fixture')"
)
for ts, symbol in ((1000, "gold"), (2000, "silver"), (3000, "tritium")):
    conn.execute(
        "INSERT INTO trade_log(commander_id, ts, event, symbol, count, price, total)"
        " VALUES('default', ?, 'sell', ?, 10, 100, 1000)",
        (ts, symbol),
    )
conn.execute(
    "INSERT INTO watches(commander_id, created, payload)"
    " VALUES('default', '2026-07-01T00:00:00Z', '{}')"
)
conn.execute(
    "INSERT INTO tracked_markets(commander_id, market_id, added_ts) VALUES('default', 42, 1)"
)
conn.commit()
conn.close()

real = marketdb.ensure_commander_profile("Real Cmdr", make_active=True)
stale = marketdb.ensure_commander_profile("Stale Test", make_active=False)
conn = marketdb.connect_user()
conn.execute(
    "INSERT INTO trade_log(commander_id, ts, event, symbol, count, price, total)"
    " VALUES(?, 9000, 'sell', 'bauxite', 1, 1, 1)",
    (stale,),
)
# Same logical row also exists under default: assignment must not duplicate it.
conn.execute(
    "INSERT INTO trade_log(commander_id, ts, event, symbol, count, price, total)"
    " VALUES(?, 2000, 'sell', 'silver', 10, 100, 1000)",
    (real,),
)
conn.commit()
conn.close()

# --- overview ---------------------------------------------------------------
overview = marketdb.profile_overview()
assert overview["active_commander_id"] == real
by_id = {p["id"]: p for p in overview["profiles"]}
assert by_id[real]["active"] and not by_id[stale]["active"]
# make_active adoption on a migrated DB claims user-authored prefs only:
# watches + tracked_markets moved to the real commander, analytics stayed.
assert overview["unattributed"]["tables"].get("trade_log") == 3
assert "watches" not in overview["unattributed"]["tables"]
assert by_id[real]["tables"].get("watches") == 1
assert by_id[real]["tables"].get("tracked_markets") == 1

# --- guarded actions ---------------------------------------------------------
try:
    marketdb.assign_unattributed_history("default")
    raise AssertionError("assigning to default must be rejected")
except ValidationError:
    pass
try:
    marketdb.delete_commander_profile(real)
    raise AssertionError("deleting the active profile must be rejected")
except ValidationError:
    pass
try:
    marketdb.delete_commander_profile("default")
    raise AssertionError("deleting the default bucket must be rejected")
except ValidationError:
    pass
try:
    marketdb.assign_unattributed_history("cmdr-does-not-exist")
    raise AssertionError("assigning to a missing profile must be rejected")
except ValidationError:
    pass

# --- explicit assignment ------------------------------------------------------
result = marketdb.assign_unattributed_history(real)
assert result["moved"].get("trade_log") == 3, result
overview = marketdb.profile_overview()
assert overview["unattributed"]["rows"] == 0
assert overview["adopted_by"] == real
conn = marketdb.connect_user()
# 3 default rows merged onto 1 pre-existing (one identical PK collapses): 3 unique.
n = conn.execute(
    "SELECT COUNT(*) FROM trade_log WHERE commander_id = ?", (real,)
).fetchone()[0]
assert n == 3, n
conn.close()
# Idempotent re-run is a no-op.
assert marketdb.assign_unattributed_history(real)["rows"] == 0

# --- per-file bookkeeping never moves and never counts ------------------------
# Regression: owner-less launcher stubs are recorded under `default`; moving
# those markers with an assignment made every restart re-import the stubs and
# resurrect the "unassigned history" banner.
conn = marketdb.connect_user()
conn.execute(
    "CREATE TABLE IF NOT EXISTS ledger_journal_files("
    "commander_id TEXT NOT NULL, file_key TEXT NOT NULL, size_bytes INTEGER,"
    " mtime_ns INTEGER, content_hash TEXT, last_line INTEGER NOT NULL DEFAULT 0,"
    " event_count INTEGER NOT NULL DEFAULT 0, first_event_ts INTEGER,"
    " last_event_ts INTEGER, complete INTEGER NOT NULL DEFAULT 0,"
    " imported_at TEXT, error TEXT, PRIMARY KEY(commander_id,file_key)) WITHOUT ROWID"
)
conn.execute(
    "INSERT OR REPLACE INTO imported_journals(commander_id, filename)"
    " VALUES('default', 'Journal.stub.log')"
)
conn.execute(
    "INSERT OR REPLACE INTO ledger_journal_files(commander_id, file_key, complete)"
    " VALUES('default', 'Journal.stub.log', 1)"
)
conn.execute("INSERT INTO watches(commander_id, created, payload) VALUES('default','x','{}')")
conn.commit()
conn.close()

overview = marketdb.profile_overview()
assert overview["unattributed"]["tables"] == {"watches": 1}, overview["unattributed"]
result = marketdb.assign_unattributed_history(real)
assert result["moved"] == {"watches": 1}, result
conn = marketdb.connect_user()
for table, key_column in (("imported_journals", "filename"), ("ledger_journal_files", "file_key")):
    owner = conn.execute(
        f"SELECT commander_id FROM {table} WHERE {key_column}='Journal.stub.log'"
    ).fetchone()[0]
    assert owner == "default", (table, owner)
conn.close()

# --- delete stale identity ----------------------------------------------------
result = marketdb.delete_commander_profile(stale)
assert result["removed"].get("trade_log") == 1
overview = marketdb.profile_overview()
assert stale not in {p["id"] for p in overview["profiles"]}

# Deleting the adoption owner clears the marker so a real commander can adopt.
other = marketdb.ensure_commander_profile("Other", make_active=False)
marketdb.activate_commander_profile(other)
assert marketdb.profile_overview()["active_commander_id"] == other
result = marketdb.delete_commander_profile(real)
assert marketdb.profile_overview()["adopted_by"] is None

# --- endpoints (localhost = admin) --------------------------------------------
from elite.server import create_app  # noqa: E402
from elite.state import AppState  # noqa: E402

app = create_app(AppState())
client = app.test_client()

response = client.get("/api/profiles")
assert response.status_code == 200
payload = response.get_json()
assert {p["id"] for p in payload["profiles"]} >= {other}

response = client.post("/api/profiles/assign-unattributed", json={"commander_id": "nope"})
assert response.status_code == 400
assert "profile" in response.get_json()["error"].lower()

response = client.delete(f"/api/profiles/{other}")
assert response.status_code == 400  # active profile is protected

response = client.post(f"/api/profiles/{other}/activate")
assert response.status_code == 200

print("profile admin OK")
