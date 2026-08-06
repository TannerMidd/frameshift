/**
 * Borderless fullscreen for the desktop window.
 *
 * The DOM Fullscreen API is no help here: inside an embedded WebView2 there is
 * no browser window for it to expand, so requestFullscreen leaves the app
 * exactly the size it was. Real fullscreen is a Win32 state owned by
 * elite/windowchrome.py, and this is the page's half of it — the keys that ask
 * for it, the class that hides the title bar, and the hint that says how to get
 * back out once there is no title bar left to click.
 *
 * A browser tab and a paired tablet have no bridge and keep the DOM path; see
 * toggleFullscreen in shell/panel.js.
 */
import { desktopBridge } from "../core/desktop.js";
import { byId } from "../core/dom.js";

const HINT_MS = 3200;

let fullscreen = false;
/** @type {number|null} */
let hintTimer = null;

/** Whether the native window is fullscreen right now. */
export function isFullscreen() {
  return fullscreen;
}

/**
 * Which fullscreen action a keypress is asking for, if any.
 *
 * @param {{key: string, defaultPrevented: boolean}} event
 * @param {{fullscreen: boolean}} context
 * @returns {"toggle"|"exit"|null}
 */
export function fullscreenKeyIntent(event, context) {
  if (event.defaultPrevented) return null;
  if (event.key === "F11") return "toggle";
  // Escape belongs to whatever the commander is actually looking at. It only
  // ever leaves fullscreen, never enters it, and never when something nearer
  // the front — an open dialog — has already claimed the keypress.
  if (event.key !== "Escape" || !context.fullscreen) return null;
  return "exit";
}

/**
 * Ask the native window to change state.
 *
 * @param {boolean} [on] omitted toggles
 * @returns {boolean} whether there was a native window to ask
 */
export function requestFullscreen(on) {
  const api = desktopBridge();
  if (on === undefined) {
    if (!api?.window_toggle_fullscreen) return false;
    api.window_toggle_fullscreen();
    return true;
  }
  if (!api?.window_set_fullscreen) return false;
  api.window_set_fullscreen(on);
  return true;
}

/** Leave native fullscreen. Does nothing in a browser or when already out. */
export function exitFullscreen() {
  if (fullscreen) requestFullscreen(false);
}

/**
 * Point the Panel rail's fullscreen button at the current state. Native and DOM
 * fullscreen both arrive here, so the button reads right on either surface.
 *
 * @param {boolean} on
 */
export function syncFullscreenControl(on) {
  const button = /** @type {HTMLElement|null} */ (byId("fp-full"));
  if (!button) return;
  button.setAttribute("aria-pressed", String(on));
  button.title = on
    ? "Leave fullscreen (F11 or Esc)"
    : "Fill the screen and hide the window frame (F11)";
}

function hintElement() {
  const existing = /** @type {HTMLElement|null} */ (byId("window-fullscreen-hint"));
  if (existing) return existing;
  const hint = document.createElement("div");
  hint.id = "window-fullscreen-hint";
  hint.className = "fs-hint hidden";
  hint.setAttribute("role", "status");
  hint.setAttribute("aria-live", "polite");
  hint.textContent = "Fullscreen. Press F11 or Esc to bring the window back.";
  // On the body rather than inside #app-root, for the same reason the title bar
  // is: the shell re-renders that element and would take the hint with it.
  document.body.append(hint);
  return hint;
}

/** @param {boolean} on */
function showHint(on) {
  if (hintTimer !== null) {
    clearTimeout(hintTimer);
    hintTimer = null;
  }
  if (!on) {
    /** @type {HTMLElement|null} */ (byId("window-fullscreen-hint"))?.classList.add("hidden");
    return;
  }
  const hint = hintElement();
  hint.classList.remove("hidden");
  hintTimer = window.setTimeout(() => {
    hint.classList.add("hidden");
    hintTimer = null;
  }, HINT_MS);
}

/**
 * Adopt a fullscreen state the native window has reached.
 *
 * @param {boolean} on
 * @param {boolean} [announce] false while taking on the state the window booted
 *   into, where a hint on every launch would nag rather than teach
 */
export function applyFullscreenState(on, announce = true) {
  fullscreen = Boolean(on);
  document.body.classList.toggle("window-fullscreen", fullscreen);
  syncFullscreenControl(fullscreen);
  if (announce) showHint(fullscreen);
}

/** Own the F11 and Escape keys. */
export function initializeFullscreen() {
  // On window rather than document: window is the last bubble target, so this
  // runs after every document-level handler however early it registered — and
  // window chrome boots first, well before the features that also want Escape.
  window.addEventListener("keydown", (event) => {
    const intent = fullscreenKeyIntent(event, { fullscreen });
    if (!intent) return;
    // Claim the key only once a native window has actually been asked. There is
    // no bridge in a browser tab, and swallowing F11 there would break the
    // browser's own fullscreen for a paired tablet.
    if (!requestFullscreen(intent === "toggle" ? undefined : false)) return;
    event.preventDefault();
  });
}
