"""Client for the Spansh trade-route planner API (async job: submit then poll)."""

import time

import requests

from . import biovalues
from .errors import UserFacingError

BASE = "https://spansh.co.uk/api"
HEADERS = {"User-Agent": "Frameshift/2.0 (personal ED companion app)"}
SUBMIT_TIMEOUT = 20
POLL_TIMEOUT = 20
MAX_WAIT_SECONDS = 90
BIO_SIGNAL = "$SAA_SignalType_Biological;"


def system_genuses(id64):
    """Community-mapped biological signals for a system, by id64, from the
    Spansh galaxy dump (GET /api/dump/<id64>). Returns
    {body_name: {"count": N, "genuses": [genus_info, ...]}} for bodies with a
    biological signal. Best-effort: returns {} on any failure."""
    if not id64:
        return {}
    try:
        resp = requests.get(f"{BASE}/dump/{int(id64)}", headers=HEADERS, timeout=SUBMIT_TIMEOUT)
        if resp.status_code != 200:
            return {}
        bodies = (resp.json().get("system") or {}).get("bodies") or []
    except (requests.RequestException, ValueError, TypeError):
        return {}
    out = {}
    for b in bodies:
        sig = b.get("signals") or {}
        count = (sig.get("signals") or {}).get(BIO_SIGNAL)
        raw = sig.get("genuses") or []
        if not count and not raw:
            continue
        genuses = []
        for g in raw:
            codex = g.get("name") if isinstance(g, dict) else g
            name = biovalues.codex_genus_name(codex)
            if name:
                genuses.append(biovalues.genus_info(name))
        out[b.get("name")] = {"count": count, "genuses": genuses}
    return out


class SpanshError(UserFacingError):
    pass


# Service chips the UI shows; the raw list runs to 25+ entries per station.
KEY_SERVICES = (
    "Market", "Outfitting", "Shipyard", "Material Trader", "Technology Broker",
    "Universal Cartographics", "Vista Genomics", "Interstellar Factors",
    "Black Market", "Refuel", "Repair", "Restock", "Search and Rescue",
)

_station_dump_cache = {}  # id64 -> (fetched_epoch, stations)
_STATION_DUMP_TTL = 600


def _parse_dump_stations(system):
    """Normalise a Spansh system dump into station-fact rows. Orbital stations
    sit at the system level; surface ports/settlements hang off bodies."""
    out = []

    def add(s, body=None):
        pads = s.get("landingPads") or {}
        market = s.get("market") or {}
        out.append({
            "market_id": s.get("id"),
            "station": s.get("name"),
            "type": s.get("type"),
            "body": body,
            "dist_ls": s.get("distanceToArrival"),
            "pads": {"l": pads.get("large", 0), "m": pads.get("medium", 0), "s": pads.get("small", 0)},
            "economy": s.get("primaryEconomy"),
            "government": s.get("government"),
            "faction": s.get("controllingFaction"),
            "allegiance": s.get("allegiance"),
            "services": [sv for sv in KEY_SERVICES if sv in (s.get("services") or [])],
            "has_market": bool(market.get("commodities")),
            "updated": market.get("updateTime") or s.get("updateTime"),
        })

    for s in system.get("stations") or []:
        add(s)
    for b in system.get("bodies") or []:
        for s in b.get("stations") or []:
            add(s, body=b.get("name"))
    # Real ports by arrival distance; fleet carriers sink to the bottom —
    # they are transient and can outnumber the actual stations in a busy
    # system, drowning the list a commander came to read.
    out.sort(key=lambda s: ("carrier" in (s["type"] or "").lower(),
                            s["dist_ls"] is None, s["dist_ls"] or 0))
    return out


def system_stations(id64):
    """Station facts for a system from the Spansh dump, cached briefly.
    Best-effort: returns [] when the system is unknown to Spansh."""
    if not id64:
        return []
    import time as _time

    cached = _station_dump_cache.get(id64)
    if cached and _time.time() - cached[0] < _STATION_DUMP_TTL:
        return cached[1]
    try:
        resp = requests.get(f"{BASE}/dump/{int(id64)}", headers=HEADERS, timeout=SUBMIT_TIMEOUT)
        if resp.status_code != 200:
            return []
        stations = _parse_dump_stations(resp.json().get("system") or {})
    except (requests.RequestException, ValueError, TypeError):
        return []
    _station_dump_cache[id64] = (_time.time(), stations)
    return stations


