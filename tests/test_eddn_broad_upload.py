"""Account-free EDDN builders: privacy, transforms, batching and fail-closed context."""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
_tmp = tempfile.TemporaryDirectory()
os.environ["ET_DATA_DIR"] = _tmp.name
os.environ["ET_EDDN_EXTENDED_UPLOAD"] = "1"

from elite import eddn_upload, settings  # noqa: E402


uploader = eddn_upload.EddnUploader()
header = uploader._header("SECRET CMDR NAME", "4.4.0.3", "r1 ")
assert header["uploaderID"].startswith("frameshift-")
assert "SECRET" not in header["uploaderID"]
assert header["uploaderID"] == uploader._header("SECRET CMDR NAME")["uploaderID"]
assert header["gamebuild"] == "r1 ", "Frontier build whitespace is significant"
assert uploader._header("TEST")["gameversion"] == ""
assert uploader._header("TEST")["gamebuild"] == ""

captured = []
uploader._queue_envelope = lambda schema, message, commander, gv, gb, key: captured.append(
    (schema, message, commander, gv, gb, key)
)
eddn_upload._fresh = lambda *a, **k: True

location = {
    "system": "Shinrarta Dezhra",
    "system_address": 3932277478106,
    "star_pos": [55.7, 17.6, 27.1],
    "body_name": "Shinrarta Dezhra A 1",
    "body_id": 7,
}

# Market consent does not silently opt a commander into the broader journal
# and snapshot contribution classes.
settings.update({"eddn_upload": True, "eddn_extended_upload": False})
assert eddn_upload.enabled() is True and eddn_upload.extended_enabled() is False
uploader.maybe_publish_journal({
    "timestamp": "2026-07-12T11:59:59Z", "event": "Location",
    "StarSystem": location["system"], "SystemAddress": location["system_address"],
}, "TEST", location)
assert captured == []
settings.update({"eddn_extended_upload": True})

# General journal schemas retain scientific data but remove localized/private
# fields recursively and use the trusted complete location tuple.
uploader.maybe_publish_journal({
    "timestamp": "2026-07-12T12:00:00Z",
    "event": "Location",
    "StationName": "Jameson Memorial",
    "Factions": [{
        "Name": "Pilots Federation", "MyReputation": 100,
        "FactionState_Localised": "None",
    }],
    "ActiveFine": 500,
    "Commander": "PRIVATE NAME",
    "FID": "F123456",
    "FutureUnknownField": "must stay local",
    "StarSystem": "Shinrarta Dezhra",
    "SystemAddress": 3932277478106,
}, "TEST", location, "4.4.0.3", "r1", horizons=True, odyssey=True)
schema, message, commander, gv, _gb, _key = captured.pop()
assert schema == "journal" and commander == "TEST" and gv == "4.4.0.3"
assert message["StarPos"] == location["star_pos"]
assert "ActiveFine" not in message and "MyReputation" not in message["Factions"][0]
assert not ({"Commander", "FID", "FutureUnknownField"} & set(message))
assert not any(key.endswith("_Localised") for key in message["Factions"][0])
assert message["horizons"] is True and message["odyssey"] is True

# A source/context mismatch is dropped instead of attaching stale coordinates.
uploader.maybe_publish_journal({
    "timestamp": "2026-07-12T12:00:01Z", "event": "Scan",
    "StarSystem": "Wrong System", "SystemAddress": 1, "BodyID": 2,
}, "TEST", location)
assert captured == []

# Dedicated strict schemas receive only their allowed keys. Codex personal
# progress is removed and its body pair comes only from the watcher cross-check.
uploader.maybe_publish_journal({
    "timestamp": "2026-07-12T12:00:02Z", "event": "CodexEntry",
    "System": "Shinrarta Dezhra", "SystemAddress": 3932277478106,
    "EntryID": 123, "Name": "$Codex_Ent_Test;", "IsNewEntry": True,
    "NewTraitsDiscovered": True, "BodyName": "untrusted", "BodyID": 99,
}, "TEST", location)
schema, message, *_ = captured.pop()
assert schema == "codexentry" and "StarSystem" not in message
assert "IsNewEntry" not in message and "NewTraitsDiscovered" not in message
assert message["BodyName"] == location["body_name"] and message["BodyID"] == 7

uploader.maybe_publish_journal({
    "timestamp": "2026-07-12T12:00:03Z", "event": "FSSAllBodiesFound",
    "SystemName": "Shinrarta Dezhra", "SystemAddress": 3932277478106, "Count": 9,
}, "TEST", location)
schema, message, *_ = captured.pop()
assert schema == "fssallbodiesfound" and "StarSystem" not in message
assert message["SystemName"] == location["system"] and message["StarPos"] == location["star_pos"]

uploader.maybe_publish_journal({
    "timestamp": "2026-07-12T12:00:04Z", "event": "DockingGranted",
    "MarketID": 128666762, "StationName": "Jameson Memorial", "LandingPad": 1,
}, "TEST", location)
schema, message, *_ = captured.pop()
assert schema == "dockinggranted"
assert not ({"StarSystem", "StarPos", "SystemAddress"} & set(message))

