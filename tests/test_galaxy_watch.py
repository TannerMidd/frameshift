"""Galaxy-wide system-name tripwire over the EDDN feed.

This runs inside the EDDN receive loop against every message on the relay, so
the properties under test are as much about what it *refuses* to do -- fire on
commander-authored names, fire twice, raise, or block -- as about firing.
"""

import json
import os
import sys
import tempfile
import threading
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_tmp = tempfile.TemporaryDirectory()
os.environ["ET_DATA_DIR"] = _tmp.name

from elite import galaxywatch  # noqa: E402

JOURNAL_SCHEMA = "https://eddn.edcd.io/schemas/journal/1"
COMMODITY_SCHEMA = "https://eddn.edcd.io/schemas/commodity/3"


def envelope(message, schema=JOURNAL_SCHEMA, header=None):
    return {
        "$schemaRef": schema,
        "header": header or {"gameversion": "4.1.0.0", "softwareName": "E:D Market Connector"},
        "message": message,
    }


def fresh(watch=("Raxxla",)):
    """Reset every latch and point the watch set at `watch`."""
    galaxywatch.reset()
    hits = []
    galaxywatch.subscribe(hits.append)
    galaxywatch._cached_watch = frozenset(name.casefold() for name in watch)
    galaxywatch._cached_display = tuple(watch)
    galaxywatch._cache_expires = float("inf")
    return hits


# --- fires on a genuine sighting --------------------------------------------
hits = fresh()
matched = galaxywatch.observe(envelope({"event": "FSDJump", "StarSystem": "Raxxla"}))
assert matched == "Raxxla", matched
assert len(hits) == 1, hits
assert hits[0]["system"] == "Raxxla"
assert hits[0]["event"] == "FSDJump"
assert hits[0]["schema"] == JOURNAL_SCHEMA

# Case is irrelevant: the watch set and the message are both casefolded.
hits = fresh()
assert galaxywatch.observe(envelope({"event": "Location", "StarSystem": "RAXXLA"})) == "RAXXLA"
assert len(hits) == 1, hits

# `System` (codexentry) and `systemName` (commodity) are system names too.
hits = fresh()
assert galaxywatch.observe(envelope({"event": "CodexEntry", "System": "Raxxla"})) == "Raxxla"
assert len(hits) == 1, hits

hits = fresh()
assert galaxywatch.observe(
    envelope({"systemName": "Raxxla", "stationName": "Jameson Memorial"}, COMMODITY_SCHEMA)
) == "Raxxla"
assert hits[0]["event"] == "market update", hits

# --- refuses commander-authored names ---------------------------------------
# Fleet carriers can be named anything at all. If station names were read, any
# player could fire this alert for everyone by renaming their carrier.
hits = fresh()
assert galaxywatch.observe(envelope({
    "event": "Docked", "StarSystem": "Shinrarta Dezhra", "StationName": "RAXXLA",
})) is None
assert galaxywatch.observe(
    envelope({"systemName": "Sol", "stationName": "Raxxla"}, COMMODITY_SCHEMA)) is None
assert galaxywatch.observe(envelope({
    "event": "Scan", "StarSystem": "Sol", "BodyName": "Raxxla", "Body": "Raxxla",
})) is None
assert hits == [], hits

# --- latches ----------------------------------------------------------------
# A real discovery puts the name on thousands of messages within minutes.
hits = fresh()
assert galaxywatch.observe(envelope({"event": "FSDJump", "StarSystem": "Raxxla"})) == "Raxxla"
for _ in range(50):
    assert galaxywatch.observe(envelope({"event": "FSDJump", "StarSystem": "Raxxla"})) is None
    assert galaxywatch.observe(envelope({"event": "Location", "StarSystem": "raxxla"})) is None
assert len(hits) == 1, hits

# --- near misses stay silent ------------------------------------------------
hits = fresh()
for message in (
    {"event": "FSDJump", "StarSystem": "Raxxlar"},
    {"event": "FSDJump", "StarSystem": "Sol"},
    {"event": "FSDJump", "StarSystem": ""},
    {"event": "FSDJump", "StarSystem": None},
    {"event": "FSDJump", "StarSystem": 42},
    {"event": "FSDJump"},
):
    assert galaxywatch.observe(envelope(message)) is None, message
assert hits == [], hits

# --- an empty watch list does nothing at all --------------------------------
hits = fresh(watch=())
assert galaxywatch.observe(envelope({"event": "FSDJump", "StarSystem": "Raxxla"})) is None
assert hits == []
assert galaxywatch.stats()["messages_checked"] == 0, galaxywatch.stats()

# --- malformed input never raises -------------------------------------------
hits = fresh()
for junk in (
    None, 42, "string", [], {}, {"message": None}, {"message": []}, {"message": "x"},
    {"message": {"StarSystem": ["Raxxla"]}}, {"message": {"StarSystem": {"n": "Raxxla"}}},
):
    assert galaxywatch.observe(junk) is None, junk
assert hits == [], hits

# --- a throwing sink cannot break the receive loop or starve other sinks ----
galaxywatch.reset()
galaxywatch._cached_watch = frozenset({"raxxla"})
galaxywatch._cache_expires = float("inf")
delivered = []


def explode(_hit):
    raise RuntimeError("sink failure")


galaxywatch.subscribe(explode)
galaxywatch.subscribe(delivered.append)
assert galaxywatch.observe(envelope({"event": "FSDJump", "StarSystem": "Raxxla"})) == "Raxxla"
assert len(delivered) == 1, delivered

