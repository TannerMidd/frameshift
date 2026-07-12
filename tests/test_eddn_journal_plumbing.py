"""Journal-side EDDN provenance: atomic context, flags, body cross-check and sidecars."""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
_tmp = tempfile.TemporaryDirectory()
os.environ["ET_DATA_DIR"] = _tmp.name
os.environ["ET_EDDN_EXTENDED_UPLOAD"] = "1"

from elite import eddn_upload  # noqa: E402
from elite.journal import JournalWatcher  # noqa: E402
from elite.state import AppState  # noqa: E402


eddn_upload._fresh = lambda *args, **kwargs: True
uploader = eddn_upload.EddnUploader()
captured = []
uploader._queue_envelope = lambda schema, message, commander, gv, gb, key: captured.append(
    (schema, message, commander, gv, gb, key)
)
eddn_upload.UPLOADER = uploader

state = AppState()
watcher = JournalWatcher(state, journal_dir=Path(_tmp.name))
watcher._live = True
watcher._activate_commander = lambda name: None
watcher._fetch_community_bio = lambda *args: None

watcher.handle_event({
    "timestamp": "2026-07-12T11:59:50Z", "event": "Fileheader",
    "gameversion": "4.4.0.3", "build": "r330683/r0 ", "Odyssey": True,
})
assert state.game_build == "r330683/r0 "
assert state.horizons is None and state.odyssey is None

watcher.handle_event({
    "timestamp": "2026-07-12T11:59:51Z", "event": "LoadGame",
    "Commander": "PRIVATE CMDR", "Horizons": True, "Odyssey": True,
})
assert state.horizons is True and state.odyssey is True

location_event = {
    "timestamp": "2026-07-12T12:00:00Z", "event": "Location",
    "StarSystem": "Shinrarta Dezhra", "SystemAddress": 3932277478106,
    "StarPos": [55.71875, 17.59375, 27.15625], "Body": "Shinrarta Dezhra A 1",
    "BodyID": 7, "Docked": False,
}
watcher.handle_event(location_event)
assert captured.pop()[0] == "journal"

# Docked can update display state without a position. The uploader must retain
# the prior complete trusted tuple, detect the mismatch, and drop this event.
watcher.handle_event({
    "timestamp": "2026-07-12T12:00:01Z", "event": "Docked",
    "StarSystem": "Untrusted New System", "SystemAddress": 99,
    "StationName": "Unknown Port", "MarketID": 123,
})
assert state.system == "Untrusted New System"
assert watcher._eddn_location["system"] == "Shinrarta Dezhra"
assert captured == []

# A strict event that agrees with the trusted tuple remains publishable and
# receives known LoadGame flags plus unmodified Fileheader source metadata.
watcher.handle_event({
    "timestamp": "2026-07-12T12:00:02Z", "event": "FSSBodySignals",
    "SystemAddress": 3932277478106, "BodyID": 7,
    "Signals": [{"Type": "$SAA_SignalType_Biological;", "Count": 1}],
})
schema, message, _cmdr, game_version, game_build, _key = captured.pop()
assert schema == "fssbodysignals"
assert message["StarSystem"] == "Shinrarta Dezhra"
assert message["odyssey"] is True and message["horizons"] is True
assert game_version == "4.4.0.3" and game_build == "r330683/r0 "

# Codex BodyID is supplied only after the independent Status-vs-journal names
# agree. Raw Codex body values cannot override the matched pair.
watcher.handle_event({
    "timestamp": "2026-07-12T12:00:03Z", "event": "ApproachBody",
    "BodyName": "Shinrarta Dezhra A 1", "BodyID": 7,
})
watcher._apply_status({"BodyName": "Shinrarta Dezhra A 1"})
watcher.handle_event({
    "timestamp": "2026-07-12T12:00:04Z", "event": "CodexEntry",
    "System": "Shinrarta Dezhra", "SystemAddress": 3932277478106,
    "EntryID": 100, "BodyName": "bad", "BodyID": 999,
})
schema, message, *_ = captured.pop()
assert schema == "codexentry" and message["BodyName"] == "Shinrarta Dezhra A 1"
assert message["BodyID"] == 7

