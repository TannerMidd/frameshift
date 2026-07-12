"""Backend coverage for the account-free specialist workflow suite."""

import os
import sys
import tempfile
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
_tmp = tempfile.TemporaryDirectory()
os.environ["ET_DATA_DIR"] = _tmp.name

from elite import marketdb  # noqa: E402
from elite.carrierops import CarrierPlanner  # noqa: E402
from elite.combatops import CombatTracker  # noqa: E402
from elite.exobiology import ExobiologyMapper, surface_vector  # noqa: E402
from elite.mining import MiningTracker  # noqa: E402
from elite.specialists import EXPECTED_JOURNAL_EVENTS, SpecialistWorkflows  # noqa: E402


commander_id = marketdb.ensure_commander_profile("Specialist Test")


def event(second, kind, **fields):
    return {
        "timestamp": f"2026-07-12T12:00:{second:02d}Z",
        "event": kind,
        **fields,
    }


# ---------- mining: yield, prospector quality, and bounded sale attribution ----------

mining = MiningTracker(commander_id)
mining.observe_event(event(0, "Cargo", Inventory=[
    {"Name": "drones", "Name_Localised": "Limpet", "Count": 20},
]))
mining.observe_event(event(1, "BuyDrones", Count=10, BuyPrice=101, TotalCost=1010))
for second in (2, 3):
    mining.observe_event(event(second, "LaunchDrone", Type="Prospector"))
for second in (4, 5, 6):
    mining.observe_event(event(second, "LaunchDrone", Type="Collection"))
mining.observe_event(event(7, "ProspectedAsteroid", Materials=[
    {"Name": "Platinum", "Proportion": 31.5},
    {"Name": "Painite", "Proportion": 12.0},
], Content="$AsteroidMaterialContent_High;", Remaining=100.0))
mining.observe_event(event(8, "ProspectedAsteroid", Materials=[
    {"Name": "Platinum", "Proportion": 42.25},
], Content="$AsteroidMaterialContent_High;", Remaining=100.0,
    MotherlodeMaterial="Monazite"))
refined = event(9, "MiningRefined", Type="Platinum", Type_Localised="Platinum", Count=4)
mining.observe_event(refined, event_uid="refined-1")
mining.observe_event(refined, event_uid="refined-1")  # journal replay is idempotent
mining.observe_event(event(10, "Cargo", Inventory=[
    {"Name": "drones", "Name_Localised": "Limpet", "Count": 25},
    {"Name": "platinum", "Name_Localised": "Platinum", "Count": 4},
]))
# Ten tonnes sold, but only the four actually refined this run are attributed.
mine = mining.observe_event(event(
    11, "MarketSell", Type="Platinum", Type_Localised="Platinum",
    Count=10, SellPrice=200_000, TotalSale=2_000_000,
))["session"]
assert mine["refined_t"] == 4 and mine["attributed_revenue_cr"] == 800_000, mine
assert mine["limpets"]["estimated_used"] == 5, mine["limpets"]
assert mine["limpets"]["inventory_accounting"] == 5
assert mine["limpets"]["cash_net_cost_cr"] == 1010
assert mine["prospected_materials"][0]["name"] == "Platinum"
assert mine["prospected_materials"][0]["best_pct"] == 42.25
assert mine["tons_per_asteroid"] == 2.0
mining.observe_event(event(12, "Shutdown"))
assert not mining.snapshot()["active"] and len(mining.history()) == 1

print("mining OK: prospectors, cargo yield, limpet economics, replay-safe revenue")


# ---------- combat / AX: loadout facts, kills, bonds, ammo, synthesis ----------

