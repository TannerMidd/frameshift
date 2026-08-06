/**
 * Composition step for the desktop window's custom chrome.
 *
 * main.js only wires bootstrap modules together, so the title bar is reached
 * through a named initializer rather than mounted directly.
 */
import { initializeFullscreen } from "../shell/fullscreen.js";
import { mountTitleBar } from "../shell/titlebar.js";

/** No-ops outside the pywebview desktop window. */
export function initializeWindowChrome() {
  mountTitleBar();
  initializeFullscreen();
}
