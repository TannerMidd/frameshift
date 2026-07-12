"""Account-free operations board CRUD, export/import and conflict handling."""

import copy
import json
import os
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
_tmp = tempfile.TemporaryDirectory()
os.environ["ET_DATA_DIR"] = _tmp.name

from elite import marketdb  # noqa: E402
from elite.operations import FORMAT, OperationsBoard  # noqa: E402


commander = marketdb.ensure_commander_profile("Operations Tester")
ops = OperationsBoard(commander)
board = ops.create_board("Trailblazer build", "Coordinate the local squad without an account")
objective = ops.add_objective(
    board["id"], "Deliver construction steel", priority=85,
    system="Wregoe Test", station="Construction Site", payload={"remaining": 5000},
)
assignment = ops.assign(board["id"], "CMDR Alice", objective_id=objective["id"], role="Hauler")
reservation = ops.reserve(
    board["id"], "commodity", "steel", 400, objective_id=objective["id"],
    unit="t", assignee="CMDR Alice",
)
contribution = ops.contribute(
    board["id"], "CMDR Bob", "delivery", 128, objective_id=objective["id"],
    unit="t", note="First Type-9 load", evidence="Journal.2026-01.log:42",
)

snap = ops.snapshot(board["id"])
assert snap["board"]["title"] == "Trailblazer build"
assert snap["objectives"][0]["payload"] == {"remaining": 5000}
assert snap["assignments"][0]["id"] == assignment["id"]
assert snap["reservations"][0]["amount"] == 400
assert snap["contributions"][0]["amount"] == 128
assert len({board["id"], objective["id"], assignment["id"], reservation["id"], contribution["id"]}) == 5

exported = ops.export_json(board["id"])
document = json.loads(exported)
assert document["format"] == FORMAT and document["version"] == 1
assert ops.import_json(exported)["unchanged"] == 5

# Equal-revision concurrent edits are resolved deterministically and recorded,
# so every peer converges while preserving the losing version for review.
remote = copy.deepcopy(document)
remote_board = remote["records"]["boards"][0]
remote_board["title"] = "Trailblazer build — remote edit"
remote_board["updated_at"] = "2099-01-01T00:00:00+00:00"
remote_board["updated_by"] = "node-zzzzzzzz"
remote_board["version_hash"] = "ffffffffffffffffffffffff"
report = ops.import_json(remote)
assert report["updated"] == 1 and report["conflicts"] == 1, report
assert ops.snapshot(board["id"])["board"]["title"].endswith("remote edit")
assert ops.conflicts(board["id"])[0]["record_id"] == board["id"]

# A newer local revision cannot be overwritten by an old export.
ops.update("boards", board["id"], description="newer local description")
stale = ops.import_json(document)
assert stale["kept_local"] >= 1
assert ops.snapshot(board["id"])["board"]["description"] == "newer local description"

# Local HTTP requests are served concurrently.  A writer that begins while
# another write transaction is active must wait, then merge its field change
# against the newly committed revision instead of overwriting it from a stale
# read.  Holding the first transaction makes this ordering deterministic.
holder = marketdb.connect_user()
holder.execute("BEGIN IMMEDIATE")
held = holder.execute(
    "SELECT revision FROM operation_boards WHERE id=?", (board["id"],)
).fetchone()[0]
holder.execute(
    "UPDATE operation_boards SET description=?, revision=? WHERE id=?",
    ("concurrent description", held + 1, board["id"]),
)
started = threading.Event()

def concurrent_status_update():
    started.set()
    return OperationsBoard(commander).update("boards", board["id"], status="paused")

with ThreadPoolExecutor(max_workers=1) as executor:
    pending = executor.submit(concurrent_status_update)
    assert started.wait(2)
    time.sleep(0.1)
    assert not pending.done(), "concurrent update did not wait for the active writer"
    holder.commit()
    concurrent = pending.result(timeout=5)
holder.close()
assert concurrent["status"] == "paused"
assert concurrent["description"] == "concurrent description"
assert concurrent["revision"] == held + 2

# Immutable contribution IDs allow independently-created rows to merge without
# last-writer loss.
fresh = ops.export_data(board["id"])
new_contribution = copy.deepcopy(fresh["records"]["contributions"][0])
new_contribution["id"] = "contrib-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
new_contribution["contributor"] = "CMDR Charlie"
new_contribution["amount"] = 256
new_contribution["updated_by"] = "node-remote"
new_contribution["version_hash"] = "remote-contribution-v1"
fresh["records"] = {
    "boards": [], "objectives": [], "assignments": [], "reservations": [],
    "contributions": [new_contribution],
}
merged = ops.import_json(fresh)
assert merged["inserted"] == 1, merged
assert sum(row["amount"] for row in ops.snapshot(board["id"])["contributions"]) == 384

# Imports are transactional: a valid-looking board is rolled back if a child
# references a board that does not exist.
bad = ops.export_data(board["id"])
bad["records"] = {key: [] for key in bad["records"]}
bad_board = copy.deepcopy(document["records"]["boards"][0])
bad_board["id"] = "board-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
bad_board["version_hash"] = "bad-board-v1"
bad_objective = copy.deepcopy(document["records"]["objectives"][0])
bad_objective["id"] = "opobj-cccccccccccccccccccccccccccccccc"
bad_objective["board_id"] = "board-does-not-exist-anywhere"
bad_objective["version_hash"] = "bad-objective-v1"
bad["records"]["boards"] = [bad_board]
bad["records"]["objectives"] = [bad_objective]
try:
    ops.import_json(bad)
    raise AssertionError("broken foreign key import was accepted")
except Exception:
    pass
assert all(row["id"] != bad_board["id"] for row in ops.list_boards(include_deleted=True))

removed = ops.remove("reservations", reservation["id"])
assert removed["deleted_at"] and ops.snapshot(board["id"])["reservations"] == []
assert ops.snapshot(board["id"], include_deleted=True)["reservations"]

print("operations OK: local board, assignments, reservations, contributions, conflict-safe JSON")
