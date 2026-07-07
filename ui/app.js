/* Elite Trader UI: polls /api/state and renders. Same page works in the
   desktop window (pywebview) and any browser on the LAN. */

const $ = (id) => document.getElementById(id);

let state = null;
let marketSort = { key: "sell", dir: -1 };
let routeFormTouched = false;

/* Active route being flown (persisted): { kind, label, waypoints:[{system,note}], index } */
let activeRoute = null;
try { activeRoute = JSON.parse(localStorage.getItem("activeRoute") || "null"); } catch (e) {}

/* ---------- helpers ---------- */

function fmtCr(n) {
  return n == null ? "—" : Math.round(n).toLocaleString() + " cr";
}

function copyText(text, btn) {
  const done = () => {
    if (!btn) return;
    btn.classList.add("done");
    setTimeout(() => btn.classList.remove("done"), 900);
  };
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(done);
  } else {
    // http:// LAN origins are not "secure contexts", so fall back.
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand("copy"); done(); } catch (e) {}
    ta.remove();
  }
}

function openExternal(url, label) {
  if (window.pywebview && window.pywebview.api) {
    if ($("inapp-toggle").checked && window.pywebview.api.open_inline) {
      window.pywebview.api.open_inline(url, label || "Browser");
      return true;
    }
    if (window.pywebview.api.open_url) {
      window.pywebview.api.open_url(url);
      return true;
    }
  }
  return false; // let the browser handle it
}

/* ---------- autoplot ---------- */

let plotBusy = false;

function setPlotStatus(text, isError) {
  for (const id of ["plot-status", "fp-plot-status"]) {
    const el = $(id);
    if (!el) continue;
    el.classList.toggle("error", !!isError);
    el.textContent = text;
  }
}

async function plotSystem(system) {
  if (!system || plotBusy) return;
  plotBusy = true;
  setPlotStatus(`Plotting route to ${system} — leave the game window alone for ~10s…`, false);
  try {
    const resp = await fetch("/api/plot", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ system }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || "Plot failed");
    setPlotStatus(`Sent plot sequence for ${system} — check the game.`, false);
    speak("Route plotted to " + system);
  } catch (err) {
    setPlotStatus(String(err.message || err), true);
  } finally {
    plotBusy = false;
  }
}

/* ---------- voice callouts (F8) ---------- */

let voiceOn = localStorage.getItem("voice") === "1";

function speak(text, force) {
  if ((!voiceOn && !force) || !("speechSynthesis" in window) || !text) return;
  try {
    const u = new SpeechSynthesisUtterance(text);
    u.rate = 1.05;
    u.pitch = 1;
    window.speechSynthesis.cancel();  // don't queue stale callouts
    window.speechSynthesis.speak(u);
  } catch (e) { /* speech is a nicety */ }
}

function setVoice(on, announce) {
  voiceOn = on;
  localStorage.setItem("voice", on ? "1" : "0");
  const btn = $("fp-voice");
  if (btn) {
    btn.setAttribute("aria-pressed", on ? "true" : "false");
    btn.classList.toggle("on", on);
    btn.textContent = on ? "🔊 VOICE" : "🔈 VOICE";
  }
  if (on && announce) speak("Voice callouts on.", true);
}

/* ---------- flight panel mode ---------- */

const PANEL_PAGES = ["status", "trade", "commodities", "bio", "guides", "analytics", "local", "database"];

function setPanelMode(on) {
  document.body.classList.toggle("panel-mode", on);
  localStorage.setItem("panelMode", on ? "1" : "0");
  try {
    if (on && document.documentElement.requestFullscreen) {
      document.documentElement.requestFullscreen();
    } else if (!on && document.fullscreenElement) {
      document.exitFullscreen();
    }
  } catch (e) { /* fullscreen is a nicety, not a requirement */ }
  if (on) {
    setPanelPage(localStorage.getItem("panelPage") || "status");
  } else {
    $("flight-panel").classList.add("hidden");
  }
}

/* The element that represents a panel page: the flight panel for "status",
   otherwise that tab's pane (the location card above it stays put, like chrome). */
function panelViewEl(name) {
  return name === "status" ? $("flight-panel") : $("tab-" + name);
}

function slideIn(el, dir) {
  if (!el || !dir) return;
  el.classList.remove("slide-in-left", "slide-in-right");
  void el.offsetWidth; // restart the animation if the class is re-applied
  el.classList.add(dir > 0 ? "slide-in-right" : "slide-in-left");
  el.addEventListener("animationend",
    () => el.classList.remove("slide-in-left", "slide-in-right"), { once: true });
}

function setPanelPage(name, slideDir) {
  if (!PANEL_PAGES.includes(name)) name = "status";
  localStorage.setItem("panelPage", name);
  const statusPage = name === "status";
  $("flight-panel").classList.toggle("hidden", !statusPage);
  document.body.classList.toggle("fp-status-page", statusPage);
  if (!statusPage) activateTab(name);
  document.querySelectorAll("#fp-nav button").forEach((b) =>
    b.classList.toggle("active", b.dataset.page === name));
  if (statusPage && state) renderPanel();
  window.scrollTo(0, 0);
  if (slideDir) slideIn(panelViewEl(name), slideDir);
}

function panelSwipe(dx) {
  const current = localStorage.getItem("panelPage") || "status";
  const idx = PANEL_PAGES.indexOf(current);
  const forward = dx < 0;
  const next = PANEL_PAGES[(idx + (forward ? 1 : PANEL_PAGES.length - 1)) % PANEL_PAGES.length];
  setPanelPage(next, forward ? 1 : -1);
}

function initPanelNav() {
  document.querySelectorAll("#fp-nav button").forEach((b) =>
    b.addEventListener("click", () => {
      if (b.dataset.page === "__exit") { setPanelMode(false); return; }
      const current = localStorage.getItem("panelPage") || "status";
      const delta = PANEL_PAGES.indexOf(b.dataset.page) - PANEL_PAGES.indexOf(current);
      setPanelPage(b.dataset.page, Math.sign(delta));
    }));

  // Swipe left/right between pages (except inside horizontally scrollable
  // tables and form fields). The page follows the finger while the gesture is
  // in flight; past the threshold it hands off to a directional slide-in.
  let startX = 0, startY = 0, gesture = null, view = null;
  const endDrag = () => {
    if (view) {
      view.classList.remove("fp-dragging");
      view.style.transform = "";
    }
    gesture = null;
    view = null;
  };
  document.addEventListener("touchstart", (ev) => {
    gesture = null;
    if (!document.body.classList.contains("panel-mode") || ev.touches.length !== 1) return;
    if (ev.target.closest(".table-wrap, input, select, textarea")) return;
    startX = ev.touches[0].clientX;
    startY = ev.touches[0].clientY;
    gesture = "pending";
  }, { passive: true });
  document.addEventListener("touchmove", (ev) => {
    if (!gesture) return;
    const dx = ev.touches[0].clientX - startX;
    const dy = ev.touches[0].clientY - startY;
    if (gesture === "pending") {
      // Decide the gesture's orientation once, so vertical scrolling never drags the page.
      if (Math.abs(dy) > 18 && Math.abs(dy) > Math.abs(dx)) { gesture = null; return; }
      if (Math.abs(dx) > 14 && Math.abs(dx) > Math.abs(dy) * 1.4) {
        gesture = "swipe";
        view = panelViewEl(localStorage.getItem("panelPage") || "status");
        view.classList.add("fp-dragging");
      }
      return;
    }
    if (view) view.style.transform = `translateX(${dx * 0.85}px)`;
  }, { passive: true });
  document.addEventListener("touchend", (ev) => {
    if (gesture !== "swipe") { gesture = null; return; }
    const dx = ev.changedTouches[0].clientX - startX;
    endDrag();
    if (Math.abs(dx) > 70) panelSwipe(dx);
  }, { passive: true });
  document.addEventListener("touchcancel", endDrag, { passive: true });
}

function renderPanel() {
  if (!document.body.classList.contains("panel-mode")) return;
  $("fp-cmdr").textContent = state.commander ? "CMDR " + state.commander : "";
  $("fp-system").textContent = state.system || "—";
  $("fp-station").textContent = state.docked && state.station
    ? `DOCKED · ${state.station}`
    : (state.body && state.body !== state.system ? `IN SPACE · ${state.body}` : "IN SPACE");
  $("fp-dest").textContent = state.destination ? `DESTINATION · ${state.destination}` : "";

  const fuelPct = state.fuel_capacity > 0 ? Math.min(100, (state.fuel_main / state.fuel_capacity) * 100) : 0;
  const fill = $("fp-fuel-fill");
  fill.style.width = fuelPct + "%";
  fill.style.background = fuelPct < 25 ? "var(--bad)" : "var(--orange)";
  $("fp-fuel").textContent = state.fuel_main != null
    ? `${state.fuel_main.toFixed(1)} / ${(state.fuel_capacity || 0).toFixed(0)} t` : "—";

  const cargoPct = state.cargo_capacity > 0 ? Math.min(100, (state.cargo_tons / state.cargo_capacity) * 100) : 0;
  $("fp-cargo-fill").style.width = cargoPct + "%";
  $("fp-cargo").textContent = state.cargo_tons != null
    ? `${Math.round(state.cargo_tons)} / ${state.cargo_capacity || 0} t` : "—";

  $("fp-credits").textContent = state.credits != null ? shortCr(state.credits) + " cr" : "—";
  const legal = $("fp-legal");
  legal.textContent = state.legal_state || "—";
  legal.style.color = state.legal_state && state.legal_state !== "Clean" ? "var(--bad)" : "var(--good)";
  const ex = state.exploration || {};
  $("fp-explo").textContent = ex.count ? "≈" + shortCr(ex.total) + " cr" : "—";
  const vault = (state.bio || {}).vault || {};
  $("fp-bio").textContent = (vault.items || []).length ? "≈" + shortCr(vault.total) + " cr" : "—";

  const jumps = (state.jump_history || []).slice(0, 4);
  const jl = $("fp-jumps");
  const sig = JSON.stringify(jumps);
  if (jl.dataset.sig !== sig) {
    jl.dataset.sig = sig;
    jl.innerHTML = "";
    for (const j of jumps) {
      const b = document.createElement("button");
      b.className = "fp-jump";
      b.innerHTML = `<span>${esc(j.system)}</span><span class="fp-jump-dist">${j.dist != null ? j.dist.toFixed(1) + " ly" : ""}</span>`;
      b.addEventListener("click", () => plotSystem(j.system));
      jl.appendChild(b);
    }
  }
}

