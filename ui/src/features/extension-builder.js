/** @import {BuilderModel, BuilderSeed} from "./extension-builder/model.js" */
import { extensionsApi } from "../api/extensions.js";
import { byId, requireById } from "../core/dom.js";
import { clear, html, render } from "../core/html.js";
import { appStore } from "../core/store.js";
import { XB_TEMPLATES } from "./extension-builder/catalog.js";
import {
  collectManifest,
  createBuilderModel,
  xbBlankRule,
  xbCoerce,
  xbRuleFromManifest,
  xbSlug,
} from "./extension-builder/model.js";
import {
  handleBuilderRuleClick,
  handleBuilderRuleInput,
  renderBuilderRules,
} from "./extension-builder/rules-view.js";

export { XB_EVENTS, XB_OPS, XB_TEMPLATES } from "./extension-builder/catalog.js";
export { xbBlankRule, xbCoerce, xbRuleFromManifest, xbSlug };

/** @type {BuilderModel|null} */
export let xbModel = null;
export let xbBusy = false;

let xbTestRequest = 0;
let xbEditRequest = 0;
let initialized = false;
/** @type {() => Promise<void>} */
let refreshLocalServices = async () => {};

/** @param {unknown} error */
function errorMessage(error) {
  return error instanceof Error ? error.message : String(error);
}

/** @param {string} id @returns {HTMLElement} */
function element(id) {
  return /** @type {HTMLElement} */ (requireById(id));
}

/** @param {string} id @returns {HTMLInputElement} */
function input(id) {
  return /** @type {HTMLInputElement} */ (requireById(id));
}

/**
 * Invalidate a history replay and remove every commander-derived result.
 * The machine-local builder model may remain open across a handoff.
 */
export function resetExtensionBuilderHistory() {
  xbTestRequest += 1;
  const status = byId("xb-status");
  const results = byId("xb-results");
  const button = byId("xb-test");
  if (status) status.textContent = "";
  if (results) {
    clear(results);
    results.classList.add("hidden");
  }
  if (button instanceof HTMLButtonElement) button.disabled = false;
}

/** @param {BuilderSeed|null} [seed] */
export function xbOpen(seed = null) {
  xbModel = createBuilderModel(seed);
  input("xb-name").value = xbModel.name;
  element("xb-form").classList.remove("hidden");
  resetExtensionBuilderHistory();
  xbRenderRules();
  xbSyncId();
  input("xb-name").focus();
}

export function xbClose() {
  xbEditRequest += 1;
  xbModel = null;
  resetExtensionBuilderHistory();
  element("xb-form").classList.add("hidden");
}

export function xbSyncId() {
  const id = xbModel?.editingId || xbSlug(input("xb-name").value);
  element("xb-id").textContent = id || "—";
}

export function xbRenderRules() {
  if (!xbModel) return;
  renderBuilderRules(element("xb-rules"), xbModel);
}

/** @param {HTMLElement} target */
export function xbHandleClick(target) {
  if (!xbModel) return false;
  return handleBuilderRuleClick(target, xbModel, xbRenderRules);
}

/** @param {HTMLElement} target */
export function xbHandleInput(target) {
  if (!xbModel) return;
  handleBuilderRuleInput(target, xbModel, xbRenderRules);
}

export function xbCollect() {
  if (!xbModel) throw new Error("Open the extension builder first.");
  return collectManifest(xbModel, input("xb-name").value);
}

export async function xbTest() {
  const status = element("xb-status");
  const results = element("xb-results");
  const button = /** @type {HTMLButtonElement} */ (element("xb-test"));
  const requestId = ++xbTestRequest;
  let manifest;
  try {
    manifest = xbCollect();
  } catch (error) {
    status.textContent = errorMessage(error);
    button.disabled = false;
    return;
  }
  const identity = appStore.identity();
  if (!identity.commanderId) {
    clear(results);
    results.classList.add("hidden");
    status.textContent = "Waiting for a commander profile before replaying journal history.";
    button.disabled = false;
    return;
  }
  const isCurrentRequest = () => requestId === xbTestRequest && appStore.isCurrent(identity);
  button.disabled = true;
  status.textContent = "Replaying your recent history…";
  clear(results);
  results.classList.add("hidden");
  try {
    const data = await extensionsApi.testManifestAgainstHistory(manifest);
    if (!isCurrentRequest()) return;
    const matches = data.matches || [];
    status.textContent = matches.length
      ? `Scanned your last ${data.scanned} events — this would have fired ${matches.length} time${matches.length === 1 ? "" : "s"}${data.truncated ? " (showing the first " + matches.length + ")" : ""}:`
      : `Scanned your last ${data.scanned} events — no matches. The rule may still be right (nothing recent qualified); loosen a condition to see it fire.`;
    render(
      results,
      html`${matches.slice(0, 12).map(
        (match) =>
          html`<div class="xb-hit">
            <span class="mono dim"
              >${(match.timestamp || "").replace("T", " ").replace("Z", "")}</span
            >
            <span class="mono">${match.event_type || "?"}</span>
            <span
              class="xb-hit-msg ${
                match.action.level === "critical"
                  ? "bad"
                  : match.action.level === "warn"
                    ? "warn"
                    : ""
              }"
            >
              ${match.action.type === "objective" ? "◎ " : "⚠ "}${
                match.action.text || match.action.title || ""
              }
            </span>
          </div>`,
      )}`,
    );
    results.classList.toggle("hidden", !matches.length);
  } catch (error) {
    if (isCurrentRequest()) status.textContent = errorMessage(error);
  } finally {
    if (isCurrentRequest() && button.isConnected) button.disabled = false;
  }
}

