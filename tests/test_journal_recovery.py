"""Journal background failures remain visible and retryable."""

import json
import os
import sys
import tempfile
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
_tmp = tempfile.TemporaryDirectory()
os.environ["ET_DATA_DIR"] = _tmp.name

from elite import marketdb  # noqa: E402
from elite.journal import JournalWatcher  # noqa: E402
from elite.state import AppState  # noqa: E402


root = Path(_tmp.name)


def write(name, events):
    (root / name).write_text(
        "\n".join(json.dumps(event, separators=(",", ":")) for event in events) + "\n",
        encoding="utf-8",
    )


completed = "Journal.2026-07-11T120000.01.log"
write(completed, [
    {"timestamp": "2026-07-11T12:00:00Z", "event": "Fileheader", "gameversion": "4.1"},
    {"timestamp": "2026-07-11T12:00:01Z", "event": "Commander", "Name": "Alpha"},
    {"timestamp": "2026-07-11T12:00:02Z", "event": "MissionCompleted",
     "Name": "Mission_Test", "Reward": 12345},
])
write("Journal.2026-07-12T120000.01.log", [
    {"timestamp": "2026-07-12T12:00:00Z", "event": "Fileheader", "gameversion": "4.1"},
    {"timestamp": "2026-07-12T12:00:01Z", "event": "Commander", "Name": "Alpha"},
])

watcher = JournalWatcher(AppState(), journal_dir=root)
original = watcher._import_event


def fail_once(event, commander_id):
    raise OSError("simulated transient database failure")


watcher._import_event = fail_once
watcher.import_trade_history()
alpha = marketdb.commander_profile_id("Alpha")
conn = marketdb.connect_user()
assert not conn.execute(
    "SELECT 1 FROM imported_journals WHERE commander_id=? AND filename=?",
    (alpha, completed),
).fetchone(), "a partial reducer run was incorrectly marked complete"
conn.close()

watcher._import_event = original
from elite.timings import TimingModel  # noqa: E402

original_observe = TimingModel.observe_event
TimingModel.observe_event = lambda self, event: (_ for _ in ()).throw(
    OSError("simulated timing reducer failure"))
try:
    watcher.import_trade_history()
finally:
    TimingModel.observe_event = original_observe
conn = marketdb.connect_user()
assert not conn.execute(
    "SELECT 1 FROM imported_journals WHERE commander_id=? AND filename=?",
    (alpha, completed),
).fetchone(), "a timing failure was incorrectly checkpointed"
conn.close()

watcher.import_trade_history()
conn = marketdb.connect_user()
assert conn.execute(
    "SELECT 1 FROM imported_journals WHERE commander_id=? AND filename=?",
    (alpha, completed),
).fetchone(), "successful retry did not checkpoint the journal"
assert conn.execute(
    "SELECT amount FROM income_log WHERE commander_id=? AND category='mission'",
    (alpha,),
).fetchone()[0] == 12345
conn.close()

# Startup/poll failures are reported and do not disappear into bare `pass`
# handlers. Let the chronological import fail once and retry, then stop after
# the first live-poll wait.
contexts = []
watcher = JournalWatcher(AppState(), journal_dir=root)
watcher.bootstrap = lambda: (_ for _ in ()).throw(RuntimeError("bootstrap"))
watcher._probe_game = lambda: (_ for _ in ()).throw(RuntimeError("probe"))
history_calls = {"count": 0}


def fail_history_once():
    history_calls["count"] += 1
    if history_calls["count"] == 1:
        raise RuntimeError("history")
    return True


watcher.import_trade_history = fail_history_once
watcher._ensure_journal_dir = lambda: (_ for _ in ()).throw(RuntimeError("poll"))
watcher._log_background_failure = lambda context, exc: contexts.append((context, type(exc).__name__))

original_wait = watcher._stop_event.wait
wait_calls = {"count": 0}


def stop_after_retry(_seconds):
    wait_calls["count"] += 1
    if wait_calls["count"] == 1:
        return False
    raise StopIteration()


watcher._stop_event.wait = stop_after_retry
try:
    try:
        watcher.run_forever()
    except StopIteration:
        pass
finally:
    watcher._stop_event.wait = original_wait

assert [context for context, _kind in contexts] == [
    "initial game process probe", "journal history import", "journal bootstrap",
    "game process probe",
    "journal watcher poll",
]
assert history_calls["count"] == 2

print("journal recovery OK: failed reducers retry and watcher failures are diagnosed")
