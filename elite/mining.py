"""Local mining-session analytics driven entirely by journal events."""

from __future__ import annotations

import uuid

from .workflowdb import WorkflowStore, event_epoch_ms


WORKFLOW = "mining"
JOURNAL_EVENTS = frozenset(
    {
        "AsteroidCracked",
        "BuyDrones",
        "Cargo",
        "CollectCargo",
        "Died",
        "EjectCargo",
        "LaunchDrone",
        "MarketSell",
        "MiningRefined",
        "ProspectedAsteroid",
        "SellDrones",
        "Shutdown",
    }
)


def _symbol(value) -> str:
    text = str(value or "").strip().strip("$;").lower()
    return text[:-5] if text.endswith("_name") else text


def _integer(value, default=0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _number(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _inventory(event: dict) -> dict[str, dict]:
    result = {}
    for item in event.get("Inventory") or []:
        symbol = _symbol(item.get("Name") or item.get("Type"))
        if not symbol:
            continue
        result[symbol] = {
            "name": item.get("Name_Localised") or symbol.replace("_", " ").title(),
            "count": _integer(item.get("Count")),
            "stolen": _integer(item.get("Stolen")),
        }
    return result


def _default_state() -> dict:
    return {"version": 1, "last_cargo": {}, "session": None}


def _new_session(ts: int, event: dict | None = None, context: dict | None = None) -> dict:
    event, context = event or {}, context or {}
    return {
        "session_key": f"{ts}-{uuid.uuid4().hex[:12]}",
        "active": True,
        "started_ts": ts,
        "last_event_ts": ts,
        "ended_ts": None,
        "end_reason": None,
        "system": context.get("system") or event.get("StarSystem") or event.get("SystemName"),
        "body": context.get("body") or event.get("Body") or event.get("BodyName"),
        "ring": context.get("ring") or event.get("Ring"),
        "asteroids_prospected": 0,
        "asteroids_cracked": 0,
        "prospector_limpets": 0,
        "collector_limpets": 0,
        "other_limpets": 0,
        "limpets_bought": 0,
        "limpets_sold": 0,
        "limpet_buy_cost_cr": 0,
        "limpet_sale_cr": 0,
        "cargo_start": {},
        "cargo_current": {},
        "collected": {},
        "jettisoned": {},
        "refined": {},
        "prospected_materials": {},
        "motherlodes": {},
        "sales": {},
        "attributed_revenue_cr": 0,
    }


def _increment_named(bucket: dict, symbol: str, name: str | None, count: int) -> None:
    if not symbol or not count:
        return
    row = bucket.setdefault(symbol, {"name": name or symbol.replace("_", " ").title(), "count": 0})
    row["count"] += count
    if name:
        row["name"] = name


def _present(state: dict) -> dict:
    session = state.get("session")
    if not session:
        return {"active": False, "session": None, "last_cargo": state.get("last_cargo") or {}}

    refined = [
        {"symbol": symbol, **row}
        for symbol, row in session.get("refined", {}).items()
    ]
    refined.sort(key=lambda row: (-row["count"], row["name"]))
    refined_t = sum(row["count"] for row in refined)

    targets = []
    for symbol, row in session.get("prospected_materials", {}).items():
        sightings = row.get("sightings") or 0
        targets.append(
            {
                "symbol": symbol,
                "name": row.get("name") or symbol,
                "sightings": sightings,
                "best_pct": round(row.get("best_pct") or 0, 2),
                "average_pct": round((row.get("total_pct") or 0) / sightings, 2) if sightings else 0,
            }
        )
    targets.sort(key=lambda row: (-row["best_pct"], row["name"]))

    started = session.get("started_ts") or 0
    stopped = session.get("ended_ts") or session.get("last_event_ts") or started
    duration_s = max(0, (stopped - started) / 1000)
    hours = duration_s / 3600
    prospectors = max(
        session.get("asteroids_prospected") or 0,
        session.get("prospector_limpets") or 0,
    )
    deployed = prospectors + (session.get("collector_limpets") or 0) + (session.get("other_limpets") or 0)

    start_limpets = (session.get("cargo_start", {}).get("drones") or {}).get("count")
    current_limpets = (session.get("cargo_current", {}).get("drones") or {}).get("count")
    inventory_used = None
    if start_limpets is not None and current_limpets is not None:
        inventory_used = max(
            0,
            start_limpets + (session.get("limpets_bought") or 0)
            - (session.get("limpets_sold") or 0) - current_limpets,
        )
    used = max(deployed, inventory_used or 0)
    bought = session.get("limpets_bought") or 0
    average_buy = (session.get("limpet_buy_cost_cr") or 0) / bought if bought else None
    estimated_consumed_cost = round(used * average_buy) if average_buy is not None else None
    cash_net_cost = (session.get("limpet_buy_cost_cr") or 0) - (session.get("limpet_sale_cr") or 0)
    revenue = session.get("attributed_revenue_cr") or 0

    cargo_yield = []
    for row in refined:
        symbol = row["symbol"]
        start_count = (session.get("cargo_start", {}).get(symbol) or {}).get("count", 0)
        current_count = (session.get("cargo_current", {}).get(symbol) or {}).get("count", 0)
        cargo_yield.append({
            **row,
            "cargo_delta": current_count - start_count,
            "sold_t": (session.get("sales", {}).get(symbol) or {}).get("count", 0),
        })

    result = {
        **session,
        "duration_s": round(duration_s),
        "refined_t": refined_t,
        "refined": refined,
        "cargo_yield": cargo_yield,
        "prospected_materials": targets,
        "tons_per_hour": round(refined_t / hours, 2) if hours else None,
        "tons_per_asteroid": round(refined_t / prospectors, 2) if prospectors else None,
        "limpets": {
            "prospectors_used": prospectors,
            "collectors_launched": session.get("collector_limpets") or 0,
            "other_launched": session.get("other_limpets") or 0,
            "estimated_used": used,
            "inventory_accounting": inventory_used,
            "bought": bought,
            "sold": session.get("limpets_sold") or 0,
            "remaining": current_limpets,
            "cash_net_cost_cr": cash_net_cost,
            "estimated_consumed_cost_cr": estimated_consumed_cost,
            "cost_source": "observed purchase price" if average_buy is not None else "unknown",
            "limpets_per_tonne": round(used / refined_t, 2) if refined_t else None,
            "cost_per_tonne_cr": (
                round(estimated_consumed_cost / refined_t)
                if estimated_consumed_cost is not None and refined_t else None
            ),
        },
        "attributed_revenue_cr": revenue,
        "net_after_limpet_cash_cr": revenue - cash_net_cost,
    }
    result.pop("cargo_start", None)
    result.pop("cargo_current", None)
    return {
        "active": bool(session.get("active")),
        "session": result,
        "last_cargo": state.get("last_cargo") or {},
    }


class MiningTracker:
    """Durable reducer for a commander mining run.

    ``MarketSell`` revenue is only attributed up to the number of tonnes this
    session actually refined, avoiding accidental inclusion of traded cargo.
    """

    def __init__(self, commander_id: str | None = None):
        self.store = WorkflowStore(WORKFLOW, _default_state, commander_id)

    def start(self, *, timestamp=None, context: dict | None = None, force=False) -> dict:
        ts = event_epoch_ms(timestamp)
        if force and self.snapshot().get("active"):
            self.end("restarted", ts)

        def change(state):
            current = state.get("session")
            if current and current.get("active") and not force:
                return False
            session = _new_session(ts, context=context)
            session["cargo_start"] = dict(state.get("last_cargo") or {})
            session["cargo_current"] = dict(state.get("last_cargo") or {})
            state["session"] = session
            return True

        state, _ = self.store.mutate(change)
        return _present(state)

    @staticmethod
    def _ensure_session(state: dict, ts: int, event: dict) -> dict:
        session = state.get("session")
        if not session or not session.get("active"):
            session = _new_session(ts, event)
            session["cargo_start"] = dict(state.get("last_cargo") or {})
            session["cargo_current"] = dict(state.get("last_cargo") or {})
            state["session"] = session
        session["last_event_ts"] = max(session.get("last_event_ts") or ts, ts)
        return session

    def _reduce(self, state: dict, event: dict, ts: int) -> bool:
        kind = event["event"]
        if kind == "Cargo":
            inventory = _inventory(event)
            state["last_cargo"] = inventory
            session = state.get("session")
            if session and session.get("active"):
                session["cargo_current"] = inventory
                session["last_event_ts"] = max(session.get("last_event_ts") or ts, ts)
            return True

        session = state.get("session")
        if kind in {"Shutdown", "Died"}:
            if not session or not session.get("active"):
                return False
            session["active"] = False
            session["ended_ts"] = ts
            session["last_event_ts"] = ts
            session["end_reason"] = kind.lower()
            return True

        if kind == "LaunchDrone":
            drone_type = str(event.get("Type") or "").casefold()
            if drone_type not in {"prospector", "collection"} and not session:
                return False
            session = self._ensure_session(state, ts, event)
            if drone_type == "prospector":
                session["prospector_limpets"] += 1
            elif drone_type == "collection":
                session["collector_limpets"] += 1
            else:
                session["other_limpets"] += 1
            return True

        if kind in {"ProspectedAsteroid", "MiningRefined", "AsteroidCracked", "BuyDrones"}:
            session = self._ensure_session(state, ts, event)
        elif not session or not session.get("active"):
            return False
        else:
            session["last_event_ts"] = max(session.get("last_event_ts") or ts, ts)

        if kind == "BuyDrones":
            count = _integer(event.get("Count"))
            session["limpets_bought"] += count
            session["limpet_buy_cost_cr"] += _integer(
                event.get("TotalCost"), count * _integer(event.get("BuyPrice"))
            )
        elif kind == "SellDrones":
            count = _integer(event.get("Count"))
            session["limpets_sold"] += count
            session["limpet_sale_cr"] += _integer(
                event.get("TotalSale"), count * _integer(event.get("SellPrice"))
            )
        elif kind == "ProspectedAsteroid":
            session["asteroids_prospected"] += 1
            motherlode = _symbol(event.get("MotherlodeMaterial"))
            if motherlode:
                _increment_named(
                    session["motherlodes"], motherlode,
                    event.get("MotherlodeMaterial_Localised"), 1,
                )
            for item in event.get("Materials") or []:
                symbol = _symbol(item.get("Name"))
                if not symbol:
                    continue
                row = session["prospected_materials"].setdefault(
                    symbol,
                    {
                        "name": item.get("Name_Localised") or item.get("Name") or symbol,
                        "sightings": 0,
                        "total_pct": 0.0,
                        "best_pct": 0.0,
                    },
                )
                proportion = max(0.0, _number(item.get("Proportion")))
                row["sightings"] += 1
                row["total_pct"] += proportion
                row["best_pct"] = max(row["best_pct"], proportion)
        elif kind == "AsteroidCracked":
            session["asteroids_cracked"] += 1
        elif kind == "MiningRefined":
            symbol = _symbol(event.get("Type"))
            _increment_named(
                session["refined"], symbol,
                event.get("Type_Localised") or event.get("Type"),
                max(1, _integer(event.get("Count"), 1)),
            )
        elif kind == "CollectCargo":
            symbol = _symbol(event.get("Type"))
            _increment_named(
                session["collected"], symbol,
                event.get("Type_Localised") or event.get("Type"),
                max(1, _integer(event.get("Count"), 1)),
            )
        elif kind == "EjectCargo":
            symbol = _symbol(event.get("Type"))
            _increment_named(
                session["jettisoned"], symbol,
                event.get("Type_Localised") or event.get("Type"),
                max(1, _integer(event.get("Count"), 1)),
            )
        elif kind == "MarketSell":
            symbol = _symbol(event.get("Type"))
            refined = (session.get("refined", {}).get(symbol) or {}).get("count", 0)
            previous = (session.get("sales", {}).get(symbol) or {}).get("count", 0)
            attributable = min(max(0, refined - previous), max(0, _integer(event.get("Count"))))
            if attributable:
                sold_count = max(1, _integer(event.get("Count"), 1))
                total = _integer(
                    event.get("TotalSale"), sold_count * _integer(event.get("SellPrice"))
                )
                revenue = round(total * attributable / sold_count)
                _increment_named(
                    session["sales"], symbol,
                    event.get("Type_Localised") or event.get("Type"), attributable,
                )
                session["sales"][symbol]["revenue_cr"] = (
                    session["sales"][symbol].get("revenue_cr") or 0
                ) + revenue
                session["attributed_revenue_cr"] += revenue
        return True

    def observe_event(self, event: dict, event_uid: str | None = None) -> dict:
        if not isinstance(event, dict) or event.get("event") not in JOURNAL_EVENTS:
            return self.snapshot()
        closing = event["event"] in {"Shutdown", "Died"}
        state, _changed = self.store.apply_event(
            event, self._reduce, event_uid,
            archive=(lambda value: _present(value).get("session")) if closing else None,
        )
        return _present(state)

    def end(self, reason="manual", timestamp=None) -> dict:
        ts = event_epoch_ms(timestamp)

        def change(state):
            session = state.get("session")
            if not session or not session.get("active"):
                return False
            session.update(active=False, ended_ts=ts, last_event_ts=ts, end_reason=str(reason))
            return True

        state, _changed = self.store.mutate(
            change, archive=lambda value: _present(value).get("session")
        )
        return _present(state)

    def snapshot(self) -> dict:
        return _present(self.store.load())

    def history(self, limit=20) -> list[dict]:
        return self.store.history(limit)
