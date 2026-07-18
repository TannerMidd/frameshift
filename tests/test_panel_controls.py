"""Contracts for the Holo Bracket button system (design handoff v1.0).

Two files — ui/holo-buttons.css + ui/hb.js — carry the app-wide button
language; every ad-hoc button style from before the migration must stay gone.
"""

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
APP = (ROOT / "ui" / "app.js").read_text(encoding="utf-8")
HTML = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "ui" / "style.css").read_text(encoding="utf-8")
HB_CSS = (ROOT / "ui" / "holo-buttons.css").read_text(encoding="utf-8")
HB_JS = (ROOT / "ui" / "hb.js").read_text(encoding="utf-8")

# The system ships as two files, loaded after style.css so the .hb layer wins.
assert HTML.index('href="style.css"') < HTML.index('href="holo-buttons.css"')
assert '<script src="hb.js" defer></script>' in HTML

# Tiers, states and sizes from the spec are all present in the shipped CSS.
for token in (
    ".hb {",
    ".hb-primary",
    ".hb-ghost",
    ".hb-utility",
    ".hb-danger",
    ".hb-good",
    ".hb-lg",
    ".hb-sm",
    ".hb-icon",
    '.hb[aria-busy="true"]:disabled',
    '.hb[aria-pressed="true"]',
    ".hb-group",
    ".hb-fx",
    "@keyframes hb-pulse",
    "@keyframes hb-flash",
    "body.panel-mode .hb { min-height: 54px",
    "@media (prefers-reduced-motion: reduce)",
):
    assert token in HB_CSS, token

# Press FX: one delegated listener, no per-button wiring.
assert 'document.addEventListener("pointerdown"' in HB_JS
assert 'closest(".hb")' in HB_JS
assert 'className = "hb-fx"' in HB_JS

# The legacy button classes are fully migrated out of markup, script and CSS.
for legacy in (
    'class="primary',
    'class="copy',
    'class="plotbtn',
    'class="fp-btn',
    'class="fp-railbtn',
    'class="tab ',
    'class="tab"',
    'class="go-launch"',
):
    assert legacy not in HTML, legacy
for legacy in ("button.primary", ".plotbtn", ".fp-btn {", ".fp-railbtn", ".copy {", ".copy:hover"):
    assert legacy not in CSS, legacy
for legacy in ('className = "copy', 'className = "primary', 'className = "plotbtn'):
    assert legacy not in APP, legacy

# Segmented groups: the desktop tabs and the specialist switcher are
# .hb-group rows whose active segment carries aria-pressed="true".
assert '<nav class="tabs hb-group" id="tabs">' in HTML
assert '<div class="sp-switcher hb-group" role="tablist"' in HTML
assert 'b.setAttribute("aria-pressed", String(b.dataset.tab === name)))' in APP
assert 'button.setAttribute("aria-pressed", String(active));' in APP
assert 'data-page="status" class="active" aria-current="page"' in HTML
assert 'b.setAttribute("aria-current", "page");' in APP

# Destructive and semantic states are exposed through the spec's classes.
assert 'id="ep-traders" class="hb hb-utility"' in HTML
assert 'id="galhistory-clear"' in HTML and 'class="hb hb-utility hb-sm hb-danger"' in HTML
assert 'revoke.className = "hb hb-utility hb-danger";' in APP
assert 'remove.className = "hb hb-utility hb-danger";' in APP
assert 'remove.className = "hb hb-utility hb-danger ep-remove";' in APP
assert 'stop.className = "hb hb-utility hb-icon hb-sm hb-danger rp-stop";' in APP
assert 'btn.classList.toggle("hb-danger", on);' in APP  # PLOT → CANCEL swap
assert 'btn.classList.add("hb-good");' in APP  # copy feedback

# Busy semantics: interaction-disabled commands get aria-busy, availability
# rules stay quiet; the PLOT→CANCEL swap keeps aria-busy while live.
assert "function initBusyButtonStates()" in APP
assert 'button?.matches("button.hb, .ub-btn")' in APP
assert 'button.setAttribute("aria-busy", "true");' in APP
assert 'target.removeAttribute("aria-busy");' in APP
assert "const completed = new WeakSet();" in APP
assert "oldValue !== null || !target.disabled" in APP
assert "attributeOldValue: true" in APP
assert 'btn.setAttribute("aria-busy", "true");' in APP

# Checkboxes are cockpit switches in the same design language: squared
# accent-framed housing, sliding paddle that lights orange when on. The
# .setting rows render the identical look through their .switch span.
assert 'input[type="checkbox"] {' in CSS
assert "appearance: none;" in CSS
assert 'input[type="checkbox"]:checked::after' in CSS
assert 'body.panel-mode input[type="checkbox"] { width: 46px; height: 26px; min-height: 26px; }' in CSS
assert 'input[type="checkbox"] { appearance: auto; }' in CSS  # forced-colors fallback
assert ".setting .switch" in CSS and "border-radius: 999px" not in CSS.split(".setting .switch", 1)[1].split("}", 1)[0]

# Touch, keyboard and Windows High Contrast remain first-class.
assert ".fp-nav-pages button { min-height: 44px; }" in CSS
assert ":focus-visible" in HB_CSS
assert "@media (forced-colors: active)" in CSS
assert "background: Highlight;" in CSS
assert "color: HighlightText;" in CSS

# No temporary visual-QA hook may ship.
assert "_capture_panel.js" not in HTML
assert not (ROOT / "ui" / "_capture_panel.js").exists()
assert "_final_panel_audit.js" not in HTML
assert not (ROOT / "ui" / "_final_panel_audit.js").exists()
assert "_busy_state_audit.js" not in HTML
assert not (ROOT / "ui" / "_busy_state_audit.js").exists()

result = subprocess.run(
    ["node", "--check", str(ROOT / "ui" / "app.js")],
    cwd=ROOT,
    capture_output=True,
    text=True,
    check=False,
)
assert result.returncode == 0, result.stdout + result.stderr

print("panel controls OK: Holo Bracket system shipped, legacy button styles gone")
