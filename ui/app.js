/* Frameshift UI: polls /api/state and renders. Same page works in the
   desktop window (pywebview) and any browser on the LAN. */

const $ = (id) => document.getElementById(id);
const GalaxyData = window.FrameshiftGalaxyData;

let state = null;
let marketSort = { key: "sell", dir: -1 };
let routeFormTouched = false;
let galaxyHistory = [];
let galaxyHistoryCommander = null;
let securityStatus = null;
let securityLocked = false;
let pairingReturnFocus = null;
let pairingInertState = [];
let opsState = {
  objectives: [], plan: null, timings: null, boards: [], snapshot: null,
  conflicts: [], activeBoardId: "",
};
let opsWorkspaceLoading = null;
let specialistState = null;
let specialistLoading = null;
let specialistLastFetch = 0;
let profileGeneration = 0;

/* Active route being flown (persisted): { kind, label, waypoints:[{system,note}], index } */
let activeRoute = null;
let activeRouteCommander = null;

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

/* ---------- zero-account LAN pairing ---------- */

function friendlyDeviceName() {
  const platform = navigator.userAgentData?.platform || navigator.platform || "Browser";
  const mobile = /Android|iPhone|iPad|Mobile/i.test(navigator.userAgent || "");
  return `${platform}${mobile ? " tablet" : " browser"}`.slice(0, 80);
}

function pairingFocusable(gate) {
  return [...gate.querySelectorAll('button:not([disabled]):not(.hidden), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])')]
    .filter((element) => !element.closest(".hidden"));
}

function pairingTrapKeydown(event) {
  const gate = $("pairing-gate");
  if (gate.classList.contains("hidden") || event.key !== "Tab") return;
  const focusable = pairingFocusable(gate);
  if (!focusable.length) {
    event.preventDefault();
    gate.querySelector(".pairing-panel").focus();
    return;
  }
  const first = focusable[0], last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

function setPairingModalOpen(open) {
  const gate = $("pairing-gate");
  const wasOpen = !gate.classList.contains("hidden");
  if (open) {
    if (!wasOpen) {
      pairingReturnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      pairingInertState = [...document.body.children]
        .filter((element) => element !== gate && element instanceof HTMLElement)
        .map((element) => ({
          element,
          inert: !!element.inert,
          ariaHidden: element.getAttribute("aria-hidden"),
        }));
      for (const item of pairingInertState) {
        item.element.inert = true;
        item.element.setAttribute("aria-hidden", "true");
      }
      gate.addEventListener("keydown", pairingTrapKeydown);
    }
    gate.classList.remove("hidden");
    gate.setAttribute("aria-hidden", "false");
    setTimeout(() => {
      const target = pairingFocusable(gate)[0] || gate.querySelector(".pairing-panel");
      target?.focus();
    }, 0);
    return;
  }
  gate.classList.add("hidden");
  gate.setAttribute("aria-hidden", "true");
  gate.removeEventListener("keydown", pairingTrapKeydown);
  for (const item of pairingInertState) {
    item.element.inert = item.inert;
    if (item.ariaHidden == null) item.element.removeAttribute("aria-hidden");
    else item.element.setAttribute("aria-hidden", item.ariaHidden);
  }
  pairingInertState = [];
  if (pairingReturnFocus?.isConnected) pairingReturnFocus.focus();
  pairingReturnFocus = null;
}

function showPairingGate(title, message, retry) {
  const gate = $("pairing-gate");
  $("pairing-title").textContent = title;
  $("pairing-message").textContent = message;
  $("pairing-retry").classList.toggle("hidden", !retry);
  setPairingModalOpen(true);
}

function clearAuthenticatedRuntime() {
  state = null;
  securityStatus = null;
  activeRoute = null;
  activeRouteCommander = null;
  galaxyHistory = [];
  galaxyHistoryCommander = null;
  engMatsSig = null;
  resetProfileWorkspaces(null);
}

function enterPairingRequired(message) {
  securityLocked = true;
  try {
    clearAuthenticatedRuntime();
  } catch (_error) {
    // A stale workspace must never prevent a revoked device from being locked.
    state = null;
  } finally {
    showPairingGate(
      "This device is not paired",
      message || "Access was revoked or expired. Open a new one-time LAN link from Frameshift on the gaming PC.",
      true,
    );
  }
}

async function fetchSecurityStatus() {
  const resp = await fetch("/api/security/status", { cache: "no-store" });
  if (!resp.ok) throw new Error("Frameshift security status is unavailable.");
  securityStatus = await resp.json();
  return securityStatus;
}

async function bootstrapSecurity() {
  const url = new URL(window.location.href);
  const code = url.searchParams.get("pair");
  try {
    if (code) {
      showPairingGate("Pairing this device…", "Exchanging the one-time cockpit link. No account or password is required.", false);
      const response = await fetch("/api/security/pair", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code, device_name: friendlyDeviceName() }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "The pairing link was not accepted.");
      url.searchParams.delete("pair");
      history.replaceState({}, "", url.pathname + (url.search ? url.search : "") + url.hash);
    }
    const status = await fetchSecurityStatus();
    if (status.pairing_required) {
      enterPairingRequired("Open the current one-time LAN link shown in Frameshift on the gaming PC. Previously paired devices reconnect automatically.");
      return false;
    }
    securityLocked = false;
    setPairingModalOpen(false);
    return true;
  } catch (error) {
    showPairingGate("Pairing could not finish", String(error.message || error), true);
    return false;
  }
}

function pairingAbsoluteUrl(status) {
  const pairing = status?.pairing;
  if (!pairing) return "";
  if (pairing.urls?.length) return pairing.urls[0];
  return window.location.origin + pairing.path;
}

async function refreshSecurityPanel(rotate = false) {
  try {
    if (rotate) {
      const response = await fetch("/api/security/pairing-code", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scopes: ["admin"] }),
      });
      if (!response.ok) throw new Error("Could not create a pairing link.");
    }
    const status = await fetchSecurityStatus();
    const admin = status.scopes?.includes("admin");
    $("pairing-refresh").classList.toggle("hidden", !admin);
    $("security-state").textContent = status.local
      ? `Desktop access is automatic · ${status.paired_devices || 0} paired device${status.paired_devices === 1 ? "" : "s"}`
      : `Paired as ${status.device?.name || "LAN device"} · ${status.scopes.join(" / ")}`;
    const link = pairingAbsoluteUrl(status);
    $("pairing-share").classList.toggle("hidden", !link);
    if (link) {
      $("pairing-link").value = link;
      const qr = $("pairing-qr");
      const qrSvg = status.pairing.qr_svg || "";
      qr.classList.toggle("hidden", !qrSvg);
      if (qrSvg) qr.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(qrSvg);
      const seconds = Math.max(0, Math.round((status.pairing.expires_at * 1000 - Date.now()) / 1000));
      $("pairing-expiry").textContent = `Single use · expires in about ${Math.max(1, Math.ceil(seconds / 60))} minute${seconds > 60 ? "s" : ""}. The device remains paired afterwards.`;
    }
    await renderPairedDevices(admin);
  } catch (error) {
    $("security-state").textContent = String(error.message || error);
  }
}