combat = CombatTracker(commander_id)
loadout = event(13, "Loadout", Modules=[
    {"Slot": "HP1", "Item": "hpt_guardian_gausscannon_fixed_medium", "On": True,
     "AmmoInClip": 4, "AmmoInHopper": 80, "Health": 1.0},
    {"Slot": "HP2", "Item": "hpt_flakmortar_fixed_medium", "On": True,
     "AmmoInClip": 1, "AmmoInHopper": 32, "Health": 1.0},
    {"Slot": "U1", "Item": "hpt_xenoscanner_basic_tiny", "On": True, "Health": 1.0},
    {"Slot": "U2", "Item": "hpt_antiunknownshutdown_tiny_v2", "On": True, "Health": 1.0},
    {"Slot": "U3", "Item": "hpt_heatsinklauncher_turret_tiny", "On": True,
     "AmmoInClip": 1, "AmmoInHopper": 3, "Health": 1.0},
    {"Slot": "U4", "Item": "hpt_causticsinklauncher_turret_tiny", "On": True,
     "AmmoInClip": 1, "AmmoInHopper": 5, "Health": 1.0},
    {"Slot": "S1", "Item": "int_hullreinforcement_size5_class2", "On": True, "Health": 1.0},
    {"Slot": "S2", "Item": "int_modulereinforcement_size5_class2", "On": True, "Health": 1.0},
    {"Slot": "S3", "Item": "int_dronecontrol_repair_size5_class2", "On": True, "Health": 1.0},
])
combat.observe_event(loadout)
combat.observe_event(event(14, "Cargo", Inventory=[{"Name": "drones", "Count": 16}]))
ready = combat.snapshot()["readiness"]
assert ready["level"] == "interceptor_tooling_present" and ready["score"] == 100, ready
assert ready["ammo"]["observed_total"] == 127 and ready["cargo_limpets"] == 16

combat.observe_event(event(
    15, "ShipTargeted", TargetLocked=True, Ship="$ShipName_ThargoidInterceptor;",
    Ship_Localised="Cyclops", Faction="$faction_Thargoid;", HullHealth=1.0,
))
bounty = event(16, "Bounty", VictimFaction="$faction_Thargoid;", TotalReward=8_000_000)
combat.observe_event(bounty, event_uid="ax-kill-1")
combat.observe_event(bounty, event_uid="ax-kill-1")
combat.observe_event(event(
    17, "Synthesis", Name="Weapon_Ammo_Basic",
    Materials=[{"Name": "iron", "Count": 2}, {"Name": "nickel", "Count": 1}],
))
combat.observe_event(event(
    17, "ShipTargeted", TargetLocked=True, Ship="$ShipName_ThargoidInterceptor;",
    Ship_Localised="Cyclops", Faction="$faction_Thargoid;", HullHealth=1.0,
))
fight = combat.observe_event(event(
    18, "FactionKillBond", VictimFaction="$faction_Thargoid;", Reward=1_000_000,
))["session"]
assert fight["kills"] == 2 and fight["ax_kills"] == 2, fight
assert fight["ax_kills_by_type"] == {"Cyclops": 2}
assert fight["bounty_cr"] == 8_000_000 and fight["bond_cr"] == 1_000_000
assert fight["synthesis"] == {"weapon_ammo_basic": 1}
assert fight["synthesis_materials"] == {"iron": 2, "nickel": 1}
combat.observe_event(event(19, "Docked", StationName="Rescue Megaship"))
assert not combat.snapshot()["active"] and len(combat.history()) == 1

print("combat OK: AX readiness, observed ammo, synth use, replay-safe kills and bonds")


# ---------- fleet carrier: authoritative status + explicit planning inputs ----------