function plotButton(system) {
  const btn = document.createElement("button");
  btn.className = "plotbtn";
  btn.type = "button";
  btn.title = "Plot route in game to " + system;
  btn.setAttribute("aria-label", btn.title);
  btn.textContent = "◎";
  btn.addEventListener("click", () => plotSystem(system));
  return btn;
}

/* One-tap "best loop from here" for the flight panel (F8). Uses the trade-route
   endpoint's built-in defaults (100 ly radius, ship jump range, current system). */
async function findBestLoop() {
  const btn = $("fp-bestloop");
  const status = $("fp-loop-status");
  const out = $("fp-loop-results");
  btn.disabled = true;
  status.classList.remove("error");
  status.textContent = "Finding the best loop from here… (~3–10s)";
  out.innerHTML = "";
  try {
    const resp = await fetch("/api/trade-route", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        mode: "loop",
        results: 3,
        min_supply: state && state.cargo_capacity ? state.cargo_capacity : undefined,
      }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || "Search failed");
    const loops = data.loops || [];
    if (!loops.length) {
      status.textContent = "No profitable loop found near you right now — try the Trade tab for wider settings.";
      return;
    }
    status.textContent = `Top ${loops.length} loop${loops.length > 1 ? "s" : ""} within 100 ly, best profit/hour:`;
    loops.forEach((l) => {
      const div = document.createElement("div");
      div.className = "fp-loop";
      div.innerHTML =
        `<div class="fp-loop-line"><b>${esc(l.a.station)}</b> <span class="dim">${esc(l.a.system)}</span>` +
        `<span class="fp-loop-arrow">⇄</span>` +
        `<b>${esc(l.b.station)}</b> <span class="dim">${esc(l.b.system)}</span></div>` +
        `<div class="fp-loop-sub">` +
        (l.profit_per_hour != null ? `<b class="good">+${fmtNum(l.profit_per_hour)} cr/hr</b>` : `<b class="good">+${fmtNum(l.profit)} cr/trip</b>`) +
        ` · +${fmtNum(l.profit)} cr/trip · ${l.distance} ly apart · start ${l.a.from_player} ly away</div>`;
      const line = div.querySelector(".fp-loop-line");
      line.appendChild(plotButton(l.a.system));
      line.appendChild(plotButton(l.b.system));
      out.appendChild(div);
    });
    speak(`Best loop found. ${loops[0].a.station} to ${loops[0].b.station}.`);
  } catch (err) {
    status.classList.add("error");
    status.textContent = String(err.message || err);
  } finally {
    btn.disabled = false;
  }
}

/* ---------- route progress tracking (F3) ---------- */

function sysEq(a, b) {
  return (a || "").trim().toLowerCase() === (b || "").trim().toLowerCase();
}

function saveActiveRoute() {
  if (activeRoute) localStorage.setItem("activeRoute", JSON.stringify(activeRoute));
  else localStorage.removeItem("activeRoute");
}