async function renderPairedDevices(admin) {
  const list = $("paired-devices");
  list.innerHTML = "";
  if (!admin) return;
  const response = await fetch("/api/security/devices", { cache: "no-store" });
  if (!response.ok) return;
  const devices = (await response.json()).devices || [];
  for (const device of devices) {
    const row = document.createElement("div");
    row.className = "paired-device";
    const main = document.createElement("div");
    main.className = "device-main";
    main.innerHTML = `<div class="device-name">${esc(device.name || "LAN device")}</div>` +
      `<div class="dim">${esc(device.last_ip || "address unknown")} · last seen ${esc(device.last_seen || "never")}</div>`;
    const scope = document.createElement("select");
    for (const value of ["read", "control", "admin"]) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = value.toUpperCase();
      option.selected = (device.scopes || []).includes(value) &&
        !(device.scopes || []).some((other) => ["read", "control", "admin"].indexOf(other) > ["read", "control", "admin"].indexOf(value));
      scope.appendChild(option);
    }
    scope.title = "READ views data; CONTROL can plot/speak; ADMIN can change settings and pair devices.";
    scope.addEventListener("change", async () => {
      await fetch(`/api/security/devices/${encodeURIComponent(device.id)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scopes: [scope.value] }),
      });
      refreshSecurityPanel();
    });
    const revoke = document.createElement("button");
    revoke.className = "copy";
    revoke.textContent = "REVOKE";
    revoke.addEventListener("click", async () => {
      await fetch(`/api/security/devices/${encodeURIComponent(device.id)}`, { method: "DELETE" });
      refreshSecurityPanel();
    });
    row.append(main, scope, revoke);
    list.appendChild(row);
  }
}

async function loadProfiles() {
  const card = $("profiles-card");
  const admin = securityStatus?.scopes?.includes("admin");
  card.classList.toggle("hidden", !admin);
  if (!admin) return;
  const list = $("profiles-list");
  const bucket = $("profiles-unattributed");
  try {
    const response = await fetch("/api/profiles", { cache: "no-store" });
    if (!response.ok) throw new Error("Commander profiles are unavailable.");
    const data = await response.json();
    const profiles = data.profiles || [];
    const named = profiles.filter((p) => p.id !== "default");

    bucket.classList.toggle("hidden", !(data.unattributed?.rows > 0));
    bucket.innerHTML = "";
    if (data.unattributed?.rows > 0 && named.length) {
      const info = document.createElement("div");
      info.className = "device-main";
      info.innerHTML =
        `<div class="device-name">UNASSIGNED HISTORY · ${Number(data.unattributed.rows).toLocaleString()} records</div>` +
        `<div class="dim">Trades, earnings and watches saved before this machine knew your commander name. ` +
        `If all of it is yours, assign it — it merges safely (duplicates are skipped).</div>`;
      const pick = document.createElement("select");
      for (const profile of named) {
        const option = document.createElement("option");
        option.value = profile.id;
        option.textContent = profile.name;
        option.selected = profile.active;
        pick.appendChild(option);
      }
      const assign = document.createElement("button");
      assign.className = "primary small";
      assign.textContent = "ASSIGN";
      assign.addEventListener("click", async () => {
        const target = named.find((p) => p.id === pick.value);
        if (!confirm(`Give all unassigned history to ${target?.name || "this commander"}? ` +
            "Only do this if no other person's account has used this machine.")) return;
        const result = await fetch("/api/profiles/assign-unattributed", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ commander_id: pick.value }),
        });
        if (!result.ok) alert((await result.json()).error || "Could not assign the history.");
        loadProfiles();
      });
      bucket.append(info, pick, assign);
    }

    list.classList.remove("dim");
    list.innerHTML = "";
    if (!named.length) {
      list.classList.add("dim");
      list.textContent = "No commander seen yet — profiles appear after your first login with the game running.";
      return;
    }
    for (const profile of named) {
      const row = document.createElement("div");
      row.className = "paired-device";
      const main = document.createElement("div");
      main.className = "device-main";
      const mode = profile.galaxy_mode === "legacy" ? " · LEGACY galaxy" : "";
      main.innerHTML =
        `<div class="device-name">${esc(profile.name)}${profile.active ? ' <span class="chip">ACTIVE</span>' : ""}</div>` +
        `<div class="dim">${Number(profile.rows || 0).toLocaleString()} local records${mode}` +
        ` · last seen ${esc((profile.last_seen_at || "never").slice(0, 16).replace("T", " "))}</div>`;
      row.appendChild(main);
      if (!profile.active) {
        const activate = document.createElement("button");
        activate.className = "copy";
        activate.textContent = "ACTIVATE";
        activate.title = "Show this commander's data now. The journal switches it back automatically at your next login.";
        activate.addEventListener("click", async () => {
          await fetch(`/api/profiles/${encodeURIComponent(profile.id)}/activate`, { method: "POST" });
          loadProfiles();
        });
        const remove = document.createElement("button");
        remove.className = "copy";
        remove.textContent = "DELETE";
        remove.title = "Remove this profile and every local record it owns. In-game progress is never affected.";
        remove.addEventListener("click", async () => {
          if (!confirm(`Delete ${profile.name} and its ${Number(profile.rows || 0).toLocaleString()} local records? ` +
              "This cannot be undone (a backup is kept in data/backups).")) return;
          const result = await fetch(`/api/profiles/${encodeURIComponent(profile.id)}`, { method: "DELETE" });
          if (!result.ok) alert((await result.json()).error || "Could not delete the profile.");
          loadProfiles();
        });
        row.append(activate, remove);
      }
      list.appendChild(row);
    }
  } catch (error) {
    list.classList.add("dim");
    list.textContent = String(error.message || error);
  }
}

async function loadLocalServices() {
  const card = $("local-services-card");
  const admin = securityStatus?.scopes?.includes("admin");
  card.classList.toggle("hidden", !admin);
  $("ext-builder-card").classList.toggle("hidden", !admin);
  if (!admin) return;
  // The health probe can take seconds (SQLite integrity check on a large DB,
  // worse on spinning disks) — never make the extensions list wait for it.
  const extensionsDone = (async () => {
    try {
      const resp = await fetch("/api/extensions", { cache: "no-store" });
      if (!resp.ok) throw new Error("Extension packs are unavailable.");
      renderExtensionRows(await resp.json());
    } catch (error) {
      $("extensions-status").textContent = String(error.message || error);
    }
  })();
  try {
    const resp = await fetch("/api/diagnostics/health", { cache: "no-store" });
    if (!resp.ok) throw new Error("Local diagnostics are unavailable.");
    const health = await resp.json();
    const db = health.market_database || {};
    const integrity = health.sqlite_integrity || (health.market_database_error ? "unavailable" : "unknown");
    $("local-health").textContent =
      `Frameshift ${health.version || "?"} · database ${integrity}` +
      `${db.markets != null ? ` · ${Number(db.markets).toLocaleString()} markets` : ""}` +
      " · logs rotate locally";
  } catch (error) {
    $("local-health").textContent = String(error.message || error);
  }
  await extensionsDone;
}

function renderExtensionRows(extensions) {
  const loaded = extensions.loaded || [];
  const errors = extensions.errors || [];
  $("extensions-status").innerHTML =
      `<b>${loaded.length} extension pack${loaded.length === 1 ? "" : "s"} loaded</b>` +
      `<span class="dim"> from the local extensions folder` +
      `${errors.length ? ` · ${errors.length} rejected (details included in diagnostics)` : ""}</span>` +
      `<div class="extension-rows">${loaded.map((extension) => {
        const process = extension.mode === "process";
        const permissionText = (extension.permissions || []).join(" / ") || "no permissions";
        const approval = process
          ? extension.approved
            ? '<span class="good">APPROVED FOR THIS EXACT BUILD</span>'
            : '<span class="warn">CODE EXECUTION BLOCKED · APPROVAL REQUIRED</span>'
          : '<span class="good">DECLARATIVE · NO CODE EXECUTION</span>';
        const action = process
          ? (extension.approved
            ? `<button type="button" class="copy danger" data-extension-action="revoke" data-extension-id="${esc(extension.id)}">REVOKE</button>`
            : `<button type="button" class="copy" data-extension-action="approve" data-extension-id="${esc(extension.id)}">APPROVE CODE</button>`)
          : `<span class="extension-tools">` +
            `<button type="button" class="copy" data-extension-action="edit" data-extension-id="${esc(extension.id)}" title="Open in the extension builder">✎ EDIT</button>` +
            `<button type="button" class="copy danger" data-extension-action="delete" data-extension-id="${esc(extension.id)}" title="Remove this pack">✕</button></span>`;
        return `<div class="extension-row"><div><b>${esc(extension.name || extension.id)}</b>` +
          `<span class="dim">${esc(extension.id)} · ${esc(extension.version || "0")} · ${esc(permissionText)}</span>` +
          `<span>${approval}${process && extension.fingerprint ? ` · fingerprint ${esc(extension.fingerprint)}` : ""}</span></div>${action}</div>`;
      }).join("")}</div>`;
}

async function downloadSupportBundle() {
  const button = $("diagnostics-bundle");
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "BUILDING…";
  try {
    const response = await fetch("/api/diagnostics/bundle", { method: "POST" });
    if (!response.ok) throw new Error("Support bundle could not be created.");
    const blob = await response.blob();
    const disposition = response.headers.get("Content-Disposition") || "";
    const match = disposition.match(/filename\*?=(?:UTF-8''|\")?([^\";]+)/i);
    const filename = decodeURIComponent(match?.[1] || "frameshift-diagnostics.zip");
    const href = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = href;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(href), 1000);
    button.textContent = "SAVED";
  } catch (error) {
    button.textContent = "FAILED";
  } finally {
    setTimeout(() => { button.textContent = original; button.disabled = false; }, 1200);
  }
}

async function reloadExtensions() {
  const button = $("extensions-reload");
  button.disabled = true;
  try {
    const response = await fetch("/api/extensions/reload", { method: "POST" });
    if (!response.ok) throw new Error("Extension packs could not be reloaded.");
    await loadLocalServices();
  } catch (error) {
    $("extensions-status").textContent = String(error.message || error);
  } finally {
    button.disabled = false;
  }
}

/* ---------- extension builder ----------
   A guided form that writes declarative extension manifests: pick a journal
   event, add conditions, choose an alert or objective. The server validates
   with the exact code that vets installed packs; nothing here executes. */

// Curated journal events with the fields people actually condition on.
// Free-text entry still allows any event and any dotted field path.
const XB_EVENTS = [
  { id: "*", label: "Any event", fields: ["event", "timestamp"] },
  { id: "FSDJump", label: "Hyperspace jump (FSDJump)", fields: ["StarSystem", "JumpDist", "FuelLevel", "FuelUsed", "StarClass", "Population"] },
  { id: "Docked", label: "Docked at a station", fields: ["StationName", "StarSystem", "StationType", "DistFromStarLS"] },
  { id: "Undocked", label: "Undocked", fields: ["StationName"] },
  { id: "Bounty", label: "Bounty awarded", fields: ["Reward", "Target", "VictimFaction"] },
  { id: "MissionCompleted", label: "Mission completed", fields: ["Reward", "Faction", "Name"] },
  { id: "MissionAccepted", label: "Mission accepted", fields: ["Faction", "Name", "Reward", "Expiry"] },
  { id: "MarketSell", label: "Sold commodity", fields: ["Type", "Count", "SellPrice", "TotalSale", "AvgPricePaid"] },
  { id: "MarketBuy", label: "Bought commodity", fields: ["Type", "Count", "BuyPrice", "TotalCost"] },
  { id: "Scan", label: "Body scanned", fields: ["BodyName", "WasDiscovered", "WasMapped", "PlanetClass", "TerraformState", "Landable"] },
  { id: "SAASignalsFound", label: "Surface signals found", fields: ["BodyName"] },
  { id: "ScanOrganic", label: "Organic scanned", fields: ["Genus_Localised", "Species_Localised", "ScanType"] },
  { id: "SellOrganicData", label: "Sold exobiology data", fields: [] },
  { id: "HullDamage", label: "Hull damage", fields: ["Health", "PlayerPilot"] },
  { id: "ShieldState", label: "Shields up/down", fields: ["ShieldsUp"] },
  { id: "Interdicted", label: "Interdicted", fields: ["Submitted", "Interdictor", "IsPlayer"] },
  { id: "FuelScoop", label: "Fuel scooped", fields: ["Scooped", "Total"] },
  { id: "CollectCargo", label: "Cargo collected", fields: ["Type", "Stolen"] },
  { id: "CargoDepot", label: "Wing mission depot", fields: ["UpdateType", "ItemsDelivered", "TotalItemsToDeliver"] },
  { id: "RedeemVoucher", label: "Voucher redeemed", fields: ["Type", "Amount"] },
  { id: "ReceiveText", label: "Message received", fields: ["From", "Message", "Channel"] },
  { id: "Touchdown", label: "Surface touchdown", fields: ["Body", "OnPlanet"] },
  { id: "LaunchSRV", label: "SRV deployed", fields: ["SRVType"] },
];

const XB_OPS = [
  { id: "eq", label: "equals" },
  { id: "in", label: "is one of (comma-separated)" },
  { id: "min", label: "is at least" },
  { id: "max", label: "is at most" },
  { id: "exists", label: "is present" },
  { id: "absent", label: "is absent" },
];

const XB_TEMPLATES = [
  {
    label: "💰 Big bounty callout",
    name: "Big bounty callout",
    rule: { event: "Bounty", conditions: [{ field: "Reward", op: "min", value: "100000" }],
      action: { type: "alert", level: "info", text: "Bounty {Reward} cr — {Target}", voice: true } },
  },
  {
    label: "⛽ Low fuel after jump",
    name: "Low fuel after jump",
    rule: { event: "FSDJump", conditions: [{ field: "FuelLevel", op: "max", value: "8" }],
      action: { type: "alert", level: "warn", text: "Fuel at {FuelLevel} t after jump — find a scoopable star", voice: true } },
  },
  {
    label: "📦 Mission payout tracker",
    name: "Mission payout tracker",
    rule: { event: "MissionCompleted", conditions: [{ field: "Reward", op: "min", value: "1000000" }],
      action: { type: "alert", level: "info", text: "{Faction} paid {Reward} cr", voice: false } },
  },
  {
    label: "★ First-discovery follow-up",
    name: "First discovery follow-up",
    rule: { event: "Scan", conditions: [{ field: "WasDiscovered", op: "eq", value: "false" }],
      action: { type: "objective", title: "Map first discovery {BodyName}", category: "exploration" } },
  },
];

let xbModel = null;      // { name, id, editingId, rules: [...] }
let xbBusy = false;

function xbSlug(name) {
  const slug = String(name || "").toLowerCase()
    .replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 40);
  return /^[a-z0-9]/.test(slug) && slug.length >= 2 ? slug : "";
}

function xbBlankRule() {
  return { event: "FSDJump", customEvent: "", conditions: [],
    action: { type: "alert", level: "info", text: "", voice: false, title: "", category: "" } };
}

function xbOpen(seed) {
  xbModel = {
    name: seed?.name || "",
    editingId: seed?.editingId || null,
    rules: (seed?.rules || [xbBlankRule()]).map((r) => ({ ...xbBlankRule(), ...r,
      action: { ...xbBlankRule().action, ...(r.action || {}) },
      conditions: (r.conditions || []).map((c) => ({ ...c })) })),
  };
  $("xb-name").value = xbModel.name;
  $("xb-form").classList.remove("hidden");
  $("xb-status").textContent = "";
  $("xb-results").classList.add("hidden");
  xbRenderRules();
  xbSyncId();
  $("xb-name").focus();
}

function xbClose() {
  xbModel = null;
  $("xb-form").classList.add("hidden");
}

function xbSyncId() {
  const id = xbModel?.editingId || xbSlug($("xb-name").value);
  $("xb-id").textContent = id || "—";
}

// Rebuilds the rule blocks from the model. Value edits update the model via
// the delegated input listener without a rebuild; structure changes rebuild.
function xbRenderRules() {
  const wrap = $("xb-rules");
  wrap.innerHTML = "";
  xbModel.rules.forEach((rule, ri) => {
    const block = document.createElement("div");
    block.className = "xb-rule";
    const known = XB_EVENTS.some((e) => e.id === rule.event);
    const catalogEntry = XB_EVENTS.find((e) => e.id === (known ? rule.event : "")) || null;
    const fields = catalogEntry ? catalogEntry.fields : [];
    const alert = rule.action.type === "alert";
    block.innerHTML =
      `<div class="xb-rule-head"><span class="xb-rule-n">RULE ${ri + 1}</span>` +
      (xbModel.rules.length > 1
        ? `<button type="button" class="copy small" data-xb="rule-remove" data-ri="${ri}">✕ REMOVE</button>` : "") +
      `</div>` +
      `<div class="xb-row"><span class="xb-kw">WHEN</span>` +
      `<select data-xb="event" data-ri="${ri}">` +
      XB_EVENTS.map((e) => `<option value="${esc(e.id)}"${known && e.id === rule.event ? " selected" : ""}>${esc(e.label)}</option>`).join("") +
      `<option value="__custom__"${known ? "" : " selected"}>Custom event…</option>` +
      `</select>` +
      (known ? "" :
        `<input type="text" data-xb="custom-event" data-ri="${ri}" placeholder="Journal event name" value="${esc(rule.customEvent || rule.event || "")}">`) +
      `</div>` +
      `<div class="xb-conditions">` +
      rule.conditions.map((c, ci) =>
        `<div class="xb-row xb-cond"><span class="xb-kw">${ci === 0 ? "IF" : "AND"}</span>` +
        `<input type="text" data-xb="cond-field" data-ri="${ri}" data-ci="${ci}" list="xb-fields-${ri}" placeholder="Field" value="${esc(c.field || "")}">` +
        `<select data-xb="cond-op" data-ri="${ri}" data-ci="${ci}">` +
        XB_OPS.map((o) => `<option value="${o.id}"${o.id === c.op ? " selected" : ""}>${o.label}</option>`).join("") +
        `</select>` +
        (["exists", "absent"].includes(c.op) ? "" :
          `<input type="text" data-xb="cond-value" data-ri="${ri}" data-ci="${ci}" placeholder="Value" value="${esc(c.value || "")}">`) +
        `<button type="button" class="copy small" data-xb="cond-remove" data-ri="${ri}" data-ci="${ci}" title="Remove condition">✕</button>` +
        `</div>`).join("") +
      `</div>` +
      `<datalist id="xb-fields-${ri}">${fields.map((f) => `<option value="${esc(f)}">`).join("")}</datalist>` +
      `<button type="button" class="copy small" data-xb="cond-add" data-ri="${ri}">＋ CONDITION</button>` +
      `<div class="xb-row"><span class="xb-kw">THEN</span>` +
      `<select data-xb="action-type" data-ri="${ri}">` +
      `<option value="alert"${alert ? " selected" : ""}>Show a cockpit alert</option>` +
      `<option value="objective"${alert ? "" : " selected"}>Suggest an objective</option>` +
      `</select>` +
      (alert
        ? `<select data-xb="action-level" data-ri="${ri}">` +
          ["info", "warn", "critical"].map((l) => `<option value="${l}"${rule.action.level === l ? " selected" : ""}>${l.toUpperCase()}</option>`).join("") +
          `</select>`
        : `<input type="text" data-xb="action-category" data-ri="${ri}" placeholder="Category (optional)" value="${esc(rule.action.category || "")}">`) +
      `</div>` +
      `<div class="xb-row xb-msgrow">` +
      (alert
        ? `<input type="text" data-xb="action-text" data-ri="${ri}" maxlength="500" placeholder="Alert text — {FieldName} inserts a value from the event" value="${esc(rule.action.text || "")}">`
        : `<input type="text" data-xb="action-title" data-ri="${ri}" maxlength="240" placeholder="Objective title — {FieldName} inserts a value from the event" value="${esc(rule.action.title || "")}">`) +
      `</div>` +
      (alert
        ? `<label class="check xb-voice"><input type="checkbox" data-xb="action-voice" data-ri="${ri}"${rule.action.voice ? " checked" : ""}> Also speak it (voice callout)</label>`
        : "") +
      (fields.length
        ? `<div class="xb-chips dim">Insert: ${fields.map((f) => `<button type="button" class="chip" data-xb="chip" data-ri="${ri}" data-field="${esc(f)}">{${esc(f)}}</button>`).join(" ")}</div>`
        : "");
    wrap.appendChild(block);
  });
}

function xbHandleClick(target) {
  const kind = target.dataset.xb;
  const ri = Number(target.dataset.ri);
  const rule = xbModel?.rules?.[ri];
  if (!kind) return false;
  if (kind === "rule-remove") { xbModel.rules.splice(ri, 1); xbRenderRules(); }
  else if (kind === "cond-add") { rule.conditions.push({ field: "", op: "eq", value: "" }); xbRenderRules(); }
  else if (kind === "cond-remove") { rule.conditions.splice(Number(target.dataset.ci), 1); xbRenderRules(); }
  else if (kind === "chip") {
    const input = $("xb-rules").querySelector(
      `[data-xb="${rule.action.type === "alert" ? "action-text" : "action-title"}"][data-ri="${ri}"]`);
    if (input) {
      input.value += `{${target.dataset.field}}`;
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.focus();
    }
  } else return false;
  return true;
}

function xbHandleInput(target) {
  const kind = target.dataset.xb;
  const ri = Number(target.dataset.ri);
  const rule = xbModel?.rules?.[ri];
  if (!kind || !rule) return;
  const ci = Number(target.dataset.ci);
  if (kind === "event") {
    if (target.value === "__custom__") { rule.event = ""; rule.customEvent = ""; }
    else rule.event = target.value;
    xbRenderRules();
  } else if (kind === "custom-event") { rule.customEvent = target.value; rule.event = ""; }
  else if (kind === "cond-field") rule.conditions[ci].field = target.value.trim();
  else if (kind === "cond-op") {
    rule.conditions[ci].op = target.value;
    xbRenderRules();  // value input appears/disappears
  } else if (kind === "cond-value") rule.conditions[ci].value = target.value;
  else if (kind === "action-type") { rule.action.type = target.value; xbRenderRules(); }
  else if (kind === "action-level") rule.action.level = target.value;
  else if (kind === "action-text") rule.action.text = target.value;
  else if (kind === "action-title") rule.action.title = target.value;
  else if (kind === "action-category") rule.action.category = target.value;
  else if (kind === "action-voice") rule.action.voice = target.checked;
}

// "50" → 50, "true" → true — journal values are typed, string-compare fails.
function xbCoerce(text) {
  const value = String(text).trim();
  if (value === "true") return true;
  if (value === "false") return false;
  if (value !== "" && !isNaN(Number(value))) return Number(value);
  return value;
}

function xbCollect() {
  const name = $("xb-name").value.trim();
  const id = xbModel.editingId || xbSlug(name);
  if (!name) throw new Error("Give the extension a name.");
  if (!id) throw new Error("The name must contain at least two letters or digits.");
  const rules = [];
  const permissions = new Set(["read:journal"]);
  for (const rule of xbModel.rules) {
    const event = rule.event || rule.customEvent.trim();
    if (!event) throw new Error("Every rule needs an event.");
    const when = {};
    for (const c of rule.conditions) {
      if (!c.field) throw new Error("Conditions need a field name.");
      if (c.op === "exists") when[c.field] = { exists: true };
      else if (c.op === "absent") when[c.field] = { exists: false };
      else if (c.op === "in") when[c.field] = { in: c.value.split(",").map((v) => xbCoerce(v)).filter((v) => v !== "") };
      else if (c.op === "min" || c.op === "max") {
        const n = Number(c.value);
        if (isNaN(n)) throw new Error(`"${c.field}" needs a numeric value for ${c.op === "min" ? "at least" : "at most"}.`);
        when[c.field] = { [c.op]: n };
      } else when[c.field] = { eq: xbCoerce(c.value) };
    }
    const action = { type: rule.action.type };
    if (rule.action.type === "alert") {
      if (!rule.action.text.trim()) throw new Error("Alert rules need alert text.");
      action.text = rule.action.text.trim();
      action.level = rule.action.level || "info";
      action.code = "user." + id;
      if (rule.action.voice) action.say = action.text;
      permissions.add("emit:alert");
    } else {
      if (!rule.action.title.trim()) throw new Error("Objective rules need a title.");
      action.title = rule.action.title.trim();
      if (rule.action.category.trim()) action.category = rule.action.category.trim();
      permissions.add("emit:objective");
    }
    const entry = { event, action };
    if (Object.keys(when).length) entry.when = when;
    rules.push(entry);
  }
  if (!rules.length) throw new Error("Add at least one rule.");
  return { id, api_version: 1, name, version: "1", permissions: [...permissions], rules };
}

async function xbTest() {
  const status = $("xb-status");
  const results = $("xb-results");
  const button = $("xb-test");
  let manifest;
  try { manifest = xbCollect(); } catch (err) { status.textContent = String(err.message || err); return; }
  button.disabled = true;
  status.textContent = "Replaying your recent history…";
  results.classList.add("hidden");
  try {
    const headers = { "Content-Type": "application/json" };
    const commanderId = profileStorageId();
    if (commanderId) headers["X-Frameshift-Commander"] = commanderId;
    const resp = await fetch("/api/extensions/test", {
      method: "POST", headers, body: JSON.stringify({ manifest }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || "Test failed");
    const matches = data.matches || [];
    status.textContent = matches.length
      ? `Scanned your last ${data.scanned} events — this would have fired ${matches.length} time${matches.length === 1 ? "" : "s"}${data.truncated ? " (showing the first " + matches.length + ")" : ""}:`
      : `Scanned your last ${data.scanned} events — no matches. The rule may still be right (nothing recent qualified); loosen a condition to see it fire.`;
    results.innerHTML = matches.slice(0, 12).map((m) =>
      `<div class="xb-hit"><span class="mono dim">${esc((m.timestamp || "").replace("T", " ").replace("Z", ""))}</span>` +
      `<span class="mono">${esc(m.event_type || "?")}</span>` +
      `<span class="xb-hit-msg ${m.action.level === "critical" ? "bad" : m.action.level === "warn" ? "warn" : ""}">` +
      `${m.action.type === "objective" ? "◎ " : "⚠ "}${esc(m.action.text || m.action.title || "")}</span></div>`).join("");
    results.classList.toggle("hidden", !matches.length);
  } catch (err) {
    status.textContent = String(err.message || err);
  } finally {
    button.disabled = false;
  }
}

async function xbSave(ev) {
  ev.preventDefault();
  if (xbBusy) return;
  const status = $("xb-status");
  let manifest;
  try { manifest = xbCollect(); } catch (err) { status.textContent = String(err.message || err); return; }
  xbBusy = true;
  $("xb-save").disabled = true;
  status.textContent = "Saving…";
  try {
    const resp = await fetch("/api/extensions/save", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ manifest }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || "Save failed");
    xbClose();
    await loadLocalServices();
    $("xb-status").textContent = "";
  } catch (err) {
    status.textContent = String(err.message || err);
  } finally {
    xbBusy = false;
    $("xb-save").disabled = false;
  }
}

// Load an installed declarative pack back into the builder form.
function xbRuleFromManifest(rule) {
  const conditions = [];
  for (const [field, expected] of Object.entries(rule.when || {})) {
    if (expected && typeof expected === "object") {
      if ("exists" in expected) conditions.push({ field, op: expected.exists ? "exists" : "absent", value: "" });
      if ("eq" in expected) conditions.push({ field, op: "eq", value: String(expected.eq) });
      if ("in" in expected) conditions.push({ field, op: "in", value: (expected.in || []).join(", ") });
      if ("min" in expected) conditions.push({ field, op: "min", value: String(expected.min) });
      if ("max" in expected) conditions.push({ field, op: "max", value: String(expected.max) });
    } else {
      conditions.push({ field, op: "eq", value: String(expected) });
    }
  }
  const action = rule.action || {};
  const known = XB_EVENTS.some((e) => e.id === rule.event);
  return {
    event: known ? rule.event : "",
    customEvent: known ? "" : rule.event,
    conditions,
    action: {
      type: action.type === "objective" ? "objective" : "alert",
      level: action.level || "info",
      text: action.text || "",
      voice: !!action.say,
      title: action.title || "",
      category: action.category || "",
    },
  };
}

async function xbEditPack(extensionId) {
  try {
    const resp = await fetch(`/api/extensions/${encodeURIComponent(extensionId)}/manifest`);
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || "Extension could not be loaded");
    const manifest = data.manifest || {};
    xbOpen({
      name: manifest.name || extensionId,
      editingId: manifest.id || extensionId,
      rules: (manifest.rules || []).map(xbRuleFromManifest),
    });
    $("ext-builder-card").scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (err) {
    $("extensions-status").textContent = String(err.message || err);
  }
}

async function xbDeletePack(extensionId, button) {
  if (!confirm(`Remove the extension "${extensionId}"? Its alerts and suggestions stop immediately.`)) return;
  if (button) button.disabled = true;
  try {
    const resp = await fetch(`/api/extensions/${encodeURIComponent(extensionId)}`, { method: "DELETE" });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || "Extension could not be removed");
    await loadLocalServices();
  } catch (err) {
    $("extensions-status").textContent = String(err.message || err);
  } finally {
    if (button) button.disabled = false;
  }
}

function initExtensionBuilder() {
  const templates = $("xb-templates");
  if (!templates) return;
  templates.innerHTML = XB_TEMPLATES.map((t, i) =>
    `<button type="button" class="xb-template" data-template="${i}">${esc(t.label)}</button>`).join("") +
    `<button type="button" class="xb-template xb-blank" data-template="blank">Blank</button>`;
  templates.addEventListener("click", (ev) => {
    const chip = ev.target.closest("[data-template]");
    if (!chip) return;
    const t = XB_TEMPLATES[Number(chip.dataset.template)];
    xbOpen(t ? { name: t.name, rules: [t.rule] } : null);
  });
  $("xb-new").addEventListener("click", () => xbOpen(null));
  $("xb-cancel").addEventListener("click", xbClose);
  $("xb-add-rule").addEventListener("click", () => { xbModel.rules.push(xbBlankRule()); xbRenderRules(); });
  $("xb-test").addEventListener("click", xbTest);
  $("xb-form").addEventListener("submit", xbSave);
  $("xb-name").addEventListener("input", xbSyncId);
  $("xb-rules").addEventListener("click", (ev) => {
    const target = ev.target.closest("[data-xb]");
    if (target && xbHandleClick(target)) ev.preventDefault();
  });
  $("xb-rules").addEventListener("input", (ev) => {
    const target = ev.target.closest("[data-xb]");
    if (target) xbHandleInput(target);
  });
}

async function changeExtensionApproval(extensionId, action, button) {
  if (!extensionId) return;
  if (action === "edit") { xbEditPack(extensionId); return; }
  if (action === "delete") { xbDeletePack(extensionId, button); return; }
  if (!["approve", "revoke"].includes(action)) return;
  if (action === "approve" && !window.confirm(
    "Approve this exact process extension build? It can execute local code and is not an operating-system sandbox. Any code change will require approval again."
  )) return;
  button.disabled = true;
  try {
    const response = await fetch(
      `/api/extensions/${encodeURIComponent(extensionId)}/${action}`,
      { method: "POST" },
    );
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Extension approval could not be changed.");
    await loadLocalServices();
  } catch (error) {
    $("extensions-status").textContent = String(error.message || error);
  } finally {
    button.disabled = false;
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
let plotCancelling = false;

function setPlotStatus(text, isError) {
  for (const id of ["plot-status", "fp-plot-status"]) {
    const el = $(id);
    if (!el) continue;
    el.classList.toggle("error", !!isError);
    el.textContent = text;
  }
}

// While a plot is running the PLOT button turns into a CANCEL button, so the
// same control that started it can stop it (the sequence types into the game
// for several seconds — an accidental plot is easy to abort).
function setPlotBusy(on) {
  plotBusy = on;
  for (const id of ["plot-btn", "fp-plot-btn"]) {
    const btn = $(id);
    if (!btn) continue;
    btn.textContent = on ? "CANCEL" : "PLOT";
    btn.classList.toggle("danger", on);
  }
}

async function cancelPlot() {
  if (!plotBusy || plotCancelling) return;
  plotCancelling = true;
  setPlotStatus("Cancelling — releasing keys…", false);
  try {
    await fetch("/api/plot/cancel", { method: "POST" });
  } catch (err) {
    /* the in-flight plot request will still report the outcome */
  }
}

async function plotSystem(system) {
  if (!system || plotBusy) return;
  setPlotBusy(true);
  setPlotStatus(`Plotting route to ${system} — leave the game window alone. Tap CANCEL to stop.`, false);
  try {
    const resp = await fetch("/api/plot", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ system }),
    });
    const data = await resp.json();
    if (data.cancelled) {
      setPlotStatus("Plot cancelled.", false);
      return;
    }
    if (!resp.ok) throw new Error(data.error || "Plot failed");
    setPlotStatus(`Sent plot sequence for ${system} — check the game.`, false);
    speak("Route plotted to " + system);
  } catch (err) {
    setPlotStatus(String(err.message || err), true);
  } finally {
    setPlotBusy(false);
    plotCancelling = false;
  }
}

/* ---------- voice callouts (F8) ---------- */

let voiceOn = localStorage.getItem("voice") === "1";

/* Neural voice (Piper on the server): set by /api/tts/status. When ready and
   enabled on this device, callouts play server-synthesized audio — every
   device hears the same human-sounding voice. Browser TTS is the fallback. */
let ttsReady = false;
// On by default once the voice is installed; per-device opt-out.
const neuralVoiceEnabled = () => ttsReady && localStorage.getItem("neuralVoice") !== "0";
let calloutAudio = null;

let calloutObjectUrl = null;

async function playNeural(text) {
  if (calloutAudio) calloutAudio.pause();  // don't stack stale callouts
  if (calloutObjectUrl) URL.revokeObjectURL(calloutObjectUrl);
  const resp = await fetch("/api/speak", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  if (!resp.ok) throw new Error("voice synthesis failed");
  calloutObjectUrl = URL.createObjectURL(await resp.blob());
  calloutAudio = new Audio(calloutObjectUrl);
  calloutAudio.volume = voiceVolume();
  calloutAudio.addEventListener("ended", () => {
    if (calloutObjectUrl) URL.revokeObjectURL(calloutObjectUrl);
    calloutObjectUrl = null;
  }, { once: true });
  return calloutAudio.play();
}

function speak(text, force) {
  if ((!voiceOn && !force) || !text) return;
  if (neuralVoiceEnabled()) {
    try {
      playNeural(text).catch(() => speakBrowser(text));
      return;
    } catch (e) { /* fall through to the browser voice */ }
  }
  speakBrowser(text);
}

function speakBrowser(text) {
  if (!("speechSynthesis" in window)) return;
  try {
    const u = new SpeechSynthesisUtterance(text);
    u.rate = 1.05;
    u.pitch = 1;
    u.volume = voiceVolume();
    window.speechSynthesis.cancel();  // don't queue stale callouts
    window.speechSynthesis.speak(u);
  } catch (e) { /* speech is a nicety */ }
}

async function loadTtsStatus() {
  try {
    const resp = await fetch("/api/tts/status", { cache: "no-store" });
    if (!resp.ok) return null;
    const data = await resp.json();
    ttsReady = !!data.ready;
    return data;
  } catch (e) {
    return null;
  }
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

const PANEL_PAGES = ["status", "trade", "commodities", "bio", "guides", "analytics", "engineering", "galaxy", "ops", "specialists", "local", "database"];

/* A new browser/device starts in the cockpit panel.  Treat only the explicit
   values written by the UI as preferences; initialization itself must not
   manufacture or replace one. */
function panelModeOnLaunch(storage = localStorage) {
  try {
    const saved = storage.getItem("panelMode");
    return saved == null ? true : saved === "1";
  } catch (_error) {
    return true;
  }
}

function setPanelMode(on, persist = true) {
  document.body.classList.toggle("panel-mode", on);
  if (persist) localStorage.setItem("panelMode", on ? "1" : "0");
  // Fullscreen is opt-in via the rail's ⛶ FULL button; leaving the panel
  // always drops back out of it.
  if (!on && document.fullscreenElement) {
    document.exitFullscreen().catch(() => {});
  }
  if (on) {
    runPanelBoot();
    setPanelPage(localStorage.getItem("panelPage") || "status");
  } else {
    $("flight-panel").classList.add("hidden");
  }
  document.documentElement.classList.remove("panel-mode-prepaint");
}

function toggleFullscreen() {
  if (document.fullscreenElement) {
    document.exitFullscreen().catch(() => {});
  } else if (document.documentElement.requestFullscreen) {
    document.documentElement.requestFullscreen().catch(() => {});
  }
}

/* One-shot boot splash when the panel is entered. The CSS animation fades it
   out; re-hiding it afterwards keeps it out of the way of screen readers and
   lets the next entry replay the animation from the start. */
let bootTimer = null;
function runPanelBoot() {
  const boot = $("fp-boot");
  if (!boot) return;
  clearTimeout(bootTimer);
  boot.classList.add("hidden");
  void boot.offsetWidth;  // restart the CSS animations
  boot.classList.remove("hidden");
  bootTimer = setTimeout(() => boot.classList.add("hidden"), 1800);
}

/* The status strip's clock (HH:MM:SS, local time) */
setInterval(() => {
  if (!document.body.classList.contains("panel-mode")) return;
  const el = $("fp-clock");
  if (!el) return;
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  el.textContent = `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}, 1000);

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
  // When a directional slide is about to play, skip the fade-up entrance —
  // two stacked animations is what caused the post-slide content flash.
  if (!statusPage) activateTab(name, !slideDir);
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
  // Scoped to [data-page]: the rail also holds the voice and exit buttons,
  // which have their own handlers.
  document.querySelectorAll("#fp-nav button[data-page]").forEach((b) =>
    b.addEventListener("click", () => {
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
    if (document.body.classList.contains("arranging")) return;  // dragging cards, not pages
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

/* Rebuy readout: red when the balance can't cover one rebuy, amber when it
   can't cover two — the same thresholds the voice callout uses. */
function renderRebuy(el) {
  if (!el) return;
  if (!state.rebuy) { el.textContent = "—"; el.classList.remove("bad", "low"); return; }
  el.textContent = shortCr(state.rebuy) + " cr";
  const c = state.credits;
  const covered1 = c != null && c >= state.rebuy;
  const covered2 = c != null && c >= state.rebuy * 2;
  el.classList.toggle("bad", c != null && !covered1);
  el.classList.toggle("low", covered1 && !covered2);
  el.title = c == null ? "Your ship's insurance cost"
    : !covered1 ? "REBUY NOT COVERED — you cannot afford to lose this ship"
    : !covered2 ? "Less than 2 rebuys in the bank"
    : "Insurance covered";
}

/* ---------- arrangement mode: drag cards to reorder any page ---------- */
/* Card order is saved per tab (and per device) as a flat list of data-arr
   keys. Cards reorder only among themselves within their container, so fixed
   chrome (intro text, grids) keeps its place. */

const arrKey = (el) => el.dataset.arr || "";

function arrContainers(pane) {
  return [pane, ...pane.querySelectorAll(".two-col")];
}

function applyCardOrders() {
  document.querySelectorAll(".tabpane").forEach((pane) => {
    let saved;
    try { saved = JSON.parse(localStorage.getItem("cardOrder:" + pane.id)); } catch { return; }
    if (!Array.isArray(saved) || !saved.length) return;
    for (const container of arrContainers(pane)) {
      // Orderable units: keyed cards and keyed grid blocks (.two-col).
      const units = [...container.children].filter((el) => arrKey(el));
      if (units.length < 2) continue;
      // Re-place sorted units into the same DOM slots so unkeyed siblings stay put.
      const slots = units.map(() => document.createComment("card-slot"));
      units.forEach((el, i) => container.replaceChild(slots[i], el));
      const pos = (el) => { const i = saved.indexOf(arrKey(el)); return i === -1 ? 1e9 : i; };
      const sorted = [...units].sort((a, b) => pos(a) - pos(b));
      slots.forEach((slot, i) => container.replaceChild(sorted[i], slot));
    }
  });
}

function saveCardOrder(pane) {
  const keys = [...pane.querySelectorAll("[data-arr]")].map(arrKey);
  localStorage.setItem("cardOrder:" + pane.id, JSON.stringify(keys));
}

/* ---------- card visibility: hide cards you never use (per device) ----------
   Complements arrange mode: every commander plays differently, and every card
   someone else needs is noise to you. Hidden keys are stored per tab like the
   order; hidden cards stay visible (dimmed) while arranging so they can be
   restored, and are display:none otherwise. */

/* The engineering cards used to live on LOCAL; carry any per-device order and
   hidden choices for them over to the new tab so customizations survive. */
function migrateEngineeringLayout() {
  const moved = ["engineers", "materials", "odyssey", "engplanner"];
  try {
    const localHidden = JSON.parse(localStorage.getItem("cardHidden:tab-local")) || [];
    const carried = localHidden.filter((k) => moved.includes(k));
    if (carried.length) {
      const engHidden = new Set(JSON.parse(localStorage.getItem("cardHidden:tab-engineering")) || []);
      carried.forEach((k) => engHidden.add(k));
      localStorage.setItem("cardHidden:tab-engineering", JSON.stringify([...engHidden]));
      localStorage.setItem("cardHidden:tab-local",
        JSON.stringify(localHidden.filter((k) => !moved.includes(k))));
    }
    const localOrder = JSON.parse(localStorage.getItem("cardOrder:tab-local"));
    if (Array.isArray(localOrder) && localOrder.some((k) => moved.includes(k))) {
      localStorage.setItem("cardOrder:tab-local",
        JSON.stringify(localOrder.filter((k) => !moved.includes(k))));
    }
  } catch { /* fresh device — nothing to migrate */ }
}

function hiddenCardKeys(paneId) {
  try {
    const v = JSON.parse(localStorage.getItem("cardHidden:" + paneId));
    return Array.isArray(v) ? v : [];
  } catch { return []; }
}

function setCardHidden(pane, card, hide) {
  const keys = new Set(hiddenCardKeys(pane.id));
  if (hide) keys.add(arrKey(card)); else keys.delete(arrKey(card));
  localStorage.setItem("cardHidden:" + pane.id, JSON.stringify([...keys]));
  applyCardVisibility();
  syncEyeButton(card);
}

function applyCardVisibility() {
  document.querySelectorAll(".tabpane").forEach((pane) => {
    const hidden = new Set(hiddenCardKeys(pane.id));
    let anyVisible = false, anyHidden = false;
    pane.querySelectorAll("section.card[data-arr], .two-col[data-arr] > section.card[data-arr]").forEach((card) => {
      const off = hidden.has(arrKey(card));
      card.classList.toggle("user-hidden", off);
      if (off) anyHidden = true;
      else if (!card.classList.contains("hidden")) anyVisible = true;
    });
    // If the whole page was hidden away, leave a way back.
    let note = pane.querySelector(".all-hidden-note");
    if (!anyVisible && anyHidden) {
      if (!note) {
        note = document.createElement("div");
        note.className = "dim empty all-hidden-note";
        note.textContent = "Every card on this page is hidden — tap ⇅ ARRANGE, then ⊕ SHOW to bring them back.";
        pane.appendChild(note);
      }
    } else if (note) {
      note.remove();
    }
  });
}

function syncEyeButton(card) {
  const eye = card.querySelector(".arr-eye");
  if (!eye) return;
  const off = card.classList.contains("user-hidden");
  eye.textContent = off ? "⊕ SHOW" : "⊘ HIDE";
  eye.title = off ? "Show this card again on this device"
    : "Hide this card on this device (restore it any time in arrange mode)";
}

function setArrangeMode(on) {
  document.body.classList.toggle("arranging", on);
  for (const btn of [$("arrange-btn"), $("fp-arrange")]) {
    if (!btn) continue;
    btn.classList.toggle("on", on);
    btn.setAttribute("aria-pressed", String(on));
  }
  $("arrange-btn").textContent = on ? "✓ DONE" : "⇅ ARRANGE";
  document.querySelectorAll(".arr-handle, .arr-eye").forEach((h) => h.remove());
  if (!on) return;
  document.querySelectorAll(".tabpane section.card[data-arr]").forEach((card) => {
    const h = document.createElement("button");
    h.type = "button";
    h.className = "arr-handle";
    h.textContent = "⠿ DRAG";
    h.setAttribute("aria-label", "Drag to reorder this card");
    h.addEventListener("pointerdown", (ev) => startCardDrag(ev, card));
    card.appendChild(h);
    const eye = document.createElement("button");
    eye.type = "button";
    eye.className = "arr-eye";
    eye.addEventListener("click", () => {
      const pane = card.closest(".tabpane");
      if (pane) setCardHidden(pane, card, !card.classList.contains("user-hidden"));
    });
    card.appendChild(eye);
    syncEyeButton(card);
  });
}

function startCardDrag(ev, card) {
  ev.preventDefault();
  card.classList.add("arr-drag");
  const pane = card.closest(".tabpane");

  // Listen on the document: reordering moves the card (and its handle) in the
  // DOM, which drops pointer capture mid-drag — document-level listeners keep
  // receiving the pointer no matter where the card lands.
  const onMove = (mv) => {
    if (mv.pointerId !== ev.pointerId) return;
    const container = card.parentElement;
    for (const sib of container.children) {
      if (sib === card || !arrKey(sib) || sib.classList.contains("hidden")) continue;
      const r = sib.getBoundingClientRect();
      if (mv.clientX < r.left || mv.clientX > r.right || mv.clientY < r.top || mv.clientY > r.bottom) continue;
      // Pointer is over a sibling unit. Swap only once the pointer crosses its
      // midpoint (on the axis the two are separated along) — plain edge-entry
      // swapping oscillates when the sibling is taller than the drag step.
      const cr = card.getBoundingClientRect();
      const horiz = Math.abs((r.left + r.right) - (cr.left + cr.right)) >
                    Math.abs((r.top + r.bottom) - (cr.top + cr.bottom));
      const mid = horiz ? (r.left + r.right) / 2 : (r.top + r.bottom) / 2;
      const pos = horiz ? mv.clientX : mv.clientY;
      const sibBefore = (card.compareDocumentPosition(sib) & Node.DOCUMENT_POSITION_PRECEDING) !== 0;
      if (sibBefore && pos < mid) container.insertBefore(card, sib);
      else if (!sibBefore && pos > mid) container.insertBefore(card, sib.nextSibling);
      break;
    }
  };
  const onUp = (up) => {
    if (up.pointerId !== ev.pointerId) return;
    document.removeEventListener("pointermove", onMove);
    document.removeEventListener("pointerup", onUp);
    document.removeEventListener("pointercancel", onUp);
    card.classList.remove("arr-drag");
    if (pane) saveCardOrder(pane);
  };
  document.addEventListener("pointermove", onMove);
  document.addEventListener("pointerup", onUp);
  document.addEventListener("pointercancel", onUp);
}

function renderPanel() {
  if (!document.body.classList.contains("panel-mode")) return;
  const stationTxt = state.docked && state.station
    ? `DOCKED · ${state.station}`
    : (state.body && state.body !== state.system ? `IN SPACE · ${state.body}` : "IN SPACE");

  $("fp-cmdr").textContent = state.commander ? "CMDR " + state.commander : "";
  $("fp-system").textContent = state.system || "—";
  const stationChip = $("fp-station");
  stationChip.textContent = state.docked ? "◆ " + stationTxt : stationTxt;
  stationChip.classList.toggle("inspace", !state.docked);
  $("fp-dest").textContent = state.destination ? `DESTINATION · ${state.destination}` : "";
  // Named ship first; otherwise the model, mending raw journal type strings
  // like "Diamondbackxl" / "Cobramkiii" into readable labels.
  const shipName = (state.ship_name || "").trim();
  const shipType = (state.ship_type || "").trim()
    .replace(/mk\s*(i+v?|vi*)$/i, (m, n) => " MK " + n.toUpperCase())
    .replace(/xl$/i, " XL");
  $("fp-ship-type").textContent = (shipName || shipType).toUpperCase();

  const fuelPct = state.fuel_capacity > 0 ? Math.min(100, (state.fuel_main / state.fuel_capacity) * 100) : 0;
  const fuelTxt = state.fuel_main != null
    ? `${state.fuel_main.toFixed(1)} / ${(state.fuel_capacity || 0).toFixed(0)} t` : "—";
  const fill = $("fp-fuel-fill");
  fill.style.width = fuelPct + "%";
  fill.style.background = fuelPct < 25 ? "var(--bad)" : "";
  $("fp-fuel").textContent = fuelTxt;

  // Fuel note: jumps of fuel at recent burn + whether there's a scoop here/next
  const nav = state.nav || {};
  const ahead = nav.ahead || [];
  const fuelNotes = [];
  if (nav.jumps_of_fuel != null) fuelNotes.push(`≈${nav.jumps_of_fuel} JUMPS AT CURRENT BURN`);
  const scoop = ahead[0]?.scoopable ? "SCOOPABLE STAR IN SYSTEM"
    : ahead[1]?.scoopable ? "NEXT STAR IS SCOOPABLE" : "";
  $("fp-fuel-note").innerHTML =
    `<span>${fuelNotes.join(" · ")}</span>` + (scoop ? `<span class="good">${scoop}</span>` : "");

  const cargoPct = state.cargo_capacity > 0 ? Math.min(100, (state.cargo_tons / state.cargo_capacity) * 100) : 0;
  const cargoTxt = state.cargo_tons != null
    ? `${Math.round(state.cargo_tons)} / ${state.cargo_capacity || 0} t` : "—";
  $("fp-cargo-fill").style.width = cargoPct + "%";
  $("fp-cargo").textContent = cargoTxt;
  $("fp-cargo-note").textContent = state.cargo_capacity > 0
    ? (state.cargo_tons ? `${Math.max(0, state.cargo_capacity - Math.round(state.cargo_tons))} T FREE`
      : "HOLD EMPTY · READY FOR LOOP CARGO")
    : "";

  // Persistent status strip (visible on every panel page)
  $("fp-strip-system").textContent = state.system || "—";
  $("fp-strip-station").textContent = stationTxt;
  $("fp-strip-dest-block").classList.toggle("hidden", !state.destination);
  // Jumps left on the in-game route: nav.ahead includes the current system.
  const jumpsLeft = ahead.length > 1 ? ahead.length - 1 : 0;
  $("fp-strip-dest").textContent = state.destination
    ? state.destination + (jumpsLeft ? ` · ${jumpsLeft} JUMP${jumpsLeft === 1 ? "" : "S"}` : "") : "";
  const stripFuel = $("fp-strip-fuel-fill");
  stripFuel.style.width = fuelPct + "%";
  stripFuel.style.background = fuelPct < 25 ? "var(--bad)" : "";
  $("fp-strip-fuel").textContent = fuelTxt.replace(/ /g, "");
  $("fp-strip-cargo-fill").style.width = cargoPct + "%";
  $("fp-strip-cargo").textContent = cargoTxt.replace(/ /g, "");

  // Data-at-risk chip: unsold scans + samples vs. the ship's rebuy. Same
  // thresholds as the server's voice callout (10x warn, 50x critical).
  const atRisk = ((state.exploration || {}).total || 0) + (((state.bio || {}).vault || {}).total || 0);
  const risky = state.rebuy > 0 && atRisk >= 20e6 && atRisk >= state.rebuy * 10;
  $("fp-risk").classList.toggle("hidden", !risky);
  if (risky) {
    $("fp-risk").classList.toggle("crit", atRisk >= state.rebuy * 50);
    $("fp-risk-text").textContent =
      `≈${shortCr(atRisk)} cr unbanked · ${(atRisk / state.rebuy).toFixed(1).replace(/\.0$/, "")}× your rebuy`;
  }

  $("fp-credits").textContent = state.credits != null ? shortCr(state.credits) : "—";
  const legal = $("fp-legal");
  legal.textContent = (state.legal_state || "—").toUpperCase();
  legal.style.color = state.legal_state && state.legal_state !== "Clean" ? "var(--bad)" : "var(--good)";
  renderRebuy($("fp-rebuy"));
  // The telemetry tiles drop the "cr" unit — the column head says it once.
  $("fp-rebuy").textContent = $("fp-rebuy").textContent.replace(/ cr$/, "");
  const covers = $("fp-rebuy-covers");
  if (state.rebuy > 0 && state.credits != null) {
    const ratio = state.credits / state.rebuy;
    covers.textContent = `COVERS ${ratio >= 10 ? Math.round(ratio) : ratio.toFixed(1)}×`;
    covers.className = "fp-tel-sub" + (ratio < 1 ? " bad" : ratio < 2 ? " thin" : "");
  } else {
    covers.textContent = "";
  }
  const ex = state.exploration || {};
  $("fp-explo").textContent = ex.count ? "≈" + shortCr(ex.total) : "—";
  $("fp-explo-label").textContent = "EXPLO DATA" + (ex.count ? ` · ${ex.count} BODIES` : "");
  const vault = (state.bio || {}).vault || {};
  const species = (vault.items || []).length;
  $("fp-bio").textContent = species ? "≈" + shortCr(vault.total) : "—";
  $("fp-bio-label").textContent = "BIO SAMPLES" + (species ? ` · ${species} SPECIES` : "");
  $("fp-telemetry-at").textContent =
    "TELEMETRY " + new Date().toLocaleTimeString([], { hour12: false });
  $("fp-link").textContent = "LINK STABLE";

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

function copySystemButton(system) {
  const btn = document.createElement("button");
  btn.className = "copy";
  btn.type = "button";
  btn.title = "Copy system name";
  btn.setAttribute("aria-label", btn.title);
  btn.textContent = "⧉";
  btn.addEventListener("click", () => copyText(system, btn));
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

function profileStorageId(snapshot = state) {
  return String(snapshot?.commander_id || "").trim() || null;
}

function commanderFetch(url, options = {}) {
  const commanderId = profileStorageId();
  if (!commanderId) throw new Error("Wait for the commander profile before accessing local commander data.");
  return fetch(url, {
    ...options,
    headers: {
      ...(options.headers || {}),
      "X-Frameshift-Commander": commanderId,
    },
  });
}

function activeRouteKey(commanderId) {
  return `activeRoute:v2:${encodeURIComponent(commanderId)}`;
}

function loadActiveRoute(commanderId) {
  activeRouteCommander = commanderId || null;
  activeRoute = null;
  if (!commanderId) return;
  const key = activeRouteKey(commanderId);
  try {
    let raw = localStorage.getItem(key);
    // The old key had no commander discriminator. The first established
    // profile adopts it and removes the ambiguous copy; Live/Legacy or another
    // account can never inherit it afterwards.
    if (raw == null) {
      raw = localStorage.getItem("activeRoute");
      if (raw != null) {
        localStorage.setItem(key, raw);
        localStorage.removeItem("activeRoute");
      }
    }
    const parsed = JSON.parse(raw || "null");
    activeRoute = parsed && Array.isArray(parsed.waypoints) ? parsed : null;
  } catch (error) {
    activeRoute = null;
  }
}

function saveActiveRoute() {
  const commanderId = profileStorageId();
  if (!commanderId || activeRouteCommander !== commanderId) return;
  const key = activeRouteKey(commanderId);
  if (activeRoute) localStorage.setItem(key, JSON.stringify(activeRoute));
  else localStorage.removeItem(key);
}

function trackRoute(kind, label, waypoints) {
  waypoints = (waypoints || []).filter((w) => w && w.system);
  const commanderId = profileStorageId();
  if (!waypoints.length || !commanderId) return;
  activeRouteCommander = commanderId;
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
  renderPanelRouteLine();
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

/* Compact progress line under the destination on the flight panel's status
   page: a bar plus "WAYPOINT n / m" while a tracked route is active. */
function renderPanelRouteLine() {
  const line = $("fp-routeline");
  if (!line) return;
  if (!activeRoute || !activeRoute.waypoints.length) {
    line.classList.add("hidden");
    return;
  }
  const total = activeRoute.waypoints.length;
  const done = Math.min(activeRoute.index, total);
  line.classList.remove("hidden");
  $("fp-route-fill").style.width = (total ? Math.round((done / total) * 100) : 0) + "%";
  $("fp-route-text").textContent = done >= total
    ? "ROUTE COMPLETE"
    : `WAYPOINT ${done + 1} / ${total} · ${activeRoute.waypoints[done].system.toUpperCase()}`;
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
  renderRebuy($("rebuy"));

  renderGalaxyModeNotice();
  renderBanner();
  renderGameState();
  handleAlerts();
  renderLinks();
  renderMarket();
  renderJumps();
  renderCargo();
  renderBio();
  renderColonisation();
  renderSession(state.session);
  renderMissions(state.missions);
  renderMassacre();
  renderMaterials(state.materials);
  renderEngineers();
  renderStoredShips();
  renderOdysseyLocker();
  renderCarrier();
  renderGalaxy();
  refreshLoadoutExport();
  // Re-plan against all three inventories used by ship, synthesis and Odyssey
  // recipes whenever any one of them changes.
  const engineeringInventorySig = JSON.stringify([
    state.commander_id || null,
    state.materials || null,
    state.ship_locker || null,
    state.cargo_inventory || null,
  ]);
  if (engMatsSig !== engineeringInventorySig) {
    engMatsSig = engineeringInventorySig;
    loadEngineering();
  }
  if (syncRouteToPosition()) saveActiveRoute();
  renderRouteProgress();
  renderPanel();
  seedRouteForm();
}

function renderGalaxyModeNotice() {
  const banner = $("galaxy-mode-banner");
  const legacy = String(state.galaxy_mode || "live").toLowerCase() === "legacy";
  banner.classList.toggle("hidden", !legacy);
  if (legacy) {
    banner.textContent = "LEGACY GALAXY detected — commander history, engineering, objectives, and local specialist tools remain available. Live community market, routing, outfitting, and galaxy searches are disabled so Horizons 3.8 data cannot be mixed with the Live galaxy.";
  }
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
  const d = Math.floor(secs / 86400), h = Math.floor((secs % 86400) / 3600), m = Math.floor((secs % 3600) / 60);
  if (d) return `${d}d ${h}h`;
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
  // The clock stops at Shutdown (or crash detection) — a session isn't the
  // hours the app sat open, it's the hours the game ran.
  const until = sess.end_ts != null ? sess.end_ts : Date.now() / 1000;
  const dur = has ? Math.max(0, until - sess.start_ts) : null;
  const ended = has && sess.end_ts != null;
  const earned = has ? sess.earned : null;
  // Ignore cr/hr for the first couple of minutes so it doesn't read as ±millions.
  const crhr = (has && dur > 120 && earned != null) ? earned / (dur / 3600) : null;

  const earnedTxt = signedCr(earned);
  const crhrTxt = crhr == null ? "—" : (crhr >= 0 ? "+" : "−") + shortCr(Math.abs(crhr)) + " cr/hr";
  const jumpsTxt = has ? String(sess.jumps || 0) : "—";
  const lyTxt = has ? fmtNum(sess.ly || 0) + " ly" : "—";
  const durTxt = dur != null ? fmtDuration(dur) + (ended ? " · ended" : "") : "";
  const collectedTxt = has && sess.collected ? "≈" + shortCr(sess.collected) + " cr" : "—";

  // Flight-panel tiles
  setText("fp-sess-earned", earnedTxt);
  setText("fp-sess-crhr", crhrTxt);
  setText("fp-sess-jumps", jumpsTxt);
  setText("fp-sess-ly", lyTxt);
  setText("fp-sess-collected", collectedTxt);
  setText("fp-sess-since", durTxt ? "· " + durTxt.toUpperCase() : "");
  colorSign("fp-sess-earned", earned);

  // Analytics session card (live parts; trade profit/tons filled by loadAnalytics)
  setText("session-earned", earnedTxt);
  setText("session-crhr", crhrTxt);
  setText("session-duration", durTxt || "—");
  setText("session-jumps", jumpsTxt);
  setText("session-ly", lyTxt);
  setText("session-collected", collectedTxt);
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
    // With depot tracking, what matters aboard is the REMAINING amount, not
    // the original mission total (148 already delivered ≠ still owed).
    const need = m.commodity_symbol
      ? (m.to_deliver != null && m.delivered != null
          ? Math.max(0, m.to_deliver - m.delivered)
          : (m.count || 0))
      : 0;
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
      // Haulage depot progress (CargoDepot events; wing missions update from
      // wingmates' deliveries too).
      (m.to_deliver ? `· <span class="${(m.delivered || 0) >= m.to_deliver ? "good" : ""}">${fmtNum(m.delivered || 0)}/${fmtNum(m.to_deliver)} delivered</span> ` : "") +
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

/* ---------- system stations (facts + per-station market) ---------- */

async function loadSystemStations(ev) {
  if (ev) ev.preventDefault();
  const status = $("ss-status"), list = $("ss-list"), go = $("ss-go");
  const sys = $("ss-system").value.trim() || (state && state.system) || "";
  go.disabled = true;
  status.classList.remove("error");
  status.textContent = "Fetching stations… (~2–5s)";
  list.innerHTML = "";
  try {
    const resp = await fetch("/api/system-stations?system=" + encodeURIComponent(sys));
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || "Lookup failed");
    const sts = data.stations || [];
    status.textContent = sts.length
      ? `${sts.length} station${sts.length === 1 ? "" : "s"} in ${data.system}`
      : (data.note || "No stations known for this system.");
    for (const s of sts) list.appendChild(stationRow(s));
  } catch (err) {
    status.classList.add("error");
    status.textContent = String(err.message || err);
  } finally {
    go.disabled = false;
  }
}

function stationRow(s) {
  const div = document.createElement("div");
  div.className = "sst";
  const pads = s.pads && (s.pads.l || s.pads.m || s.pads.s)
    ? `pads L${s.pads.l}/M${s.pads.m}/S${s.pads.s}` : null;
  const facts = [
    s.type, s.body ? "on " + s.body : null,
    s.dist_ls != null ? fmtNum(Math.round(s.dist_ls)) + " ls" : null,
    pads, s.economy, s.faction,
  ].filter(Boolean);
  div.innerHTML =
    `<div class="sst-line"><b>${esc(s.station)}</b>` +
    `<span class="dim sst-facts">${esc(facts.join(" · "))}</span></div>` +
    ((s.services || []).length
      ? `<div class="sst-services">${s.services.map((sv) => `<span class="chip">${esc(sv)}</span>`).join("")}</div>` : "") +
    `<div class="sst-market hidden"></div>`;
  const line = div.querySelector(".sst-line");
  if (s.local_market) {
    const btn = document.createElement("button");
    btn.className = "copy";
    btn.textContent = "▤ MARKET";
    btn.title = "This station's commodity market from your local database (EDDN-fresh)";
    btn.addEventListener("click", () => toggleStationMarket(div, s.market_id, btn));
    line.appendChild(btn);
  }
  return div;
}

async function toggleStationMarket(div, marketId, btn) {
  const box = div.querySelector(".sst-market");
  if (!box.classList.contains("hidden")) { box.classList.add("hidden"); return; }
  if (!box.dataset.loaded) {
    btn.disabled = true;
    try {
      const resp = await fetch("/api/station-market?market_id=" + marketId);
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error || "No market data");
      const rows = (data.items || []).map((i) =>
        `<tr><td>${esc(i.name)}<div class="sub">${esc(i.category)}</div></td>` +
        `<td class="num">${i.sell ? i.sell.toLocaleString() : "—"}</td>` +
        `<td class="num">${i.buy ? i.buy.toLocaleString() : "—"}</td>` +
        `<td class="num">${i.demand ? i.demand.toLocaleString() : "—"}</td>` +
        `<td class="num">${i.stock ? i.stock.toLocaleString() : "—"}</td></tr>`).join("");
      box.innerHTML =
        `<div class="table-wrap"><table><thead><tr><th>Commodity</th>` +
        `<th class="num">Sell</th><th class="num">Buy</th><th class="num">Demand</th><th class="num">Stock</th></tr></thead>` +
        `<tbody>${rows}</tbody></table></div>` +
        `<div class="dim sst-updated">${(data.items || []).length} commodities · prices as of ` +
        `${data.updated_at ? new Date(data.updated_at * 1000).toLocaleString() : "?"}</div>`;
      box.dataset.loaded = "1";
    } catch (err) {
      box.innerHTML = `<div class="dim">${esc(String(err.message || err))}</div>`;
    } finally {
      btn.disabled = false;
    }
  }
  box.classList.remove("hidden");
}

/* ---------- combat: massacre stacks ---------- */

function renderMassacre() {
  const card = $("massacre-card");
  const combat = state.combat || {};
  const stacks = combat.massacre || [];
  const show = stacks.length > 0 || (combat.kills || 0) > 0;
  card.classList.toggle("hidden", !show);
  if (!show) return;

  $("massacre-reward").textContent = stacks.length
    ? "≈" + shortCr(stacks.reduce((a, s) => a + (s.reward || 0), 0)) + " cr" : "";
  $("combat-session").textContent =
    `This session: ${combat.kills || 0} kills · ` +
    `bounty claims ≈${shortCr(combat.bounty_cr || 0)} cr · ` +
    `bond claims ≈${shortCr(combat.bonds_cr || 0)} cr — redeem before you lose them.`;

  const list = $("massacre-list");
  const sig = JSON.stringify(stacks);
  if (list.dataset.sig === sig) return;
  list.dataset.sig = sig;
  list.innerHTML = "";
  for (const s of stacks) {
    const pct = s.kills_needed ? Math.round((s.kills_done / s.kills_needed) * 100) : 0;
    const div = document.createElement("div");
    div.className = "stack" + (s.complete ? " done" : "");
    div.innerHTML =
      `<div class="stack-line"><b>${esc(s.faction)}</b>` +
      `<span class="dim">${s.missions} mission${s.missions === 1 ? "" : "s"} · ${s.givers} giver${s.givers === 1 ? "" : "s"}</span>` +
      `<span class="profit">≈${shortCr(s.reward)} cr</span></div>` +
      `<div class="stack-bar"><div style="width:${pct}%"></div></div>` +
      `<div class="stack-sub ${s.complete ? "good" : "dim"}">` +
      (s.complete ? "✓ STACK COMPLETE — hand your missions in" : `${s.kills_done} / ${s.kills_needed} kills`) +
      `</div>`;
    list.appendChild(div);
  }
}

/* ---------- engineering planner ---------- */
let engMatsSig = null;
let engCatalog = [];
let engCatalogById = new Map();
let engKindLabels = {};

async function loadEngineering() {
  const summary = $("engplan-summary");
  const expectedCommander = profileStorageId();
  const generation = profileGeneration;
  if (!expectedCommander) {
    if (summary) summary.innerHTML = '<div class="dim ep-api-error">Waiting for the commander profile...</div>';
    return;
  }
  try {
    const resp = await fetch("/api/engineering");
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
    if (generation !== profileGeneration || profileStorageId() !== expectedCommander
        || data.commander_id !== expectedCommander) return;
    const localCatalog = data.catalog || {};
    engCatalog = (localCatalog.groups || []).filter((item) => !item.alias_of);
    engCatalogById = new Map(engCatalog.map((item) => [item.id, item]));
    engKindLabels = localCatalog.kind_labels || {};
    const catalogStats = localCatalog.stats || {};
    fillEngineeringKinds(catalogStats);
    fillEngineeringCatalog();
    renderEngPlans(data.wishlist || { items: data.pinned || [], materials: [] });
    setText("ep-catalog-count", `${engCatalog.length.toLocaleString()} items · ${(catalogStats.recipes || 0).toLocaleString()} recipes`);
  } catch (error) {
    if (generation !== profileGeneration || profileStorageId() !== expectedCommander) return;
    if (summary) summary.innerHTML = `<div class="warn ep-api-error">Engineering planner unavailable: ${esc(error.message)}</div>`;
  }
}

function fillEngineeringKinds(stats) {
  const select = $("ep-kind");
  if (!select || select.options.length > 1) return;
  const counts = stats.categories || {};
  for (const [kind, label] of Object.entries(engKindLabels)) {
    if (!counts[kind]) continue;
    const option = document.createElement("option");
    option.value = kind;
    option.textContent = `${label} (${counts[kind]})`;
    select.appendChild(option);
  }
}

function engineeringMatches(item, query, kind) {
  if (kind && item.kind !== kind) return false;
  const haystack = [item.display_name, item.module, item.name, item.kind_label,
    ...(item.engineers || [])].join(" ").toLowerCase();
  return query.toLowerCase().split(/\s+/).filter(Boolean).every((word) => haystack.includes(word));
}

function fillEngineeringCatalog(preferredId) {
  const select = $("ep-blueprint");
  if (!select) return;
  const previous = preferredId || select.value;
  const query = ($("ep-search") && $("ep-search").value.trim()) || "";
  const kind = ($("ep-kind") && $("ep-kind").value) || "";
  const matches = engCatalog.filter((item) => engineeringMatches(item, query, kind));
  select.innerHTML = "";
  const groups = new Map();
  for (const item of matches) {
    const label = item.kind_label || item.kind;
    if (!groups.has(label)) groups.set(label, []);
    groups.get(label).push(item);
  }
  for (const [label, items] of groups) {
    const optgroup = document.createElement("optgroup");
    optgroup.label = label;
    for (const item of items) {
      const option = document.createElement("option");
      option.value = item.id;
      option.textContent = item.display_name;
      optgroup.appendChild(option);
    }
    select.appendChild(optgroup);
  }
  if (matches.some((item) => item.id === previous)) select.value = previous;
  setText("ep-match-count", `${matches.length} matching ${matches.length === 1 ? "recipe" : "recipes"}`);
  $("ep-pin").disabled = !matches.length;
  updateEngineeringGradeFields();
}

function addGradeOption(select, value, label) {
  const option = document.createElement("option");
  option.value = value;
  option.textContent = label;
  select.appendChild(option);
}

function updateEngineeringGradeFields(preferredCurrent, preferredTarget) {
  const item = engCatalogById.get($("ep-blueprint").value);
  const currentWrap = $("ep-current-wrap");
  const targetWrap = $("ep-target-wrap");
  if (!item) {
    currentWrap.classList.add("hidden");
    targetWrap.classList.add("hidden");
    setText("ep-desc", "No matching recipe.");
    return;
  }
  const isClimb = item.kind === "ship-engineering" || item.kind === "odyssey-upgrade";
  currentWrap.classList.toggle("hidden", !isClimb);
  targetWrap.classList.toggle("hidden", !isClimb);
  const grades = item.grades || [];
  if (isClimb && grades.length) {
    const target = $("ep-target");
    const oldTarget = Number(preferredTarget != null ? preferredTarget : grades[grades.length - 1]);
    target.innerHTML = "";
    for (const grade of grades) addGradeOption(target, grade, `Grade ${grade}${grade === grades[grades.length - 1] ? " (max)" : ""}`);
    target.value = grades.includes(oldTarget) ? String(oldTarget) : String(grades[grades.length - 1]);
    const targetGrade = Number(target.value);
    const current = $("ep-current");
    const defaultCurrent = item.kind === "odyssey-upgrade" ? Math.max(0, grades[0] - 1) : 0;
    const oldCurrent = Number(preferredCurrent != null ? preferredCurrent : defaultCurrent);
    current.innerHTML = "";
    addGradeOption(current, defaultCurrent, defaultCurrent ? `Grade ${defaultCurrent}` : "Stock / unengineered");
    for (const grade of grades.filter((grade) => grade < targetGrade && grade > defaultCurrent)) {
      addGradeOption(current, grade, `Grade ${grade}`);
    }
    const valid = [...current.options].some((option) => Number(option.value) === oldCurrent);
    current.value = String(valid ? oldCurrent : defaultCurrent);
  }
  const access = (item.engineer_access || []).map((engineer) =>
    `${engineer.name}${engineer.max_grade ? ` G${engineer.max_grade}` : ""}`);
  const engineerText = access.length ? ` · ${access.join(", ")}` : "";
  const gradeText = !isClimb && grades.length ? ` · tier G${grades.join("/")}` : "";
  $("ep-desc").innerHTML = `<b>${esc(item.kind_label || item.kind)}</b> · ${esc(item.module)}${gradeText}${esc(engineerText)}`;
}

function engineeringGradeText(item) {
  if (item.kind === "ship-engineering" || item.kind === "odyssey-upgrade") {
    const from = item.current_grade ? `G${item.current_grade}` : "stock";
    return `${from} → G${item.target_grade}`;
  }
  const catalogItem = engCatalogById.get(item.id);
  return catalogItem && catalogItem.grades.length ? `G${catalogItem.grades.join("/")} recipe` : "exact recipe";
}

function renderEngPlans(wishlist) {
  const list = $("engplan-list");
  const materials = $("engplan-materials");
  const summary = $("engplan-summary");
  if (!list || !materials || !summary) return;
  const items = wishlist.items || [];
  list.innerHTML = "";
  materials.innerHTML = "";
  if (!items.length) {
    summary.innerHTML = "";
    list.innerHTML = '<div class="dim empty ep-empty">Your wishlist is empty. Search the complete catalog above, choose the grade path and quantity, then add it here. A strong first ship upgrade is <b>Frame Shift Drive · Increased FSD Range</b>.</div>';
    return;
  }
  const readiness = wishlist.craftable ? "Everything is aboard"
    : wishlist.obtainable_with_suggested_trades ? "Material trades can close every listed gap"
      : `${wishlist.progress || 0}% directly collected`;
  summary.innerHTML = `<div class="ep-summary-head"><div><span class="ep-count">${items.length}</span> ` +
    `wishlist ${items.length === 1 ? "item" : "items"}</div><div class="${wishlist.craftable ? "good" : "dim"}">${esc(readiness)}</div></div>` +
    `<div class="stack-bar"><div style="width:${wishlist.progress || 0}%"></div></div>`;

  for (const item of items) {
    const card = document.createElement("div");
    card.className = "engplan ep-wish-item" + (item.craftable ? " done" : "");
    const engineers = (item.engineer_access || []).length
      ? item.engineer_access.map((engineer) => `${engineer.name}${engineer.max_grade ? ` G${engineer.max_grade}` : ""}`).join(", ")
      : "Synthesis / broker / merchant";
    const applicationText = item.kind === "ship-engineering"
      ? `${item.applications} deterministic application${item.applications === 1 ? "" : "s"}`
      : `${item.quantity} item${item.quantity === 1 ? "" : "s"}`;
    card.innerHTML = `<div class="ep-wish-main"><div><span class="chip ep-kind-chip">${esc(item.kind_label)}</span>` +
      `<b>${esc(item.blueprint)}</b></div><div class="ep-wish-actions"></div></div>` +
      `<div class="ep-wish-facts"><span>${esc(engineeringGradeText(item))}</span><span>×${item.quantity}</span>` +
      `<span>${esc(applicationText)}</span><span class="${item.craftable ? "good" : "dim"}">${item.craftable ? "Ready" : `${item.progress}% allocated`}</span></div>` +
      `<div class="dim ep-engineers">${esc(engineers)}</div>`;
    const actions = card.querySelector(".ep-wish-actions");
    const edit = document.createElement("button");
    edit.type = "button";
    edit.className = "copy";
    edit.textContent = "EDIT";
    edit.addEventListener("click", () => editEngineeringItem(item));
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "copy ep-remove";
    remove.textContent = "REMOVE";
    remove.addEventListener("click", () => pinBlueprint({ id: item.id, action: "unpin" }));
    actions.append(edit, remove);
    list.appendChild(card);
  }

  const materialRows = wishlist.materials || [];
  materials.innerHTML = `<div class="ep-shopping-head"><div class="label">CONSOLIDATED SHOPPING LIST</div>` +
    `<div class="dim">${materialRows.length} distinct ${materialRows.length === 1 ? "requirement" : "requirements"} · inventory is reserved once across the whole wishlist</div></div>`;
  const rows = document.createElement("div");
  rows.className = "ep-material-rows";
  for (const material of materialRows) {
    const short = material.deficit > 0;
    const grade = material.grade ? `G${material.grade} ` : "";
    const trade = material.trade ? `<div class="ep-trade"><b>VALID TRADE</b> ${material.trade.spend}× ${esc(material.trade.from)} ` +
      `covers ${material.trade.covers >= material.deficit ? "this shortfall" : `${material.trade.covers} of ${material.deficit}`}</div>` : "";
    const row = document.createElement("div");
    row.className = "ep-material-row" + (short ? " short" : " ready");
    row.innerHTML = `<div class="ep-material-status ${short ? "warn" : "good"}">${short ? "○" : "✓"}</div>` +
      `<div class="ep-material-body"><div class="ep-material-main"><b>${esc(material.name)}</b>` +
      `<span class="chip">${grade}${esc(material.kind)}</span><span class="ep-counts ${short ? "warn" : ""}">` +
      `${material.have} / ${material.need}${short ? ` · need ${material.deficit}` : ""}</span></div>` +
      `${trade}<details class="ep-source"><summary>WHERE TO FIND IT</summary><div>${esc(material.source || "No source note in the bundled catalog.")}</div></details></div>`;
    rows.appendChild(row);
  }
  materials.appendChild(rows);
}

function editEngineeringItem(item) {
  $("ep-search").value = "";
  $("ep-kind").value = item.kind;
  fillEngineeringCatalog(item.id);
  $("ep-blueprint").value = item.id;
  updateEngineeringGradeFields(item.current_grade, item.target_grade);
  $("ep-quantity").value = item.quantity;
  $("ep-pin").textContent = "UPDATE WISHLIST";
  $("engplan-form").scrollIntoView({ behavior: "smooth", block: "center" });
}

async function pinBlueprint(item) {
  try {
    const resp = await commanderFetch("/api/engineering/pin", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(item),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
    $("ep-pin").textContent = "ADD TO WISHLIST";
    await loadEngineering();
  } catch (error) {
    $("engplan-summary").innerHTML = `<div class="warn ep-api-error">Could not update wishlist: ${esc(error.message)}</div>`;
  }
}

async function findTraders() {
  const out = $("engplan-traders");
  out.innerHTML = '<div class="dim">Finding material traders near you… (~5s)</div>';
  try {
    const kinds = ["raw", "manufactured", "encoded"];
    const results = await Promise.all(kinds.map((k) =>
      fetch("/api/material-traders?kind=" + k).then((r) => r.json())));
    out.innerHTML = "";
    results.forEach((res, i) => {
      const t = (res.traders || [])[0];
      const div = document.createElement("div");
      div.className = "ep-trader";
      if (!t) {
        div.innerHTML = `<b>${kinds[i].toUpperCase()}</b> <span class="dim">${esc(res.error || "none found")}</span>`;
      } else {
        div.innerHTML = `<b>${kinds[i].toUpperCase()}</b> ${esc(t.station)} ` +
          `<span class="dim">${esc(t.system)} · ${t.distance} ly${t.large_pad ? " · L pad" : ""}</span>`;
        div.appendChild(plotButton(t.system));
      }
      out.appendChild(div);
    });
  } catch (e) {
    out.innerHTML = '<div class="dim">Trader search failed — try again.</div>';
  }
}

/* ---------- game-offline banner + launch button ---------- */

const LAUNCH_IDLE_LABEL = "▲ LAUNCH ELITE DANGEROUS";
const LAUNCH_IDLE_STATUS = "Game offline — showing your last session's data";
let launchSentAt = 0;
let launchStageTimer = null;

function resetLaunchUI(statusText) {
  launchSentAt = 0;
  clearTimeout(launchStageTimer);
  $("game-offline").classList.remove("launching");
  $("launch-game").disabled = false;
  $("launch-label").textContent = LAUNCH_IDLE_LABEL;
  $("launch-status").textContent = statusText;
}

/* Shown only when the server has positively probed the game as NOT running
   (null = not probed yet — stay quiet rather than flash a false banner). */
function renderGameState() {
  const bar = $("game-offline");
  const offline = state.game_running === false;
  bar.classList.toggle("show", offline);
  if (!offline && launchSentAt) {
    // Telemetry is flowing: the launch worked.
    resetLaunchUI(LAUNCH_IDLE_STATUS);
    showFlightToast({ level: "info", text: "✦ LAUNCH CONFIRMED · journal telemetry live · o7" });
  } else if (offline && launchSentAt && Date.now() - launchSentAt > 60000) {
    // The game writes its journal within seconds of starting, so a silent
    // minute means it isn't coming (killed during loading, launcher stuck).
    resetLaunchUI("No telemetry after a minute — the game may not have started. Launch again when ready.");
  }
}

async function launchGame() {
  if (launchSentAt) {
    // Second press while spooling = abort the wait (QA found the game can be
    // exited mid-load, which would otherwise leave the sequence hanging).
    // A short grace period first: an accidental double-tap on a touchscreen
    // must not "abort" a launch that is genuinely underway.
    if (Date.now() - launchSentAt < 2000) return;
    resetLaunchUI("Sequence aborted — launch again when ready.");
    return;
  }
  const bar = $("game-offline");
  const btn = $("launch-game");
  const status = $("launch-status");
  btn.disabled = true;  // only while the request itself is in flight
  bar.classList.add("launching");
  $("launch-label").textContent = "IGNITION SEQUENCE ENGAGED";
  status.textContent = "T-0 · handing off to the launcher…";
  try {
    const resp = await fetch("/api/launch-game", { method: "POST" });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || "Launch failed");
    launchSentAt = Date.now();
    btn.disabled = false;  // pressing again now aborts
    status.textContent = (data.already_running
      ? "The game is already running — waiting for its journal telemetry."
      : `T-0 · handed off to ${data.via} — spooling up…`) + " Press again to abort.";
    clearTimeout(launchStageTimer);
    launchStageTimer = setTimeout(() => {
      if (launchSentAt) status.textContent =
        "Awaiting journal telemetry — the cockpit takes a minute or two. Press again to abort.";
    }, 12000);
  } catch (err) {
    resetLaunchUI(String(err.message || err));
  }
}

/* ---------- where to sell exploration/bio data ---------- */

/* The deep-space "get me home" search: nearest ports with Universal
   Cartographics (map data) and Vista Genomics (bio samples). */
async function findSellPoints(ev) {
  ev.preventDefault();
  const btn = $("sd-go");
  const status = $("sd-status");
  const out = $("sd-results");
  btn.disabled = true;
  status.classList.remove("error");
  status.textContent = "Searching outward from your position… (~5s)";
  out.innerHTML = "";
  try {
    const resp = await fetch("/api/sell-data?carriers=" + ($("sd-carriers").checked ? "1" : "0"));
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || "Search failed");
    const sections = [
      ["carto", "UNIVERSAL CARTOGRAPHICS", "sells your exploration map data"],
      ["bio", "VISTA GENOMICS", "sells your bio samples"],
    ];
    status.textContent = `Nearest ports from ${data.reference || "your position"}:`;
    for (const [key, title, blurb] of sections) {
      const rows = data[key] || [];
      const sec = document.createElement("div");
      sec.className = "sd-section";
      sec.innerHTML = `<div class="label">${title} <span class="dim">${blurb}</span></div>`;
      if (!rows.length) {
        sec.innerHTML += '<div class="dim empty">None found — widen the search by including fleet carriers.</div>';
        out.appendChild(sec);
        continue;
      }
      const wrap = document.createElement("div");
      wrap.className = "table-wrap";
      const range = state && state.max_jump_range > 0 ? state.max_jump_range : null;
      const table = document.createElement("table");
      table.innerHTML =
        "<thead><tr><th>Station</th><th>System</th><th class=\"num\">Jump</th>" +
        (range ? `<th class="num" title="At your ship's ${range.toFixed(1)} ly jump range — before neutron boosts">≈ Jumps</th>` : "") +
        "<th class=\"num\">Star dist</th><th>Pad</th><th></th></tr></thead>";
      const tbody = document.createElement("tbody");
      for (const s of rows) {
        const tr = document.createElement("tr");
        tr.innerHTML =
          `<td>${esc(s.station)}${s.carrier ? ' <span class="chip" title="Fleet carriers move — this position may be stale. Check before committing to the trip.">CARRIER</span>' : ""}</td>` +
          `<td class="dim">${esc(s.system)}</td>` +
          `<td class="num">${fmtNum(s.distance)} ly</td>` +
          (range ? `<td class="num">${s.distance > 0 ? Math.max(1, Math.ceil(s.distance / range)) : 0}</td>` : "") +
          `<td class="num">${s.dist_ls != null ? fmtNum(Math.round(s.dist_ls)) + " ls" : "—"}</td>` +
          `<td>${s.large_pad ? "L" : "M/S"}</td>`;
        const td = document.createElement("td");
        td.className = "num";
        td.appendChild(plotButton(s.system));
        tr.appendChild(td);
        tbody.appendChild(tr);
      }
      table.appendChild(tbody);
      wrap.appendChild(table);
      sec.appendChild(wrap);
      out.appendChild(sec);
    }
  } catch (err) {
    status.classList.add("error");
    status.textContent = String(err.message || err);
  } finally {
    btn.disabled = false;
  }
}

/* ---------- interstellar factors (pay off bounties & fines) ---------- */

async function findInterstellarFactors(ev) {
  ev.preventDefault();
  const btn = $("iff-go");
  const status = $("iff-status");
  const out = $("iff-results");
  btn.disabled = true;
  status.classList.remove("error");
  status.textContent = "Searching outward from your position… (~5s)";
  out.innerHTML = "";
  try {
    const resp = await fetch("/api/interstellar-factors");
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || "Search failed");
    const rows = data.stations || [];
    if (!rows.length) {
      status.textContent = "None found nearby — try again from a more populated system.";
      return;
    }
    status.textContent = `Nearest Interstellar Factors from ${data.reference || "your position"}:`;
    const range = state && state.max_jump_range > 0 ? state.max_jump_range : null;
    const wrap = document.createElement("div");
    wrap.className = "table-wrap";
    const table = document.createElement("table");
    table.innerHTML =
      "<thead><tr><th>Station</th><th>System</th><th class=\"num\">Jump</th>" +
      (range ? `<th class="num" title="At your ship's ${range.toFixed(1)} ly jump range">≈ Jumps</th>` : "") +
      "<th class=\"num\">Star dist</th><th>Pad</th><th></th></tr></thead>";
    const tbody = document.createElement("tbody");
    for (const s of rows) {
      const tr = document.createElement("tr");
      tr.innerHTML =
        `<td>${esc(s.station)}</td>` +
        `<td class="dim">${esc(s.system)}</td>` +
        `<td class="num">${fmtNum(s.distance)} ly</td>` +
        (range ? `<td class="num">${s.distance > 0 ? Math.max(1, Math.ceil(s.distance / range)) : 0}</td>` : "") +
        `<td class="num">${s.dist_ls != null ? fmtNum(Math.round(s.dist_ls)) + " ls" : "—"}</td>` +
        `<td>${s.large_pad ? "L" : "M/S"}</td>`;
      const td = document.createElement("td");
      td.className = "num";
      td.appendChild(plotButton(s.system));
      tr.appendChild(td);
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    wrap.appendChild(table);
    out.appendChild(wrap);
  } catch (err) {
    status.classList.add("error");
    status.textContent = String(err.message || err);
  } finally {
    btn.disabled = false;
  }
}

/* ---------- current-ship export (EDSY link + SLEF copy) ---------- */

let loadoutSig = null;   // ship identity last exported, to refetch on changes
let loadoutSlef = "";    // SLEF JSON for the copy button

async function refreshLoadoutExport() {
  const a = $("build-edsy"), btn = $("build-slef"), desc = $("build-current-desc");
  if (!a) return;
  const sig = state.has_loadout
    ? [state.ship_type, state.ship_name, state.ship_ident, state.rebuy,
       state.max_jump_range, state.cargo_capacity].join("|")
    : "none";
  if (sig === loadoutSig) return;
  loadoutSig = sig;
  if (!state.has_loadout) {
    a.classList.add("hidden");
    btn.classList.add("hidden");
    return;
  }
  try {
    const resp = await fetch("/api/loadout-export");
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || "Export failed");
    const label = [state.ship_name || state.ship_type, state.ship_ident ? "(" + state.ship_ident + ")" : ""]
      .filter(Boolean).join(" ");
    desc.textContent = `${label || "Your ship"} — open the live build in EDSY to plan the next ` +
      "module or engineering upgrade, or copy SLEF and paste it into Coriolis / Inara.";
    a.href = data.edsy_url;
    a.classList.remove("hidden");
    loadoutSlef = data.slef;
    btn.classList.remove("hidden");
  } catch (e) {
    loadoutSig = null; // retry on the next state change
  }
}

/* ---------- engineering materials (F6) ---------- */

function renderMaterials(mats) {
  mats = mats || {};
  const groups = $("materials-groups");
  const total = mats.total || 0;
  $("materials-empty").classList.toggle("hidden", total > 0);
  $("materials-total").textContent = total ? total + " items" : "";

  // Jumponium readiness: how many FSD-injection synths the raw pile covers.
  const synth = state.synth;
  const line = $("synth-line");
  if (line) {
    line.classList.toggle("hidden", !synth || !total);
    if (synth && total) {
      line.innerHTML = `<b>FSD INJECTION</b> <span class="dim">(jumponium · one-jump range boost)</span> · ` +
        `basic ×${synth.basic} <span class="dim">(+25%)</span> · ` +
        `standard ×${synth.standard} <span class="dim">(+50%)</span> · ` +
        `premium ×${synth.premium} <span class="dim">(+100%)</span>`;
    }
  }
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

/* ---------- engineers (unlock progress) ---------- */

const ENG_STAGE_LABEL = {
  Unlocked: ["UNLOCKED", "ready to use — higher grades unlock as you craft with them"],
  Invited: ["INVITED", "visit their workshop once to unlock them"],
  Known: ["KNOWN", "meet their unlock terms first — requirements on Inara"],
};

function renderEngineers() {
  const list = $("engineers-list");
  if (!list) return;
  const engineers = state.engineers || [];
  $("engineers-empty").classList.toggle("hidden", engineers.length > 0);
  const unlocked = engineers.filter((e) => e.progress === "Unlocked").length;
  $("engineers-count").textContent = engineers.length
    ? `${unlocked} of ${engineers.length} unlocked` : "";
  const sig = JSON.stringify(engineers);
  if (list.dataset.sig === sig) return;
  list.dataset.sig = sig;
  list.innerHTML = "";
  for (const stage of ["Unlocked", "Invited", "Known"]) {
    const rows = engineers.filter((e) => e.progress === stage);
    if (!rows.length) continue;
    const [label, blurb] = ENG_STAGE_LABEL[stage];
    const sec = document.createElement("div");
    sec.className = "eng-stage";
    sec.innerHTML = `<div class="label">${label} <span class="dim">${rows.length} · ${blurb}</span></div>`;
    for (const e of rows) {
      const div = document.createElement("div");
      div.className = "eng-row";
      const pips = stage === "Unlocked" && e.rank
        ? `<span class="eng-pips" title="Grade ${e.rank} of 5 unlocked">${"●".repeat(e.rank)}${"○".repeat(Math.max(0, 5 - e.rank))} G${e.rank}</span>`
        : "";
      div.innerHTML =
        `<b>${esc(e.name)}</b>${e.on_foot ? ' <span class="chip" title="Odyssey on-foot engineer — upgrades suits and hand weapons">ON-FOOT</span>' : ""} ${pips}` +
        `<span class="dim">${e.offers ? esc(e.offers) : ""}${e.system ? (e.offers ? " · " : "") + esc(e.system) : ""}</span>`;
      if (e.system) div.appendChild(plotButton(e.system));
      sec.appendChild(div);
    }
    list.appendChild(sec);
  }
}

/* ---------- stored ships (fleet overview) ---------- */

function renderStoredShips() {
  const list = $("ships-list");
  if (!list) return;
  const st = state.stored_ships;
  const shipsHere = (st && st.here) || [];
  const remote = (st && st.remote) || [];
  $("ships-empty").classList.toggle("hidden", !!st);
  const total = shipsHere.length + remote.length + 1; // + the one you're flying
  $("ships-count").textContent = st ? `${total} ships` : "";
  const sig = JSON.stringify([st, state.ship_type, state.ship_name, state.system]);
  if (list.dataset.sig === sig) return;
  list.dataset.sig = sig;
  list.innerHTML = "";
  if (!st) return;

  const addRow = (title, sub, system) => {
    const div = document.createElement("div");
    div.className = "ship-row";
    div.innerHTML = `<b>${title}</b><span class="dim">${sub}</span>`;
    if (system) div.appendChild(plotButton(system));
    list.appendChild(div);
  };

  const flying = [state.ship_name, state.ship_type && `(${state.ship_type})`].filter(Boolean).join(" ");
  addRow(esc(flying || "Current ship"), "with you now" + (state.system ? ` · ${esc(state.system)}` : ""), null);
  for (const s of shipsHere) {
    addRow(shipTitle(s), `stored at ${esc(st.station || "this station")} · ${esc(st.system || "")}` + shipTags(s), null);
  }
  for (const s of remote) {
    const sub = `${esc(s.system || "?")}` +
      (s.in_transit ? " · in transit" :
        s.transfer_cr != null ? ` · transfer ${shortCr(s.transfer_cr)} cr · ${fmtDuration(s.transfer_s)}` : "") +
      shipTags(s);
    addRow(shipTitle(s), sub, s.system);
  }
  const note = document.createElement("div");
  note.className = "dim";
  note.textContent = `As of your shipyard visit at ${st.station || "?"} — transfers are paid at any shipyard and the ship flies itself to you.`;
  list.appendChild(note);
}

function shipTitle(s) {
  return esc([s.name, s.name ? `(${s.type})` : s.type].filter(Boolean).join(" "));
}

function shipTags(s) {
  return (s.hot ? ' · <span class="warn" title="This ship is wanted — landing at a normal station risks fines or worse. Clean it at an Interstellar Factors contact.">⚠ HOT</span>' : "") +
    (s.value != null ? ` · ${shortCr(s.value)} cr` : "");
}

/* ---------- odyssey locker (on-foot inventory) ---------- */

function renderOdysseyLocker() {
  const card = $("odyssey-card");
  if (!card) return;
  const locker = state.ship_locker;
  card.classList.toggle("hidden", !locker || !locker.total);
  if (!locker || !locker.total) return;
  $("odyssey-total").textContent = locker.total + " items";
  const groups = $("odyssey-groups");
  const sig = JSON.stringify(locker);
  if (groups.dataset.sig === sig) return;
  groups.dataset.sig = sig;
  groups.innerHTML = "";
  for (const [key, label] of [["items", "GOODS"], ["components", "ASSETS"],
                              ["data", "DATA"], ["consumables", "CONSUMABLES"]]) {
    const items = locker[key] || [];
    if (!items.length) continue;
    const col = document.createElement("div");
    col.className = "mat-group";
    col.innerHTML = `<div class="label">${label} <span class="dim">${items.length}</span></div>`;
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

/* ---------- fleet carrier ---------- */

const FC_FUEL_MAX = 1000; // tritium tank capacity, tons

function renderCarrier() {
  const card = $("fc-card");
  if (!card) return;
  const fc = state.carrier;
  card.classList.toggle("hidden", !fc);
  if (!fc) return;
  $("fc-ident").textContent = [fc.name, fc.callsign].filter(Boolean).join(" · ");
  $("fc-balance").textContent = fc.balance != null ? shortCr(fc.balance) + " cr" : "";
  const fuel = fc.fuel_t;
  const pct = fuel != null ? Math.min(100, Math.round((fuel / FC_FUEL_MAX) * 100)) : 0;
  const fill = $("fc-fuel-fill");
  fill.style.width = pct + "%";
  fill.style.background = fuel != null && fuel < 135 ? "var(--bad)" : "var(--good)"; // < ~one max jump + margin
  $("fc-fuel-text").textContent = fuel != null ? `${fmtNum(fuel)} / ${FC_FUEL_MAX} t` : "—";
  $("fc-space").textContent = fc.free_space != null
    ? `· FREE SPACE ${fmtNum(fc.free_space)} t${fc.capacity ? " of " + fmtNum(fc.capacity) : ""}` : "";

  const jumpEl = $("fc-jump");
  const jump = fc.jump;
  jumpEl.classList.toggle("hidden", !jump);
  if (jump) {
    const rem = jump.departure_ts ? jump.departure_ts - Date.now() / 1000 : null;
    jumpEl.innerHTML =
      `<span class="fc-jump-badge">◈ JUMP SCHEDULED</span> → <b>${esc(jump.system || "?")}</b>` +
      (jump.body ? ` <span class="dim">${esc(jump.body)}</span>` : "") +
      (rem != null ? ` · <span class="${rem <= 0 ? "dim" : "soon"}">${rem <= 0 ? "departing…" : "departs in " + fmtDuration(rem)}</span>` : "");
    if (jump.system) jumpEl.appendChild(plotButton(jump.system));
  }
}

/* ---------- galaxy: local Powerplay · BGS · conflicts · visit history ---------- */

function galaxyHistoryKey(commanderId) {
  return "galaxyHistory:v2:" + encodeURIComponent(commanderId);
}

function loadGalaxyHistory(commanderId, legacyCommanderName) {
  galaxyHistoryCommander = commanderId || null;
  galaxyHistory = [];
  if (!commanderId) return;
  try {
    const key = galaxyHistoryKey(commanderId);
    let raw = localStorage.getItem(key);
    if (raw == null && legacyCommanderName) {
      const legacyKey = "galaxyHistory:v1:" + encodeURIComponent(legacyCommanderName);
      raw = localStorage.getItem(legacyKey);
      if (raw != null) {
        localStorage.setItem(key, raw);
        localStorage.removeItem(legacyKey);
      }
    }
    const value = JSON.parse(raw || "[]");
    galaxyHistory = Array.isArray(value) ? value : [];
  } catch (e) {
    galaxyHistory = [];
  }
}

function saveGalaxyHistory() {
  if (!galaxyHistoryCommander) return;
  try {
    localStorage.setItem(galaxyHistoryKey(galaxyHistoryCommander), JSON.stringify(galaxyHistory));
  } catch (e) { /* a full/disabled browser store must never break live rendering */ }
}

function updateGalaxyHistory(gal) {
  const commanderId = profileStorageId();
  if (!commanderId) {
    return { all: [], entries: [], current: null, previous: null };
  }
  if (galaxyHistoryCommander !== commanderId) {
    loadGalaxyHistory(commanderId, state.commander || null);
  }
  const entry = GalaxyData.observation(state.system, gal, new Date().toISOString());
  const previousLength = galaxyHistory.length;
  const previousTail = galaxyHistory[previousLength - 1];
  galaxyHistory = GalaxyData.appendObservation(galaxyHistory, entry, 300);
  const nextTail = galaxyHistory[galaxyHistory.length - 1];
  if (galaxyHistory.length !== previousLength || previousTail !== nextTail) saveGalaxyHistory();
  const systemEntries = galaxyHistory.filter((item) => item.system === state.system);
  const latest = systemEntries[systemEntries.length - 1] || null;
  const current = entry && latest && latest.signature === entry.signature ? latest : null;
  return {
    all: galaxyHistory,
    entries: systemEntries,
    current,
    previous: current ? systemEntries[systemEntries.length - 2] || null : null,
  };
}

function renderGalaxy() {
  const gal = state.galaxy || {};
  const history = updateGalaxyHistory(gal);
  renderPowerplay(gal, history);
  renderFactions(gal, history);
  renderConflicts(gal);
  renderGalaxyHistory(history);
  renderCommunityGoals(gal);
  renderSquadron(gal);
}

function ppProgressPercent(value) {
  if (value == null || value === "") return null;
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return null;
  return Math.max(0, Math.min(100, numeric <= 1 ? numeric * 100 : numeric));
}

function powerplayStateNote(powerplayState) {
  const notes = {
    homesystem: "This is the Power's home system.",
    unoccupied: "No Power currently controls this system.",
    acquisition: "An uncontrolled system being worked for acquisition.",
    contested: "Multiple Powers are competing for this system.",
    exploited: "Power-controlled at the Exploited control band.",
    fortified: "Power-controlled at the Fortified control band.",
    stronghold: "Power-controlled at the Stronghold control band.",
  };
  const key = String(powerplayState || "").toLowerCase().replace(/[^a-z]/g, "");
  return notes[key] || "System state reported by the local journal snapshot.";
}

function powerplayRewardsHtml(pp) {
  const progress = GalaxyData.moduleProgress(pp.power, pp.rank, pp.merits);
  const pips = GalaxyData.MODULE_RANKS.map((rank, index) => {
    const item = progress.order[index];
    const unlocked = pp.rank != null && Number(pp.rank) >= rank;
    const title = item ? `${item.module} · rank ${rank}` : `Powerplay module · rank ${rank}`;
    return `<span class="pp-reward-pip ${unlocked ? "on" : ""}" title="${esc(title)}">${rank}</span>`;
  }).join("");
  let next;
  if (progress.complete) {
    next = `<b class="good">ALL 12 POWERPLAY MODULES AVAILABLE</b>`;
  } else {
    const moduleName = progress.nextModule ? `<b>${esc(progress.nextModule)}</b> · ` : "";
    const meritsLeft = progress.remainingMerits != null
      ? ` · ${fmtNum(progress.remainingMerits)} merits remaining` : "";
    next = `Next: ${moduleName}rank ${progress.nextRank}${meritsLeft}`;
  }
  const bar = !progress.complete && progress.fraction != null
    ? `<div class="pp-reward-bar" title="Progress toward the next module milestone"><div style="width:${(progress.fraction * 100).toFixed(1)}%"></div></div>`
    : "";
  return `<div class="pp-rewards">
    <div class="pp-reward-head"><span class="label" title="Built-in Powerplay 2.0 reward table, verified ${GalaxyData.DATA_AS_OF}; no online lookup">PP2 MODULE TRACK</span><span class="dim">${progress.unlockedCount}/12 unlocked</span></div>
    <div class="pp-reward-pips">${pips}</div>${bar}
    <div class="pp-reward-next">${next}</div>
    <div class="dim pp-reward-note">Rank and merits do not decay between cycles while you stay pledged. Leaving or defecting resets that Powerplay progression.</div>
  </div>`;
}

function renderPowerplay(gal, history) {
  const card = $("powerplay-card");
  if (!card) return;
  const pp = gal.powerplay;
  const sys = gal.pp_system;
  $("pp-empty").classList.toggle("hidden", !!(pp || sys));
  $("pp-pledge").classList.toggle("hidden", !pp);
  $("pp-sys").classList.toggle("hidden", !sys);
  $("pp-merits").textContent = pp && pp.session_merits
    ? `+${fmtNum(pp.session_merits)} merits this session` : "";
  const sig = JSON.stringify([pp, sys]);
  if (card.dataset.sig === sig) return;
  card.dataset.sig = sig;

  if (pp) {
    const weeks = pp.time_pledged_s != null ? Math.floor(pp.time_pledged_s / 604800) : null;
    $("pp-pledge").innerHTML =
      `<div class="pp-row"><b>${esc(pp.power || "?")}</b>` +
      ` <span class="chip" title="Your persistent Powerplay 2.0 rank. It rises with lifetime merits for this pledge.">RANK ${pp.rank != null ? pp.rank : "?"}</span>` +
      `<span class="dim"> · ${fmtNum(pp.merits || 0)} merits` +
      (weeks != null ? ` · pledged ${weeks >= 1 ? weeks + "w" : "under a week"}` : "") + `</span></div>` +
      powerplayRewardsHtml(pp);
  }
  if (sys) {
    const prog = ppProgressPercent(sys.control_progress);
    const reinf = sys.reinforcement;
    const under = sys.undermining;
    const contenders = GalaxyData.contestingPowers(sys);
    let stateLine = `<b>${esc(sys.controlling || "Uncontrolled")}</b>` +
      (sys.state ? ` <span class="chip">${esc(sys.state).toUpperCase()}</span>` : "");
    if (contenders.length) {
      stateLine += ` <span class="dim">· other Powers present: ${esc(contenders.join(", "))}</span>`;
    }
    const previousPowerplay = history.previous && history.previous.powerplay;
    const currentPowerplay = history.current && history.current.powerplay;
    const progressDelta = previousPowerplay && currentPowerplay &&
      previousPowerplay.controlling === currentPowerplay.controlling &&
      previousPowerplay.control_progress != null && currentPowerplay.control_progress != null
      ? ppProgressPercent(currentPowerplay.control_progress) - ppProgressPercent(previousPowerplay.control_progress)
      : null;
    const scores = reinf != null || under != null
      ? `<div class="pp-scores dim" title="Raw reinforcement and undermining scores reported by the journal; these are not a forecast.">` +
        `<span class="good">▲ ${fmtNum(reinf || 0)} reinforcement</span>` +
        ` <span>vs</span> <span class="warn">▼ ${fmtNum(under || 0)} undermining</span></div>`
      : "";
    const conflictProgress = (sys.conflict_progress || []).filter((item) => item && item.power);
    const conflictHtml = conflictProgress.length
      ? `<div class="pp-conflict-progress"><div class="label">POWER CONFLICT PROGRESS <span class="dim">journal snapshot</span></div>` +
        conflictProgress.map((item) => {
          const pct = ppProgressPercent(item.progress);
          return `<div class="pp-conflict-row"><span>${esc(item.power)}</span>` +
            `<div class="pp-mini-bar" title="Conflict progress reported by the journal; not a prediction"><div style="width:${pct == null ? 0 : pct.toFixed(1)}%"></div></div>` +
            `<b>${pct == null ? "—" : pct.toFixed(1) + "%"}</b></div>`;
        }).join("") + `</div>`
      : "";
    $("pp-sys").innerHTML =
      `<div class="label">THIS SYSTEM <span class="dim">${esc(state.system || "")}</span></div>` +
      `<div class="pp-row">${stateLine}</div><div class="dim pp-state-note">${esc(powerplayStateNote(sys.state))}</div>` +
      (prog != null
        ? `<div class="pp-bar" title="Progress within the system's currently reported Powerplay control state; not a simple fortification meter"><div style="width:${prog.toFixed(1)}%"></div></div>` +
          `<div class="dim">current-state progress ${prog.toFixed(1)}%` +
          (progressDelta != null && Math.abs(progressDelta) >= 0.05
            ? ` · <span class="${progressDelta >= 0 ? "good" : "warn"}">${progressDelta >= 0 ? "+" : ""}${progressDelta.toFixed(1)} pp since your prior observation</span>` : "") +
          `</div>`
        : "") + scores + conflictHtml;
  }
}

function renderFactions(gal, history) {
  const list = $("factions-list");
  if (!list) return;
  const factions = gal.factions || [];
  $("factions-empty").classList.toggle("hidden", factions.length > 0);
  $("factions-count").textContent = factions.length
    ? `${factions.length} factions · ${gal.controlling_faction || "?"} controls` : "";
  const deltas = GalaxyData.factionDeltas(history.current, history.previous);
  const deltaByFaction = new Map(deltas.map((item) => [item.name, item.delta]));
  const sig = JSON.stringify([factions, gal.controlling_faction, deltas]);
  if (list.dataset.sig === sig) return;
  list.dataset.sig = sig;
  list.innerHTML = "";
  for (const f of factions) {
    const inf = f.influence != null ? f.influence : 0;
    const controls = f.name === gal.controlling_faction;
    const rep = GalaxyData.reputationBand(f.my_reputation);
    const delta = deltaByFaction.get(f.name);
    const states = []
      .concat((f.active_states || []).map((s) => [s, ""]))
      .concat((f.pending_states || []).map((s) => [s, " (pending)"]))
      .concat((f.recovering_states || []).map((s) => [s, " (recovering)"]));
    const div = document.createElement("div");
    div.className = "fact-row";
    div.innerHTML =
      `<div class="fact-top"><b>${esc(f.name || "?")}</b>` +
      (controls ? ' <span class="chip" title="Currently controls this system — owns the main station and sets security">CONTROLS</span>' : "") +
      (rep ? ` <span class="chip ${rep.className}" title="Your personal reputation with this faction">${rep.label}</span>` : "") +
      (delta != null && Math.abs(delta) >= 0.005
        ? ` <span class="fact-delta ${delta >= 0 ? "good" : "warn"}" title="Influence change in percentage points since this browser's previous observation">${delta >= 0 ? "+" : ""}${delta.toFixed(1)} pp</span>` : "") +
      `<span class="fact-inf">${(inf * 100).toFixed(1)}%</span></div>` +
      `<div class="fact-bar"><div style="width:${Math.min(100, inf * 100).toFixed(1)}%"></div></div>` +
      (states.length || f.government
        ? `<div class="dim">${esc(f.government || "")}${f.allegiance ? " · " + esc(f.allegiance) : ""}` +
          (states.length ? " · " + states.map(([s, tag]) => esc(s) + tag).join(", ") : "") + `</div>`
        : "");
    list.appendChild(div);
  }
}

function renderConflicts(gal) {
  const card = $("conflicts-card");
  if (!card) return;
  const conflicts = gal.conflicts || [];
  card.classList.toggle("hidden", !conflicts.length);
  if (!conflicts.length) return;
  $("conflicts-count").textContent = conflicts.length === 1 ? "1 conflict" : conflicts.length + " conflicts";
  const list = $("conflicts-list");
  const sig = JSON.stringify(conflicts);
  if (list.dataset.sig === sig) return;
  list.dataset.sig = sig;
  list.innerHTML = "";
  for (const c of conflicts) {
    const f1 = c.faction1 || {}, f2 = c.faction2 || {};
    const isElection = String(c.war_type || "").toLowerCase().includes("election");
    const div = document.createElement("div");
    div.className = "conflict-row";
    const side = (f, other) =>
      `<b>${esc(f.name || "?")}</b> <span class="${(f.won_days || 0) > (other.won_days || 0) ? "good" : "dim"}">${f.won_days || 0}</span>` +
      (f.stake ? `<span class="dim conflict-stake" title="What this side loses if it's defeated">stakes ${esc(f.stake)}</span>` : "");
    div.innerHTML =
      `<div class="conflict-head"><span class="chip">${esc((c.war_type || "war").toUpperCase())}</span>` +
      ` <span class="dim">${c.status === "active" ? "days won — first to 4 of 7 wins" : esc(c.status || "")}</span></div>` +
      `<div class="conflict-sides">${side(f1, f2)}<span class="dim conflict-vs">vs</span>${side(f2, f1)}</div>` +
      (isElection
        ? `<div class="dim conflict-guidance"><b>Election:</b> support a side with missions, trade, exploration data and other non-combat BGS actions. Elections have no conflict zones or combat bonds.</div>`
        : `<div class="dim conflict-guidance"><b>${esc(c.war_type || "War")}:</b> support a side in conflict zones, with combat bonds and with appropriate missions.</div>`);
    list.appendChild(div);
  }
}

function renderGalaxyHistory(history) {
  const summary = $("galhistory-summary");
  const list = $("galhistory-list");
  if (!summary || !list) return;
  const current = history.current;
  const previous = history.previous;
  $("galhistory-count").textContent = history.entries.length
    ? `${history.entries.length} observation${history.entries.length === 1 ? "" : "s"} here` : "";
  $("galhistory-empty").classList.toggle("hidden", !!previous);
  const sig = JSON.stringify(history.entries);
  if (list.dataset.sig === sig) return;
  list.dataset.sig = sig;
  summary.innerHTML = "";
  list.innerHTML = "";

  if (current && previous) {
    const elapsed = (Date.parse(current.observed_at) - Date.parse(previous.observed_at)) / 1000;
    const deltas = GalaxyData.factionDeltas(current, previous).filter((item) => Math.abs(item.delta) >= 0.005);
    const changes = deltas.slice(0, 5).map((item) =>
      `<span class="history-delta ${item.delta >= 0 ? "good" : "warn"}"><b>${esc(item.name)}</b> ${item.delta >= 0 ? "+" : ""}${item.delta.toFixed(1)} pp</span>`);
    if (current.controlling_faction !== previous.controlling_faction) {
      changes.unshift(`<span class="history-delta warn">Control changed: <b>${esc(previous.controlling_faction || "none")}</b> → <b>${esc(current.controlling_faction || "none")}</b></span>`);
    }
    if (current.powerplay && previous.powerplay && current.powerplay.state !== previous.powerplay.state) {
      changes.unshift(`<span class="history-delta">Powerplay state: <b>${esc(previous.powerplay.state || "none")}</b> → <b>${esc(current.powerplay.state || "none")}</b></span>`);
    }
    summary.innerHTML = `<div class="history-summary-head">CHANGE SINCE PREVIOUS OBSERVATION <span class="dim">${elapsed >= 0 ? fmtDuration(elapsed) + " ago" : "earlier"}</span></div>` +
      (changes.length ? `<div class="history-deltas">${changes.join("")}</div>`
        : `<div class="dim">No material faction influence, control or Powerplay-state change observed.</div>`);
  }

  for (const entry of history.entries.slice(-5).reverse()) {
    const when = new Date(entry.observed_at);
    const timestamp = Number.isNaN(when.getTime()) ? "time unknown" : when.toLocaleString();
    const detail = [entry.controlling_faction ? entry.controlling_faction + " controls" : null,
      entry.powerplay && entry.powerplay.state ? "PP " + entry.powerplay.state : null].filter(Boolean).join(" · ");
    const row = document.createElement("div");
    row.className = "galhistory-row";
    row.innerHTML = `<b>${esc(entry.system)}</b><span class="dim">${esc(timestamp)}</span>` +
      (detail ? `<span>${esc(detail)}</span>` : "");
    list.appendChild(row);
  }
}

function clearGalaxyHistory() {
  if (!window.confirm("Clear the Galaxy observations saved in this browser and make the current system a new baseline?")) return;
  if (galaxyHistoryCommander == null) {
    loadGalaxyHistory(profileStorageId(), state?.commander || null);
  }
  galaxyHistory = [];
  saveGalaxyHistory();
  $("galaxy-history-card").dataset.sig = "";
  $("galhistory-list").dataset.sig = "";
  $("powerplay-card").dataset.sig = "";
  if (state) renderGalaxy();
}

function renderCommunityGoals(gal) {
  const list = $("cg-list");
  if (!list) return;
  const now = Date.now() / 1000;
  const goals = (gal.community_goals || []).map((g) => {
    const exp = g.expiry ? Date.parse(g.expiry) / 1000 : null;
    return { ...g, remaining_s: exp != null ? exp - now : null };
  });
  const live = goals.filter((g) => !g.complete && (g.remaining_s == null || g.remaining_s > -86400));
  $("cg-empty").classList.toggle("hidden", live.length > 0);
  $("cg-count").textContent = live.length ? (live.length === 1 ? "1 active" : live.length + " active") : "";
  const sig = JSON.stringify(goals.map((g) => [g.cgid, g.contribution, g.tier, Math.floor((g.remaining_s || 0) / 60)]));
  if (list.dataset.sig === sig) return;
  list.dataset.sig = sig;
  list.innerHTML = "";
  for (const g of live) {
    const div = document.createElement("div");
    div.className = "cg-row";
    const pct = g.percentile != null
      ? `<span class="chip" title="Your contribution rank among every participating commander — lower band = bigger reward">TOP ${g.percentile}%</span>` : "";
    div.innerHTML =
      `<div class="fact-top"><b>${esc(g.title || "Community goal")}</b>${pct}` +
      (g.remaining_s != null ? `<span class="dim cg-expiry">${g.remaining_s <= 0 ? "ended" : "ends in " + fmtDuration(g.remaining_s)}</span>` : "") + `</div>` +
      `<div class="dim">${esc(g.market || "?")} · ${esc(g.system || "?")}` +
      (g.contribution != null ? ` · your contribution ${fmtNum(g.contribution)}` : "") +
      (g.tier ? ` · tier ${esc(String(g.tier))} reached` : "") +
      (g.contributors ? ` · ${fmtNum(g.contributors)} commanders` : "") + `</div>`;
    if (g.system) div.appendChild(plotButton(g.system));
    list.appendChild(div);
  }
}

function renderSquadron(gal) {
  const card = $("squadron-card");
  if (!card) return;
  const sq = gal.squadron;
  card.classList.toggle("hidden", !sq);
  if (!sq) return;
  const info = $("squadron-info");
  const sig = JSON.stringify(sq);
  if (info.dataset.sig === sig) return;
  info.dataset.sig = sig;
  info.innerHTML = `<div class="pp-row"><b>${esc(sq.name || "?")}</b>` +
    (sq.rank != null ? ` <span class="chip">RANK ${sq.rank}</span>` : "") +
    `<span class="dim"> · squadron chat, goals and leaderboards live in the game's right-hand panel</span></div>`;
}

let lastFuelSig = null;   // advisory code+system last spoken, to avoid repeats
let lastAlertId = 0;      // highest one-shot alert id already handled
let alertsInit = false;   // baseline set on first state so old alerts don't replay

function renderBanner() {
  const banner = $("banner");
  const advisory = state.nav && state.nav.advisory;
  const rebuildVisible = !!(
    state.journal_rebuild?.active || state.journal_rebuild?.phase === "error"
  );
  if (!rebuildVisible) delete banner.dataset.rebuildSignature;

  // Speak the fuel advisory once whenever the situation (code + system) changes.
  const sig = advisory ? advisory.code + "|" + (state.nav.system || "") : null;
  if (sig !== lastFuelSig) {
    if (advisory) speak(advisory.say);
    lastFuelSig = sig;
  }

  banner.classList.remove("banner-critical", "banner-warn", "banner-rebuild");
  if (state.journal_dir_found === false) {
    // The folder notice replaces the rebuild subtree. Force reconstruction to
    // recreate its DOM if the folder reappears with the same progress values.
    delete banner.dataset.rebuildSignature;
    if (!banner.querySelector(".banner-settings-btn")) {
      banner.textContent = "Elite Dangerous journal folder not found — if the game is installed, point Frameshift at it: ";
      const btn = document.createElement("button");
      btn.className = "copy banner-settings-btn";
      btn.textContent = "OPEN SETTINGS";
      btn.addEventListener("click", () => {
        if (document.body.classList.contains("panel-mode")) setPanelPage("database");
        else activateTab("database");
        const inp = $("journal-dir-input");
        if (inp) inp.focus();
      });
      banner.appendChild(btn);
    }
    banner.classList.remove("hidden");
  } else if (rebuildVisible) {
    renderJournalRebuild(banner, state.journal_rebuild);
  } else if (!state.system) {
    banner.textContent = "Waiting for journal data - start Elite Dangerous (or play a bit) and this will fill in.";
    banner.classList.remove("hidden");
  } else if (advisory) {
    banner.textContent = "⚠ " + advisory.text;
    banner.classList.add(advisory.level === "critical" ? "banner-critical" : "banner-warn");
    banner.classList.remove("hidden");
  } else {
    banner.classList.add("hidden");
  }
}

/* Startup initiation sequence: the reconstruction banner styled as a cockpit
   power-on checklist. Fixed child order (tests index into it):
   [0] .bs-head (dot + title/sub) · [1] .bs-steps (3 stages) ·
   [2] .bs-meter (bar[progressbar] + mono readout; absent on fault). */
const REBUILD_STAGES = [
  { label: "FLIGHT RECORDER", done: "SYNCED" },
  { label: "COCKPIT RESTORE", done: "RESTORED" },
  { label: "SYSTEMS CHECK", done: "PASS" },
];

function renderJournalRebuild(banner, rebuild) {
  const phase = String(rebuild.phase || "preparing");
  const completed = Math.max(0, Number(rebuild.completed) || 0);
  const total = Math.max(0, Number(rebuild.total) || 0);
  const shownCompleted = total ? Math.min(completed, total) : completed;
  const retrying = !!rebuild.retrying;
  const attempt = Number(rebuild.attempt) || 0;
  const current = String(rebuild.current || "");
  const fault = phase === "error";

  const signature = JSON.stringify([phase, shownCompleted, total, attempt, retrying, current]);
  if (banner.dataset.rebuildSignature === signature) {
    banner.classList.add("banner-rebuild");
    banner.classList.remove("hidden");
    return;
  }
  banner.dataset.rebuildSignature = signature;
  banner.replaceChildren();
  banner.classList.add("banner-rebuild");
  banner.classList.toggle("bs-fault", fault);
  banner.classList.toggle("bs-hold", retrying && !fault);

  // Which stage of the fixed pre-flight order is live right now.
  const stageIndex = phase === "bootstrap" ? 1 : phase === "finalizing" ? 2 : 0;
  const count = total
    ? `${String(shownCompleted).padStart(2, "0")}/${String(total).padStart(2, "0")}`
    : "--/--";

  const head = document.createElement("div");
  head.className = "bs-head";
  const dot = document.createElement("span");
  dot.className = "bs-dot";
  const lines = document.createElement("div");
  lines.className = "bs-lines";
  const title = document.createElement("div");
  title.className = "bs-title";
  title.textContent = fault ? "STARTUP FAULT"
    : retrying ? "STARTUP SEQUENCE — HOLDING" : "STARTUP SEQUENCE";
  const sub = document.createElement("div");
  sub.className = "bs-sub";
  sub.textContent = fault
    ? "Commander history could not be reconstructed safely — restart Frameshift once. "
      + "If this returns, create a support bundle from Settings → Diagnostics; "
      + "your journals and databases have not been deleted."
    : retrying
      ? "A local journal or database file was temporarily unavailable — retrying automatically"
        + (attempt ? ` (attempt ${attempt})` : "") + "."
      : "Journals are the source of truth: the live cockpit is rebuilt from your "
        + "latest flight logs at every launch.";
  lines.append(title, sub);
  head.append(dot, lines);

  const steps = document.createElement("div");
  steps.className = "bs-steps";
  REBUILD_STAGES.forEach((stage, index) => {
    const step = document.createElement("div");
    step.className = "bs-step";
    const glyph = document.createElement("span");
    glyph.className = "bs-glyph";
    const label = document.createElement("span");
    label.textContent = stage.label;
    const state = document.createElement("span");
    state.className = "bs-step-state";
    if (index < stageIndex) {
      step.classList.add("done");
      glyph.textContent = "✓";
      state.textContent = stage.done;
    } else if (index === stageIndex) {
      step.classList.add(fault ? "fault" : "active");
      glyph.textContent = fault ? "✕" : "▸";
      state.textContent = fault ? "FAULT"
        : retrying ? `RETRY${attempt ? " " + attempt : ""}`
        : index === 2 || !total ? "RUNNING" : count;
    } else {
      glyph.textContent = "○";
      state.textContent = "HOLD";
    }
    step.append(glyph, label, state);
    steps.appendChild(step);
  });

  banner.append(head, steps);

  if (!fault) {
    const meter = document.createElement("div");
    meter.className = "bs-meter";
    const bar = document.createElement("div");
    bar.className = "bs-bar";
    bar.setAttribute("role", "progressbar");
    bar.setAttribute("aria-valuemin", "0");
    const fill = document.createElement("div");
    fill.className = "bs-fill";
    if (total) {
      fill.style.width = `${Math.round(100 * shownCompleted / total)}%`;
      bar.setAttribute("aria-valuemax", String(total));
      bar.setAttribute("aria-valuenow", String(shownCompleted));
      bar.setAttribute("aria-valuetext", `${shownCompleted} of ${total} journals complete`);
      bar.setAttribute("aria-label", `${shownCompleted} of ${total} journals complete`);
    } else {
      bar.classList.add("indeterminate");
      bar.setAttribute("aria-label", "Journal reconstruction in progress");
    }
    bar.appendChild(fill);
    const readout = document.createElement("div");
    readout.className = "bs-readout";
    // Journal filenames carry a timestamp: show it like a log tape readout.
    const tape = current.replace(/^Journal\./, "").replace(/\.\d+\.log$/i, "");
    readout.textContent = phase === "finalizing" ? "CROSS-CHECK · PRESERVED DATA"
      : (total ? `LOG ${count}` : "LOG --/--") + (tape ? ` · ${tape}` : "");
    meter.append(bar, readout);
    banner.appendChild(meter);
  }
  banner.classList.remove("hidden");
}

// One-shot voice alerts (interdiction, hull damage, first discovery). Each is
// spoken once; on first load we skip the backlog so nothing stale replays.
function handleAlerts() {
  const alerts = (state && state.alerts) || [];
  const maxId = alerts.length ? Math.max(...alerts.map((a) => a.id)) : 0;
  // On the first state we see, adopt its high-water mark without speaking, so
  // alerts that fired before the page opened don't all replay at once.
  if (!alertsInit) { alertsInit = true; lastAlertId = maxId; return; }
  if (!alerts.length) return;
  const fresh = alerts.filter((a) => a.id > lastAlertId).sort((a, b) => a.id - b.id);
  if (fresh.length) lastAlertId = Math.max(lastAlertId, maxId);
  for (const a of fresh) {
    speak(a.say);
    showFlightToast(a);
  }
}

let flightToastTimer = null;
function showFlightToast(alert) {
  const toast = $("flight-toast");
  if (!toast) return;
  toast.textContent = alert.text;
  toast.className = "flight-toast " + (alert.level === "critical" ? "toast-critical"
    : alert.level === "warn" ? "toast-warn" : "toast-info");
  toast.classList.remove("hidden");
  clearTimeout(flightToastTimer);
  flightToastTimer = setTimeout(() => toast.classList.add("hidden"), 7000);
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

/* Price history for the shown market (docked-at stations are tracked server
   side; anyone's EDDN report adds points). Fetched once per market id. */
let marketHist = { id: null, series: {} };

async function loadMarketHistory(mid) {
  if (!mid || marketHist.id === mid) return;
  marketHist = { id: mid, series: {} };
  try {
    const resp = await fetch("/api/price-history?market_id=" + mid);
    const data = await resp.json();
    if (marketHist.id === mid) {
      marketHist.series = data.history || {};
      renderMarket();
    }
  } catch (e) { /* sparklines are a nicety */ }
}

function sparkline(points) {
  const sells = (points || []).map((p) => p[1]).filter((v) => v > 0);
  if (sells.length < 2) return "";
  const w = 64, h = 16;
  const min = Math.min(...sells), max = Math.max(...sells);
  const span = max - min || 1;
  const step = w / (sells.length - 1);
  const pts = sells.map((v, i) =>
    `${(i * step).toFixed(1)},${(h - 2 - ((v - min) / span) * (h - 4)).toFixed(1)}`).join(" ");
  const up = sells[sells.length - 1] >= sells[0];
  return `<svg class="spark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" ` +
    `role="img" aria-label="sell price trend"><polyline points="${pts}" fill="none" ` +
    `stroke="${up ? "var(--good)" : "var(--bad)"}" stroke-width="1.5"/></svg>`;
}

/* Full-width price-history chart, expanded under a market row by tapping its
   sparkline. Sell line solid, buy line dim; min/max and time-span labels. */
let histExpanded = null;  // commodity symbol currently expanded

function histChart(points) {
  const w = 720, h = 150, padL = 8, padR = 8, padT = 16, padB = 22;
  const pts = (points || []).filter((p) => p[1] > 0 || p[2] > 0);
  if (pts.length < 2) return "";
  const t0 = pts[0][0], t1 = pts[pts.length - 1][0];
  const tSpan = (t1 - t0) || 1;
  const vals = pts.flatMap((p) => [p[1], p[2]]).filter((v) => v > 0);
  const min = Math.min(...vals), max = Math.max(...vals);
  const vSpan = (max - min) || 1;
  const x = (ts) => padL + ((ts - t0) / tSpan) * (w - padL - padR);
  const y = (v) => padT + (1 - (v - min) / vSpan) * (h - padT - padB);
  const line = (idx, color, width) => {
    const p = pts.filter((pt) => pt[idx] > 0);
    if (p.length < 2) return "";
    return `<polyline points="${p.map((pt) => `${x(pt[0]).toFixed(1)},${y(pt[idx]).toFixed(1)}`).join(" ")}"` +
      ` fill="none" stroke="${color}" stroke-width="${width}"/>`;
  };
  const fmtDate = (ts) => new Date(ts * 1000).toLocaleDateString();
  const last = pts[pts.length - 1];
  return `<svg class="histchart" viewBox="0 0 ${w} ${h}" role="img" aria-label="price history">` +
    line(2, "var(--dim)", 1) +          // buy price, dim
    line(1, "var(--good)", 1.8) +       // sell price
    `<text x="${padL}" y="11" class="hc-label">${max.toLocaleString()} cr</text>` +
    `<text x="${padL}" y="${h - padB + 12}" class="hc-label">${min.toLocaleString()} cr</text>` +
    `<text x="${padL}" y="${h - 4}" class="hc-label dim">${fmtDate(t0)}</text>` +
    `<text x="${w - padR}" y="${h - 4}" class="hc-label dim" text-anchor="end">${fmtDate(t1)}</text>` +
    (last[1] > 0 ? `<text x="${w - padR}" y="11" class="hc-label good" text-anchor="end">` +
      `sell ${last[1].toLocaleString()} cr</text>` : "") +
    `</svg>`;
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
  loadMarketHistory(market.market_id);
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
  const histReady = marketHist.id === market.market_id;
  for (const i of items) {
    const series = histReady ? marketHist.series[i.symbol] : null;
    const spark = series && sparkline(series);
    const tr = document.createElement("tr");
    tr.innerHTML =
      `<td>${esc(i.name)}<div class="sub">${esc(i.category)}</div></td>` +
      `<td class="num">${i.sell ? i.sell.toLocaleString() : "—"}${trendArrow(i.sell, i.prev_sell)}</td>` +
      `<td class="num">${i.buy ? i.buy.toLocaleString() : "—"}${trendArrow(i.buy, i.prev_buy)}</td>` +
      `<td class="num">${i.demand ? i.demand.toLocaleString() : "—"}</td>` +
      `<td class="num">${i.stock ? i.stock.toLocaleString() : "—"}</td>` +
      (spark
        ? `<td class="num sparkcell spark-click" data-sym="${esc(i.symbol)}" ` +
          `title="Tap for the full price-history chart">${spark}</td>`
        : `<td class="num sparkcell"><span class="dim" title="History builds as this station gets visits and EDDN reports">·</span></td>`);
    tbody.appendChild(tr);
    if (histExpanded === i.symbol && series) {
      const hr = document.createElement("tr");
      hr.className = "hist-row";
      hr.innerHTML = `<td colspan="6">${histChart(series)}</td>`;
      tbody.appendChild(hr);
    }
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

// The Vista Genomics "first logged" bonus: being the FIRST commander ever to
// scan a species on a body pays 5x. The journal only confirms it when you
// sell, so this is a strong estimate: the body was undiscovered when you
// scanned it, and no other commander has reported that genus there via EDDN.
const BIO_FIRST_TIP = "You're almost certainly the first commander to log this species here " +
  "(the body was undiscovered and nobody has reported it) — Vista Genomics pays 5× for a first log. " +
  "Confirmed only when you sell.";
const BIO_FIRST_BODY_TIP = "Undiscovered body — you were first here, so any species you log " +
  "is almost certainly a first log (5× at Vista Genomics).";

function renderBio() {
  const bio = state.bio || {};

  // Exploration data card
  const ex = state.exploration || { total: 0, count: 0, top: [] };
  $("explo-total").textContent = ex.count ? "≈" + fmtNum(ex.total) + " cr" : "";
  $("explo-summary").textContent = ex.count
    ? `${ex.count} ${ex.count === 1 ? "body" : "bodies"} scanned · ${ex.mapped} mapped` +
      ` · ${ex.firsts} first ${ex.firsts === 1 ? "discovery" : "discoveries"}`
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
    const sampPay = samp.value != null ? samp.value * (samp.first ? 5 : 1) : null;
    // Live clonal-colony distance: how far you've moved from the nearest
    // previous sample vs. the genus's required spacing. Green = clear.
    let distHtml = "";
    if (samp.min_dist_m != null) {
      const need = samp.colony_m;
      const clear = samp.clear === true;
      const pctd = need ? Math.min(100, Math.round(100 * samp.min_dist_m / need)) : 0;
      distHtml =
        `<div class="samp-dist${clear ? " samp-ok" : ""}">` +
        `<span class="samp-dist-num">${fmtNum(samp.min_dist_m)} m</span>` +
        (need ? `<span class="dim">of ${fmtNum(need)} m needed</span>` : "") +
        (clear ? `<span class="samp-badge">✓ CLEAR TO SAMPLE</span>`
          : samp.clear === false ? `<span class="samp-badge samp-wait">KEEP MOVING</span>` : "") +
        `</div>` +
        (need ? `<div class="seedbar"><div style="height:100%;width:${pctd}%;background:${clear ? "var(--good)" : "var(--orange)"}"></div></div>` : "");
    }
    $("bio-sampling").innerHTML =
      `<div class="route-line"><b>${esc(samp.species)}</b>` +
      (samp.variant ? `<span class="dim">${esc(samp.variant)}</span>` : "") +
      (samp.first ? `<span class="bio-first" title="${BIO_FIRST_TIP}">★ FIRST LOG ×5</span>` : "") +
      `<span class="profit">${sampPay != null ? "+" + fmtNum(sampPay) + " cr" : ""}</span></div>` +
      `<div class="commodities">sample ${samp.progress}/3` +
      (samp.colony_m ? ` · move ≥ ${samp.colony_m} m between samples` : "") + `</div>` +
      `<div class="seedbar"><div style="height:100%;width:${pct}%;background:var(--good)"></div></div>` +
      distHtml;
  } else {
    sampCard.classList.add("hidden");
  }

  // Vault
  const vault = bio.vault || { items: [], total: 0 };
  $("bio-vault-total").textContent = vault.items.length ? "≈" + fmtNum(vault.total) + " cr" : "";
  $("bio-vault-empty").classList.toggle("hidden", vault.items.length > 0);
  const ul = $("bio-vault");
  const vsig = JSON.stringify(vault.items);
  if (ul.dataset.sig !== vsig) {
    ul.dataset.sig = vsig;
    ul.innerHTML = "";
    for (const s of vault.items) {
      const pay = (s.value || 0) * (s.first ? 5 : 1);
      const li = document.createElement("li");
      li.innerHTML = `<span>${esc(s.species)}` +
        (s.first ? ` <span class="bio-first" title="${BIO_FIRST_TIP}">★ FIRST LOG ×5</span>` : "") +
        `${s.body ? ` <span class="sub">${esc(s.body)}</span>` : ""}</span>` +
        `<span class="count">+${fmtNum(pay)} cr</span>`;
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
      `<td>${esc(b.body)}` +
      `${b.was_discovered === false ? ` <span class="bio-first" title="${BIO_FIRST_BODY_TIP}">★</span>` : ""}` +
      `${b.landable === false ? ' <span class="sub">not landable</span>' : ""}` +
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

function confidenceAgeText(seconds) {
  if (seconds == null || !Number.isFinite(Number(seconds))) return "age unknown";
  const value = Math.max(0, Number(seconds));
  if (value < 3600) return `${Math.max(1, Math.round(value / 60))}m old`;
  if (value < 172800) return `${Math.round(value / 3600)}h old`;
  return `${Math.round(value / 86400)}d old`;
}

function confidenceHtml(confidence) {
  if (!confidence) return "";
  const allowed = new Set(["high", "medium", "low"]);
  const band = allowed.has(String(confidence.band).toLowerCase())
    ? String(confidence.band).toLowerCase() : "low";
  const score = Number.isFinite(Number(confidence.score)) ? Math.round(Number(confidence.score)) : "?";
  const reasons = Array.isArray(confidence.reasons) && confidence.reasons.length
    ? confidence.reasons.join("; ") : "no material freshness or depth warning";
  const detail = `${confidence.source || "market observation"}; ${confidenceAgeText(confidence.age_s)}; ${reasons}`;
  return `<span class="confidence confidence-${band}" title="${esc(detail)}">${band.toUpperCase()} ${score}</span>`;
}

function creditRangeHtml(range, suffix = "cr") {
  if (!range || range.low == null || range.observed == null) return "";
  return `<span class="risk-range">conservative ${fmtNum(range.low)}–${fmtNum(range.observed)} ${esc(suffix)}</span>`;
}

/* ---------- trade routes ---------- */

async function findRoutes(ev) {
  ev.preventDefault();
  const go = $("rf-go");
  const status = $("route-status");
  const results = $("route-results");
  go.disabled = true;
  status.classList.remove("error");
  const searchMode = $("rf-mode").value;
  status.textContent = searchMode === "loop"
    ? "Searching your local market database… (~3-10s)"
    : "Planning the chain… (local database, or Spansh when it isn't built · ~10-30s)";
  results.innerHTML = "";
  try {
    const mode = searchMode;
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
    const resp = await commanderFetch("/api/watch", {
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

function clearAlertWorkspace() {
  if (alertPollTimer) clearTimeout(alertPollTimer);
  alertPollTimer = null;
  lastAlertTs = null;
  lastAlertId = 0;
  alertsInit = false;
  lastFuelSig = null;
  if ($("watch-list")) $("watch-list").replaceChildren();
  if ($("alert-strip")) {
    $("alert-strip").replaceChildren();
    $("alert-strip").classList.add("hidden");
  }
  if ($("flight-toast")) {
    clearTimeout(flightToastTimer);
    flightToastTimer = null;
    $("flight-toast").classList.add("hidden");
    $("flight-toast").textContent = "";
  }
}

async function pollAlerts() {
  const generation = profileGeneration;
  const expectedCommander = profileStorageId(state);
  if (alertPollTimer) clearTimeout(alertPollTimer);
  alertPollTimer = null;
  if (!expectedCommander) {
    clearAlertWorkspace();
    return;
  }
  try {
    const resp = await fetch("/api/alerts", { cache: "no-store" });
    if (resp.ok) {
      const data = await resp.json();
      if (generation !== profileGeneration
          || expectedCommander !== profileStorageId(state)
          || String(data.commander_id || "") !== expectedCommander) return;
      renderWatches(data.watches || []);
      renderAlerts(data.alerts || []);
    }
  } catch (e) { /* retry next tick */ }
  if (generation === profileGeneration && expectedCommander === profileStorageId(state)) {
    alertPollTimer = setTimeout(pollAlerts, 15000);
  }
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
      await commanderFetch("/api/watch/remove", {
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
      try { new Notification("Frameshift route alert", { body: newest.text }); } catch (e) {}
    }
    lastAlertTs = newest.ts;
  }
  strip.classList.remove("hidden");
  strip.innerHTML = "";
  for (const a of alerts.slice(0, 3)) {
    const row = document.createElement("div");
    row.className = "alert-row";
    const message = document.createElement("span");
    message.textContent = `⚠ ${a.text}`;
    row.appendChild(message);
    if (a.market_id != null) {
      const recover = document.createElement("button");
      recover.className = "plotbtn alert-recover";
      recover.textContent = "RECOVER CARGO";
      recover.title = "Use the cargo currently aboard to find a different buyer, excluding the degraded market";
      recover.addEventListener("click", () => recoverCargo(a.market_id, recover));
      row.appendChild(recover);
    }
    strip.appendChild(row);
  }
  const dismiss = document.createElement("button");
  dismiss.className = "copy";
  dismiss.textContent = "dismiss";
  dismiss.addEventListener("click", async () => {
    await commanderFetch("/api/alerts/clear", { method: "POST" });
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
      confidenceHtml(l.confidence) +
      `<span class="profit">${l.profit_per_hour != null ? "+" + fmtNum(l.profit_per_hour) + " cr/hr" : "+" + fmtNum(l.profit) + " cr / trip"}</span>` +
      `</div>` +
      `<div class="commodities">` +
      `observed +${fmtNum(l.profit)} cr / round trip` +
      (l.profit_range ? ` · ${creditRangeHtml(l.profit_range)}` : "") +
      (l.minutes_per_trip != null ? ` · ≈${l.minutes_per_trip} min/trip` : "") +
      ` · ${l.distance} ly apart · start ${l.a.from_player} ly from you` +
      (l.positioning_minutes != null ? ` · ${fmtNum(l.positioning_minutes)} min positioning` : "") +
      (l.first_trip_profit_per_hour != null ? ` · first run ${fmtNum(l.first_trip_profit_per_hour)} cr/hr incl. positioning` : "") +
      ` · ${l.a.dist_ls != null ? fmtNum(l.a.dist_ls) : "?"} / ${l.b.dist_ls != null ? fmtNum(l.b.dist_ls) : "?"} ls to pads` +
      (tons ? ` · ${fmtNum(l.profit / tons)} cr/t moved` : "") +
      `</div>` +
      `<div class="leg-label">OUTBOUND ${confidenceHtml(l.outbound.confidence)} <span class="profit-cell">+${fmtNum(l.outbound.profit)}</span></div>` +
      commodityTableHtml(l.outbound.commodities) +
      `<div class="leg-label">RETURN ${confidenceHtml(l.inbound.confidence)} <span class="profit-cell">${l.inbound.commodities.length ? "+" + fmtNum(l.inbound.profit) : "fly back empty"}</span></div>` +
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
  let totalProfit = 0, totalLow = 0, totalDist = 0, totalTons = 0, firstOutlay = 0;
  hops.forEach((h, i) => {
    totalProfit += h.profit || 0;
    totalLow += h.profit_range?.low ?? h.profit ?? 0;
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
    `<span class="risk-range">conservative ${fmtNum(totalLow)}–${fmtNum(totalProfit)} cr</span>` +
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
      confidenceHtml(h.confidence) +
      `<span class="profit">+${fmtNum(h.profit)} cr</span>` +
      `</div>` +
      commodityTableHtml(h.commodities) +
      `<div class="commodities">` +
      (h.distance != null ? `${Number(h.distance).toFixed(1)} ly jump` : "") +
      (h.to_dist_ls != null ? ` · ${fmtNum(h.to_dist_ls)} ls to station` : "") +
      (tons ? ` · ${fmtNum(h.profit / tons)} cr/t` : "") +
      (outlay ? ` · costs ${fmtNum(outlay)} cr to load` : "") +
      (h.profit_range ? ` · ${creditRangeHtml(h.profit_range)}` : "") +
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

/* ---------- contextual autocomplete ----------
   Text inputs that name a system, station, ship or module get a datalist fed
   by the local database (indexed prefix search per keystroke) or a static
   catalog. Native datalists keep it accessible and touch-friendly. */

const SUGGEST_SHIPS = [
  "Adder", "Alliance Challenger", "Alliance Chieftain", "Alliance Crusader",
  "Anaconda", "Asp Explorer", "Asp Scout", "Beluga Liner", "Cobra Mk III",
  "Cobra Mk IV", "Cobra Mk V", "Corsair", "Diamondback Explorer",
  "Diamondback Scout", "Dolphin", "Eagle", "Federal Assault Ship",
  "Federal Corvette", "Federal Dropship", "Federal Gunship", "Fer-de-Lance",
  "Hauler", "Imperial Clipper", "Imperial Courier", "Imperial Cutter",
  "Imperial Eagle", "Keelback", "Krait Mk II", "Krait Phantom", "Mamba",
  "Mandalay", "Orca", "Panther Clipper Mk II", "Python", "Python Mk II",
  "Sidewinder", "Type-6 Transporter", "Type-7 Transporter",
  "Type-8 Transporter", "Type-9 Heavy", "Type-10 Defender", "Viper Mk III",
  "Viper Mk IV", "Vulture",
];
const SUGGEST_MODULES = [
  "Fuel Scoop", "Frame Shift Drive", "FSD Interdictor", "Guardian FSD Booster",
  "Detailed Surface Scanner", "Refinery", "Prospector Limpet Controller",
  "Collector Limpet Controller", "Fuel Transfer Limpet Controller",
  "Repair Limpet Controller", "Auto Field-Maintenance Unit",
  "Shield Generator", "Bi-Weave Shield Generator", "Prismatic Shield Generator",
  "Shield Cell Bank", "Hull Reinforcement Package",
  "Module Reinforcement Package", "Power Plant", "Power Distributor",
  "Thrusters", "Life Support", "Sensors", "Cargo Rack", "Passenger Cabin",
  "Planetary Vehicle Hangar", "Supercruise Assist", "Advanced Docking Computer",
  "Beam Laser", "Pulse Laser", "Burst Laser", "Multi-Cannon", "Cannon",
  "Fragment Cannon", "Missile Rack", "Seeker Missile Rack", "Torpedo Pylon",
  "Mine Launcher", "Plasma Accelerator", "Rail Gun", "Mining Laser",
  "Abrasion Blaster", "Sub-surface Displacement Missile",
  "Seismic Charge Launcher", "Chaff Launcher", "Heat Sink Launcher",
  "Point Defence", "Kill Warrant Scanner", "Frame Shift Wake Scanner",
  "Xeno Scanner", "AX Multi-Cannon", "AX Missile Rack",
];

function attachSuggest(id, kind) {
  const input = $(id);
  if (!input) return;
  const list = document.createElement("datalist");
  list.id = "suggest-" + id;
  document.body.appendChild(list);
  input.setAttribute("list", list.id);
  input.removeAttribute("autocomplete"); // datalist needs the browser popup

  let timer = null;
  let seq = 0;
  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const q = input.value.trim();
      if (q.length < 2) { list.replaceChildren(); return; }
      const mySeq = ++seq;
      let names = [];
      try {
        const resp = await fetch(`/api/suggest?kind=${kind}&q=${encodeURIComponent(q)}`);
        if (resp.ok) names = (await resp.json()).suggestions || [];
      } catch (e) { /* suggestions are best-effort */ }
      if (mySeq !== seq) return; // a newer keystroke's response wins
      if (kind === "systems") {
        // Recently visited systems that match float to the top.
        const lower = q.toLowerCase();
        const recents = (typeof galaxyHistory !== "undefined" ? galaxyHistory : [])
          .map((h) => h.system)
          .filter((s) => s && s.toLowerCase().startsWith(lower));
        names = [...new Set([...recents.slice(0, 3), ...names])].slice(0, 12);
      }
      list.replaceChildren();
      for (const name of names) {
        const option = document.createElement("option");
        option.value = name;
        list.appendChild(option);
      }
    }, 150);
  });
}

function initSuggest() {
  for (const id of ["fp-plot-input", "plot-input", "nr-to", "ss-system",
                    "cs-near", "mn-near", "os-near",
                    "ops-objective-system", "ops-board-objective-system"]) {
    attachSuggest(id, "systems");
  }
  for (const id of ["ops-objective-station", "ops-board-objective-station"]) {
    attachSuggest(id, "stations");
  }
  // Outfitting & shipyard search: ships + common module types, locally.
  const os = $("os-query");
  if (os) {
    const list = document.createElement("datalist");
    list.id = "suggest-os-query";
    for (const name of [...SUGGEST_MODULES, ...SUGGEST_SHIPS]) {
      const option = document.createElement("option");
      option.value = name;
      list.appendChild(option);
    }
    document.body.appendChild(list);
    os.setAttribute("list", list.id);
    os.removeAttribute("autocomplete");
  }
  // Ops resource reservations are usually commodities.
  const reservation = $("ops-reservation-key");
  if (reservation) reservation.setAttribute("list", "commodity-list");
}

/* ---------- sortable result tables ----------
   Each searchable table caches its last results and re-renders from that cache
   when a header is clicked, so sorting never refires the search; the chosen
   sort survives new searches until the page reloads. A column map supplies the
   sort value and the direction a first click should mean ("better first" for
   prices/units/freshness, "closest first" for distances). */

function sortedRows(results, columns, sort, mode) {
  if (!sort || !columns[sort.key]) return results;
  const { value } = columns[sort.key];
  return [...results].sort((a, b) => {
    const va = value(a, mode);
    const vb = value(b, mode);
    if (va < vb) return -sort.dir;
    if (va > vb) return sort.dir;
    return 0;
  });
}

function updateSortIndicators(table, sort) {
  for (const th of table.querySelectorAll("th.sortable")) {
    th.classList.toggle("sort-asc", !!sort && th.dataset.sort === sort.key && sort.dir === 1);
    th.classList.toggle("sort-desc", !!sort && th.dataset.sort === sort.key && sort.dir === -1);
  }
}

// Clicking the active column reverses it; a new column starts at its natural
// direction (fallbackDir when the column defers, e.g. price depends on mode).
function bumpSort(sort, key, columns, fallbackDir = 1) {
  if (sort && sort.key === key) return { key, dir: -sort.dir };
  const firstDir = columns[key].firstDir != null ? columns[key].firstDir : fallbackDir;
  return { key, dir: firstDir };
}

function sortableHeaders(tableId, onSort) {
  $(tableId).querySelector("thead").addEventListener("click", (ev) => {
    const th = ev.target.closest("th.sortable");
    if (th && th.dataset.sort) onSort(th.dataset.sort);
  });
}

let csResults = null;   // { results, mode }
let csSort = null;      // { key, dir }  (dir: 1 ascending, -1 descending)

// A null price dir resolves per mode: buying wants cheap first, selling rich first.
const CS_SORT_COLUMNS = {
  station: { value: (r) => (r.station || "").toLowerCase(), firstDir: 1 },
  system: { value: (r) => (r.system || "").toLowerCase(), firstDir: 1 },
  price: {
    value: (r, mode) => (mode === "buy" ? r.buy_price : r.sell_price) || 0,
    firstDir: null,
  },
  units: { value: (r, mode) => (mode === "buy" ? r.supply : r.demand) || 0, firstDir: -1 },
  jump: { value: (r) => (r.distance != null ? Number(r.distance) : Infinity), firstDir: 1 },
  dist_ls: { value: (r) => (r.dist_ls != null ? Number(r.dist_ls) : Infinity), firstDir: 1 },
  updated: { value: (r) => Number(r.updated_at) || Date.parse(r.updated_at) || 0, firstDir: -1 },
};

function renderCommodityRows() {
  const table = $("cs-table");
  const tbody = table.querySelector("tbody");
  if (!csResults) return;
  const { results, mode } = csResults;
  updateSortIndicators(table, csSort);

  tbody.innerHTML = "";
  for (const r of sortedRows(results, CS_SORT_COLUMNS, csSort, mode)) {
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
      `<td class="num freshness-cell">${ageText(r.updated_at)} ${confidenceHtml(r.confidence)}</td>`;
    const td = document.createElement("td");
    td.appendChild(copySystemButton(r.system));
    td.appendChild(plotButton(r.system));
    tr.appendChild(td);
    tbody.appendChild(tr);
  }
}

function sortCommodityTable(key) {
  if (!CS_SORT_COLUMNS[key] || !csResults) return;
  csSort = bumpSort(csSort, key, CS_SORT_COLUMNS, csResults.mode === "buy" ? 1 : -1);
  renderCommodityRows();
}

async function searchCommodity(ev) {
  ev.preventDefault();
  const status = $("cs-status");
  const table = $("cs-table");
  const go = $("cs-go");
  const mode = $("cs-mode").value;
  const near = $("cs-near").value.trim();
  go.disabled = true;
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
    if (near) params.set("system", near);
    const resp = await fetch("/api/commodity-search?" + params);
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || "Search failed");
    csResults = { results: data.results || [], mode };
    renderCommodityRows();
    table.classList.toggle("hidden", !csResults.results.length);
    const where = near ? ` of ${near}` : "";
    status.textContent = csResults.results.length
      ? `${csResults.results.length} station(s) ${mode === "buy" ? "selling" : "buying"} ${data.commodity} within ${$("cs-radius").value} ly${where}. Click a column header to sort.`
      : `Nothing ${mode === "buy" ? "selling" : "buying"} ${data.commodity || "that"} ${near ? "near " + near : "nearby"} with those filters — ` +
        "widen the radius, or if you're deep in the black, WHERE TO SELL YOUR DATA (Explore tab) finds the nearest civilization.";
  } catch (err) {
    table.classList.add("hidden");
    status.classList.add("error");
    status.textContent = String(err.message || err);
  } finally {
    go.disabled = false;
  }
}

/* ---------- mining advisor ---------- */

let mnResults = null;
let mnSort = null;

const MN_SORT_COLUMNS = {
  mineral: { value: (r) => (r.name || "").toLowerCase(), firstDir: 1 },
  method: { value: (r) => (r.method || "").toLowerCase(), firstDir: 1 },
  sell: { value: (r) => r.sell_price || 0, firstDir: -1 },
  station: { value: (r) => (r.station || "").toLowerCase(), firstDir: 1 },
  jump: { value: (r) => (r.distance != null ? Number(r.distance) : Infinity), firstDir: 1 },
  demand: { value: (r) => r.demand || 0, firstDir: -1 },
};

function renderMiningRows() {
  const table = $("mining-table");
  const tbody = table.querySelector("tbody");
  if (!mnResults) return;
  updateSortIndicators(table, mnSort);
  tbody.innerHTML = "";
  for (const r of sortedRows(mnResults, MN_SORT_COLUMNS, mnSort)) {
    const tr = document.createElement("tr");
    tr.innerHTML =
      `<td><b>${esc(r.name)}</b></td>` +
      `<td><span class="mine-method mine-${esc(r.method)}">${esc(r.method)}</span></td>` +
      `<td class="num orange">${fmtNum(r.sell_price)}</td>` +
      `<td>${esc(r.station)}${r.large_pad ? "" : ' <span class="sub">no L pad</span>'}<div class="sub">${esc(r.system)} · ${confidenceAgeText(r.confidence?.age_s)} ${confidenceHtml(r.confidence)}</div></td>` +
      `<td class="num">${r.distance} ly</td>` +
      `<td class="num">${fmtNum(r.demand)}</td>`;
    const td = document.createElement("td");
    const hs = document.createElement("button");
    hs.className = "plotbtn";
    hs.type = "button";
    hs.textContent = "◇ hotspots";
    hs.title = "Find the nearest ring hotspots for " + r.name;
    hs.addEventListener("click", () => showHotspots(r.name, hs, tr));
    td.appendChild(hs);
    td.appendChild(copySystemButton(r.system));
    td.appendChild(plotButton(r.system));
    tr.appendChild(td);
    tbody.appendChild(tr);
  }
}

function sortMiningTable(key) {
  if (!MN_SORT_COLUMNS[key] || !mnResults) return;
  mnSort = bumpSort(mnSort, key, MN_SORT_COLUMNS);
  renderMiningRows();
}

async function searchMining(ev) {
  ev.preventDefault();
  const status = $("mining-status");
  const table = $("mining-table");
  const go = $("mn-go");
  const near = $("mn-near").value.trim();
  go.disabled = true;
  status.classList.remove("error");
  status.textContent = "Checking live prices…";
  try {
    const params = new URLSearchParams({
      radius: $("mn-radius").value || "50",
      min_price: $("mn-minprice").value || "0",
      max_price_age_days: $("mn-age").value || "30",
      large_pad: $("mn-largepad").checked ? "1" : "0",
    });
    if (near) params.set("system", near);
    const resp = await fetch("/api/mining?" + params);
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || "Search failed");
    mnResults = data.results || [];
    renderMiningRows();
    table.classList.toggle("hidden", !mnResults.length);
    status.textContent = mnResults.length
      ? `${mnResults.length} mineable commodities with buyers within ${$("mn-radius").value} ly` +
        `${near ? " of " + near : ""}${mnSort ? "" : ", best price first"}. ◇ finds where to mine each.`
      : `Nothing mineable selling ${near ? "near " + near : "nearby"} with those filters — widen the radius or lower Min price. ` +
        "(Deep in the black? There are no buyers out here — see WHERE TO SELL YOUR DATA on the Explore tab.)";
  } catch (err) {
    table.classList.add("hidden");
    status.classList.add("error");
    status.textContent = String(err.message || err);
  } finally {
    go.disabled = false;
  }
}

async function showHotspots(mineral, btn, afterRow) {
  const next = afterRow.nextSibling;
  if (next && next.classList && next.classList.contains("hotspot-row")) {
    next.remove();  // toggle off
    return;
  }
  btn.disabled = true;
  const detail = document.createElement("tr");
  detail.className = "hotspot-row";
  const cell = document.createElement("td");
  cell.colSpan = 7;
  cell.innerHTML = `<span class="dim">Finding nearest ${esc(mineral)} hotspots via Spansh…</span>`;
  detail.appendChild(cell);
  afterRow.after(detail);
  try {
    const resp = await fetch("/api/mining/hotspots?mineral=" + encodeURIComponent(mineral));
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || "Search failed");
    const hs = data.hotspots || [];
    if (!hs.length) {
      cell.innerHTML = `<span class="dim">No community-mapped ${esc(mineral)} hotspots found nearby.</span>`;
      return;
    }
    cell.innerHTML = `<div class="hotspots-title">Nearest <b>${esc(mineral)}</b> hotspots <span class="dim">· community-mapped · higher count = richer overlap</span></div>`;
    const list = document.createElement("div");
    list.className = "hotspot-list";
    for (const h of hs.slice(0, 10)) {
      const item = document.createElement("div");
      item.className = "hotspot-item";
      item.innerHTML =
        `<span class="hs-count">${h.count}×</span>` +
        `<b>${esc(h.ring)}</b> <span class="dim">${esc(h.system)} · ${h.distance} ly` +
        `${h.dist_ls != null ? " · " + fmtNum(Math.round(h.dist_ls)) + " ls" : ""}` +
        `${h.reserve ? " · " + esc(h.reserve) : ""}</span>`;
      item.appendChild(plotButton(h.system));
      list.appendChild(item);
    }
    cell.appendChild(list);
  } catch (err) {
    cell.innerHTML = `<span style="color:var(--bad)">${esc(String(err.message || err))}</span>`;
  } finally {
    btn.disabled = false;
  }
}

/* ---------- guides: exobiology route (Billionaire's Boulevard) ---------- */

// Genera the pilot wants the route restricted to (empty = every genus). This is
// a pre-query filter: only bodies hosting one of these come back from Spansh.
const exoGenera = new Set();

async function buildExoGenusChips() {
  const wrap = $("exo-genus-chips");
  if (!wrap) return;
  let genera;
  try {
    const resp = await fetch("/api/exobio-genera");
    genera = (await resp.json()).genera || [];
  } catch {
    return; // filter is optional; leave the row empty if the list can't load
  }
  wrap.innerHTML = "";
  genera.forEach((g) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "exo-chip";
    chip.textContent = g;
    chip.setAttribute("aria-pressed", "false");
    chip.addEventListener("click", () => {
      const on = !exoGenera.has(g);
      if (on) exoGenera.add(g); else exoGenera.delete(g);
      chip.classList.toggle("on", on);
      chip.setAttribute("aria-pressed", String(on));
      updateExoGenusHint();
    });
    wrap.appendChild(chip);
  });
  updateExoGenusHint();
}

function updateExoGenusHint() {
  const hint = $("exo-genus-hint");
  if (hint) hint.textContent = exoGenera.size
    ? `only ${[...exoGenera].sort().join(", ")}`
    : "none = every genus";
}

async function searchExobio(ev) {
  ev.preventDefault();
  const status = $("exo-status");
  const out = $("exo-results");
  const go = $("exo-go");
  go.disabled = true;
  status.classList.remove("error");
  status.textContent = "Searching Spansh for nearby bio-rich worlds… (~5–15s)";
  out.innerHTML = "";
  try {
    const params = new URLSearchParams({
      max_gravity: $("exo-grav").value || "0.5",
      min_value: $("exo-minvalue").value || "1000000",
    });
    if (exoGenera.size) params.set("genera", [...exoGenera].join(","));
    const resp = await fetch("/api/exobio-route?" + params);
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || "Search failed");
    const systems = data.systems || [];
    if (!systems.length) {
      status.textContent = exoGenera.size
        ? `No landable bodies hosting ${[...exoGenera].sort().join(", ")} found nearby — try clearing genera, raising Max gravity or lowering Min value.`
        : "No landable bodies with biological signals found near you at all — you may be in truly deep space.";
      return;
    }
    const genusNote = exoGenera.size
      ? `${[...exoGenera].sort().join(", ")} · `
      : "";
    const relaxNote = data.relaxed
      ? `Nothing cleared your ${esc(data.relaxed)} filter nearby, so here are the closest matching worlds regardless. `
      : "";
    status.textContent = relaxNote + genusNote +
      `${systems.length} systems in visit order · ≈${fmtNum(data.total_value)} cr of exobiology if you sample it all (species nobody has logged yet pay 5×).`;

    const summary = document.createElement("div");
    summary.className = "route-summary";
    summary.innerHTML =
      `<span class="profit">≈${fmtNum(data.total_value)} cr</span>` +
      `<span>${systems.length} systems</span>` +
      `<span>${systems[0].distance}–${systems[systems.length - 1].distance} ly out</span>`;
    summary.appendChild(trackButton("exobio", "Exobiology route",
      () => systems.map((s) => ({ system: s.system, note: "≈" + fmtNum(s.value) + " cr" }))));
    out.appendChild(summary);

    systems.forEach((s, i) => {
      const div = document.createElement("div");
      div.className = "hop";
      div.style.setProperty("--i", i);
      const bodies = s.bodies.map((b) =>
        `<div>${esc(b.body)} <span class="sub">${esc(b.subtype || "")} · ${b.gravity} g · ` +
        `${b.dist_ls != null ? fmtNum(Math.round(b.dist_ls)) + " ls" : "?"} · ≈${fmtNum(b.value)} cr</span>` +
        (b.genuses && b.genuses.length
          ? `<div class="sub exo-genuses">${b.genuses.map((g) =>
              exoGenera.has(g) ? `<b class="exo-hit">${esc(g)}</b>` : esc(g)).join(" · ")}</div>` : "") +
        `</div>`
      ).join("");
      div.innerHTML =
        `<div class="route-line"><span class="dim">#${i + 1}</span><b>${esc(s.system)}</b>` +
        `<span class="dim">${s.distance} ly</span>` +
        `<span class="profit">≈${fmtNum(s.value)} cr</span></div>` +
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
    // Pre-flight fuel check: neutron stars can't be scooped, so flag legs
    // longer than the tank's estimated jump budget (worst recent burn).
    const tankJumps = state && state.nav && state.nav.fuel_per_jump > 0 && state.fuel_capacity
      ? Math.floor(state.fuel_capacity / state.nav.fuel_per_jump) : null;
    if (wps.length && tankJumps) {
      status.append(` ⛽ Your tank ≈${tankJumps} jumps at recent burn — neutron stars can't be scooped, so top off before flagged legs.`);
    }
    if (wps.length) {
      status.append(" ");
      status.appendChild(trackButton("neutron", "Neutron: " + ($("nr-to").value.trim() || "route"),
        () => wps.map((w) => ({ system: w.system, note: w.neutron ? "☄ neutron" : "" }))));
    }
    tbody.innerHTML = "";
    wps.forEach((w, i) => {
      const dryLeg = tankJumps != null && w.jumps != null && w.jumps >= tankJumps;
      const tr = document.createElement("tr");
      tr.innerHTML =
        `<td>${i + 1}</td>` +
        `<td>${esc(w.system)}${w.neutron ? ' <span class="orange">☄ neutron</span>' : ""}` +
        (dryLeg ? ` <span class="warn" title="Reaching this waypoint takes ≈${w.jumps} jumps — about a full tank at your recent burn rate. Top off first and refuel at a normal (KGB FOAM) star along the way.">⚠ ${w.jumps}-jump leg — top off</span>` : "") +
        `</td>` +
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

let osResults = null;
let osSort = null;

const OS_SORT_COLUMNS = {
  station: { value: (r) => (r.station || "").toLowerCase(), firstDir: 1 },
  system: { value: (r) => (r.system || "").toLowerCase(), firstDir: 1 },
  jump: { value: (r) => (r.distance != null ? Number(r.distance) : Infinity), firstDir: 1 },
  dist_ls: { value: (r) => (r.dist_ls != null ? Number(r.dist_ls) : Infinity), firstDir: 1 },
  pad: { value: (r) => (r.large_pad ? 0 : 1), firstDir: 1 },
};

function renderStationRows() {
  const table = $("os-table");
  const tbody = table.querySelector("tbody");
  if (!osResults) return;
  updateSortIndicators(table, osSort);
  tbody.innerHTML = "";
  for (const r of sortedRows(osResults, OS_SORT_COLUMNS, osSort)) {
    const tr = document.createElement("tr");
    tr.innerHTML =
      `<td>${esc(r.station)}<div class="sub">${esc(r.type || "")}</div></td>` +
      `<td>${esc(r.system)}</td>` +
      `<td class="num">${r.distance} ly</td>` +
      `<td class="num">${r.dist_ls != null ? fmtNum(Math.round(r.dist_ls)) + " ls" : "?"}</td>` +
      `<td>${r.large_pad ? "L" : "M/S"}</td>`;
    const td = document.createElement("td");
    td.appendChild(copySystemButton(r.system));
    td.appendChild(plotButton(r.system));
    tr.appendChild(td);
    tbody.appendChild(tr);
  }
}

function sortStationTable(key) {
  if (!OS_SORT_COLUMNS[key] || !osResults) return;
  osSort = bumpSort(osSort, key, OS_SORT_COLUMNS);
  renderStationRows();
}

async function searchStations(ev) {
  ev.preventDefault();
  const status = $("os-status");
  const table = $("os-table");
  const go = $("os-go");
  const near = $("os-near").value.trim();
  go.disabled = true;
  status.classList.remove("error");
  status.textContent = "Searching…";
  try {
    const params = new URLSearchParams({ q: $("os-query").value.trim(), type: $("os-type").value });
    if (near) params.set("system", near);
    const resp = await fetch("/api/station-search?" + params);
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || "Search failed");
    osResults = data.results || [];
    status.textContent = osResults.length
      ? `${osResults.length} station(s) with "${$("os-query").value.trim()}" nearest ${near || "you"}:`
      : "Nothing found — check the spelling (e.g. '6A Fuel Scoop', 'Python Mk II').";
    renderStationRows();
    table.classList.toggle("hidden", osResults.length === 0);
  } catch (err) {
    status.classList.add("error");
    status.textContent = String(err.message || err);
  } finally {
    go.disabled = false;
  }
}

/* ---------- colonization ---------- */

const coloSources = {};  // market_id -> {symbol -> sources[]} (data, so plot buttons can be rebuilt live)

function fillSourceCell(cell, sources) {
  cell.innerHTML = "";
  if (!(sources || []).length) {
    cell.innerHTML = '<span class="dim">none within 50 ly</span>';
    return;
  }
  for (const s of sources) {
    const row = document.createElement("div");
    row.className = "colo-src";
    row.innerHTML = `<b>${esc(s.station)}</b> <span class="sub">${esc(s.system)} · ` +
      `${fmtNum(s.buy_price)} cr · ${fmtNum(s.supply)} supply · ${s.distance} ly</span>`;
    row.appendChild(plotButton(s.system));
    cell.appendChild(row);
  }
}

function renderColonisation() {
  const list = $("colonisation-list");
  const depots = (state.colonisation || []).filter((d) => !d.complete && !d.failed);
  $("colonisation-empty").classList.toggle("hidden", depots.length > 0);
  // The depot event re-fires every few seconds while docked with only its
  // timestamp moving — leave it out of the signature so the card doesn't
  // rebuild (flashing, and eating fetched sources) unless something real
  // changed: progress, deliveries, a new project.
  const sig = JSON.stringify(depots.map(({ updated, ...rest }) => rest));
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
          const cache = (coloSources[d.market_id] = {});
          for (const c of data.commodities || []) {
            const cell = div.querySelector(`.src[data-symbol="${CSS.escape(c.symbol)}"]`);
            if (!cell) continue;
            cache[c.symbol] = c.sources || [];
            fillSourceCell(cell, cache[c.symbol]);
          }
          btn.textContent = "REFRESH";
          btn.disabled = false;
        } catch (e) {
          btn.textContent = "FIND SOURCES";
          btn.disabled = false;
        }
      });
      div.querySelector(".route-line").insertBefore(btn, div.querySelector(".profit"));
      // Survive rebuilds (progress ticks, deliveries): re-fill previously
      // fetched sources instead of losing them.
      const cached = coloSources[d.market_id];
      if (cached) {
        let hits = 0;
        for (const [sym, sources] of Object.entries(cached)) {
          const cell = div.querySelector(`.src[data-symbol="${CSS.escape(sym)}"]`);
          if (cell) { fillSourceCell(cell, sources); hits++; }
        }
        if (hits) btn.textContent = "REFRESH";
      }
    }
    list.appendChild(div);
  }
}

/* ---------- best sell for current cargo ---------- */

function renderCargoBuyers(results, recovery = false) {
  const out = $("cargo-sell-results");
  out.innerHTML = "";
  results.slice(0, 5).forEach((r, idx) => {
    const div = document.createElement("div");
    div.className = "hop";
    div.style.setProperty("--i", idx);
    const items = (r.items || []).map((item) =>
      `${esc(item.name)} ×${fmtNum(item.units)} @ ${fmtNum(item.sell_price)}${item.partial ? " (demand-capped)" : ""}`
    ).join(" · ");
    div.innerHTML =
      `<div class="route-line"><b>${esc(r.station)}</b><span class="dim">${esc(r.system)}</span>` +
      confidenceHtml(r.confidence) +
      `<span class="profit">+${fmtNum(r.total)} cr observed</span></div>` +
      `<div class="commodities">${r.distance} ly · ${r.dist_ls != null ? fmtNum(r.dist_ls) + " ls" : "?"}` +
      `${r.large_pad ? "" : " · no L pad"}` +
      (r.payout_range ? ` · ${creditRangeHtml(r.payout_range, "cr payout")}` : "") +
      ` · ${confidenceAgeText(r.confidence?.age_s)} · ${items}</div>`;
    const line = div.querySelector(".route-line");
    if (recovery && idx === 0) {
      const mark = document.createElement("span");
      mark.className = "recovery-mark";
      mark.textContent = "RECOMMENDED DIVERSION";
      line.insertBefore(mark, line.querySelector(".profit"));
    }
    line.insertBefore(plotButton(r.system), line.querySelector(".profit"));
    out.appendChild(div);
  });
}

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
    renderCargoBuyers(results);
  } catch (err) {
    status.classList.add("error");
    status.textContent = String(err.message || err);
  }
}

async function recoverCargo(failedMarketId, button) {
  const status = $("cargo-sell-status");
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "REPLANNING…";
  try {
    const response = await fetch("/api/cargo-recovery", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ failed_market_id: failedMarketId, radius: 100, max_age_days: 7, limit: 5 }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Cargo recovery failed");
    const results = [data.recommended, ...(data.alternatives || [])].filter(Boolean);
    if (document.body.classList.contains("panel-mode")) setPanelPage("local");
    else activateTab("local");
    status.classList.toggle("error", !results.length);
    status.textContent = results.length
      ? "Diversion calculated from the cargo currently aboard. The failed market is excluded; payouts remain observations, not guarantees."
      : "No viable replacement buyer was found within 100 ly using market reports from the last 7 days.";
    renderCargoBuyers(results, true);
    $("cargo-sell-results").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    status.classList.add("error");
    status.textContent = String(error.message || error);
    if (document.body.classList.contains("panel-mode")) setPanelPage("local");
    else activateTab("local");
  } finally {
    button.disabled = false;
    button.textContent = original;
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

/* Centered placeholder inside an SVG chart so an empty card explains itself. */
function chartEmptyNote(svg, msg) {
  const W = svg.clientWidth || 900, H = 120;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svgEl("text", { x: W / 2, y: H / 2, "text-anchor": "middle", fill: "var(--dim)", "font-size": 13 }, svg)
    .textContent = msg;
}

function drawBalanceChart(svg, points) {
  svg.innerHTML = "";
  if (points.length < 2) {
    chartEmptyNote(svg, "No balance history yet — it records as you play (and big journal imports fill it in).");
    return;
  }
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
  svgEl("path", { d, fill: "none", stroke: accentColor(), "stroke-width": 2, "stroke-linejoin": "round" }, svg);
  const last = points[points.length - 1];
  svgEl("circle", { cx: x(last.ts), cy: y(last.balance), r: 3.5, fill: accentColor() }, svg);
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
  if (!days.length) {
    chartEmptyNote(svg, "No trading days recorded yet — sell some cargo and daily profit shows here.");
    return;
  }
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

function clearAnalyticsWorkspace() {
  for (const id of ["an-today", "an-week", "an-period", "an-tons", "session-trade", "session-tons"]) {
    if ($(id)) $(id).textContent = "\u2014";
  }
  renderEarnings({});
  for (const id of ["an-balance", "an-daily"]) {
    if ($(id)) $(id).replaceChildren();
  }
  if ($("an-top")) {
    $("an-top").classList.add("hidden");
    $("an-top").querySelector("tbody")?.replaceChildren();
  }
  $("an-empty")?.classList.remove("hidden");
}

async function loadAnalytics() {
  const generation = profileGeneration;
  const expectedCommander = profileStorageId(state);
  if (!expectedCommander) {
    clearAnalyticsWorkspace();
    return;
  }
  try {
    const resp = await fetch("/api/analytics?days=" + $("an-days").value, { cache: "no-store" });
    if (!resp.ok) return;
    const a = await resp.json();
    if (generation !== profileGeneration
        || expectedCommander !== profileStorageId(state)
        || String(a.commander_id || "") !== expectedCommander) return;
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
  nudgeDbStatus();  // single poll chain — never fork a second timer loop
}

let dbPollTimer = null;

async function pollDbStatus() {
  // Don't hammer this: fast only while a build runs, relaxed while the
  // Database page is actually on screen, and barely at all otherwise —
  // the DB stats page is the only consumer.
  let seeding = false;
  try {
    const resp = await fetch("/api/marketdb/status", { cache: "no-store" });
    if (resp.ok) {
      const s = await resp.json();
      renderDbStatus(s);
      seeding = !!(s.seeding && (s.seeding.phase === "downloading" || s.seeding.phase === "importing"));
    }
  } catch (e) { /* retry next tick */ }
  const dbVisible = !document.hidden && $("db-status").offsetParent !== null;
  dbPollTimer = setTimeout(pollDbStatus, seeding ? 1500 : dbVisible ? 15000 : 120000);
}

function nudgeDbStatus() {
  // Opening the Database page shouldn't wait out a 2-minute idle timer.
  clearTimeout(dbPollTimer);
  pollDbStatus();
}

/* First-run nudge: a fresh install works, but its best features (trade
   loops, commodity search, mining, colonisation sourcing) need the local
   market database. Until it's built — and unless dismissed — say so
   plainly and point at the button, so setup is never a scavenger hunt. */
function renderSetupBanner(s) {
  const el = $("setup-banner");
  if (!el) return;
  const seeding = s.seeding || {};
  const busy = seeding.phase === "downloading" || seeding.phase === "importing";
  const show = !s.ready && !busy
    && localStorage.getItem("dbSetupDismissed") !== "1"
    && !(state && state.journal_dir_found === false);  // one problem at a time
  el.classList.toggle("hidden", !show);
  if (!show || el.dataset.built) return;
  el.dataset.built = "1";
  el.innerHTML =
    `<span class="ub-badge">⚑ FIRST-TIME SETUP</span>` +
    `<span class="ub-text">Build the <b>local market database</b> to unlock trade loops, commodity ` +
    `search and mining <span class="dim">(one-time ~3.9 GB download, ~15 min — EDDN keeps it fresh afterwards)</span></span>`;
  const go = document.createElement("button");
  go.className = "ub-btn";
  go.textContent = "TAKE ME THERE";
  go.addEventListener("click", () => {
    if (document.body.classList.contains("panel-mode")) setPanelPage("database");
    else activateTab("database");
    const btn = $("seed-btn");
    if (btn) btn.scrollIntoView({ behavior: "smooth", block: "center" });
  });
  const dismiss = document.createElement("button");
  dismiss.className = "ub-dismiss";
  dismiss.textContent = "✕";
  dismiss.title = "Hide this reminder on this device — you can build the database any time from the Settings page";
  dismiss.setAttribute("aria-label", "Dismiss setup reminder");
  dismiss.addEventListener("click", () => {
    localStorage.setItem("dbSetupDismissed", "1");
    el.classList.add("hidden");
  });
  el.appendChild(go);
  el.appendChild(dismiss);
}

function renderDbStatus(s) {
  renderSetupBanner(s);
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
  let delay = 30 * 60 * 1000;  // re-check every 30 min so a new release is noticed
  try {
    const resp = await fetch("/api/update/check", { cache: "no-store" });
    if (resp.ok) {
      updateInfo = await resp.json();
      if (updateInfo.current) $("app-version").textContent = "v" + updateInfo.current;
      renderUpdateBanner();
      if (updateInfo.error) delay = 60 * 1000;  // transient check error: retry soon
    } else {
      delay = 60 * 1000;
    }
  } catch (e) {
    delay = 60 * 1000;  // server not up yet (just launched): retry soon, not in hours
  }
  setTimeout(pollUpdate, delay);
}

async function checkForUpdatesNow(btn, stat) {
  btn.disabled = true;
  stat.textContent = "Checking…";
  try {
    const resp = await fetch("/api/update/check?force=1", { cache: "no-store" });
    updateInfo = await resp.json();
    if (updateInfo.current) $("app-version").textContent = "v" + updateInfo.current;
    renderUpdateBanner();
    if (updateInfo.error) stat.textContent = updateInfo.error;
    else if (updateInfo.available) stat.textContent = `v${updateInfo.latest} available — see the banner at the top.`;
    else stat.textContent = `You're on the latest version (v${updateInfo.current}).`;
  } catch (e) {
    stat.textContent = "Couldn't check right now.";
  } finally {
    btn.disabled = false;
  }
}

/* Tiny markdown renderer for GitHub release bodies: headings, lists, bold,
   code, links, quotes and rules. Input is escaped before any tags are added. */
function mdToHtml(md) {
  const inline = (s) => esc(s)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/\*([^*]+)\*/g, "<i>$1</i>")
    .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener">$1</a>');
  // Join lazy continuations first: release notes are hard-wrapped at ~80
  // columns, and like GitHub we fold a plain line into the paragraph or list
  // item above it rather than starting a new block.
  const special = (l) => /^\s*[-*]\s+/.test(l) || /^#{1,4}\s/.test(l) || /^-{3,}$/.test(l) || l.startsWith(">");
  const lines = [];
  for (const raw of String(md || "").replace(/\r/g, "").split("\n")) {
    const line = raw.trimEnd();
    if (line && lines.length && lines[lines.length - 1] && !special(line) &&
        !/^#{1,4}\s|^-{3,}$/.test(lines[lines.length - 1])) {
      lines[lines.length - 1] += " " + line.trim();
    } else {
      lines.push(line);
    }
  }
  let html = "", inList = false;
  for (const line of lines) {
    const li = line.match(/^\s*[-*]\s+(.*)/);
    if (li) {
      if (!inList) { html += "<ul>"; inList = true; }
      html += `<li>${inline(li[1])}</li>`;
      continue;
    }
    if (inList) { html += "</ul>"; inList = false; }
    const h = line.match(/^(#{1,4})\s+(.*)/);
    if (h) { const lvl = Math.min(h[1].length + 2, 5); html += `<h${lvl}>${inline(h[2])}</h${lvl}>`; continue; }
    if (/^-{3,}$/.test(line)) { html += "<hr>"; continue; }
    if (line.startsWith(">")) { html += `<blockquote>${inline(line.replace(/^>\s?/, ""))}</blockquote>`; continue; }
    if (line) html += `<p>${inline(line)}</p>`;
  }
  if (inList) html += "</ul>";
  return html;
}

function showReleaseNotes() {
  if (!updateInfo) return;
  if (!updateInfo.notes) {
    // No body on the release — fall back to opening it externally.
    if (!openExternal(updateInfo.notes_url, "Release notes")) window.open(updateInfo.notes_url, "_blank");
    return;
  }
  $("notes-title").textContent = updateInfo.notes_title || `Frameshift v${updateInfo.latest}`;
  $("notes-body").innerHTML = mdToHtml(updateInfo.notes);
  $("notes-external").href = updateInfo.notes_url;
  $("notes-modal").classList.remove("hidden");
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
    `<span class="ub-text">Frameshift <b>v${esc(updateInfo.latest)}</b> is available` +
    ` <span class="dim">(you have v${esc(updateInfo.current)})</span></span>`;
  const notes = document.createElement("a");
  notes.href = updateInfo.notes_url;
  notes.target = "_blank";
  notes.rel = "noopener";
  notes.className = "ub-notes";
  notes.textContent = "release notes";
  notes.addEventListener("click", (ev) => { ev.preventDefault(); showReleaseNotes(); });
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
      if (status) status.textContent = "Restarting — Frameshift will reopen in a moment.";
    } else if (s.phase === "error") {
      updateApplying = false;
      if (status) status.textContent = "Update failed: " + (s.error || "unknown error");
      $("update-banner").classList.add("ub-error");
      return;
    }
  } catch (e) {
    // Connection lost while restarting is the expected success signal.
    if (status) status.textContent = "Restarting — Frameshift will reopen in a moment. You can close this tab.";
    return;
  }
  setTimeout(pollUpdateStatus, 700);
}

/* ---------- settings ---------- */

const SETTINGS_DEFS = [
  { key: "exclude_surface", label: "Exclude surface stations",
    desc: "Hide planetary outposts, ports and settlements from trade routes, searches and mining — orbital stations only." },
  { key: "exclude_carriers", label: "Exclude fleet carriers",
    desc: "Keep fleet carriers out of the market database and its results — carriers move, so listed positions go stale. Untick to collect carrier markets from the live feed too (rebuild the database to include them from the start)." },
  { key: "eddn_upload", label: "Contribute market data (EDDN)",
    desc: "Upload only commodity markets you dock at back to the community feed this app is built on. Anonymous and enabled by default." },
  { key: "eddn_extended_upload", label: "Contribute exploration & navigation observations (EDDN)",
    desc: "Optional broader contribution: routes, scans, biological signals, exact Codex/settlement coordinates, docking outcomes, outfitting, shipyard and carrier-material observations. Anonymous; off until you opt in." },
  { key: "auto_update", label: "Automatic updates",
    desc: "Check for new releases and offer a one-click update.", requires: "auto_update_supported" },
];

async function loadSettings() {
  try {
    const resp = await fetch("/api/settings", { cache: "no-store" });
    if (!resp.ok) return;
    const data = await resp.json();
    renderSettings(data.settings || {}, data.info || {});
  } catch (e) { /* offline */ }
}

/* CRT ambience (scanlines + readout flicker) is a per-device display choice:
   it lives in this browser's localStorage, not the server settings. Off by
   default — the drifting scanlines can shimmer on some screens. */
function applyCrtFx() {
  document.body.classList.toggle("crt-fx", localStorage.getItem("crtFx") === "1");
}

/* ---------- color themes (per device) ----------
   Commanders re-color their in-game HUD; the companion should follow suit.
   Only the ACCENT changes — the dark cockpit background and the semantic
   good/bad colors stay, so every combination remains readable. */

const THEME_PRESETS = {
  elite:   { label: "Elite Orange", accent: "#ff7100", soft: "#ff9a40" },
  ice:     { label: "Ice Blue",     accent: "#35a7ff", soft: "#7cc4ff" },
  emerald: { label: "Emerald",      accent: "#2ecc71", soft: "#82e0aa" },
  gold:    { label: "Gold",         accent: "#ffbf00", soft: "#ffd966" },
  crimson: { label: "Crimson",      accent: "#ff4438", soft: "#ff8a80" },
  violet:  { label: "Violet",       accent: "#a86bff", soft: "#c9a2ff" },
};

function hexToRgb(hex) {
  const m = /^#?([0-9a-f]{6})$/i.exec((hex || "").trim());
  if (!m) return null;
  const n = parseInt(m[1], 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

/* Custom accents need a lighter companion shade (labels, hovers): blend 35%
   toward white, mirroring how the stock soft orange relates to the accent. */
function softenAccent(hex) {
  const rgb = hexToRgb(hex);
  if (!rgb) return hex;
  return "#" + rgb.map((c) => Math.round(c + (255 - c) * 0.35))
    .map((c) => c.toString(16).padStart(2, "0")).join("");
}

function currentTheme() {
  return localStorage.getItem("accentTheme") || "elite";
}

function applyTheme() {
  const t = currentTheme();
  const preset = THEME_PRESETS[t];
  const accent = preset ? preset.accent : (hexToRgb(t) ? t : THEME_PRESETS.elite.accent);
  const soft = preset ? preset.soft : softenAccent(accent);
  const root = document.documentElement.style;
  root.setProperty("--orange", accent);
  root.setProperty("--orange-soft", soft);
  root.setProperty("--accent-rgb", hexToRgb(accent).join(", "));
  root.setProperty("--accent-soft-rgb", hexToRgb(soft).join(", "));
}

/* Charts draw with the live accent (SVG attributes can't read CSS vars). */
function accentColor() {
  return getComputedStyle(document.documentElement).getPropertyValue("--orange").trim() || "#ff7100";
}

function buildThemeSetting() {
  const wrap = document.createElement("div");
  wrap.className = "setting setting-theme";
  wrap.innerHTML =
    `<div class="setting-text"><b>Color theme</b>` +
    `<div class="dim">The accent color on this device — match your in-game HUD. ` +
    `Presets are tuned for readability; Custom takes any color.</div></div>`;
  const chips = document.createElement("div");
  chips.className = "theme-chips";
  const custom = document.createElement("label");
  const customInput = document.createElement("input");
  const syncActive = () => {
    const t = currentTheme();
    chips.querySelectorAll("[data-theme]").forEach((b) =>
      b.classList.toggle("on", b.dataset.theme === t));
    custom.classList.toggle("on", !THEME_PRESETS[t]);
    custom.style.setProperty("--chip", !THEME_PRESETS[t] ? t : "#888");
  };
  for (const [id, p] of Object.entries(THEME_PRESETS)) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "theme-chip";
    b.dataset.theme = id;
    b.style.setProperty("--chip", p.accent);
    b.innerHTML = `<span class="theme-dot" aria-hidden="true"></span>${p.label}`;
    b.addEventListener("click", () => {
      localStorage.setItem("accentTheme", id);
      applyTheme();
      syncActive();
    });
    chips.appendChild(b);
  }
  custom.className = "theme-chip theme-custom";
  customInput.type = "color";
  customInput.value = hexToRgb(currentTheme()) ? currentTheme() : THEME_PRESETS.elite.accent;
  customInput.addEventListener("input", () => {
    localStorage.setItem("accentTheme", customInput.value);
    applyTheme();
    syncActive();
  });
  custom.append(customInput);
  custom.appendChild(document.createTextNode("Custom…"));
  chips.appendChild(custom);
  wrap.appendChild(chips);
  syncActive();
  return wrap;
}

/* Per-device size preferences: whole-app zoom plus finer dials for the two
   things squinted at most — the status strip and the small helper text. */
const DISPLAY_DEFAULTS = { uiScale: 100, stripScale: 100, helperScale: 100, voiceVolume: 100 };

function displayVal(key) {
  const v = parseInt(localStorage.getItem(key), 10);
  return Number.isFinite(v) ? v : DISPLAY_DEFAULTS[key];
}

function applyDisplaySettings() {
  document.body.style.zoom = displayVal("uiScale") / 100;
  const root = document.documentElement.style;
  root.setProperty("--strip-scale", displayVal("stripScale") / 100);
  root.setProperty("--helper-scale", displayVal("helperScale") / 100);
}

function voiceVolume() {
  return Math.max(0, Math.min(1, displayVal("voiceVolume") / 100));
}

function buildSliderSetting({ key, label, desc, min, max, step, unit, onRelease }) {
  const row = document.createElement("label");
  row.className = "setting";
  const input = document.createElement("input");
  input.type = "range";
  input.min = min; input.max = max; input.step = step;
  input.value = displayVal(key);
  const txt = document.createElement("div");
  txt.className = "setting-text";
  const title = document.createElement("b");
  const val = document.createElement("span");
  val.className = "range-val";
  const sync = () => { val.textContent = ` ${input.value}${unit}`; };
  title.textContent = label;
  title.appendChild(val);
  const hint = document.createElement("div");
  hint.className = "dim";
  hint.textContent = desc + " Double-click the slider to reset.";
  txt.append(title, hint);
  sync();
  input.addEventListener("input", () => {
    localStorage.setItem(key, input.value);
    sync();
    applyDisplaySettings();
  });
  if (onRelease) input.addEventListener("change", () => onRelease(Number(input.value)));
  input.addEventListener("dblclick", () => {
    input.value = DISPLAY_DEFAULTS[key];
    localStorage.setItem(key, input.value);
    sync();
    applyDisplaySettings();
  });
  row.append(input, txt);
  return row;
}

function buildDisplaySettings() {
  return [
    buildSliderSetting({
      key: "uiScale", label: "Interface size", unit: "%",
      min: 80, max: 140, step: 5,
      desc: "Zooms the whole app on this device — every page, desktop and panel mode alike.",
    }),
    buildSliderSetting({
      key: "stripScale", label: "Status bar text", unit: "%",
      min: 100, max: 160, step: 5,
      desc: "Size of the top status strip in panel mode: system, station, fuel, cargo, clock.",
    }),
    buildSliderSetting({
      key: "helperScale", label: "Helper text", unit: "%",
      min: 100, max: 150, step: 5,
      desc: "Size of the small grey hints and descriptions, like this one.",
    }),
  ];
}

function buildVoiceVolumeSetting() {
  return buildSliderSetting({
    key: "voiceVolume", label: "Voice volume", unit: "%",
    min: 0, max: 100, step: 5,
    desc: "Callout loudness on this device — applies to the neural and browser voices alike.",
    onRelease: (v) => { if (v > 0) speak(`Voice volume ${v} percent.`, true); },
  });
}

function buildCrtSetting() {
  const row = document.createElement("label");
  row.className = "setting";
  const cb = document.createElement("input");
  cb.type = "checkbox";
  cb.checked = localStorage.getItem("crtFx") === "1";
  cb.addEventListener("change", () => {
    localStorage.setItem("crtFx", cb.checked ? "1" : "0");
    applyCrtFx();
  });
  const sw = document.createElement("span");
  sw.className = "switch";
  const txt = document.createElement("div");
  txt.className = "setting-text";
  txt.innerHTML = "<b>CRT effects</b><div class=\"dim\">Retro scanlines and readout " +
    "flicker in the flight panel. Saved on this device only — they can shimmer on some screens.</div>";
  row.append(cb, sw, txt);
  return row;
}

/* Neural voice setting: download-once server feature (like the market DB),
   plus a per-device on/off once installed. */
function buildTtsSetting() {
  const wrap = document.createElement("div");
  wrap.className = "tts-wrap";
  const render = (st) => {
    wrap.innerHTML = "";
    const txt = document.createElement("div");
    txt.className = "setting-text";

    // Voice picker: switching to an installed voice is instant; picking a new
    // one downloads it (~60-115 MB) and activates when done. Server-wide —
    // synthesis happens on the PC, every device hears the chosen voice.
    const sel = document.createElement("select");
    sel.className = "tts-voices";
    for (const v of (st && st.voices) || []) {
      const opt = document.createElement("option");
      opt.value = v.name;
      opt.textContent = `${v.label} ${v.installed ? "· installed" : `· ~${v.mb} MB download`}`;
      opt.selected = st && st.voice === v.name;
      sel.appendChild(opt);
    }
    sel.disabled = !!(st && (st.downloading || st.supported === false));
    sel.addEventListener("change", async () => {
      sel.disabled = true;
      try {
        await fetch("/api/tts/voice", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ voice: sel.value }),
        });
      } catch (e) { /* status poll shows the outcome */ }
      loadTtsStatus().then(render);
    });

    if (st && st.ready) {
      const row = document.createElement("label");
      row.className = "setting";
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = localStorage.getItem("neuralVoice") !== "0";
      cb.addEventListener("change", () => localStorage.setItem("neuralVoice", cb.checked ? "1" : "0"));
      const sw = document.createElement("span");
      sw.className = "switch";
      txt.innerHTML = "<b>Neural voice</b>" +
        "<div class=\"dim\">Human-sounding callouts, synthesized on this PC by Piper. " +
        "The voice is shared by every device; this on/off switch is per device.</div>";
      row.append(cb, sw, txt);
      const test = document.createElement("button");
      test.className = "primary small";
      test.textContent = "TEST";
      test.title = "Play a sample callout with the neural voice (even while the switch is off)";
      test.addEventListener("click", () =>
        playNeural("Neural voice online. All systems nominal. o7").catch(() => {}));
      wrap.append(row, sel, test);
      return;
    }
    const row = document.createElement("div");
    row.className = "setting tts-static";
    if (st && st.downloading) {
      txt.innerHTML = `<b>Neural voice</b><div class="dim">Downloading the voice… ${Math.round((st.progress || 0) * 100)}% — callouts switch over automatically when it finishes.</div>`;
      row.appendChild(txt);
      wrap.appendChild(row);
      setTimeout(() => loadTtsStatus().then(render), 2000);
      return;
    }
    txt.innerHTML = "<b>Neural voice</b>" +
      "<div class=\"dim\">Replace the robotic browser voice with a " +
      "human-sounding one, synthesized locally on this PC — every device on your LAN hears it. " +
      "One-time download (Piper TTS + the voice you pick), fully offline afterwards." +
      (st && st.error ? ` <span class="bad-text">${esc(st.error)}</span>` : "") +
      (st && st.supported === false ? " Not available on this platform." : "") + "</div>";
    row.appendChild(txt);
    const btn = document.createElement("button");
    btn.className = "primary small";
    btn.textContent = "DOWNLOAD VOICE";
    btn.disabled = !!(st && st.supported === false);
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        await fetch("/api/tts/download", { method: "POST" });
      } catch (e) { /* status poll shows the outcome */ }
      loadTtsStatus().then(render);
    });
    wrap.append(row, sel, btn);
  };
  loadTtsStatus().then(render);
  return wrap;
}

function renderSettings(values, info) {
  const list = $("settings-list");
  if (!list) return;
  list.innerHTML = "";
  list.appendChild(buildThemeSetting());
  list.appendChild(buildTtsSetting());
  list.appendChild(buildVoiceVolumeSetting());
  list.appendChild(buildCrtSetting());
  for (const row of buildDisplaySettings()) list.appendChild(row);
  for (const def of SETTINGS_DEFS) {
    const supported = !def.requires || info[def.requires];
    const row = document.createElement("label");
    row.className = "setting" + (supported ? "" : " disabled");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = !!values[def.key];
    cb.disabled = !supported;
    cb.addEventListener("change", () => saveSetting(def.key, cb.checked, row));
    const sw = document.createElement("span");
    sw.className = "switch";
    const txt = document.createElement("div");
    txt.className = "setting-text";
    txt.innerHTML = `<b>${esc(def.label)}</b><div class="dim">${esc(def.desc)}` +
      `${supported ? "" : " Packaged Windows app only."}</div>`;
    row.append(cb, sw, txt);
    list.appendChild(row);
  }
  list.appendChild(buildJournalDirSetting(values));

  if (info.auto_update_supported) {
    const wrap = document.createElement("div");
    wrap.className = "update-check-row";
    const btn = document.createElement("button");
    btn.className = "primary small";
    btn.textContent = "Check for updates now";
    const stat = document.createElement("span");
    stat.className = "dim";
    btn.addEventListener("click", () => checkForUpdatesNow(btn, stat));
    wrap.append(btn, stat);
    list.appendChild(wrap);
  }

  const parts = [`Frameshift v${esc(info.version || "?")}`];
  if (info.journal_dir) parts.push(`journal: <span class="path">${esc(info.journal_dir)}</span>`);
  if (info.data_dir) parts.push(`data: <span class="path">${esc(info.data_dir)}</span>`);
  $("settings-info").innerHTML = parts.join(" · ");
}

/* Journal-folder setting: a validated text path. Blank = auto-detect. Saved
   changes are picked up by the journal watcher within a second, no restart. */
function buildJournalDirSetting(values) {
  const wrap = document.createElement("div");
  wrap.className = "setting setting-journal";
  wrap.innerHTML =
    `<div class="setting-text"><b>Journal folder</b>` +
    `<div class="dim">Where Elite Dangerous writes its journal. Leave blank to auto-detect. ` +
    `Takes effect immediately.</div></div>`;
  const row = document.createElement("div");
  row.className = "journal-dir-row";
  const input = document.createElement("input");
  input.type = "text";
  input.id = "journal-dir-input";
  input.placeholder = "auto-detect";
  input.value = values.journal_dir || "";
  input.setAttribute("spellcheck", "false");
  const save = document.createElement("button");
  save.className = "primary small";
  save.textContent = "SAVE";
  const status = document.createElement("div");
  status.className = "dim journal-dir-status";

  let timer = null, seq = 0;
  const validate = async () => {
    const mine = ++seq;
    try {
      const resp = await fetch("/api/journal-dir/validate?path=" + encodeURIComponent(input.value.trim()));
      const v = await resp.json();
      if (mine !== seq) return;  // a newer keystroke's check superseded this one
      status.classList.toggle("error", !v.exists && !v.unchecked);
      status.textContent = v.unchecked
        ? `– can't check ${v.path} from here (outside your user profile); SAVE still applies it`
        : v.exists
          ? `✓ ${v.files} journal file${v.files === 1 ? "" : "s"} in ${v.path}` + (v.auto ? " (auto-detected)" : "")
          : `✗ folder not found: ${v.path}`;
    } catch { /* server briefly unreachable */ }
  };
  input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(validate, 350); });
  save.addEventListener("click", async () => {
    await saveSetting("journal_dir", input.value.trim(), wrap);
    validate();
  });
  row.append(input, save);
  wrap.append(row, status);
  validate();
  return wrap;
}

async function saveSetting(key, value, row) {
  try {
    const resp = await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ [key]: value }),
    });
    if (!resp.ok) throw new Error();
    if (row) {
      row.classList.add("saved");
      setTimeout(() => row.classList.remove("saved"), 700);
    }
  } catch (e) {
    // revert the toggle on failure
    const cb = row && row.querySelector("input");
    if (cb) cb.checked = !value;
  }
}

/* ---------- OPS: local objectives, session planning and learned timings ---------- */

function opsBoardStorageKey(commanderId = profileStorageId()) {
  return commanderId ? `opsBoardId:v2:${encodeURIComponent(commanderId)}` : null;
}

function loadOpsBoardId(commanderId) {
  if (!commanderId) return "";
  const key = opsBoardStorageKey(commanderId);
  let value = localStorage.getItem(key);
  if (value == null) {
    value = localStorage.getItem("opsBoardId");
    if (value != null) {
      localStorage.setItem(key, value);
      localStorage.removeItem("opsBoardId");
    }
  }
  return value || "";
}

function saveOpsBoardId(value) {
  const key = opsBoardStorageKey();
  if (!key) return;
  if (value) localStorage.setItem(key, value);
  else localStorage.removeItem(key);
}

async function opsJson(url, options = {}) {
  const generation = profileGeneration;
  const response = await commanderFetch(url, options);
  const raw = await response.text();
  let data = {};
  if (raw) {
    try { data = JSON.parse(raw); }
    catch (error) { data = { error: raw.slice(0, 300) }; }
  }
  if (!response.ok) throw new Error(data.error || data.message || `Local OPS request failed (${response.status}).`);
  if (generation !== profileGeneration) throw new Error("Commander profile changed while the OPS request was running.");
  return data;
}

function opsEpochLabel(value) {
  if (value == null || value === "") return "";
  const numeric = Number(value);
  const date = Number.isFinite(numeric)
    ? new Date(numeric > 10_000_000_000 ? numeric : numeric * 1000)
    : new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

function opsDateInput(value) {
  if (!value) return "";
  const date = new Date(Number(value) * 1000);
  if (Number.isNaN(date.getTime())) return "";
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

function opsActivityName(value) {
  return String(value || "other").replaceAll("_", " ").replace(/\b\w/g, (char) => char.toUpperCase());
}

function opsTimingProvenance(activity, plannedSeconds) {
  const estimate = opsState.timings?.activities?.[activity];
  const planned = plannedSeconds ? `Plan estimate ${fmtDuration(plannedSeconds)}.` : "";
  if (!estimate) return `${planned} No timing provenance is available for this activity.`.trim();
  if (estimate.source === "personal_median") {
    const context = estimate.context ? ` for ${estimate.context}` : "";
    const margin = estimate.conservative_margin
      ? `; +${Math.round(estimate.conservative_margin * 100)}% planning margin` : "";
    return `${planned} Personal median${context}: ${fmtDuration(estimate.median_seconds)} from ` +
      `${estimate.sample_count} local journal sample${estimate.sample_count === 1 ? "" : "s"}${margin}.`;
  }
  const partial = estimate.sample_count
    ? ` (${estimate.sample_count} sample${estimate.sample_count === 1 ? "" : "s"}; 3 required for a personal median)` : "";
  return `${planned} Conservative built-in default: ${fmtDuration(estimate.seconds)}${partial}.`;
}

function renderOpsTimings() {
  const timings = opsState.timings || {};
  const activities = Object.entries(timings.activities || {});
  const personal = activities.filter(([, value]) => value.source === "personal_median");
  $("ops-timing-summary").textContent = activities.length
    ? `${personal.length} PERSONAL · ${activities.length - personal.length} DEFAULT`
    : "NO TIMING DATA";
  if (!activities.length) {
    $("ops-timing-list").innerHTML = '<div class="empty dim">No timing model is available yet.</div>';
    return;
  }
  const pending = (timings.pending || []).length
    ? `<div class="dim empty">Currently learning ${(timings.pending || []).map((row) => esc(opsActivityName(row.activity))).join(", ")}.</div>` : "";
  $("ops-timing-list").innerHTML = pending + '<div class="ops-timing-table">' + activities
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([activity, value]) => {
      const source = value.source === "personal_median"
        ? `${value.sample_count} samples · median ${fmtDuration(value.median_seconds)} · planned ${fmtDuration(value.seconds)}`
        : `conservative default · ${fmtDuration(value.seconds)}${value.sample_count ? ` · ${value.sample_count}/3 samples` : ""}`;
      return `<div class="ops-timing-row"><b>${esc(opsActivityName(activity))}</b><span>${esc(source)}</span></div>`;
    }).join("") + "</div>";
}

async function loadOpsTimings() {
  const generation = profileGeneration;
  try {
    const data = await opsJson("/api/timings", { cache: "no-store" });
    if (generation !== profileGeneration) return;
    opsState.timings = data.timings || data;
    renderOpsTimings();
    if (opsState.plan) renderOpsPlan(opsState.plan);
  } catch (error) {
    if (generation !== profileGeneration) return;
    $("ops-timing-summary").textContent = "UNAVAILABLE";
    $("ops-timing-list").innerHTML = `<div class="empty warn">${esc(error.message)}</div>`;
  }
}

function opsAlternativeReason(task, plan, selectedIds, nodeById) {
  const dependencies = (task.depends_on || []).map((id) => nodeById.get(id)).filter(Boolean);
  const missing = dependencies.filter((item) => !selectedIds.has(item.id));
  if (missing.length) return `Its required bundle also includes ${missing.map((item) => item.title).join(", ")}.`;
  if ((task.estimated_minutes || Math.ceil((task.estimated_seconds || 0) / 60)) > plan.remaining_minutes) {
    return `Needs about ${task.estimated_minutes || Math.ceil(task.estimated_seconds / 60)} minutes; ` +
      `${plan.remaining_minutes} remain after selected work.`;
  }
  return "Ranked behind selected work by priority, reward per minute and duration, or its dependency bundle did not fit.";
}

function opsTaskMarkup(task, index, selected, plan, nodeById) {
  const destination = task.plot || {};
  const place = [destination.system, destination.station || destination.body].filter(Boolean).join(" · ");
  const facts = [
    `<span class="ops-fact">${esc(opsActivityName(task.activity))}</span>`,
    `<span class="ops-fact">${esc(fmtDuration(task.estimated_seconds || 0))}</span>`,
    `<span class="ops-fact">PRIORITY ${Number(task.priority || 0)}</span>`,
  ];
  if (task.reward) facts.push(`<span class="ops-fact reward">${esc(fmtCr(task.reward))}</span>`);
  if (place) facts.push(`<span class="ops-fact">⌖ ${esc(place)}</span>`);
  if (task.deadline) facts.push(`<span class="ops-fact urgent">DUE ${esc(opsEpochLabel(task.deadline))}</span>`);
  if (task.risk) facts.push(`<span class="ops-fact urgent">RISK ${esc(String(task.risk).toUpperCase())}</span>`);
  const dependencies = (task.depends_on || []).map((id) => nodeById.get(id)).filter(Boolean);
  const requiredBy = selected
    ? (plan.selected || []).filter((candidate) => (candidate.depends_on || []).includes(task.id)) : [];
  let decision;
  if (requiredBy.length) {
    decision = `Included first because ${requiredBy.map((item) => item.title).join(", ")} depends on it.`;
  } else if (selected) {
    decision = "Selected by priority, reward per minute and duration; its dependency bundle fits this budget.";
  } else {
    const selectedIds = new Set((plan.selected || []).map((item) => item.id));
    decision = opsAlternativeReason(task, plan, selectedIds, nodeById);
  }
  const dependencyLine = dependencies.length
    ? `<div class="ops-dependencies"><b>Requires:</b> ${dependencies.map((item) => esc(item.title)).join(" → ")}</div>` : "";
  const plot = destination.system
    ? `<button class="copy ops-task-action" type="button" data-ops-plot="${esc(destination.system)}">PLOT ${esc(destination.system)}</button>` : "";
  return `<article class="ops-task${selected ? "" : " alternative"}">` +
    `<div class="ops-task-number">${selected ? index + 1 : `A${index + 1}`}</div>` +
    `<div><div class="ops-task-title">${esc(task.title || "Untitled task")}</div>` +
    `<div class="ops-task-why">${esc(task.why || "Known local objective")} · ${esc(decision)}</div>` +
    `<div class="ops-task-facts">${facts.join("")}</div>${dependencyLine}` +
    `<div class="ops-provenance"><b>Timing:</b> ${esc(opsTimingProvenance(task.activity, task.estimated_seconds))}</div></div>${plot}</article>`;
}

function renderOpsPlan(plan) {
  opsState.plan = plan;
  const graphNodes = plan.graph?.nodes || [...(plan.selected || []), ...(plan.alternatives || [])];
  const nodeById = new Map(graphNodes.map((task) => [task.id, task]));
  $("ops-plan-meta").textContent = `${plan.planned_minutes || 0} / ${plan.budget_minutes || 0} MIN`;
  $("ops-plan-status").textContent = (plan.selected || []).length
    ? `${plan.selected.length} task${plan.selected.length === 1 ? "" : "s"} selected · ` +
      `${plan.remaining_minutes || 0} minutes deliberately left uncommitted · generated ${new Date(plan.generated_at || Date.now()).toLocaleTimeString()}`
    : "No known work fit this budget. Review the warnings and alternatives below.";
  const warnings = plan.warnings || [];
  $("ops-plan-warnings").classList.toggle("hidden", !warnings.length);
  $("ops-plan-warnings").innerHTML = warnings.map((warning) => `<div>▲ ${esc(warning)}</div>`).join("");
  $("ops-plan-selected").innerHTML = (plan.selected || []).length
    ? (plan.selected || []).map((task, index) => opsTaskMarkup(task, index, true, plan, nodeById)).join("")
    : '<div class="empty dim">No selected tasks.</div>';
  const alternatives = plan.alternatives || [];
  const visibleAlternatives = alternatives.slice(0, 50);
  $("ops-alternatives-wrap").classList.toggle("hidden", !alternatives.length);
  $("ops-alternative-count").textContent = alternatives.length
    ? `${alternatives.length} NOT SELECTED${alternatives.length > 50 ? " · SHOWING 50" : ""}` : "";
  $("ops-plan-alternatives").innerHTML = visibleAlternatives
    .map((task, index) => opsTaskMarkup(task, index, false, plan, nodeById)).join("");
}

async function buildOpsPlan(event) {
  event.preventDefault();
  const button = $("ops-plan-go");
  button.disabled = true;
  button.textContent = "PLANNING…";
  $("ops-plan-status").textContent = "Evaluating current journal state, saved objectives and dependency bundles…";
  try {
    const data = await opsJson("/api/objectives/plan", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        minutes: Number($("ops-budget").value),
        time_budget_minutes: Number($("ops-budget").value),
        max_tasks: Number($("ops-max-tasks").value),
      }),
    });
    renderOpsPlan(data.plan || data);
  } catch (error) {
    $("ops-plan-status").innerHTML = `<span class="warn">${esc(error.message)}</span>`;
  } finally {
    button.disabled = false;
    button.textContent = "BUILD SESSION PLAN";
  }
}

