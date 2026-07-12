"""Regression coverage for chronological replay and atomic specialist close."""

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
_tmp = tempfile.TemporaryDirectory()
os.environ["ET_DATA_DIR"] = _tmp.name

from elite import marketdb  # noqa: E402
from elite import journal as journal_module  # noqa: E402
from elite.carrierops import CarrierPlanner  # noqa: E402
from elite.combatops import CombatTracker  # noqa: E402
from elite.exobiology import ExobiologyMapper  # noqa: E402
from elite.journal import BOOTSTRAP_MAX_FILES, JournalWatcher  # noqa: E402
from elite.mining import MiningTracker  # noqa: E402
from elite.state import AppState  # noqa: E402
from elite.timings import TimingModel  # noqa: E402
from elite.workflowdb import event_epoch_ms  # noqa: E402


def event(second, kind, **fields):
    return {
        "timestamp": f"2026-07-01T00:00:{second:02d}Z",
        "event": kind,
        **fields,
    }


def install_archive_failure(name):
    conn = marketdb.connect_user()
    conn.execute(
        f"CREATE TRIGGER {name} BEFORE INSERT ON specialist_history "
        "BEGIN SELECT RAISE(ABORT, 'simulated archive failure'); END"
    )
    conn.commit()
    conn.close()


def remove_archive_failure(name):
    conn = marketdb.connect_user()
    conn.execute(f"DROP TRIGGER {name}")
    conn.commit()
    conn.close()


commander_id = marketdb.ensure_commander_profile("Atomic Test")

# State close, processed-event marker, and archive must commit together. A
# failed archive leaves the active state and event retryable for both trackers.
mining = MiningTracker(commander_id)
mining.observe_event(event(1, "ProspectedAsteroid", Materials=[]), "mine-start")
install_archive_failure("fail_mining_archive")
try:
    mining.observe_event(event(2, "Shutdown"), "mine-close")
    raise AssertionError("mining close unexpectedly survived archive failure")
except sqlite3.IntegrityError:
    pass
assert mining.snapshot()["active"]
remove_archive_failure("fail_mining_archive")
mining.observe_event(event(2, "Shutdown"), "mine-close")
assert not mining.snapshot()["active"] and len(mining.history()) == 1

combat = CombatTracker(commander_id)
combat.observe_event(event(3, "UnderAttack"), "combat-start")
install_archive_failure("fail_combat_archive")
try:
    combat.observe_event(event(4, "Docked", StationName="Jameson Memorial"), "combat-close")
    raise AssertionError("combat close unexpectedly survived archive failure")
except sqlite3.IntegrityError:
    pass
assert combat.snapshot()["active"]
remove_archive_failure("fail_combat_archive")
combat.observe_event(event(4, "Docked", StationName="Jameson Memorial"), "combat-close")
assert not combat.snapshot()["active"] and len(combat.history()) == 1

print("specialist transactions OK: close/event/archive roll back and retry together")

# A missing repair marker alone never authorizes clearing a fresh pre-game
# session. Only the flawed history-v4 build triggers projection reconstruction.
fresh = MiningTracker("fresh-pregame")
fresh.start(context={"system": "Pre-game"})
JournalWatcher(AppState(), journal_dir=Path(_tmp.name) / "missing")._prepare_derived_history_replay()
assert fresh.snapshot()["active"], "fresh pre-game specialist state was cleared"


# Put the mining start farther back than the live bootstrap cap. The completed
# journal sweep must run first, or the recent Shutdown is seen before this
# start and startup incorrectly leaves an active session.
root = Path(_tmp.name) / "journals"
root.mkdir()
file_count = BOOTSTRAP_MAX_FILES + 6
for index in range(file_count):
    events = [
        {"timestamp": f"2026-07-{index + 1:02d}T12:00:00Z", "event": "Fileheader",
         "gameversion": "4.1"},
        {"timestamp": f"2026-07-{index + 1:02d}T12:00:01Z", "event": "Commander",
         "Name": "Chronology Test"},
    ]
    if index == 0:
        events.append({
            "timestamp": "2026-07-01T12:00:02Z", "event": "ProspectedAsteroid",
            "Materials": [{"Name": "Platinum", "Proportion": 42.0}],
        })
    if index == file_count - 2:
        events.append({
            "timestamp": f"2026-07-{index + 1:02d}T12:00:02Z", "event": "Shutdown",
        })
    path = root / f"Journal.2026-07-{index + 1:02d}T120000.01.log"
    path.write_text(
        "\n".join(json.dumps(row, separators=(",", ":")) for row in events) + "\n",
        encoding="utf-8",
    )