function trackRoute(kind, label, waypoints) {
  waypoints = (waypoints || []).filter((w) => w && w.system);
  if (!waypoints.length) return;
  activeRoute = { kind, label, waypoints, index: 0 };
  // If we're already sitting at an early waypoint, start from there.
  syncRouteToPosition();
  saveActiveRoute();
  renderRouteProgress();
  const wrap = $("route-progress");
  if (wrap) wrap.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function advanceRoute(delta) {
  if (!activeRoute) return;
  activeRoute.index = Math.max(0, Math.min(activeRoute.waypoints.length, activeRoute.index + delta));
  saveActiveRoute();
  renderRouteProgress();
}

function stopRoute() {
  activeRoute = null;
  saveActiveRoute();
  renderRouteProgress();
}

/* Advance the cursor past any waypoint whose system matches where we are now.
   Returns true if the index moved. */
function syncRouteToPosition() {
  if (!activeRoute || !state || !state.system) return false;
  let reached = -1;
  activeRoute.waypoints.forEach((w, i) => {
    if (i >= activeRoute.index && sysEq(w.system, state.system)) reached = i;
  });
  if (reached >= 0 && reached + 1 > activeRoute.index) {
    activeRoute.index = reached + 1;
    const total = activeRoute.waypoints.length;
    if (activeRoute.index >= total) speak("Route complete.");
    else speak(`Waypoint ${activeRoute.index} of ${total} reached. Next, ${activeRoute.waypoints[activeRoute.index].system}.`);
    return true;
  }
  return false;
}

function renderRouteProgress() {
  const wrap = $("route-progress");
  if (!wrap) return;
  if (!activeRoute) {
    wrap.classList.add("hidden");
    wrap.innerHTML = "";
    return;
  }
  const wps = activeRoute.waypoints;
  const total = wps.length;
  const done = Math.min(activeRoute.index, total);
  const complete = done >= total;
  const target = complete ? null : wps[done];
  const pct = total ? Math.round((done / total) * 100) : 0;

  wrap.classList.remove("hidden");
  wrap.innerHTML =
    `<div class="rp-main">` +
    `<span class="rp-badge">◈ ROUTE</span>` +
    `<span class="rp-label">${esc(activeRoute.label)}</span>` +
    `<span class="rp-count">${done}/${total}${complete ? " · done" : ""}</span>` +
    (target
      ? `<span class="rp-next">NEXT <b>${esc(target.system)}</b>${target.note ? ` <span class="dim">${esc(target.note)}</span>` : ""}</span>`
      : `<span class="rp-next rp-done">Arrived — route complete 🎉</span>`) +
    `</div>` +
    `<div class="rp-bar"><div style="width:${pct}%"></div></div>`;

  const main = wrap.querySelector(".rp-main");
  if (target) {
    main.insertBefore(plotButton(target.system), main.querySelector(".rp-next").nextSibling);
    const skip = document.createElement("button");
    skip.className = "plotbtn rp-skip";
    skip.textContent = "✓ done";
    skip.title = "Mark this waypoint reached";
    skip.addEventListener("click", () => advanceRoute(1));
    main.appendChild(skip);
  }
  if (done > 0 && !complete) {
    const back = document.createElement("button");
    back.className = "plotbtn rp-back";
    back.textContent = "↩";
    back.title = "Step back one waypoint";
    back.addEventListener("click", () => advanceRoute(-1));
    main.appendChild(back);
  }
  const stop = document.createElement("button");
  stop.className = "copy rp-stop";
  stop.textContent = "✕";
  stop.title = "Stop tracking this route";
  stop.setAttribute("aria-label", "Stop tracking route");
  stop.addEventListener("click", stopRoute);
  main.appendChild(stop);
}

/* A small "track this route" button for a list of waypoint systems. */
function trackButton(kind, label, waypointsFn) {
  const btn = document.createElement("button");
  btn.className = "plotbtn trackbtn";
  btn.type = "button";
  btn.textContent = "◈ TRACK";
  btn.title = "Follow this route step by step (marks your progress as you jump)";
  btn.addEventListener("click", () => trackRoute(kind, label, waypointsFn()));
  return btn;
}

/* ---------- rendering ---------- */

function render() {
  if (!state) return;

  $("commander").textContent = state.commander ? "CMDR " + state.commander : "—";
  const shipBits = [state.ship_name, state.ship_type].filter(Boolean);
  $("ship").textContent = shipBits.length ? shipBits.join(" · ") : "—";

  $("system").textContent = state.system || "Unknown";

  const stationStatus = $("station-status");
  const stationCopy = $("station-copy");
  if (state.docked && state.station) {
    let txt = "Docked at " + state.station;
    if (state.station_type) txt += " (" + state.station_type + ")";
    if (state.dist_from_star_ls != null) txt += " · " + Math.round(state.dist_from_star_ls) + " ls";
    stationStatus.textContent = txt;
    stationCopy.classList.remove("hidden");
  } else {
    stationStatus.textContent = state.body && state.body !== state.system
      ? "In space near " + state.body
      : "In space";
    stationCopy.classList.add("hidden");
  }

  $("destination-row").textContent = state.destination ? "Destination: " + state.destination : "";

  $("credits").textContent = fmtCr(state.credits);
  const fuel = state.fuel_main == null ? "—"
    : state.fuel_main.toFixed(1) + (state.fuel_capacity ? " / " + state.fuel_capacity.toFixed(0) : "") + " t";
  $("fuel").textContent = fuel;
  $("cargo").textContent = (state.cargo_tons != null ? Math.round(state.cargo_tons) : "—")
    + (state.cargo_capacity ? " / " + state.cargo_capacity : "") + " t";
  $("legal").textContent = state.legal_state || "—";

  renderBanner();
  renderLinks();
  renderMarket();
  renderJumps();
  renderCargo();
  renderBio();
  renderColonisation();
  renderSession(state.session);
  renderMissions(state.missions);
  renderMaterials(state.materials);
  if (syncRouteToPosition()) saveActiveRoute();
  renderRouteProgress();
  renderPanel();
  seedRouteForm();
}

/* ---------- small DOM helpers ---------- */

function setText(id, txt) {
  const el = $(id);
  if (el) el.textContent = txt;
}

function colorSign(id, v) {
  const el = $(id);
  if (el) el.style.color = v == null ? "" : (v >= 0 ? "var(--good)" : "var(--bad)");
}

function fmtDuration(secs) {
  if (secs == null || secs < 0) return "—";
  const h = Math.floor(secs / 3600), m = Math.floor((secs % 3600) / 60);
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m`;
  return `${Math.floor(secs)}s`;
}

function signedCr(n) {
  if (n == null) return "—";
  return (n >= 0 ? "+" : "−") + shortCr(Math.abs(n)) + " cr";
}

/* ---------- live session tracker (F1) ---------- */

function renderSession(sess) {
  sess = sess || {};
  const has = sess.start_ts != null;
  const dur = has ? Math.max(0, Date.now() / 1000 - sess.start_ts) : null;
  const earned = has ? sess.earned : null;
  // Ignore cr/hr for the first couple of minutes so it doesn't read as ±millions.
  const crhr = (has && dur > 120 && earned != null) ? earned / (dur / 3600) : null;

  const earnedTxt = signedCr(earned);
  const crhrTxt = crhr == null ? "—" : (crhr >= 0 ? "+" : "−") + shortCr(Math.abs(crhr)) + " cr/hr";
  const jumpsTxt = has ? String(sess.jumps || 0) : "—";
  const lyTxt = has ? fmtNum(sess.ly || 0) + " ly" : "—";
  const durTxt = dur != null ? fmtDuration(dur) : "";

  // Flight-panel tiles
  setText("fp-sess-earned", earnedTxt);
  setText("fp-sess-crhr", crhrTxt);
  setText("fp-sess-jumps", jumpsTxt);
  setText("fp-sess-ly", lyTxt);
  setText("fp-sess-since", durTxt);
  colorSign("fp-sess-earned", earned);

  // Analytics session card (live parts; trade profit/tons filled by loadAnalytics)
  setText("session-earned", earnedTxt);
  setText("session-crhr", crhrTxt);
  setText("session-duration", durTxt || "—");
  setText("session-jumps", jumpsTxt);
  setText("session-ly", lyTxt);
  setText("session-since", durTxt ? "· " + durTxt : "");
  colorSign("session-earned", earned);
  colorSign("session-crhr", crhr);
}

/* ---------- earnings breakdown (F2) ---------- */

const EARNINGS_META = {
  trade: ["Trade", "#6fbf73"],
  mission: ["Missions", "#e0a54a"],
  exploration: ["Exploration", "#5aa9e6"],
  exobiology: ["Exobiology", "#3fb6a8"],
  bounty: ["Bounties & bonds", "#e05d5d"],
  other: ["Other", "#8a8f98"],
};

function renderEarnings(e) {
  const box = $("earnings-breakdown");
  if (!box) return;
  e = e || {};
  const cats = Object.keys(EARNINGS_META).filter((k) => (e[k] || 0) > 0);
  cats.sort((a, b) => (e[b] || 0) - (e[a] || 0));
  const total = cats.reduce((s, k) => s + (e[k] || 0), 0);
  $("earnings-empty").classList.toggle("hidden", cats.length > 0);
  box.innerHTML = "";
  for (const k of cats) {
    const [label, color] = EARNINGS_META[k];
    const val = e[k] || 0;
    const pct = total ? (val / total) * 100 : 0;
    const row = document.createElement("div");
    row.className = "earn-row";
    row.innerHTML =
      `<div class="earn-head">` +
      `<span class="earn-dot" style="background:${color}"></span>` +
      `<span class="earn-label">${label}</span>` +
      `<span class="earn-val">+${fmtNum(val)} cr</span>` +
      `<span class="earn-pct">${pct.toFixed(0)}%</span></div>` +
      `<div class="earn-bar"><div style="width:${pct}%;background:${color}"></div></div>`;
    box.appendChild(row);
  }
}

/* ---------- active missions (F5) ---------- */

function renderMissions(missions) {
  missions = missions || [];
  const list = $("missions-list");
  $("missions-empty").classList.toggle("hidden", missions.length > 0);
  $("missions-count").textContent = missions.length ? missions.length + " active" : "";

  const cargo = {};
  for (const c of state.cargo_inventory || []) cargo[(c.symbol || "").toLowerCase()] = c.count;
  // Re-render on mission/cargo change, and once a minute so countdowns tick.
  const sig = JSON.stringify(missions) + "|" + JSON.stringify(state.cargo_inventory || [])
    + "|" + Math.floor(Date.now() / 60000);
  if (list.dataset.sig === sig) return;
  list.dataset.sig = sig;
  list.innerHTML = "";

  for (const m of missions) {
    const rem = m.expiry_ts ? m.expiry_ts - Date.now() / 1000 : null;
    const expired = rem != null && rem <= 0;
    const soon = rem != null && rem > 0 && rem < 3600;
    const need = m.commodity_symbol ? (m.count || 0) : 0;
    const have = need ? (cargo[m.commodity_symbol] || 0) : 0;
    const short = need && have < need;

    const div = document.createElement("div");
    div.className = "mission";
    div.innerHTML =
      `<div class="mission-top">` +
      `<span class="mission-kind kind-${esc(m.kind)}">${esc(m.kind)}</span>` +
      `<b>${esc(m.name)}</b>` +
      `<span class="mission-reward">+${fmtNum(m.reward)} cr</span>` +
      `</div>` +
      `<div class="mission-sub">` +
      (m.dest_system ? `<span class="arrow">→</span> ${esc(m.dest_station || "?")}, <b>${esc(m.dest_system)}</b> ` : "") +
      (m.commodity ? `· <span class="${short ? "warn" : ""}">${short ? "⚠ " : ""}${fmtNum(have)}/${fmtNum(need)} ${esc(m.commodity)}</span> ` : "") +
      (m.faction ? `· ${esc(m.faction)} ` : "") +
      (rem != null ? `· <span class="${expired ? "warn" : soon ? "soon" : "dim"}">${expired ? "EXPIRED" : "expires " + fmtDuration(rem)}</span>` : "") +
      `</div>`;
    if (m.dest_system) {
      const top = div.querySelector(".mission-top");
      top.insertBefore(plotButton(m.dest_system), top.querySelector(".mission-reward"));
    }
    list.appendChild(div);
  }
}

/* ---------- engineering materials (F6) ---------- */

function renderMaterials(mats) {
  mats = mats || {};
  const groups = $("materials-groups");
  const total = mats.total || 0;
  $("materials-empty").classList.toggle("hidden", total > 0);
  $("materials-total").textContent = total ? total + " items" : "";
  const sig = JSON.stringify(mats);
  if (groups.dataset.sig === sig) return;
  groups.dataset.sig = sig;
  groups.innerHTML = "";
  for (const cat of ["raw", "manufactured", "encoded"]) {
    const items = mats[cat] || [];
    if (!items.length) continue;
    const col = document.createElement("div");
    col.className = "mat-group";
    col.innerHTML = `<div class="label">${cat.toUpperCase()} <span class="dim">${items.length}</span></div>`;
    const ul = document.createElement("ul");
    ul.className = "cargo-list";
    for (const it of items) {
      const li = document.createElement("li");
      li.innerHTML = `<span>${esc(it.name)}</span><span class="count">${it.count}</span>`;
      ul.appendChild(li);
    }
    col.appendChild(ul);
    groups.appendChild(col);
  }
}

let lowFuelSpoken = false;

function renderBanner() {
  const banner = $("banner");
  const lowFuel = state.fuel_main != null && state.fuel_capacity > 0 &&
    !state.docked && state.fuel_main / state.fuel_capacity < 0.25;
  if (lowFuel && !lowFuelSpoken) { speak("Warning. Low fuel."); lowFuelSpoken = true; }
  if (!lowFuel) lowFuelSpoken = false;
  if (state.journal_dir_found === false) {
    banner.textContent = "Elite Dangerous journal folder not found - set the ED_JOURNAL_DIR environment variable.";
    banner.classList.remove("hidden");
  } else if (!state.system) {
    banner.textContent = "Waiting for journal data - start Elite Dangerous (or play a bit) and this will fill in.";
    banner.classList.remove("hidden");
  } else if (lowFuel) {
    banner.textContent = `⚠ LOW FUEL: ${state.fuel_main.toFixed(1)} / ${state.fuel_capacity.toFixed(0)} t — find a scoopable star (K G B F O A M) or a station soon.`;
    banner.classList.remove("hidden");
  } else {
    banner.classList.add("hidden");
  }
}

function renderLinks() {
  const row = $("links");
  const links = state.links || [];
  const sig = JSON.stringify(links);
  if (row.dataset.sig === sig) return;
  row.dataset.sig = sig;
  row.innerHTML = "";
  for (const l of links) {
    const a = document.createElement("a");
    a.href = l.url;
    a.target = "_blank";
    a.rel = "noopener";
    a.textContent = l.label;
    a.addEventListener("click", (ev) => {
      if (openExternal(l.url, l.label)) ev.preventDefault();
    });
    row.appendChild(a);
  }
}

function renderMarket() {
  const market = state.market;
  const title = $("market-title");
  const tbody = $("market-table").querySelector("tbody");
  const empty = $("market-empty");

  if (!market || !market.items || !market.items.length) {
    title.textContent = "STATION MARKET";
    tbody.innerHTML = "";
    empty.classList.remove("hidden");
    return;
  }
  empty.classList.add("hidden");
  title.textContent = market.is_current_station
    ? "STATION MARKET — " + market.station
    : "LAST VISITED MARKET — " + (market.station || "?");

  const filter = $("market-filter").value.trim().toLowerCase();
  let items = market.items;
  if (filter) {
    items = items.filter((i) =>
      (i.name || "").toLowerCase().includes(filter) ||
      (i.category || "").toLowerCase().includes(filter));
  }
  const { key, dir } = marketSort;
  items = [...items].sort((a, b) => {
    const av = a[key] ?? 0, bv = b[key] ?? 0;
    return (typeof av === "string" ? av.localeCompare(bv) : av - bv) * dir;
  });

  tbody.innerHTML = "";
  for (const i of items) {
    const tr = document.createElement("tr");
    tr.innerHTML =
      `<td>${esc(i.name)}<div class="sub">${esc(i.category)}</div></td>` +
      `<td class="num">${i.sell ? i.sell.toLocaleString() : "—"}${trendArrow(i.sell, i.prev_sell)}</td>` +
      `<td class="num">${i.buy ? i.buy.toLocaleString() : "—"}${trendArrow(i.buy, i.prev_buy)}</td>` +
      `<td class="num">${i.demand ? i.demand.toLocaleString() : "—"}</td>` +
      `<td class="num">${i.stock ? i.stock.toLocaleString() : "—"}</td>`;
    tbody.appendChild(tr);
  }
}

/* Up/down arrow comparing a live price to the last price the DB recorded. */
function trendArrow(cur, prev) {
  if (prev == null || !cur || cur === prev) return "";
  const up = cur > prev;
  const pct = prev ? Math.round((Math.abs(cur - prev) / prev) * 100) : 0;
  const title = `was ${prev.toLocaleString()} (${up ? "+" : "−"}${pct}% since last report)`;
  return ` <span class="trend ${up ? "up" : "down"}" title="${title}">${up ? "▲" : "▼"}</span>`;
}

function renderJumps() {
  const ul = $("jumps");
  const jumps = state.jump_history || [];
  $("jumps-empty").classList.toggle("hidden", jumps.length > 0);
  const sig = JSON.stringify(jumps);
  if (ul.dataset.sig === sig) return;
  ul.dataset.sig = sig;
  ul.innerHTML = "";
  for (const j of jumps) {
    const li = document.createElement("li");
    const when = j.timestamp ? new Date(j.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "";
    li.innerHTML =
      `<span class="sysname">${esc(j.system)}</span>` +
      `<span class="dist">${j.dist != null ? j.dist.toFixed(1) + " ly" : ""}</span>` +
      `<span class="when">${when}</span>`;
    const btn = document.createElement("button");
    btn.className = "copy";
    btn.title = "Copy system name";
    btn.textContent = "⧉";
    btn.addEventListener("click", () => copyText(j.system, btn));
    li.appendChild(btn);
    li.appendChild(plotButton(j.system));
    ul.appendChild(li);
  }
}

function renderCargo() {
  const ul = $("cargo-list");
  const inv = state.cargo_inventory || [];
  $("cargo-empty").classList.toggle("hidden", inv.length > 0);
  const sig = JSON.stringify(inv);
  if (ul.dataset.sig === sig) return;
  ul.dataset.sig = sig;
  ul.innerHTML = "";
  for (const c of inv) {
    const li = document.createElement("li");
    li.innerHTML = `<span>${esc(c.name)}</span><span class="count">${c.count} t</span>`;
    ul.appendChild(li);
  }
}

function fmtRange(lo, hi) {
  if (lo == null) return "?";
  const m = (n) => (n / 1e6).toFixed(1).replace(/\.0$/, "") + "M";
  return lo === hi ? m(lo) : m(lo) + "–" + m(hi);
}

function renderBio() {
  const bio = state.bio || {};

  // Exploration data card
  const ex = state.exploration || { total: 0, count: 0, top: [] };
  $("explo-total").textContent = ex.count ? "≈" + fmtNum(ex.total) + " cr" : "";
  $("explo-summary").textContent = ex.count
    ? `${ex.count} bodies scanned · ${ex.mapped} mapped · ${ex.firsts} first discoveries`
    : "";
  $("explo-empty").classList.toggle("hidden", ex.count > 0);
  const exUl = $("explo-top");
  const exSig = JSON.stringify(ex.top);
  if (exUl.dataset.sig !== exSig) {
    exUl.dataset.sig = exSig;
    exUl.innerHTML = "";
    for (const b of ex.top || []) {
      const li = document.createElement("li");
      li.innerHTML = `<span>${esc(b.body)} <span class="sub">${esc(b.class || "")}` +
        `${b.mapped ? " · mapped" : ""}${b.first ? " · first discovery" : ""}</span></span>` +
        `<span class="count">≈${fmtNum(b.value)} cr</span>`;
      exUl.appendChild(li);
    }
  }

  // Sampling progress
  const sampCard = $("bio-sampling-card");
  const samp = bio.sampling;
  if (samp) {
    sampCard.classList.remove("hidden");
    const pct = Math.round(100 * (samp.progress || 0) / 3);
    $("bio-sampling").innerHTML =
      `<div class="route-line"><b>${esc(samp.species)}</b>` +
      (samp.variant ? `<span class="dim">${esc(samp.variant)}</span>` : "") +
      `<span class="profit">${samp.value != null ? "+" + fmtNum(samp.value) + " cr" : ""}</span></div>` +
      `<div class="commodities">sample ${samp.progress}/3` +
      (samp.colony_m ? ` · move ≥ ${samp.colony_m} m between samples` : "") + `</div>` +
      `<div class="seedbar"><div style="height:100%;width:${pct}%;background:var(--good)"></div></div>`;
  } else {
    sampCard.classList.add("hidden");
  }

  // Vault
  const vault = bio.vault || { items: [], total: 0 };
  $("bio-vault-total").textContent = vault.items.length ? fmtNum(vault.total) + " cr" : "";
  $("bio-vault-empty").classList.toggle("hidden", vault.items.length > 0);
  const ul = $("bio-vault");
  const vsig = JSON.stringify(vault.items);
  if (ul.dataset.sig !== vsig) {
    ul.dataset.sig = vsig;
    ul.innerHTML = "";
    for (const s of vault.items) {
      const li = document.createElement("li");
      li.innerHTML = `<span>${esc(s.species)}${s.body ? ` <span class="sub">${esc(s.body)}</span>` : ""}</span>` +
        `<span class="count">+${fmtNum(s.value)} cr</span>`;
      ul.appendChild(li);
    }
  }

  // System signals table
  const rows = bio.system_signals || [];
  $("bio-empty").classList.toggle("hidden", rows.length > 0);
  const table = $("bio-table");
  table.classList.toggle("hidden", rows.length === 0);
  const tbody = table.querySelector("tbody");
  const bsig = JSON.stringify(rows);
  if (tbody.dataset.sig === bsig) return;
  tbody.dataset.sig = bsig;
  tbody.innerHTML = "";
  for (const b of rows) {
    const known = (b.genuses || []).map((g) =>
      `<div>${esc(g.name)} <span class="sub">${fmtRange(g.min_value, g.max_value)}` +
      (g.colony_m ? ` · ${g.colony_m} m` : "") + `</span></div>`
    ).join("");
    // Community-mapped genuses (Spansh) for bodies you haven't DSS'd yourself.
    const community = !known && (b.community_genuses || []).length
      ? b.community_genuses.map((g) =>
          `<div class="community">◇ ${esc(g.name)} <span class="sub">${fmtRange(g.min_value, g.max_value)}` +
          (g.colony_m ? ` · ${g.colony_m} m` : "") + ` · community</span></div>`
        ).join("")
      : "";
    const predicted = !known && !community && (b.predicted || []).length
      ? `<div class="sub">predicted: ${(b.predicted).map((g) =>
          `${esc(g.name)} (${fmtRange(g.min_value, g.max_value)})`).join(", ")}</div>`
      : "";
    const genuses = known || community || predicted || `<span class="dim">${b.count ? "not mapped yet" : ""}</span>`;
    const tr = document.createElement("tr");
    tr.innerHTML =
      `<td>${esc(b.body)}${b.landable === false ? ' <span class="sub">not landable</span>' : ""}` +
      `${b.source === "community" ? ' <span class="sub">◇ community</span>' : ""}</td>` +
      `<td class="num">${b.count || "?"}</td>` +
      `<td>${genuses}</td>` +
      `<td>${esc(b.planet_class || "?")}<div class="sub">${esc(b.atmosphere || "")}</div></td>` +
      `<td class="num">${b.gravity_g != null ? b.gravity_g + " g" : "?"}</td>` +
      `<td class="num">${b.temp_k != null ? b.temp_k + " K" : "?"}</td>`;
    tbody.appendChild(tr);
  }
}

function seedRouteForm() {
  if (routeFormTouched) return;
  if (state.credits != null && !$("rf-capital").value) $("rf-capital").value = state.credits;
  if (state.cargo_capacity != null && !$("rf-cargo").value) $("rf-cargo").value = state.cargo_capacity;
  if (state.max_jump_range != null && !$("rf-hop").value) $("rf-hop").value = state.max_jump_range.toFixed(1);
  if (state.max_jump_range != null && !$("rf-jumprange").value) $("rf-jumprange").value = state.max_jump_range.toFixed(1);
  if (state.max_jump_range != null && !$("rr-range").value) $("rr-range").value = state.max_jump_range.toFixed(1);
  if (state.max_jump_range != null && !$("nr-range").value) $("nr-range").value = state.max_jump_range.toFixed(1);
  // Default min supply to the hold size so listed routes can actually fill it.
  if (state.cargo_capacity && !$("rf-minsupply").value) $("rf-minsupply").value = state.cargo_capacity;
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* ---------- trade routes ---------- */

async function findRoutes(ev) {
  ev.preventDefault();
  const go = $("rf-go");
  const status = $("route-status");
  const results = $("route-results");
  go.disabled = true;
  status.classList.remove("error");
  status.textContent = "Asking Spansh for routes… (can take ~10-30s)";
  results.innerHTML = "";
  try {
    const mode = $("rf-mode").value;
    const resp = await fetch("/api/trade-route", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        mode,
        capital: Number($("rf-capital").value) || undefined,
        max_cargo: Number($("rf-cargo").value) || undefined,
        radius: Number($("rf-radius").value) || undefined,
        max_leg: Number($("rf-maxleg").value) || undefined,
        jump_range: Number($("rf-jumprange").value) || undefined,
        results: Number($("rf-results").value) || undefined,
        min_supply: Number($("rf-minsupply").value) || undefined,
        max_hop_distance: Number($("rf-hop").value) || undefined,
        max_hops: Number($("rf-hops").value) || undefined,
        max_system_distance: Number($("rf-lsdist").value) || undefined,
        max_price_age_days: Number($("rf-age").value) || undefined,
        requires_large_pad: $("rf-largepad").checked,
      }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || "Route request failed");
    const src = data.source === "local" ? "local database" : "Spansh API (local DB not built yet)";
    if (data.mode === "loop") {
      renderLoops(data.loops || []);
      status.textContent = (data.loops || []).length
        ? `Best ${data.loops.length} loops within ${$("rf-radius").value} ly of ${state.system}, ranked by estimated profit/hour.`
        : "No profitable loop found with those settings.";
    } else {
      renderRoutes(data.hops || []);
      status.textContent = data.hops && data.hops.length
        ? `Route found (${data.hops.length} hop${data.hops.length > 1 ? "s" : ""}) from ${state.system} via ${src}.`
        : `No profitable route for those settings (via ${src}).`;
    }
  } catch (err) {
    status.classList.add("error");
    status.textContent = String(err.message || err);
  } finally {
    go.disabled = false;
  }
}

function commodityTableHtml(commodities) {
  const rows = (commodities || []).map((c) => {
    const unit = (c.sell_price != null && c.buy_price != null) ? c.sell_price - c.buy_price : null;
    const line = c.profit != null ? c.profit : (unit != null && c.amount != null ? unit * c.amount : null);
    const lowStock = c.supply != null && c.amount != null && c.supply < c.amount * 2;
    return `<tr>` +
      `<td>${esc(c.name)}</td>` +
      `<td class="num">${fmtNum(c.amount)}</td>` +
      `<td class="num">${fmtNum(c.buy_price)}</td>` +
      `<td class="num${lowStock ? " warn" : ""}">${fmtNum(c.supply)}</td>` +
      `<td class="num">${fmtNum(c.sell_price)}</td>` +
      `<td class="num">${fmtNum(c.demand)}</td>` +
      `<td class="num">${unit != null ? "+" + fmtNum(unit) : "?"}</td>` +
      `<td class="num profit-cell">+${fmtNum(line)}</td>` +
      `</tr>`;
  }).join("");
  if (!rows) return "";
  return `<table class="hop-table">` +
    `<thead><tr><th>Commodity</th><th class="num">Units</th><th class="num">Buy</th>` +
    `<th class="num">Stock</th><th class="num">Sell</th><th class="num">Demand</th>` +
    `<th class="num">cr/unit</th><th class="num">Total</th></tr></thead>` +
    `<tbody>${rows}</tbody></table>`;
}

/* ---------- route watches & alerts ---------- */

async function watchLoop(loop, btn) {
  if (window.Notification && Notification.permission === "default") {
    try { await Notification.requestPermission(); } catch (e) {}
  }
  try {
    const resp = await fetch("/api/watch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ loop }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || "Watch failed");
    btn.textContent = "WATCHING";
    btn.disabled = true;
    pollAlerts(true);
  } catch (err) {
    alert(String(err.message || err));
  }
}

let lastAlertTs = null;
let alertPollTimer = null;

async function pollAlerts() {
  if (alertPollTimer) clearTimeout(alertPollTimer);
  try {
    const resp = await fetch("/api/alerts", { cache: "no-store" });
    if (resp.ok) {
      const data = await resp.json();
      renderWatches(data.watches || []);
      renderAlerts(data.alerts || []);
    }
  } catch (e) { /* retry next tick */ }
  alertPollTimer = setTimeout(pollAlerts, 15000);
}

function renderWatches(watches) {
  const el = $("watch-list");
  el.innerHTML = "";
  for (const w of watches) {
    const chip = document.createElement("span");
    chip.className = "watch-chip";
    chip.append(`👁 ${w.label} `);
    const x = document.createElement("button");
    x.className = "copy";
    x.textContent = "×";
    x.title = "Stop watching";
    x.addEventListener("click", async () => {
      await fetch("/api/watch/remove", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: w.id }),
      });
      pollAlerts();
    });
    chip.appendChild(x);
    el.appendChild(chip);
  }
}

function renderAlerts(alerts) {
  const strip = $("alert-strip");
  if (!alerts.length) {
    strip.classList.add("hidden");
    return;
  }
  const newest = alerts[0];
  if (newest.ts !== lastAlertTs) {
    if (lastAlertTs !== null && window.Notification && Notification.permission === "granted") {
      try { new Notification("Elite Trader route alert", { body: newest.text }); } catch (e) {}
    }
    lastAlertTs = newest.ts;
  }
  strip.classList.remove("hidden");
  strip.innerHTML = "";
  for (const a of alerts.slice(0, 3)) {
    const row = document.createElement("div");
    row.textContent = `⚠ ${a.text}`;
    strip.appendChild(row);
  }
  const dismiss = document.createElement("button");
  dismiss.className = "copy";
  dismiss.textContent = "dismiss";
  dismiss.addEventListener("click", async () => {
    await fetch("/api/alerts/clear", { method: "POST" });
    pollAlerts();
  });
  strip.appendChild(dismiss);
}

function renderLoops(loops) {
  const results = $("route-results");
  results.innerHTML = "";
  loops.forEach((l, i) => {
    const div = document.createElement("div");
    div.className = "hop";
    div.style.setProperty("--i", i);
    const tons = [...l.outbound.commodities, ...l.inbound.commodities].reduce((a, c) => a + (c.amount || 0), 0);
    div.innerHTML =
      `<div class="route-line">` +
      `<span class="dim">#${i + 1}</span>` +
      `<b>${esc(l.a.station)}</b><span class="dim">${esc(l.a.system)}</span>` +
      `<span class="arrow">⇄</span>` +
      `<b>${esc(l.b.station)}</b><span class="dim">${esc(l.b.system)}</span>` +
      `<span class="profit">${l.profit_per_hour != null ? "+" + fmtNum(l.profit_per_hour) + " cr/hr" : "+" + fmtNum(l.profit) + " cr / trip"}</span>` +
      `</div>` +
      `<div class="commodities">` +
      `+${fmtNum(l.profit)} cr / round trip` +
      (l.minutes_per_trip != null ? ` · ≈${l.minutes_per_trip} min/trip` : "") +
      ` · ${l.distance} ly apart · start ${l.a.from_player} ly from you` +
      ` · ${l.a.dist_ls != null ? fmtNum(l.a.dist_ls) : "?"} / ${l.b.dist_ls != null ? fmtNum(l.b.dist_ls) : "?"} ls to pads` +
      (tons ? ` · ${fmtNum(l.profit / tons)} cr/t moved` : "") +
      `</div>` +
      `<div class="leg-label">OUTBOUND <span class="profit-cell">+${fmtNum(l.outbound.profit)}</span></div>` +
      commodityTableHtml(l.outbound.commodities) +
      `<div class="leg-label">RETURN <span class="profit-cell">${l.inbound.commodities.length ? "+" + fmtNum(l.inbound.profit) : "fly back empty"}</span></div>` +
      commodityTableHtml(l.inbound.commodities);
    const line = div.querySelector(".route-line");
    const btnA = plotButton(l.a.system);
    const btnB = plotButton(l.b.system);
    const watchBtn = document.createElement("button");
    watchBtn.className = "plotbtn";
    watchBtn.textContent = "WATCH";
    watchBtn.title = "Alert me when this loop's prices/stock degrade (live EDDN)";
    watchBtn.addEventListener("click", () => watchLoop(l, watchBtn));
    line.insertBefore(btnA, line.querySelector(".profit"));
    line.insertBefore(btnB, line.querySelector(".profit"));
    line.insertBefore(watchBtn, line.querySelector(".profit"));
    results.appendChild(div);
  });
}

