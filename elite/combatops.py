"""Combat and anti-xeno session tracking with local loadout readiness checks."""

from __future__ import annotations

import uuid

from .workflowdb import WorkflowStore, event_epoch_ms


WORKFLOW = "combat_ops"
JOURNAL_EVENTS = frozenset(
    {
        "Bounty",
        "CapShipBond",
        "Cargo",
        "Died",
        "Docked",
        "FactionKillBond",
        "FighterDestroyed",
        "HeatDamage",
        "HullDamage",
        "Loadout",
        "Materials",
        "PVPKill",
        "RedeemVoucher",
        "ShipTargeted",
        "Shutdown",
        "Synthesis",
        "UnderAttack",
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


def _contains(item: str, *needles: str) -> bool:
    return any(needle in item for needle in needles)


def _cargo_count(cargo, symbol: str) -> int:
    if isinstance(cargo, dict):
        row = cargo.get(symbol) or cargo.get(symbol.casefold())
        return _integer(row.get("count") if isinstance(row, dict) else row)
    total = 0
    for row in cargo or []:
        if _symbol(row.get("Name") or row.get("Type") or row.get("symbol")) == symbol:
            total += _integer(row.get("Count", row.get("count")))
    return total


def _raw_material_units(materials) -> int:
    raw = (materials or {}).get("Raw") or (materials or {}).get("raw") or {}
    if isinstance(raw, dict):
        return sum(
            _integer(item.get("count") if isinstance(item, dict) else item)
            for item in raw.values()
        )
    return sum(_integer(item.get("Count", item.get("count"))) for item in raw or [])


def ax_readiness(loadout: dict | None, materials=None, cargo=None) -> dict:
    """Inspect only facts present in Loadout/Materials/Cargo snapshots.

    The journal does not emit weapon shots, so ammunition is deliberately
    labelled as the latest Loadout observation rather than a live estimate.
    """
    modules = (loadout or {}).get("Modules") or []
    groups = {
        "ax_weapons": [],
        "flak": [],
        "xeno_scanners": [],
        "shutdown_neutralisers": [],
        "caustic_sinks": [],
        "heat_sinks": [],
        "repair_or_decon": [],
        "hull_reinforcement": [],
        "module_reinforcement": [],
    }
    ammo = []
    for module in modules:
        item = _symbol(module.get("Item"))
        if not item:
            continue
        installed = {
            "slot": module.get("Slot"),
            "item": item,
            "enabled": bool(module.get("On", True)),
            "health_pct": (
                round(float(module.get("Health")) * 100, 1)
                if module.get("Health") is not None else None
            ),
        }
        is_ax_weapon = _contains(
            item,
            "guardian_gauss",
            "guardian_shard",
            "guardian_plasma",
            "ax_multicannon",
            "ax_missile",
            "xenokinetic",
            "causticmissile",
            "advancedmissilerack",
            "guardian_nanite",
        ) or (
            item.endswith("_advanced")
            and _contains(item, "multicannon", "dumbfiremissilerack")
        )
        if is_ax_weapon:
            groups["ax_weapons"].append(installed)
        if "flakmortar" in item:
            groups["flak"].append(installed)
        if "xenoscanner" in item:
            groups["xeno_scanners"].append(installed)
        if _contains(
            item, "shutdownfieldneutral", "shutdown_field_neutral", "antiunknownshutdown"
        ):
            groups["shutdown_neutralisers"].append(installed)
        if "causticsink" in item:
            groups["caustic_sinks"].append(installed)
        if "heatsinklauncher" in item:
            groups["heat_sinks"].append(installed)
        if _contains(
            item, "dronecontrol_repair", "dronecontrol_decontamination", "multidronecontrol_xeno"
        ):
            groups["repair_or_decon"].append(installed)
        if "hullreinforcement" in item:
            groups["hull_reinforcement"].append(installed)
        if "modulereinforcement" in item:
            groups["module_reinforcement"].append(installed)
        if is_ax_weapon or "flakmortar" in item or "heatsinklauncher" in item or "causticsink" in item:
            clip = _integer(module.get("AmmoInClip"))
            hopper = _integer(module.get("AmmoInHopper"))
            ammo.append({**installed, "clip": clip, "hopper": hopper, "total": clip + hopper})

    limpets = _cargo_count(cargo, "drones")
    present = {name: bool(items) for name, items in groups.items()}
    score = (
        35 * present["ax_weapons"]
        + 15 * present["heat_sinks"]
        + 10 * present["xeno_scanners"]
        + 10 * present["flak"]
        + 10 * present["shutdown_neutralisers"]
        + 5 * present["hull_reinforcement"]
        + 5 * present["module_reinforcement"]
        + 5 * present["caustic_sinks"]
        + 3 * present["repair_or_decon"]
        + 2 * bool(limpets)
    )
    if not present["ax_weapons"]:
        level = "not_ax_equipped"
    elif not present["heat_sinks"]:
        level = "limited"
    elif present["flak"] and present["xeno_scanners"] and present["shutdown_neutralisers"]:
        level = "interceptor_tooling_present"
    else:
        level = "scout_or_support_ready"

    return {
        "level": level,
        "score": score,
        "ax_weapon_count": len(groups["ax_weapons"]),
        "checklist": present,
        "modules": groups,
        "ammo": {
            "observed_total": sum(row["total"] for row in ammo),
            "by_module": ammo,
            "precision": "latest Loadout snapshot; weapon firing is not journaled",
        },
        "cargo_limpets": limpets,
        "raw_material_units": _raw_material_units(materials),
    }


def _default_state() -> dict:
    return {
        "version": 1,
        "loadout": None,
        "cargo": {},
        "materials": {},
        "current_target": None,
        "synthesis_lifetime": {},
        "session": None,
    }


def _new_session(ts: int) -> dict:
    return {
        "session_key": f"{ts}-{uuid.uuid4().hex[:12]}",
        "active": True,
        "started_ts": ts,
        "last_event_ts": ts,
        "ended_ts": None,
        "end_reason": None,
        "kills": 0,
        "pvp_kills": 0,
        "ax_kills": 0,
        "ax_kills_by_type": {},
        "bounty_cr": 0,
        "bond_cr": 0,
        "redeemed_cr": 0,
        "damage_events": 0,
        "deaths": 0,
        "fighter_losses": 0,
        "synthesis": {},
        "synthesis_materials": {},
        "ammo_start": None,
        "ammo_latest": None,
    }


def _is_thargoid(*values) -> bool:
    combined = " ".join(str(value or "") for value in values).casefold()
    return "thargoid" in combined or "xeno" in combined


def _target(event: dict, ts: int | None = None) -> dict | None:
    if event.get("TargetLocked") is False:
        return None
    return {
        "ship": event.get("Ship_Localised") or event.get("Ship"),
        "pilot": event.get("PilotName_Localised") or event.get("PilotName"),
        "rank": event.get("PilotRank"),
        "faction": event.get("Faction"),
        "legal_status": event.get("LegalStatus"),
        "bounty_cr": event.get("Bounty"),
        "hull_pct": event.get("HullHealth"),
        "shield_pct": event.get("ShieldHealth"),
        "targeted_ts": ts,
        "is_thargoid": _is_thargoid(
            event.get("Faction"), event.get("Ship"), event.get("PilotName")
        ),
    }


def _ammo_total(loadout) -> int | None:
    if not loadout:
        return None
    return sum(
        _integer(module.get("AmmoInClip")) + _integer(module.get("AmmoInHopper"))
        for module in loadout.get("Modules") or []
        if module.get("AmmoInClip") is not None or module.get("AmmoInHopper") is not None
    )


def _cargo(event: dict) -> dict:
    result = {}
    for item in event.get("Inventory") or []:
        symbol = _symbol(item.get("Name") or item.get("Type"))
        if symbol:
            result[symbol] = {
                "name": item.get("Name_Localised") or symbol.replace("_", " ").title(),
                "count": _integer(item.get("Count")),
            }
    return result


def _materials(event: dict) -> dict:
    result = {"Raw": {}, "Manufactured": {}, "Encoded": {}}
    for category in result:
        for item in event.get(category) or []:
            symbol = _symbol(item.get("Name"))
            if symbol:
                result[category][symbol] = {
                    "name": item.get("Name_Localised") or item.get("Name") or symbol,
                    "count": _integer(item.get("Count")),
                }
    return result


def _present(state: dict) -> dict:
    session = state.get("session")
    result = None
    if session:
        started = session.get("started_ts") or 0
        stopped = session.get("ended_ts") or session.get("last_event_ts") or started
        result = {**session, "duration_s": round(max(0, stopped - started) / 1000)}
    return {
        "active": bool(session and session.get("active")),
        "session": result,
        "target": state.get("current_target"),
        "readiness": ax_readiness(state.get("loadout"), state.get("materials"), state.get("cargo")),
        "synthesis_lifetime": dict(state.get("synthesis_lifetime") or {}),
    }


class CombatTracker:
    """Journal reducer for combat, CZ, and AX sessions."""

    def __init__(self, commander_id: str | None = None):
        self.store = WorkflowStore(WORKFLOW, _default_state, commander_id)

    @staticmethod
    def _ensure_session(state: dict, ts: int) -> dict:
        session = state.get("session")
        if not session or not session.get("active"):
            session = _new_session(ts)
            ammo = _ammo_total(state.get("loadout"))
            session["ammo_start"] = ammo
            session["ammo_latest"] = ammo
            state["session"] = session
        session["last_event_ts"] = max(session.get("last_event_ts") or ts, ts)
        return session

    @staticmethod
    def _end_session(state: dict, ts: int, reason: str) -> bool:
        session = state.get("session")
        if not session or not session.get("active"):
            return False
        session.update(active=False, ended_ts=ts, last_event_ts=ts, end_reason=reason)
        return True

    def _reduce(self, state: dict, event: dict, ts: int) -> bool:
        kind = event["event"]
        if kind == "Loadout":
            state["loadout"] = event
            session = state.get("session")
            if session and session.get("active"):
                ammo = _ammo_total(event)
                session["ammo_latest"] = ammo
                if session.get("ammo_start") is None:
                    session["ammo_start"] = ammo
                session["last_event_ts"] = max(session.get("last_event_ts") or ts, ts)
            return True
        if kind == "Cargo":
            state["cargo"] = _cargo(event)
            return True
        if kind == "Materials":
            state["materials"] = _materials(event)
            return True
        if kind == "ShipTargeted":
            state["current_target"] = _target(event, ts)
            return True

        if kind in {"Docked", "Shutdown"}:
            return self._end_session(state, ts, kind.lower())
        if kind == "Died":
            session = state.get("session")
            if not session or not session.get("active"):
                return False
            session["deaths"] += 1
            return self._end_session(state, ts, "died")

        if kind == "Synthesis":
            name = _symbol(event.get("Name")) or "unknown"
            state["synthesis_lifetime"][name] = state["synthesis_lifetime"].get(name, 0) + 1
            session = state.get("session")
            combat_synth = _contains(name, "ammo", "weapon", "heatsink", "heat_sink", "chaff", "afm")
            if session and session.get("active"):
                session["last_event_ts"] = max(session.get("last_event_ts") or ts, ts)
                session["synthesis"][name] = session["synthesis"].get(name, 0) + 1
                for item in event.get("Materials") or []:
                    material = _symbol(item.get("Name"))
                    if material:
                        session["synthesis_materials"][material] = (
                            session["synthesis_materials"].get(material, 0)
                            + _integer(item.get("Count"))
                        )
            elif combat_synth:
                # Preparation is retained globally but does not fabricate a
                # combat session before a shot or attack occurred.
                pass
            return True

        starts = {
            "Bounty", "FactionKillBond", "CapShipBond", "PVPKill",
            "UnderAttack", "HeatDamage", "HullDamage", "FighterDestroyed",
        }
        if kind in starts:
            session = self._ensure_session(state, ts)
        else:
            session = state.get("session")
            if not session or not session.get("active"):
                return False
            session["last_event_ts"] = max(session.get("last_event_ts") or ts, ts)

        if kind in {"Bounty", "FactionKillBond"}:
            target = state.get("current_target") or {}
            victim = event.get("VictimFaction")
            is_ax = _is_thargoid(victim) or bool(target.get("is_thargoid"))
            session["kills"] += 1
            if kind == "Bounty":
                reward = event.get("TotalReward")
                if reward is None:
                    reward = sum(_integer(row.get("Reward")) for row in event.get("Rewards") or [])
                session["bounty_cr"] += _integer(reward)
            else:
                session["bond_cr"] += _integer(event.get("Reward"))
            if is_ax:
                session["ax_kills"] += 1
                ship = target.get("ship") or "Unknown Thargoid"
                session["ax_kills_by_type"][ship] = session["ax_kills_by_type"].get(ship, 0) + 1
            # A destroyed target must not classify a later kill if the game
            # does not happen to emit an intervening target-unlock event.
            state["current_target"] = None
        elif kind == "CapShipBond":
            session["bond_cr"] += _integer(event.get("Reward"))
        elif kind == "PVPKill":
            session["pvp_kills"] += 1
        elif kind in {"HeatDamage", "HullDamage"}:
            session["damage_events"] += 1
        elif kind == "FighterDestroyed":
            session["fighter_losses"] += 1
        elif kind == "RedeemVoucher":
            session["redeemed_cr"] += _integer(event.get("Amount"))
        return True

    def observe_event(self, event: dict, event_uid: str | None = None) -> dict:
        if not isinstance(event, dict) or event.get("event") not in JOURNAL_EVENTS:
            return self.snapshot()
        closing = event["event"] in {"Docked", "Shutdown", "Died"}
        state, _changed = self.store.apply_event(
            event, self._reduce, event_uid,
            archive=(lambda value: _present(value).get("session")) if closing else None,
        )
        return _present(state)

    def start(self, timestamp=None, force=False) -> dict:
        ts = event_epoch_ms(timestamp)
        if force and self.snapshot().get("active"):
            self.end("restarted", ts)

        def change(state):
            if state.get("session") and state["session"].get("active") and not force:
                return False
            state["session"] = _new_session(ts)
            ammo = _ammo_total(state.get("loadout"))
            state["session"].update(ammo_start=ammo, ammo_latest=ammo)
            return True

        state, _ = self.store.mutate(change)
        return _present(state)

    def end(self, reason="manual", timestamp=None) -> dict:
        ts = event_epoch_ms(timestamp)
        state, _changed = self.store.mutate(
            lambda state: self._end_session(state, ts, str(reason)),
            archive=lambda value: _present(value).get("session"),
        )
        return _present(state)

    def update_inputs(self, *, loadout=None, materials=None, cargo=None) -> dict:
        def change(state):
            changed = False
            for key, value in (("loadout", loadout), ("materials", materials), ("cargo", cargo)):
                if value is not None and state.get(key) != value:
                    state[key] = value
                    changed = True
            return changed

        state, _ = self.store.mutate(change)
        return _present(state)

    def snapshot(self) -> dict:
        return _present(self.store.load())

    def history(self, limit=20) -> list[dict]:
        return self.store.history(limit)
