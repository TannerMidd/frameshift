import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  findDataSaleStations: vi.fn(),
  findExobiologyRoute: vi.fn(),
  findInterstellarFactors: vi.fn(),
  findMaterialBodies: vi.fn(),
  findMaterialTraders: vi.fn(),
  findMiningHotspots: vi.fn(),
  findMiningLocations: vi.fn(),
  planNeutronRoute: vi.fn(),
  planRichesRoute: vi.fn(),
  searchCommodities: vi.fn(),
  searchStations: vi.fn(),
  getSystemStations: vi.fn(),
}));

vi.mock("../../ui/src/api/engineering.js", () => ({
  engineeringApi: {
    findMaterialBodies: apiMocks.findMaterialBodies,
    findMaterialTraders: apiMocks.findMaterialTraders,
  },
}));

vi.mock("../../ui/src/api/market.js", () => ({
  marketApi: {
    findDataSaleStations: apiMocks.findDataSaleStations,
    findInterstellarFactors: apiMocks.findInterstellarFactors,
    findMiningHotspots: apiMocks.findMiningHotspots,
    findMiningLocations: apiMocks.findMiningLocations,
    searchCommodities: apiMocks.searchCommodities,
    searchStations: apiMocks.searchStations,
  },
}));

vi.mock("../../ui/src/api/navigation.js", () => ({
  navigationApi: {
    findExobiologyRoute: apiMocks.findExobiologyRoute,
    planNeutronRoute: apiMocks.planNeutronRoute,
    planRichesRoute: apiMocks.planRichesRoute,
  },
}));

vi.mock("../../ui/src/api/system.js", () => ({
  systemApi: {
    getSystemStations: apiMocks.getSystemStations,
  },
}));

vi.mock("../../ui/src/features/routes.js", () => ({
  trackButton: () => document.createElement("button"),
}));

vi.mock("../../ui/src/shell/status.js", () => ({
  copySystemButton: () => document.createElement("button"),
  plotButton: () => document.createElement("button"),
}));

import { StaleResponseError } from "../../ui/src/core/http.js";
import { appStore } from "../../ui/src/core/store.js";
import { searchCommodity } from "../../ui/src/features/commodities.js";
import { searchExobio } from "../../ui/src/features/exobio.js";
import { searchMaterialBodies } from "../../ui/src/features/material-bodies.js";
import { searchMaterialTraders } from "../../ui/src/features/material-traders.js";
import { searchMining, showHotspots } from "../../ui/src/features/mining.js";
import { planNeutron, planRiches } from "../../ui/src/features/planners.js";
import { findInterstellarFactors, findSellPoints } from "../../ui/src/features/services.js";
import { searchStations } from "../../ui/src/features/stations.js";
import { loadSystemStations } from "../../ui/src/features/system-stations.js";

function deferred() {
  let reject;
  let resolve;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, reject, resolve };
}

function submitEvent() {
  return { preventDefault: vi.fn() };
}

function mountDiscoveryForms() {
  document.body.innerHTML = `
    <div id="cs-status"></div>
    <button id="cs-go"></button>
    <select id="cs-mode"><option value="sell" selected>Sell</option></select>
    <input id="cs-near" value="">
    <input id="cs-query" value="Gold">
    <input id="cs-radius" value="50">
    <input id="cs-min" value="1">
    <input id="cs-largepad" type="checkbox">
    <table id="cs-table"><thead></thead><tbody></tbody></table>

    <div id="mining-status"></div>
    <button id="mn-go"></button>
    <input id="mn-near" value="">
    <input id="mn-radius" value="50">
    <input id="mn-minprice" value="0">
    <input id="mn-age" value="30">
    <input id="mn-largepad" type="checkbox">
    <table id="mining-table"><thead></thead><tbody><tr id="hotspot-anchor"></tr></tbody></table>
    <button id="hotspot-go"></button>

    <div id="exo-status"></div>
    <button id="exo-go"></button>
    <div id="exo-results"></div>
    <input id="exo-grav" value="0.5">
    <input id="exo-minvalue" value="1000000">

    <div id="sd-status"></div>
    <button id="sd-go"></button>
    <div id="sd-results"></div>
    <input id="sd-carriers" type="checkbox">

    <div id="iff-status"></div>
    <button id="iff-go"></button>
    <div id="iff-results"></div>

    <div id="rr-status"></div>
    <button id="rr-go"></button>
    <div id="rr-results"></div>
    <input id="rr-range" value="30">
    <input id="rr-radius" value="50">
    <input id="rr-minvalue" value="300000">
    <input id="rr-max" value="30">
    <input id="rr-loop" type="checkbox" checked>

    <div id="nr-status"></div>
    <button id="nr-go"></button>
    <input id="nr-to" value="Colonia">
    <input id="nr-range" value="30">
    <input id="nr-eff" value="60">
    <table id="nr-table"><tbody></tbody></table>

    <div id="os-status"></div>
    <button id="os-go"></button>
    <input id="os-near" value="">
    <input id="os-query" value="Fuel Scoop">
    <select id="os-type"><option value="module" selected>Module</option></select>
    <table id="os-table"><thead></thead><tbody></tbody></table>

    <div id="ss-status"></div>
    <button id="ss-go"></button>
    <input id="ss-system" value="">
    <div id="ss-list"></div>

    <div id="mf-status"></div>
    <button id="mf-go"></button>
    <div id="mf-traders-status"></div>
    <div id="mf-traders-wrap"></div>
    <table id="mf-traders-table"><thead></thead><tbody></tbody></table>
    <div id="mf-bodies-status"></div>
    <div id="mf-bodies-wrap"></div>
    <table id="mf-bodies-table"><thead></thead><tbody></tbody></table>
  `;
}

