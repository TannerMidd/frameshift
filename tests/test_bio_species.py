"""Species-level community biology.

The galaxy dump only records the *genus* a surface map reveals, so a body can
only ever be priced as a range from it. Spansh's body search additionally
returns `landmarks` -- the exact species commanders recorded after landing.
These tests pin the parsing of that payload and how it reaches the snapshot.
"""

import os
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_tmp = tempfile.TemporaryDirectory()
os.environ["ET_DATA_DIR"] = _tmp.name

from elite import biovalues, spansh  # noqa: E402
from elite.state import AppState  # noqa: E402


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def patched_post(payload, status_code=200, boom=None):
    def post(url, json=None, headers=None, timeout=None):
        if boom:
            raise boom
        return FakeResponse(payload, status_code)
    return post


# --- genus stems the body search returns ------------------------------------
# The search reports bare plural stems, not the codex token, and two of them
# differ from the singular forms the dump uses. A miss here silently drops the
# genus's value range.
for stem, expected in [
    ("Bacterial", "Bacterium"), ("Shrubs", "Frutexa"), ("Tussocks", "Tussock"),
    ("Cactoids", "Cactoida"), ("Anemones", "Anemone"), ("Conchas", "Concha"),
    ("Fungoids", "Fungoida"), ("Fonticulus", "Fonticulua"), ("Aleoids", "Aleoida"),
    ("Osseus", "Osseus"), ("Stratum", "Stratum"), ("Tubus", "Tubus"),
    ("Electricae", "Electricae"), ("Clypeus", "Clypeus"), ("Recepta", "Recepta"),
    ("Fumerolas", "Fumerola"),
]:
    got = biovalues.codex_genus_name(stem)
    assert got == expected, (stem, got, expected)
    assert got in biovalues.GENUS_VALUE_RANGE, f"{got} has no value range"
# The codex-token form must keep working -- the dump fallback still uses it.
assert biovalues.codex_genus_name("$Codex_Ent_Cactoid_Genus_Name;") == "Cactoida"

# --- landmark parsing -------------------------------------------------------
BODY = {
    "name": "Xinca 5 a",
    "signals": [{"name": "Biological", "count": 3}],
    "genuses": [{"name": "Conchas"}, {"name": "Bacterial"}],
    "landmarks": [
        {"type": "Concha", "subtype": "Concha Aureolas", "value": 7774700, "variant": "Grey"},
        {"type": "Concha", "subtype": "Concha Aureolas", "value": 7774700, "variant": "Grey"},
        {"type": "Bacterium", "subtype": "Bacterium Alcyoneum", "value": 1658500, "variant": "Lime"},
        # Geology and points of interest ride in the same list and must not be
        # mistaken for organics. "Fumarole" is geology; "Fumerola" is a genus.
        {"type": "Surface Station", "subtype": "Crater Outpost", "value": 0},
        {"type": "Geyser", "subtype": "Water Geyser", "value": 0},
        {"type": "Fumarole", "subtype": "Sulphur Dioxide Fumarole", "value": 0},
        {"type": "Crashed Ship", "subtype": "Crashed Ship", "value": 0},
        {"type": "Thargoid", "subtype": "Thargoid Barnacle", "value": 0},
        {"type": None, "subtype": "Nameless"},
        "not-a-dict",
    ],
}

spansh.requests.post = patched_post({"results": [BODY]})
out = spansh.system_bio("Xinca")
assert set(out) == {"Xinca 5 a"}, out
entry = out["Xinca 5 a"]
assert entry["count"] == 3, entry
assert [g["name"] for g in entry["genuses"]] == ["Concha", "Bacterium"], entry["genuses"]

species = entry["species"]
assert [s["name"] for s in species] == ["Concha Aureolas", "Bacterium Alcyoneum"], species
assert species[0]["value"] == 7774700
assert species[0]["variant"] == "Grey"
assert species[0]["sites"] == 2, "repeat landmarks are sample sites, not extra species"
assert species[0]["genus"] == "Concha"
assert species[0]["colony_m"] == biovalues.GENUS_COLONY_M["Concha"]
assert species[1]["sites"] == 1
# Sorted most valuable first, and the survey total counts organics only.
assert entry["species_value"] == 7774700 + 1658500, entry["species_value"]

# --- failure must be distinguishable from an empty system -------------------
# None means "ask the dump instead"; {} means "this system really has no bio".
spansh.requests.post = patched_post({"results": []})
assert spansh.system_bio("Djiwal") == {}, "empty system is a result, not a failure"

spansh.requests.post = patched_post(None, status_code=502)
assert spansh.system_bio("Xinca") is None, "HTTP error must report failure"

spansh.requests.post = patched_post(None)
assert spansh.system_bio("Xinca") is None, "unparseable body must report failure"

spansh.requests.post = patched_post({"results": "wrong-type"})
assert spansh.system_bio("Xinca") is None

