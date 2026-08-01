import { beforeEach, describe, expect, it } from "vitest";

import { CS_SORT_COLUMNS, bumpSort, sortedRows } from "../../ui/src/features/commodities.js";
import { engineeringGradeText, engineeringMatches } from "../../ui/src/features/engineering.js";
import {
  galaxyHistoryKey,
  getGalaxyHistory,
  getGalaxyHistoryCommander,
  loadGalaxyHistory,
  powerplayStateNote,
  ppProgressPercent,
} from "../../ui/src/features/galaxy.js";
import {
  activeRouteKey,
  clearRouteWorkspace,
  confidenceAgeText,
  confidenceHtml,
  creditRangeHtml,
  getActiveRoute,
  loadActiveRoute,
} from "../../ui/src/features/routes.js";
import { MN_SORT_COLUMNS } from "../../ui/src/features/mining.js";
import { OS_SORT_COLUMNS } from "../../ui/src/features/stations.js";
import {
  resolveMaterial,
  setMaterialReference,
  tradeLines,
} from "../../ui/src/features/material-finder.js";
import { MT_SORT_COLUMNS } from "../../ui/src/features/material-traders.js";
import { MB_SORT_COLUMNS } from "../../ui/src/features/material-bodies.js";
import { panelModeOnLaunch } from "../../ui/src/shell/panel.js";

describe("panel preference behavior", () => {
  it("defaults fresh devices to Panel while respecting both saved choices", () => {
    const storage = (value) => ({
      getItem: () => value,
    });

    expect(panelModeOnLaunch(storage(null))).toBe(true);
    expect(panelModeOnLaunch(storage("1"))).toBe(true);
    expect(panelModeOnLaunch(storage("0"))).toBe(false);
    expect(
      panelModeOnLaunch({
        getItem() {
          throw new Error("storage denied");
        },
      }),
    ).toBe(true);
  });
});

describe("search ordering behavior", () => {
  const rows = [
    { station: "Zulu", sell_price: 52_000, buy_price: 50_000 },
    { station: "Alpha", sell_price: 48_000, buy_price: 44_000 },
  ];

  it("sorts without mutating API results and follows column defaults", () => {
    const sorted = sortedRows(rows, CS_SORT_COLUMNS, { key: "station", dir: 1 }, "sell");

    expect(sorted.map((row) => row.station)).toEqual(["Alpha", "Zulu"]);
    expect(rows.map((row) => row.station)).toEqual(["Zulu", "Alpha"]);
    expect(bumpSort(null, "units", CS_SORT_COLUMNS)).toEqual({
      key: "units",
      dir: -1,
    });
    expect(bumpSort({ key: "station", dir: 1 }, "station", CS_SORT_COLUMNS)).toEqual({
      key: "station",
      dir: -1,
    });
  });

  it("uses the shared sorter for mining and station result columns", () => {
    const mining = [
      { name: "Painite", sell_price: 600_000 },
      { name: "Platinum", sell_price: 250_000 },
    ];
    expect(
      sortedRows(mining, MN_SORT_COLUMNS, { key: "sell", dir: -1 }).map((row) => row.name),
    ).toEqual(["Painite", "Platinum"]);

    const stations = [
      { station: "Far", distance: 40 },
      { station: "Near", distance: 3 },
    ];
    expect(
      sortedRows(stations, OS_SORT_COLUMNS, { key: "jump", dir: 1 }).map((row) => row.station),
    ).toEqual(["Near", "Far"]);
  });
});

describe("route risk presentation", () => {
  it("renders bounded confidence and conservative ranges", () => {
    expect(confidenceAgeText(90)).toBe("2m old");
    expect(confidenceAgeText(172_800)).toBe("2d old");

    const markup = confidenceHtml({
      band: "unexpected",
      score: 42.4,
      source: "local market",
      age_s: 90,
      reasons: ["thin <supply>"],
    });
    expect(markup).toContain('class="confidence confidence-low"');
    expect(markup).toContain("LOW 42");
    expect(markup).toContain("thin &lt;supply&gt;");
    expect(creditRangeHtml({ low: 1000, observed: 1500 })).toContain("conservative 1,000–1,500 cr");
  });
});

