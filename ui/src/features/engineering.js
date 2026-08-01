/**
 * @import {
 *   EngineeringCatalogGroup,
 *   EngineeringCatalogStats,
 *   EngineeringPinRequest,
 *   EngineeringWishlist,
 *   EngineeringWishlistItem,
 * } from "../api/contracts/engineering.js"
 */

import { byId, requireById, setText } from "../core/dom.js";
import { clear, html, render } from "../core/html.js";
import { appStore } from "../core/store.js";
import { isStaleCommanderResponse } from "../api/errors.js";
import { engineeringApi } from "../api/engineering.js";
import { renderEngineeringPlans } from "./engineering-plans.js";
import { setMaterialReference } from "./material-finder.js";

/** @type {EngineeringCatalogGroup[]} */
export let engCatalog = [];
/** @type {Map<string, EngineeringCatalogGroup>} */
export let engCatalogById = new Map();
/** @type {Record<string, string>} */
export let engKindLabels = {};

let engineeringLoadRequest = 0;
let engineeringMutationRequest = 0;

/**
 * @typedef {{
 *   display_name: string,
 *   module: string,
 *   name: string,
 *   kind: string,
 *   kind_label: string,
 *   engineers?: string[],
 * }} EngineeringSearchItem
 */

/** @param {string} id */
function element(id) {
  return /** @type {HTMLElement} */ (requireById(id));
}

/** @param {string} id */
function input(id) {
  return /** @type {HTMLInputElement} */ (requireById(id));
}

/** @param {string} id */
function select(id) {
  return /** @type {HTMLSelectElement} */ (requireById(id));
}

/** @param {string} id */
function button(id) {
  return /** @type {HTMLButtonElement} */ (requireById(id));
}

/** @param {unknown} error */
function errorMessage(error) {
  return error instanceof Error ? error.message : String(error);
}

export function clearEngineeringWorkspace() {
  engineeringLoadRequest += 1;
  engineeringMutationRequest += 1;
  engCatalog = [];
  engCatalogById = new Map();
  engKindLabels = {};
  for (const id of ["engplan-list", "engplan-materials", "engplan-summary"]) {
    byId(id)?.replaceChildren();
  }
  const catalogSelect = /** @type {HTMLSelectElement|null} */ (byId("ep-blueprint"));
  catalogSelect?.replaceChildren();
  const kindSelect = /** @type {HTMLSelectElement|null} */ (byId("ep-kind"));
  if (kindSelect) {
    while (kindSelect.options.length > 1) kindSelect.remove(1);
    kindSelect.value = "";
  }
  const search = /** @type {HTMLInputElement|null} */ (byId("ep-search"));
  if (search) search.value = "";
  const pin = /** @type {HTMLButtonElement|null} */ (byId("ep-pin"));
  if (pin) {
    pin.disabled = true;
    pin.textContent = "ADD TO WISHLIST";
  }
  for (const id of ["ep-match-count", "ep-catalog-count"]) {
    const target = byId(id);
    if (target) setText(target, "");
  }
}

appStore.onProfileChange(clearEngineeringWorkspace);

export async function loadEngineering() {
  const summary = /** @type {HTMLElement|null} */ (byId("engplan-summary"));
  const identity = appStore.identity();
  const requestId = ++engineeringLoadRequest;
  if (!identity.commanderId) {
    if (summary) {
      render(
        summary,
        html`<div class="dim ep-api-error">Waiting for the commander profile...</div>`,
      );
    }
    return;
  }
  try {
    const data = await engineeringApi.getWorkshop();
    if (
      requestId !== engineeringLoadRequest ||
      !appStore.isCurrent(identity) ||
      data.commander_id !== identity.commanderId
    ) {
      return;
    }
    const localCatalog = data.catalog;
    engCatalog = (localCatalog.groups || []).filter((item) => !item.alias_of);
    engCatalogById = new Map(engCatalog.map((item) => [item.id, item]));
    engKindLabels = localCatalog.kind_labels || {};
    setMaterialReference(localCatalog);
    const catalogStats = localCatalog.stats || {};
    fillEngineeringKinds(catalogStats);
    fillEngineeringCatalog();
    const wishlist =
      data.wishlist ||
      /** @type {EngineeringWishlist} */ ({
        items: data.pinned || [],
        materials: [],
      });
    renderEngPlans(wishlist);
    setText(
      "ep-catalog-count",
      `${engCatalog.length.toLocaleString()} items · ${(catalogStats.recipes || 0).toLocaleString()} recipes`,
    );
  } catch (error) {
    if (
      requestId !== engineeringLoadRequest ||
      isStaleCommanderResponse(error) ||
      !appStore.isCurrent(identity)
    ) {
      return;
    }
    if (summary) {
      render(
        summary,
        html`<div class="warn ep-api-error">
          Engineering planner unavailable: ${errorMessage(error)}
        </div>`,
      );
    }
  }
}