def plan_route(
    system,
    station=None,
    capital=100000,
    max_cargo=8,
    max_hop_distance=25.0,
    max_hops=4,
    max_system_distance=1000,
    max_price_age_days=30,
    requires_large_pad=False,
    allow_planetary=True,
    allow_prohibited=False,
    unique=False,
):
    if not system:
        raise SpanshError("No starting system known yet - is the game running?")

    payload = {
        "system": system,
        "capital": int(capital),
        "max_cargo": int(max_cargo),
        "max_hop_distance": float(max_hop_distance),
        "max_hops": int(max_hops),
        "max_system_distance": int(max_system_distance),
        "max_price_age": int(max_price_age_days) * 86400,
        "requires_large_pad": 1 if requires_large_pad else 0,
        "allow_planetary": 1 if allow_planetary else 0,
        "allow_prohibited": 1 if allow_prohibited else 0,
        "unique": 1 if unique else 0,
        "permit": 0,
    }
    if station:
        payload["station"] = station

    return _parse_result(submit_and_poll("trade/route", payload))


def submit_and_poll(path, payload):
    """Spansh's async job pattern: POST the form, poll /results/<job>."""
    try:
        resp = requests.post(
            f"{BASE}/{path}", data=payload, headers=HEADERS, timeout=SUBMIT_TIMEOUT
        )
    except requests.RequestException as exc:
        raise SpanshError(f"Could not reach Spansh: {exc}") from exc

    if resp.status_code >= 400:
        raise SpanshError(_error_text(resp))
    job = resp.json().get("job")
    if not job:
        raise SpanshError(f"Spansh did not return a job id: {resp.text[:200]}")

    deadline = time.monotonic() + MAX_WAIT_SECONDS
    while time.monotonic() < deadline:
        try:
            poll = requests.get(f"{BASE}/results/{job}", headers=HEADERS, timeout=POLL_TIMEOUT)
        except requests.RequestException as exc:
            raise SpanshError(f"Lost connection to Spansh: {exc}") from exc
        if poll.status_code >= 400:
            raise SpanshError(_error_text(poll))
        data = poll.json()
        status = data.get("status")
        if status == "ok":
            return data.get("result")
        if status in ("queued", "processing"):
            time.sleep(1.5)
            continue
        raise SpanshError(f"Spansh job failed: {data.get('error') or status}")
    raise SpanshError("Spansh took too long to compute a route; try again.")


def riches_route(
    from_system,
    to_system=None,
    jump_range=30.0,
    radius=50,
    max_results=30,
    max_distance=1000,
    min_value=300000,
    use_mapping_value=True,
    loop=True,
):
    """Road to Riches: high-value scan/mapping targets near your position."""
    payload = {
        "from": from_system,
        "to": to_system or from_system,
        "range": float(jump_range),
        "radius": int(radius),
        "max_results": int(max_results),
        "max_distance": int(max_distance),
        "min_value": int(min_value),
        "use_mapping_value": 1 if use_mapping_value else 0,
        "loop": 1 if loop else 0,
    }
    result = submit_and_poll("riches/route", payload)
    systems = []
    for hop in result if isinstance(result, list) else []:
        bodies = [
            {
                "name": b.get("name"),
                "type": b.get("subtype") or b.get("type"),
                "terraformable": bool(b.get("is_terraformable")),
                "dist_ls": b.get("distance_to_arrival"),
                "map_value": b.get("estimated_mapping_value"),
                "scan_value": b.get("estimated_scan_value"),
            }
            for b in hop.get("bodies") or []
        ]
        systems.append(
            {
                "system": hop.get("name") or hop.get("system_name"),
                "jumps": hop.get("jumps"),
                "bodies": bodies,
                "total_value": sum((b["map_value"] or b["scan_value"] or 0) for b in bodies),
            }
        )
    return systems


MODULE_RE = None  # compiled lazily