function renderRoutes(hops) {
  const results = $("route-results");
  results.innerHTML = "";
  if (!hops.length) return;

  // Route-wide totals for the summary bar.
  let totalProfit = 0, totalDist = 0, totalTons = 0, firstOutlay = 0;
  hops.forEach((h, i) => {
    totalProfit += h.profit || 0;
    totalDist += h.distance || 0;
    for (const c of h.commodities || []) {
      totalTons += c.amount || 0;
      if (i === 0) firstOutlay += (c.amount || 0) * (c.buy_price || 0);
    }
  });
  const summary = document.createElement("div");
  summary.className = "route-summary";
  summary.innerHTML =
    `<span class="profit">+${fmtNum(totalProfit)} cr total</span>` +
    `<span>${hops.length} hop${hops.length > 1 ? "s" : ""}</span>` +
    `<span>${totalDist.toFixed(1)} ly</span>` +
    `<span>${fmtNum(totalTons)} t moved</span>` +
    (totalTons ? `<span>${fmtNum(totalProfit / totalTons)} cr/t avg</span>` : "") +
    (firstOutlay ? `<span>needs ~${fmtNum(firstOutlay)} cr up front</span>` : "");
  if (hops.length > 1) {
    summary.appendChild(trackButton("chain", "Trade chain", () => {
      const wp = [];
      if (hops[0].from_system) wp.push({ system: hops[0].from_system, note: hops[0].from_station });
      for (const h of hops) if (h.to_system) wp.push({ system: h.to_system, note: h.to_station });
      return wp;
    }));
  }
  results.appendChild(summary);

  hops.forEach((h, i) => {
    const div = document.createElement("div");
    div.className = "hop";
    div.style.setProperty("--i", i);
    const tons = (h.commodities || []).reduce((a, c) => a + (c.amount || 0), 0);
    const outlay = (h.commodities || []).reduce((a, c) => a + (c.amount || 0) * (c.buy_price || 0), 0);

    div.innerHTML =
      `<div class="route-line">` +
      `<b>${esc(h.from_station)}</b><span class="dim">${esc(h.from_system)}</span>` +
      `<span class="arrow">➜</span>` +
      `<b>${esc(h.to_station)}</b><span class="dim">${esc(h.to_system)}</span>` +
      `<span class="profit">+${fmtNum(h.profit)} cr</span>` +
      `</div>` +
      commodityTableHtml(h.commodities) +
      `<div class="commodities">` +
      (h.distance != null ? `${Number(h.distance).toFixed(1)} ly jump` : "") +
      (h.to_dist_ls != null ? ` · ${fmtNum(h.to_dist_ls)} ls to station` : "") +
      (tons ? ` · ${fmtNum(h.profit / tons)} cr/t` : "") +
      (outlay ? ` · costs ${fmtNum(outlay)} cr to load` : "") +
      (h.cumulative_profit != null ? ` · total so far: ${fmtNum(h.cumulative_profit)} cr` : "") +
      `</div>`;
    if (h.to_system) {
      const line = div.querySelector(".route-line");
      line.insertBefore(plotButton(h.to_system), line.querySelector(".profit"));
    }
    results.appendChild(div);
  });
}

