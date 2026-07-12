"""Validate every Frameshift EDDN envelope against official live schemas."""

import json
import os
import sys
import tempfile
from pathlib import Path

TEST_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TEST_DIR.parent))
sys.path.insert(0, str(TEST_DIR))
_tmp = tempfile.TemporaryDirectory()
os.environ["ET_DATA_DIR"] = _tmp.name
os.environ["ET_EDDN_EXTENDED_UPLOAD"] = "1"

from _eddn_schema_validator import validate  # noqa: E402
from elite import eddn_upload  # noqa: E402


SCHEMA_FILES = {
    "commodity": "commodity-v3.0.json",
    "journal": "journal-v1.0.json",
    "fssallbodiesfound": "fssallbodiesfound-v1.0.json",
    "fssbodysignals": "fssbodysignals-v1.0.json",
    "fssdiscoveryscan": "fssdiscoveryscan-v1.0.json",
    "fsssignaldiscovered": "fsssignaldiscovered-v1.0.json",
    "navbeaconscan": "navbeaconscan-v1.0.json",
    "scanbarycentre": "scanbarycentre-v1.0.json",
    "codexentry": "codexentry-v1.0.json",
    "approachsettlement": "approachsettlement-v1.0.json",
    "dockinggranted": "dockinggranted-v1.0.json",
    "dockingdenied": "dockingdenied-v1.0.json",
    "fcmaterials": "fcmaterials_journal-v1.0.json",
    "navroute": "navroute-v1.0.json",
    "outfitting": "outfitting-v2.0.json",
    "shipyard": "shipyard-v2.0.json",
}
FIXTURES = TEST_DIR / "fixtures" / "eddn_schemas"
schemas = {
    name: json.loads((FIXTURES / filename).read_text(encoding="utf-8"))
    for name, filename in SCHEMA_FILES.items()
}

assert set(eddn_upload.SCHEMAS) == set(schemas)
assert "scanorganic" not in eddn_upload.SCHEMAS

timestamp = "2026-07-12T12:00:00Z"
location = {
    "system": "Shinrarta Dezhra",
    "system_address": 3932277478106,
    "star_pos": [55.71875, 17.59375, 27.15625],
    "body_name": "Shinrarta Dezhra A 1",
    "body_id": 7,
}
header = eddn_upload.EddnUploader._header("PRIVATE CMDR", "4.4.0.3", "r330683/r0 ")
assert "PRIVATE CMDR" not in json.dumps(header)

envelopes = {}


def envelope(schema_name, message):
    assert message is not None, f"builder rejected canonical {schema_name} input"
    value = {
        "$schemaRef": eddn_upload.SCHEMAS[schema_name],
        "header": dict(header),
        "message": message,
    }
    validate(value, schemas[schema_name])
    envelopes[schema_name] = value


events = {
    "journal": {
        "timestamp": timestamp, "event": "Docked", "StationName": "Jameson Memorial",
        "StarSystem": location["system"], "SystemAddress": location["system_address"],
    },
    "fssallbodiesfound": {
        "timestamp": timestamp, "event": "FSSAllBodiesFound",
        "SystemName": location["system"], "SystemAddress": location["system_address"],
        "Count": 12,
    },
    "fssbodysignals": {
        "timestamp": timestamp, "event": "FSSBodySignals",
        "SystemAddress": location["system_address"], "BodyID": 7,
        "BodyName": "Shinrarta Dezhra A 1",
        "Signals": [{
            "Type": "$SAA_SignalType_Biological;", "Type_Localised": "Biological", "Count": 2,
        }],
    },
    "fssdiscoveryscan": {
        "timestamp": timestamp, "event": "FSSDiscoveryScan",
        "SystemName": location["system"], "SystemAddress": location["system_address"],
        "Progress": 1.0, "BodyCount": 12, "NonBodyCount": 3,
    },
    "navbeaconscan": {
        "timestamp": timestamp, "event": "NavBeaconScan",
        "SystemAddress": location["system_address"], "NumBodies": 12,
    },
    "scanbarycentre": {
        "timestamp": timestamp, "event": "ScanBaryCentre",
        "StarSystem": location["system"], "SystemAddress": location["system_address"],
        "BodyID": 4, "SemiMajorAxis": 1000000.0,
    },
    "codexentry": {
        "timestamp": timestamp, "event": "CodexEntry", "System": location["system"],
        "SystemAddress": location["system_address"], "EntryID": 12345,
        "Name": "$Codex_Ent_Bacterial_Genus_Name;", "Region": "$Codex_RegionName_18;",
        "Category": "$Codex_Category_Biology;", "IsNewEntry": True,
    },
    "approachsettlement": {
        "timestamp": timestamp, "event": "ApproachSettlement",
        "SystemAddress": location["system_address"], "Name": "Dav's Hope",
        "BodyID": 7, "BodyName": "Shinrarta Dezhra A 1",
        "Latitude": 1.25, "Longitude": 2.5,
        "StationFaction": {"Name": "Pilots Federation", "FactionState": "$FactionState_None;"},
        "StationEconomies": [{"Name": "$economy_HighTech;", "Proportion": 1.0}],
    },
    "dockinggranted": {
        "timestamp": timestamp, "event": "DockingGranted", "MarketID": 128666762,
        "StationName": "Jameson Memorial", "StationType": "Orbis", "LandingPad": 1,
    },
    "dockingdenied": {
        "timestamp": timestamp, "event": "DockingDenied", "MarketID": 128666762,
        "StationName": "Jameson Memorial", "StationType": "Orbis", "Reason": "NoSpace",
    },
}