# Seed commander-authored inputs and history that journals cannot reproduce.
# These coexist with the corrupt recent-first v4 projection and must be staged
# before the repair clears specialist tables.
chronology_id = marketdb.ensure_commander_profile("Chronology Test")
carrier = CarrierPlanner(chronology_id)
carrier.configure_upkeep(25_000_000, target_weeks=10, source="commander input")
carrier.plan_route(
    [{"system": "Beagle Point", "distance_ly": 499.5}],
    tritium_per_jump_t=68, reserve_t=1_000,
)
carrier.set_inventory(
    {"tritium": {"name": "Tritium", "count": 777}},
    source="commander inventory input",
)

bio = ExobiologyMapper(chronology_id)
bio.observe_event({
    "timestamp": "2026-07-31T13:00:00Z", "event": "Location",
    "StarSystem": "Dryau Awesomes", "SystemAddress": 42,
}, "bio-location")
bio.update_position({
    "lat": 12.5, "lon": -30.25, "body": "Dryau Awesomes A 1",
    "radius_m": 1_000_000,
})
bio.add_pin("Return to bacterium", timestamp="2026-08-01T00:00:00Z")

chronology = MiningTracker(chronology_id)
chronology.start(timestamp="2026-08-01T01:00:00Z", context={"system": "Manual Camp"})
combat_manual = CombatTracker(chronology_id)
combat_manual.start(timestamp="2026-08-01T01:00:01Z")
assert TimingModel(chronology_id).record(
    "docking", 123, context="Pruned Journal Port",
    started_at=event_epoch_ms("2026-05-01T00:00:00Z"),
    ended_at=event_epoch_ms("2026-05-01T00:02:03Z"), source="journal",
)

# Reproduce pre-profile inputs that still lived under ``default`` when repair
# began. The first real profile was Atomic Test; bootstrap has already recorded
# that adoption target, so staged helper rows must follow it during finalize.
default_carrier = CarrierPlanner("default")
default_carrier.configure_upkeep(9_000_000, target_weeks=6, source="commander input")
default_carrier.set_inventory({}, source="commander inventory input")
default_bio = ExobiologyMapper("default")
default_bio.update_position({
    "lat": -4.5, "lon": 88.0, "body": "Pre-profile A 1", "radius_m": 900_000,
})
default_bio.add_pin("Pre-profile waypoint", timestamp="2026-05-02T00:00:00Z")

journal_started = event_epoch_ms("2026-07-01T12:00:02Z")
journal_ended = event_epoch_ms("2026-07-30T12:00:02Z")
chronology.store.archive("lost-source-session", {
    "session_key": "lost-source-session",
    "started_ts": event_epoch_ms("2026-06-01T00:00:00Z"),
    "ended_ts": event_epoch_ms("2026-06-01T01:00:00Z"),
    "end_reason": "manual",
})
chronology.store.archive("corrupt-v4-session", {
    "session_key": "corrupt-v4-session",
    "started_ts": journal_started,
    "ended_ts": event_epoch_ms("2026-07-29T12:00:02Z"),
    "end_reason": "shutdown",
})
chronology.store.archive("duplicate-v4-timestamps", {
    "session_key": "duplicate-v4-timestamps",
    "started_ts": journal_started,
    "ended_ts": journal_ended,
    "end_reason": "shutdown",
})

