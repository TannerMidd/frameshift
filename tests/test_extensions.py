"""Declarative extension manifests: validation, permissions and event actions."""

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_tmp = tempfile.TemporaryDirectory()
os.environ["ET_DATA_DIR"] = _tmp.name

from elite.extensions import ExtensionManager  # noqa: E402


root = Path(_tmp.name) / "extensions"
pack = root / "sample.alerts"
pack.mkdir(parents=True)
(pack / "manifest.json").write_text(json.dumps({
    "id": "sample.alerts",
    "name": "Sample alerts",
    "version": "1.0.0",
    "api_version": 1,
    "permissions": ["read:journal", "emit:alert", "emit:objective"],
    "rules": [
        {
            "event": "HullDamage",
            "when": {"Health": {"max": 0.25}},
            "action": {
                "type": "alert",
                "level": "red",
                "code": "extension-hull",
                "text": "Hull at {Health}",
            },
        },
        {
            "event": "FSDJump",
            "when": {"StarSystem": {"exists": True}},
            "action": {
                "type": "objective",
                "category": "exploration",
                "title": "Survey {StarSystem}",
                "system": "{StarSystem}",
            },
        },
    ],
}), encoding="utf-8")

manager = ExtensionManager(root)
status = manager.reload()
assert status["errors"] == [], status
assert status["loaded"][0]["id"] == "sample.alerts"

seen = []
unsubscribe = manager.subscribe(seen.append)
assert manager.publish({"event": "HullDamage", "Health": 0.4}) == []
actions = manager.publish({"event": "HullDamage", "Health": 0.2})
assert actions[0]["text"] == "Hull at 0.2" and seen == actions
actions = manager.publish({"event": "FSDJump", "StarSystem": "Shinrarta Dezhra"})
assert actions[0]["title"] == "Survey Shinrarta Dezhra"
unsubscribe()

# A rule cannot emit an action it did not request permission for.
limited = root / "limited.pack"
limited.mkdir()
(limited / "manifest.json").write_text(json.dumps({
    "id": "limited.pack",
    "api_version": 1,
    "permissions": ["read:journal"],
    "rules": [{"event": "*", "action": {"type": "alert", "text": "nope"}}],
}), encoding="utf-8")
manager.reload()
assert manager.publish({"event": "Music"}) == []

# Invalid or mismatched manifests are reported without taking down valid packs.
bad = root / "bad-pack"
bad.mkdir()
(bad / "manifest.json").write_text('{"id":"different", "api_version":1}', encoding="utf-8")
status = manager.reload()
assert any(row["id"] == "bad-pack" for row in status["errors"]), status
assert any(row["id"] == "sample.alerts" for row in status["loaded"]), status

# A pack cannot approve itself. Approval is stored outside the pack and bound
# to every reviewed file, including auxiliary scripts, DLLs and helpers.
process_pack = root / "process.pack"
process_pack.mkdir()
(process_pack / "adapter.bin").write_bytes(b"reviewed adapter v1")
(process_pack / "helper.dll").write_bytes(b"reviewed helper v1")
(process_pack / "APPROVED").write_text("self approval must be ignored", encoding="utf-8")
(process_pack / "manifest.json").write_text(json.dumps({
    "id": "process.pack",
    "api_version": 1,
    "permissions": ["read:journal", "emit:alert"],
    "command": ["adapter.bin"],
}), encoding="utf-8")
status = manager.reload()
process_row = next(row for row in status["loaded"] if row["id"] == "process.pack")
assert process_row["approved"] is False and process_row["approval_required"] is True
status = manager.approve_process("process.pack")
assert next(row for row in status["loaded"] if row["id"] == "process.pack")["approved"] is True
approval_file = root.parent / "extension-approvals.json"
assert approval_file.is_file() and not approval_file.is_relative_to(process_pack)
approved_extension = next(
    extension for extension in manager._extensions if extension.extension_id == "process.pack"
)

# Changing an auxiliary code file is blocked immediately, before a reload.
(process_pack / "helper.dll").write_bytes(b"changed helper v2")
with patch("elite.extensions.subprocess.run") as process_run:
    manager._run_process(approved_extension, {"event": {"event": "Music"}})
    assert process_run.call_count == 0, "changed helper ran before reload"
status = manager.reload()
assert next(row for row in status["loaded"] if row["id"] == "process.pack")["approved"] is False
manager.approve_process("process.pack")
approved_extension = next(
    extension for extension in manager._extensions if extension.extension_id == "process.pack"
)

# The command executable has the same complete-pack boundary.
(process_pack / "adapter.bin").write_bytes(b"changed adapter v2")
with patch("elite.extensions.subprocess.run") as process_run:
    manager._run_process(approved_extension, {"event": {"event": "Music"}})
    assert process_run.call_count == 0, "changed executable ran before reload"
status = manager.reload()
assert next(row for row in status["loaded"] if row["id"] == "process.pack")["approved"] is False
manager.approve_process("process.pack")
status = manager.revoke_process("process.pack")
assert next(row for row in status["loaded"] if row["id"] == "process.pack")["approved"] is False

# Invalid condition operands are isolated at load time and cannot abort valid
# rules from other packs.
malformed = root / "malformed.rule"
malformed.mkdir()
(malformed / "manifest.json").write_text(json.dumps({
    "id": "malformed.rule",
    "api_version": 1,
    "permissions": ["read:journal", "emit:alert"],
    "rules": [{
        "event": "Music", "when": {"Track": {"in": 7}},
        "action": {"type": "alert", "text": "bad"},
    }],
}), encoding="utf-8")
status = manager.reload()
assert any(row["id"] == "malformed.rule" for row in status["errors"]), status
assert manager.publish({"event": "HullDamage", "Health": 0.2})[0]["text"] == "Hull at 0.2"

manager.shutdown()

print("extensions OK: validation, full-pack approvals, rules, templating, isolation")