# ScanOrganic is intentionally not uploaded: it has no contract in the official
# EDCD/EDDN live repository used by the release's schema tests.
uploader.maybe_publish_journal({
    "timestamp": "2026-07-12T12:00:05Z", "event": "ScanOrganic",
    "ScanType": "Sample", "Body": 7,
}, "TEST", location)
assert captured == []

# FSS signals are coalesced until the next event, mission targets are omitted,
# and ephemeral/private fields never enter the batch.
for name, uss_type in (
    ("Non-Human Signal Source", "$USS_Type_NonHuman;"),
    ("Mission Target", "$USS_Type_MissionTarget;"),
):
    uploader.maybe_publish_journal({
        "timestamp": "2026-07-12T12:00:06Z", "event": "FSSSignalDiscovered",
        "SystemAddress": 3932277478106, "SignalName": name,
        "USSType": uss_type, "TimeRemaining": 500,
        "SignalName_Localised": "private/localized",
    }, "TEST", location, "4.4.0.3", "r1", horizons=True, odyssey=True)
assert captured == []
uploader.maybe_publish_journal({
    "timestamp": "2026-07-12T12:00:07Z", "event": "Music", "MusicTrack": "Exploration",
}, "TEST", location, "4.4.0.3", "r1", horizons=True, odyssey=True)
schema, message, *_ = captured.pop()
assert schema == "fsssignaldiscovered" and len(message["signals"]) == 1
signal = message["signals"][0]
assert "event" not in signal and "SystemAddress" not in signal and "TimeRemaining" not in signal

# Disabling contribution discards a pending batch instead of leaking it through
# the pre-location flush path.
uploader.maybe_publish_journal({
    "timestamp": "2026-07-12T12:00:07Z", "event": "FSSSignalDiscovered",
    "SystemAddress": 3932277478106, "SignalName": "Pending Carrier", "IsStation": True,
}, "TEST", location)
settings.update({"eddn_extended_upload": False})
uploader.flush_fss_signals(location, "TEST", preserve_unmatched=True)
settings.update({"eddn_extended_upload": True})
assert uploader._pending_signals == [] and captured == []

# Outfitting/Shipyard files are transformed into the v2 string arrays and
# commander-dependent modules are elided.
uploader.maybe_publish_snapshot("outfitting", {
    "timestamp": "2026-07-12T12:00:08Z", "event": "Outfitting",
    "MarketID": 128666762, "StationName": "Jameson Memorial",
    "StarSystem": "Shinrarta Dezhra",
    "Items": [
        {"Name": "Int_ShieldGenerator_Size5_Class5", "BuyPrice": 1},
        {"Name": "Hpt_PlasmaAccelerator_Fixed_Large", "SKU": "POWERPLAY"},
        {"Name": "Int_DetailedSurfaceScanner_Tiny", "SKU": "ELITE_HORIZONS_V_PLANETARY_LANDINGS"},
        {"Name": "PaintJob_Orange"},
        {"Name": "Int_PlanetApproachSuite", "SKU": "ELITE_HORIZONS_V_PLANETARY_LANDINGS"},
    ],
}, "TEST", horizons=True, odyssey=True)
schema, message, *_ = captured.pop()
assert schema == "outfitting" and set(message) <= {
    "systemName", "stationName", "marketId", "timestamp", "modules", "horizons", "odyssey",
}
assert message["modules"] == [
    "Int_ShieldGenerator_Size5_Class5", "Int_DetailedSurfaceScanner_Tiny",
]

uploader.maybe_publish_snapshot("shipyard", {
    "timestamp": "2026-07-12T12:00:09Z", "event": "Shipyard",
    "MarketID": 128666762, "StationName": "Jameson Memorial",
    "StarSystem": "Shinrarta Dezhra", "AllowCobraMkIV": False,
    "PriceList": [{"ShipType": "Anaconda", "ShipPrice": 1}],
}, "TEST")
schema, message, *_ = captured.pop()
assert schema == "shipyard" and message["ships"] == ["Anaconda"]
assert message["allowCobraMkIV"] is False and "PriceList" not in message

# Carrier material orders come from FCMaterials.json, not its journal signal.
uploader.maybe_publish_snapshot("fcmaterials", {
    "timestamp": "2026-07-12T12:00:10Z", "event": "FCMaterials",
    "MarketID": 3706117376, "CarrierName": "LOCAL CARRIER", "CarrierID": "ABC-123",
    "Items": [{
        "id": 1, "Name": "chemicalsample", "Name_Localised": "Chemical Sample",
        "Price": 100, "Stock": 3, "Demand": 0,
    }],
}, "TEST")
schema, message, *_ = captured.pop()
assert schema == "fcmaterials" and "Name_Localised" not in message["Items"][0]

print("broad EDDN upload OK: official shapes, privacy, context, batching, snapshots")
