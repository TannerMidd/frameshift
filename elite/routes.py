"""Local trade-route planner over the market database: the same feature the
Spansh/Inara planners provide, computed offline against EDDN-fresh prices.

Beam search: each hop is buy-at-source -> sell-at-destination; destinations
must lie within max_hop_distance ly of the source system. Cargo is filled
greedily with the most profitable commodities (respecting supply, demand and
capital), and the best few partial routes are extended each round."""

import math

from . import marketdb
from .errors import UserFacingError

BEAM_WIDTH = 6
DESTS_PER_HOP = 5
MAX_COMMODITIES_PER_HOP = 3
MAX_SOURCE_CANDIDATES = 60
PAIR_QUERY_LIMIT = 600

LOOP_STATION_CAP = 900       # nearest stations considered in loop mode
LOOP_FLOW_LIMIT = 30000      # top commodity flows pulled from SQL
LOOP_FLOWS_PER_PAIR = 12
LOOP_RESULTS = 8
LOOP_CANDIDATES = 250        # loops kept for time-weighted ranking

# Travel-time model for profit/hour (rough but consistent across candidates).
JUMP_TIME_S = 50.0           # one hyperspace jump incl. align/scoop average
DOCK_OVERHEAD_S = 180.0      # request dock, land, trade, launch
SC_BASE_S = 60.0             # drop from jump + initial acceleration
UNKNOWN_DIST_LS = 500.0      # assumed when a station's star distance is unknown


def _supercruise_time_s(dist_ls):
    ls = dist_ls if dist_ls and dist_ls > 0 else UNKNOWN_DIST_LS
    return SC_BASE_S + 170.0 * (ls / 1000.0) ** 0.35


def _leg_time_s(distance_ly, dest_dist_ls, jump_range):
    jumps = math.ceil(distance_ly / max(1.0, jump_range)) if distance_ly > 0.01 else 0
    return jumps * JUMP_TIME_S + _supercruise_time_s(dest_dist_ls) + DOCK_OVERHEAD_S


class RouteError(UserFacingError):
    pass


# Older SQLite builds cap host parameters at 999; wide-radius searches (deep
# space needs 1000+ ly) sweep in far more stations than that, so every
# market-id IN(...) query must run in chunks.
SQL_IN_CHUNK = 800


def _chunks(seq, n=SQL_IN_CHUNK):
    seq = list(seq)
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def plan_route_local(
    system,
    station=None,
    star_pos=None,
    capital=100000,
    max_cargo=8,
    max_hop_distance=25.0,
    max_hops=4,
    max_system_distance=1000,
    max_price_age_days=30,
    requires_large_pad=False,
    min_supply=1,
):
    conn = marketdb.connect()
    try:
        if not marketdb.status(conn)["ready"]:
            raise RouteError("Local market database is empty - build it from the Market Database panel first.")

        start = _resolve_start(conn, system, star_pos)
        min_updated = marketdb.now_epoch() - int(max_price_age_days) * 86400
        filters = {
            "min_updated": min_updated,
            "require_large_pad": bool(requires_large_pad),
            "max_dist_ls": float(max_system_distance),
        }

        sources = _source_candidates(conn, start, station, float(max_hop_distance), filters)
        if not sources:
            raise RouteError(
                "No market stations found near the start with fresh enough prices - "
                "try a larger max hop distance or price age."
            )

        beam = [
            {"hops": [], "profit": 0, "capital": int(capital), "at": src, "seen": {src["market_id"]}}
            for src in sources
        ]
        best = None
        for _ in range(max(1, int(max_hops))):
            candidates = []
            for route in beam:
                candidates.extend(
                    _extend(conn, route, float(max_hop_distance), int(max_cargo), filters,
                            max(1, int(min_supply)))
                )
            if not candidates:
                break
            candidates.sort(key=lambda r: r["profit"], reverse=True)
            beam = candidates[:BEAM_WIDTH]
            if best is None or beam[0]["profit"] > best["profit"]:
                best = beam[0]

        if not best or not best["hops"]:
            raise RouteError("No profitable route found with those settings.")
        return _format(conn, best)
    finally:
        conn.close()


# ---------- internals ----------