function fmtNum(n) {
  return n == null ? "?" : Math.round(n).toLocaleString();
}

/* ---------- commodity search ---------- */

async function loadCommodityList() {
  try {
    const resp = await fetch("/api/commodities");
    if (!resp.ok) return;
    const data = await resp.json();
    const dl = $("commodity-list");
    dl.innerHTML = "";
    for (const c of data.commodities || []) {
      const opt = document.createElement("option");
      opt.value = c.name;
      dl.appendChild(opt);
    }
    if (!dl.children.length) setTimeout(loadCommodityList, 30000); // DB not seeded yet
  } catch (e) { setTimeout(loadCommodityList, 30000); }
}

function ageText(epoch) {
  if (!epoch) return "?";
  const mins = Math.max(0, (Date.now() / 1000 - epoch) / 60);
  if (mins < 60) return Math.round(mins) + "m";
  if (mins < 48 * 60) return Math.round(mins / 60) + "h";
  return Math.round(mins / 1440) + "d";
}

async function searchCommodity(ev) {
  ev.preventDefault();
  const status = $("cs-status");
  const table = $("cs-table");
  const tbody = table.querySelector("tbody");
  const mode = $("cs-mode").value;
  status.classList.remove("error");
  status.textContent = "Searching…";
  try {
    const params = new URLSearchParams({
      q: $("cs-query").value.trim(),
      mode,
      radius: $("cs-radius").value || "50",
      min_units: $("cs-min").value || "1",
      large_pad: $("cs-largepad").checked ? "1" : "0",
    });
    const resp = await fetch("/api/commodity-search?" + params);
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || "Search failed");
    tbody.innerHTML = "";
    for (const r of data.results || []) {
      const tr = document.createElement("tr");
      const price = mode === "buy" ? r.buy_price : r.sell_price;
      const units = mode === "buy" ? r.supply : r.demand;
      tr.innerHTML =
        `<td>${esc(r.station)}${r.large_pad ? "" : ' <span class="sub">no L pad</span>'}</td>` +
        `<td>${esc(r.system)}</td>` +
        `<td class="num orange">${fmtNum(price)}</td>` +
        `<td class="num">${fmtNum(units)}</td>` +
        `<td class="num">${r.distance} ly</td>` +
        `<td class="num">${r.dist_ls != null ? fmtNum(r.dist_ls) + " ls" : "?"}</td>` +
        `<td class="num">${ageText(r.updated_at)}</td>`;
      const td = document.createElement("td");
      td.appendChild(plotButton(r.system));
      tr.appendChild(td);
      tbody.appendChild(tr);
    }
    table.classList.toggle("hidden", !(data.results || []).length);
    status.textContent = (data.results || []).length
      ? `${data.results.length} station(s) ${mode === "buy" ? "selling" : "buying"} ${data.commodity} within ${$("cs-radius").value} ly.`
      : `Nothing ${mode === "buy" ? "selling" : "buying"} ${data.commodity || "that"} nearby with those filters.`;
  } catch (err) {
    table.classList.add("hidden");
    status.classList.add("error");
    status.textContent = String(err.message || err);
  }
}