/** @param {SubmitEvent|Event} event */
export async function xbSave(event) {
  event.preventDefault();
  if (xbBusy) return;
  const status = element("xb-status");
  let manifest;
  try {
    manifest = xbCollect();
  } catch (error) {
    status.textContent = errorMessage(error);
    return;
  }
  xbBusy = true;
  const button = /** @type {HTMLButtonElement} */ (element("xb-save"));
  button.disabled = true;
  status.textContent = "Saving…";
  try {
    await extensionsApi.saveManifest(manifest);
    xbClose();
    await refreshLocalServices();
    element("xb-status").textContent = "";
  } catch (error) {
    status.textContent = errorMessage(error);
  } finally {
    xbBusy = false;
    button.disabled = false;
  }
}

/** @param {string} extensionId */
export async function xbEditPack(extensionId) {
  const request = ++xbEditRequest;
  try {
    const { manifest } = await extensionsApi.getManifest(extensionId);
    if (request !== xbEditRequest) return;
    xbOpen({
      name: manifest.name || extensionId,
      editingId: manifest.id || extensionId,
      rules: (manifest.rules || []).map(xbRuleFromManifest),
    });
    element("ext-builder-card").scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (error) {
    if (request === xbEditRequest) element("extensions-status").textContent = errorMessage(error);
  }
}

/**
 * @param {string} extensionId
 * @param {HTMLButtonElement|null} [button]
 */
export async function xbDeletePack(extensionId, button = null) {
  if (
    !window.confirm(
      `Remove the extension "${extensionId}"? Its alerts and suggestions stop immediately.`,
    )
  ) {
    return;
  }
  if (button) button.disabled = true;
  try {
    await extensionsApi.deleteExtension(extensionId);
    await refreshLocalServices();
  } catch (error) {
    element("extensions-status").textContent = errorMessage(error);
  } finally {
    if (button?.isConnected) button.disabled = false;
  }
}

/**
 * @param {{refreshLocalServices?: () => Promise<void>}} [options]
 */
export function initExtensionBuilder(options = {}) {
  if (options.refreshLocalServices) refreshLocalServices = options.refreshLocalServices;
  if (initialized) return;
  const templates = byId("xb-templates");
  if (!templates) return;
  initialized = true;
  render(
    templates,
    html`${XB_TEMPLATES.map(
        (template, index) =>
          html`<button type="button" class="xb-template" data-template="${index}">
            ${template.label}
          </button>`,
      )} <button type="button" class="xb-template xb-blank" data-template="blank">Blank</button>`,
  );
  templates.addEventListener("click", (event) => {
    if (!(event.target instanceof Element)) return;
    const chip = event.target.closest("[data-template]");
    if (!(chip instanceof HTMLElement)) return;
    const template = XB_TEMPLATES[Number(chip.dataset.template)];
    const seed = template
      ? /** @type {BuilderSeed} */ ({ name: template.name, rules: template.rules })
      : null;
    xbOpen(seed);
  });
  element("xb-new").addEventListener("click", () => xbOpen());
  element("xb-cancel").addEventListener("click", xbClose);
  element("xb-add-rule").addEventListener("click", () => {
    if (!xbModel) return;
    xbModel.rules.push(xbBlankRule());
    xbRenderRules();
  });
  element("xb-test").addEventListener("click", () => void xbTest());
  element("xb-form").addEventListener("submit", (event) => void xbSave(event));
  input("xb-name").addEventListener("input", xbSyncId);
  element("xb-rules").addEventListener("click", (event) => {
    if (!(event.target instanceof Element)) return;
    const target = event.target.closest("[data-xb]");
    if (target instanceof HTMLElement && xbHandleClick(target)) event.preventDefault();
  });
  element("xb-rules").addEventListener("input", (event) => {
    if (!(event.target instanceof Element)) return;
    const target = event.target.closest("[data-xb]");
    if (target instanceof HTMLElement) xbHandleInput(target);
  });
}