# A disagreement is conservative: neither body field leaves the machine.
watcher.handle_event({
    "timestamp": "2026-07-12T12:00:04Z", "event": "ApproachBody",
    "BodyName": "Shinrarta Dezhra A 2", "BodyID": 8,
})
watcher._apply_status({"BodyName": "Shinrarta Dezhra A 1"})
watcher.handle_event({
    "timestamp": "2026-07-12T12:00:04Z", "event": "CodexEntry",
    "System": "Shinrarta Dezhra", "SystemAddress": 3932277478106, "EntryID": 101,
})
schema, message, *_ = captured.pop()
assert schema == "codexentry" and "BodyName" not in message and "BodyID" not in message

# FCMaterials uses the authoritative JSON sidecar builder and never needs
# commander/account authentication.
watcher._apply_fcmaterials({
    "timestamp": "2026-07-12T12:00:05Z", "event": "FCMaterials",
    "MarketID": 3706117376, "CarrierName": "LOCAL CARRIER", "CarrierID": "ABC-123",
    "Items": [{"id": 1, "Name": "chemicalsample", "Price": 1, "Stock": 2, "Demand": 0}],
})
assert captured.pop()[0] == "fcmaterials"

# Both documented FSS journal orderings retain their final contiguous batch.
batch_uploader = eddn_upload.EddnUploader()
batch_captured = []
batch_uploader._queue_envelope = lambda schema, message, commander, gv, gb, key: batch_captured.append(
    (schema, message)
)
eddn_upload.UPLOADER = batch_uploader
state2 = AppState()
state2.update(
    commander="PRIVATE CMDR", game_version="4.4.0.3", game_build="r330683/r0 ",
    horizons=True, odyssey=True,
)
watcher2 = JournalWatcher(state2, journal_dir=Path(_tmp.name))
watcher2._live = True
watcher2._fetch_community_bio = lambda *args: None
old_location = {
    "timestamp": "2026-07-12T12:01:00Z", "event": "Location",
    "StarSystem": "Old System", "SystemAddress": 100,
    "StarPos": [1.0, 2.0, 3.0], "Docked": False,
}
watcher2.handle_event(old_location)
batch_captured.clear()

# Horizons ordering: signals follow Location and are flushed before FSDJump
# replaces the trusted tuple.
watcher2.handle_event({
    "timestamp": "2026-07-12T12:01:01Z", "event": "FSSSignalDiscovered",
    "SystemAddress": 100, "SignalName": "Old Carrier", "IsStation": True,
})
watcher2.handle_event({
    "timestamp": "2026-07-12T12:01:02Z", "event": "FSDJump",
    "StarSystem": "New System", "SystemAddress": 200, "StarPos": [4.0, 5.0, 6.0],
})
assert [row[0] for row in batch_captured] == ["fsssignaldiscovered", "journal"]
assert batch_captured[0][1]["SystemAddress"] == 100
assert watcher2._eddn_status_body_name is None
batch_captured.clear()

# Odyssey ordering: signals precede Location. The preflush preserves their
# address mismatch, then the post-handler flush publishes against the new tuple.
watcher2.handle_event({
    "timestamp": "2026-07-12T12:01:03Z", "event": "FSSSignalDiscovered",
    "SystemAddress": 300, "SignalName": "Future Carrier", "IsStation": True,
})
watcher2.handle_event({
    "timestamp": "2026-07-12T12:01:04Z", "event": "Location",
    "StarSystem": "Future System", "SystemAddress": 300,
    "StarPos": [7.0, 8.0, 9.0], "Docked": False,
})
assert [row[0] for row in batch_captured] == ["fsssignaldiscovered", "journal"]
assert batch_captured[0][1]["SystemAddress"] == 300

print("EDDN journal plumbing OK: trusted provenance, flags, Codex and FCMaterials")