def _resolve_start(conn, system, star_pos):
    if system:
        row = marketdb.find_system(conn, system)
        if row:
            return {"system": row[1], "x": row[2], "y": row[3], "z": row[4]}
    if star_pos and len(star_pos) == 3:
        return {"system": system or "current position", "x": star_pos[0], "y": star_pos[1], "z": star_pos[2]}
    raise RouteError(f"Start system '{system}' not found in the local database.")


def _source_candidates(conn, start, station_name, max_hop, filters):
    near = marketdb.stations_near(conn, start["x"], start["y"], start["z"], max_hop, **filters)
    if station_name:
        exact = [
            s for s in near
            if s["station"].lower() == station_name.lower()
            and s["system"].lower() == start["system"].lower()
        ]
        if exact:
            return exact
    # Prefer close-by stations as the first buy point.
    near.sort(key=lambda s: (s["x"] - start["x"]) ** 2 + (s["y"] - start["y"]) ** 2 + (s["z"] - start["z"]) ** 2)
    return near[:MAX_SOURCE_CANDIDATES]


def _extend(conn, route, max_hop, max_cargo, filters, min_supply=1):
    src = route["at"]
    dests = marketdb.stations_near(conn, src["x"], src["y"], src["z"], max_hop, **filters)
    dest_by_id = {
        d["market_id"]: d for d in dests
        if d["market_id"] != src["market_id"] and d["market_id"] not in route["seen"]
    }
    if not dest_by_id:
        return []

    pairs = []
    multi = len(dest_by_id) > SQL_IN_CHUNK
    for chunk in _chunks(dest_by_id.keys()):
        marks = ",".join("?" for _ in chunk)
        pairs.extend(conn.execute(
            f"""SELECT cd.market_id, cs.symbol, cs.buy_price, cd.sell_price, cs.supply, cd.demand
                FROM commodities cs
                JOIN commodities cd ON cd.symbol = cs.symbol
                WHERE cs.market_id = ?
                  AND cd.market_id IN ({marks})
                  AND cs.supply > 0 AND cs.buy_price > 0
                  AND cd.demand > 0 AND cd.sell_price > cs.buy_price
                ORDER BY (cd.sell_price - cs.buy_price) DESC
                LIMIT {PAIR_QUERY_LIMIT}""",
            [src["market_id"], *chunk],
        ).fetchall())
    if multi:  # re-establish the global best-margin trim across chunks
        pairs.sort(key=lambda p: -(p[3] - p[2]))
        pairs = pairs[:PAIR_QUERY_LIMIT]

    flows_by_dest = {}
    for market_id, symbol, buy, sell, supply, demand in pairs:
        flows_by_dest.setdefault(market_id, []).append((symbol, buy, sell, supply, demand))

    extensions = []
    for market_id, flows in flows_by_dest.items():
        load = _fill_cargo(flows, max_cargo, route["capital"], min_supply)
        if not load:
            continue
        dest = dest_by_id[market_id]
        hop_profit = sum(c["profit"] for c in load)
        extensions.append(
            {
                "hops": route["hops"]
                + [{"from": src, "to": dest, "commodities": load, "profit": hop_profit,
                    "distance": _dist(src, dest)}],
                "profit": route["profit"] + hop_profit,
                "capital": route["capital"] + hop_profit,
                "at": dest,
                "seen": route["seen"] | {market_id},
            }
        )
    extensions.sort(key=lambda r: r["profit"], reverse=True)
    return extensions[:DESTS_PER_HOP]


