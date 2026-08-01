/** @import {EngineeringCatalog, MaterialColumn, TradeableMaterial, TradeRatios} from "../api/contracts/engineering.js" */

import { byId, requireById, setVisible } from "../core/dom.js";
import { clear, html, render } from "../core/html.js";
import { appStore } from "../core/store.js";
import { searchMaterialTraders } from "./material-traders.js";
import { clearMaterialBodies, searchMaterialBodies } from "./material-bodies.js";

const TRADER_KINDS = ["raw", "manufactured", "encoded"];

/** @type {Map<string, TradeableMaterial>} */
let mfMaterials = new Map();
/** @type {{[key: string]: MaterialColumn}} */
let mfColumns = {};
/** @type {TradeRatios} */
let mfRatios = { up: 6, down: 3 };

/**
 * Fold a name the way the bundled catalog does, so "Datamined Wake Exceptions",
 * "datamined wake exceptions" and "dataminedwakeexceptions" all resolve.
 * Mirrors `engineering_catalog._key` in Python — keep the two in step.
 * @param {string} value
 */
function foldName(value) {
  return String(value || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "");
}

/** @param {string} kind @param {string|null|undefined} family */
function columnKey(kind, family) {
  return `${kind}/${family}`;
}

/**
 * Index the material reference shipped with the engineering catalog and fill
 * the finder's autocomplete list.
 * @param {EngineeringCatalog} catalog
 */
export function setMaterialReference(catalog) {
  const materials = catalog.materials || [];
  mfMaterials = new Map(materials.map((material) => [foldName(material.name), material]));
  mfColumns = catalog.material_columns || {};
  mfRatios = catalog.trade_ratios || { up: 6, down: 3 };
  const list = byId("mf-material-list");
  if (!list) return;
  list.replaceChildren();
  for (const material of materials) {
    const option = document.createElement("option");
    option.value = material.name;
    list.appendChild(option);
  }
  // The material field is restored from the last session, so answer it now
  // rather than leaving a filled box with no answer under it.
  if (byId("mf-answer")) renderMaterialAnswer();
}

/**
 * @typedef {{
 *   material: TradeableMaterial,
 *   column: MaterialColumn|null,
 *   peers: TradeableMaterial[],
 * }} ResolvedMaterial
 */

/**
 * Resolve a typed name to its material and trader column. Pure — no DOM.
 * @param {string} name
 * @returns {ResolvedMaterial|null}
 */
export function resolveMaterial(name) {
  const material = mfMaterials.get(foldName(name));
  if (!material) return null;
  const key = columnKey(material.kind, material.family);
  const peers = [...mfMaterials.values()]
    .filter(
      (other) =>
        other.symbol !== material.symbol &&
        other.kind === material.kind &&
        other.family === material.family &&
        other.grade,
    )
    .sort((a, b) => (a.grade || 0) - (b.grade || 0));
  return { material, column: mfColumns[key] || null, peers };
}

/**
 * Names that look like what was typed, for an unrecognised entry.
 * @param {string} name
 */
function nearMatches(name) {
  const folded = foldName(name);
  if (folded.length < 3) return [];
  return [...mfMaterials.values()]
    .filter((material) => foldName(material.name).includes(folded))
    .slice(0, 6)
    .map((material) => material.name);
}

/**
 * What each column peer buys, concretely. Pure — no DOM.
 * @param {TradeableMaterial} material
 * @param {TradeableMaterial[]} peers
 * @param {TradeRatios} [ratios]
 * @returns {{peer: string, grade: number, spend: number, gain: number, direction: string}[]}
 */
export function tradeLines(material, peers, ratios = mfRatios) {
  const target = material.grade || 0;
  /** @type {{peer: string, grade: number, spend: number, gain: number, direction: string}[]} */
  const lines = [];
  for (const peer of peers) {
    const grade = peer.grade || 0;
    if (!grade || !target || grade === target) continue;
    lines.push(
      grade > target
        ? {
            peer: peer.name,
            grade,
            spend: 1,
            gain: ratios.down ** (grade - target),
            direction: "down",
          }
        : {
            peer: peer.name,
            grade,
            spend: ratios.up ** (target - grade),
            gain: 1,
            direction: "up",
          },
    );
  }
  // Best deal first. Trading down always beats trading up, and within a
  // direction the nearest grade is the cheapest in practice — one G4 buys 27
  // G1s, but G4s are far harder to come by than the G2 that buys 3.
  return lines.sort(
    (a, b) =>
      (a.direction === b.direction ? 0 : a.direction === "down" ? -1 : 1) ||
      Math.abs(a.grade - target) - Math.abs(b.grade - target),
  );
}

