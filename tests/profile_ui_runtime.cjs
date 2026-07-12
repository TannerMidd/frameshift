"use strict";

/* Runtime checks for profile-local browser state and the mandatory pairing
   dialog. This evaluates the production bundle but does not run DOMContentLoaded. */

const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");


class FakeClassList {
  constructor(...values) { this.values = new Set(values); }
  add(...values) { values.forEach((value) => this.values.add(value)); }
  remove(...values) { values.forEach((value) => this.values.delete(value)); }
  contains(value) { return this.values.has(value); }
  toggle(value, force) {
    const enabled = force == null ? !this.contains(value) : !!force;
    if (enabled) this.add(value); else this.remove(value);
    return enabled;
  }
}


let documentStub;
const elements = new Map();

class FakeElement {
  constructor(id = "") {
    this.id = id;
    this.classList = new FakeClassList();
    this.dataset = {};
    this.attributes = {};
    this.children = [];
    this.value = "";
    this.textContent = "";
    this.innerHTML = "";
    this.disabled = false;
    this.inert = false;
    this.isConnected = true;
    this.style = { setProperty() {} };
  }
  addEventListener() {}
  removeEventListener() {}
  appendChild(child) { this.children.push(child); child.parentElement = this; return child; }
  append(...children) { children.forEach((child) => this.appendChild(child)); }
  replaceChildren(...children) { this.children = children; }
  reset() { this.value = ""; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name] ?? null; }
  removeAttribute(name) { delete this.attributes[name]; }
  focus() { documentStub.activeElement = this; }
  scrollIntoView() {}
  closest(selector) {
    if (selector === ".hidden") return this.classList.contains("hidden") ? this : null;
    return null;
  }
  querySelector(selector) {
    if (this.id === "pairing-gate" && selector === ".pairing-panel") return getElement("pairing-panel");
    return getElement(`${this.id}:${selector}`);
  }
  querySelectorAll(selector) {
    if (this.id === "pairing-gate" && selector.includes("button:not")) {
      return [getElement("pairing-retry")];
    }
    return [];
  }
}

function getElement(id) {
  if (!elements.has(id)) elements.set(id, new FakeElement(id));
  return elements.get(id);
}

const gate = getElement("pairing-gate");
gate.classList.add("hidden");
const panel = getElement("pairing-panel");
gate.appendChild(panel);
const background = getElement("background-app");
const body = getElement("body");
body.children = [gate, background];

documentStub = {
  body,
  activeElement: background,
  getElementById: getElement,
  createElement: (tag) => new FakeElement(tag),
  createElementNS: (_namespace, tag) => new FakeElement(tag),
  addEventListener() {},
  removeEventListener() {},
  querySelector: () => new FakeElement("query"),
  querySelectorAll: () => [],
};

const storage = new Map();
const localStorageStub = {
  getItem: (key) => storage.has(key) ? storage.get(key) : null,
  setItem: (key, value) => storage.set(key, String(value)),
  removeItem: (key) => storage.delete(key),
};

let fetchImpl = async () => ({ ok: true, status: 200, json: async () => ({}) });
const windowStub = {
  FrameshiftGalaxyData: {},
  location: { href: "http://192.168.1.65:8667/", origin: "http://192.168.1.65:8667" },
  confirm: () => true,
  addEventListener() {},
  removeEventListener() {},
};

const context = vm.createContext({
  console,
  document: documentStub,
  window: windowStub,
  HTMLElement: FakeElement,
  localStorage: localStorageStub,
  navigator: {},
  history: { replaceState() {} },
  location: windowStub.location,
  URL,
  URLSearchParams,
  Blob,
  FormData: class FormData {},
  Node: { DOCUMENT_POSITION_PRECEDING: 2 },
  fetch: (...args) => fetchImpl(...args),
  setInterval: () => 1,
  clearInterval() {},
  setTimeout: (callback, delay) => { if (delay === 0) callback(); return 1; },
  clearTimeout() {},
  requestAnimationFrame: (callback) => callback(),
  cancelAnimationFrame() {},
  confirm: () => true,
  alert() {},
});
context.globalThis = context;

const appPath = path.resolve(__dirname, "..", "ui", "app.js");
vm.runInContext(fs.readFileSync(appPath, "utf8"), context, { filename: appPath });

