import { byId } from "../core/dom.js";
import { appStore } from "../core/store.js";
import { exitFullscreen, requestFullscreen } from "./fullscreen.js";
import { activateTab } from "./tabs.js";
import { renderPanel } from "./status.js";

/** @param {string} id @returns {HTMLElement|null} */
const $ = (id) => /** @type {HTMLElement|null} */ (byId(id));

export const PANEL_PAGES = [
  "status",
  "trade",
  "commodities",
  "bio",
  "guides",
  "analytics",
  "engineering",
  "galaxy",
  "ops",
  "specialists",
  "local",
  "database",
];

/** @param {{getItem(key: string): string|null}} [storage] */
export function panelModeOnLaunch(storage = localStorage) {
  try {
    const saved = storage.getItem("panelMode");
    return saved == null ? true : saved === "1";
  } catch (_error) {
    return true;
  }
}

/**
 * @param {boolean} on
 * @param {boolean} [persist]
 */
export function setPanelMode(on, persist = true) {
  document.body.classList.toggle("panel-mode", on);
  if (persist) localStorage.setItem("panelMode", on ? "1" : "0");
  // Fullscreen is opt-in via the rail's ⛶ FULL button or F11; leaving the panel
  // always drops back out of it. Dropping the native one is not just tidiness:
  // ⛶ FULL lives in the panel rail, so a chromeless desktop layout would be
  // left with no title bar and no rail — nothing visible to get back with.
  if (!on) {
    exitFullscreen();
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
  }
  if (on) {
    runPanelBoot();
    setPanelPage(localStorage.getItem("panelPage") || "status");
  } else {
    $("flight-panel")?.classList.add("hidden");
  }
  document.documentElement.classList.remove("panel-mode-prepaint");
}

export function toggleFullscreen() {
  // In the desktop window this is a real Win32 state. The DOM Fullscreen API
  // cannot expand a WebView2 host, which is why this button used to do nothing
  // there; a paired tablet in a real browser has no bridge and keeps the DOM
  // path, where it works properly.
  if (requestFullscreen()) return;
  if (document.fullscreenElement) {
    document.exitFullscreen().catch(() => {});
  } else if (document.documentElement.requestFullscreen) {
    document.documentElement.requestFullscreen().catch(() => {});
  }
}

/** @type {number|null} */
export let bootTimer = null;

export function runPanelBoot() {
  const boot = $("fp-boot");
  if (!boot) return;
  if (bootTimer !== null) clearTimeout(bootTimer);
  boot.classList.add("hidden");
  void boot.offsetWidth; // restart the CSS animations
  boot.classList.remove("hidden");
  bootTimer = setTimeout(() => boot.classList.add("hidden"), 1800);
}

