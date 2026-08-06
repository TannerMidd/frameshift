import { requireById } from "../core/dom.js";
import { copyText } from "../core/clipboard.js";
import { appStore } from "../core/store.js";
import { openExternal } from "../features/extensions.js";
import { launchGame } from "../features/launch.js";
import { cancelPlot, plotBusy, plotSystem } from "../features/plot.js";
import { findBestLoop } from "../features/routes.js";
import { applyCrtFx, applyDisplaySettings, applyTheme } from "../features/settings.js";
import {
  applyCardOrders,
  applyCardVisibility,
  migrateEngineeringLayout,
  setArrangeMode,
} from "./cards.js";
import { isFullscreen, syncFullscreenControl } from "./fullscreen.js";
import { initPanelNav, setPanelMode, toggleFullscreen } from "./panel.js";
import { setVoice, voiceOn } from "./voice.js";

let initialized = false;

/** @param {string} id */
function input(id) {
  return /** @type {HTMLInputElement} */ (requireById(id));
}

/**
 * @param {string} inputId
 */
function submitPlot(inputId) {
  if (plotBusy) {
    cancelPlot();
    return;
  }
  const name = input(inputId).value.trim();
  if (name) plotSystem(name);
}

/** Own the shared desktop/Panel shell controls. */
export function initializeShellControls() {
  if (initialized) return;
  initialized = true;

  initPanelNav();
  requireById("panel-toggle").addEventListener("click", () => setPanelMode(true));
  requireById("panel-exit").addEventListener("click", () => setPanelMode(false));
  requireById("fp-plot-form").addEventListener("submit", (event) => {
    event.preventDefault();
    submitPlot("fp-plot-input");
  });
  requireById("plot-form").addEventListener("submit", (event) => {
    event.preventDefault();
    submitPlot("plot-input");
  });
  requireById("fp-bestloop").addEventListener("click", findBestLoop);
  requireById("fp-voice").addEventListener("click", () => setVoice(!voiceOn, true));
  setVoice(voiceOn);
  requireById("fp-full").addEventListener("click", toggleFullscreen);
  // Only fires for the DOM path a real browser takes; the desktop window
  // reports its own state instead. Both land on the same function, so the
  // button reads correctly whichever one moved it.
  document.addEventListener("fullscreenchange", () =>
    syncFullscreenControl(Boolean(document.fullscreenElement)),
  );
  // The window can open straight into a remembered fullscreen, well before this
  // button exists to be labelled.
  syncFullscreenControl(isFullscreen());

  applyTheme();
  applyCrtFx();
  applyDisplaySettings();
  migrateEngineeringLayout();
  applyCardOrders();
  applyCardVisibility();
  const toggleArrange = () => setArrangeMode(!document.body.classList.contains("arranging"));
  requireById("arrange-btn").addEventListener("click", toggleArrange);
  requireById("fp-arrange").addEventListener("click", toggleArrange);

  const systemCopy = document.querySelector('[data-copy-target="system"]');
  if (!systemCopy) throw new Error("Required system copy control is missing.");
  systemCopy.addEventListener("click", (event) => {
    const system = appStore.getSnapshot()?.system;
    if (system) void copyText(system, event.currentTarget);
  });
  requireById("station-copy").addEventListener("click", (event) => {
    const station = appStore.getSnapshot()?.station;
    if (station) void copyText(station, event.currentTarget);
  });
  requireById("launch-game").addEventListener("click", launchGame);

  const inAppToggle = input("inapp-toggle");
  inAppToggle.checked = localStorage.getItem("inappLinks") === "1";
  inAppToggle.addEventListener("change", () =>
    localStorage.setItem("inappLinks", inAppToggle.checked ? "1" : "0"),
  );
  const showToggle = () => requireById("inapp-toggle-wrap").classList.remove("hidden");
  if ("pywebview" in window && window.pywebview) showToggle();
  window.addEventListener("pywebviewready", showToggle);

  document.addEventListener("click", (event) => {
    if (!(event.target instanceof Element)) return;
    const anchor = event.target.closest("a.extlink");
    if (anchor instanceof HTMLAnchorElement && openExternal(anchor.href, anchor.textContent)) {
      event.preventDefault();
    }
  });
}
