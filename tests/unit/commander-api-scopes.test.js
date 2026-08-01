import { describe, expect, it, vi } from "vitest";

import { createEngineeringApi } from "../../ui/src/api/engineering.js";
import { createMarketApi } from "../../ui/src/api/market.js";

function clientStub() {
  return {
    json: vi.fn().mockResolvedValue({}),
  };
}

describe("commander-scoped planning APIs", () => {
  it("scopes trade-route planning to the active commander", async () => {
    const client = clientStub();
    const api = createMarketApi(client);
    const request = { mode: "loop", results: 3 };

    await api.findTradeRoute(request);

    expect(client.json).toHaveBeenCalledWith("/api/trade-route", {
      method: "POST",
      json: request,
      scope: "commander",
    });
  });

  it("scopes material-trader searches to the active commander", async () => {
    const client = clientStub();
    const api = createEngineeringApi(client);

    await api.findMaterialTraders("raw", "Sol");

    expect(client.json).toHaveBeenCalledWith("/api/material-traders?kind=raw&system=Sol", {
      scope: "commander",
    });
  });

  it("scopes material-body prospecting searches to the active commander", async () => {
    const client = clientStub();
    const api = createEngineeringApi(client);

    await api.findMaterialBodies("Phosphorus", "Sol", 12.5);

    expect(client.json).toHaveBeenCalledWith(
      "/api/material-bodies?material=Phosphorus&system=Sol&min_share=12.5",
      { scope: "commander" },
    );
  });
});
