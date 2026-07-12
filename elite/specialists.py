"""Facade for the local mining, combat, carrier, and exobiology workflows."""

from __future__ import annotations

from .carrierops import CarrierPlanner, JOURNAL_EVENTS as CARRIER_EVENTS
from .combatops import CombatTracker, JOURNAL_EVENTS as COMBAT_EVENTS
from .exobiology import ExobiologyMapper, JOURNAL_EVENTS as EXOBIO_EVENTS
from .mining import MiningTracker, JOURNAL_EVENTS as MINING_EVENTS


EXPECTED_JOURNAL_EVENTS = {
    "mining": sorted(MINING_EVENTS),
    "combat": sorted(COMBAT_EVENTS),
    "carrier": sorted(CARRIER_EVENTS),
    "exobiology": sorted(EXOBIO_EVENTS),
}


class SpecialistWorkflows:
    """One integration point for a journal event and Status.json position."""

    def __init__(self, commander_id: str | None = None):
        self.mining = MiningTracker(commander_id)
        self.combat = CombatTracker(commander_id)
        self.carrier = CarrierPlanner(commander_id)
        self.exobiology = ExobiologyMapper(commander_id)

    def observe_event(self, event: dict, event_uid: str | None = None, *, context=None) -> dict:
        kind = event.get("event") if isinstance(event, dict) else None
        dispatched = []
        if kind in MINING_EVENTS:
            self.mining.observe_event(event, event_uid)
            dispatched.append("mining")
        if kind in COMBAT_EVENTS:
            self.combat.observe_event(event, event_uid)
            dispatched.append("combat")
        if kind in CARRIER_EVENTS:
            self.carrier.observe_event(event, event_uid, context=context)
            dispatched.append("carrier")
        if kind in EXOBIO_EVENTS:
            self.exobiology.observe_event(event, event_uid)
            dispatched.append("exobiology")
        return {"dispatched": dispatched, "snapshot": self.snapshot()}

    def update_status(self, position: dict | None) -> dict:
        self.exobiology.update_position(position)
        return self.snapshot()

    def update_readiness_inputs(self, *, loadout=None, materials=None, cargo=None) -> dict:
        self.combat.update_inputs(loadout=loadout, materials=materials, cargo=cargo)
        return self.snapshot()

    def snapshot(self, *, exobiology_options=None) -> dict:
        return {
            "mining": self.mining.snapshot(),
            "combat": self.combat.snapshot(),
            "carrier": self.carrier.snapshot(),
            "exobiology": self.exobiology.snapshot(**(exobiology_options or {})),
        }