/* ---------- guides: road to riches + neutron ---------- */

async function planRiches(ev) {
  ev.preventDefault();
  const status = $("rr-status");
  const out = $("rr-results");
  const go = $("rr-go");
  go.disabled = true;
  status.classList.remove("error");
  status.textContent = "Asking Spansh for high-value bodies… (~10-30s)";
  out.innerHTML = "";
  try {
    const resp = await fetch("/api/riches", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        jump_range: Number($("rr-range").value) || undefined,
        radius: Number($("rr-radius").value) || undefined,
        min_value: Number($("rr-minvalue").value) || undefined,
        max_results: Number($("rr-max").value) || undefined,
        loop: $("rr-loop").checked,
      }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || "Request failed");
    const systems = (data.systems || []).filter((s) => (s.bodies || []).length);
    const total = systems.reduce((a, s) => a + (s.total_value || 0), 0);
    status.textContent = systems.length
      ? `${systems.length} systems in visit order · ≈${fmtNum(total)} cr if you map everything (first discovery/footfall pays more).`
      : "Nothing above the value threshold nearby — lower Min value or raise Radius.";
    if (systems.length) {
      status.append(" ");
      status.appendChild(trackButton("riches", "Road to Riches",
        () => systems.map((s) => ({ system: s.system, note: "≈" + fmtNum(s.total_value) + " cr" }))));
    }
    systems.forEach((s, i) => {
      const div = document.createElement("div");
      div.className = "hop";
      div.style.setProperty("--i", i);
      const bodies = (s.bodies || []).map((b) =>
        `<div>${esc(b.name)} <span class="sub">${esc(b.type || "?")}${b.terraformable ? " · terraformable" : ""}` +
        ` · ${b.dist_ls != null ? fmtNum(b.dist_ls) + " ls" : "?"} · ≈${fmtNum(b.map_value || b.scan_value)} cr</span></div>`
      ).join("");
      div.innerHTML =
        `<div class="route-line"><span class="dim">#${i + 1}</span><b>${esc(s.system)}</b>` +
        `<span class="profit">≈${fmtNum(s.total_value)} cr</span></div>` +
        `<div class="commodities">${bodies}</div>`;
      const line = div.querySelector(".route-line");
      const copyBtn = document.createElement("button");
      copyBtn.className = "copy"; copyBtn.textContent = "⧉"; copyBtn.title = "Copy system name";
      copyBtn.addEventListener("click", () => copyText(s.system, copyBtn));
      line.insertBefore(copyBtn, line.querySelector(".profit"));
      line.insertBefore(plotButton(s.system), line.querySelector(".profit"));
      out.appendChild(div);
    });
  } catch (err) {
    status.classList.add("error");
    status.textContent = String(err.message || err);
  } finally {
    go.disabled = false;
  }
}

async function planNeutron(ev) {
  ev.preventDefault();
  const status = $("nr-status");
  const table = $("nr-table");
  const tbody = table.querySelector("tbody");
  const go = $("nr-go");
  go.disabled = true;
  status.classList.remove("error");
  status.textContent = "Plotting neutron route… (~10-30s)";
  try {
    const resp = await fetch("/api/neutron", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        to: $("nr-to").value.trim(),
        jump_range: Number($("nr-range").value) || undefined,
        efficiency: Number($("nr-eff").value) || undefined,
      }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || "Request failed");
    const wps = data.waypoints || [];
    status.textContent = `${data.total_jumps} jumps total across ${wps.length} waypoints — plot each waypoint as you reach the previous one.`;
    if (wps.length) {
      status.append(" ");
      status.appendChild(trackButton("neutron", "Neutron: " + ($("nr-to").value.trim() || "route"),
        () => wps.map((w) => ({ system: w.system, note: w.neutron ? "☄ neutron" : "" }))));
    }
    tbody.innerHTML = "";
    wps.forEach((w, i) => {
      const tr = document.createElement("tr");
      tr.innerHTML =
        `<td>${i + 1}</td>` +
        `<td>${esc(w.system)}${w.neutron ? ' <span class="orange">☄ neutron</span>' : ""}</td>` +
        `<td class="num">${w.distance_jumped != null ? Number(w.distance_jumped).toFixed(1) : ""}</td>` +
        `<td class="num">${w.distance_left != null ? Number(w.distance_left).toFixed(0) : ""}</td>` +
        `<td class="num">${w.jumps ?? ""}</td>`;
      const td = document.createElement("td");
      const copyBtn = document.createElement("button");
      copyBtn.className = "copy"; copyBtn.textContent = "⧉"; copyBtn.title = "Copy system name";
      copyBtn.addEventListener("click", () => copyText(w.system, copyBtn));
      td.appendChild(copyBtn);
      td.appendChild(plotButton(w.system));
      tr.appendChild(td);
      tbody.appendChild(tr);
    });
    table.classList.toggle("hidden", wps.length === 0);
  } catch (err) {
    status.classList.add("error");
    status.textContent = String(err.message || err);
  } finally {
    go.disabled = false;
  }
}

/* ---------- outfitting & shipyard search ---------- */

async function searchStations(ev) {
  ev.preventDefault();
  const status = $("os-status");
  const table = $("os-table");
  const tbody = table.querySelector("tbody");
  const go = $("os-go");
  go.disabled = true;
  status.classList.remove("error");
  status.textContent = "Searching…";
  try {
    const params = new URLSearchParams({ q: $("os-query").value.trim(), type: $("os-type").value });
    const resp = await fetch("/api/station-search?" + params);
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || "Search failed");
    const results = data.results || [];
    status.textContent = results.length
      ? `${results.length} nearest station(s) with "${$("os-query").value.trim()}":`
      : "Nothing found — check the spelling (e.g. '6A Fuel Scoop', 'Python Mk II').";
    tbody.innerHTML = "";
    for (const r of results) {
      const tr = document.createElement("tr");
      tr.innerHTML =
        `<td>${esc(r.station)}<div class="sub">${esc(r.type || "")}</div></td>` +
        `<td>${esc(r.system)}</td>` +
        `<td class="num">${r.distance} ly</td>` +
        `<td class="num">${r.dist_ls != null ? fmtNum(Math.round(r.dist_ls)) + " ls" : "?"}</td>` +
        `<td>${r.large_pad ? "L" : "M/S"}</td>`;
      const td = document.createElement("td");
      td.appendChild(plotButton(r.system));
      tr.appendChild(td);
      tbody.appendChild(tr);
    }
    table.classList.toggle("hidden", results.length === 0);
  } catch (err) {
    status.classList.add("error");
    status.textContent = String(err.message || err);
  } finally {
    go.disabled = false;
  }
}

/* ---------- colonization ---------- */

function renderColonisation() {
  const card = $("colonisation-card");
  const list = $("colonisation-list");
  const depots = (state.colonisation || []).filter((d) => !d.complete && !d.failed);
  card.classList.toggle("hidden", depots.length === 0);
  const sig = JSON.stringify(depots);
  if (list.dataset.sig === sig) return;
  list.dataset.sig = sig;
  list.innerHTML = "";
  for (const d of depots) {
    const div = document.createElement("div");
    div.className = "hop";
    const pct = Math.round((d.progress || 0) * 100);
    const remaining = d.resources.filter((r) => r.remaining > 0);
    const rows = remaining.slice(0, 12).map((r) =>
      `<tr><td>${esc(r.name)}</td><td class="num">${fmtNum(r.remaining)}</td>` +
      `<td class="num">${fmtNum(r.payment)}</td>` +
      `<td class="num profit-cell">+${fmtNum(r.remaining * r.payment)}</td><td class="src" data-symbol="${esc(r.symbol)}"></td></tr>`
    ).join("");
    div.innerHTML =
      `<div class="route-line"><b>${esc(d.station || "Construction site")}</b>` +
      `<span class="dim">${esc(d.system || "")}</span>` +
      `<span class="profit">${pct}% complete</span></div>` +
      `<div class="seedbar"><div style="height:100%;width:${pct}%;background:var(--orange)"></div></div>` +
      (remaining.length
        ? `<table class="hop-table"><thead><tr><th>Still needed</th><th class="num">Units</th>` +
          `<th class="num">Pays/unit</th><th class="num">Total payout</th><th>Nearest source</th></tr></thead>` +
          `<tbody>${rows}</tbody></table>`
        : `<div class="commodities">All resources delivered.</div>`);
    if (remaining.length) {
      const btn = document.createElement("button");
      btn.className = "plotbtn";
      btn.textContent = "FIND SOURCES";
      btn.title = "Cheapest nearby stations selling what's still needed";
      btn.addEventListener("click", async () => {
        btn.disabled = true;
        btn.textContent = "SEARCHING…";
        try {
          const resp = await fetch(`/api/colonisation-sources?market_id=${d.market_id}&radius=50`);
          const data = await resp.json();
          if (!resp.ok) throw new Error(data.error || "failed");
          for (const c of data.commodities || []) {
            const cell = div.querySelector(`.src[data-symbol="${CSS.escape(c.symbol)}"]`);
            if (!cell) continue;
            cell.innerHTML = (c.sources || []).map((s) =>
              `<div>${esc(s.station)} <span class="sub">${esc(s.system)} · ${fmtNum(s.buy_price)} cr · ` +
              `${fmtNum(s.supply)} supply · ${s.distance} ly</span></div>`
            ).join("") || '<span class="dim">none within 50 ly</span>';
          }
          btn.textContent = "DONE";
        } catch (e) {
          btn.textContent = "FIND SOURCES";
          btn.disabled = false;
        }
      });
      div.querySelector(".route-line").insertBefore(btn, div.querySelector(".profit"));
    }
    list.appendChild(div);
  }
}

/* ---------- best sell for current cargo ---------- */