function opsObjectiveQuery() {
  const filter = $("ops-objective-filter")?.value || "current";
  if (filter === "done") return "done,dismissed";
  if (filter === "all") return "open,active,blocked,done,dismissed";
  return "open,active,blocked";
}

function renderOpsObjectives() {
  const objectives = opsState.objectives || [];
  $("ops-objective-count").textContent = String(objectives.length);
  $("ops-objective-statusline").textContent = objectives.length
    ? "Status changes are saved immediately. EDIT exposes every stored planning field."
    : "No objectives match this view.";
  const statuses = ["open", "active", "blocked", "done", "dismissed"];
  $("ops-objective-list").innerHTML = objectives.map((objective) => {
    const facts = [opsActivityName(objective.category)];
    if (objective.estimated_seconds) facts.push(fmtDuration(objective.estimated_seconds));
    if (objective.system) facts.push(objective.system +
      (objective.station || objective.body ? ` · ${objective.station || objective.body}` : ""));
    if (objective.deadline) facts.push(`due ${opsEpochLabel(objective.deadline)}`);
    if (objective.reward) facts.push(fmtCr(objective.reward));
    const options = statuses.map((value) =>
      `<option value="${value}"${objective.status === value ? " selected" : ""}>${value.toUpperCase()}</option>`).join("");
    return `<article class="ops-record ${esc(objective.status)}" data-objective-id="${esc(objective.id)}">` +
      `<div><div class="ops-record-title">${esc(objective.title)}</div>` +
      `<div class="ops-record-meta"><span>PRIORITY ${Number(objective.priority || 0)}</span>` +
      facts.map((fact) => `<span>${esc(fact)}</span>`).join("") + `</div></div>` +
      `<div class="ops-record-controls"><select data-objective-status="${esc(objective.id)}" aria-label="Status for ${esc(objective.title)}">${options}</select>` +
      `<button class="copy" type="button" data-objective-edit="${esc(objective.id)}">EDIT</button>` +
      `<button class="copy danger" type="button" data-objective-delete="${esc(objective.id)}">DELETE</button></div></article>`;
  }).join("");
}