describe("engineering catalog behavior", () => {
  const blueprint = {
    id: "fsd-range",
    kind: "ship-engineering",
    kind_label: "Ship engineering",
    display_name: "Frame Shift Drive · Increased Range",
    module: "Frame Shift Drive",
    name: "increased_range",
    engineers: ["Felicity Farseer"],
  };

  it("matches every search term across labels, modules, and engineers", () => {
    expect(engineeringMatches(blueprint, "felicity range", "ship-engineering")).toBe(true);
    expect(engineeringMatches(blueprint, "felicity armor", "ship-engineering")).toBe(false);
    expect(engineeringMatches(blueprint, "range", "odyssey-upgrade")).toBe(false);
    expect(
      engineeringGradeText({
        kind: "ship-engineering",
        current_grade: 2,
        target_grade: 5,
      }),
    ).toBe("G2 → G5");
  });
});

describe("material finder resolution", () => {
  // Mirrors the raw category 2 column the server ships, plus one Guardian
  // material that has a family but no trader column.
  const material = (name, symbol, grade, extra = {}) => ({
    name,
    symbol,
    kind: "raw",
    family: "Category2",
    grade,
    tradeable: true,
    source: "Surface prospecting",
    ...extra,
  });
  const catalog = {
    groups: [],
    kind_labels: {},
    stats: {},
    materials: [
      material("Phosphorus", "phosphorus", 1),
      material("Chromium", "chromium", 2),
      material("Molybdenum", "molybdenum", 3),
      material("Technetium", "technetium", 4),
      material("Nickel", "nickel", 1, { family: "Category5" }),
      material("Sensor Fragment", "sensorfragment", 5, {
        kind: "manufactured",
        family: "ThargoidSite",
        tradeable: false,
      }),
    ],
    material_columns: {
      "raw/Category2": { label: "Raw category 2", tradeable: true },
      "raw/Category5": { label: "Raw category 5", tradeable: true },
      "manufactured/ThargoidSite": { label: "Thargoid structures", tradeable: false },
    },
    trade_ratios: { up: 6, down: 3 },
  };

  beforeEach(() => {
    document.body.innerHTML = "";
    setMaterialReference(catalog);
  });

  it("resolves a material to its column and same-column peers only", () => {
    const resolved = resolveMaterial("Phosphorus");
    expect(resolved.material.symbol).toBe("phosphorus");
    expect(resolved.column.label).toBe("Raw category 2");
    // Nickel shares the kind but not the column, so it is never a peer.
    expect(resolved.peers.map((peer) => peer.name)).toEqual([
      "Chromium",
      "Molybdenum",
      "Technetium",
    ]);
  });

  it("folds case and punctuation the way the bundled catalog does", () => {
    expect(resolveMaterial("phosphorus").material.symbol).toBe("phosphorus");
    expect(resolveMaterial("  PHOSPHORUS  ".trim()).material.symbol).toBe("phosphorus");
    expect(resolveMaterial("Sensor-Fragment").material.symbol).toBe("sensorfragment");
    expect(resolveMaterial("unobtainium")).toBe(null);
  });

  it("prices every peer against the target, cheapest first", () => {
    const target = resolveMaterial("Phosphorus");
    expect(tradeLines(target.material, target.peers, catalog.trade_ratios)).toEqual([
      { peer: "Chromium", grade: 2, spend: 1, gain: 3, direction: "down" },
      { peer: "Molybdenum", grade: 3, spend: 1, gain: 9, direction: "down" },
      { peer: "Technetium", grade: 4, spend: 1, gain: 27, direction: "down" },
    ]);
  });

  it("charges 6x per grade step when trading up", () => {
    const target = resolveMaterial("Technetium");
    expect(tradeLines(target.material, target.peers, catalog.trade_ratios)).toEqual([
      { peer: "Molybdenum", grade: 3, spend: 6, gain: 1, direction: "up" },
      { peer: "Chromium", grade: 2, spend: 36, gain: 1, direction: "up" },
      { peer: "Phosphorus", grade: 1, spend: 216, gain: 1, direction: "up" },
    ]);
  });

  it("keeps a non-tradeable material's column without offering trades", () => {
    const resolved = resolveMaterial("Sensor Fragment");
    expect(resolved.column).toEqual({ label: "Thargoid structures", tradeable: false });
    expect(resolved.material.tradeable).toBe(false);
  });

  it("sorts trader and body rows without mutating the source array", () => {
    const traders = [
      { station: "B", system: "Beta", kind: "raw", distance: 20, dist_ls: 500, large_pad: false },
      { station: "A", system: "Alpha", kind: "encoded", distance: 5, dist_ls: 20, large_pad: true },
    ];
    const original = [...traders];
    expect(
      sortedRows(traders, MT_SORT_COLUMNS, bumpSort(null, "jump", MT_SORT_COLUMNS)).map(
        (row) => row.station,
      ),
    ).toEqual(["A", "B"]);
    expect(traders).toEqual(original);

    const bodies = [
      { body: "One", subtype: "Icy body", share: 8.1, system: "Alpha", distance: 4 },
      { body: "Two", subtype: "Rocky body", share: 14.2, system: "Beta", distance: 9 },
    ];
    // Share defaults to richest-first; distance defaults to nearest-first.
    expect(
      sortedRows(bodies, MB_SORT_COLUMNS, bumpSort(null, "share", MB_SORT_COLUMNS)).map(
        (row) => row.body,
      ),
    ).toEqual(["Two", "One"]);
    expect(
      sortedRows(bodies, MB_SORT_COLUMNS, bumpSort(null, "jump", MB_SORT_COLUMNS)).map(
        (row) => row.body,
      ),
    ).toEqual(["One", "Two"]);
  });
});

