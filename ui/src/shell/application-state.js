import { systemApi } from "../api/system.js";
import { isUnauthorizedResponse } from "../api/errors.js";
import { appStore } from "../core/store.js";
import { byId, requireById } from "../core/dom.js";
import { fmtCr } from "../core/fmt.js";
import { html, render as renderHtml } from "../core/html.js";
import { handleAlerts, renderBanner, renderLinks } from "../features/alerts.js";
import { clearAnalyticsWorkspace, loadAnalytics } from "../features/analytics.js";
import { renderBio } from "../features/bio.js";
import { renderColonisation, resetColonisationWorkspace } from "../features/colonisation.js";
import { renderMassacre } from "../features/combat.js";
import { loadEngineering } from "../features/engineering.js";
import { resetExtensionBuilderHistory } from "../features/extension-builder.js";
import { loadGalaxyHistory, renderGalaxy } from "../features/galaxy.js";
import { renderGameState } from "../features/launch.js";
import { renderCargo, renderJumps, renderMarket } from "../features/market.js";
import {
  renderCarrier,
  renderEngineers,
  renderMaterials,
  renderOdysseyLocker,
  renderStoredShips,
} from "../features/materials.js";
import {
  clearAlertWorkspace,
  loadActiveRoute,
  pollAlerts,
  profileStorageId,
  renderRouteProgress,
  saveActiveRoute,
  seedRouteForm,
  syncRouteToPosition,
} from "../features/routes.js";
import { enterPairingRequired } from "../features/security.js";
import { refreshLoadoutExport, resetLoadoutExport } from "../features/services.js";
import { renderMissions, renderSession } from "../features/status.js";
import { renderJumpSequence } from "../shell/jump-sequence.js";
import { renderRebuy } from "../shell/rebuy.js";
import { renderPanel } from "../shell/status.js";

/** @import {ApplicationState} from "../api/contracts/state.js" */

/** @type {null|(() => void)} */
let disposeProfileLifecycle = null;
/** @type {string|null} */
let engineeringInventorySignature = null;

/** @param {ApplicationState|null} [snapshot] */
export function renderApplicationSnapshot(snapshot = appStore.getSnapshot()) {
  if (!snapshot) return;

  requireById("commander").textContent = snapshot.commander ? `CMDR ${snapshot.commander}` : "—";
  const shipBits = [snapshot.ship_name, snapshot.ship_type].filter(Boolean);
  requireById("ship").textContent = shipBits.length ? shipBits.join(" · ") : "—";
  requireById("system").textContent = snapshot.system || "Unknown";

  const stationStatus = requireById("station-status");
  const stationCopy = requireById("station-copy");
  if (snapshot.docked && snapshot.station) {
    let text = `Docked at ${snapshot.station}`;
    if (snapshot.station_type) text += ` (${snapshot.station_type})`;
    if (snapshot.dist_from_star_ls != null) {
      text += ` · ${Math.round(snapshot.dist_from_star_ls)} ls`;
    }
    stationStatus.textContent = text;
    stationCopy.classList.remove("hidden");
  } else {
    stationStatus.textContent =
      snapshot.body && snapshot.body !== snapshot.system
        ? `In space near ${snapshot.body}`
        : "In space";
    stationCopy.classList.add("hidden");
  }

  requireById("destination-row").textContent = snapshot.destination
    ? `Destination: ${snapshot.destination}`
    : "";
  requireById("credits").textContent = fmtCr(snapshot.credits);
  const fuel =
    snapshot.fuel_main == null
      ? "—"
      : snapshot.fuel_main.toFixed(1) +
        (snapshot.fuel_capacity ? ` / ${snapshot.fuel_capacity.toFixed(0)}` : "") +
        " t";
  requireById("fuel").textContent = fuel;
  requireById("cargo").textContent =
    (snapshot.cargo_tons != null ? Math.round(snapshot.cargo_tons) : "—") +
    (snapshot.cargo_capacity ? ` / ${snapshot.cargo_capacity}` : "") +
    " t";
  requireById("legal").textContent = snapshot.legal_state || "—";
  renderRebuy(requireById("rebuy"), snapshot);

  renderGalaxyModeNotice(snapshot);
  renderBanner(snapshot);
  renderGameState(snapshot);
  handleAlerts(snapshot);
  renderLinks(snapshot);
  renderMarket();
  renderJumps();
  renderCargo();
  renderBio(snapshot);
  renderColonisation(snapshot);
  renderSession(snapshot.session);
  renderMissions(snapshot.missions, snapshot);
  renderMassacre(snapshot);
  renderMaterials(snapshot.materials);
  renderEngineers();
  renderStoredShips();
  renderOdysseyLocker();
  renderCarrier();
  renderGalaxy();
  refreshLoadoutExport();

  const nextEngineeringInventorySignature = JSON.stringify([
    snapshot.commander_id || null,
    snapshot.materials || null,
    snapshot.ship_locker || null,
    snapshot.cargo_inventory || null,
  ]);
  if (engineeringInventorySignature !== nextEngineeringInventorySignature) {
    engineeringInventorySignature = nextEngineeringInventorySignature;
    loadEngineering();
  }
  if (syncRouteToPosition()) saveActiveRoute();
  renderRouteProgress();
  renderPanel(snapshot);
  renderJumpSequence(snapshot);
  seedRouteForm();
}