/** Render the offline half of the answer. Never touches the network. */
export function renderMaterialAnswer() {
  const out = /** @type {HTMLElement} */ (requireById("mf-answer"));
  const typed = /** @type {HTMLInputElement} */ (requireById("mf-material")).value.trim();
  // Before the catalog lands nothing is known, so say nothing — calling a real
  // material unknown just because the payload is still in flight is worse than
  // a blank space. The catalog load renders again once it arrives.
  if (!typed || !mfMaterials.size) {
    clear(out);
    return null;
  }
  const resolved = resolveMaterial(typed);
  if (!resolved) {
    const suggestions = nearMatches(typed);
    render(
      out,
      html`<div class="mf-unknown dim">
        <b>${typed}</b> is not an engineering material Frameshift
        knows.${suggestions.length ? ` Did you mean: ${suggestions.join(", ")}?` : ""}
      </div>`,
    );
    return null;
  }

  const { material, column, peers } = resolved;
  const tradeable = material.tradeable && column?.tradeable;
  const lines = tradeable ? tradeLines(material, peers, mfRatios) : [];
  render(
    out,
    html`<div class="mf-head">
        <b>${material.name}</b>
        <span class="chip">${material.kind}${material.grade ? ` · G${material.grade}` : ""}</span>
        <span class="dim">${column ? `${column.label} column` : "no material-trader column"}</span>
      </div>
      ${
        tradeable
          ? html`<div class="mf-trades">
              <div class="label">
                TRADE FOR IT
                <span class="dim"
                  >at ${material.kind === "encoded" ? "an" : "a"} ${material.kind} material
                  trader</span
                >
              </div>
              ${lines.map(
                (line) =>
                  html`<div class="mf-trade-line mf-${line.direction}">
                    <span class="mf-spend">${line.spend}×</span> <b>${line.peer}</b>
                    <span class="dim">G${line.grade}</span>
                    <span class="mf-arrow">→</span>
                    <span class="mf-gain">${line.gain}×</span> ${material.name}
                  </div>`,
              )}
            </div>`
          : html`<div class="mf-trades dim">
              Material traders do not handle
              ${material.name}${column ? ` — the ${column.label} group cannot be exchanged` : ""}.
              You have to collect it.
            </div>`
      }
      <div class="mf-source">
        <b>WHERE IT DROPS</b> ${material.source || "No source note in the bundled catalog."}
      </div>`,
  );
  return resolved;
}

/** Run both searches for whatever is currently typed. @param {SubmitEvent|{preventDefault(): void}} ev */
export async function runMaterialFinder(ev) {
  ev.preventDefault();
  const status = /** @type {HTMLElement} */ (requireById("mf-status"));
  const near = /** @type {HTMLInputElement} */ (requireById("mf-near")).value.trim();
  const typed = /** @type {HTMLInputElement} */ (requireById("mf-material")).value.trim();
  if (!appStore.identity().commanderId) {
    status.classList.add("error");
    status.textContent = "Waiting for the commander profile...";
    return;
  }
  status.classList.remove("error");
  status.textContent = "";
  const resolved = renderMaterialAnswer();

  if (typed && !resolved) {
    setVisible(requireById("mf-traders-wrap"), false);
    setVisible(requireById("mf-bodies-wrap"), false);
    return;
  }

  // Only raw materials sit on body surfaces; the other kinds have no bodies to
  // prospect and the catalog source text is the whole answer for them.
  const prospectable = resolved?.material.kind === "raw";
  setVisible(requireById("mf-bodies-wrap"), Boolean(prospectable));
  if (!prospectable) clearMaterialBodies();

  const tradeable = !resolved || (resolved.material.tradeable && resolved.column?.tradeable);
  setVisible(requireById("mf-traders-wrap"), Boolean(tradeable));

  const minShare = /** @type {HTMLInputElement} */ (requireById("mf-minshare")).value;
  // Independent searches: one failing must not blank the other.
  await Promise.all([
    tradeable
      ? searchMaterialTraders({ kinds: resolved ? [resolved.material.kind] : TRADER_KINDS, near })
      : null,
    prospectable && resolved
      ? searchMaterialBodies({ material: resolved.material.name, near, minShare })
      : null,
  ]);
}

/** Cross-link target for the workshop's FIND TRADERS button. */
export function showAllMaterialTraders() {
  const input = /** @type {HTMLInputElement} */ (requireById("mf-material"));
  input.value = "";
  requireById("matfinder-card").scrollIntoView({ behavior: "smooth", block: "start" });
  input.focus();
  return runMaterialFinder({ preventDefault() {} });
}

appStore.onProfileChange(() => {
  byId("mf-answer")?.replaceChildren();
  const status = /** @type {HTMLElement|null} */ (byId("mf-status"));
  if (status) {
    status.classList.remove("error");
    status.textContent = "";
  }
  const near = /** @type {HTMLInputElement|null} */ (byId("mf-near"));
  if (near) near.value = "";
});
