"""Commander isolation for durable analytics, tracking, and route watches."""

import json
import os
import sys
import tempfile
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_tmp = tempfile.TemporaryDirectory()
os.environ["ET_DATA_DIR"] = _tmp.name

from elite import alerts, marketdb  # noqa: E402


def reset_alert_view():
    with alerts._lock:
        alerts.WATCHES.clear()
        alerts.ALERTS.clear()
        alerts._loaded = False
        alerts._loaded_commander_id = None


def loop(label):
    return {
        "a": {"market_id": 111, "station": f"{label} Start"},
        "b": {"market_id": 222, "station": f"{label} End"},
        "profit": 500_000,
        "outbound": {
            "commodities": [
                {
                    "symbol": "gold",
                    "amount": 40,
                    "buy_price": 9_000,
                    "sell_price": 10_000,
                }
            ]
        },
        "inbound": {"commodities": []},
    }


# User-authored preferences created before an identity existed belong to the
# first commander. Derived v2.0 history may contain several accounts and must
# stay quarantined until the journal importer reconstructs attributed rows.
legacy_watch = {
    "label": "Legacy route",
    "market_ids": [901, 902],
    "conditions": [[901, "silver", "buy", 5, 100, "Legacy Port"]],
    "profit": 10,
}
conn = marketdb.connect()
conn.execute(
    "INSERT OR REPLACE INTO user_meta(key,value)"
    " VALUES('migrated_from_market_db','v2.0 test fixture')"
)
conn.execute(
    "INSERT INTO trade_log"
    "(commander_id, ts, event, symbol, name, count, price, total, profit)"
    " VALUES('default', 1, 'sell', 'gold', 'Gold', 1, 10, 10, 2)"
)
conn.execute("INSERT INTO balance_log VALUES('default', 1, 1000)")
conn.execute("INSERT INTO income_log VALUES('default', 1, 'mission', 'legacy', 50)")
conn.execute("INSERT INTO imported_journals VALUES('default', 'Journal.legacy.log')")
conn.execute("INSERT INTO tracked_markets VALUES('default', 901, 1)")
legacy_watch_id = conn.execute(
    "INSERT INTO watches(commander_id, created, payload) VALUES('default', 'then', ?)",
    (json.dumps(legacy_watch),),
).lastrowid
conn.commit()
conn.close()

alpha = marketdb.ensure_commander_profile("Alpha")
assert marketdb.active_commander_id() == alpha

user = marketdb.connect_user()
for table in ("tracked_markets", "watches"):
    assert user.execute(
        f"SELECT COUNT(*) FROM {table} WHERE commander_id = 'default'"
    ).fetchone()[0] == 0, f"{table} retained legacy default rows"
    assert user.execute(
        f"SELECT COUNT(*) FROM {table} WHERE commander_id = ?", (alpha,)
    ).fetchone()[0] >= 1, f"{table} was not adopted by the first commander"
for table in ("trade_log", "balance_log", "income_log", "imported_journals"):
    assert user.execute(
        f"SELECT COUNT(*) FROM {table} WHERE commander_id = 'default'"
    ).fetchone()[0] >= 1, f"{table} mixed history was not quarantined"
    assert user.execute(
        f"SELECT COUNT(*) FROM {table} WHERE commander_id = ?", (alpha,)
    ).fetchone()[0] == 0, f"{table} mixed history was misattributed to Alpha"
assert user.execute(
    "SELECT value FROM user_meta WHERE key = 'default_profile_adopted_by'"
).fetchone()[0] == alpha
user.close()

# Omitted ids follow the active commander; explicit ids remain supported for
# imports and other code that deliberately targets a non-active profile.
marketdb.log_trade(2, "sell", "silver", "Silver", 2, 20, 40, 4)
marketdb.log_balance(2, 2000)
marketdb.log_income(2, "bounty", 100, "Alpha bounty")
marketdb.log_trade(3, "sell", "palladium", "Palladium", 1, 30, 30, 3,
                   commander_id="explicit-profile")

beta = marketdb.ensure_commander_profile("Beta")
marketdb.log_trade(4, "sell", "platinum", "Platinum", 1, 40, 40, 4)
marketdb.log_balance(4, 4000)
marketdb.log_income(4, "exploration", 400, "Beta scan")

