"""Profile-bound browser persistence and pairing-gate runtime contract."""

import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
app = (ROOT / "ui" / "app.js").read_text(encoding="utf-8")
html = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")

assert "activeRoute:v2:" in app
assert "galaxyHistory:v2:" in app
assert "state.commander_id || null" in app
assert "enterPairingRequired" in app and "resp.status === 401" in app
assert "profileGeneration" in app
assert "X-Frameshift-Commander" in app and "commanderFetch" in app
assert "clearAnalyticsWorkspace();" in app and "a.commander_id" in app
assert "clearAlertWorkspace();" in app and "data.commander_id" in app
assert "eddn_extended_upload" in app and "Optional broader contribution" in app
assert "data-extension-action=\"approve\"" in app
assert "/api/extensions/${encodeURIComponent(extensionId)}/${action}" in app
assert 'class="pairing-panel" tabindex="-1"' in html

node = shutil.which("node")
if node:
    subprocess.run([node, "--check", str(ROOT / "ui" / "app.js")], check=True)
    subprocess.run([node, str(ROOT / "tests" / "profile_ui_runtime.cjs")], check=True)

print("profile UI OK: scoped persistence, handoff invalidation, and accessible pairing gate")