async function findCargoSell() {
  const status = $("cargo-sell-status");
  const out = $("cargo-sell-results");
  status.classList.remove("error");
  status.textContent = "Finding the best buyers for your hold…";
  out.innerHTML = "";
  try {
    const resp = await fetch("/api/cargo-sell?radius=50");
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || "Search failed");
    const results = data.results || [];
    status.textContent = results.length
      ? `Top ${results.length} buyers for your cargo within 50 ly:`
      : "Nobody nearby is buying what you're carrying — try after the next EDDN update or widen the net.";
    results.slice(0, 5).forEach((r, idx) => {
      const div = document.createElement("div");
      div.className = "hop";
      div.style.setProperty("--i", idx);
      const items = r.items.map((i) =>
        `${esc(i.name)} ×${fmtNum(i.units)} @ ${fmtNum(i.sell_price)}${i.partial ? " (demand-capped)" : ""}`
      ).join(" · ");
      div.innerHTML =
        `<div class="route-line"><b>${esc(r.station)}</b><span class="dim">${esc(r.system)}</span>` +
        `<span class="profit">+${fmtNum(r.total)} cr</span></div>` +
        `<div class="commodities">${r.distance} ly · ${r.dist_ls != null ? fmtNum(r.dist_ls) + " ls" : "?"}` +
        `${r.large_pad ? "" : " · no L pad"} · ${items}</div>`;
      const line = div.querySelector(".route-line");
      line.insertBefore(plotButton(r.system), line.querySelector(".profit"));
      out.appendChild(div);
    });
  } catch (err) {
    status.classList.add("error");
    status.textContent = String(err.message || err);
  }
}

/* ---------- analytics ---------- */

const SVG_NS = "http://www.w3.org/2000/svg";

function svgEl(tag, attrs, parent) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  if (parent) parent.appendChild(el);
  return el;
}

function chartTip() {
  let tip = document.getElementById("chart-tip");
  if (!tip) {
    tip = document.createElement("div");
    tip.id = "chart-tip";
    tip.className = "chart-tip hidden";
    document.body.appendChild(tip);
  }
  return tip;
}

function shortCr(n) {
  const a = Math.abs(n);
  if (a >= 1e9) return (n / 1e9).toFixed(1) + "B";
  if (a >= 1e6) return (n / 1e6).toFixed(1) + "M";
  if (a >= 1e3) return (n / 1e3).toFixed(0) + "k";
  return String(Math.round(n));
}

function drawBalanceChart(svg, points) {
  svg.innerHTML = "";
  if (points.length < 2) return;
  const W = svg.clientWidth || 900, H = 220, padL = 56, padR = 70, padY = 18;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  const ts = points.map((p) => p.ts), vs = points.map((p) => p.balance);
  const t0 = Math.min(...ts), t1 = Math.max(...ts);
  const v0 = Math.min(...vs), v1 = Math.max(...vs);
  const vpad = (v1 - v0) * 0.08 || v1 * 0.05 || 1;
  const x = (t) => padL + ((t - t0) / Math.max(1, t1 - t0)) * (W - padL - padR);
  const y = (v) => H - padY - ((v - (v0 - vpad)) / ((v1 + vpad) - (v0 - vpad))) * (H - 2 * padY);

  for (let i = 0; i <= 2; i++) {  // recessive grid: 3 lines
    const v = v0 + ((v1 - v0) * i) / 2;
    svgEl("line", { x1: padL, x2: W - padR, y1: y(v), y2: y(v), stroke: "var(--border)", "stroke-width": 1 }, svg);
    svgEl("text", { x: padL - 8, y: y(v) + 4, "text-anchor": "end", fill: "var(--dim)", "font-size": 11 }, svg)
      .textContent = shortCr(v);
  }
  const d = points.map((p, i) => `${i ? "L" : "M"}${x(p.ts).toFixed(1)},${y(p.balance).toFixed(1)}`).join("");
  svgEl("path", { d, fill: "none", stroke: "#ff7100", "stroke-width": 2, "stroke-linejoin": "round" }, svg);
  const last = points[points.length - 1];
  svgEl("circle", { cx: x(last.ts), cy: y(last.balance), r: 3.5, fill: "#ff7100" }, svg);
  svgEl("text", { x: x(last.ts) + 8, y: y(last.balance) + 4, fill: "var(--text)", "font-size": 12, "font-weight": 600 }, svg)
    .textContent = shortCr(last.balance);
  for (const t of [t0, t1]) {
    svgEl("text", { x: x(t), y: H - 2, "text-anchor": t === t0 ? "start" : "end", fill: "var(--dim)", "font-size": 11 }, svg)
      .textContent = new Date(t * 1000).toLocaleDateString([], { month: "short", day: "numeric" });
  }
  // crosshair + tooltip
  const cross = svgEl("line", { y1: padY, y2: H - padY, stroke: "var(--dim)", "stroke-width": 1, opacity: 0 }, svg);
  const tip = chartTip();
  svg.addEventListener("mousemove", (ev) => {
    const rect = svg.getBoundingClientRect();
    const mx = ((ev.clientX - rect.left) / rect.width) * W;
    let best = points[0], bd = Infinity;
    for (const p of points) { const dd = Math.abs(x(p.ts) - mx); if (dd < bd) { bd = dd; best = p; } }
    cross.setAttribute("x1", x(best.ts)); cross.setAttribute("x2", x(best.ts));
    cross.setAttribute("opacity", 0.5);
    tip.classList.remove("hidden");
    tip.textContent = `${new Date(best.ts * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })} · ${fmtNum(best.balance)} cr`;
    tip.style.left = (ev.pageX + 14) + "px";
    tip.style.top = (ev.pageY - 10) + "px";
  });
  svg.addEventListener("mouseleave", () => { cross.setAttribute("opacity", 0); tip.classList.add("hidden"); });
}

function drawDailyChart(svg, days) {
  svg.innerHTML = "";
  if (!days.length) return;
  const W = svg.clientWidth || 900, H = 200, padL = 56, padR = 16, padY = 16, gap = 2;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  const vals = days.map((d) => d.profit);
  const vmax = Math.max(0, ...vals), vmin = Math.min(0, ...vals);
  const span = (vmax - vmin) || 1;
  const y = (v) => padY + ((vmax - v) / span) * (H - 2 * padY - 14);
  const bw = Math.max(3, (W - padL - padR) / days.length - gap);
  svgEl("line", { x1: padL, x2: W - padR, y1: y(0), y2: y(0), stroke: "var(--border)", "stroke-width": 1 }, svg);
  svgEl("text", { x: padL - 8, y: y(vmax) + 4, "text-anchor": "end", fill: "var(--dim)", "font-size": 11 }, svg)
    .textContent = shortCr(vmax);
  const tip = chartTip();
  const maxIdx = vals.indexOf(Math.max(...vals));
  days.forEach((d, i) => {
    const vx = padL + i * ((W - padL - padR) / days.length) + gap / 2;
    const h = Math.max(2, Math.abs(y(d.profit) - y(0)));
    const ry = d.profit >= 0 ? y(d.profit) : y(0);
    const bar = svgEl("rect", {
      x: vx, y: ry, width: bw, height: h, rx: 3,
      fill: d.profit >= 0 ? "#6fbf73" : "#e05d5d",
    }, svg);
    if (i === maxIdx && d.profit > 0) {
      svgEl("text", { x: vx + bw / 2, y: ry - 4, "text-anchor": "middle", fill: "var(--text)", "font-size": 11, "font-weight": 600 }, svg)
        .textContent = shortCr(d.profit);
    }
    bar.addEventListener("mousemove", (ev) => {
      tip.classList.remove("hidden");
      tip.textContent = `${d.date} · ${fmtNum(d.profit)} cr · ${fmtNum(d.tons)} t sold`;
      tip.style.left = (ev.pageX + 14) + "px";
      tip.style.top = (ev.pageY - 10) + "px";
    });
    bar.addEventListener("mouseleave", () => tip.classList.add("hidden"));
    if (i === 0 || i === days.length - 1) {
      svgEl("text", { x: vx + bw / 2, y: H - 2, "text-anchor": "middle", fill: "var(--dim)", "font-size": 10 }, svg)
        .textContent = d.date.slice(5);
    }
  });
}