carrier = CarrierPlanner(commander_id)
carrier.observe_event(event(
    20, "CarrierStats", CarrierID=123, CarrierType="FleetCarrier", Callsign="ABC-123",
    Name="WAYWARD STAR", DockingAccess="all", AllowNotorious=False, FuelLevel=300,
    JumpRangeCurr=480.0, JumpRangeMax=500.0, PendingDecommission=False,
    Finance={"CarrierBalance": 1_000_000_000, "ReserveBalance": 120_000_000,
             "AvailableBalance": 880_000_000, "ReservePercent": 12},
    SpaceUsage={"TotalCapacity": 25000, "Crew": 5000, "Cargo": 1000,
                "CargoSpaceReserved": 100, "ShipPacks": 0, "ModulePacks": 0,
                "FreeSpace": 18900},
    Crew=[{"CrewRole": "Refuel", "CrewName": "Ada", "Activated": True, "Enabled": True}],
    ShipPacks=[], ModulePacks=[],
))
status = carrier.configure_upkeep(10_000_000, target_weeks=8)
assert status["upkeep"]["reserve_weeks"] == 12.0
assert status["upkeep"]["target_shortfall_cr"] == 0
carrier.set_inventory({"tritium": {"name": "Tritium", "count": 100}})
route = carrier.plan_route(
    [
        {"system": "A", "distance_ly": 450},
        {"system": "B", "distance_ly": 520},
    ],
    tritium_per_jump_t=50,
    reserve_t=50,
)["route"]
assert route["tritium_required_t"] == 100 and route["available_t"] == 400, route
assert not route["valid"] and route["issues"][0]["leg"] == 2
route = carrier.plan_route(
    [{"system": "A", "distance_ly": 450, "tritium_t": 70}], reserve_t=50
)["route"]
assert route["valid"] and route["tritium_source"] == "per-leg input"

carrier.observe_event(event(
    21, "CarrierTradeOrder", CarrierID=123, BlackMarket=False,
    Commodity="Tritium", Commodity_Localised="Tritium", PurchaseOrder=100,
    Price=50_000,
))
assert carrier.snapshot()["orders"]["buy_order_exposure_cr"] == 5_000_000
transfer = event(22, "CargoTransfer", Transfers=[
    {"Type": "tritium", "Type_Localised": "Tritium", "Count": 25, "Direction": "tocarrier"},
])
carrier.observe_event(transfer, event_uid="cargo-transfer")  # no owner context: ignored
assert carrier.snapshot()["inventory"]["tritium"]["count"] == 100
carrier.observe_event(transfer, event_uid="cargo-transfer", context={"at_own_carrier": True})
assert carrier.snapshot()["inventory"]["tritium"]["count"] == 125
carrier.observe_event(event(
    23, "CarrierJumpRequest", CarrierID=123, SystemName="Moultac",
    SystemAddress=55, Body="Moultac 2", BodyID=4, DepartureTime="2026-07-12T13:00:00Z",
))
carrier.observe_event(event(24, "CarrierJump", StarSystem="Someone Else"))
assert carrier.snapshot()["pending_jump"]["system"] == "Moultac"
carrier.observe_event(event(25, "CarrierJump", StarSystem="Moultac", SystemAddress=55, Body="Moultac 2"))
assert carrier.snapshot()["pending_jump"] is None

print("carrier OK: reserve runway, orders, safe cargo attribution, explicit tritium route")


# ---------- exobiology: persistent sample pins and heading-relative map ----------