user = marketdb.connect_user()
assert user.execute(
    "SELECT commander_id FROM trade_log WHERE ts = 2"
).fetchone()[0] == alpha
assert user.execute(
    "SELECT commander_id FROM trade_log WHERE ts = 4"
).fetchone()[0] == beta
assert user.execute(
    "SELECT commander_id FROM trade_log WHERE ts = 3"
).fetchone()[0] == "explicit-profile"
assert user.execute(
    "SELECT value FROM user_meta WHERE key = 'default_profile_adopted_by'"
).fetchone()[0] == alpha, "later profiles must not steal adopted history"
user.close()

# The tracked-market cache is profile keyed, including after both profiles
# have already populated their cache entries and the active commander flips.
marketdb.track_market(111, commander_id=alpha)
marketdb.track_market(222)  # Beta is active.
assert 222 in marketdb.tracked_ids() and 111 not in marketdb.tracked_ids()
assert {111, 901}.issubset(marketdb.tracked_ids(alpha))
marketdb.ensure_commander_profile("Alpha")
assert 111 in marketdb.tracked_ids() and 222 not in marketdb.tracked_ids()
marketdb.ensure_commander_profile("Beta")
assert 222 in marketdb.tracked_ids() and 111 not in marketdb.tracked_ids()

print("commander storage OK: safe preference adoption, history quarantine, isolated tracking cache")

# Watches are loaded, inserted, updated, deleted, and snapshotted only for the
# selected commander. Both pilots watch the same market to prove that EDDN
# evaluates only the currently active watch.
reset_alert_view()
marketdb.ensure_commander_profile("Alpha")
alpha_watch = alerts.add_loop_watch(loop("Alpha"))
alpha_snapshot = alerts.snapshot()
assert {item["id"] for item in alpha_snapshot["watches"]} == {
    legacy_watch_id, alpha_watch["id"]
}
assert alerts.watched_market_ids() == {111, 222, 901, 902}

marketdb.ensure_commander_profile("Beta")
assert alerts.snapshot() == {"watches": [], "alerts": []}
beta_watch = alerts.add_loop_watch(loop("Beta"))
assert alerts.watched_market_ids() == {111, 222}
assert not alerts.remove_watch(alpha_watch["id"]), "Beta deleted Alpha's watch"

alerts.on_market_update(222, "Shared Port", [("gold", 0, 8_000, 0, 500)])
beta_snapshot = alerts.snapshot()
assert {item["id"] for item in beta_snapshot["watches"]} == {beta_watch["id"]}
assert len(beta_snapshot["alerts"]) == 1
assert beta_snapshot["alerts"][0]["watch_id"] == beta_watch["id"]
assert beta_snapshot["alerts"][0]["commander_id"] == beta
assert "Beta" in beta_snapshot["alerts"][0]["text"]

user = marketdb.connect_user()
alpha_payload = json.loads(user.execute(
    "SELECT payload FROM watches WHERE id = ? AND commander_id = ?",
    (alpha_watch["id"], alpha),
).fetchone()[0])
beta_payload = json.loads(user.execute(
    "SELECT payload FROM watches WHERE id = ? AND commander_id = ?",
    (beta_watch["id"], beta),
).fetchone()[0])
assert [c[4] for c in alpha_payload["conditions"] if c[2] == "sell"] == [10_000]
assert [c[4] for c in beta_payload["conditions"] if c[2] == "sell"] == [8_000]
user.close()

# Switching profiles discards transient alerts rather than leaking their route
# details. A subsequent EDDN message now updates Alpha's baseline, not Beta's.
marketdb.ensure_commander_profile("Alpha")
alpha_snapshot = alerts.snapshot()
assert alpha_snapshot["alerts"] == []
assert {item["id"] for item in alpha_snapshot["watches"]} == {
    legacy_watch_id, alpha_watch["id"]
}
alerts.on_market_update(222, "Shared Port", [("gold", 0, 8_500, 0, 500)])
alpha_snapshot = alerts.snapshot()
assert len(alpha_snapshot["alerts"]) == 1
assert alpha_snapshot["alerts"][0]["watch_id"] == alpha_watch["id"]
assert "Alpha" in alpha_snapshot["alerts"][0]["text"]
assert alerts.remove_watch(alpha_watch["id"])

user = marketdb.connect_user()
assert not user.execute(
    "SELECT 1 FROM watches WHERE id = ? AND commander_id = ?",
    (alpha_watch["id"], alpha),
).fetchone()
assert user.execute(
    "SELECT 1 FROM watches WHERE id = ? AND commander_id = ?",
    (beta_watch["id"], beta),
).fetchone(), "removing Alpha's watch deleted Beta's watch"
user.close()

print("route watches OK: active-only CRUD/snapshots and safe EDDN rebaselining")
print("ALL COMMANDER-SCOPING TESTS PASSED")