// An unscoped v2.0 route is adopted once. The Legacy profile with the same
// display name receives an independent empty key.
storage.set("activeRoute", JSON.stringify({ waypoints: [{ system: "Sol" }], index: 0 }));
storage.set("galaxyHistory:v1:Same%20Name", JSON.stringify([{ system: "Sol" }]));
vm.runInContext(`
  state = { commander_id: "cmdr-live", commander: "Same Name" };
  loadActiveRoute("cmdr-live");
  loadGalaxyHistory("cmdr-live", "Same Name");
`, context);
if (!storage.has("activeRoute:v2:cmdr-live") || storage.has("activeRoute")) {
  throw new Error("legacy active route was not migrated exactly once");
}
if (!storage.has("galaxyHistory:v2:cmdr-live") || storage.has("galaxyHistory:v1:Same%20Name")) {
  throw new Error("legacy Galaxy history was not migrated to the profile key");
}
vm.runInContext('loadActiveRoute("cmdr-legacy"); loadGalaxyHistory("cmdr-legacy", "Same Name");', context);
if (vm.runInContext("activeRoute !== null || galaxyHistory.length !== 0", context)) {
  throw new Error("Live browser state leaked into the Legacy profile");
}

(async () => {
  // Every profile mutation carries the commander the user was actually
  // looking at when they clicked, so a server-side handoff can reject it.
  let mutationRequest;
  fetchImpl = async (url, options) => {
    mutationRequest = { url, options };
    return { ok: true, status: 200 };
  };
  await vm.runInContext(`
    state = { commander_id: "cmdr-alpha", commander: "Alpha" };
    commanderFetch("/api/engineering/pin", { method: "POST", body: "{}" });
  `, context);
  if (mutationRequest?.options?.headers?.["X-Frameshift-Commander"] !== "cmdr-alpha") {
    throw new Error("profile mutation did not carry the displayed commander");
  }

  // An analytics response started for a previous profile must not repaint the
  // new commander's dashboard after a handoff.
  let releaseAnalytics;
  fetchImpl = async () => new Promise((resolve) => { releaseAnalytics = resolve; });
  getElement("an-days").value = "30";
  getElement("an-today").textContent = "CLEARED";
  const staleAnalytics = vm.runInContext(`
    profileGeneration = 20;
    state = { commander_id: "cmdr-alpha", commander: "Alpha" };
    loadAnalytics();
  `, context);
  vm.runInContext(`
    profileGeneration = 21;
    state = { commander_id: "cmdr-beta", commander: "Beta" };
  `, context);
  releaseAnalytics({
    ok: true,
    status: 200,
    json: async () => ({
      commander_id: "cmdr-alpha",
      today: { profit: 999 }, week: { profit: 999 }, period: { profit: 999, tons: 9 },
      session: {}, earnings: {}, balance: [], daily: [], top: [],
    }),
  });
  await staleAnalytics;
  if (getElement("an-today").textContent !== "CLEARED") {
    throw new Error("stale analytics response crossed the commander handoff");
  }

  // Route watches and alert text are commander-owned too. A slower response
  // from Alpha cannot populate Beta's global strip.
  let releaseAlerts;
  fetchImpl = async () => new Promise((resolve) => { releaseAlerts = resolve; });
  vm.runInContext("clearAlertWorkspace();", context);
  const staleAlerts = vm.runInContext(`
    profileGeneration = 30;
    state = { commander_id: "cmdr-alpha", commander: "Alpha" };
    pollAlerts();
  `, context);
  vm.runInContext(`
    profileGeneration = 31;
    state = { commander_id: "cmdr-beta", commander: "Beta" };
  `, context);
  releaseAlerts({
    ok: true,
    status: 200,
    json: async () => ({
      commander_id: "cmdr-alpha",
      watches: [{ id: 1, label: "Alpha's trade loop" }],
      alerts: [{ ts: "alpha", text: "Alpha-only market alert" }],
    }),
  });
  await staleAlerts;
  if (getElement("watch-list").children.length !== 0
      || !getElement("alert-strip").classList.contains("hidden")) {
    throw new Error("stale route alerts crossed the commander handoff");
  }

  // A revoked session must discard live state and put keyboard/screen-reader
  // focus inside a modal while every application sibling is inert.
  fetchImpl = async () => ({
    ok: false,
    status: 401,
    json: async () => ({ pairing_required: true, error: "Device revoked" }),
  });
  vm.runInContext('state = { commander_id: "cmdr-live", commander: "Same Name", system: "Sol" };', context);
  await vm.runInContext("poll()", context);
  if (!vm.runInContext("state === null && securityLocked === true", context)) {
    throw new Error("revoked poll retained authenticated state");
  }
  if (gate.classList.contains("hidden") || !background.inert || background.getAttribute("aria-hidden") !== "true") {
    throw new Error("pairing modal did not isolate the previous application surface");
  }
  if (documentStub.activeElement !== getElement("pairing-retry")) {
    throw new Error("pairing modal did not move focus to its retry action");
  }
  vm.runInContext("setPairingModalOpen(false)", context);
  if (background.inert || background.getAttribute("aria-hidden") != null || documentStub.activeElement !== background) {
    throw new Error("pairing modal did not restore background accessibility/focus state");
  }
  process.stdout.write("profile UI runtime OK: scoped storage, analytics/alert handoff, revoked gate, inert/focus restore\n");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
