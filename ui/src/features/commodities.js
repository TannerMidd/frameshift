import { isStaleCommanderResponse } from "../api/errors.js";
import { marketApi } from "../api/market.js";
import { byId, requireById } from "../core/dom.js";
import { fmtNum } from "../core/fmt.js";
import { clear, html, render } from "../core/html.js";
import { appStore } from "../core/store.js";
import { copySystemButton, plotButton } from "../shell/status.js";
import { getGalaxyHistory } from "./galaxy-history.js";
import { SUGGEST_MODULES, SUGGEST_SHIPS } from "./commodity-suggestions.js";

export { SUGGEST_MODULES, SUGGEST_SHIPS };

/** @import {CommoditySearchQuery, CommoditySearchResult} from "../api/contracts/market.js" */

/** @typedef {{key: string, dir: number}} SortState */
/** @template T @typedef {{value: (row: T, mode?: string) => string|number, firstDir: number|null}} SortColumn */
/**
 * @typedef {{
 *   score?: unknown,
 *   band?: unknown,
 *   age_s?: unknown,
 *   source?: unknown,
 *   reasons?: unknown,
 * }} ConfidenceInput
 */

export async function loadCommodityList() {
  try {
    const data = await marketApi.listCommodities();
    const list = /** @type {HTMLDataListElement} */ (requireById("commodity-list"));
    clear(list);
    for (const commodity of data.commodities || []) {
      const option = document.createElement("option");
      option.value = commodity.name;
      list.appendChild(option);
    }
    if (!list.children.length) setTimeout(loadCommodityList, 30000);
  } catch {
    setTimeout(loadCommodityList, 30000);
  }
}

/** @param {number|null|undefined} epoch */
export function ageText(epoch) {
  if (!epoch) return "?";
  const minutes = Math.max(0, (Date.now() / 1000 - epoch) / 60);
  if (minutes < 60) return `${Math.round(minutes)}m`;
  if (minutes < 48 * 60) return `${Math.round(minutes / 60)}h`;
  return `${Math.round(minutes / 1440)}d`;
}

/** @param {unknown} seconds */
export function confidenceAgeLabel(seconds) {
  if (seconds == null || !Number.isFinite(Number(seconds))) return "age unknown";
  const value = Math.max(0, Number(seconds));
  if (value < 3600) return `${Math.max(1, Math.round(value / 60))}m old`;
  if (value < 172800) return `${Math.round(value / 3600)}h old`;
  return `${Math.round(value / 86400)}d old`;
}

/** @param {ConfidenceInput|null|undefined} confidence @returns {HTMLSpanElement|null} */
export function confidenceBadge(confidence) {
  if (!confidence) return null;
  const allowed = new Set(["high", "medium", "low"]);
  const candidate = String(confidence.band).toLowerCase();
  const band = allowed.has(candidate) ? candidate : "low";
  const score = Number.isFinite(Number(confidence.score))
    ? Math.round(Number(confidence.score))
    : "?";
  const reasons =
    Array.isArray(confidence.reasons) && confidence.reasons.length
      ? confidence.reasons.join("; ")
      : "no material freshness or depth warning";
  const detail = `${confidence.source || "market observation"}; ${confidenceAgeLabel(confidence.age_s)}; ${reasons}`;
  const badge = document.createElement("span");
  badge.className = `confidence confidence-${band}`;
  badge.title = detail;
  badge.textContent = `${band.toUpperCase()} ${score}`;
  return badge;
}

/**
 * @param {unknown} range
 * @param {string} [suffix]
 * @returns {HTMLSpanElement|null}
 */
export function creditRangeBadge(range, suffix = "cr") {
  if (!range || typeof range !== "object") return null;
  const low = Reflect.get(range, "low");
  const observed = Reflect.get(range, "observed");
  if (low == null || observed == null) return null;
  const badge = document.createElement("span");
  badge.className = "risk-range";
  badge.textContent = `conservative ${fmtNum(low)}–${fmtNum(observed)} ${suffix}`;
  return badge;
}

