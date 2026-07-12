"""Engineering workshop UI and one-file packaging stay wired to the catalog."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

html = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
js = (ROOT / "ui" / "app.js").read_text(encoding="utf-8")
css = (ROOT / "ui" / "style.css").read_text(encoding="utf-8")

for element_id in (
    "ep-search", "ep-kind", "ep-blueprint", "ep-current", "ep-target",
    "ep-quantity", "engplan-summary", "engplan-list", "engplan-materials",
):
    assert f'id="{element_id}"' in html, element_id

assert "1, 2, 3, 4 and 5" in html
assert "typical roll" not in html.casefold()
assert "luck" not in html.casefold()
assert "function fillEngineeringCatalog" in js
assert "function updateEngineeringGradeFields" in js
assert "function renderEngPlans" in js
assert "state.ship_locker" in js and "state.cargo_inventory" in js
assert '$("ep-search").addEventListener("input"' in js
assert '$("ep-target").addEventListener("change"' in js
assert "ep-material-row" in css and "ep-wish-item" in css

workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
batch = (ROOT / "build_exe.bat").read_text(encoding="utf-8")
# PyInstaller .spec files are generated local build artifacts and intentionally
# ignored.  Validate the two versioned build entry points used by maintainers
# and CI so this assertion also works in a clean checkout.
for content in (workflow, batch):
    assert "engineering_catalog.json.gz" in content
    assert "THIRD_PARTY_NOTICES.md" in content
ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
assert "!elite/data/engineering_catalog.json.gz" in ignore

print("ALL COMPLETE ENGINEERING UI/PACKAGING TESTS PASSED")