/** @param {EngineeringCatalogStats} [stats] */
export function fillEngineeringKinds(stats = {}) {
  const kindSelect = /** @type {HTMLSelectElement|null} */ (byId("ep-kind"));
  if (!kindSelect || kindSelect.options.length > 1) return;
  const counts = stats.categories || {};
  for (const [kind, label] of Object.entries(engKindLabels)) {
    if (!counts[kind]) continue;
    const option = document.createElement("option");
    option.value = kind;
    option.textContent = `${label} (${counts[kind]})`;
    kindSelect.appendChild(option);
  }
}

/**
 * @param {EngineeringSearchItem} item
 * @param {string} query
 * @param {string} kind
 */
export function engineeringMatches(item, query, kind) {
  if (kind && item.kind !== kind) return false;
  const haystack = [
    item.display_name,
    item.module,
    item.name,
    item.kind_label,
    ...(item.engineers || []),
  ]
    .join(" ")
    .toLowerCase();
  return query
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)
    .every((word) => haystack.includes(word));
}

/** @param {string} [preferredId] */
export function fillEngineeringCatalog(preferredId) {
  const catalogSelect = /** @type {HTMLSelectElement|null} */ (byId("ep-blueprint"));
  if (!catalogSelect) return;
  const previous = preferredId || catalogSelect.value;
  const query = /** @type {HTMLInputElement|null} */ (byId("ep-search"))?.value.trim() || "";
  const kind = /** @type {HTMLSelectElement|null} */ (byId("ep-kind"))?.value || "";
  const matches = engCatalog.filter((item) => engineeringMatches(item, query, kind));
  clear(catalogSelect);
  /** @type {Map<string, EngineeringCatalogGroup[]>} */
  const groups = new Map();
  for (const item of matches) {
    const label = item.kind_label || item.kind;
    const items = groups.get(label);
    if (items) items.push(item);
    else groups.set(label, [item]);
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
    catalogSelect.appendChild(optgroup);
  }
  if (matches.some((item) => item.id === previous)) catalogSelect.value = previous;
  setText(
    "ep-match-count",
    `${matches.length} matching ${matches.length === 1 ? "recipe" : "recipes"}`,
  );
  button("ep-pin").disabled = !matches.length;
  updateEngineeringGradeFields();
}

/**
 * @param {HTMLSelectElement} gradeSelect
 * @param {string|number} value
 * @param {string} label
 */
export function addGradeOption(gradeSelect, value, label) {
  const option = document.createElement("option");
  option.value = String(value);
  option.textContent = label;
  gradeSelect.appendChild(option);
}

/**
 * @param {number} [preferredCurrent]
 * @param {number} [preferredTarget]
 */