for schema_name, event in events.items():
    message = eddn_upload._build_journal_message(
        schema_name, event, location, horizons=True, odyssey=True
    )
    envelope(schema_name, message)

snapshots = {
    "outfitting": {
        "timestamp": timestamp, "event": "Outfitting", "MarketID": 128666762,
        "StationName": "Jameson Memorial", "StarSystem": location["system"],
        "Items": [{"Name": "Int_ShieldGenerator_Size5_Class5", "BuyPrice": 100}],
    },
    "shipyard": {
        "timestamp": timestamp, "event": "Shipyard", "MarketID": 128666762,
        "StationName": "Jameson Memorial", "StarSystem": location["system"],
        "AllowCobraMkIV": False,
        "PriceList": [{"ShipType": "Anaconda", "ShipPrice": 146969450}],
    },
    "navroute": {
        "timestamp": timestamp, "event": "NavRoute", "Route": [{
            "StarSystem": location["system"], "SystemAddress": location["system_address"],
            "StarPos": location["star_pos"], "StarClass": "A",
        }],
    },
    "fcmaterials": {
        "timestamp": timestamp, "event": "FCMaterials", "MarketID": 3706117376,
        "CarrierName": "LOCAL CARRIER", "CarrierID": "ABC-123", "Items": [{
            "id": 1, "Name": "chemicalsample", "Price": 100, "Stock": 3, "Demand": 0,
        }],
    },
}
for schema_name, data in snapshots.items():
    message = eddn_upload._build_snapshot_message(
        schema_name, data, horizons=True, odyssey=True
    )
    envelope(schema_name, message)

# FSSSignalDiscovered has a batching contract, so exercise the public path.
eddn_upload._fresh = lambda *args, **kwargs: True
uploader = eddn_upload.EddnUploader()
batched = []
uploader._queue_envelope = lambda schema_name, message, commander, gv, gb, key: batched.append(
    (schema_name, message)
)
for signal_name in ("Fleet Carrier A", "Non-Human Signal Source"):
    uploader.maybe_publish_journal({
        "timestamp": timestamp, "event": "FSSSignalDiscovered",
        "SystemAddress": location["system_address"], "SignalName": signal_name,
        "IsStation": signal_name.startswith("Fleet"),
    }, "PRIVATE CMDR", location, "4.4.0.3", "r330683/r0 ", True, True)
uploader.maybe_publish_journal(
    {"timestamp": timestamp, "event": "Music", "MusicTrack": "Exploration"},
    "PRIVATE CMDR", location, "4.4.0.3", "r330683/r0 ", True, True,
)
schema_name, message = batched.pop()
assert schema_name == "fsssignaldiscovered"
envelope(schema_name, message)

# Commodity uses a background wrapper in production; call its synchronous
# builder target and intercept the completed envelope.
market_uploader = eddn_upload.EddnUploader()
market_output = []
market_uploader._publish_envelope = lambda schema_name, value: market_output.append(
    (schema_name, value)
)
market_uploader._publish({
    "timestamp": timestamp, "event": "Market", "MarketID": 128666762,
    "StationName": "Jameson Memorial", "StarSystem": location["system"],
    "Items": [{
        "Name": "$Gold_Name;", "Category": "$Metals;", "MeanPrice": 50000,
        "BuyPrice": 49000, "Stock": 1000, "StockBracket": 3,
        "SellPrice": 48000, "Demand": 0, "DemandBracket": 0,
    }],
}, "PRIVATE CMDR", "4.4.0.3", "r330683/r0 ", True, True)
schema_name, value = market_output.pop()
assert schema_name == "commodity"
assert value["message"]["commodities"][0]["name"] == "gold"
validate(value, schemas["commodity"])
envelopes["commodity"] = value

assert set(envelopes) == set(schemas)

# Negative controls prove the offline validator is enforcing the strict live
# contracts, not merely parsing the fixture files.
bad_docking = json.loads(json.dumps(envelopes["dockinggranted"]))
bad_docking["message"]["StarSystem"] = location["system"]
try:
    validate(bad_docking, schemas["dockinggranted"])
except AssertionError:
    pass
else:
    raise AssertionError("official docking schema accepted a forbidden context field")

bad_outfitting = json.loads(json.dumps(envelopes["outfitting"]))
bad_outfitting["message"]["modules"] = [{"Name": "Int_ShieldGenerator_Size5_Class5"}]
try:
    validate(bad_outfitting, schemas["outfitting"])
except AssertionError:
    pass
else:
    raise AssertionError("official outfitting schema accepted raw module objects")

print(f"EDDN official schema OK: {len(envelopes)} offline live-schema envelopes")
