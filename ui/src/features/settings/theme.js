import { desktopBridge } from "../../core/desktop.js";
import { setStyleValue } from "../../core/dom.js";
import { html, render } from "../../core/html.js";

/** @typedef {{label: string, accent: string, soft: string}} ThemePreset */

/** @type {Readonly<Record<string, ThemePreset>>} */
export const THEME_PRESETS = Object.freeze({
  elite: { label: "Elite Orange", accent: "#ff7100", soft: "#ff9a40" },
  ice: { label: "Ice Blue", accent: "#35a7ff", soft: "#7cc4ff" },
  emerald: { label: "Emerald", accent: "#2ecc71", soft: "#82e0aa" },
  gold: { label: "Gold", accent: "#ffbf00", soft: "#ffd966" },
  crimson: { label: "Crimson", accent: "#ff4438", soft: "#ff8a80" },
  violet: { label: "Violet", accent: "#a86bff", soft: "#c9a2ff" },
});

/**
 * @param {string} hex
 * @returns {[number, number, number]|null}
 */
export function hexToRgb(hex) {
  const match = /^#?([0-9a-f]{6})$/i.exec(String(hex || "").trim());
  const digits = match?.[1];
  if (!digits) return null;
  const value = Number.parseInt(digits, 16);
  return [(value >> 16) & 255, (value >> 8) & 255, value & 255];
}

/** @param {string} hex */
export function softenAccent(hex) {
  const rgb = hexToRgb(hex);
  if (!rgb) return hex;
  return (
    "#" +
    rgb
      .map((channel) => Math.round(channel + (255 - channel) * 0.35))
      .map((channel) => channel.toString(16).padStart(2, "0"))
      .join("")
  );
}

export function currentTheme() {
  return localStorage.getItem("accentTheme") || "elite";
}

export function applyTheme() {
  const theme = currentTheme();
  const preset = THEME_PRESETS[theme];
  const elite = /** @type {ThemePreset} */ (THEME_PRESETS.elite);
  const accent = preset ? preset.accent : hexToRgb(theme) ? theme : elite.accent;
  const soft = preset ? preset.soft : softenAccent(accent);
  const accentRgb = hexToRgb(accent) || /** @type {[number, number, number]} */ ([255, 113, 0]);
  const softRgb = hexToRgb(soft) || accentRgb;
  const root = document.documentElement;
  setStyleValue(root, "--orange", accent);
  setStyleValue(root, "--orange-soft", soft);
  setStyleValue(root, "--accent-rgb", accentRgb.join(", "));
  setStyleValue(root, "--accent-soft-rgb", softRgb.join(", "));
  // The desktop window's system border and pre-paint background are outside the
  // document, so CSS variables cannot reach them. No-ops everywhere else.
  desktopBridge()?.set_accent?.(accent);
}

export function accentColor() {
  return (
    getComputedStyle(document.documentElement).getPropertyValue("--orange").trim() || "#ff7100"
  );
}

/** @returns {HTMLElement} */
export function buildThemeSetting() {
  const wrap = document.createElement("div");
  wrap.className = "setting setting-theme";
  render(
    wrap,
    html`<div class="setting-text">
      <b>Color theme</b>
      <div class="dim">
        The accent color on this device — match your in-game HUD. Presets are tuned for readability;
        Custom takes any color.
      </div>
    </div>`,
  );
  const chips = document.createElement("div");
  chips.className = "theme-chips";
  const custom = document.createElement("label");
  const customInput = document.createElement("input");
  const syncActive = () => {
    const theme = currentTheme();
    chips.querySelectorAll("[data-theme]").forEach((element) => {
      if (!(element instanceof HTMLElement)) return;
      const active = element.dataset.theme === theme;
      element.classList.toggle("on", active);
      element.setAttribute("aria-pressed", String(active));
    });
    custom.classList.toggle("on", !THEME_PRESETS[theme]);
    setStyleValue(custom, "--chip", !THEME_PRESETS[theme] ? theme : "#888");
  };
  for (const [id, preset] of Object.entries(THEME_PRESETS)) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "theme-chip";
    button.dataset.theme = id;
    setStyleValue(button, "--chip", preset.accent);
    render(button, html`<span class="theme-dot" aria-hidden="true"></span>${preset.label}`);
    button.addEventListener("click", () => {
      localStorage.setItem("accentTheme", id);
      applyTheme();
      syncActive();
    });
    chips.appendChild(button);
  }
  custom.className = "theme-chip theme-custom";
  customInput.type = "color";
  customInput.value = hexToRgb(currentTheme())
    ? currentTheme()
    : /** @type {ThemePreset} */ (THEME_PRESETS.elite).accent;
  customInput.addEventListener("input", () => {
    localStorage.setItem("accentTheme", customInput.value);
    applyTheme();
    syncActive();
  });
  custom.append(customInput, document.createTextNode("Custom…"));
  chips.appendChild(custom);
  wrap.appendChild(chips);
  syncActive();
  return wrap;
}