spansh.requests.post = patched_post(None, boom=spansh.requests.RequestException("down"))
assert spansh.system_bio("Xinca") is None, "network error must report failure"

assert spansh.system_bio("") is None
assert spansh.system_bio(None) is None

# A body with a signal count but nothing sampled still appears, so the genus
# range remains the fallback rather than the row vanishing.
spansh.requests.post = patched_post({"results": [{
    "name": "Lonely 1", "signals": [{"name": "Biological", "count": 2}],
    "genuses": [{"name": "Bacterial"}], "landmarks": [],
}]})
lonely = spansh.system_bio("Lonely")["Lonely 1"]
assert lonely["species"] == []
assert lonely["species_value"] is None
assert [g["name"] for g in lonely["genuses"]] == ["Bacterium"]

# --- the snapshot merge -----------------------------------------------------
state = AppState()
state.system_address = 42
state.bio_community = {"id64": 42, "system": "Xinca", "bodies": {
    "Xinca 5 a": {
        "count": 3,
        "genuses": [biovalues.genus_info("Concha")],
        "species": [{"name": "Concha Aureolas", "genus": "Concha", "value": 7774700,
                     "variant": "Grey", "colony_m": 150, "sites": 2}],
        "species_value": 7774700,
    },
    "Xinca 5 b": {"count": 1, "genuses": [biovalues.genus_info("Bacterium")],
                  "species": [], "species_value": None},
}}
# One body you mapped yourself, one you have never even seen.
state.bio_signals = {"Xinca 5 a": {"body": "Xinca 5 a", "count": 3,
                                   "genuses": [biovalues.genus_info("Concha")]}}

rows = {r["body"]: r for r in state._bio_signals_snapshot()}
assert set(rows) == {"Xinca 5 a", "Xinca 5 b"}, rows

own = rows["Xinca 5 a"]
# Your own map gives the genus; species is strictly more, so it attaches even
# though you already have genus data of your own.
assert own["genuses"], "own genus data must survive"
assert [s["name"] for s in own["community_species"]] == ["Concha Aureolas"]
assert own["species_value"] == 7774700

unseen = rows["Xinca 5 b"]
assert unseen["source"] == "community"
assert unseen["community_species"] == []
assert [g["name"] for g in unseen["community_genuses"]] == ["Bacterium"]

# A stale community payload for a different system must never leak in.
state.system_address = 999
assert state._bio_signals_snapshot()[0].get("community_species") is None

# --- the journal-side fetch ------------------------------------------------
# _fetch_community_bio must prefer the species search and fall back to the dump
# only when it FAILS -- never when the system legitimately has no biology, or a
# quiet system would trigger a pointless second request every jump.
from elite.journal import JournalWatcher  # noqa: E402

calls = []


def make_watcher():
    watcher = JournalWatcher(AppState())
    watcher._live = True
    watcher.state.galaxy_mode = "live"
    watcher.state.system_address = 4408557439331
    watcher.state.system = "Xinca"
    return watcher


def run_fetch(bio_result, dump_result):
    calls.clear()

    def fake_bio(name):
        calls.append(("system_bio", name))
        return bio_result

    def fake_dump(id64):
        calls.append(("system_genuses", id64))
        return dump_result

    spansh.system_bio, spansh.system_genuses = fake_bio, fake_dump
    watcher = make_watcher()
    watcher._fetch_community_bio(4408557439331, "Xinca")
    for thread in threading.enumerate():
        if thread.name == "bio-community":
            thread.join(10)
    return watcher.state.bio_community


real_bio, real_dump = spansh.system_bio, spansh.system_genuses
try:
    # Species available: the dump is never asked.
    community = run_fetch({"Xinca 5 a": {"count": 1, "genuses": [], "species": [
        {"name": "Concha Aureolas", "genus": "Concha", "value": 7774700, "sites": 2}],
        "species_value": 7774700}}, {"never": "used"})
    assert [c[0] for c in calls] == ["system_bio"], calls
    assert community["bodies"]["Xinca 5 a"]["species"][0]["name"] == "Concha Aureolas", community

    # Genuinely empty system: still no dump call.
    community = run_fetch({}, {"never": "used"})
    assert [c[0] for c in calls] == ["system_bio"], calls
    assert community["bodies"] == {}, community

    # Search failed: fall back to the dump so genus ranges still show.
    community = run_fetch(None, {"Xinca 5 a": {"count": 1, "genuses": [
        biovalues.genus_info("Concha")]}})
    assert [c[0] for c in calls] == ["system_bio", "system_genuses"], calls
    assert community["bodies"]["Xinca 5 a"]["genuses"][0]["name"] == "Concha", community
finally:
    spansh.system_bio, spansh.system_genuses = real_bio, real_dump

_tmp.cleanup()
print("bio species OK: stem mapping, landmark filtering, failure vs empty, snapshot merge, "
      "journal fetch prefers species and falls back only on failure")