# --- unsubscribe ------------------------------------------------------------
galaxywatch.reset()
galaxywatch._cached_watch = frozenset({"raxxla"})
galaxywatch._cache_expires = float("inf")
seen = []
unsubscribe = galaxywatch.subscribe(seen.append)
unsubscribe()
assert galaxywatch.observe(envelope({"event": "FSDJump", "StarSystem": "Raxxla"})) == "Raxxla"
assert seen == [], seen

# --- watch list validation --------------------------------------------------
# settings._normalise only proves a list-valued setting is a bounded list, so
# a hand-edited settings.json reaches this hot path unchecked.
from elite import settings  # noqa: E402

assert "Raxxla" in settings.DEFAULTS["galaxy_watch_systems"], settings.DEFAULTS

settings.update({"galaxy_watch_systems": [
    "Raxxla", "  Merope  ", "", "   ", None, 42, ["nested"], {"a": 1},
    "x" * 500, "raxxla", "Sol",
]})
watch = galaxywatch.watched_names(force=True)
assert watch == {"raxxla", "merope", "sol"}, watch
assert galaxywatch.stats()["watching"] == ["Raxxla", "Merope", "Sol"], galaxywatch.stats()

settings.update({"galaxy_watch_systems": [f"System {n}" for n in range(200)]})
assert len(galaxywatch.watched_names(force=True)) == galaxywatch._MAX_WATCHED

settings.update({"galaxy_watch_systems": []})
assert galaxywatch.watched_names(force=True) == frozenset()

settings.update({"galaxy_watch_systems": ["Raxxla"]})

# --- concurrent observers see exactly one fire ------------------------------
galaxywatch.reset()
galaxywatch._cached_watch = frozenset({"raxxla"})
galaxywatch._cache_expires = float("inf")
fires = []
galaxywatch.subscribe(fires.append)
barrier = threading.Barrier(8)


def hammer():
    barrier.wait()
    for _ in range(200):
        galaxywatch.observe(envelope({"event": "FSDJump", "StarSystem": "Raxxla"}))


threads = [threading.Thread(target=hammer) for _ in range(8)]
for thread in threads:
    thread.start()
for thread in threads:
    thread.join()
assert len(fires) == 1, fires
assert galaxywatch.stats()["messages_checked"] == 1600, galaxywatch.stats()
assert galaxywatch.stats()["fired"] == ["raxxla"], galaxywatch.stats()

# --- the EDDN listener hook -------------------------------------------------
# _handle must still ingest commodity messages unchanged, must feed the
# tripwire from non-commodity schemas it otherwise discards, and must skip
# Legacy-galaxy traffic (a frozen fork of the universe).
from elite.eddn import EddnListener  # noqa: E402

galaxywatch.reset()
galaxywatch._cached_watch = frozenset({"raxxla"})
galaxywatch._cache_expires = float("inf")
sightings = []
galaxywatch.subscribe(sightings.append)

listener = EddnListener()
packed = lambda payload: zlib.compress(json.dumps(payload).encode("utf-8"))  # noqa: E731

# A journal/1 message is dropped for market purposes but still watched.
listener._handle(None, packed(envelope({"event": "FSDJump", "StarSystem": "Raxxla"})))
assert len(sightings) == 1, sightings
assert listener.stats()["markets_updated"] == 0, listener.stats()

# Legacy-galaxy traffic is excluded.
galaxywatch.reset()
galaxywatch._cached_watch = frozenset({"raxxla"})
galaxywatch._cache_expires = float("inf")
legacy = []
galaxywatch.subscribe(legacy.append)
listener._handle(None, packed(envelope(
    {"event": "FSDJump", "StarSystem": "Raxxla"}, header={"gameversion": "3.8.0.1"})))
assert legacy == [], legacy

# Garbage never escapes _handle.
listener._handle(None, b"not-zlib")
listener._handle(None, zlib.compress(b"not-json"))
listener._handle(None, packed({"$schemaRef": JOURNAL_SCHEMA, "message": "wrong-type"}))

# --- the cockpit delivery path ----------------------------------------------
# Mirrors the subscription app.py installs at startup. Nothing else exercises
# that lambda, so a renamed push_alert argument would otherwise only surface on
# the day the tripwire finally fires.
from elite.state import AppState  # noqa: E402

galaxywatch.reset()
galaxywatch._cached_watch = frozenset({"raxxla"})
galaxywatch._cache_expires = float("inf")
state = AppState()
galaxywatch.subscribe(lambda hit: state.push_alert(
    "critical",
    "galaxy-watch",
    f"Galaxy watch. {hit['system']} has been reached.",
    f"GALAXY WATCH — {hit['system']} just appeared on the live EDDN feed ({hit['event']}).",
))
galaxywatch.observe(envelope({"event": "FSDJump", "StarSystem": "Raxxla"}))

alerts = state.snapshot()["alerts"]
assert len(alerts) == 1, alerts
assert alerts[0]["level"] == "critical", alerts
assert alerts[0]["code"] == "galaxy-watch", alerts
assert "Raxxla" in alerts[0]["say"], alerts
assert "Raxxla" in alerts[0]["text"] and "FSDJump" in alerts[0]["text"], alerts

# --- the health card surface ------------------------------------------------
from elite.diagnostics import health_snapshot  # noqa: E402

health = health_snapshot()
assert "galaxy_watch_error" not in health, health
assert health["galaxy_watch"]["fired"] == ["raxxla"], health["galaxy_watch"]
assert health["galaxy_watch"]["messages_checked"] >= 1, health["galaxy_watch"]

galaxywatch.reset()
_tmp.cleanup()
print("galaxy watch OK: fires once, ignores commander-authored names, survives junk and threads, "
      "EDDN hook leaves ingestion intact, cockpit alert and health card wired")