async function loadOpsObjectives() {
  const generation = profileGeneration;
  try {
    const statuses = encodeURIComponent(opsObjectiveQuery());
    const data = await opsJson(`/api/objectives?statuses=${statuses}`, { cache: "no-store" });
    if (generation !== profileGeneration) return;
    opsState.objectives = data.objectives || (Array.isArray(data) ? data : []);
    renderOpsObjectives();
  } catch (error) {
    if (generation !== profileGeneration) return;
    $("ops-objective-statusline").innerHTML = `<span class="warn">${esc(error.message)}</span>`;
  }
}

function resetOpsObjectiveForm() {
  $("ops-objective-form").reset();
  $("ops-objective-id").value = "";
  $("ops-objective-priority").value = "50";
  $("ops-objective-status").value = "open";
  $("ops-objective-save").textContent = "ADD OBJECTIVE";
  $("ops-objective-cancel").classList.add("hidden");
}

function editOpsObjective(objectiveId) {
  const objective = opsState.objectives.find((item) => item.id === objectiveId);
  if (!objective) return;
  $("ops-objective-id").value = objective.id;
  $("ops-objective-title").value = objective.title || "";
  $("ops-objective-category").value = objective.category || "other";
  $("ops-objective-priority").value = objective.priority ?? 50;
  $("ops-objective-minutes").value = objective.estimated_seconds
    ? Math.max(1, Math.round(objective.estimated_seconds / 60)) : "";
  $("ops-objective-system").value = objective.system || "";
  $("ops-objective-station").value = objective.station || "";
  $("ops-objective-body").value = objective.body || "";
  $("ops-objective-deadline").value = opsDateInput(objective.deadline);
  $("ops-objective-status").value = objective.status || "open";
  $("ops-objective-save").textContent = "SAVE CHANGES";
  $("ops-objective-cancel").classList.remove("hidden");
  $("ops-objective-title").focus();
}

