import { requireById } from "../../core/dom.js";
import {
  fillEngineeringCatalog,
  loadEngineering,
  pinBlueprint,
  updateEngineeringGradeFields,
} from "../engineering.js";
import {
  renderMaterialAnswer,
  runMaterialFinder,
  showAllMaterialTraders,
} from "../material-finder.js";
import { sortMaterialTraders } from "../material-traders.js";
import { sortMaterialBodies } from "../material-bodies.js";
import { sortableHeaders } from "../commodities.js";
import { persistForm } from "../../core/form-persistence.js";

let initialized = false;

/** @param {string} id */
function valueControl(id) {
  return /** @type {HTMLInputElement|HTMLSelectElement} */ (requireById(id));
}

/** Own Engineering pane forms and initial load. */
export function initializeEngineeringControls() {
  if (initialized) return;
  initialized = true;

  requireById("engplan-form").addEventListener("submit", (event) => {
    event.preventDefault();
    pinBlueprint({
      id: valueControl("ep-blueprint").value,
      current_grade: Number(valueControl("ep-current").value) || 0,
      target_grade: Number(valueControl("ep-target").value) || 0,
      quantity: Number(valueControl("ep-quantity").value) || 1,
    });
  });
  requireById("ep-search").addEventListener("input", () => fillEngineeringCatalog());
  requireById("ep-kind").addEventListener("change", () => fillEngineeringCatalog());
  requireById("ep-blueprint").addEventListener("change", () => updateEngineeringGradeFields());
  requireById("ep-target").addEventListener("change", () =>
    updateEngineeringGradeFields(
      Number(valueControl("ep-current").value),
      Number(valueControl("ep-target").value),
    ),
  );
  requireById("ep-traders").addEventListener("click", showAllMaterialTraders);

  requireById("mf-form").addEventListener("submit", runMaterialFinder);
  // The column, trades and drop sources resolve offline, so answer as they type.
  requireById("mf-material").addEventListener("input", () => renderMaterialAnswer());
  sortableHeaders("mf-traders-table", sortMaterialTraders);
  sortableHeaders("mf-bodies-table", sortMaterialBodies);
  // "Near" is deliberately excluded: a stale location must not outlive a session.
  persistForm("mf-form", "materialFinder", ["mf-material", "mf-minshare"]);
  // Answer the restored material whichever lands first, this or the catalog.
  renderMaterialAnswer();

  loadEngineering();
}
