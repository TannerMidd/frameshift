"""Client for the Spansh trade-route planner API (async job: submit then poll)."""

import time

import requests

BASE = "https://spansh.co.uk/api"
HEADERS = {"User-Agent": "EliteTrader/1.0 (personal ED companion app)"}
SUBMIT_TIMEOUT = 20
POLL_TIMEOUT = 20
MAX_WAIT_SECONDS = 90


class SpanshError(Exception):
    pass


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


def station_search(reference_system, module=None, ship=None, size=20):
    """Nearest stations selling a module ('6A Fuel Scoop') or a ship."""
    global MODULE_RE
    import re

    if MODULE_RE is None:
        MODULE_RE = re.compile(r"^(\d)\s*([A-EI])\s+(.+)$", re.IGNORECASE)
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
    try:
        resp = requests.post(f"{BASE}/stations/search", json=body, headers=HEADERS, timeout=SUBMIT_TIMEOUT)
    except requests.RequestException as exc:
        raise SpanshError(f"Could not reach Spansh: {exc}") from exc
    if resp.status_code >= 400:
        raise SpanshError(_error_text(resp))
    results = resp.json().get("results") or []
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
