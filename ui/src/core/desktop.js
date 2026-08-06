/**
 * The bridge to the native desktop window.
 *
 * Only the packaged app injects `window.pywebview`; a browser tab and a paired
 * tablet have no native window behind them. Every call through here is
 * therefore optional-chained to nothing rather than guarded at each call site,
 * so shared code can ask the window to do something without first asking
 * whether there is one.
 */

/**
 * @typedef {{
 *   window_minimize?: () => void,
 *   window_toggle_maximize?: () => void,
 *   window_close?: () => void,
 *   window_is_maximized?: () => Promise<boolean>,
 *   window_toggle_fullscreen?: () => void,
 *   window_set_fullscreen?: (on: boolean) => void,
 *   window_is_fullscreen?: () => Promise<boolean>,
 *   set_chrome_metrics?: (metrics: {drag: number[][], maxButton: number[]|null}) => void,
 *   set_accent?: (accent: string) => void,
 * }} DesktopBridge
 */

/** @returns {DesktopBridge|undefined} */
export function desktopBridge() {
  const found = /** @type {{api?: DesktopBridge}|undefined} */ (Reflect.get(window, "pywebview"));
  return found?.api;
}

/** True once pywebview has injected its bridge. */
export function isDesktopWindow() {
  return Boolean(Reflect.get(window, "pywebview"));
}