def _stations_search(body, coords=None):
    """POST to Spansh's station search. Spansh rejects reference systems it
    doesn't index (fresh discoveries, deep space), so known coordinates are
    retried as the reference when the named system bounces."""
    def post(payload):
        try:
            return requests.post(f"{BASE}/stations/search", json=payload, headers=HEADERS, timeout=SUBMIT_TIMEOUT)
        except requests.RequestException as exc:
            raise SpanshError(f"Could not reach Spansh: {exc}") from exc

    resp = post(body)
    if resp.status_code >= 400 and coords and len(coords) == 3:
        body = dict(body)
        body.pop("reference_system", None)
        body["reference_coords"] = {"x": coords[0], "y": coords[1], "z": coords[2]}
        resp = post(body)
    if resp.status_code >= 400:
        raise SpanshError(_error_text(resp))
    return resp.json().get("results") or []


def station_search(reference_system, module=None, ship=None, size=20, coords=None):
    """Nearest stations selling a module ('6A Fuel Scoop') or a ship."""
    global MODULE_RE
    import re

    if MODULE_RE is None:
        # (\S.*) rather than (.+): the name may not start with whitespace, which
        # removes the \s+/. ambiguity that made the match polynomial-time on
        # adversarial input (the query string comes off the LAN API).
        MODULE_RE = re.compile(r"^(\d)\s*([A-EI])\s+(\S.*)$", re.IGNORECASE)
    filters = {}
    if module:
        m = MODULE_RE.match(module.strip())
        if m:
            filters["modules"] = [{"class": [m.group(1)], "rating": [m.group(2).upper()],
                                   "name": [m.group(3).strip().title()]}]
        else:
            filters["modules"] = [{"name": [module.strip().title()]}]
    elif ship:
        filters["ships"] = {"value": [ship.strip()]}
    else:
        raise SpanshError("Give a module or a ship to search for.")

    body = {
        "filters": filters,
        "sort": [{"distance": {"direction": "asc"}}],
        "size": int(size),
        "page": 0,
        "reference_system": reference_system,
    }
    results = _stations_search(body, coords)
    return [
        {
            "station": s.get("name"),
            "system": s.get("system_name"),
            "distance": round(s.get("distance") or 0, 1),
            "dist_ls": s.get("distance_to_arrival"),
            "type": s.get("type"),
            "large_pad": bool(s.get("has_large_pad")),
            "updated_at": s.get("outfitting_updated_at") or s.get("shipyard_updated_at") or s.get("updated_at"),
        }
        for s in results
    ]


def service_stations(reference_system, service, size=8, coords=None):
    """Nearest stations offering a dockable service ('Universal Cartographics',
    'Vista Genomics', ...). Fleet carriers can fit both and often sit far out
    in the black, so they're included and flagged — but they move, so treat
    their position as a lead, not a promise."""
    if not reference_system and not (coords and len(coords) == 3):
        raise SpanshError("No reference system known yet - is the game running?")
    body = {
        "filters": {"services": [{"name": [service]}]},
        "sort": [{"distance": {"direction": "asc"}}],
        "size": int(size),
        "page": 0,
        "reference_system": reference_system,
    }
    if not reference_system:
        body.pop("reference_system")
        body["reference_coords"] = {"x": coords[0], "y": coords[1], "z": coords[2]}
    return [
        {
            "station": s.get("name"),
            "system": s.get("system_name"),
            "distance": round(s.get("distance") or 0, 1),
            "dist_ls": s.get("distance_to_arrival"),
            "type": s.get("type"),
            "carrier": (s.get("type") or "") == "Drake-Class Carrier",
            "large_pad": bool(s.get("has_large_pad")),
            "updated_at": s.get("updated_at"),
        }
        for s in _stations_search(body, coords)
    ]


def material_traders(reference_system, kind, size=8, coords=None):
    """Nearest material traders of one kind ('Raw'|'Manufactured'|'Encoded')."""
    body = {
        "filters": {"material_trader": {"value": [kind.title()]}},
        "sort": [{"distance": {"direction": "asc"}}],
        "size": int(size),
        "page": 0,
        "reference_system": reference_system,
    }
    return [
        {
            "station": s.get("name"),
            "system": s.get("system_name"),
            "distance": round(s.get("distance") or 0, 1),
            "dist_ls": s.get("distance_to_arrival"),
            "large_pad": bool(s.get("has_large_pad")),
        }
        for s in _stations_search(body, coords)
    ]


