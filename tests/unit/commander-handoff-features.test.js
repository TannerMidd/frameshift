import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  findMaterialTraders: vi.fn(),
  findTradeRoute: vi.fn(),
}));

vi.mock("../../ui/src/api/engineering.js", () => ({
  engineeringApi: {
    findMaterialTraders: apiMocks.findMaterialTraders,
  },
}));

vi.mock("../../ui/src/api/market.js", () => ({
  marketApi: {
    findTradeRoute: apiMocks.findTradeRoute,
  },
}));

import { StaleResponseError } from "../../ui/src/core/http.js";
import { appStore } from "../../ui/src/core/store.js";
import { searchMaterialTraders } from "../../ui/src/features/material-traders.js";
import { findBestLoop, findRoutes } from "../../ui/src/features/routes.js";

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, reject, resolve };
}

function publishCommander(commanderId, system) {
  appStore.setSnapshot({
    cargo_capacity: 64,
    commander_id: commanderId,
    system,
  });
}

function mountBestLoop() {
  document.body.innerHTML = `
    <button id="fp-bestloop" type="button">Find loop</button>
    <div id="fp-loop-status"></div>
    <div id="fp-loop-results"></div>
  `;
}

function mountRouteForm() {
  document.body.innerHTML = `
    <form>
      <button id="rf-go" type="submit">Find route</button>
      <div id="route-status"></div>
      <div id="route-results"></div>
      <select id="rf-mode"><option value="route" selected>Route</option></select>
      <input id="rf-capital" value="1000000">
      <input id="rf-cargo" value="64">
      <input id="rf-radius" value="100">
      <input id="rf-maxleg" value="50">
      <input id="rf-jumprange" value="30">
      <input id="rf-results" value="3">
      <input id="rf-minsupply" value="64">
      <input id="rf-hop" value="30">
      <input id="rf-hops" value="5">
      <input id="rf-lsdist" value="2000">
      <input id="rf-age" value="7">
      <input id="rf-largepad" type="checkbox">
    </form>
  `;
}

describe("commander handoff UI guards", () => {
  beforeEach(() => {
    apiMocks.findMaterialTraders.mockReset();
    apiMocks.findTradeRoute.mockReset();
    document.body.replaceChildren();
    appStore.clear();
  });

  it("does not let an old best-loop response overwrite a new commander's search", async () => {
    mountBestLoop();
    publishCommander("alpha", "Sol");
    const alphaResponse = deferred();
    const betaResponse = deferred();
    apiMocks.findTradeRoute
      .mockReturnValueOnce(alphaResponse.promise)
      .mockReturnValueOnce(betaResponse.promise);

    const alphaSearch = findBestLoop();
    publishCommander("beta", "Achenar");
    expect(document.querySelector("#fp-bestloop").disabled).toBe(false);
    expect(document.querySelector("#fp-loop-status").textContent).toBe("");
    const betaSearch = findBestLoop();

    alphaResponse.resolve({ loops: [] });
    await alphaSearch;

    expect(document.querySelector("#fp-loop-status").textContent).toContain(
      "Finding the best loop",
    );
    expect(document.querySelector("#fp-bestloop").disabled).toBe(true);
    expect(document.querySelector("#fp-loop-results").children).toHaveLength(0);

    betaResponse.resolve({ loops: [] });
    await betaSearch;
    expect(document.querySelector("#fp-loop-status").textContent).toContain("No profitable loop");
    expect(document.querySelector("#fp-bestloop").disabled).toBe(false);
  });

  it("does not let an old route response overwrite a new commander's search", async () => {
    mountRouteForm();
    publishCommander("alpha", "Sol");
    const alphaResponse = deferred();
    const betaResponse = deferred();
    apiMocks.findTradeRoute
      .mockReturnValueOnce(alphaResponse.promise)
      .mockReturnValueOnce(betaResponse.promise);

    const alphaSearch = findRoutes({ preventDefault: vi.fn() });
    publishCommander("beta", "Achenar");
    expect(document.querySelector("#rf-go").disabled).toBe(false);
    expect(document.querySelector("#route-status").textContent).toBe("");
    const betaSearch = findRoutes({ preventDefault: vi.fn() });

    alphaResponse.resolve({ hops: [], mode: "route", source: "local" });
    await alphaSearch;

    expect(document.querySelector("#route-status").textContent).toContain("Planning the chain");
    expect(document.querySelector("#rf-go").disabled).toBe(true);
    expect(document.querySelector("#route-results").children).toHaveLength(0);

    betaResponse.resolve({ hops: [], mode: "route", source: "local" });
    await betaSearch;
    expect(document.querySelector("#route-status").textContent).toContain("No profitable route");
    expect(document.querySelector("#rf-go").disabled).toBe(false);
  });

  it("does not turn stale material-trader failures into another commander's rows", async () => {
    document.body.innerHTML = [
      '<div id="mf-traders-status"></div>',
      '<button id="mf-go"></button>',
      '<div id="mf-traders-wrap"></div>',
      '<table id="mf-traders-table"><tbody></tbody></table>',
    ].join("");
    publishCommander("alpha", "Sol");
    const alphaResponses = [deferred(), deferred(), deferred()];
    const betaResponses = [deferred(), deferred(), deferred()];
    for (const response of [...alphaResponses, ...betaResponses]) {
      apiMocks.findMaterialTraders.mockReturnValueOnce(response.promise);
    }
    const kinds = ["raw", "manufactured", "encoded"];

    const alphaSearch = searchMaterialTraders({ kinds });
    publishCommander("beta", "Achenar");
    // The profile change resets the table and re-enables the button.
    expect(document.querySelector("#mf-traders-table tbody").children).toHaveLength(0);
    expect(document.querySelector("#mf-go").disabled).toBe(false);
    const betaSearch = searchMaterialTraders({ kinds });

    alphaResponses[0].reject(new StaleResponseError());
    alphaResponses[1].resolve({ traders: [] });
    alphaResponses[2].resolve({ traders: [] });
    await alphaSearch;

    // Alpha's flight is abandoned silently: no rows, no error text for beta.
    expect(document.querySelector("#mf-traders-table tbody").children).toHaveLength(0);
    expect(document.querySelector("#mf-traders-status").classList.contains("error")).toBe(false);

    betaResponses[0].resolve({
      traders: [{ station: "Jameson Memorial", system: "Shinrarta Dezhra", distance: 0 }],
    });
    betaResponses[1].resolve({ traders: [] });
    betaResponses[2].resolve({ traders: [] });
    await betaSearch;
    expect(document.querySelector("#mf-traders-table tbody").children).toHaveLength(1);
    expect(document.querySelector("#mf-traders-status").textContent).toContain("1 material trader");
  });
});