async function saveOpsObjective(event) {
  event.preventDefault();
  const objectiveId = $("ops-objective-id").value;
  const minutes = Number($("ops-objective-minutes").value);
  const deadlineValue = $("ops-objective-deadline").value;
  const payload = {
    title: $("ops-objective-title").value.trim(),
    category: $("ops-objective-category").value,
    priority: Number($("ops-objective-priority").value),
    estimated_seconds: minutes > 0 ? Math.round(minutes * 60) : null,
    system: $("ops-objective-system").value.trim() || null,
    station: $("ops-objective-station").value.trim() || null,
    body: $("ops-objective-body").value.trim() || null,
    deadline: deadlineValue ? new Date(deadlineValue).toISOString() : null,
    status: $("ops-objective-status").value,
  };
  const button = $("ops-objective-save");
  button.disabled = true;
  try {
    const saved = await opsJson(objectiveId ? `/api/objectives/${encodeURIComponent(objectiveId)}` : "/api/objectives", {
      method: objectiveId ? "PATCH" : "POST",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
    const createdId = saved.objective?.id || saved.id;
    if (!objectiveId && createdId && payload.status !== "open") {
      await opsJson(`/api/objectives/${encodeURIComponent(createdId)}`, {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: payload.status }),
      });
    }
    resetOpsObjectiveForm();
    await loadOpsObjectives();
    $("ops-plan-status").textContent = "Objectives changed. Build a new session plan when ready.";
  } catch (error) {
    $("ops-objective-statusline").innerHTML = `<span class="warn">${esc(error.message)}</span>`;
  } finally {
    button.disabled = false;
  }
}

