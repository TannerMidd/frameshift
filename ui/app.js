/* Elite Trader UI: polls /api/state and renders. Same page works in the
   desktop window (pywebview) and any browser on the LAN. */

const $ = (id) => document.getElementById(id);

let state = null;
let marketSort = { key: "sell", dir: -1 };
let routeFormTouched = false;

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

async function plotSystem(system) {
  if (!system || plotBusy) return;
  const status = $("plot-status");
  plotBusy = true;
  status.classList.remove("error");
  status.textContent = `Plotting route to ${system} — leave the game window alone for ~10s…`;
  try {
    const resp = await fetch("/api/plot", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ system }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || "Plot failed");
    status.textContent = `Sent plot sequence for ${system} — check the game.`;
  } catch (err) {
    status.classList.add("error");
    status.textContent = String(err.message || err);
  } finally {
    plotBusy = false;
  }
}

function plotButton(system) {
  const btn = document.createElement("button");
  btn.className = "plotbtn";
  btn.type = "button";
  btn.title = "Plot route in game to " + system;
  btn.textContent = "◎";
  btn.addEventListener("click", () => plotSystem(system));
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
  seedRouteForm();
}

function renderBanner() {
  const banner = $("banner");
  if (state.journal_dir_found === false) {
    banner.textContent = "Elite Dangerous journal folder not found - set the ED_JOURNAL_DIR environment variable.";
    banner.classList.remove("hidden");
  } else if (!state.system) {
    banner.textContent = "Waiting for journal data - start Elite Dangerous (or play a bit) and this will fill in.";
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
      `<td class="num">${i.sell ? i.sell.toLocaleString() : "—"}</td>` +
      `<td class="num">${i.buy ? i.buy.toLocaleString() : "—"}</td>` +
      `<td class="num">${i.demand ? i.demand.toLocaleString() : "—"}</td>` +
      `<td class="num">${i.stock ? i.stock.toLocaleString() : "—"}</td>`;
    tbody.appendChild(tr);
  }
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

function seedRouteForm() {
  if (routeFormTouched) return;
  if (state.credits != null && !$("rf-capital").value) $("rf-capital").value = state.credits;
  if (state.cargo_capacity != null && !$("rf-cargo").value) $("rf-cargo").value = state.cargo_capacity;
  if (state.max_jump_range != null && !$("rf-hop").value) $("rf-hop").value = state.max_jump_range.toFixed(1);
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
    const resp = await fetch("/api/trade-route", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        capital: Number($("rf-capital").value) || undefined,
        max_cargo: Number($("rf-cargo").value) || undefined,
        max_hop_distance: Number($("rf-hop").value) || undefined,
        max_hops: Number($("rf-hops").value) || undefined,
        max_system_distance: Number($("rf-lsdist").value) || undefined,
        max_price_age_days: Number($("rf-age").value) || undefined,
        requires_large_pad: $("rf-largepad").checked,
      }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || "Route request failed");
    renderRoutes(data.hops || []);
    const src = data.source === "local" ? "local database" : "Spansh API (local DB not built yet)";
    status.textContent = data.hops && data.hops.length
      ? `Route found (${data.hops.length} hop${data.hops.length > 1 ? "s" : ""}) from ${state.system} via ${src}.`
      : `No profitable route for those settings (via ${src}).`;
  } catch (err) {
    status.classList.add("error");
    status.textContent = String(err.message || err);
  } finally {
    go.disabled = false;
  }
}

function renderRoutes(hops) {
  const results = $("route-results");
  results.innerHTML = "";
  for (const h of hops) {
    const div = document.createElement("div");
    div.className = "hop";
    const commodities = (h.commodities || [])
      .map((c) => `<b>${esc(c.name)}</b> ×${c.amount ?? "?"} (buy ${fmtNum(c.buy_price)}, sell ${fmtNum(c.sell_price)})`)
      .join(" · ");
    div.innerHTML =
      `<div class="route-line">` +
      `<b>${esc(h.from_station)}</b><span class="dim">${esc(h.from_system)}</span>` +
      `<span class="arrow">➜</span>` +
      `<b>${esc(h.to_station)}</b><span class="dim">${esc(h.to_system)}</span>` +
      `<span class="profit">+${fmtNum(h.profit)} cr</span>` +
      `</div>` +
      (commodities ? `<div class="commodities">${commodities}</div>` : "") +
      (h.distance != null ? `<div class="commodities">${Number(h.distance).toFixed(1)} ly jump` +
        (h.to_dist_ls != null ? ` · ${fmtNum(h.to_dist_ls)} ls to station` : "") +
        (h.cumulative_profit != null ? ` · total so far: ${fmtNum(h.cumulative_profit)} cr` : "") + `</div>` : "");
    if (h.to_system) {
      const line = div.querySelector(".route-line");
      line.insertBefore(plotButton(h.to_system), line.querySelector(".profit"));
    }
    results.appendChild(div);
  }
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
  el.textContent =
    `${(s.stations || 0).toLocaleString()} stations · ${(s.commodity_rows || 0).toLocaleString()} price rows · ` +
    `${s.db_size_mb} MB · seeded ${s.seeded_at || "?"} · ${eddnTxt}`;
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

document.addEventListener("DOMContentLoaded", () => {
  document.querySelector('[data-copy-target="system"]')
    .addEventListener("click", (ev) => state?.system && copyText(state.system, ev.currentTarget));
  $("station-copy")
    .addEventListener("click", (ev) => state?.station && copyText(state.station, ev.currentTarget));

  $("route-form").addEventListener("submit", findRoutes);
  $("route-form").addEventListener("input", () => { routeFormTouched = true; });

  $("plot-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const name = $("plot-input").value.trim();
    if (name) plotSystem(name);
  });

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

  poll();
  pollDbStatus();
  loadCommodityList();
});
