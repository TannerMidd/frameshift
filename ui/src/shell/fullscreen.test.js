import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/** @type {typeof import("./fullscreen.js")} */
let mod;

/** The subset of the js_api bridge fullscreen talks to. */
function fakeBridge() {
  return {
    window_toggle_fullscreen: vi.fn(),
    window_set_fullscreen: vi.fn(),
  };
}

async function freshModule() {
  vi.resetModules();
  mod = await import("./fullscreen.js");
}

/** @param {ReturnType<typeof fakeBridge>|null} api */
function setDesktop(api) {
  if (api) Reflect.set(window, "pywebview", { api });
  else Reflect.deleteProperty(window, "pywebview");
}

/**
 * Take the key handler rather than dispatching at the real window.
 *
 * Each test imports a fresh copy of the module, but the listeners the previous
 * copies attached are still on the window and would answer the same keypress
 * with their own stale state. Capturing asserts what it registers for, and
 * leaves nothing behind to double-count.
 *
 * @returns {(event: KeyboardEvent) => void}
 */
function captureKeydown() {
  /** @type {((event: KeyboardEvent) => void)|null} */
  let handler = null;
  const spy = vi.spyOn(window, "addEventListener").mockImplementation((type, listener) => {
    // On the window, not the document: it is the last bubble target, so the
    // page's own dialogs get first refusal on Escape.
    if (type === "keydown") handler = /** @type {(event: KeyboardEvent) => void} */ (listener);
  });
  mod.initializeFullscreen();
  spy.mockRestore();
  if (!handler) throw new Error("initializeFullscreen registered no keydown listener");
  return handler;
}

/** @param {string} key */
function keyEvent(key) {
  return new KeyboardEvent("keydown", { key, cancelable: true });
}

function railButton() {
  const button = document.createElement("button");
  button.id = "fp-full";
  document.body.append(button);
  return button;
}

beforeEach(async () => {
  document.body.replaceChildren();
  document.body.className = "";
  setDesktop(null);
  await freshModule();
});

afterEach(() => {
  setDesktop(null);
  vi.useRealTimers();
});

describe("fullscreenKeyIntent", () => {
  it("reads F11 as a toggle from either state", () => {
    const base = { defaultPrevented: false };
    expect(mod.fullscreenKeyIntent({ ...base, key: "F11" }, { fullscreen: false })).toBe("toggle");
    expect(mod.fullscreenKeyIntent({ ...base, key: "F11" }, { fullscreen: true })).toBe("toggle");
  });

  it("lets Escape leave fullscreen but never enter it", () => {
    const escape = { key: "Escape", defaultPrevented: false };
    expect(mod.fullscreenKeyIntent(escape, { fullscreen: true })).toBe("exit");
    expect(mod.fullscreenKeyIntent(escape, { fullscreen: false })).toBeNull();
  });

  it("stands aside for whatever already used the key", () => {
    const used = { defaultPrevented: true };
    expect(mod.fullscreenKeyIntent({ ...used, key: "Escape" }, { fullscreen: true })).toBeNull();
    expect(mod.fullscreenKeyIntent({ ...used, key: "F11" }, { fullscreen: true })).toBeNull();
  });

  it("ignores every other key", () => {
    for (const key of ["f11", "F12", "Enter", "Esc", " ", "a"]) {
      expect(mod.fullscreenKeyIntent({ key, defaultPrevented: false }, { fullscreen: true })).toBe(
        null,
      );
    }
  });
});

describe("keyboard", () => {
  it("toggles the native window on F11 and consumes the key", () => {
    const api = fakeBridge();
    setDesktop(api);
    const event = keyEvent("F11");
    captureKeydown()(event);
    expect(api.window_toggle_fullscreen).toHaveBeenCalledTimes(1);
    expect(event.defaultPrevented).toBe(true);
  });

  it("leaves F11 alone when there is no native window", () => {
    // A paired tablet and a browser tab have their own fullscreen on this key.
    // Swallowing it here would take that away and give nothing back.
    const event = keyEvent("F11");
    captureKeydown()(event);
    expect(event.defaultPrevented).toBe(false);
  });

  it("exits on Escape only while fullscreen", () => {
    const api = fakeBridge();
    setDesktop(api);
    const handler = captureKeydown();

    handler(keyEvent("Escape"));
    expect(api.window_set_fullscreen).not.toHaveBeenCalled();

    mod.applyFullscreenState(true);
    const event = keyEvent("Escape");
    handler(event);
    expect(api.window_set_fullscreen).toHaveBeenCalledWith(false);
    expect(event.defaultPrevented).toBe(true);
  });

  it("stands aside for a dialog that already handled Escape", () => {
    const api = fakeBridge();
    setDesktop(api);
    const handler = captureKeydown();
    mod.applyFullscreenState(true);

    const event = keyEvent("Escape");
    event.preventDefault();
    handler(event);
    expect(api.window_set_fullscreen).not.toHaveBeenCalled();
  });
});

describe("state", () => {
  it("hides the title bar by marking the body", () => {
    mod.applyFullscreenState(true);
    expect(document.body.classList.contains("window-fullscreen")).toBe(true);
    expect(mod.isFullscreen()).toBe(true);

    mod.applyFullscreenState(false);
    expect(document.body.classList.contains("window-fullscreen")).toBe(false);
    expect(mod.isFullscreen()).toBe(false);
  });

  it("keeps the rail button in step, and survives it being absent", () => {
    // The window can be fullscreen before the shell has rendered the rail.
    expect(() => mod.syncFullscreenControl(true)).not.toThrow();

    const button = railButton();
    mod.applyFullscreenState(true);
    expect(button.getAttribute("aria-pressed")).toBe("true");
    expect(button.title).toContain("Esc");

    mod.applyFullscreenState(false);
    expect(button.getAttribute("aria-pressed")).toBe("false");
    expect(button.title).toContain("F11");
  });

  it("only asks the window to leave when it is actually fullscreen", () => {
    const api = fakeBridge();
    setDesktop(api);
    mod.exitFullscreen();
    expect(api.window_set_fullscreen).not.toHaveBeenCalled();

    mod.applyFullscreenState(true);
    mod.exitFullscreen();
    expect(api.window_set_fullscreen).toHaveBeenCalledWith(false);
  });
});

describe("the way back out", () => {
  const hint = () => document.getElementById("window-fullscreen-hint");

  it("says how to leave, then gets out of the way", () => {
    vi.useFakeTimers();
    mod.applyFullscreenState(true);

    const shown = hint();
    expect(shown?.textContent).toBe("Fullscreen. Press F11 or Esc to bring the window back.");
    expect(shown?.getAttribute("role")).toBe("status");
    expect(shown?.classList.contains("hidden")).toBe(false);

    vi.advanceTimersByTime(3200);
    expect(hint()?.classList.contains("hidden")).toBe(true);
  });

  it("clears immediately on the way out", () => {
    vi.useFakeTimers();
    mod.applyFullscreenState(true);
    mod.applyFullscreenState(false);
    expect(hint()?.classList.contains("hidden")).toBe(true);
  });

  it("stays quiet for the state the window booted into", () => {
    // Remembered fullscreen would otherwise nag on every single launch.
    mod.applyFullscreenState(true, false);
    expect(hint()).toBeNull();
    expect(document.body.classList.contains("window-fullscreen")).toBe(true);
  });
});