setInterval(() => {
  if (!document.body.classList.contains("panel-mode")) return;
  const el = $("fp-clock");
  if (!el) return;
  const d = new Date();
  /** @param {number} n */
  const p = (n) => String(n).padStart(2, "0");
  el.textContent = `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}, 1000);

/** @param {string} name */
export function panelViewEl(name) {
  return name === "status" ? $("flight-panel") : $("tab-" + name);
}

/**
 * @param {HTMLElement|null} el
 * @param {number|undefined} dir
 */
export function slideIn(el, dir) {
  if (!el || !dir) return;
  el.classList.remove("slide-in-left", "slide-in-right");
  void el.offsetWidth; // restart the animation if the class is re-applied
  el.classList.add(dir > 0 ? "slide-in-right" : "slide-in-left");
  el.addEventListener(
    "animationend",
    () => el.classList.remove("slide-in-left", "slide-in-right"),
    { once: true },
  );
}

/**
 * @param {string} name
 * @param {number} [slideDir]
 */
export function setPanelPage(name, slideDir) {
  if (!PANEL_PAGES.includes(name)) name = "status";
  localStorage.setItem("panelPage", name);
  const statusPage = name === "status";
  $("flight-panel")?.classList.toggle("hidden", !statusPage);
  document.body.classList.toggle("fp-status-page", statusPage);
  // When a directional slide is about to play, skip the fade-up entrance —
  // two stacked animations is what caused the post-slide content flash.
  if (!statusPage) activateTab(name, !slideDir);
  document.querySelectorAll("#fp-nav button[data-page]").forEach((b) => {
    if (!(b instanceof HTMLButtonElement)) return;
    const active = b.dataset.page === name;
    b.classList.toggle("active", active);
    if (active) b.setAttribute("aria-current", "page");
    else b.removeAttribute("aria-current");
  });
  if (statusPage && appStore.getSnapshot()) renderPanel();
  window.scrollTo(0, 0);
  if (slideDir) slideIn(panelViewEl(name), slideDir);
}

/** @param {number} dx */
export function panelSwipe(dx) {
  const current = localStorage.getItem("panelPage") || "status";
  const idx = PANEL_PAGES.indexOf(current);
  const forward = dx < 0;
  const next = PANEL_PAGES[(idx + (forward ? 1 : PANEL_PAGES.length - 1)) % PANEL_PAGES.length];
  setPanelPage(next || "status", forward ? 1 : -1);
}

export function initPanelNav() {
  // Scoped to [data-page]: the rail also holds the voice and exit buttons,
  // which have their own handlers.
  document.querySelectorAll("#fp-nav button[data-page]").forEach((element) => {
    if (!(element instanceof HTMLButtonElement)) return;
    const b = element;
    b.addEventListener("click", () => {
      const current = localStorage.getItem("panelPage") || "status";
      const page = b.dataset.page || "status";
      const delta = PANEL_PAGES.indexOf(page) - PANEL_PAGES.indexOf(current);
      setPanelPage(page, Math.sign(delta));
    });
  });

  // Swipe left/right between pages (except inside horizontally scrollable
  // tables and form fields). The page follows the finger while the gesture is
  // in flight; past the threshold it hands off to a directional slide-in.
  let startX = 0;
  let startY = 0;
  /** @type {"pending"|"swipe"|null} */
  let gesture = null;
  /** @type {HTMLElement|null} */
  let view = null;
  const endDrag = () => {
    if (view) {
      view.classList.remove("fp-dragging");
      view.style.transform = "";
    }
    gesture = null;
    view = null;
  };
  document.addEventListener(
    "touchstart",
    (ev) => {
      gesture = null;
      if (!document.body.classList.contains("panel-mode") || ev.touches.length !== 1) return;
      if (document.body.classList.contains("arranging")) return; // dragging cards, not pages
      if (
        ev.target instanceof Element &&
        ev.target.closest(".table-wrap, input, select, textarea")
      ) {
        return;
      }
      const touch = ev.touches[0];
      if (!touch) return;
      startX = touch.clientX;
      startY = touch.clientY;
      gesture = "pending";
    },
    { passive: true },
  );
  document.addEventListener(
    "touchmove",
    (ev) => {
      if (!gesture) return;
      const touch = ev.touches[0];
      if (!touch) return;
      const dx = touch.clientX - startX;
      const dy = touch.clientY - startY;
      if (gesture === "pending") {
        // Decide the gesture's orientation once, so vertical scrolling never drags the page.
        if (Math.abs(dy) > 18 && Math.abs(dy) > Math.abs(dx)) {
          gesture = null;
          return;
        }
        if (Math.abs(dx) > 14 && Math.abs(dx) > Math.abs(dy) * 1.4) {
          gesture = "swipe";
          view = panelViewEl(localStorage.getItem("panelPage") || "status");
          view?.classList.add("fp-dragging");
        }
        return;
      }
      if (view) view.style.transform = `translateX(${dx * 0.85}px)`;
    },
    { passive: true },
  );
  document.addEventListener(
    "touchend",
    (ev) => {
      if (gesture !== "swipe") {
        gesture = null;
        return;
      }
      const touch = ev.changedTouches[0];
      if (!touch) {
        endDrag();
        return;
      }
      const dx = touch.clientX - startX;
      endDrag();
      if (Math.abs(dx) > 70) panelSwipe(dx);
    },
    { passive: true },
  );
  document.addEventListener("touchcancel", endDrag, { passive: true });
}