async function patchOpsObjective(objectiveId, changes) {
  try {
    await opsJson(`/api/objectives/${encodeURIComponent(objectiveId)}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(changes),
    });
    await loadOpsObjectives();
  } catch (error) {
    $("ops-objective-statusline").innerHTML = `<span class="warn">${esc(error.message)}</span>`;
    await loadOpsObjectives();
  }
}

async function deleteOpsObjective(objectiveId) {
  const objective = opsState.objectives.find((item) => item.id === objectiveId);
  if (!confirm(`Delete objective “${objective?.title || objectiveId}”?`)) return;
  try {
    await opsJson(`/api/objectives/${encodeURIComponent(objectiveId)}`, { method: "DELETE" });
    if ($("ops-objective-id").value === objectiveId) resetOpsObjectiveForm();
    await loadOpsObjectives();
  } catch (error) {
    $("ops-objective-statusline").innerHTML = `<span class="warn">${esc(error.message)}</span>`;
  }
}

/* ---------- OPS: account-free operations boards ---------- */

function opsStatusOptions(values, current) {
  return values.map((value) =>
    `<option value="${value}"${value === current ? " selected" : ""}>${value.toUpperCase()}</option>`).join("");
}

function renderOpsBoardSelector() {
  const select = $("ops-board-select");
  select.innerHTML = "";
  if (!opsState.boards.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "NO BOARDS";
    select.appendChild(option);
    select.disabled = true;
    return;
  }
  select.disabled = false;
  for (const board of opsState.boards) {
    const option = document.createElement("option");
    option.value = board.id;
    option.textContent = `${board.title} · ${String(board.status || "active").toUpperCase()}`;
    option.selected = board.id === opsState.activeBoardId;
    select.appendChild(option);
  }
}

function opsObjectiveTitle(objectiveId) {
  if (!objectiveId) return "Whole board";
  return (opsState.snapshot?.objectives || []).find((item) => item.id === objectiveId)?.title || "Unknown objective";
}

function fillOpsBoardObjectiveSelects() {
  const objectives = opsState.snapshot?.objectives || [];
  for (const id of ["ops-assignment-objective", "ops-reservation-objective", "ops-contribution-objective"]) {
    const select = $(id);
    const previous = select.value;
    select.innerHTML = '<option value="">Whole board</option>';
    for (const objective of objectives) {
      const option = document.createElement("option");
      option.value = objective.id;
      option.textContent = objective.title;
      select.appendChild(option);
    }
    if ([...select.options].some((option) => option.value === previous)) select.value = previous;
  }
}

function opsBoardRecordMarkup(record, kind) {
  let title = record.title || "Untitled record";
  const facts = [];
  let statuses = null;
  if (kind === "objectives") {
    statuses = ["open", "active", "blocked", "done"];
    if (record.description) facts.push(record.description);
    if (record.system) facts.push(record.system + (record.station ? ` · ${record.station}` : ""));
    if (record.deadline) facts.push(`due ${opsEpochLabel(record.deadline)}`);
    facts.push(`priority ${record.priority ?? 50}`);
  } else if (kind === "assignments") {
    title = record.assignee || "Unassigned";
    statuses = ["assigned", "active", "done", "released"];
    facts.push(opsObjectiveTitle(record.objective_id));
    if (record.role) facts.push(record.role);
  } else if (kind === "reservations") {
    title = record.resource_key || "Reserved resource";
    statuses = ["reserved", "fulfilled", "released"];
    facts.push(`${Number(record.amount || 0).toLocaleString()}${record.unit ? ` ${record.unit}` : ""}`);
    facts.push(record.resource_type || "resource");
    facts.push(opsObjectiveTitle(record.objective_id));
    if (record.assignee) facts.push(`by ${record.assignee}`);
  } else if (kind === "contributions") {
    title = `${record.contributor || "Commander"} · ${record.kind || "contribution"}`;
    facts.push(`${Number(record.amount || 0).toLocaleString()}${record.unit ? ` ${record.unit}` : ""}`);
    facts.push(opsObjectiveTitle(record.objective_id));
    if (record.note) facts.push(record.note);
  }
  facts.push(`rev ${record.revision || 1}`);
  const statusClass = String(record.status || "").toLowerCase().replace(/[^a-z-]/g, "");
  const selector = statuses
    ? `<select data-op-status data-kind="${kind}" data-id="${esc(record.id)}" aria-label="${esc(kind)} status">` +
      opsStatusOptions(statuses, record.status) + "</select>" : "";
  return `<article class="ops-record ${statusClass}"><div><div class="ops-record-title">${esc(title)}</div>` +
    `<div class="ops-record-meta">${facts.filter(Boolean).map((fact) => `<span>${esc(fact)}</span>`).join("")}</div></div>` +
    `<div class="ops-record-controls">${selector}` +
    `<button class="copy danger" type="button" data-op-delete data-kind="${kind}" data-id="${esc(record.id)}">REMOVE</button>` +
    `</div></article>`;
}

function renderOpsConflicts() {
  const conflicts = opsState.conflicts || [];
  const box = $("ops-conflicts");
  box.classList.toggle("hidden", !conflicts.length);
  if (!conflicts.length) {
    box.innerHTML = "";
    return;
  }
  box.innerHTML = `<b>▲ ${conflicts.length} merge conflict${conflicts.length === 1 ? "" : "s"} recorded</b>` +
    `<div class="dim">The deterministic winner is already active. The losing version remains in local conflict history for review.</div>` +
    conflicts.slice(0, 20).map((conflict) => {
      const table = String(conflict.table_name || "record").replace("operation_", "");
      return `<div class="ops-conflict-row">${esc(table)} · ${esc(conflict.record_id || "unknown")} · ` +
        `${esc(opsEpochLabel(conflict.detected_at) || conflict.detected_at || "time unknown")} · ` +
        `local ${esc(String(conflict.local_version || "?").slice(0, 28))} / incoming ` +
        `${esc(String(conflict.incoming_version || "?").slice(0, 28))}</div>`;
    }).join("");
}

function renderOperationsBoard() {
  renderOpsBoardSelector();
  const snapshot = opsState.snapshot;
  const board = snapshot?.board;
  $("ops-board-empty").textContent = "Create a board here or import one shared by another commander.";
  $("ops-board-empty").classList.toggle("hidden", !!board);
  $("ops-board-workspace").classList.toggle("hidden", !board);
  $("ops-board-export").disabled = !board;
  if (!board) return;
  $("ops-board-name").textContent = board.title || "Untitled board";
  $("ops-board-briefing").textContent = board.description || "No briefing supplied.";
  $("ops-board-meta").textContent = `REVISION ${board.revision || 1} · ` +
    `updated ${opsEpochLabel(board.updated_at) || board.updated_at || "unknown"} · ` +
    `node ${String(board.updated_by || "local").slice(0, 24)}`;
  const boardStatus = $("ops-board-status");
  if (![...boardStatus.options].some((option) => option.value === board.status)) {
    const option = document.createElement("option");
    option.value = board.status;
    option.textContent = String(board.status || "active").toUpperCase();
    boardStatus.appendChild(option);
  }
  boardStatus.value = board.status || "active";
  fillOpsBoardObjectiveSelects();
  $("ops-board-objectives").innerHTML = (snapshot.objectives || []).length
    ? snapshot.objectives.map((record) => opsBoardRecordMarkup(record, "objectives")).join("")
    : '<div class="empty dim">No board objectives yet.</div>';
  $("ops-assignments").innerHTML = (snapshot.assignments || []).length
    ? snapshot.assignments.map((record) => opsBoardRecordMarkup(record, "assignments")).join("")
    : '<div class="empty dim">No assignments yet.</div>';
  $("ops-reservations").innerHTML = (snapshot.reservations || []).length
    ? snapshot.reservations.map((record) => opsBoardRecordMarkup(record, "reservations")).join("")
    : '<div class="empty dim">No resource reservations yet.</div>';
  $("ops-contributions").innerHTML = (snapshot.contributions || []).length
    ? snapshot.contributions.map((record) => opsBoardRecordMarkup(record, "contributions")).join("")
    : '<div class="empty dim">No contributions logged yet.</div>';
  renderOpsConflicts();
}

async function loadOperations() {
  const generation = profileGeneration;
  try {
    const listData = await opsJson("/api/operations", { cache: "no-store" });
    if (generation !== profileGeneration) return;
    opsState.boards = listData.boards || (Array.isArray(listData) ? listData : []);
    const selectedExists = opsState.boards.some((board) => board.id === opsState.activeBoardId);
    if (!selectedExists) {
      opsState.activeBoardId = opsState.boards[0]?.id || "";
      saveOpsBoardId(opsState.activeBoardId);
    }
    let detailData = null;
    if (opsState.activeBoardId) {
      detailData = await opsJson(`/api/operations?board_id=${encodeURIComponent(opsState.activeBoardId)}`, { cache: "no-store" });
      if (generation !== profileGeneration) return;
    }
    opsState.snapshot = detailData?.snapshot || (detailData?.board ? detailData : null);
    opsState.conflicts = detailData?.conflicts || listData.conflicts || [];
    renderOperationsBoard();
  } catch (error) {
    if (generation !== profileGeneration) return;
    $("ops-board-empty").classList.remove("hidden");
    $("ops-board-empty").innerHTML = `<span class="warn">${esc(error.message)}</span>`;
    $("ops-board-workspace").classList.add("hidden");
  }
}

async function postOperation(kind, payload) {
  const actions = {
    boards: "create_board", objectives: "add_objective", assignments: "assign",
    reservations: "reserve", contributions: "contribute",
  };
  const data = await opsJson("/api/operations", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action: actions[kind], ...payload }),
  });
  return data.record || data.board || data.objective || data.assignment || data.reservation || data.contribution || data;
}

async function createOperationsBoard(event) {
  event.preventDefault();
  try {
    const board = await postOperation("boards", {
      title: $("ops-board-title").value.trim(),
      description: $("ops-board-description").value.trim(),
    });
    $("ops-board-form").reset();
    $("ops-new-board-wrap").open = false;
    opsState.activeBoardId = board.id || "";
    saveOpsBoardId(opsState.activeBoardId);
    await loadOperations();
  } catch (error) {
    $("ops-import-report").innerHTML = `<span class="warn">${esc(error.message)}</span>`;
  }
}

async function addOperationsObjective(event) {
  event.preventDefault();
  try {
    const deadline = $("ops-board-objective-deadline").value;
    await postOperation("objectives", {
      board_id: opsState.activeBoardId,
      title: $("ops-board-objective-title").value.trim(),
      description: $("ops-board-objective-description").value.trim(),
      system: $("ops-board-objective-system").value.trim() || null,
      station: $("ops-board-objective-station").value.trim() || null,
      deadline: deadline ? Math.floor(new Date(deadline).getTime() / 1000) : null,
      priority: Number($("ops-board-objective-priority").value),
    });
    $("ops-board-objective-form").reset();
    $("ops-board-objective-priority").value = "50";
    await loadOperations();
  } catch (error) { $("ops-import-report").innerHTML = `<span class="warn">${esc(error.message)}</span>`; }
}

async function addOperationsAssignment(event) {
  event.preventDefault();
  try {
    await postOperation("assignments", {
      board_id: opsState.activeBoardId,
      objective_id: $("ops-assignment-objective").value || null,
      assignee: $("ops-assignment-name").value.trim(), role: $("ops-assignment-role").value.trim(),
    });
    $("ops-assignment-form").reset();
    await loadOperations();
  } catch (error) { $("ops-import-report").innerHTML = `<span class="warn">${esc(error.message)}</span>`; }
}

async function addOperationsReservation(event) {
  event.preventDefault();
  try {
    await postOperation("reservations", {
      board_id: opsState.activeBoardId,
      objective_id: $("ops-reservation-objective").value || null,
      resource_type: $("ops-reservation-type").value,
      resource_key: $("ops-reservation-key").value.trim(),
      amount: Number($("ops-reservation-amount").value),
      unit: $("ops-reservation-unit").value.trim(),
      assignee: $("ops-reservation-assignee").value.trim() || null,
    });
    $("ops-reservation-form").reset();
    await loadOperations();
  } catch (error) { $("ops-import-report").innerHTML = `<span class="warn">${esc(error.message)}</span>`; }
}

async function addOperationsContribution(event) {
  event.preventDefault();
  try {
    await postOperation("contributions", {
      board_id: opsState.activeBoardId,
      objective_id: $("ops-contribution-objective").value || null,
      contributor: $("ops-contribution-name").value.trim(),
      kind: $("ops-contribution-kind").value.trim(),
      amount: Number($("ops-contribution-amount").value),
      unit: $("ops-contribution-unit").value.trim(),
      note: $("ops-contribution-note").value.trim(),
    });
    const commander = $("ops-contribution-name").value;
    $("ops-contribution-form").reset();
    $("ops-contribution-name").value = commander;
    await loadOperations();
  } catch (error) { $("ops-import-report").innerHTML = `<span class="warn">${esc(error.message)}</span>`; }
}

async function patchOperation(kind, recordId, changes) {
  try {
    await opsJson(`/api/operations/${encodeURIComponent(kind)}/${encodeURIComponent(recordId)}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(changes),
    });
    await loadOperations();
  } catch (error) {
    $("ops-import-report").innerHTML = `<span class="warn">${esc(error.message)}</span>`;
    await loadOperations();
  }
}