bio = ExobiologyMapper(commander_id)
bio.observe_event(event(26, "Location", StarSystem="Test System", SystemAddress=999))
bio.observe_event(event(
    27, "Scan", BodyName="Test System 2 a", BodyID=7, Radius=1_000_000,
))
bio.update_position({
    "lat": 0.0, "lon": 0.0, "body": "Test System 2 a",
    "radius_m": 1_000_000, "heading": 90,
})
base = {
    "Body": 7,
    "Genus": "$Codex_Ent_Bacterial_Genus_Name;",
    "Genus_Localised": "Bacterium",
    "Species": "$Codex_Ent_Bacterial_01_Name;",
    "Species_Localised": "Bacterium Aurasus",
    "Variant_Localised": "Bacterium Aurasus - Teal",
}
bio.observe_event(event(28, "ScanOrganic", ScanType="Log", **base))
bio.update_position({
    "lat": 0.05, "lon": 0.0, "body": "Test System 2 a",
    "radius_m": 1_000_000, "heading": 90,
})
clearance = bio.snapshot()["sampling"]["clearance"]
assert clearance and clearance["clear"] is True, clearance
bio.observe_event(event(29, "ScanOrganic", ScanType="Sample", **base))
bio.update_position({
    "lat": 0.10, "lon": 0.0, "body": "Test System 2 a",
    "radius_m": 1_000_000, "heading": 90,
})
complete = bio.observe_event(event(30, "ScanOrganic", ScanType="Analyse", **base))
assert complete["sampling"] is None
surface_map = complete["current_map"]
assert len(surface_map["pins"]) == 3 and len(surface_map["completed"]) == 1
assert surface_map["pins"][0]["bearing_deg"] == 180.0
assert surface_map["pins"][0]["relative_bearing_deg"] == 90.0
bio.add_pin("Parked ship", kind="ship")
geo = bio.geojson()
assert geo["type"] == "FeatureCollection" and len(geo["features"]) == 4
assert geo["features"][0]["geometry"]["coordinates"] == [0.0, 0.0]
bio.observe_event(event(31, "Died"))
assert len(bio.snapshot()["current_map"]["pins"]) == 4  # the map is knowledge, not unsold data
reloaded = ExobiologyMapper(commander_id)
assert len(reloaded.snapshot()["current_map"]["pins"]) == 4

# Ordinary specialist polling is paged even when a long expedition has a large
# local survey archive. Explicit GeoJSON export remains complete.
def seed_large_archive(state):
    original = next(iter(state["surveys"].values()))
    original["pins"] = [
        {
            "id": f"pin-{index}", "kind": "waypoint", "label": f"Pin {index}",
            "lat": index / 10_000, "lon": 0.0, "heading": None, "alt_m": None,
            "timestamp": index, "source": "test", "metadata": {},
        }
        for index in range(600)
    ]
    for index in range(225):
        key = f"archive-{index}"
        state["surveys"][key] = {
            "key": key, "system": "Archive", "system_address": index,
            "body": f"Archive {index}", "body_id": index, "radius_m": 1_000_000,
            "signal_count": None, "genuses": [], "pins": [], "completed": {},
            "truncated_pins": 0, "updated_ts": index,
        }
    return True


reloaded.store.mutate(seed_large_archive)
paged = reloaded.snapshot(
    body="Test System 2 a", survey_page=2, survey_page_size=25,
    pin_page=2, pin_page_size=50,
)
assert paged["surveys_total"] == 226 and len(paged["surveys"]) == 25
assert paged["survey_page"] == 2 and paged["survey_pages"] == 10
assert paged["current_map"]["pins_total"] == 600
assert len(paged["current_map"]["pins"]) == 50
assert paged["current_map"]["pins"][0]["id"] == "pin-500"
assert len(reloaded.geojson("Test System 2 a")["features"]) == 600

vector = surface_vector(
    {"lat": 0, "lon": 0, "radius_m": 1_000_000},
    {"lat": 0, "lon": 0.1},
)
assert 1745 < vector["distance_m"] < 1746 and vector["bearing_deg"] == 90.0, vector

print("exobiology OK: sample progress, clearance, map vectors, GeoJSON, persistence")


# ---------- commander separation + one-call integration contract ----------

other_id = marketdb.ensure_commander_profile("Other Specialist", make_active=False)
assert MiningTracker(other_id).snapshot()["session"] is None
assert ExobiologyMapper(other_id).snapshot()["surveys"] == []
facade = SpecialistWorkflows(commander_id)
result = facade.observe_event(event(32, "UnderAttack"), event_uid="facade-under-attack")
assert result["dispatched"] == ["combat"] and result["snapshot"]["combat"]["active"]
assert "ProspectedAsteroid" in EXPECTED_JOURNAL_EVENTS["mining"]
assert "CarrierStats" in EXPECTED_JOURNAL_EVENTS["carrier"]

print("integration OK: per-commander storage and explicit journal event contract")
print("ALL SPECIALIST WORKFLOW TESTS PASSED")