/** @param {ApplicationState|null} [snapshot] */
export function renderGalaxyModeNotice(snapshot = appStore.getSnapshot()) {
  const banner = requireById("galaxy-mode-banner");
  const legacy = String(snapshot?.galaxy_mode || "live").toLowerCase() === "legacy";
  banner.classList.toggle("hidden", !legacy);
  if (legacy) {
    banner.textContent =
      "LEGACY GALAXY detected — commander history, engineering, objectives, and local specialist tools remain available. Live community market, routing, outfitting, and galaxy searches are disabled so Horizons 3.8 data cannot be mixed with the Live galaxy.";
  }
}

/**
 * @param {ApplicationState|null|undefined} nextSnapshot
 */
export function resetProfileWorkspaces(nextSnapshot) {
  const commanderId = profileStorageId(nextSnapshot);
  const commanderName = nextSnapshot?.commander || "";

  clearAnalyticsWorkspace();
  clearAlertWorkspace();
  resetColonisationWorkspace();
  resetExtensionBuilderHistory();
  resetLoadoutExport();
  loadActiveRoute(commanderId);
  loadGalaxyHistory(commanderId, commanderName || null);
  engineeringInventorySignature = null;
  for (const id of ["engplan-summary", "engplan-list", "engplan-materials", "mf-answer"]) {
    const element = byId(id);
    if (element) element.replaceChildren();
  }
  const engineeringSummary = byId("engplan-summary");
  if (engineeringSummary) {
    renderHtml(
      engineeringSummary,
      html`<div class="dim ep-api-error">
        ${
          commanderId
            ? "Loading this commander's engineering wishlist..."
            : "Waiting for a commander profile..."
        }
      </div>`,
    );
  }
  const engineeringForm = /** @type {HTMLFormElement|null} */ (byId("engplan-form"));
  engineeringForm?.reset();
  const pinButton = byId("ep-pin");
  if (pinButton) pinButton.textContent = "ADD TO WISHLIST";

  for (const id of ["galaxy-history-card", "galhistory-list", "powerplay-card", "factions-list"]) {
    const element = /** @type {HTMLElement|null} */ (byId(id));
    if (element) element.dataset.sig = "";
  }
  renderRouteProgress();

  if (commanderId) {
    loadEngineering();
    loadAnalytics();
    pollAlerts();
  }
}

/**
 * Subscribe the cross-feature profile reset at the composition boundary.
 *
 * @returns {() => void}
 */
export function initializeProfileLifecycle() {
  if (disposeProfileLifecycle) return disposeProfileLifecycle;
  const unsubscribe = appStore.onProfileChange(({ snapshot }) => {
    resetProfileWorkspaces(snapshot);
  });
  const dispose = () => {
    unsubscribe();
    if (disposeProfileLifecycle === dispose) disposeProfileLifecycle = null;
  };
  disposeProfileLifecycle = dispose;
  return dispose;
}

export async function pollApplicationState() {
  try {
    const nextState = await systemApi.getState();
    appStore.setSnapshot(nextState);
    renderApplicationSnapshot(nextState);
  } catch (error) {
    if (isUnauthorizedResponse(error)) {
      const message = error instanceof Error ? error.message : "";
      enterPairingRequired(
        message || "This device's access was revoked or expired. Pair it again from the gaming PC.",
      );
      setTimeout(pollApplicationState, 1500);
      return;
    }
    const link = byId("fp-link");
    if (link) link.textContent = "LINK · RETRYING";
  }
  setTimeout(pollApplicationState, 1500);
}
