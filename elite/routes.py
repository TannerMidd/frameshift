"""Local trade-route planner over the market database: the same feature the
Spansh/Inara planners provide, computed offline against EDDN-fresh prices.

Beam search: each hop is buy-at-source -> sell-at-destination; destinations
must lie within max_hop_distance ly of the source system. Cargo is filled
greedily with the most profitable commodities (respecting supply, demand and
capital), and the best few partial routes are extended each round."""

import math

from . import marketdb

BEAM_WIDTH = 6
DESTS_PER_HOP = 5
MAX_COMMODITIES_PER_HOP = 3
MAX_SOURCE_CANDIDATES = 25
PAIR_QUERY_LIMIT = 600


class RouteError(Exception):
    pass


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
                    _extend(conn, route, float(max_hop_distance), int(max_cargo), filters)
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


def _extend(conn, route, max_hop, max_cargo, filters):
    src = route["at"]
    dests = marketdb.stations_near(conn, src["x"], src["y"], src["z"], max_hop, **filters)
    dest_by_id = {
        d["market_id"]: d for d in dests
        if d["market_id"] != src["market_id"] and d["market_id"] not in route["seen"]
    }
    if not dest_by_id:
        return []

    marks = ",".join("?" for _ in dest_by_id)
    pairs = conn.execute(
        f"""SELECT cd.market_id, cs.symbol, cs.buy_price, cd.sell_price, cs.supply, cd.demand
            FROM commodities cs
            JOIN commodities cd ON cd.symbol = cs.symbol
            WHERE cs.market_id = ?
              AND cd.market_id IN ({marks})
              AND cs.supply > 0 AND cs.buy_price > 0
              AND cd.demand > 0 AND cd.sell_price > cs.buy_price
            ORDER BY (cd.sell_price - cs.buy_price) DESC
            LIMIT {PAIR_QUERY_LIMIT}""",
        [src["market_id"], *dest_by_id.keys()],
    ).fetchall()

    flows_by_dest = {}
    for market_id, symbol, buy, sell, supply, demand in pairs:
        flows_by_dest.setdefault(market_id, []).append((symbol, buy, sell, supply, demand))

    extensions = []
    for market_id, flows in flows_by_dest.items():
        load = _fill_cargo(flows, max_cargo, route["capital"])
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


def _fill_cargo(flows, max_cargo, capital):
    """Greedy fill by unit profit; flows are pre-sorted by the SQL query."""
    space, funds = max_cargo, capital
    load = []
    for symbol, buy, sell, supply, demand in flows:
        if space <= 0 or funds < buy:
            break
        if any(c["symbol"] == symbol for c in load):
            continue
        units = min(space, supply, demand, funds // buy)
        if units <= 0:
            continue
        load.append(
            {"symbol": symbol, "amount": units, "buy_price": buy, "sell_price": sell,
             "profit": units * (sell - buy)}
        )
        space -= units
        funds -= units * buy
        if len(load) >= MAX_COMMODITIES_PER_HOP:
            break
    return load


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

        marks = ",".join("?" for _ in by_id)
        condition = "supply >= ? AND buy_price > 0" if mode == "buy" else "demand >= ? AND sell_price > 0"
        rows = conn.execute(
            f"""SELECT market_id, buy_price, sell_price, supply, demand
                FROM commodities
                WHERE symbol = ? AND market_id IN ({marks}) AND {condition}""",
            [symbol, *by_id.keys(), max(1, int(min_units))],
        ).fetchall()

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
                    }
                    for c in hop["commodities"]
                ],
            }
        )
    return hops