async function deleteOperation(kind, recordId) {
  const noun = kind === "boards" ? "operations board and its visible workspace" : kind.slice(0, -1);
  if (!confirm(`Remove this ${noun}? The tombstone is retained for deterministic board merging.`)) return;
  try {
    await opsJson(`/api/operations/${encodeURIComponent(kind)}/${encodeURIComponent(recordId)}`, { method: "DELETE" });
    if (kind === "boards") {
      opsState.activeBoardId = "";
      saveOpsBoardId("");
    }
    await loadOperations();
  } catch (error) {
    $("ops-import-report").innerHTML = `<span class="warn">${esc(error.message)}</span>`;
  }
}

async function exportOperationsBoard() {
  if (!opsState.activeBoardId) return;
  const button = $("ops-board-export");
  button.disabled = true;
  try {
    const response = await fetch(`/api/operations/export?board_id=${encodeURIComponent(opsState.activeBoardId)}`, { cache: "no-store" });
    if (!response.ok) throw new Error("Operations export could not be created.");
    const blob = await response.blob();
    const disposition = response.headers.get("Content-Disposition") || "";
    const match = disposition.match(/filename\*?=(?:UTF-8''|\")?([^\";]+)/i);
    const boardName = opsState.snapshot?.board?.title || "operation";
    const filename = decodeURIComponent(match?.[1] || `frameshift-${boardName.replace(/[^A-Za-z0-9_-]+/g, "-")}.json`);
    const href = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = href;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(href), 1000);
  } catch (error) {
    $("ops-import-report").innerHTML = `<span class="warn">${esc(error.message)}</span>`;
  } finally {
    button.disabled = false;
  }
}

async function importOperationsBoard(event) {
  const input = event.currentTarget;
  const file = input.files?.[0];
  if (!file) return;
  try {
    if (file.size > 20 * 1024 * 1024) throw new Error("Operations imports are limited to 20 MB.");
    const documentValue = JSON.parse(await file.text());
    if (documentValue.format !== "frameshift.operations" || Number(documentValue.version) !== 1) {
      throw new Error("This is not a supported Frameshift operations export.");
    }
    const data = await opsJson("/api/operations/import", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(documentValue),
    });
    const report = data.report || data;
    const firstBoard = documentValue.records?.boards?.[0]?.id;
    if (firstBoard) {
      opsState.activeBoardId = firstBoard;
      saveOpsBoardId(firstBoard);
    }
    $("ops-import-report").innerHTML = `<span class="good">Import complete: ` +
      `${Number(report.inserted || 0)} inserted, ${Number(report.updated || 0)} updated, ` +
      `${Number(report.kept_local || 0)} kept local, ${Number(report.conflicts || 0)} conflicts.</span>`;
    await loadOperations();
  } catch (error) {
    $("ops-import-report").innerHTML = `<span class="warn">${esc(error.message)}</span>`;
  } finally {
    input.value = "";
  }
}

async function loadOpsWorkspace() {
  if (opsWorkspaceLoading) return opsWorkspaceLoading;
  if (state?.commander) {
    if (!$("ops-assignment-name").value) $("ops-assignment-name").value = state.commander;
    if (!$("ops-contribution-name").value) $("ops-contribution-name").value = state.commander;
  }
  const loading = Promise.all([loadOpsObjectives(), loadOpsTimings(), loadOperations()]);
  opsWorkspaceLoading = loading;
  loading.finally(() => {
    if (opsWorkspaceLoading === loading) opsWorkspaceLoading = null;
  });
  return loading;
}

/* ---------- local specialist workflows ---------- */

const SPECIALIST_NAMES = ["mining", "combat", "carrier", "exobiology"];

function setSpecialistWorkflow(name) {
  if (!SPECIALIST_NAMES.includes(name)) name = "mining";
  localStorage.setItem("specialistWorkflow", name);
  document.querySelectorAll(".sp-switcher [data-specialist]").forEach((button) => {
    const active = button.dataset.specialist === name;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
    button.tabIndex = active ? 0 : -1;
  });
  document.querySelectorAll(".sp-workflow").forEach((panel) => {
    panel.classList.toggle("hidden", panel.id !== `sp-workflow-${name}`);
  });
}

function specialistVisible() {
  const pane = $("tab-specialists");
  return !!pane && !pane.classList.contains("hidden") && !document.hidden;
}

function specialistError(error, fallback = "The specialist service is unavailable.") {
  if (error instanceof Error && error.message) return error.message;
  if (typeof error === "string" && error.trim()) return error.trim();
  if (error && typeof error.message === "string" && error.message.trim()) return error.message.trim();
  return fallback;
}

async function specialistJson(url, options = {}) {
  const generation = profileGeneration;
  const request = { cache: "no-store", ...options };
  const isFormData = typeof FormData !== "undefined" && request.body instanceof FormData;
  if (request.body != null && !isFormData) {
    request.headers = { "Content-Type": "application/json", ...(request.headers || {}) };
    if (typeof request.body !== "string") request.body = JSON.stringify(request.body);
  }
  const response = await commanderFetch(url, request);
  const raw = await response.text();
  let data = {};
  try { data = raw ? JSON.parse(raw) : {}; } catch { data = { error: raw }; }
  if (!response.ok) {
    throw new Error(data.error || data.message || `Request failed (${response.status})`);
  }
  if (generation !== profileGeneration) {
    throw new Error("Commander profile changed while the specialist request was running.");
  }
  return data;
}

function normaliseSpecialistSnapshot(data) {
  if (!data || typeof data !== "object") return {};
  const snapshot = data.snapshot || data.specialists || data;
  if (snapshot !== data && (data.history || data.histories)) {
    return { ...snapshot, history: data.history || data.histories };
  }
  return snapshot;
}

function specialistWorkflow(name) {
  return specialistState?.[name] || {};
}

function specialistHistory(name) {
  const workflow = specialistWorkflow(name);
  const history = workflow.history || specialistState?.history?.[name]
    || specialistState?.histories?.[name] || specialistState?.[`${name}_history`] || [];
  return Array.isArray(history) ? history : [];
}

async function loadSpecialists(silent = false) {
  if (specialistLoading) return specialistLoading;
  const generation = profileGeneration;
  const expectedCommander = profileStorageId();
  if (!expectedCommander) return null;
  const status = $("sp-global-status");
  if (!silent && status) status.textContent = "Loading local specialist records…";
  const loading = specialistJson("/api/specialists")
    .then((data) => {
      if (generation !== profileGeneration || profileStorageId() !== expectedCommander
          || (data.commander_id && data.commander_id !== expectedCommander)) return null;
      specialistState = normaliseSpecialistSnapshot(data);
      specialistLastFetch = Date.now();
      renderSpecialists();
      if (status) {
        status.textContent = "Journal and explicit-input records are stored locally per commander.";
        status.classList.remove("error");
      }
      return specialistState;
    })
    .catch((error) => {
      if (generation !== profileGeneration) return null;
      if (status) {
        status.textContent = `Specialist records unavailable: ${specialistError(error)}`;
        status.classList.add("error");
      }
      return null;
    });
  specialistLoading = loading;
  loading.finally(() => {
    if (specialistLoading === loading) specialistLoading = null;
  });
  return loading;
}

function specialistDuration(session, active) {
  if (!session) return null;
  if (active && session.started_ts != null) {
    const raw = Number(session.started_ts);
    const startedMs = raw < 10_000_000_000 ? raw * 1000 : raw;
    if (Number.isFinite(startedMs)) return Math.max(0, (Date.now() - startedMs) / 1000);
  }
  return session.duration_s == null ? null : Number(session.duration_s);
}

function specialistTimestamp(value) {
  if (value == null || value === "") return "Unknown time";
  const raw = typeof value === "number" || /^\d+(?:\.\d+)?$/.test(String(value)) ? Number(value) : value;
  const millis = typeof raw === "number" && raw < 10_000_000_000 ? raw * 1000 : raw;
  const date = new Date(millis);
  return Number.isNaN(date.getTime()) ? "Unknown time" : date.toLocaleString();
}

