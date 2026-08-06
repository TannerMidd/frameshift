import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/** @type {typeof import("./titlebar.js")} */
let mod;

/** The subset of the js_api bridge the title bar talks to. */
function fakeBridge() {
  return {
    window_minimize: vi.fn(),
    window_toggle_maximize: vi.fn(),
    window_close: vi.fn(),
    window_is_fullscreen: vi.fn(() => Promise.resolve(false)),
    set_chrome_metrics: vi.fn(),
  };
}

/** Let the mount's one round trip to the native window settle. */
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

async function freshModule() {
  vi.resetModules();
  mod = await import("./titlebar.js");
}

/** @param {ReturnType<typeof fakeBridge>|null} api */
function setDesktop(api) {
  if (api) Reflect.set(window, "pywebview", { api });
  else Reflect.deleteProperty(window, "pywebview");
}

const titlebar = () => document.getElementById("titlebar");
const chrome = () => /** @type {any} */ (Reflect.get(window, "__frameshiftChrome"));

beforeEach(async () => {
  document.body.replaceChildren();
  const root = document.createElement("div");
  root.id = "app-root";
  document.body.appendChild(root);
  document.body.className = "";
  setDesktop(null);
  Reflect.deleteProperty(window, "__frameshiftChrome");
  await freshModule();
});

afterEach(() => {
  setDesktop(null);
});

describe("title bar mounting", () => {
  it("stays out of browser tabs and paired tablets", () => {
    expect(mod.initializeTitleBar()).toBe(false);
    expect(titlebar()).toBeNull();
    expect(document.body.classList.contains("has-titlebar")).toBe(false);
  });

  it("mounts in the desktop window as a sibling of the app root", () => {
    setDesktop(fakeBridge());
    expect(mod.initializeTitleBar()).toBe(true);
    const bar = titlebar();
    expect(bar).not.toBeNull();
    // A sibling, not a child: the shell re-renders #app-root and would
    // otherwise wipe the window's only means of being dragged or closed.
    expect(bar?.nextElementSibling?.id).toBe("app-root");
    expect(document.body.classList.contains("has-titlebar")).toBe(true);
  });

  it("never mounts twice", () => {
    setDesktop(fakeBridge());
    expect(mod.initializeTitleBar()).toBe(true);
    expect(mod.initializeTitleBar()).toBe(false);
    expect(document.querySelectorAll("#titlebar")).toHaveLength(1);
  });

  it("waits for pywebviewready when the bridge arrives late", async () => {
    mod.mountTitleBar();
    expect(titlebar()).toBeNull();
    setDesktop(fakeBridge());
    window.dispatchEvent(new Event("pywebviewready"));
    await flush();
    expect(titlebar()).not.toBeNull();
  });

  it("mounts already hidden when the window restored into fullscreen", async () => {
    const api = fakeBridge();
    api.window_is_fullscreen.mockResolvedValue(true);
    setDesktop(api);
    mod.mountTitleBar();
    await flush();

    expect(titlebar()).not.toBeNull();
    // Asked before the bar is built, so a remembered fullscreen never paints a
    // caption for a frame on its way to hiding one.
    expect(document.body.classList.contains("window-fullscreen")).toBe(true);
  });
});

describe("window controls", () => {
  it("routes each button to the native window", () => {
    const api = fakeBridge();
    setDesktop(api);
    mod.initializeTitleBar();

    /** @type {HTMLElement|null} */ (document.getElementById("titlebar-min"))?.click();
    expect(api.window_minimize).toHaveBeenCalledTimes(1);

    /** @type {HTMLElement|null} */ (document.getElementById("titlebar-close"))?.click();
    expect(api.window_close).toHaveBeenCalledTimes(1);

    /** @type {HTMLElement|null} */ (document.getElementById("titlebar-max"))?.click();
    expect(api.window_toggle_maximize).toHaveBeenCalledTimes(1);
  });

  it("maximizes on a double-click of the drag strip", () => {
    const api = fakeBridge();
    setDesktop(api);
    mod.initializeTitleBar();
    document.getElementById("titlebar-drag")?.dispatchEvent(new Event("dblclick"));
    expect(api.window_toggle_maximize).toHaveBeenCalledTimes(1);
  });
});

describe("native callbacks", () => {
  it("reports metrics in physical pixels, not CSS pixels", () => {
    const api = fakeBridge();
    setDesktop(api);
    Reflect.set(window, "devicePixelRatio", 2);
    mod.initializeTitleBar();

    expect(api.set_chrome_metrics).toHaveBeenCalled();
    const metrics = api.set_chrome_metrics.mock.calls.at(-1)?.[0];
    expect(metrics).toHaveProperty("drag");
    expect(metrics).toHaveProperty("maxButton");
    expect(metrics.drag).toHaveLength(1);
    // Windows hit-tests in physical pixels; sending CSS pixels would put the
    // drag strip in the wrong place on any scaled display.
    expect(metrics.drag[0]).toHaveLength(4);
    expect(metrics.drag[0].every((/** @type {number} */ v) => Number.isInteger(v))).toBe(true);
    Reflect.set(window, "devicePixelRatio", 1);
  });

  it("swaps the glyph and re-reports when the window is maximized", () => {
    const api = fakeBridge();
    setDesktop(api);
    mod.initializeTitleBar();
    const before = api.set_chrome_metrics.mock.calls.length;
    const maxButton = document.getElementById("titlebar-max");

    chrome().setMaximized(true);
    expect(document.body.classList.contains("window-maximized")).toBe(true);
    expect(maxButton?.getAttribute("aria-label")).toBe("Restore");
    // The button moves when the bar restyles, so the hit-test has to be redone.
    expect(api.set_chrome_metrics.mock.calls.length).toBeGreaterThan(before);

    chrome().setMaximized(false);
    expect(document.body.classList.contains("window-maximized")).toBe(false);
    expect(maxButton?.getAttribute("aria-label")).toBe("Maximize");
  });

  it("gives up the caption rectangles in fullscreen", () => {
    const api = fakeBridge();
    setDesktop(api);
    mod.initializeTitleBar();

    chrome().setFullscreen(true);
    expect(document.body.classList.contains("window-fullscreen")).toBe(true);
    const hidden = api.set_chrome_metrics.mock.calls.at(-1)?.[0];
    // Keeping the old rectangles would leave a band across the top of the
    // screen that silently drags the window and offers Snap Layouts over
    // whatever the app is painting there.
    expect(hidden.drag).toEqual([]);
    expect(hidden.maxButton).toBeNull();

    chrome().setFullscreen(false);
    const restored = api.set_chrome_metrics.mock.calls.at(-1)?.[0];
    expect(restored.drag).toHaveLength(1);
    expect(restored.maxButton).not.toBeNull();
  });

  it("carries hover for the maximize button, which Windows takes over", () => {
    setDesktop(fakeBridge());
    mod.initializeTitleBar();
    const maxButton = document.getElementById("titlebar-max");

    // Reported as HTMAXBUTTON so Snap Layouts appear, which means :hover never
    // fires there and the native side has to drive the visual state.
    chrome().setMaxHover(true);
    expect(maxButton?.classList.contains("hover")).toBe(true);
    chrome().setMaxHover(false);
    expect(maxButton?.classList.contains("hover")).toBe(false);
  });
});
