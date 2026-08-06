/**
 * Custom title bar for the frameless desktop window.
 *
 * The desktop window has no system frame, so this draws the caption in the
 * cockpit theme and owns the window buttons. It renders only inside pywebview —
 * a browser tab, a paired tablet and panel mode all keep their real chrome.
 *
 * The native side cannot see the DOM, so this module reports where the title bar
 * ended up (see reportMetrics) and Windows hit-tests against those rectangles.
 * That is what makes dragging, Aero Snap and Snap Layouts work.
 */
import { desktopBridge as bridge, isDesktopWindow } from "../core/desktop.js";
import { requireById } from "../core/dom.js";
import { html, render } from "../core/html.js";
import { applyFullscreenState, isFullscreen } from "./fullscreen.js";

/**
 * A DOM rectangle in the physical pixels Windows hit-tests against.
 * devicePixelRatio folds monitor DPI and whole-app zoom into one number, which
 * is why the title bar's height cannot simply be assumed on the native side.
 *
 * @param {Element} element
 * @returns {number[]}
 */
function physicalRect(element) {
  const box = element.getBoundingClientRect();
  const scale = window.devicePixelRatio || 1;
  return [box.left, box.top, box.right, box.bottom].map((value) => Math.round(value * scale));
}

const MARKUP = html`<div class="titlebar-drag" id="titlebar-drag">
    <span class="titlebar-mark" aria-hidden="true"></span>
    <span class="titlebar-name">FRAMESHIFT</span>
  </div>
  <div class="titlebar-buttons">
    <button
      type="button"
      class="hb hb-utility titlebar-btn"
      id="titlebar-min"
      title="Minimize"
      aria-label="Minimize"
    >
      −
    </button>
    <button
      type="button"
      class="hb hb-utility titlebar-btn"
      id="titlebar-max"
      title="Maximize"
      aria-label="Maximize"
    >
      □
    </button>
    <button
      type="button"
      class="hb hb-utility titlebar-btn titlebar-close"
      id="titlebar-close"
      title="Close"
      aria-label="Close"
    >
      ✕
    </button>
  </div>`;

/**
 * Mount the title bar. Safe to call on every surface; does nothing off desktop.
 *
 * @param {Element} [host] the element the app renders into
 * @param {boolean} [startFullscreen] the state the window was restored into
 * @returns {boolean} whether a title bar was mounted
 */
export function initializeTitleBar(host = requireById("app-root"), startFullscreen = false) {
  if (!isDesktopWindow() || document.getElementById("titlebar")) return false;

  // Before the bar is built, so a remembered fullscreen never paints a caption
  // for a frame on the way to hiding one.
  applyFullscreenState(startFullscreen, false);

  const bar = document.createElement("header");
  bar.id = "titlebar";
  bar.className = "titlebar";
  render(bar, MARKUP);
  host.insertAdjacentElement("beforebegin", bar);
  document.body.classList.add("has-titlebar");

  const drag = requireById("titlebar-drag");
  const maxButton = requireById("titlebar-max");

  const reportMetrics = () => {
    // Fullscreen has no caption, so it reports no rectangles. Sending the old
    // ones would leave a band across the top of the screen that silently drags
    // the window and offers Snap Layouts over whatever is painted there.
    const full = isFullscreen();
    bridge()?.set_chrome_metrics?.({
      drag: full ? [] : [physicalRect(bar)],
      maxButton: full ? null : physicalRect(maxButton),
    });
  };

  requireById("titlebar-min").addEventListener("click", () => bridge()?.window_minimize?.());
  requireById("titlebar-close").addEventListener("click", () => bridge()?.window_close?.());
  // The maximize button is reported to Windows as HTMAXBUTTON so hovering it
  // offers Snap Layouts. Windows then swallows the ordinary click, so the native
  // side drives both the press and the hover state — this listener is the
  // fallback for when the hit-test has not been registered yet.
  maxButton.addEventListener("click", () => bridge()?.window_toggle_maximize?.());
  drag.addEventListener("dblclick", () => bridge()?.window_toggle_maximize?.());

  /** The hooks elite/windowchrome.py calls; see its _notify. */
  Reflect.set(window, "__frameshiftChrome", {
    /** @param {boolean} hovering */
    setMaxHover(hovering) {
      maxButton.classList.toggle("hover", Boolean(hovering));
    },
    /** @param {boolean} maximized */
    setMaximized(maximized) {
      const on = Boolean(maximized);
      document.body.classList.toggle("window-maximized", on);
      maxButton.textContent = on ? "❐" : "□";
      const label = on ? "Restore" : "Maximize";
      maxButton.setAttribute("aria-label", label);
      maxButton.setAttribute("title", label);
      reportMetrics();
    },
    /** @param {boolean} on */
    setFullscreen(on) {
      applyFullscreenState(Boolean(on));
      reportMetrics();
    },
    remeasure: reportMetrics,
  });

  // Anything that can move the title bar has to re-report it, or the drag strip
  // and the real caption drift apart: a resize, a move to a different-DPI
  // monitor, and the per-device size sliders all change the numbers.
  window.addEventListener("resize", reportMetrics);
  const observer = new ResizeObserver(reportMetrics);
  observer.observe(bar);
  reportMetrics();
  return true;
}

/**
 * Mount now if the bridge is already there, otherwise when it arrives. pywebview
 * injects its api asynchronously, so at DOMContentLoaded it may not exist yet —
 * the same both-paths handling the in-app link toggle uses.
 *
 * @param {Element} [host]
 */
export function mountTitleBar(host) {
  const mount = () => {
    // The window may have opened straight into a remembered fullscreen, so ask
    // before building rather than correcting after. Older bridges without the
    // call, and a failed one, both fall through to the ordinary windowed mount.
    const pending = bridge()?.window_is_fullscreen?.();
    if (!pending) {
      initializeTitleBar(host);
      return;
    }
    pending.then(
      (full) => initializeTitleBar(host, Boolean(full)),
      () => initializeTitleBar(host),
    );
  };
  if (isDesktopWindow()) {
    mount();
    return;
  }
  window.addEventListener("pywebviewready", mount, { once: true });
}
