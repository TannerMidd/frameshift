"""System stations: Spansh dump parsing (orbital + surface, service chips,
sort by arrival distance) and the local station-market reader."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_tmp = tempfile.TemporaryDirectory()
os.environ["ET_DATA_DIR"] = _tmp.name

from elite import marketdb  # noqa: E402
from elite.spansh import _parse_dump_stations  # noqa: E402

dump = {
    "stations": [
        {"id": 2, "name": "Far Orbital", "type": "Coriolis Starport", "distanceToArrival": 2000.0,
         "landingPads": {"large": 4, "medium": 6, "small": 2},
         "primaryEconomy": "Industrial", "government": "Corporate",
         "controllingFaction": "Test Corp", "allegiance": "Federation",
         "services": ["Dock", "Market", "Shipyard", "Crew Lounge", "Vista Genomics"],
         "market": {"commodities": [{"name": "Gold"}], "updateTime": "2026-07-09 00:00:00"}},
        {"id": 1, "name": "Near Orbital", "type": "Orbis Starport", "distanceToArrival": 10.0,
         "landingPads": {"large": 8, "medium": 10, "small": 4},
         "services": ["Dock", "Market", "Outfitting", "Material Trader"],
         "market": {}},
    ],
    "bodies": [
        {"name": "Testland 2", "stations": [
            {"id": 3, "name": "Dusty Pad", "type": "Odyssey Settlement", "distanceToArrival": 500.0,
             "services": ["Dock"], "market": {}},
        ]},
    ],
}

sts = _parse_dump_stations(dump)
assert [s["station"] for s in sts] == ["Near Orbital", "Dusty Pad", "Far Orbital"], \
    [s["station"] for s in sts]  # sorted by arrival distance
far = next(s for s in sts if s["station"] == "Far Orbital")
assert far["pads"] == {"l": 4, "m": 6, "s": 2} and far["faction"] == "Test Corp"
assert far["has_market"] and far["services"] == ["Market", "Shipyard", "Vista Genomics"], far["services"]
dusty = next(s for s in sts if s["station"] == "Dusty Pad")
assert dusty["body"] == "Testland 2" and not dusty["has_market"]

print("dump parsing OK: orbital+surface merge, distance sort, curated service chips")

# ---------- local station market reader ----------

conn = marketdb.connect()
conn.execute("INSERT INTO systems(id64, name, x, y, z) VALUES(9, 'Testland', 0, 0, 0)")
conn.execute("INSERT INTO stations(market_id, system_id64, name, type, dist_ls, large_pad, updated_at)"
             " VALUES(2, 9, 'Far Orbital', 'Coriolis Starport', 2000, 1, 12345)")
conn.execute("INSERT INTO commodities(market_id, symbol, buy_price, sell_price, supply, demand)"
             " VALUES(2, 'gold', 9000, 9500, 100, 50)")
conn.execute("INSERT INTO commodity_names(symbol, name, category) VALUES('gold', 'Gold', 'Metals')")
conn.commit()
conn.close()

conn = marketdb.connect()
try:
    assert marketdb.system_station_markets(conn, "Testland") == {2: 12345}
finally:
    conn.close()
m = marketdb.station_market(2)
assert m["station"] == "Far Orbital" and m["updated_at"] == 12345
assert m["items"] == [{"symbol": "gold", "name": "Gold", "category": "Metals",
                       "buy": 9000, "sell": 9500, "stock": 100, "demand": 50}], m["items"]
assert marketdb.station_market(999) is None

print("station market OK: per-system lookup + full commodity table with names")
print("ALL SYSTEM-STATIONS TESTS PASSED")