/** @param {string} id @param {string} kind */
export function attachSuggest(id, kind) {
  const input = /** @type {HTMLInputElement|null} */ (byId(id));
  if (!input) return;
  const list = document.createElement("datalist");
  list.id = `suggest-${id}`;
  document.body.appendChild(list);
  input.setAttribute("list", list.id);
  input.removeAttribute("autocomplete");

  /** @type {ReturnType<typeof setTimeout>|null} */
  let timer = null;
  let sequence = 0;
  input.addEventListener("input", () => {
    if (timer != null) clearTimeout(timer);
    timer = setTimeout(async () => {
      const query = input.value.trim();
      if (query.length < 2) {
        list.replaceChildren();
        return;
      }
      const requestSequence = ++sequence;
      /** @type {string[]} */
      let names = [];
      try {
        names = (await marketApi.suggest(kind, query)).suggestions || [];
      } catch {
        // Suggestions are best-effort.
      }
      if (requestSequence !== sequence) return;
      if (kind === "systems") {
        const lower = query.toLowerCase();
        const recentSystems = getGalaxyHistory()
          .map((entry) => entry.system)
          .filter((system) => system.toLowerCase().startsWith(lower));
        names = [...new Set([...recentSystems.slice(0, 3), ...names])].slice(0, 12);
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

export function initSuggest() {
  for (const id of [
    "fp-plot-input",
    "plot-input",
    "nr-to",
    "ss-system",
    "cs-near",
    "mn-near",
    "mf-near",
    "os-near",
    "ops-objective-system",
    "ops-board-objective-system",
  ]) {
    attachSuggest(id, "systems");
  }
  for (const id of ["ops-objective-station", "ops-board-objective-station"]) {
    attachSuggest(id, "stations");
  }

  const outfitting = /** @type {HTMLInputElement|null} */ (byId("os-query"));
  if (outfitting) {
    const list = document.createElement("datalist");
    list.id = "suggest-os-query";
    for (const name of [...SUGGEST_MODULES, ...SUGGEST_SHIPS]) {
      const option = document.createElement("option");
      option.value = name;
      list.appendChild(option);
    }
    document.body.appendChild(list);
    outfitting.setAttribute("list", list.id);
    outfitting.removeAttribute("autocomplete");
  }
  const reservation = /** @type {HTMLInputElement|null} */ (byId("ops-reservation-key"));
  reservation?.setAttribute("list", "commodity-list");
}

/**
 * @template T
 * @param {T[]} results
 * @param {Record<string, SortColumn<T>>} columns
 * @param {SortState|null|undefined} sort
 * @param {string} [mode]
 * @returns {T[]}
 */
export function sortedRows(results, columns, sort, mode) {
  if (!sort) return results;
  const column = columns[sort.key];
  if (!column) return results;
  const { value } = column;
  return [...results].sort((left, right) => {
    const leftValue = value(left, mode);
    const rightValue = value(right, mode);
    if (leftValue < rightValue) return -sort.dir;
    if (leftValue > rightValue) return sort.dir;
    return 0;
  });
}

/** @param {Element} table @param {SortState|null|undefined} sort */
export function updateSortIndicators(table, sort) {
  for (const candidate of table.querySelectorAll("th.sortable")) {
    if (!(candidate instanceof HTMLTableCellElement)) continue;
    candidate.classList.toggle(
      "sort-asc",
      !!sort && candidate.dataset.sort === sort.key && sort.dir === 1,
    );
    candidate.classList.toggle(
      "sort-desc",
      !!sort && candidate.dataset.sort === sort.key && sort.dir === -1,
    );
  }
}

/**
 * @param {SortState|null|undefined} sort
 * @param {string} key
 * @param {Record<string, {firstDir: number|null}>} columns
 * @param {number} [fallbackDir]
 * @returns {SortState}
 */
export function bumpSort(sort, key, columns, fallbackDir = 1) {
  if (sort?.key === key) return { key, dir: -sort.dir };
  const firstDir = columns[key]?.firstDir;
  return { key, dir: firstDir != null ? firstDir : fallbackDir };
}

/** @param {string} tableId @param {(key: string) => void} onSort */
export function sortableHeaders(tableId, onSort) {
  const table = requireById(tableId);
  table.querySelector("thead")?.addEventListener("click", (event) => {
    if (!(event.target instanceof Element)) return;
    const heading = event.target.closest("th.sortable");
    if (heading instanceof HTMLTableCellElement && heading.dataset.sort) {
      onSort(heading.dataset.sort);
    }
  });
}

/** @type {{results: CommoditySearchResult[], mode: "buy"|"sell"}|null} */
export let csResults = null;
/** @type {SortState|null} */
export let csSort = null;
export let csSortTouched = false;
let commoditySearchRequestId = 0;

/** @type {Record<string, SortColumn<CommoditySearchResult>>} */
export const CS_SORT_COLUMNS = {
  station: { value: (row) => row.station.toLowerCase(), firstDir: 1 },
  system: { value: (row) => row.system.toLowerCase(), firstDir: 1 },
  price: {
    value: (row, mode) => (mode === "buy" ? row.buy_price : row.sell_price) || 0,
    firstDir: null,
  },
  units: {
    value: (row, mode) => (mode === "buy" ? row.supply : row.demand) || 0,
    firstDir: -1,
  },
  jump: {
    value: (row) => (row.distance != null ? Number(row.distance) : Infinity),
    firstDir: 1,
  },
  dist_ls: {
    value: (row) => (row.dist_ls != null ? Number(row.dist_ls) : Infinity),
    firstDir: 1,
  },
  updated: {
    value: (row) => Number(row.updated_at) || Date.parse(String(row.updated_at)) || 0,
    firstDir: -1,
  },
};

export function renderCommodityRows() {
  const table = /** @type {HTMLTableElement} */ (requireById("cs-table"));
  const body = /** @type {HTMLTableSectionElement} */ (table.tBodies[0]);
  if (!csResults) return;
  const { results, mode } = csResults;
  updateSortIndicators(table, csSort);
  clear(body);

  for (const result of sortedRows(results, CS_SORT_COLUMNS, csSort, mode)) {
    const row = document.createElement("tr");
    const price = mode === "buy" ? result.buy_price : result.sell_price;
    const units = mode === "buy" ? result.supply : result.demand;
    render(
      row,
      html`<td>
          ${result.station}${result.large_pad ? "" : html` <span class="sub">no L pad</span>`}
        </td>
        <td>${result.system}</td>
        <td class="num orange">${fmtNum(price)}</td>
        <td class="num">${fmtNum(units)}</td>
        <td class="num">${result.distance} ly</td>
        <td class="num">${result.dist_ls != null ? fmtNum(result.dist_ls) + " ls" : "?"}</td>
        <td class="num freshness-cell">${ageText(result.updated_at)}</td>`,
    );
    const freshnessCell = row.querySelector(".freshness-cell");
    const badge = confidenceBadge(result.confidence);
    if (badge && freshnessCell) freshnessCell.appendChild(badge);
    const actions = document.createElement("td");
    actions.appendChild(copySystemButton(result.system));
    actions.appendChild(plotButton(result.system));
    row.appendChild(actions);
    body.appendChild(row);
  }
}

/** @param {string} key */
export function sortCommodityTable(key) {
  if (!CS_SORT_COLUMNS[key] || !csResults) return;
  csSortTouched = true;
  csSort = bumpSort(csSort, key, CS_SORT_COLUMNS, csResults.mode === "buy" ? 1 : -1);
  renderCommodityRows();
}

/** @param {Event} event */
export async function searchCommodity(event) {
  event.preventDefault();
  const identity = appStore.identity();
  const status = /** @type {HTMLElement} */ (requireById("cs-status"));
  const table = /** @type {HTMLTableElement} */ (requireById("cs-table"));
  const go = /** @type {HTMLButtonElement} */ (requireById("cs-go"));
  /** @type {"buy"|"sell"} */
  const mode =
    /** @type {HTMLSelectElement} */ (requireById("cs-mode")).value === "buy" ? "buy" : "sell";
  const near = /** @type {HTMLInputElement} */ (requireById("cs-near")).value.trim();
  if (!identity.commanderId) {
    status.classList.add("error");
    status.textContent = "Waiting for the commander profile...";
    return;
  }
  const requestId = ++commoditySearchRequestId;
  go.disabled = true;
  status.classList.remove("error");
  status.textContent = "Searching…";
  try {
    const radius = /** @type {HTMLInputElement} */ (requireById("cs-radius")).value;
    /** @type {CommoditySearchQuery} */
    const params = {
      q: /** @type {HTMLInputElement} */ (requireById("cs-query")).value.trim(),
      mode,
      radius: radius || "50",
      min_units: /** @type {HTMLInputElement} */ (requireById("cs-min")).value || "1",
      large_pad: /** @type {HTMLInputElement} */ (requireById("cs-largepad")).checked ? "1" : "0",
      system: near || undefined,
    };
    const data = await marketApi.searchCommodities(params);
    if (!appStore.isCurrent(identity)) return;
    csResults = { results: data.results || [], mode };
    if (!csSortTouched) csSort = { key: "price", dir: mode === "buy" ? 1 : -1 };
    renderCommodityRows();
    table.classList.toggle("hidden", !csResults.results.length);
    const where = near ? ` of ${near}` : "";
    status.textContent = csResults.results.length
      ? `${csResults.results.length} station(s) ${mode === "buy" ? "selling" : "buying"} ${data.commodity} within ${radius} ly${where}. Click a column header to sort.`
      : `Nothing ${mode === "buy" ? "selling" : "buying"} ${data.commodity || "that"} ${near ? "near " + near : "nearby"} with those filters — widen the radius, or if you're deep in the black, WHERE TO SELL YOUR DATA (Explore tab) finds the nearest civilization.`;
  } catch (error) {
    if (isStaleCommanderResponse(error) || !appStore.isCurrent(identity)) return;
    table.classList.add("hidden");
    status.classList.add("error");
    status.textContent = error instanceof Error ? error.message : String(error);
  } finally {
    if (requestId === commoditySearchRequestId) go.disabled = false;
  }
}