# Simulate a database that ran the flawed recent-first history-v4 build. Stage
# and clear it explicitly, then construct a new watcher as if the process died
# at that exact point. The preservation phase must remain resumable on disk.
conn = marketdb.connect_user()
conn.execute("DELETE FROM user_meta WHERE key='journal_derived_version'")
conn.execute("INSERT OR REPLACE INTO user_meta(key,value) VALUES('history_version','4')")
conn.commit()
conn.close()
staging_watcher = JournalWatcher(AppState(), journal_dir=root)
assert staging_watcher._prepare_derived_history_replay().endswith(":preserving")
assert not CarrierPlanner(chronology_id).snapshot()["route"]["legs"]
conn = marketdb.connect_user()
assert conn.execute(
    "SELECT COUNT(*) FROM journal_replay_preserved_state WHERE commander_id=?",
    (chronology_id,),
).fetchone()[0] == 4
conn.close()

watcher = JournalWatcher(AppState(), journal_dir=root)
assert watcher.import_trade_history(), "resumed chronological sweep did not finish"

# Reproduce a bad v4 row that has the same logical session key as the rebuilt
# session but a wrong end time. The authoritative replayed row must win; it
# must not be renamed and retained as a duplicate.
conn = marketdb.connect_user()
rebuilt_key = conn.execute(
    "SELECT session_key FROM specialist_history WHERE commander_id=? AND workflow='mining'"
    " AND started_ts=? AND ended_ts=?",
    (chronology_id, journal_started, journal_ended),
).fetchone()[0]
raw = json.dumps({
    "session_key": rebuilt_key, "started_ts": journal_started,
    "ended_ts": event_epoch_ms("2026-07-29T12:00:02Z"), "end_reason": "shutdown",
}, separators=(",", ":"))
conn.execute(
    "UPDATE journal_replay_preserved_history SET session_key=?,summary_json=?"
    " WHERE commander_id=? AND workflow='mining' AND session_key='corrupt-v4-session'",
    (rebuilt_key, raw, chronology_id),
)
conn.commit()
conn.close()

# A transient final-merge failure must retry bootstrap rather than exposing a
# partially rebuilt projection or proceeding to live tailing.
finalize = watcher._finalize_derived_history_replay
finalize_calls = {"count": 0}


def flaky_finalize():
    finalize_calls["count"] += 1
    if finalize_calls["count"] == 1:
        raise sqlite3.OperationalError("simulated final merge failure")
    return finalize()


watcher._finalize_derived_history_replay = flaky_finalize
watcher._probe_game = lambda: None
watcher._fetch_community_bio = lambda *_args: watcher._stop_event.set()
watcher.run_forever()

chronology = MiningTracker(chronology_id)
mining_snapshot = chronology.snapshot()
assert mining_snapshot["active"], mining_snapshot
assert mining_snapshot["session"]["system"] == "Manual Camp", mining_snapshot
assert CombatTracker(chronology_id).snapshot()["active"]

carrier_snapshot = CarrierPlanner(chronology_id).snapshot()
assert carrier_snapshot["upkeep"]["weekly_cr"] == 25_000_000, carrier_snapshot
assert carrier_snapshot["route"]["legs"][0]["system"] == "Beagle Point", carrier_snapshot
assert carrier_snapshot["inventory"]["tritium"]["count"] == 777, carrier_snapshot

bio_snapshot = ExobiologyMapper(chronology_id).snapshot()
assert any(
    pin["label"] == "Return to bacterium" and pin["source"] == "manual"
    for pin in bio_snapshot["current_map"]["pins"]
), bio_snapshot

adopted_carrier = CarrierPlanner(commander_id).snapshot()
assert adopted_carrier["upkeep"]["weekly_cr"] == 9_000_000, adopted_carrier
assert adopted_carrier["inventory"] == {}, adopted_carrier
assert adopted_carrier["inventory_source"] == "commander inventory input", adopted_carrier
adopted_bio = ExobiologyMapper(commander_id).snapshot()
assert any(
    pin["label"] == "Pre-profile waypoint"
    for pin in adopted_bio["current_map"]["pins"]
), adopted_bio