def neutron_route(from_system, to_system, jump_range, efficiency=60):
    """Neutron highway plot: waypoint list for long-distance travel."""
    payload = {
        "from": from_system,
        "to": to_system,
        "range": float(jump_range),
        "efficiency": int(efficiency),
    }
    result = submit_and_poll("route", payload)
    jumps = result.get("system_jumps") if isinstance(result, dict) else None
    if not jumps:
        raise SpanshError("Spansh returned no route (unreachable or unknown system?).")
    return {
        "total_jumps": result.get("total_jumps"),
        "waypoints": [
            {
                "system": j.get("system"),
                "distance_jumped": j.get("distance_jumped"),
                "distance_left": j.get("distance_left"),
                "neutron": bool(j.get("neutron_star")),
                "jumps": j.get("jumps"),
            }
            for j in jumps
        ],
    }


def mining_hotspots(reference_system, mineral, size=15):
    """Nearest ring hotspots for a mineral, via Spansh's bodies search. `mineral`
    is the in-game display name (e.g. 'Void Opal', 'Low Temperature Diamonds')."""
    if not reference_system:
        raise SpanshError("No reference system known yet - is the game running?")
    if not mineral:
        raise SpanshError("No mineral given.")
    body = {
        "filters": {"ring_signals": [{"name": mineral, "value": [1, 50]}]},
        "sort": [{"distance": {"direction": "asc"}}],
        "size": int(size),
        "page": 0,
        "reference_system": reference_system,
    }
    try:
        resp = requests.post(f"{BASE}/bodies/search", json=body, headers=HEADERS, timeout=SUBMIT_TIMEOUT)
    except requests.RequestException as exc:
        raise SpanshError(f"Could not reach Spansh: {exc}") from exc
    if resp.status_code >= 400:
        raise SpanshError(_error_text(resp))
    results = resp.json().get("results") or []
    target = mineral.lower()
    out = []
    for b in results:
        for ring in b.get("rings") or []:
            sig = ring.get("signals")
            signals = sig.get("signals") if isinstance(sig, dict) else sig
            hit = next((s for s in (signals or [])
                        if (s.get("name") or "").lower() == target), None)
            if not hit:
                continue
            out.append({
                "system": b.get("system_name"),
                "body": b.get("name"),
                "ring": ring.get("name"),
                "ring_type": ring.get("type"),
                "count": hit.get("count"),
                "distance": round(b.get("distance") or 0, 1),
                "dist_ls": b.get("distance_to_arrival"),
                "reserve": b.get("reserve_level"),
            })
    out.sort(key=lambda r: (r["distance"], -(r["count"] or 0)))
    return out


EXOBIO_PAGE = 75
EXOBIO_MAX_PAGES = 8  # ~600 nearest bio bodies; pages out well past a 20 ly cap


def _exobio_page(reference_system, page):
    payload = {
        "filters": {
            "signals": [{"name": "Biological", "value": [1, 40]}],
            "is_landable": {"value": True},
        },
        "sort": [{"distance": {"direction": "asc"}}],
        "size": EXOBIO_PAGE,
        "page": page,
        "reference_system": reference_system,
    }
    try:
        resp = requests.post(f"{BASE}/bodies/search", json=payload, headers=HEADERS, timeout=SUBMIT_TIMEOUT)
    except requests.RequestException as exc:
        raise SpanshError(f"Could not reach Spansh: {exc}") from exc
    if resp.status_code >= 400:
        raise SpanshError(_error_text(resp))
    return resp.json().get("results") or []


