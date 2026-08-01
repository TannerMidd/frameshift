/** @import {MaterialTrader} from "../api/contracts/engineering.js" */

import { byId, requireById, setVisible } from "../core/dom.js";
import { fmtNum } from "../core/fmt.js";
import { clear, html, render } from "../core/html.js";
import { appStore } from "../core/store.js";
import { isStaleCommanderResponse } from "../api/errors.js";
import { engineeringApi } from "../api/engineering.js";
import { bumpSort, sortedRows, updateSortIndicators } from "./commodities.js";
import { copySystemButton, plotButton } from "../shell/status.js";

/** @typedef {MaterialTrader & {kind: string}} KindedTrader */

/** @type {KindedTrader[]|null} */
export let mtResults = null;

/** @type {{key: string, dir: number}|null} */
export let mtSort = null;

let materialTraderRequestId = 0;

/** @type {Record<string, {value: (row: KindedTrader) => string|number, firstDir: number}>} */
export const MT_SORT_COLUMNS = {
  station: { value: (r) => (r.station || "").toLowerCase(), firstDir: 1 },
  system: { value: (r) => (r.system || "").toLowerCase(), firstDir: 1 },
  kind: { value: (r) => (r.kind || "").toLowerCase(), firstDir: 1 },
  jump: { value: (r) => (r.distance != null ? Number(r.distance) : Infinity), firstDir: 1 },
  dist_ls: { value: (r) => (r.dist_ls != null ? Number(r.dist_ls) : Infinity), firstDir: 1 },
  pad: { value: (r) => (r.large_pad ? 0 : 1), firstDir: 1 },
};

export function renderMaterialTraderRows() {
  const table = /** @type {HTMLTableElement} */ (requireById("mf-traders-table"));
  const tbody = /** @type {HTMLTableSectionElement} */ (table.querySelector("tbody"));
  if (!mtResults) return;
  updateSortIndicators(table, mtSort);
  clear(tbody);
  for (const r of sortedRows(mtResults, MT_SORT_COLUMNS, mtSort)) {
    const tr = document.createElement("tr");
    render(
      tr,
      html`<td>
          <b>${r.station}</b>${r.carrier ? html` <span class="sub">fleet carrier</span>` : ""}
        </td>
        <td>${r.system}</td>
        <td><span class="chip mf-kind-chip">${r.kind}</span></td>
        <td class="num">${r.distance} ly</td>
        <td class="num">${r.dist_ls != null ? fmtNum(Math.round(r.dist_ls)) + " ls" : "—"}</td>
        <td>${r.large_pad ? "L" : "M"}</td>`,
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
export function sortMaterialTraders(key) {
  if (!MT_SORT_COLUMNS[key] || !mtResults) return;
  mtSort = bumpSort(mtSort, key, MT_SORT_COLUMNS);
  renderMaterialTraderRows();
}

/**
 * Nearest material traders for one or more kinds.
 *
 * Takes its arguments explicitly rather than reading the finder's form, so the
 * dependency runs one way and this table stays independently testable.
 *
 * @param {{kinds: string[], near?: string}} options
 */
export async function searchMaterialTraders({ kinds, near }) {
  const identity = appStore.identity();
  const status = /** @type {HTMLElement} */ (requireById("mf-traders-status"));
  const table = /** @type {HTMLTableElement} */ (requireById("mf-traders-table"));
  const go = /** @type {HTMLButtonElement} */ (requireById("mf-go"));
  if (!identity.commanderId) {
    status.classList.add("error");
    status.textContent = "Waiting for the commander profile...";
    return;
  }
  const requestId = ++materialTraderRequestId;
  go.disabled = true;
  status.classList.remove("error");
  status.textContent = "Finding the nearest material traders… (~5s)";
  try {
    const results = await Promise.all(
      kinds.map(async (kind) => {
        try {
          const data = await engineeringApi.findMaterialTraders(kind, near || undefined);
          return (data.traders || []).map((trader) => ({ ...trader, kind }));
        } catch (error) {
          if (isStaleCommanderResponse(error)) throw error;
          return [];
        }
      }),
    );
    if (requestId !== materialTraderRequestId || !appStore.isCurrent(identity)) return;
    mtResults = results.flat();
    if (!mtSort) mtSort = { key: "jump", dir: 1 };
    renderMaterialTraderRows();
    setVisible(table, mtResults.length > 0);
    status.textContent = mtResults.length
      ? `${mtResults.length} material trader${mtResults.length === 1 ? "" : "s"}` +
        `${near ? " near " + near : " nearby"}, closest first.`
      : `No material traders found${near ? " near " + near : " nearby"} — ` +
        "they sit in populated space, so head back towards the bubble.";
  } catch (err) {
    if (
      requestId !== materialTraderRequestId ||
      isStaleCommanderResponse(err) ||
      !appStore.isCurrent(identity)
    ) {
      return;
    }
    setVisible(table, false);
    status.classList.add("error");
    status.textContent = err instanceof Error ? err.message : String(err);
  } finally {
    if (requestId === materialTraderRequestId) go.disabled = false;
  }
}

appStore.onProfileChange(() => {
  materialTraderRequestId += 1;
  mtResults = null;
  mtSort = null;
  const go = /** @type {HTMLButtonElement|null} */ (byId("mf-go"));
  if (go) go.disabled = false;
  const table = /** @type {HTMLTableElement|null} */ (byId("mf-traders-table"));
  table?.querySelector("tbody")?.replaceChildren();
  if (table) setVisible(table, false);
  const status = /** @type {HTMLElement|null} */ (byId("mf-traders-status"));
  if (status) {
    status.classList.remove("error");
    status.textContent = "";
  }
  const wrap = byId("mf-traders-wrap");
  if (wrap) setVisible(wrap, false);
});