async function loadAnalytics() {
  try {
    const resp = await fetch("/api/analytics?days=" + $("an-days").value, { cache: "no-store" });
    if (!resp.ok) return;
    const a = await resp.json();
    $("an-today").textContent = "+" + fmtNum(a.today.profit) + " cr";
    $("an-week").textContent = "+" + fmtNum(a.week.profit) + " cr";
    $("an-period").textContent = "+" + fmtNum(a.period.profit) + " cr";
    $("an-tons").textContent = fmtNum(a.period.tons) + " t";
    const sess = a.session || {};
    setText("session-trade", sess.trade_profit != null ? "+" + fmtNum(sess.trade_profit) + " cr" : "—");
    setText("session-tons", sess.tons_sold != null ? fmtNum(sess.tons_sold) + " t" : "—");
    renderEarnings(a.earnings || {});
    drawBalanceChart($("an-balance"), a.balance || []);
    drawDailyChart($("an-daily"), a.daily || []);
    const top = a.top || [];
    $("an-empty").classList.toggle("hidden", top.length > 0);
    $("an-top").classList.toggle("hidden", top.length === 0);
    const tbody = $("an-top").querySelector("tbody");
    tbody.innerHTML = "";
    for (const t of top) {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${esc(t.name || t.symbol)}</td><td class="num">${fmtNum(t.tons)}</td>` +
        `<td class="num profit-cell">${t.profit < 0 ? "" : "+"}${fmtNum(t.profit)}</td>`;
      tbody.appendChild(tr);
    }
  } catch (e) { /* retry on next open */ }
}

/* ---------- market database panel ---------- */

async function seedDb() {
  if (!confirm("Download ~3.9 GB from spansh.co.uk and build the local market database?\n(Takes a while; the app stays usable meanwhile.)")) return;
  try {
    const resp = await fetch("/api/marketdb/seed", { method: "POST" });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || "Could not start build");
  } catch (err) {
    $("db-status").textContent = String(err.message || err);
  }
  pollDbStatus();
}

async function pollDbStatus() {
  let delay = 5000;
  try {
    const resp = await fetch("/api/marketdb/status", { cache: "no-store" });
    if (resp.ok) {
      const s = await resp.json();
      renderDbStatus(s);
      if (s.seeding && (s.seeding.phase === "downloading" || s.seeding.phase === "importing")) delay = 1500;
    }
  } catch (e) { /* retry next tick */ }
  setTimeout(pollDbStatus, delay);
}

function renderDbStatus(s) {
  const el = $("db-status");
  const bar = $("seed-bar");
  const fill = $("seed-fill");
  const btn = $("seed-btn");
  const seeding = s.seeding || {};

  if (seeding.phase === "downloading") {
    btn.disabled = true;
    bar.classList.remove("hidden");
    const pct = seeding.total_mb ? Math.round(100 * seeding.downloaded_mb / seeding.total_mb) : 0;
    fill.style.width = pct + "%";
    el.textContent = `Downloading dump… ${seeding.downloaded_mb} / ${seeding.total_mb} MB (${pct}%)`;
    return;
  }
  if (seeding.phase === "importing") {
    btn.disabled = true;
    bar.classList.remove("hidden");
    fill.style.width = "100%";
    el.textContent = `Importing… ${(seeding.systems_done || 0).toLocaleString()} systems, ${(seeding.stations_done || 0).toLocaleString()} station markets so far`;
    return;
  }
  btn.disabled = false;
  bar.classList.add("hidden");
  if (seeding.phase === "error") {
    el.textContent = "Build failed: " + seeding.error;
    return;
  }
  if (!s.ready) {
    el.textContent = "Not built yet — routes fall back to the Spansh API. Click Build to enable the local engine.";
    return;
  }
  btn.textContent = "REBUILD DATABASE";
  const eddn = s.eddn || {};
  const eddnTxt = eddn.connected
    ? `EDDN live (${(eddn.markets_updated || 0).toLocaleString()} markets updated this session)`
    : "EDDN reconnecting…";
  const up = s.eddn_upload || {};
  const upTxt = up.enabled
    ? ` · contributing back: ${up.uploads || 0} market${up.uploads === 1 ? "" : "s"} uploaded${up.last_error ? " (last attempt failed)" : ""}`
    : " · uploading disabled";
  el.textContent =
    `${(s.stations || 0).toLocaleString()} stations · ${(s.commodity_rows || 0).toLocaleString()} price rows · ` +
    `${s.db_size_mb} MB · seeded ${s.seeded_at || "?"} · ${eddnTxt}${upTxt}`;
}

/* ---------- auto-update ---------- */

let updateInfo = null;
let updateApplying = false;

async function pollUpdate() {
  try {
    const resp = await fetch("/api/update/check", { cache: "no-store" });
    if (resp.ok) {
      updateInfo = await resp.json();
      if (updateInfo.current) $("app-version").textContent = "v" + updateInfo.current;
      renderUpdateBanner();
    }
  } catch (e) { /* offline; try again later */ }
  // Re-check every 6 hours (the server caches, so this is cheap).
  setTimeout(pollUpdate, 6 * 3600 * 1000);
}

function renderUpdateBanner() {
  const el = $("update-banner");
  if (!updateInfo || !updateInfo.available || !updateInfo.supported) {
    if (!updateApplying) el.classList.add("hidden");
    return;
  }
  el.classList.remove("hidden");
  el.innerHTML =
    `<span class="ub-badge">⬆ UPDATE</span>` +
    `<span class="ub-text">Elite Trader <b>v${esc(updateInfo.latest)}</b> is available` +
    ` <span class="dim">(you have v${esc(updateInfo.current)})</span></span>`;
  const notes = document.createElement("a");
  notes.href = updateInfo.notes_url;
  notes.target = "_blank";
  notes.rel = "noopener";
  notes.className = "ub-notes";
  notes.textContent = "release notes";
  notes.addEventListener("click", (ev) => { if (openExternal(updateInfo.notes_url, "Release notes")) ev.preventDefault(); });
  const btn = document.createElement("button");
  btn.className = "ub-btn";
  btn.textContent = "Update & restart";
  btn.addEventListener("click", applyUpdate);
  el.appendChild(notes);
  el.appendChild(btn);
}

async function applyUpdate() {
  if (updateApplying) return;
  updateApplying = true;
  const el = $("update-banner");
  el.classList.remove("hidden");
  el.innerHTML = `<span class="ub-badge">⬆ UPDATE</span><span class="ub-text" id="ub-status">Starting update…</span>`;
  try {
    const resp = await fetch("/api/update/apply", { method: "POST" });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || "Update failed to start");
    pollUpdateStatus();
  } catch (err) {
    updateApplying = false;
    $("ub-status").textContent = String(err.message || err);
    el.classList.add("ub-error");
  }
}

async function pollUpdateStatus() {
  const status = $("ub-status");
  try {
    const resp = await fetch("/api/update/status", { cache: "no-store" });
    const s = await resp.json();
    if (s.phase === "downloading") {
      if (status) status.textContent = `Downloading update… ${s.pct}% (${s.downloaded_mb} / ${s.total_mb} MB)`;
    } else if (s.phase === "verifying") {
      if (status) status.textContent = "Verifying…";
    } else if (s.phase === "restarting") {
      if (status) status.textContent = "Restarting — Elite Trader will reopen in a moment.";
    } else if (s.phase === "error") {
      updateApplying = false;
      if (status) status.textContent = "Update failed: " + (s.error || "unknown error");
      $("update-banner").classList.add("ub-error");
      return;
    }
  } catch (e) {
    // Connection lost while restarting is the expected success signal.
    if (status) status.textContent = "Restarting — Elite Trader will reopen in a moment. You can close this tab.";
    return;
  }
  setTimeout(pollUpdateStatus, 700);
}

/* ---------- wiring ---------- */

async function poll() {
  try {
    const resp = await fetch("/api/state", { cache: "no-store" });
    if (resp.ok) {
      state = await resp.json();
      render();
    }
  } catch (e) { /* server briefly unreachable; keep last render */ }
  setTimeout(poll, 1500);
}

function activateTab(name) {
  document.querySelectorAll("#tabs .tab").forEach((b) =>
    b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tabpane").forEach((p) =>
    p.classList.toggle("hidden", p.id !== "tab-" + name));
  localStorage.setItem("activeTab", name);
  if (name === "analytics") loadAnalytics();
}

function initTabs() {
  document.querySelectorAll("#tabs .tab").forEach((b) =>
    b.addEventListener("click", () => activateTab(b.dataset.tab)));
  const saved = localStorage.getItem("activeTab");
  if (saved && document.getElementById("tab-" + saved)) activateTab(saved);
}

document.addEventListener("DOMContentLoaded", () => {
  initTabs();
  document.querySelector('[data-copy-target="system"]')
    .addEventListener("click", (ev) => state?.system && copyText(state.system, ev.currentTarget));
  $("station-copy")
    .addEventListener("click", (ev) => state?.station && copyText(state.station, ev.currentTarget));

  $("route-form").addEventListener("submit", findRoutes);
  $("route-form").addEventListener("input", () => { routeFormTouched = true; });
  const applyMode = () => {
    const loop = $("rf-mode").value === "loop";
    for (const id of ["rf-radius-wrap", "rf-maxleg-wrap", "rf-jumprange-wrap", "rf-results-wrap"])
      $(id).classList.toggle("hidden", !loop);
    $("rf-hop-wrap").classList.toggle("hidden", loop);
    $("rf-hops-wrap").classList.toggle("hidden", loop);
  };
  $("rf-mode").addEventListener("change", applyMode);

  // Persist route settings across reloads; restored values win over auto-seeding.
  const FORM_FIELDS = ["rf-mode", "rf-capital", "rf-cargo", "rf-radius", "rf-maxleg",
    "rf-jumprange", "rf-results", "rf-hop", "rf-hops", "rf-minsupply", "rf-lsdist",
    "rf-age", "rf-largepad"];
  try {
    const saved = JSON.parse(localStorage.getItem("routeForm") || "{}");
    let restored = false;
    for (const id of FORM_FIELDS) {
      if (!(id in saved)) continue;
      const el = $(id);
      if (el.type === "checkbox") el.checked = !!saved[id];
      else el.value = saved[id];
      restored = true;
    }
    if (restored) routeFormTouched = true;
  } catch (e) { /* corrupted storage - use defaults */ }
  applyMode();
  $("route-form").addEventListener("input", () => {
    const out = {};
    for (const id of FORM_FIELDS) {
      const el = $(id);
      out[id] = el.type === "checkbox" ? el.checked : el.value;
    }
    localStorage.setItem("routeForm", JSON.stringify(out));
  });

  $("plot-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const name = $("plot-input").value.trim();
    if (name) plotSystem(name);
  });

  // Flight panel mode (tablet as a cockpit display)
  initPanelNav();
  $("panel-toggle").addEventListener("click", () => setPanelMode(true));
  $("panel-exit").addEventListener("click", () => setPanelMode(false));
  $("fp-plot-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const name = $("fp-plot-input").value.trim();
    if (name) plotSystem(name);
  });
  $("fp-bestloop").addEventListener("click", findBestLoop);
  $("fp-voice").addEventListener("click", () => setVoice(!voiceOn, true));
  setVoice(voiceOn);  // reflect persisted state on the toggle (no speech yet)
  if (localStorage.getItem("panelMode") === "1") setPanelMode(true);

  // "open in app" toggle: only meaningful inside the desktop (pywebview) window.
  const toggle = $("inapp-toggle");
  toggle.checked = localStorage.getItem("inappLinks") === "1";
  toggle.addEventListener("change", () => localStorage.setItem("inappLinks", toggle.checked ? "1" : "0"));
  const showToggle = () => $("inapp-toggle-wrap").classList.remove("hidden");
  if (window.pywebview) showToggle();
  window.addEventListener("pywebviewready", showToggle);

  document.querySelectorAll("#market-table th").forEach((th) => {
    th.addEventListener("click", () => {
      const key = th.dataset.sort;
      marketSort.dir = marketSort.key === key ? -marketSort.dir : (key === "name" ? 1 : -1);
      marketSort.key = key;
      document.querySelectorAll("#market-table th").forEach((t) => t.classList.toggle("sorted", t === th));
      renderMarket();
    });
  });
  $("market-filter").addEventListener("input", renderMarket);
  $("seed-btn").addEventListener("click", seedDb);
  $("cs-form").addEventListener("submit", searchCommodity);
  $("os-form").addEventListener("submit", searchStations);
  $("cargo-sell-btn").addEventListener("click", findCargoSell);
  $("rr-form").addEventListener("submit", planRiches);
  $("nr-form").addEventListener("submit", planNeutron);
  $("an-days").addEventListener("change", loadAnalytics);

  renderRouteProgress();  // show a persisted route immediately, before first poll
  poll();
  pollDbStatus();
  pollAlerts();
  pollUpdate();
  loadCommodityList();
});
