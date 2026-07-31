/** @import {DiagnosticsHealthResponse, ExtensionSnapshot} from "../api/contracts/extensions.js" */
import { extensionsApi } from "../api/extensions.js";
import { requireById } from "../core/dom.js";
import { html, render } from "../core/html.js";
import { securityStatus } from "./security.js";

let localServicesRequest = 0;

/** @param {string} id @returns {HTMLElement} */
function element(id) {
  return /** @type {HTMLElement} */ (requireById(id));
}

/** @param {unknown} error */
function errorMessage(error) {
  return error instanceof Error ? error.message : String(error);
}

export async function loadLocalServices() {
  const request = ++localServicesRequest;
  const card = element("local-services-card");
  const status = /** @type {{scopes?: string[]}|null} */ (securityStatus);
  const admin = Boolean(status?.scopes?.includes("admin"));
  card.classList.toggle("hidden", !admin);
  element("ext-builder-card").classList.toggle("hidden", !admin);
  if (!admin) return;

  // The health probe can take seconds; extension management stays responsive
  // while SQLite integrity diagnostics run independently.
  const extensionsDone = (async () => {
    try {
      const extensions = await extensionsApi.listExtensions();
      if (request === localServicesRequest) renderExtensionRows(extensions);
    } catch (error) {
      if (request === localServicesRequest) {
        element("extensions-status").textContent = errorMessage(error);
      }
    }
  })();
  try {
    const health = await extensionsApi.getDiagnosticsHealth();
    if (request !== localServicesRequest) return;
    renderLocalHealth(health);
  } catch (error) {
    if (request === localServicesRequest) {
      element("local-health").textContent = errorMessage(error);
    }
  }
  await extensionsDone;
}

/** @param {DiagnosticsHealthResponse} health */
function renderLocalHealth(health) {
  const database = health.market_database || {};
  const integrity =
    health.sqlite_integrity || (health.market_database_error ? "unavailable" : "unknown");
  const markets = database.markets;
  const watch = health.galaxy_watch;
  const watching = watch?.watching || [];
  const fired = watch?.fired || [];
  // The galaxy watch is silent by design and may never fire, so the health
  // line is the only place it can show that it is actually running.
  const watchText = fired.length
    ? ` · GALAXY WATCH HIT: ${fired.join(", ")}`
    : watching.length
      ? ` · watching ${watching.join(", ")} across EDDN`
      : "";
  element("local-health").textContent =
    `Frameshift ${health.version || "?"} · database ${integrity}` +
    `${markets != null ? ` · ${Number(markets).toLocaleString()} markets` : ""}` +
    watchText +
    " · logs rotate locally";
}

/** @param {ExtensionSnapshot} extensions */
export function renderExtensionRows(extensions) {
  const loaded = extensions.loaded || [];
  const errors = extensions.errors || [];
  const rows = loaded.map((extension) => {
    const process = extension.mode === "process";
    const permissionText = (extension.permissions || []).join(" / ") || "no permissions";
    const approval = process
      ? extension.approved
        ? html`<span class="good">APPROVED FOR THIS EXACT BUILD</span>`
        : html`<span class="warn">CODE EXECUTION BLOCKED · APPROVAL REQUIRED</span>`
      : html`<span class="good">DECLARATIVE · NO CODE EXECUTION</span>`;
    const action = process
      ? extension.approved
        ? html`<button
            type="button"
            class="hb hb-utility hb-danger"
            data-extension-action="revoke"
            data-extension-id="${extension.id}"
          >
            REVOKE
          </button>`
        : html`<button
            type="button"
            class="hb hb-utility"
            data-extension-action="approve"
            data-extension-id="${extension.id}"
          >
            APPROVE CODE
          </button>`
      : html`<span class="extension-tools">
          <button
            type="button"
            class="hb hb-utility"
            data-extension-action="edit"
            data-extension-id="${extension.id}"
            title="Open in the extension builder"
          >
            ✎ EDIT
          </button>
          <button
            type="button"
            class="hb hb-utility hb-danger"
            data-extension-action="delete"
            data-extension-id="${extension.id}"
            title="Remove this pack"
          >
            ✕
          </button>
        </span>`;
    return html`<div class="extension-row">
      <div>
        <b>${extension.name || extension.id}</b>
        <span class="dim">${extension.id} · ${extension.version || "0"} · ${permissionText}</span>
        <span
          >${approval}${
            process && extension.fingerprint ? ` · fingerprint ${extension.fingerprint}` : ""
          }</span
        >
      </div>
      ${action}
    </div>`;
  });
  render(
    element("extensions-status"),
    html`<b>${loaded.length} extension pack${loaded.length === 1 ? "" : "s"} loaded</b>
      <span class="dim">
        from the local extensions
        folder${
          errors.length ? ` · ${errors.length} rejected (details included in diagnostics)` : ""
        }</span
      >
      <div class="extension-rows">${rows}</div>`,
  );
}

export async function downloadSupportBundle() {
  const button = /** @type {HTMLButtonElement} */ (element("diagnostics-bundle"));
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "BUILDING…";
  try {
    const { blob, filename: suggestedFilename } = await extensionsApi.createSupportBundle();
    const filename = suggestedFilename || "frameshift-diagnostics.zip";
    const href = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = href;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(href), 1000);
    button.textContent = "SAVED";
  } catch {
    button.textContent = "FAILED";
  } finally {
    window.setTimeout(() => {
      button.textContent = original;
      button.disabled = false;
    }, 1200);
  }
}

export async function reloadExtensions() {
  const button = /** @type {HTMLButtonElement} */ (element("extensions-reload"));
  button.disabled = true;
  try {
    await extensionsApi.reloadExtensions();
    await loadLocalServices();
  } catch (error) {
    element("extensions-status").textContent = errorMessage(error);
  } finally {
    button.disabled = false;
  }
}

/**
 * @param {string} extensionId
 * @param {"approve"|"revoke"} action
 * @param {HTMLButtonElement} button
 */
export async function changeExtensionApproval(extensionId, action, button) {
  if (!extensionId) return;
  if (
    action === "approve" &&
    !window.confirm(
      "Approve this exact process extension build? It can execute local code and is not an operating-system sandbox. Any code change will require approval again.",
    )
  ) {
    return;
  }
  button.disabled = true;
  try {
    if (action === "approve") await extensionsApi.approveExtension(extensionId);
    else await extensionsApi.revokeExtension(extensionId);
    await loadLocalServices();
  } catch (error) {
    element("extensions-status").textContent = errorMessage(error);
  } finally {
    button.disabled = false;
  }
}

/**
 * @param {string} url
 * @param {string|null} [label]
 */
export function openExternal(url, label = null) {
  const bridge = /** @type {{
    api?: {
      open_inline?: (url: string, label: string) => void,
      open_url?: (url: string) => void,
    },
  }|undefined} */ (Reflect.get(window, "pywebview"));
  if (!bridge?.api) return false;
  const inAppToggle = requireById("inapp-toggle");
  if (inAppToggle instanceof HTMLInputElement && inAppToggle.checked && bridge.api.open_inline) {
    bridge.api.open_inline(url, label || "Browser");
    return true;
  }
  if (bridge.api.open_url) {
    bridge.api.open_url(url);
    return true;
  }
  return false;
}
