/** @import {MaterialBody} from "../api/contracts/engineering.js" */

import { byId, requireById, setVisible } from "../core/dom.js";
import { fmtNum } from "../core/fmt.js";
import { clear, html, render } from "../core/html.js";
import { appStore } from "../core/store.js";
import { isStaleCommanderResponse } from "../api/errors.js";
import { engineeringApi } from "../api/engineering.js";
import { bumpSort, sortedRows, updateSortIndicators } from "./commodities.js";
import { copySystemButton, plotButton } from "../shell/status.js";

/** @type {MaterialBody[]|null} */
export let mbResults = null;

/** @type {{key: string, dir: number}|null} */
export let mbSort = null;

let materialBodyRequestId = 0;

/** @type {Record<string, {value: (row: MaterialBody) => string|number, firstDir: number}>} */
export const MB_SORT_COLUMNS = {
  body: { value: (r) => (r.body || "").toLowerCase(), firstDir: 1 },
  subtype: { value: (r) => (r.subtype || "").toLowerCase(), firstDir: 1 },
  share: { value: (r) => r.share || 0, firstDir: -1 },
  system: { value: (r) => (r.system || "").toLowerCase(), firstDir: 1 },
  jump: { value: (r) => (r.distance != null ? Number(r.distance) : Infinity), firstDir: 1 },
  dist_ls: { value: (r) => (r.dist_ls != null ? Number(r.dist_ls) : Infinity), firstDir: 1 },
};

export function renderMaterialBodyRows() {
  const table = /** @type {HTMLTableElement} */ (requireById("mf-bodies-table"));
  const tbody = /** @type {HTMLTableSectionElement} */ (table.querySelector("tbody"));
  if (!mbResults) return;
  updateSortIndicators(table, mbSort);
  clear(tbody);
  for (const r of sortedRows(mbResults, MB_SORT_COLUMNS, mbSort)) {
    const tr = document.createElement("tr");
    render(
      tr,
      html`<td><b>${r.body}</b></td>
        <td class="dim">${r.subtype || "—"}</td>
        <td class="num orange">${r.share}%</td>
        <td>${r.system}</td>
        <td class="num">${r.distance} ly</td>
        <td class="num">${r.dist_ls != null ? fmtNum(Math.round(r.dist_ls)) + " ls" : "—"}</td>`,
    );
    const td = document.createElement("td");
    td.className = "num";
    td.appendChild(copySystemButton(r.system));
    td.appendChild(plotButton(r.system));
    tr.appendChild(td);
    tbody.appendChild(tr);
  }
}

/** @param {string} key */
export function sortMaterialBodies(key) {
  if (!MB_SORT_COLUMNS[key] || !mbResults) return;
  mbSort = bumpSort(mbSort, key, MB_SORT_COLUMNS);
  renderMaterialBodyRows();
}

export function clearMaterialBodies() {
  materialBodyRequestId += 1;
  mbResults = null;
  const table = /** @type {HTMLTableElement|null} */ (byId("mf-bodies-table"));
  table?.querySelector("tbody")?.replaceChildren();
  if (table) setVisible(table, false);
  byId("mf-bodies-status")?.replaceChildren();
}

/**
 * Nearest landable bodies to prospect one raw material on.
 * @param {{material: string, near?: string, minShare?: string|number}} options
 */
export async function searchMaterialBodies({ material, near, minShare }) {
  const identity = appStore.identity();
  const status = /** @type {HTMLElement} */ (requireById("mf-bodies-status"));
  const table = /** @type {HTMLTableElement} */ (requireById("mf-bodies-table"));
  const go = /** @type {HTMLButtonElement} */ (requireById("mf-go"));
  if (!identity.commanderId) {
    status.classList.add("error");
    status.textContent = "Waiting for the commander profile...";
    return;
  }
  const requestId = ++materialBodyRequestId;
  go.disabled = true;
  status.classList.remove("error");
  // Scanning surface composition across the galaxy is slow at Spansh's end.
  status.textContent = `Finding landable bodies rich in ${material}… (this one takes ~15s)`;
  try {
    const data = await engineeringApi.findMaterialBodies(material, near || undefined, minShare);
    if (requestId !== materialBodyRequestId || !appStore.isCurrent(identity)) return;
    mbResults = data.bodies || [];
    if (!mbSort) mbSort = { key: "jump", dir: 1 };
    renderMaterialBodyRows();
    setVisible(table, mbResults.length > 0);
    status.textContent = mbResults.length
      ? `${mbResults.length} landable ${mbResults.length === 1 ? "body" : "bodies"} with at least ` +
        `${data.min_share}% ${material}${near ? " near " + near : ""}, closest first. ` +
        "Land and use the SRV wave scanner to collect."
      : `No mapped body within reach has ${data.min_share}% or more ${material}` +
        `${near ? " near " + near : ""} — lower the minimum concentration.`;
  } catch (err) {
    if (
      requestId !== materialBodyRequestId ||
      isStaleCommanderResponse(err) ||
      !appStore.isCurrent(identity)
    ) {
      return;
    }
    setVisible(table, false);
    status.classList.add("error");
    status.textContent = err instanceof Error ? err.message : String(err);
  } finally {
    if (requestId === materialBodyRequestId) go.disabled = false;
  }
}

appStore.onProfileChange(() => {
  clearMaterialBodies();
  mbSort = null;
  const go = /** @type {HTMLButtonElement|null} */ (byId("mf-go"));
  if (go) go.disabled = false;
  const wrap = byId("mf-bodies-wrap");
  if (wrap) setVisible(wrap, false);
});