describe("profile-scoped browser persistence", () => {
  beforeEach(() => {
    localStorage.clear();
    clearRouteWorkspace();
    loadGalaxyHistory(null);
  });

  it("migrates an unscoped route once and never leaks it to another commander", () => {
    const legacyRoute = {
      waypoints: [{ system: "Sol" }],
      index: 0,
    };
    localStorage.setItem("activeRoute", JSON.stringify(legacyRoute));

    loadActiveRoute("alpha");
    expect(getActiveRoute()).toEqual({
      ...legacyRoute,
      kind: "route",
      label: "Tracked route",
    });
    expect(localStorage.getItem("activeRoute")).toBeNull();
    expect(JSON.parse(localStorage.getItem(activeRouteKey("alpha")))).toEqual(legacyRoute);

    loadActiveRoute("beta");
    expect(getActiveRoute()).toBeNull();
  });

  it("migrates display-name Galaxy history into a commander ID key", () => {
    const history = [{ system: "Sol", observed_at: "2026-01-01T00:00:00Z" }];
    const legacyKey = `galaxyHistory:v1:${encodeURIComponent("Same Name")}`;
    localStorage.setItem(legacyKey, JSON.stringify(history));

    loadGalaxyHistory("alpha", "Same Name");
    expect(getGalaxyHistory()).toEqual(history);
    expect(getGalaxyHistoryCommander()).toBe("alpha");
    expect(localStorage.getItem(legacyKey)).toBeNull();
    expect(JSON.parse(localStorage.getItem(galaxyHistoryKey("alpha")))).toEqual(history);

    loadGalaxyHistory("beta", "Same Name");
    expect(getGalaxyHistory()).toEqual([]);
    expect(getGalaxyHistoryCommander()).toBe("beta");
  });
});

describe("Galaxy display helpers", () => {
  it("clamps progress and explains known and unknown control states", () => {
    expect(ppProgressPercent(0.45)).toBe(45);
    expect(ppProgressPercent(140)).toBe(100);
    expect(ppProgressPercent(-1)).toBe(0);
    expect(powerplayStateNote("Stronghold")).toContain("Stronghold");
    expect(powerplayStateNote("future-state")).toContain("local journal snapshot");
  });
});