def exobio_bodies(reference_system, max_gravity=0.5, min_value=1_000_000, max_systems=25, genera=None):
    """The live 'Billionaire's Boulevard': landable, low-gravity bodies with
    high-value biological signals, grouped by system and ordered by distance.

    Pages outward from the reference system as far as needed to gather enough
    matches, so it is not capped to a small radius, and always returns the
    nearest results — relaxing the value (then gravity) filter as a last resort
    so it never comes back empty when any landable bio exists nearby.

    `genera`, if given, restricts the route to bodies that host at least one of
    those genera (e.g. {"Stratum"}) — an explicit pick, so it is never relaxed:
    the value/gravity fallbacks still apply, but a non-matching genus is never
    substituted in. Returns (systems, relaxed) where `relaxed` names any filter
    that had to be dropped."""
    if not reference_system:
        raise SpanshError("No reference system known yet - is the game running?")

    want = {g for g in (genera or []) if g}

    def has_wanted_genus(b):
        return not want or any(g in want for g in b["genuses"])

    # Collect every fetched landable-bio body, grouped by system, keeping all
    # bodies so we can re-filter at different strictness without re-querying.
    systems = {}

    def qualifying_count():
        return sum(
            1 for s in systems.values()
            if any(b["value"] >= min_value and b["gravity"] <= max_gravity and has_wanted_genus(b)
                   for b in s["bodies"])
        )

    # A specific genus is sparser than "any bio", so scan further out for one.
    max_pages = min(EXOBIO_MAX_PAGES * 2, 16) if want else EXOBIO_MAX_PAGES
    for page in range(max_pages):
        results = _exobio_page(reference_system, page)
        if not results:
            break
        for b in results:
            grav = round(b.get("gravity") or 0, 2)
            value = b.get("landmark_value") or 0
            genuses = []
            for g in b.get("genuses") or []:
                nm = biovalues.codex_genus_name(g.get("name") if isinstance(g, dict) else g)
                if nm and nm not in genuses:
                    genuses.append(nm)
            name = b.get("system_name")
            entry = systems.setdefault(name, {
                "system": name,
                "distance": round(b.get("distance") or 0, 1),
                "bodies": [],
            })
            entry["bodies"].append({
                "body": b.get("name"), "gravity": grav,
                "dist_ls": b.get("distance_to_arrival"), "value": value,
                "subtype": b.get("subtype"), "genuses": genuses,
            })
        if qualifying_count() >= max_systems:
            break

    def build(min_v, max_g):
        out = []
        for s in systems.values():
            bodies = [b for b in s["bodies"]
                      if b["value"] >= min_v and b["gravity"] <= max_g and has_wanted_genus(b)]
            if not bodies:
                continue
            bodies.sort(key=lambda x: -x["value"])
            out.append({"system": s["system"], "distance": s["distance"],
                        "bodies": bodies, "value": sum(b["value"] for b in bodies)})
        return sorted(out, key=lambda s: s["distance"])[:max_systems]

    result = build(min_value, max_gravity)
    if result:
        return result, None
    # Nothing cleared the filters even paging out — relax value then gravity so
    # the pilot still gets the closest matching bio worlds. The genus pick (if
    # any) is kept intact throughout; only value/gravity are loosened.
    result = build(0, max_gravity)
    if result:
        return result, "value"
    return build(0, 9e9), "value and gravity"


def _error_text(resp):
    try:
        detail = resp.json().get("error")
    except ValueError:
        detail = None
    return f"Spansh error ({resp.status_code}): {detail or resp.text[:200]}"


def _parse_result(result):
    """Normalise Spansh's hop list into what the UI renders. Written defensively:
    unknown fields are dropped rather than crashing if the API shape drifts."""
    if not isinstance(result, list):
        raise SpanshError("Unexpected Spansh response shape (no route list).")
    hops = []
    for hop in result:
        source = hop.get("source") or {}
        dest = hop.get("destination") or {}
        commodities = [
            {
                "name": c.get("name"),
                "amount": c.get("amount"),
                "buy_price": (c.get("source_commodity") or {}).get("buy_price"),
                "sell_price": (c.get("destination_commodity") or {}).get("sell_price"),
                "profit": c.get("total_profit"),
                "supply": (c.get("source_commodity") or {}).get("supply"),
                "demand": (c.get("destination_commodity") or {}).get("demand"),
            }
            for c in hop.get("commodities") or []
        ]
        hops.append(
            {
                "from_system": source.get("system"),
                "from_station": source.get("station"),
                "to_system": dest.get("system"),
                "to_station": dest.get("station"),
                "to_dist_ls": dest.get("distance_to_arrival"),
                "distance": hop.get("distance"),
                "profit": hop.get("total_profit"),
                "cumulative_profit": hop.get("cumulative_profit"),
                "commodities": commodities,
            }
        )
    return hops
