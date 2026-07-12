"use strict";

/* Targeted browser-free runtime smoke test for the Specialist module.

   This deliberately evaluates the production bundle, invokes
   initSpecialists(), fetches a representative empty snapshot, and renders all
   four workflows. A syntax-only check cannot detect a missing function until
   the DOMContentLoaded path calls it; this harness does. */

const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");


class FakeClassList {
  constructor() { this.values = new Set(); }
  add(...names) { names.forEach((name) => this.values.add(name)); }
  remove(...names) { names.forEach((name) => this.values.delete(name)); }
  contains(name) { return this.values.has(name); }
  toggle(name, force) {
    const enabled = force == null ? !this.values.has(name) : !!force;
    if (enabled) this.values.add(name); else this.values.delete(name);
    return enabled;
  }
}


const elements = new Map();

class FakeElement {
  constructor(id = "") {
    this.id = id;
    this.classList = new FakeClassList();
    this.dataset = {};
    this.style = { setProperty() {} };
    this.children = [];
    this.listeners = {};
    this.attributes = {};
    this.value = "";
    this.textContent = "";
    this.innerHTML = "";
    this.disabled = false;
    this.hidden = false;
    this.checked = false;
    this.tabIndex = 0;
    this.offsetWidth = 1;
    this.offsetParent = {};
  }
  addEventListener(type, listener) { this.listeners[type] = listener; }
  removeEventListener(type) { delete this.listeners[type]; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name] ?? null; }
  appendChild(child) { this.children.push(child); child.parentElement = this; return child; }
  replaceChildren(...children) { this.children = [...children]; }
  querySelector(selector) { return getElement(`${this.id}:${selector}`); }
  querySelectorAll() { return []; }
  closest() { return null; }
  focus() {}
  remove() {
    if (this.parentElement) {
      this.parentElement.children = this.parentElement.children.filter((item) => item !== this);
    }
  }
}

function getElement(id) {
  if (!elements.has(id)) elements.set(id, new FakeElement(id));
  return elements.get(id);
}

const switchers = ["mining", "combat", "carrier", "exobiology"].map((name) => {
  const button = getElement(`switch-${name}`);
  button.dataset.specialist = name;
  return button;
});
const workflowPanels = ["mining", "combat", "carrier", "exobiology"].map((name) =>
  getElement(`sp-workflow-${name}`));

const documentStub = {
  hidden: false,
  body: getElement("body"),
  documentElement: getElement("document-element"),
  getElementById: getElement,
  createElement: (tag) => new FakeElement(tag),
  createElementNS: (_namespace, tag) => new FakeElement(tag),
  createComment: (text) => ({ text }),
  addEventListener() {},
  removeEventListener() {},
  execCommand: () => true,
  querySelector: () => new FakeElement("query"),
  querySelectorAll(selector) {
    if (selector === ".sp-switcher [data-specialist]") return switchers;
    if (selector === ".sp-workflow") return workflowPanels;
    return [];
  },
};

const storage = new Map();
const localStorageStub = {
  getItem: (key) => storage.has(key) ? storage.get(key) : null,
  setItem: (key, value) => storage.set(key, String(value)),
  removeItem: (key) => storage.delete(key),
};

const emptySnapshot = {
  commander_id: "cmdr-runtime-test",
  mining: { active: false, session: null, history: [] },
  combat: {
    active: false, session: null, target: null, history: [],
    readiness: { level: "not_ax_equipped", score: 0, checklist: {}, ammo: { by_module: [] } },
  },
  carrier: { finance: {}, upkeep: {}, space: {}, orders: { items: [] }, inventory: {}, route: { legs: [], leg_count: 0 } },
  exobiology: { position: null, sampling: null, current_map: null },
};

const windowStub = {
  FrameshiftGalaxyData: {},
  location: { href: "http://127.0.0.1:8666/", origin: "http://127.0.0.1:8666" },
  addEventListener() {},
  removeEventListener() {},
  scrollTo() {},
  confirm: () => true,
};

const context = vm.createContext({
  console,
  document: documentStub,
  window: windowStub,
  localStorage: localStorageStub,
  navigator: {},
  history: { replaceState() {} },
  location: windowStub.location,
  URL,
  URLSearchParams,
  Blob,
  FormData: class FormData {},
  Node: { DOCUMENT_POSITION_PRECEDING: 2 },
  fetch: async () => ({
    ok: true,
    status: 200,
    headers: { get: () => null },
    text: async () => JSON.stringify(emptySnapshot),
    blob: async () => new Blob([]),
    json: async () => emptySnapshot,
  }),
  setInterval: () => 1,
  clearInterval() {},
  setTimeout: () => 1,
  clearTimeout() {},
  requestAnimationFrame: (callback) => callback(),
  cancelAnimationFrame() {},
  confirm: () => true,
  alert() {},
});
context.globalThis = context;

const appPath = path.resolve(__dirname, "..", "ui", "app.js");
const source = fs.readFileSync(appPath, "utf8");
vm.runInContext(source, context, { filename: appPath });

const required = [
  "setSpecialistWorkflow", "specialistVisible", "specialistError", "specialistJson",
  "normaliseSpecialistSnapshot", "specialistWorkflow", "specialistHistory", "loadSpecialists",
  "specialistDuration", "specialistTimestamp", "specialistAgo", "specialistNumber",
  "specialistHumanName", "renderSpecialistFacts", "renderSpecialistHistory",
  "renderMiningSpecialist", "renderCombatSpecialist", "renderCarrierSpecialist",
  "renderExobiologySpecialist", "renderSpecialists", "initSpecialists",
];
for (const name of required) {
  const type = vm.runInContext(`typeof ${name}`, context);
  if (type !== "function") throw new Error(`${name} is ${type}, expected function`);
}

(async () => {
  vm.runInContext('state = { commander_id: "cmdr-runtime-test", commander: "Runtime Test" };', context);
  vm.runInContext("initSpecialists()", context);
  await vm.runInContext("loadSpecialists()", context);
  const renderedState = vm.runInContext("specialistState", context);
  if (!renderedState || !renderedState.mining || !renderedState.exobiology) {
    throw new Error("specialist snapshot did not load and render");
  }
  process.stdout.write("specialist runtime OK: initialization and four-workflow render completed\n");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