export function updateEngineeringGradeFields(preferredCurrent, preferredTarget) {
  const item = engCatalogById.get(select("ep-blueprint").value);
  const currentWrap = element("ep-current-wrap");
  const targetWrap = element("ep-target-wrap");
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
  const maximumGrade = grades[grades.length - 1];
  if (isClimb && maximumGrade != null) {
    const target = select("ep-target");
    const oldTarget = Number(preferredTarget ?? maximumGrade);
    clear(target);
    for (const grade of grades) {
      addGradeOption(target, grade, `Grade ${grade}${grade === maximumGrade ? " (max)" : ""}`);
    }
    target.value = String(grades.includes(oldTarget) ? oldTarget : maximumGrade);
    const targetGrade = Number(target.value);
    const current = select("ep-current");
    const firstGrade = grades[0] ?? 0;
    const defaultCurrent = item.kind === "odyssey-upgrade" ? Math.max(0, firstGrade - 1) : 0;
    const oldCurrent = Number(preferredCurrent ?? defaultCurrent);
    clear(current);
    addGradeOption(
      current,
      defaultCurrent,
      defaultCurrent ? `Grade ${defaultCurrent}` : "Stock / unengineered",
    );
    for (const grade of grades.filter((grade) => grade < targetGrade && grade > defaultCurrent)) {
      addGradeOption(current, grade, `Grade ${grade}`);
    }
    const valid = [...current.options].some((option) => Number(option.value) === oldCurrent);
    current.value = String(valid ? oldCurrent : defaultCurrent);
  }
  const access = (item.engineer_access || []).map(
    (engineer) => `${engineer.name}${engineer.max_grade ? ` G${engineer.max_grade}` : ""}`,
  );
  const engineerText = access.length ? ` · ${access.join(", ")}` : "";
  const gradeText = !isClimb && grades.length ? ` · tier G${grades.join("/")}` : "";
  render(
    element("ep-desc"),
    html`<b>${item.kind_label || item.kind}</b> · ${item.module}${gradeText}${engineerText}`,
  );
}

/**
 * @param {{kind: string, current_grade?: number, target_grade?: number, id?: string}} item
 */
export function engineeringGradeText(item) {
  if (item.kind === "ship-engineering" || item.kind === "odyssey-upgrade") {
    const from = item.current_grade ? `G${item.current_grade}` : "stock";
    return `${from} → G${item.target_grade ?? "?"}`;
  }
  const catalogItem = item.id ? engCatalogById.get(item.id) : null;
  return catalogItem && catalogItem.grades.length
    ? `G${catalogItem.grades.join("/")} recipe`
    : "exact recipe";
}

/** @param {EngineeringWishlist} wishlist */
export function renderEngPlans(wishlist) {
  renderEngineeringPlans(wishlist, {
    gradeText: engineeringGradeText,
    edit: editEngineeringItem,
    remove(id) {
      void pinBlueprint({ id, action: "unpin" });
    },
  });
}

/** @param {EngineeringWishlistItem} item */
export function editEngineeringItem(item) {
  input("ep-search").value = "";
  select("ep-kind").value = item.kind;
  fillEngineeringCatalog(item.id);
  select("ep-blueprint").value = item.id;
  updateEngineeringGradeFields(item.current_grade, item.target_grade);
  input("ep-quantity").value = String(item.quantity);
  button("ep-pin").textContent = "UPDATE WISHLIST";
  element("engplan-form").scrollIntoView({ behavior: "smooth", block: "center" });
}

/** @param {EngineeringPinRequest} item */
export async function pinBlueprint(item) {
  const identity = appStore.identity();
  const requestId = ++engineeringMutationRequest;
  if (!identity.commanderId) {
    render(
      element("engplan-summary"),
      html`<div class="dim ep-api-error">Waiting for the commander profile...</div>`,
    );
    return;
  }
  try {
    await engineeringApi.setPinnedBlueprint(item);
    if (requestId !== engineeringMutationRequest || !appStore.isCurrent(identity)) return;
    button("ep-pin").textContent = "ADD TO WISHLIST";
    await loadEngineering();
  } catch (error) {
    if (
      requestId !== engineeringMutationRequest ||
      isStaleCommanderResponse(error) ||
      !appStore.isCurrent(identity)
    ) {
      return;
    }
    render(
      element("engplan-summary"),
      html`<div class="warn ep-api-error">Could not update wishlist: ${errorMessage(error)}</div>`,
    );
  }
}