def _fill_cargo(flows, max_cargo, capital, min_supply=1):
    """Greedy fill by unit profit; flows are pre-sorted by the SQL query.
    min_supply is a floor on BOTH source stock and destination demand, so
    routes never hinge on a handful of units."""
    space, funds = max_cargo, capital
    load = []
    for symbol, buy, sell, supply, demand in flows:
        if space <= 0 or funds < buy:
            break
        if supply < min_supply or demand < min_supply:
            continue
        if any(c["symbol"] == symbol for c in load):
            continue
        units = min(space, supply, demand, funds // buy)
        if units <= 0:
            continue
        load.append(
            {"symbol": symbol, "amount": units, "buy_price": buy, "sell_price": sell,
             "profit": units * (sell - buy), "supply": supply, "demand": demand}
        )
        space -= units
        funds -= units * buy
        if len(load) >= MAX_COMMODITIES_PER_HOP:
            break
    return load


def plan_loops(
    system,
    station=None,
    star_pos=None,
    capital=100000,
    max_cargo=8,
    radius=100.0,
    max_price_age_days=30,
    max_system_distance=1000,
    requires_large_pad=False,
    min_supply=1,
    jump_range=20.0,
    max_leg=None,
    top_n=LOOP_RESULTS,
):
    """Inara-style 2-station round trips near the player: fill the hold A->B,
    refill B->A, rank by estimated profit PER HOUR (travel-time model: jumps +
    supercruise + docking), not raw profit per trip. Legs may span several jumps.

    `radius` = how far from the player loop stations may be (getting to a distant
    loop is a one-time cost, so it is not penalised in the ranking); `max_leg` =
    the max distance between the two loop stations (defaults to radius)."""
    conn = marketdb.connect()
    try:
        if not marketdb.status(conn)["ready"]:
            raise RouteError("Local market database is empty - build it from the Market Database panel first.")
        start = _resolve_start(conn, system, star_pos)

        stations = marketdb.stations_near(
            conn, start["x"], start["y"], start["z"], float(radius),
            min_updated=marketdb.now_epoch() - int(max_price_age_days) * 86400,
            require_large_pad=bool(requires_large_pad),
            max_dist_ls=float(max_system_distance) if max_system_distance else None,
        )
        if len(stations) < 2:
            raise RouteError("Fewer than two market stations in range - increase the radius or price age.")
        stations.sort(key=lambda s: _dist(s, start))
        stations = stations[:LOOP_STATION_CAP]
        by_id = {s["market_id"]: s for s in stations}

        conn.execute("DROP TABLE IF EXISTS temp.near_buy")
        conn.execute("DROP TABLE IF EXISTS temp.near_sell")
        conn.execute("CREATE TEMP TABLE near_buy(market_id INTEGER, symbol TEXT, buy INTEGER, supply INTEGER)")
        conn.execute("CREATE TEMP TABLE near_sell(market_id INTEGER, symbol TEXT, sell INTEGER, demand INTEGER)")
        min_units = max(1, int(min_supply))
        for chunk in _chunks(by_id.keys()):
            marks = ",".join("?" for _ in chunk)
            conn.execute(
                f"INSERT INTO temp.near_buy SELECT market_id, symbol, buy_price, supply"
                f" FROM commodities WHERE market_id IN ({marks}) AND supply >= ? AND buy_price > 0",
                [*chunk, min_units],
            )
            conn.execute(
                f"INSERT INTO temp.near_sell SELECT market_id, symbol, sell_price, demand"
                f" FROM commodities WHERE market_id IN ({marks}) AND demand >= ? AND sell_price > 0",
                [*chunk, min_units],
            )
        conn.execute("CREATE INDEX temp.idx_nearsell_sym ON near_sell(symbol)")

        flows = conn.execute(
            f"""SELECT a.market_id, b.market_id, a.symbol, a.buy, b.sell, a.supply, b.demand
                FROM near_buy a JOIN near_sell b ON b.symbol = a.symbol
                WHERE a.market_id != b.market_id AND b.sell > a.buy
                ORDER BY (b.sell - a.buy) DESC LIMIT {LOOP_FLOW_LIMIT}"""
        ).fetchall()

        # Flows per directed pair, best-first (SQL already sorted them).
        directed = {}
        for a, b, sym, buy, sell, supply, demand in flows:
            lst = directed.setdefault((a, b), [])
            if len(lst) < LOOP_FLOWS_PER_PAIR:
                lst.append((sym, buy, sell, supply, demand))

        capital = int(capital)
        max_cargo = int(max_cargo)
        min_supply = max(1, int(min_supply))
        # Without a cap, two stations each within radius of the player could
        # still be up to 2x radius apart from each other.
        leg_cap = float(max_leg) if max_leg else float(radius)
        seen_pairs = set()
        loops = []
        for (a, b) in directed:
            key = frozenset((a, b))
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            pair_dist = _dist(by_id[a], by_id[b])
            if pair_dist > leg_cap:
                continue
            out = _fill_cargo(directed.get((a, b), []), max_cargo, capital, min_supply)
            back = _fill_cargo(directed.get((b, a), []), max_cargo, capital, min_supply)
            out_p = sum(c["profit"] for c in out)
            back_p = sum(c["profit"] for c in back)
            if out_p + back_p <= 0:
                continue
            # Present the more profitable leg as the outbound one.
            if back_p > out_p:
                a, b, out, back, out_p, back_p = b, a, back, out, back_p, out_p
            trip_s = (
                _leg_time_s(pair_dist, by_id[b]["dist_ls"], float(jump_range))
                + _leg_time_s(pair_dist, by_id[a]["dist_ls"], float(jump_range))
            )
            loops.append(
                {"a": by_id[a], "b": by_id[b], "out": out, "back": back,
                 "profit": out_p + back_p, "out_profit": out_p, "back_profit": back_p,
                 "trip_s": trip_s, "profit_per_hour": (out_p + back_p) * 3600.0 / trip_s}
            )
        # Rank by earnings rate: a 4M loop that takes an hour loses to a 2M
        # loop that takes 20 minutes.
        loops.sort(key=lambda l: l["profit_per_hour"], reverse=True)
        loops = loops[:top_n]
        if not loops:
            raise RouteError("No profitable loop found with those settings.")
        return _format_loops(conn, loops, start)
    finally:
        conn.close()


def _format_loops(conn, loops, start):
    symbols = {c["symbol"] for l in loops for c in l["out"] + l["back"]}
    names = marketdb.commodity_display_names(conn, symbols)

    def leg(commodities):
        return [
            {
                "name": names.get(c["symbol"], c["symbol"].title()),
                "symbol": c["symbol"],
                "amount": c["amount"],
                "buy_price": c["buy_price"],
                "sell_price": c["sell_price"],
                "profit": c["profit"],
                "supply": c.get("supply"),
                "demand": c.get("demand"),
            }
            for c in commodities
        ]

    def endpoint(st):
        return {
            "station": st["station"],
            "system": st["system"],
            "market_id": st["market_id"],
            "dist_ls": st["dist_ls"],
            "large_pad": st["large_pad"],
            "updated_at": st["updated_at"],
            "from_player": round(_dist(st, start), 1),
        }

    return [
        {
            "a": endpoint(l["a"]),
            "b": endpoint(l["b"]),
            "distance": round(_dist(l["a"], l["b"]), 1),
            "profit": l["profit"],
            "minutes_per_trip": round(l["trip_s"] / 60.0),
            "profit_per_hour": int(l["profit_per_hour"]),
            "outbound": {"profit": l["out_profit"], "commodities": leg(l["out"])},
            "inbound": {"profit": l["back_profit"], "commodities": leg(l["back"])},
        }
        for l in loops
    ]


def _dist(a, b):
    return math.sqrt((a["x"] - b["x"]) ** 2 + (a["y"] - b["y"]) ** 2 + (a["z"] - b["z"]) ** 2)


def list_commodities():
    """All known commodities, for the search autocomplete."""
    conn = marketdb.connect()
    try:
        rows = conn.execute(
            "SELECT symbol, name, category FROM commodity_names ORDER BY name"
        ).fetchall()
        return [{"symbol": r[0], "name": r[1], "category": r[2]} for r in rows]
    finally:
        conn.close()


def search_commodity(
    query,
    mode,  # "buy" (I want to purchase) or "sell" (I want to offload cargo)
    system=None,
    star_pos=None,
    radius=50.0,
    min_units=1,
    max_price_age_days=30,
    requires_large_pad=False,
    max_system_distance=None,
    limit=40,
):
    if mode not in ("buy", "sell"):
        raise RouteError("mode must be 'buy' or 'sell'.")
    conn = marketdb.connect()
    try:
        if not marketdb.status(conn)["ready"]:
            raise RouteError("Local market database is empty - build it from the Market Database panel first.")
        symbol, display = _resolve_commodity(conn, query)
        start = _resolve_start(conn, system, star_pos)

        stations = marketdb.stations_near(
            conn, start["x"], start["y"], start["z"], float(radius),
            min_updated=marketdb.now_epoch() - int(max_price_age_days) * 86400,
            require_large_pad=bool(requires_large_pad),
            max_dist_ls=float(max_system_distance) if max_system_distance else None,
        )
        by_id = {s["market_id"]: s for s in stations}
        if not by_id:
            return {"commodity": display, "results": []}

        condition = "supply >= ? AND buy_price > 0" if mode == "buy" else "demand >= ? AND sell_price > 0"
        rows = []
        for chunk in _chunks(by_id.keys()):
            marks = ",".join("?" for _ in chunk)
            rows.extend(conn.execute(
                f"""SELECT market_id, buy_price, sell_price, supply, demand
                    FROM commodities
                    WHERE symbol = ? AND market_id IN ({marks}) AND {condition}""",
                [symbol, *chunk, max(1, int(min_units))],
            ).fetchall())

        results = []
        for market_id, buy, sell, supply, demand in rows:
            st = by_id[market_id]
            results.append(
                {
                    "station": st["station"],
                    "system": st["system"],
                    "type": st["type"],
                    "distance": round(_dist(start, st), 1),
                    "dist_ls": st["dist_ls"],
                    "large_pad": st["large_pad"],
                    "buy_price": buy,
                    "sell_price": sell,
                    "supply": supply,
                    "demand": demand,
                    "updated_at": st["updated_at"],
                }
            )
        results.sort(key=lambda r: r["buy_price"] if mode == "buy" else -r["sell_price"])
        return {"commodity": display, "symbol": symbol, "results": results[:limit]}
    finally:
        conn.close()


def sell_cargo(
    items,  # [{"symbol", "name", "count"}]
    system=None,
    star_pos=None,
    radius=50.0,
    max_price_age_days=30,
    requires_large_pad=False,
    limit=10,
):
    """Best places to sell the CURRENT cargo hold: stations ranked by total
    payout for everything they can absorb (capped by their demand)."""
    items = [i for i in items if i.get("symbol") and (i.get("count") or 0) > 0]
    if not items:
        raise RouteError("Cargo hold is empty.")
    conn = marketdb.connect()
    try:
        if not marketdb.status(conn)["ready"]:
            raise RouteError("Local market database is empty - build it from the Database tab first.")
        start = _resolve_start(conn, system, star_pos)
        stations = marketdb.stations_near(
            conn, start["x"], start["y"], start["z"], float(radius),
            min_updated=marketdb.now_epoch() - int(max_price_age_days) * 86400,
            require_large_pad=bool(requires_large_pad),
        )
        by_id = {s["market_id"]: s for s in stations}
        if not by_id:
            return []
        counts = {i["symbol"]: i["count"] for i in items}
        names = {i["symbol"]: i.get("name") or i["symbol"].title() for i in items}
        marks_s = ",".join("?" for _ in counts)
        rows = []
        for chunk in _chunks(by_id.keys()):
            marks_m = ",".join("?" for _ in chunk)
            rows.extend(conn.execute(
                f"""SELECT market_id, symbol, sell_price, demand FROM commodities
                    WHERE market_id IN ({marks_m}) AND symbol IN ({marks_s})
                      AND sell_price > 0 AND demand > 0""",
                [*chunk, *counts.keys()],
            ).fetchall())

        per_station = {}
        for market_id, symbol, sell, demand in rows:
            units = min(counts[symbol], demand)
            if units <= 0:
                continue
            entry = per_station.setdefault(market_id, {"total": 0, "items": []})
            entry["total"] += units * sell
            entry["items"].append(
                {"name": names[symbol], "units": units, "sell_price": sell,
                 "demand": demand, "payout": units * sell,
                 "partial": units < counts[symbol]}
            )
        results = []
        for market_id, entry in per_station.items():
            st = by_id[market_id]
            entry["items"].sort(key=lambda i: -i["payout"])
            results.append(
                {"station": st["station"], "system": st["system"],
                 "distance": round(_dist(start, st), 1), "dist_ls": st["dist_ls"],
                 "large_pad": st["large_pad"], "updated_at": st["updated_at"],
                 "total": entry["total"], "items": entry["items"]}
            )
        results.sort(key=lambda r: -r["total"])
        return results[:limit]
    finally:
        conn.close()


# Commodities you can mine, with the method that yields them. "core" = deep-core
# (seismic charges; high value per unit, slow), "laser" = surface laser mining
# (bulk, fast). Symbols are verified against the local DB's commodity_names.
MINEABLES = {
    "opal": "core", "lowtemperaturediamond": "core", "alexandrite": "core",
    "grandidierite": "core", "monazite": "core", "musgravite": "core",
    "serendibite": "core", "benitoite": "core", "rhodplumsite": "core",
    "bromellite": "core",
    "painite": "laser", "platinum": "laser", "osmium": "laser", "palladium": "laser",
    "gold": "laser", "silver": "laser", "tritium": "laser", "bertrandite": "laser",
    "indite": "laser", "gallite": "laser", "coltan": "laser", "samarium": "laser",
    "cobalt": "laser",
}


def mining_advisor(
    system=None,
    star_pos=None,
    radius=50.0,
    min_price=0,
    requires_large_pad=False,
    max_price_age_days=30,
    max_system_distance=None,
    limit=25,
):
    """What's worth mining right now near you: for each mineable commodity, the
    best-paying station within range, ranked by sell price. Answers both 'what
    should I go mine' and 'where do I sell it'."""
    conn = marketdb.connect()
    try:
        if not marketdb.status(conn)["ready"]:
            raise RouteError("Local market database is empty - build it from the Database tab first.")
        start = _resolve_start(conn, system, star_pos)
        stations = marketdb.stations_near(
            conn, start["x"], start["y"], start["z"], float(radius),
            min_updated=marketdb.now_epoch() - int(max_price_age_days) * 86400,
            require_large_pad=bool(requires_large_pad),
            max_dist_ls=float(max_system_distance) if max_system_distance else None,
        )
        by_id = {s["market_id"]: s for s in stations}
        if not by_id:
            return {"results": [], "start": start["system"]}

        marks_s = ",".join("?" for _ in MINEABLES)
        rows = []
        for chunk in _chunks(by_id.keys()):
            marks_m = ",".join("?" for _ in chunk)
            rows.extend(conn.execute(
                f"""SELECT market_id, symbol, sell_price, demand FROM commodities
                    WHERE market_id IN ({marks_m}) AND symbol IN ({marks_s})
                      AND sell_price > 0 AND demand > 0""",
                [*chunk, *MINEABLES.keys()],
            ).fetchall())
        names = marketdb.commodity_display_names(conn, list(MINEABLES.keys()))

        best = {}  # symbol -> best-paying station near you
        for market_id, symbol, sell, demand in rows:
            if sell < int(min_price):
                continue
            cur = best.get(symbol)
            if cur is None or sell > cur["sell_price"]:
                st = by_id[market_id]
                best[symbol] = {
                    "symbol": symbol,
                    "name": names.get(symbol, symbol.title()),
                    "method": MINEABLES[symbol],
                    "sell_price": sell,
                    "demand": demand,
                    "station": st["station"],
                    "system": st["system"],
                    "distance": round(_dist(start, st), 1),
                    "dist_ls": st["dist_ls"],
                    "large_pad": st["large_pad"],
                    "updated_at": st["updated_at"],
                }
        results = sorted(best.values(), key=lambda r: -r["sell_price"])
        return {"results": results[:limit], "start": start["system"]}
    finally:
        conn.close()


def _resolve_commodity(conn, query):
    q = (query or "").strip()
    if not q:
        raise RouteError("No commodity given.")
    row = conn.execute(
        "SELECT symbol, name FROM commodity_names WHERE symbol = ? COLLATE NOCASE OR name = ? COLLATE NOCASE",
        (q, q),
    ).fetchone()
    if not row:
        row = conn.execute(
            "SELECT symbol, name FROM commodity_names WHERE name LIKE ? COLLATE NOCASE ORDER BY LENGTH(name) LIMIT 1",
            (f"%{q}%",),
        ).fetchone()
    if not row:
        raise RouteError(f"Unknown commodity '{query}'.")
    return row[0], row[1]


def _format(conn, route):
    symbols = {c["symbol"] for hop in route["hops"] for c in hop["commodities"]}
    names = marketdb.commodity_display_names(conn, symbols)
    hops = []
    cumulative = 0
    for hop in route["hops"]:
        cumulative += hop["profit"]
        hops.append(
            {
                "from_system": hop["from"]["system"],
                "from_station": hop["from"]["station"],
                "to_system": hop["to"]["system"],
                "to_station": hop["to"]["station"],
                "to_dist_ls": hop["to"]["dist_ls"],
                "distance": hop["distance"],
                "profit": hop["profit"],
                "cumulative_profit": cumulative,
                "commodities": [
                    {
                        "name": names.get(c["symbol"], c["symbol"].title()),
                        "amount": c["amount"],
                        "buy_price": c["buy_price"],
                        "sell_price": c["sell_price"],
                        "profit": c["profit"],
                        "supply": c.get("supply"),
                        "demand": c.get("demand"),
                    }
                    for c in hop["commodities"]
                ],
            }
        )
    return hops