history = chronology.history(20)
assert {row["session_key"] for row in history} == {
    rebuilt_key, "lost-source-session",
}, history
assert next(row for row in history if row["session_key"] == rebuilt_key)[
    "ended_ts"
] == journal_ended
assert finalize_calls["count"] == 2
conn = marketdb.connect_user()
assert conn.execute(
    "SELECT value FROM user_meta WHERE key='journal_derived_version'"
).fetchone()[0] == watcher.DERIVED_HISTORY_VERSION
assert conn.execute("SELECT COUNT(*) FROM journal_replay_preserved_state").fetchone()[0] == 0
assert conn.execute("SELECT COUNT(*) FROM journal_replay_preserved_history").fetchone()[0] == 0
assert conn.execute(
    "SELECT COUNT(*) FROM timing_observations WHERE commander_id=?"
    " AND context_key='Pruned Journal Port' AND source='journal'",
    (chronology_id,),
).fetchone()[0] == 1
conn.close()

print("journal replay OK: chronological rebuild preserves explicit local state and old history")

# A folder-switch reconstruction that hits a transient history failure must be
# retried on the next poll even though journal_dir already changed on attempt
# one; live tailing cannot run between the two attempts.
switch_state = AppState()
switch_state.update(journal_dir_found=True)
switch = JournalWatcher(switch_state, journal_dir=Path(_tmp.name) / "temporarily-missing")
switch._fixed_dir = False
attempts = {"import": 0, "bootstrap": 0}


def switch_import():
    attempts["import"] += 1
    return attempts["import"] > 1


switch.import_trade_history = switch_import
switch.bootstrap = lambda: attempts.__setitem__("bootstrap", attempts["bootstrap"] + 1)
switch._finalize_derived_history_replay = lambda: True
switch._fetch_community_bio = lambda *_args: None
original_find_journal_dir = journal_module.find_journal_dir
journal_module.find_journal_dir = lambda: root
try:
    try:
        switch._ensure_journal_dir()
        raise AssertionError("failed folder reconstruction did not stop live polling")
    except RuntimeError:
        pass
    assert switch._reconstruction_pending and not switch._live
    switch._ensure_journal_dir()
finally:
    journal_module.find_journal_dir = original_find_journal_dir
assert attempts == {"import": 2, "bootstrap": 1}, attempts
assert not switch._reconstruction_pending and switch._live

print("journal folder recovery OK: partial reconstruction retries before live tailing")

# A failure before the preserving marker commits (for example while creating
# the safety backup) is still a required v4 repair, not permission to fall
# through to recent bootstrap. The startup barrier must retry it.
retry_root = Path(_tmp.name) / "retry-journals"
retry_root.mkdir()
for day in (1, 2):
    (retry_root / f"Journal.2026-08-0{day}T120000.01.log").write_text(
        "\n".join(json.dumps(row, separators=(",", ":")) for row in (
            {"timestamp": f"2026-08-0{day}T12:00:00Z", "event": "Fileheader",
             "gameversion": "4.1"},
            {"timestamp": f"2026-08-0{day}T12:00:01Z", "event": "Commander",
             "Name": "Retry Test"},
        )) + "\n",
        encoding="utf-8",
    )
conn = marketdb.connect_user()
conn.execute("DELETE FROM user_meta WHERE key='journal_derived_version'")
conn.execute("INSERT OR REPLACE INTO user_meta(key,value) VALUES('history_version','4')")
conn.commit()
conn.close()
backup = marketdb.backup_commander_data
backup_calls = {"count": 0}


def flaky_backup(*args, **kwargs):
    backup_calls["count"] += 1
    if backup_calls["count"] == 1:
        raise OSError("simulated backup failure")
    return backup(*args, **kwargs)


marketdb.backup_commander_data = flaky_backup
retry = JournalWatcher(AppState(), journal_dir=retry_root)
retry._probe_game = lambda: None
retry._fetch_community_bio = lambda *_args: retry._stop_event.set()
try:
    retry.run_forever()
finally:
    marketdb.backup_commander_data = backup
assert backup_calls["count"] == 2, backup_calls
conn = marketdb.connect_user()
assert conn.execute(
    "SELECT value FROM user_meta WHERE key='journal_derived_version'"
).fetchone()[0] == retry.DERIVED_HISTORY_VERSION
conn.close()

print("journal staging recovery OK: pre-marker backup failures retry safely")