describe("snapshot-defaulted discovery handoffs", () => {
  beforeEach(() => {
    for (const mock of Object.values(apiMocks)) mock.mockReset();
    appStore.clear();
    mountDiscoveryForms();
  });

  it("suppresses stale errors and restores every handoff-only search control", async () => {
    appStore.setSnapshot({ commander_id: "alpha", system: "Sol" });
    const requests = Object.fromEntries(Object.keys(apiMocks).map((name) => [name, deferred()]));
    for (const [name, request] of Object.entries(requests)) {
      apiMocks[name].mockReturnValueOnce(request.promise);
    }

    const pending = [
      searchCommodity(submitEvent()),
      searchMining(submitEvent()),
      showHotspots(
        "Painite",
        document.querySelector("#hotspot-go"),
        document.querySelector("#hotspot-anchor"),
      ),
      searchExobio(submitEvent()),
      findSellPoints(submitEvent()),
      findInterstellarFactors(submitEvent()),
      planRiches(submitEvent()),
      planNeutron(submitEvent()),
      searchStations(submitEvent()),
      loadSystemStations(submitEvent()),
      // One kind only: the shared mock yields a single deferred per call.
      searchMaterialTraders({ kinds: ["raw"] }),
      searchMaterialBodies({ material: "Phosphorus" }),
    ];
    const buttonIds = [
      "cs-go",
      "mn-go",
      "hotspot-go",
      "exo-go",
      "sd-go",
      "iff-go",
      "rr-go",
      "nr-go",
      "os-go",
      "ss-go",
      "mf-go",
    ];
    for (const id of buttonIds) expect(document.getElementById(id).disabled).toBe(true);

    appStore.setSnapshot({ commander_id: "beta", system: "Achenar" });
    for (const request of Object.values(requests)) {
      request.reject(new StaleResponseError());
    }
    await Promise.all(pending);

    for (const id of buttonIds) expect(document.getElementById(id).disabled).toBe(false);
    for (const status of document.querySelectorAll('[id$="-status"]')) {
      expect(status.classList.contains("error")).toBe(false);
      expect(status.textContent).not.toContain("Commander changed");
    }
  });

  it("does not let an old response unlock or overwrite a newer commander search", async () => {
    const alphaResponse = deferred();
    const betaResponse = deferred();
    apiMocks.searchCommodities
      .mockReturnValueOnce(alphaResponse.promise)
      .mockReturnValueOnce(betaResponse.promise);

    appStore.setSnapshot({ commander_id: "alpha", system: "Sol" });
    const alphaSearch = searchCommodity(submitEvent());
    appStore.setSnapshot({ commander_id: "beta", system: "Achenar" });
    const betaSearch = searchCommodity(submitEvent());

    alphaResponse.resolve({
      commodity: "Gold",
      results: [{ station: "ALPHA PRIVATE MARKET", system: "Sol" }],
    });
    await alphaSearch;

    expect(document.querySelector("#cs-go").disabled).toBe(true);
    expect(document.body.textContent).not.toContain("ALPHA PRIVATE MARKET");
    expect(document.querySelector("#cs-status").textContent).toContain("Searching");

    betaResponse.resolve({ commodity: "Gold", results: [] });
    await betaSearch;

    expect(document.querySelector("#cs-go").disabled).toBe(false);
    expect(document.body.textContent).not.toContain("ALPHA PRIVATE MARKET");
  });

  it("clears System Stations and ignores its old commander response", async () => {
    const alphaResponse = deferred();
    apiMocks.getSystemStations.mockReturnValueOnce(alphaResponse.promise);
    appStore.setSnapshot({ commander_id: "alpha", system: "Sol" });
    document.querySelector("#ss-system").value = "Sol";

    const pending = loadSystemStations(submitEvent());
    expect(document.querySelector("#ss-go").disabled).toBe(true);

    appStore.setSnapshot({ commander_id: "beta", system: "Achenar" });
    expect(document.querySelector("#ss-system").value).toBe("");
    expect(document.querySelector("#ss-status").textContent).toBe("");
    expect(document.querySelector("#ss-go").disabled).toBe(false);

    alphaResponse.resolve({
      system: "Sol",
      stations: [{ station: "ALPHA PRIVATE PORT", services: [] }],
    });
    await pending;

    expect(document.body.textContent).not.toContain("ALPHA PRIVATE PORT");
    expect(document.querySelector("#ss-go").disabled).toBe(false);
  });
});