function specialistAgo(value) {
  if (value == null || value === "") return "unknown time";
  const raw = Number(value);
  if (!Number.isFinite(raw)) return specialistTimestamp(value);
  const millis = raw < 10_000_000_000 ? raw * 1000 : raw;
  const elapsed = Math.max(0, Date.now() - millis);
  const minutes = Math.floor(elapsed / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return days < 30 ? `${days}d ago` : specialistTimestamp(value);
}

function specialistNumber(value, suffix = "") {
  if (value == null || value === "") return "—";
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "—";
  return `${numeric.toLocaleString(undefined, { maximumFractionDigits: 2 })}${suffix}`;
}

function specialistHumanName(value) {
  return String(value || "unknown")
    .replace(/^hpt_|^int_|^ext_/, "")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function renderSpecialistFacts(id, facts) {
  const target = $(id);
  if (!target) return;
  target.innerHTML = facts.map(([label, value, note]) =>
    `<div class="sp-fact"><span>${esc(label)}</span><b>${esc(value)}</b>` +
    `${note ? `<small>${esc(note)}</small>` : ""}</div>`
  ).join("");
}

function renderSpecialistHistory(id, history, formatter) {
  const target = $(id);
  if (!target) return;
  if (!history.length) {
    target.innerHTML = '<div class="dim empty">No completed sessions recorded for this commander yet.</div>';
    return;
  }
  target.innerHTML = history.slice(0, 8).map((item) => {
    const detail = formatter(item);
    const timestamp = item.ended_ts || item.started_ts;
    return `<div class="sp-history-row"><div><b>${esc(detail.title)}</b><span>${esc(detail.subtitle)}</span></div>` +
      `<time title="${esc(specialistTimestamp(timestamp))}">${esc(specialistAgo(timestamp))}</time></div>`;
  }).join("");
}

function renderMiningSpecialist() {
  const mining = specialistWorkflow("mining");
  const session = mining.session;
  const active = !!mining.active;
  const badge = $("sp-mining-state");
  badge.textContent = active ? "RUN ACTIVE" : session ? "LAST RUN" : "IDLE";
  badge.className = `sp-state ${active ? "active" : "idle"}`;
  $("sp-mining-start").disabled = active;
  $("sp-mining-end").disabled = !active;
  $("sp-mining-message").textContent = active
    ? [session?.system, session?.body || session?.ring].filter(Boolean).join(" · ") || "Mining activity is being recorded from the journal."
    : session?.end_reason ? `Last run ended: ${specialistHumanName(session.end_reason)}.`
      : "A run also starts automatically when the journal reports mining activity.";

  setText("sp-mining-duration", fmtDuration(specialistDuration(session, active)));
  setText("sp-mining-refined", session ? specialistNumber(session.refined_t, " t") : "—");
  setText("sp-mining-rate", session?.tons_per_hour == null ? "—" : `${specialistNumber(session.tons_per_hour)} t/hr`);
  setText("sp-mining-prospected", session ? specialistNumber(session.asteroids_prospected || 0) : "—");
  setText("sp-mining-cracked", session ? specialistNumber(session.asteroids_cracked || 0) : "—");
  setText("sp-mining-revenue", session ? fmtCr(session.attributed_revenue_cr || 0) : "—");

  const yields = session?.cargo_yield || session?.refined || [];
  $("sp-mining-yield").innerHTML = yields.map((row) =>
    `<tr><td>${esc(row.name || row.symbol)}</td><td class="num">${specialistNumber(row.count, " t")}</td>` +
    `<td class="num">${row.cargo_delta == null ? "—" : specialistNumber(row.cargo_delta, " t")}</td>` +
    `<td class="num">${row.sold_t == null ? "—" : specialistNumber(row.sold_t, " t")}</td></tr>`
  ).join("");
  $("sp-mining-yield-empty").classList.toggle("hidden", yields.length > 0);

  const targets = session?.prospected_materials || [];
  $("sp-mining-targets").innerHTML = targets.map((row) =>
    `<tr><td>${esc(row.name || row.symbol)}</td><td class="num">${specialistNumber(row.sightings || 0)}</td>` +
    `<td class="num">${specialistNumber(row.best_pct, "%")}</td><td class="num">${specialistNumber(row.average_pct, "%")}</td></tr>`
  ).join("");
  $("sp-mining-targets-empty").classList.toggle("hidden", targets.length > 0);

  const limpets = session?.limpets || {};
  renderSpecialistFacts("sp-mining-limpets", [
    ["Prospectors used", session ? specialistNumber(limpets.prospectors_used || 0) : "—", "journal launches / prospected rocks"],
    ["Collectors launched", session ? specialistNumber(limpets.collectors_launched || 0) : "—", "journal launches"],
    ["Estimated used", session ? specialistNumber(limpets.estimated_used || 0) : "—", limpets.inventory_accounting == null ? "launch events only" : "inventory cross-check"],
    ["Remaining", limpets.remaining == null ? "—" : specialistNumber(limpets.remaining), "latest Cargo snapshot"],
    ["Cost / tonne", limpets.cost_per_tonne_cr == null ? "—" : fmtCr(limpets.cost_per_tonne_cr), limpets.cost_source || "purchase price not observed"],
    ["Net after limpet cash", session ? fmtCr(session.net_after_limpet_cash_cr || 0) : "—", "attributed sales − buys + returns"],
  ]);

  renderSpecialistHistory("sp-mining-history", specialistHistory("mining"), (item) => ({
    title: `${specialistNumber(item.refined_t || 0, " t")} refined · ${item.tons_per_hour == null ? "rate unavailable" : `${specialistNumber(item.tons_per_hour)} t/hr`}`,
    subtitle: `${item.asteroids_prospected || 0} rocks · ${fmtCr(item.attributed_revenue_cr || 0)} attributed revenue · ${fmtDuration(item.duration_s)}`,
  }));
}

function renderCombatSpecialist() {
  const combat = specialistWorkflow("combat");
  const readiness = combat.readiness || {};
  const levelNames = {
    not_ax_equipped: "NO AX WEAPONS OBSERVED",
    limited: "LIMITED AX TOOLING",
    scout_or_support_ready: "SCOUT / SUPPORT TOOLING PRESENT",
    interceptor_tooling_present: "INTERCEPTOR TOOLING PRESENT",
  };
  $("sp-combat-level").textContent = levelNames[readiness.level] || "NO LOADOUT OBSERVED";
  const score = Math.max(0, Math.min(100, Number(readiness.score) || 0));
  $("sp-combat-score").style.setProperty("--score", `${score * 3.6}deg`);
  $("sp-combat-score").querySelector("b").textContent = String(score);

  const checklistLabels = {
    ax_weapons: "AX weapons", heat_sinks: "Heat sinks", xeno_scanners: "Xeno scanner",
    flak: "Remote-release flak", shutdown_neutralisers: "Shutdown neutraliser",
    caustic_sinks: "Caustic sinks", repair_or_decon: "Repair / decon limpets",
    hull_reinforcement: "Hull reinforcement", module_reinforcement: "Module reinforcement",
  };
  $("sp-combat-checklist").innerHTML = Object.entries(checklistLabels).map(([key, label]) => {
    const present = !!readiness.checklist?.[key];
    return `<span class="${present ? "present" : "missing"}"><i>${present ? "✓" : "—"}</i>${esc(label)}</span>`;
  }).join("");

  const ammo = readiness.ammo?.by_module || [];
  $("sp-combat-ammo").innerHTML = ammo.map((row) =>
    `<tr><td>${esc(specialistHumanName(row.item))}</td><td>${esc(row.slot || "—")}</td>` +
    `<td class="num">${specialistNumber(row.clip || 0)}</td><td class="num">${specialistNumber(row.hopper || 0)}</td>` +
    `<td class="num">${specialistNumber(row.total || 0)}</td></tr>`
  ).join("");
  $("sp-combat-ammo-empty").classList.toggle("hidden", ammo.length > 0);

  const session = combat.session;
  const active = !!combat.active;
  const badge = $("sp-combat-state");
  badge.textContent = active ? "SESSION ACTIVE" : session ? "LAST SESSION" : "IDLE";
  badge.className = `sp-state ${active ? "active" : "idle"}`;
  $("sp-combat-start").disabled = active;
  $("sp-combat-end").disabled = !active;
  const target = combat.target;
  const unredeemed = session ? Math.max(0, (session.bounty_cr || 0) + (session.bond_cr || 0) - (session.redeemed_cr || 0)) : 0;
  $("sp-combat-message").textContent = target?.ship
    ? `Target observation: ${target.ship}${target.is_thargoid ? " · THARGOID" : ""}.`
    : active ? `${fmtCr(unredeemed)} in session claims may still need redemption.`
      : "A session also starts automatically on a kill, attack or damage event.";
  setText("sp-combat-duration", fmtDuration(specialistDuration(session, active)));
  setText("sp-combat-kills", session ? specialistNumber(session.kills || 0) : "—");
  setText("sp-combat-ax-kills", session ? specialistNumber(session.ax_kills || 0) : "—");
  setText("sp-combat-bounties", session ? fmtCr(session.bounty_cr || 0) : "—");
  setText("sp-combat-bonds", session ? fmtCr(session.bond_cr || 0) : "—");
  setText("sp-combat-damage", session ? specialistNumber(session.damage_events || 0) : "—");

  const chips = (values, empty) => {
    const rows = Object.entries(values || {});
    return rows.length ? rows.map(([name, count]) => `<span>${esc(specialistHumanName(name))}<b>×${specialistNumber(count)}</b></span>`).join("")
      : `<span class="dim">${esc(empty)}</span>`;
  };
  $("sp-combat-ax-types").innerHTML = chips(session?.ax_kills_by_type, "No AX kills in this session.");
  $("sp-combat-synthesis").innerHTML = chips(session?.synthesis, "No combat synthesis in this session.");
  renderSpecialistHistory("sp-combat-history", specialistHistory("combat"), (item) => ({
    title: `${item.kills || 0} kills · ${item.ax_kills || 0} AX · ${fmtCr((item.bounty_cr || 0) + (item.bond_cr || 0))} claims`,
    subtitle: `${item.damage_events || 0} damage events · ${Object.values(item.synthesis || {}).reduce((a, b) => a + b, 0)} synthesis · ${fmtDuration(item.duration_s)}`,
  }));
}

function carrierAddRouteLeg(leg = {}) {
  const row = document.createElement("div");
  row.className = "sp-route-leg";
  row.innerHTML =
    `<label>System<input class="sp-leg-system" type="text" maxlength="160" value="${esc(leg.system || "")}" placeholder="Destination" required></label>` +
    `<label>Distance (ly)<input class="sp-leg-distance" type="number" min="0.01" step="0.01" value="${leg.distance_ly ?? ""}" placeholder="Exact leg" required></label>` +
    `<label>Tritium (t)<input class="sp-leg-tritium" type="number" min="0" step="0.1" value="${leg.tritium_t ?? ""}" placeholder="Optional"></label>` +
    `<button class="copy sp-remove-leg" type="button" title="Remove this route leg" aria-label="Remove this route leg">×</button>`;
  row.querySelector(".sp-remove-leg").addEventListener("click", () => {
    row.remove();
    if (!$("sp-carrier-legs").children.length) carrierAddRouteLeg();
  });
  $("sp-carrier-legs").appendChild(row);
}

function renderCarrierSpecialist() {
  const carrier = specialistWorkflow("carrier");
  const observed = carrier.carrier_id != null;
  const location = carrier.location || {};
  $("sp-carrier-identity").textContent = observed
    ? `${carrier.name || "FLEET CARRIER"}${carrier.callsign ? ` · ${carrier.callsign}` : ""}`
    : "NO OWNER SNAPSHOT";
  $("sp-carrier-message").textContent = carrier.pending_decommission
    ? "Decommissioning is marked pending in the latest owner snapshot."
    : carrier.pending_jump?.system
      ? `Jump scheduled: ${carrier.pending_jump.system}${carrier.pending_jump.body ? ` · ${carrier.pending_jump.body}` : ""}.`
      : observed
        ? `${location.system || "Location not observed"}${location.body ? ` · ${location.body}` : ""} · ${carrier.docking_access || "docking access unknown"}`
        : "Open Carrier Management in game to supply an authoritative status snapshot.";

  const finance = carrier.finance || {};
  const upkeep = carrier.upkeep || {};
  const space = carrier.space || {};
  const orders = carrier.orders || {};
  setText("sp-carrier-balance", finance.balance_cr == null ? "—" : fmtCr(finance.balance_cr));
  setText("sp-carrier-reserve", finance.reserve_cr == null ? "—" : fmtCr(finance.reserve_cr));
  setText("sp-carrier-runway", upkeep.reserve_weeks == null ? "—" : `${specialistNumber(upkeep.reserve_weeks)} wk`);
  setText("sp-carrier-tank", carrier.fuel_t == null ? "—" : `${specialistNumber(carrier.fuel_t)} t`);
  setText("sp-carrier-space", space.cargo_t == null ? "—" : `${specialistNumber(space.cargo_t)} / ${specialistNumber(space.capacity_t)} t`);
  setText("sp-carrier-exposure", fmtCr(orders.buy_order_exposure_cr || 0));

  if (!$("sp-carrier-config-form").dataset.seeded) {
    if (upkeep.weekly_cr != null) $("sp-carrier-weekly").value = upkeep.weekly_cr;
    if (upkeep.target_weeks != null) $("sp-carrier-target-weeks").value = upkeep.target_weeks;
    $("sp-carrier-config-form").dataset.seeded = "1";
  }
  $("sp-carrier-upkeep-note").textContent = upkeep.weekly_cr == null
    ? "Weekly upkeep is not journaled. Enter the value shown in Carrier Management; Frameshift will not guess it."
    : `${fmtCr(upkeep.weekly_cr)} / week · source: ${upkeep.source || "commander input"}` +
      (upkeep.target_shortfall_cr > 0 ? ` · ${fmtCr(upkeep.target_shortfall_cr)} short of the ${upkeep.target_weeks}-week target.` : ` · ${upkeep.target_weeks}-week target covered.`);

  const inventory = Object.entries(carrier.inventory || {}).map(([symbol, row]) => ({ symbol, ...row }));
  $("sp-carrier-inventory").innerHTML = inventory.length
    ? inventory.map((row) => `<span>${esc(row.name || specialistHumanName(row.symbol))}<b>${specialistNumber(row.count || 0)} t</b></span>`).join("")
    : '<span class="dim">No carrier inventory has been supplied.</span>';
  $("sp-carrier-inventory-source").textContent = `Source: ${carrier.inventory_source || "not supplied"}. CargoTransfer deltas are accepted only while docked at your own carrier.`;
  if (!$("sp-carrier-inventory-form").dataset.seeded) {
    $("sp-carrier-inventory-input").value = inventory.map((row) => `${row.name || specialistHumanName(row.symbol)} | ${row.count || 0}`).join("\n");
    $("sp-carrier-inventory-form").dataset.seeded = "1";
  }

  const route = carrier.route || {};
  if (!$("sp-carrier-legs").dataset.seeded) {
    $("sp-carrier-legs").replaceChildren();
    (route.legs?.length ? route.legs : [{}]).forEach(carrierAddRouteLeg);
    if (route.reserve_t != null) $("sp-carrier-route-reserve").value = route.reserve_t;
    $("sp-carrier-legs").dataset.seeded = "1";
  }
  const issueText = (route.issues || []).map((issue) => `Leg ${issue.leg}: ${issue.reason}`).join(" · ");
  const routeResult = $("sp-carrier-route-result");
  routeResult.classList.remove("good", "warn");
  if (!route.leg_count) {
    routeResult.textContent = "Add systems and exact leg distances; Frameshift checks observed range and tritium coverage.";
  } else {
    const fuel = route.tritium_required_t == null ? "tritium unknown" : `${specialistNumber(route.tritium_required_t)} t required`;
    const stock = route.available_t == null ? "available stock unknown" : `${specialistNumber(route.available_t)} t available`;
    const deficit = route.deficit_t > 0 ? ` · ${specialistNumber(route.deficit_t)} t deficit` : "";
    routeResult.textContent = `${route.leg_count} legs · ${specialistNumber(route.total_distance_ly)} ly · ${fuel} · ${stock}${deficit}` +
      ` · source: ${route.tritium_source || "unknown"}${issueText ? ` · ${issueText}` : ""}`;
    routeResult.classList.add(route.valid && !(route.deficit_t > 0) ? "good" : "warn");
  }

  const orderItems = orders.items || [];
  $("sp-carrier-orders").innerHTML = orderItems.map((row) => {
    const exposure = row.side === "buy" ? (row.quantity || 0) * (row.price_cr || 0) : row.quantity || 0;
    return `<tr><td>${esc(row.name || row.symbol)}${row.black_market ? ' <span class="chip">BLACK MARKET</span>' : ""}</td>` +
      `<td>${esc(String(row.side || "—").toUpperCase())}</td><td class="num">${specialistNumber(row.quantity || 0, " t")}</td>` +
      `<td class="num">${fmtCr(row.price_cr || 0)}</td><td class="num">${row.side === "buy" ? fmtCr(exposure) : `${specialistNumber(exposure)} t stock`}</td></tr>`;
  }).join("");
  $("sp-carrier-orders-empty").classList.toggle("hidden", orderItems.length > 0);
}

async function runSpecialistMutation(url, body, button, successMessage) {
  const original = button?.textContent;
  if (button) {
    button.disabled = true;
    button.textContent = "WORKING…";
  }
  try {
    await specialistJson(url, { method: "POST", body });
    await loadSpecialists(true);
    $("sp-global-status").textContent = successMessage;
    $("sp-global-status").classList.remove("error");
    return true;
  } catch (error) {
    $("sp-global-status").textContent = error.message;
    $("sp-global-status").classList.add("error");
    return false;
  } finally {
    if (button) {
      button.textContent = original;
      button.disabled = false;
    }
    if (specialistState) renderSpecialists();
  }
}

function parseCarrierInventory() {
  const rows = [];
  for (const [index, raw] of $("sp-carrier-inventory-input").value.split(/\r?\n/).entries()) {
    if (!raw.trim()) continue;
    const fields = raw.split(/\s*[|,\t]\s*/);
    if (fields.length < 2 || !fields[0].trim()) throw new Error(`Inventory line ${index + 1}: use Commodity | tonnes.`);
    const count = Number(fields.at(-1));
    if (!Number.isFinite(count) || count < 0) throw new Error(`Inventory line ${index + 1}: tonnes must be zero or greater.`);
    const name = fields.slice(0, -1).join(" | ").trim();
    const symbol = name.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "");
    rows.push({ symbol, name, count: Math.floor(count) });
  }
  return rows;
}

function niceSurfaceRange(metres) {
  const choices = [50, 100, 250, 500, 1000, 2500, 5000, 10000, 25000, 50000, 100000];
  return choices.find((value) => value >= metres) || Math.ceil(metres / 100000) * 100000;
}

function renderExobiologyMap(exobiology) {
  const map = exobiology.current_map;
  const position = exobiology.position;
  const mapTarget = $("sp-exobio-map");
  if (!map) {
    mapTarget.innerHTML = '<div class="sp-map-empty"><b>NO BODY MAP YET</b><span>Land or record a surface sample to establish this local map.</span></div>';
    $("sp-exobio-range").textContent = "";
    return;
  }
  const pins = map.pins || [];
  const finiteDistances = pins.map((pin) => Number(pin.distance_m)).filter(Number.isFinite);
  const range = niceSurfaceRange(Math.max(100, ...(finiteDistances.map((value) => value * 1.2))));
  const project = (value) => Math.max(-43, Math.min(43, (Number(value) || 0) / range * 43));
  const pinColour = (pin) => pin.kind === "organic_sample" ? "#6fcf97"
    : pin.source === "manual" ? "var(--orange-soft)" : "#76a9e8";
  const pinShapes = pins.map((pin) => {
    const x = project(pin.east_m), y = -project(pin.north_m);
    return `<g class="sp-map-pin" transform="translate(${x} ${y})"><circle r="2" fill="${pinColour(pin)}">` +
      `<title>${esc(pin.label || specialistHumanName(pin.kind))} · ${specialistNumber(pin.distance_m, " m")}</title></circle></g>`;
  }).join("");
  const liveOnBody = !!position && (!position.body || position.body === map.body);
  const headingKnown = liveOnBody && position.heading != null && Number.isFinite(Number(position.heading));
  const heading = headingKnown ? Number(position.heading) : 0;
  const player = liveOnBody
    ? `<g class="sp-map-player" transform="rotate(${heading})">${headingKnown ? '<polygon points="0,-6 3.4,4 0,2 -3.4,4" />' : ""}` +
      `<circle r="${headingKnown ? 7 : 3}"><title>Commander${headingKnown ? ` · heading ${Math.round(heading)}°` : " · heading unavailable"}</title></circle></g>` : "";
  mapTarget.innerHTML =
    `<svg viewBox="-50 -50 100 100" aria-hidden="true" focusable="false">` +
    `<circle class="sp-map-boundary" r="44"/><circle class="sp-map-grid" r="22"/>` +
    `<path class="sp-map-axis" d="M-44 0H44M0-44V44"/><text class="sp-map-north" x="0" y="-46">N</text>` +
    `${pinShapes}${player}</svg>`;
  $("sp-exobio-range").textContent = `EDGE ${specialistNumber(range, " m")} · ${pins.length} pins`;
}

function renderExobiologySpecialist() {
  const exobiology = specialistWorkflow("exobiology");
  const map = exobiology.current_map;
  const position = exobiology.position;
  $("sp-exobio-body").textContent = map?.body || position?.body || "NO SURFACE POSITION";
  $("sp-exobio-coords").textContent = position
    ? `${Number(position.lat).toFixed(5)}°, ${Number(position.lon).toFixed(5)}°` +
      (position.heading == null ? "" : ` · HDG ${Math.round(position.heading)}°`) +
      (position.alt_m == null ? "" : ` · ALT ${specialistNumber(position.alt_m, " m")}`)
    : "Latitude / longitude unavailable";
  $("sp-exobio-export").disabled = !map;
  $("sp-exobio-pin-add").disabled = !position;
  renderExobiologyMap(exobiology);

  const sampling = exobiology.sampling;
  const clearance = sampling?.clearance;
  $("sp-sampling-name").textContent = sampling
    ? sampling.variant || sampling.species || sampling.genus || "Organism in progress"
    : "No organism in progress";
  $("sp-sampling-progress").textContent = sampling
    ? `Sample ${sampling.progress || 0} / 3${sampling.colony_m ? ` · required spacing ${specialistNumber(sampling.colony_m, " m")}` : " · spacing unknown"}`
    : "Start a sample in game to arm clearance guidance.";
  const clearanceEl = $("sp-sampling-clearance");
  clearanceEl.className = "sp-clearance unknown";
  if (!sampling || !clearance) {
    clearanceEl.textContent = sampling ? "WAITING FOR POSITION" : "CLEARANCE NOT ARMED";
  } else if (clearance.clear === true) {
    clearanceEl.className = "sp-clearance clear";
    clearanceEl.textContent = `CLEAR TO SAMPLE · ${specialistNumber(clearance.min_dist_m, " m")}`;
  } else if (clearance.clear === false) {
    clearanceEl.className = "sp-clearance blocked";
    const remaining = Math.max(0, Number(sampling.colony_m || 0) - Number(clearance.min_dist_m || 0));
    clearanceEl.textContent = `MOVE ${specialistNumber(remaining, " m")} FARTHER · ${specialistNumber(clearance.min_dist_m, " m")} CLEAR`;
  } else {
    clearanceEl.textContent = `${specialistNumber(clearance.min_dist_m, " m")} FROM NEAREST SAMPLE · REQUIRED SPACING UNKNOWN`;
  }

  const pins = map?.pins || [];
  const pinTotal = Number(map?.pins_total ?? pins.length);
  $("sp-exobio-pin-count").textContent = `${pinTotal} PIN${pinTotal === 1 ? "" : "S"}` +
    (pinTotal > pins.length ? ` / ${pins.length} MOST RECENT SHOWN` : "");
  $("sp-exobio-pins").innerHTML = pins.length ? pins.slice().reverse().map((pin) => {
    const bearing = pin.bearing_deg == null ? "bearing unknown" : `${Math.round(pin.bearing_deg)}° · ${specialistNumber(pin.distance_m, " m")}`;
    const relative = pin.relative_bearing_deg == null ? "" : ` · ${pin.relative_bearing_deg < 0 ? "left" : "right"} ${Math.abs(Math.round(pin.relative_bearing_deg))}°`;
    const remove = pin.source === "manual"
      ? `<button type="button" class="copy sp-pin-delete" data-pin-id="${esc(pin.id)}">REMOVE</button>` : "";
    return `<div class="sp-pin-row"><i class="${pin.kind === "organic_sample" ? "sample" : pin.source === "manual" ? "manual" : "journal"}"></i>` +
      `<div><b>${esc(pin.label || specialistHumanName(pin.kind))}</b><span>${esc(bearing + relative)} · ${esc(pin.source || "journal")}</span></div>${remove}</div>`;
  }).join("") : '<div class="dim empty">No pins on this body yet.</div>';
  $("sp-exobio-pin-status").textContent = position
    ? "Manual pins use the current Status.json latitude and longitude. Journal sample pins cannot be removed here."
    : "Pins require a live latitude and longitude from Status.json.";
}

function renderSpecialists() {
  renderMiningSpecialist();
  renderCombatSpecialist();
  renderCarrierSpecialist();
  renderExobiologySpecialist();
}

async function exportExobiologyGeoJson() {
  const button = $("sp-exobio-export");
  button.disabled = true;
  try {
    const response = await fetch("/api/specialists/exobiology/geojson", { cache: "no-store" });
    if (!response.ok) {
      let message = "GeoJSON export failed.";
      try { message = (await response.json()).error || message; } catch {}
      throw new Error(message);
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const body = specialistWorkflow("exobiology").current_map?.body || "surface-map";
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${body.replace(/[^a-z0-9_-]+/gi, "-")}.geojson`;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    $("sp-global-status").textContent = "Surface pins exported as portable GeoJSON.";
    $("sp-global-status").classList.remove("error");
  } catch (error) {
    $("sp-global-status").textContent = error.message;
    $("sp-global-status").classList.add("error");
  } finally {
    button.disabled = !specialistWorkflow("exobiology").current_map;
  }
}

async function removeExobiologyPin(pinId, button) {
  button.disabled = true;
  try {
    await specialistJson(`/api/specialists/exobiology/pins/${encodeURIComponent(pinId)}`, { method: "DELETE" });
    await loadSpecialists(true);
    $("sp-global-status").textContent = "Manual surface pin removed.";
    $("sp-global-status").classList.remove("error");
  } catch (error) {
    $("sp-global-status").textContent = error.message;
    $("sp-global-status").classList.add("error");
    button.disabled = false;
  }
}

function initSpecialists() {
  const switchButtons = [...document.querySelectorAll(".sp-switcher [data-specialist]")];
  switchButtons.forEach((button, index) => {
    button.addEventListener("click", () => setSpecialistWorkflow(button.dataset.specialist));
    button.addEventListener("keydown", (event) => {
      if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
      event.preventDefault();
      const next = (index + (event.key === 'ArrowRight' ? 1 : switchButtons.length - 1)) % switchButtons.length;
      setSpecialistWorkflow(switchButtons[next].dataset.specialist);
      switchButtons[next].focus();
    });
  });
  setSpecialistWorkflow(localStorage.getItem("specialistWorkflow") || "mining");

  $("sp-mining-start").addEventListener("click", (event) =>
    runSpecialistMutation("/api/specialists/mining/start", {}, event.currentTarget, "Mining run started."));
  $("sp-mining-end").addEventListener("click", (event) =>
    runSpecialistMutation("/api/specialists/mining/end", { reason: "manual" }, event.currentTarget, "Mining run archived."));
  $("sp-combat-start").addEventListener("click", (event) =>
    runSpecialistMutation("/api/specialists/combat/start", {}, event.currentTarget, "Combat session started."));
  $("sp-combat-end").addEventListener("click", (event) =>
    runSpecialistMutation("/api/specialists/combat/end", { reason: "manual" }, event.currentTarget, "Combat session archived."));

  $("sp-carrier-config-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const weekly = Number($("sp-carrier-weekly").value);
    const target = Number($("sp-carrier-target-weeks").value);
    await runSpecialistMutation("/api/specialists/carrier/config", {
      weekly_upkeep_cr: weekly, target_weeks: target,
    }, event.submitter, "Carrier upkeep input saved locally.");
  });
  $("sp-carrier-inventory-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const items = parseCarrierInventory();
      await runSpecialistMutation("/api/specialists/carrier/inventory", {
        items, source: "commander inventory input",
      }, event.submitter, "Carrier inventory input saved locally.");
    } catch (error) {
      $("sp-global-status").textContent = error.message;
      $("sp-global-status").classList.add("error");
    }
  });
  $("sp-carrier-add-leg").addEventListener("click", () => carrierAddRouteLeg());
  $("sp-carrier-route-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const legs = [...document.querySelectorAll(".sp-route-leg")].map((row) => {
      const tritium = row.querySelector(".sp-leg-tritium").value;
      return {
        system: row.querySelector(".sp-leg-system").value.trim(),
        distance_ly: Number(row.querySelector(".sp-leg-distance").value),
        ...(tritium === "" ? {} : { tritium_t: Number(tritium) }),
      };
    });
    const perJump = $("sp-carrier-per-jump").value;
    await runSpecialistMutation("/api/specialists/carrier/route", {
      legs,
      tritium_per_jump_t: perJump === "" ? null : Number(perJump),
      reserve_t: Number($("sp-carrier-route-reserve").value) || 0,
    }, event.submitter, "Carrier tritium route recalculated from explicit inputs.");
  });

  $("sp-exobio-pin-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const added = await runSpecialistMutation("/api/specialists/exobiology/pins", {
      label: $("sp-exobio-pin-label").value.trim(), kind: $("sp-exobio-pin-kind").value,
    }, event.submitter, "Current surface position pinned locally.");
    if (added) $("sp-exobio-pin-label").value = "";
  });
  $("sp-exobio-pins").addEventListener("click", (event) => {
    const button = event.target.closest(".sp-pin-delete");
    if (button) removeExobiologyPin(button.dataset.pinId, button);
  });
  $("sp-exobio-export").addEventListener("click", exportExobiologyGeoJson);

  if (!$("sp-carrier-legs").children.length) carrierAddRouteLeg();
  setInterval(() => {
    if (!specialistVisible()) return;
    if (specialistState) {
      renderMiningSpecialist();
      renderCombatSpecialist();
    }
    if (!specialistLoading && Date.now() - specialistLastFetch >= 4000) loadSpecialists(true);
  }, 1000);
}

/* ---------- wiring ---------- */

function resetProfileWorkspaces(nextSnapshot) {
  profileGeneration += 1;
  const commanderId = profileStorageId(nextSnapshot);
  const commanderName = nextSnapshot?.commander || "";

  clearAnalyticsWorkspace();
  clearAlertWorkspace();
  loadActiveRoute(commanderId);
  loadGalaxyHistory(commanderId, commanderName || null);
  engMatsSig = null;
  for (const id of ["engplan-summary", "engplan-list", "engplan-materials", "engplan-traders"]) {
    if ($(id)) $(id).replaceChildren();
  }
  if ($("engplan-summary")) {
    $("engplan-summary").innerHTML = `<div class="dim ep-api-error">${commanderId
      ? "Loading this commander's engineering wishlist..."
      : "Waiting for a commander profile..."}</div>`;
  }
  $("engplan-form")?.reset();
  if ($("ep-pin")) $("ep-pin").textContent = "ADD TO WISHLIST";

  opsWorkspaceLoading = null;
  opsState = {
    objectives: [], plan: null, timings: null, boards: [], snapshot: null,
    conflicts: [], activeBoardId: loadOpsBoardId(commanderId),
  };
  specialistState = null;
  specialistLoading = null;
  specialistLastFetch = 0;

  if ($("ops-objective-form")) resetOpsObjectiveForm();
  for (const id of [
    "ops-board-form", "ops-board-objective-form", "ops-assignment-form",
    "ops-reservation-form", "ops-contribution-form",
  ]) {
    $(id)?.reset();
  }
  if ($("ops-assignment-name")) $("ops-assignment-name").value = commanderName;
  if ($("ops-contribution-name")) $("ops-contribution-name").value = commanderName;
  for (const id of [
    "ops-plan-selected", "ops-plan-alternatives", "ops-objective-list",
    "ops-board-objectives", "ops-assignments", "ops-reservations", "ops-contributions",
    "ops-timing-list", "ops-plan-warnings", "ops-conflicts",
  ]) {
    if ($(id)) $(id).replaceChildren();
  }
  if ($("ops-board-workspace")) $("ops-board-workspace").classList.add("hidden");
  if ($("ops-board-empty")) {
    $("ops-board-empty").classList.remove("hidden");
    $("ops-board-empty").textContent = commanderId
      ? "Loading this commander's local operations workspace..."
      : "Waiting for a commander profile...";
  }

  for (const id of ["sp-carrier-config-form", "sp-carrier-inventory-form", "sp-carrier-legs"]) {
    const element = $(id);
    if (element) delete element.dataset.seeded;
  }
  $("sp-carrier-config-form")?.reset();
  $("sp-carrier-inventory-form")?.reset();
  $("sp-carrier-route-form")?.reset();
  if ($("sp-carrier-legs")) {
    $("sp-carrier-legs").replaceChildren();
    carrierAddRouteLeg();
  }
  if ($("sp-global-status")) {
    $("sp-global-status").textContent = commanderId
      ? "Loading local specialist records for this commander..."
      : "Waiting for a commander profile...";
  }
  if ($("sp-global-status")) renderSpecialists();

  for (const id of [
    "galaxy-history-card", "galhistory-list", "powerplay-card", "factions-list",
  ]) {
    if ($(id)) $(id).dataset.sig = "";
  }
  renderRouteProgress();

  if (commanderId) {
    // Do not wait for the user to revisit a tab: stale forms and cached
    // responses must be replaced as part of the identity transition itself.
    loadEngineering();
    loadOpsWorkspace();
    loadSpecialists(true);
    loadAnalytics();
    pollAlerts();
  }
}

async function poll() {
  try {
    const resp = await fetch("/api/state", { cache: "no-store" });
    if (resp.status === 401) {
      let detail = {};
      try { detail = await resp.json(); } catch (error) {}
      enterPairingRequired(detail.error || "This device's access was revoked or expired. Pair it again from the gaming PC.");
      setTimeout(poll, 1500);
      return;
    }
    if (resp.ok) {
      const nextState = await resp.json();
      const previousCommander = profileStorageId();
      state = nextState;
      const nextCommander = profileStorageId(nextState);
      if (previousCommander !== nextCommander) resetProfileWorkspaces(nextState);
      securityLocked = false;
      render();
    }
  } catch (e) {
    // Server briefly unreachable; keep the last render but say so quietly.
    const link = $("fp-link");
    if (link) link.textContent = "LINK · RETRYING";
  }
  setTimeout(poll, 1500);
}

function paneEnter(el) {
  el.classList.remove("pane-enter");
  void el.offsetWidth; // restart cleanly if the class is re-applied
  el.classList.add("pane-enter");
  el.addEventListener("animationend",
    () => el.classList.remove("pane-enter"), { once: true });
}

function activateTab(name, enter = true) {
  document.querySelectorAll("#tabs .tab").forEach((b) =>
    b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tabpane").forEach((p) => {
    const show = p.id === "tab-" + name;
    const wasHidden = p.classList.contains("hidden");
    p.classList.toggle("hidden", !show);
    if (show && wasHidden && enter) paneEnter(p);
    // A hidden pane never fires animationend; strip stale motion classes so
    // they can't replay when the pane is next revealed.
    if (!show) p.classList.remove("pane-enter", "slide-in-left", "slide-in-right");
  });
  localStorage.setItem("activeTab", name);
  if (name === "analytics") loadAnalytics();
  if (name === "ops") loadOpsWorkspace();
  if (name === "specialists" && Date.now() - specialistLastFetch >= 1500) loadSpecialists();
  if (name === "database") nudgeDbStatus();
}

function initTabs() {
  document.querySelectorAll("#tabs .tab").forEach((b) =>
    b.addEventListener("click", () => activateTab(b.dataset.tab)));
  const saved = localStorage.getItem("activeTab");
  if (saved && document.getElementById("tab-" + saved)) activateTab(saved);
}

document.addEventListener("DOMContentLoaded", async () => {
  try {
    $("pairing-retry").addEventListener("click", () => window.location.reload());
    if (!await bootstrapSecurity()) return;
    initSpecialists();
    initTabs();
    // Resolve the pre-paint guard as soon as local authentication is ready.
    // Waiting until the rest of the page's handlers are wired lets the desktop
    // layout flash briefly on every Panel refresh.
    setPanelMode(panelModeOnLaunch(), false);
  } finally {
    // Storage/browser policy or an initialization exception may prevent the
    // normal setPanelMode path. Never leave an authenticated page invisible.
    document.documentElement.classList.remove("panel-mode-prepaint");
  }

  // OPS is entirely local: durable commander objectives, learned timings and
  // file-exchanged operations boards. Delegation survives each list render.
  $("ops-plan-form").addEventListener("submit", buildOpsPlan);
  $("ops-plan-card").addEventListener("click", (event) => {
    const button = event.target.closest("[data-ops-plot]");
    if (!button) return;
    const system = button.dataset.opsPlot;
    if (system) plotSystem(system);
  });
  $("ops-objective-form").addEventListener("submit", saveOpsObjective);
  $("ops-objective-cancel").addEventListener("click", resetOpsObjectiveForm);
  $("ops-objective-filter").addEventListener("change", loadOpsObjectives);
  $("ops-objective-list").addEventListener("change", (event) => {
    const select = event.target.closest("[data-objective-status]");
    if (select) patchOpsObjective(select.dataset.objectiveStatus, { status: select.value });
  });
  $("ops-objective-list").addEventListener("click", (event) => {
    const edit = event.target.closest("[data-objective-edit]");
    const remove = event.target.closest("[data-objective-delete]");
    if (edit) editOpsObjective(edit.dataset.objectiveEdit);
    if (remove) deleteOpsObjective(remove.dataset.objectiveDelete);
  });
  $("ops-board-select").addEventListener("change", (event) => {
    opsState.activeBoardId = event.currentTarget.value;
    saveOpsBoardId(opsState.activeBoardId);
    loadOperations();
  });
  $("ops-board-refresh").addEventListener("click", loadOperations);
  $("ops-board-export").addEventListener("click", exportOperationsBoard);
  $("ops-board-import-trigger").addEventListener("click", () => $("ops-board-import").click());
  $("ops-board-import").addEventListener("change", importOperationsBoard);
  $("ops-board-form").addEventListener("submit", createOperationsBoard);
  $("ops-board-objective-form").addEventListener("submit", addOperationsObjective);
  $("ops-assignment-form").addEventListener("submit", addOperationsAssignment);
  $("ops-reservation-form").addEventListener("submit", addOperationsReservation);
  $("ops-contribution-form").addEventListener("submit", addOperationsContribution);
  $("ops-board-status").addEventListener("change", (event) => {
    const boardId = opsState.snapshot?.board?.id;
    if (boardId) patchOperation("boards", boardId, { status: event.currentTarget.value });
  });
  $("ops-board-delete").addEventListener("click", () => {
    const boardId = opsState.snapshot?.board?.id;
    if (boardId) deleteOperation("boards", boardId);
  });
  $("ops-board-workspace").addEventListener("change", (event) => {
    const select = event.target.closest("[data-op-status]");
    if (select) patchOperation(select.dataset.kind, select.dataset.id, { status: select.value });
  });
  $("ops-board-workspace").addEventListener("click", (event) => {
    const remove = event.target.closest("[data-op-delete]");
    if (remove) deleteOperation(remove.dataset.kind, remove.dataset.id);
  });
  if (state?.commander) {
    $("ops-assignment-name").value = state.commander;
    $("ops-contribution-name").value = state.commander;
  }
  $("galhistory-clear").addEventListener("click", clearGalaxyHistory);
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

  // Persist search settings across reloads; restored values win over
  // auto-seeding. Returns whether anything was restored.
  const persistForm = (formId, storageKey, fieldIds) => {
    let restored = false;
    try {
      const saved = JSON.parse(localStorage.getItem(storageKey) || "{}");
      for (const id of fieldIds) {
        if (!(id in saved)) continue;
        const el = $(id);
        if (el.type === "checkbox") el.checked = !!saved[id];
        else el.value = saved[id];
        restored = true;
      }
    } catch (e) { /* corrupted storage - use defaults */ }
    $(formId).addEventListener("input", () => {
      const out = {};
      for (const id of fieldIds) {
        const el = $(id);
        out[id] = el.type === "checkbox" ? el.checked : el.value;
      }
      localStorage.setItem(storageKey, JSON.stringify(out));
    });
    return restored;
  };
  if (persistForm("route-form", "routeForm", ["rf-mode", "rf-capital", "rf-cargo",
      "rf-radius", "rf-maxleg", "rf-jumprange", "rf-results", "rf-hop", "rf-hops",
      "rf-minsupply", "rf-lsdist", "rf-age", "rf-largepad"])) {
    routeFormTouched = true;
  }
  applyMode();
  // The "Near" overrides deliberately reset each launch: a search silently
  // pinned to last week's system would be worse than retyping it.
  persistForm("cs-form", "csForm", ["cs-query", "cs-mode", "cs-radius", "cs-min", "cs-largepad"]);
  persistForm("mining-form", "miningForm", ["mn-radius", "mn-minprice", "mn-age", "mn-largepad"]);
  persistForm("os-form", "osForm", ["os-query", "os-type"]);
  persistForm("nr-form", "neutronForm", ["nr-to", "nr-range", "nr-eff"]);
  persistForm("rr-form", "richesForm", ["rr-range", "rr-radius", "rr-minvalue", "rr-max", "rr-loop"]);
  persistForm("exo-form", "exoForm", ["exo-grav", "exo-minvalue"]);
  persistForm("sd-form", "sellDataForm", ["sd-carriers"]);

  $("plot-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    if (plotBusy) { cancelPlot(); return; }
    const name = $("plot-input").value.trim();
    if (name) plotSystem(name);
  });

  // Flight panel mode (tablet as a cockpit display)
  initPanelNav();
  $("panel-toggle").addEventListener("click", () => setPanelMode(true));
  $("panel-exit").addEventListener("click", () => setPanelMode(false));
  $("fp-plot-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    if (plotBusy) { cancelPlot(); return; }
    const name = $("fp-plot-input").value.trim();
    if (name) plotSystem(name);
  });
  $("fp-bestloop").addEventListener("click", findBestLoop);
  $("fp-voice").addEventListener("click", () => setVoice(!voiceOn, true));
  setVoice(voiceOn);  // reflect persisted state on the toggle (no speech yet)
  applyTheme();
  applyCrtFx();
  applyDisplaySettings();
  $("fp-full").addEventListener("click", toggleFullscreen);
  document.addEventListener("fullscreenchange", () => {
    const on = !!document.fullscreenElement;
    const btn = $("fp-full");
    btn.classList.toggle("on", on);
    btn.setAttribute("aria-pressed", String(on));
    btn.title = on ? "Leave fullscreen" : "Expand to fullscreen";
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
  $("pairing-copy").addEventListener("click", (ev) => copyText($("pairing-link").value, ev.currentTarget));
  $("pairing-refresh").addEventListener("click", () => refreshSecurityPanel(true));
  $("diagnostics-bundle").addEventListener("click", downloadSupportBundle);
  $("extensions-reload").addEventListener("click", reloadExtensions);
  initExtensionBuilder();
  $("extensions-status").addEventListener("click", (event) => {
    const button = event.target.closest("[data-extension-action]");
    if (button) changeExtensionApproval(
      button.dataset.extensionId, button.dataset.extensionAction, button);
  });
  $("cs-form").addEventListener("submit", searchCommodity);
  sortableHeaders("cs-table", sortCommodityTable);
  sortableHeaders("mining-table", sortMiningTable);
  sortableHeaders("os-table", sortStationTable);
  initSuggest();
  $("mining-form").addEventListener("submit", searchMining);
  $("os-form").addEventListener("submit", searchStations);
  $("cargo-sell-btn").addEventListener("click", findCargoSell);
  $("sd-form").addEventListener("submit", findSellPoints);
  $("iff-form").addEventListener("submit", findInterstellarFactors);
  $("build-slef").addEventListener("click", (ev) => loadoutSlef && copyText(loadoutSlef, ev.currentTarget));
  $("launch-game").addEventListener("click", launchGame);
  $("exo-form").addEventListener("submit", searchExobio);
  buildExoGenusChips();

  migrateEngineeringLayout();
  applyCardOrders();
  applyCardVisibility();
  const toggleArrange = () => setArrangeMode(!document.body.classList.contains("arranging"));
  $("arrange-btn").addEventListener("click", toggleArrange);
  $("fp-arrange").addEventListener("click", toggleArrange);

  $("engplan-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    pinBlueprint({
      id: $("ep-blueprint").value,
      current_grade: Number($("ep-current").value) || 0,
      target_grade: Number($("ep-target").value) || 0,
      quantity: Number($("ep-quantity").value) || 1,
    });
  });
  $("ep-search").addEventListener("input", () => fillEngineeringCatalog());
  $("ep-kind").addEventListener("change", () => fillEngineeringCatalog());
  $("ep-blueprint").addEventListener("change", () => updateEngineeringGradeFields());
  $("ep-target").addEventListener("change", () => updateEngineeringGradeFields(
    Number($("ep-current").value), Number($("ep-target").value)));
  $("ep-traders").addEventListener("click", findTraders);
  loadEngineering();
  $("ss-form").addEventListener("submit", loadSystemStations);
  // Delegated: rows rebuild every poll, the table itself doesn't.
  $("market-table").addEventListener("click", (ev) => {
    const cell = ev.target.closest(".spark-click");
    if (!cell) return;
    histExpanded = histExpanded === cell.dataset.sym ? null : cell.dataset.sym;
    renderMarket();
  });

  $("notes-close").addEventListener("click", () => $("notes-modal").classList.add("hidden"));
  $("notes-modal").addEventListener("click", (ev) => {
    if (ev.target === ev.currentTarget) $("notes-modal").classList.add("hidden");
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") $("notes-modal").classList.add("hidden");
  });
  $("rr-form").addEventListener("submit", planRiches);
  $("nr-form").addEventListener("submit", planNeutron);

  // Static guide links open in the real browser when inside the desktop window.
  document.addEventListener("click", (ev) => {
    const a = ev.target.closest("a.extlink");
    if (a && openExternal(a.href, a.textContent)) ev.preventDefault();
  });
  $("an-days").addEventListener("change", loadAnalytics);

  renderRouteProgress();  // show a persisted route immediately, before first poll
  loadTtsStatus();        // arms the neural voice for callouts if it's installed
  poll();
  pollDbStatus();
  pollAlerts();
  pollUpdate();
  loadSettings();
  refreshSecurityPanel();
  loadLocalServices();
  loadProfiles();
  $("profiles-refresh").addEventListener("click", () => loadProfiles());
  loadCommodityList();
});
