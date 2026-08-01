import { test, expect } from "@playwright/test";
import harness from "./ui-harness.cjs";

const { capturePageErrors, installMockApi, seedStorage, startUiServer } = harness;

let uiServer;
const FIXED_BROWSER_TIME = new Date("2026-07-18T12:00:00Z");

test.beforeAll(async () => {
  uiServer = await startUiServer();
});

test.afterAll(async () => {
  await uiServer.close();
});

async function bootDesktop(page, apiOptions = {}) {
  await page.clock.setFixedTime(FIXED_BROWSER_TIME);
  const pageErrors = capturePageErrors(page);
  const api = await installMockApi(page, apiOptions);
  await seedStorage(page);
  await page.goto(uiServer.baseURL);
  await expect(page.locator("#commander")).toHaveText("CMDR Test Pilot");
  await expect(page.locator("#pairing-gate")).toHaveClass(/hidden/);
  await expect(page.locator("body")).not.toHaveClass(/panel-mode/);
  return { api, pageErrors };
}

async function activateDesktopTab(page, name) {
  const button = page.locator(`#tabs [data-tab="${name}"]`);
  const pane = page.locator(`#tab-${name}`);
  await button.click();
  await expect(button).toHaveAttribute("aria-pressed", "true");
  await expect(pane).toBeVisible();
  expect((await pane.innerText()).trim()).not.toBe("");
  return pane;
}

test("boots against fixtures and every desktop tab exposes content", async ({ page }) => {
  const { pageErrors } = await bootDesktop(page);
  const tabs = [
    "trade",
    "commodities",
    "bio",
    "guides",
    "analytics",
    "engineering",
    "galaxy",
    "ops",
    "specialists",
    "local",
    "database",
  ];

  for (const tab of tabs) await activateDesktopTab(page, tab);

  expect(pageErrors).toEqual([]);
});

test("boots and navigates cleanly at tablet dimensions @tablet", async ({ page }) => {
  const { pageErrors } = await bootDesktop(page);

  await activateDesktopTab(page, "galaxy");
  await expect(page.locator("#tab-galaxy")).toBeVisible();
  await page.locator("#panel-toggle").click();
  await expect(page.locator("body")).toHaveClass(/panel-mode/);
  await expect(page.locator("#fp-boot")).toHaveClass(/hidden/, { timeout: 3_000 });
  expect(pageErrors).toEqual([]);
});

test("exchanges a one-time pairing link and removes it from browser history", async ({ page }) => {
  const pageErrors = capturePageErrors(page);
  const pairingCode = "one-time-cockpit-code";
  const api = await installMockApi(page, {
    pairingRequired: true,
    pairingCode,
  });
  await seedStorage(page);

  await page.goto(`${uiServer.baseURL}?pair=${pairingCode}`);

  await expect(page.locator("#commander")).toHaveText("CMDR Test Pilot");
  await expect(page.locator("#pairing-gate")).toHaveClass(/hidden/);
  await expect(page).toHaveURL(uiServer.baseURL);
  await expect.poll(() => api.matching("POST", "/api/security/pair").length).toBe(1);
  const exchange = api.matching("POST", "/api/security/pair")[0];
  expect(exchange.postData.code).toBe(pairingCode);
  expect(exchange.postData.device_name).toEqual(expect.any(String));
  expect(exchange.postData.device_name).not.toBe("");
  expect(pageErrors).toEqual([]);
});

test("enters panel mode and navigates between panel pages", async ({ page }) => {
  const { pageErrors } = await bootDesktop(page);

  await page.locator("#panel-toggle").click();
  await expect(page.locator("body")).toHaveClass(/panel-mode/);
  await expect(page.locator("#flight-panel")).toBeVisible();
  await expect(page.locator('#fp-nav [data-page="status"]')).toHaveAttribute(
    "aria-current",
    "page",
  );

  // The entry animation is deliberately brief but can cover the navigation
  // rail. Waiting on its semantic hidden state avoids timing sleeps.
  await expect(page.locator("#fp-boot")).toHaveClass(/hidden/, { timeout: 3_000 });

  await page.locator("#flight-panel").evaluate((target) => {
    const createTouch = (clientX) =>
      new Touch({
        identifier: 1,
        target,
        clientX,
        clientY: 200,
        screenX: clientX,
        screenY: 200,
      });
    const dispatchTouch = (type, touches, changedTouches) => {
      target.dispatchEvent(
        new TouchEvent(type, {
          bubbles: true,
          cancelable: true,
          composed: true,
          touches,
          targetTouches: touches,
          changedTouches,
        }),
      );
    };

    const start = createTouch(240);
    const end = createTouch(100);
    dispatchTouch("touchstart", [start], [start]);
    dispatchTouch("touchmove", [end], [end]);
    dispatchTouch("touchend", [], [end]);
  });
  await expect(page.locator("#flight-panel")).toBeHidden();
  await expect(page.locator("#tab-trade")).toBeVisible();
  await expect(page.locator('#fp-nav [data-page="trade"]')).toHaveAttribute("aria-current", "page");

  await page.locator('#fp-nav [data-page="commodities"]').click();
  await expect(page.locator("#flight-panel")).toBeHidden();
  await expect(page.locator("#tab-commodities")).toBeVisible();
  await expect(page.locator('#fp-nav [data-page="commodities"]')).toHaveAttribute(
    "aria-current",
    "page",
  );

  await page.locator('#fp-nav [data-page="status"]').click();
  await expect(page.locator("#flight-panel")).toBeVisible();
  expect(pageErrors).toEqual([]);
});

test("fresh devices default to Panel and an explicit exit persists", async ({ page }) => {
  const pageErrors = capturePageErrors(page);
  await installMockApi(page);
  await page.goto(uiServer.baseURL);

  await expect(page.locator("#commander")).toHaveText("CMDR Test Pilot");
  await expect(page.locator("body")).toHaveClass(/panel-mode/);
  await expect(page.locator("#flight-panel")).toBeVisible();
  await expect(page.locator("#fp-boot")).toHaveClass(/hidden/, { timeout: 3_000 });

  const statusControl = page.locator('#fp-nav [data-page="status"]');
  const controlBox = await statusControl.boundingBox();
  expect(controlBox.height).toBeGreaterThanOrEqual(44);
  const exitControl = page.locator("#panel-exit");
  expect(
    await exitControl.evaluate((button) => {
      const PointerEventType = button.ownerDocument.defaultView.PointerEvent;
      button.dispatchEvent(
        new PointerEventType("pointerdown", {
          bubbles: true,
          pointerType: "mouse",
        }),
      );
      return button.querySelectorAll(".hb-fx").length;
    }),
  ).toBe(1);

  await page.locator("#panel-exit").click();
  await expect(page.locator("body")).not.toHaveClass(/panel-mode/);
  expect(await page.evaluate(() => localStorage.getItem("panelMode"))).toBe("0");

  await page.reload();
  await expect(page.locator("#commander")).toHaveText("CMDR Test Pilot");
  await expect(page.locator("body")).not.toHaveClass(/panel-mode/);
  expect(pageErrors).toEqual([]);
});

test("arrange mode adds reversible card controls", async ({ page }) => {
  const { pageErrors } = await bootDesktop(page);
  const arrange = page.locator("#arrange-btn");

  await arrange.click();
  await expect(page.locator("body")).toHaveClass(/arranging/);
  await expect(arrange).toHaveAttribute("aria-pressed", "true");
  expect(await page.locator(".arr-handle").count()).toBeGreaterThan(0);
  expect(await page.locator(".arr-eye").count()).toBeGreaterThan(0);

  await arrange.click();
  await expect(page.locator("body")).not.toHaveClass(/arranging/);
  await expect(page.locator(".arr-handle")).toHaveCount(0);
  await expect(page.locator(".arr-eye")).toHaveCount(0);
  expect(pageErrors).toEqual([]);
});

test("commodity search renders a deterministic API result", async ({ page }) => {
  const { api, pageErrors } = await bootDesktop(page);
  await activateDesktopTab(page, "commodities");

  await page.locator("#cs-query").fill("Gold");
  await page.locator("#cs-mode").selectOption("sell");
  await page.locator("#cs-near").fill("Lave");
  await page.locator("#cs-form").getByRole("button", { name: "SEARCH" }).click();

  const row = page.locator("#cs-table tbody tr").first();
  await expect(page.locator("#cs-table")).toBeVisible();
  await expect(row).toContainText("Jameson Memorial");
  await expect(row).toContainText("Shinrarta Dezhra");
  await expect(row).toContainText("52,000");
  await expect(page.locator("#cs-status")).toContainText("1 station(s) buying Gold");

  await expect.poll(() => api.matching("GET", "/api/commodity-search").length).toBe(1);
  const request = api.matching("GET", "/api/commodity-search")[0];
  const params = new URLSearchParams(request.search);
  expect(params.get("q")).toBe("Gold");
  expect(params.get("mode")).toBe("sell");
  expect(params.get("system")).toBe("Lave");

  const stationHeader = page.locator('#cs-table th[data-sort="station"]');
  await stationHeader.click();
  await expect(stationHeader).toHaveClass(/sort-asc/);
  await expect(row.getByRole("button", { name: "Copy system name" })).toBeVisible();

  const saved = await page.evaluate(() => JSON.parse(localStorage.getItem("csForm")));
  expect(saved["cs-query"]).toBe("Gold");
  expect(saved).not.toHaveProperty("cs-near");

  await page.reload();
  await expect(page.locator("#commander")).toHaveText("CMDR Test Pilot");
  await expect(page.locator("#cs-query")).toHaveValue("Gold");
  await expect(page.locator("#cs-near")).toHaveValue("");
  expect(pageErrors).toEqual([]);
});

test("mining and outfitting searches honor temporary Near overrides", async ({ page }) => {
  const { api, pageErrors } = await bootDesktop(page);
  await activateDesktopTab(page, "commodities");

  await page.locator("#mn-radius").fill("75");
  await page.locator("#mn-near").fill("Colonia");
  await page.locator("#mn-go").click();
  await expect.poll(() => api.matching("GET", "/api/mining").length).toBe(1);
  const mining = new URLSearchParams(api.matching("GET", "/api/mining")[0].search);
  expect(mining.get("system")).toBe("Colonia");
  expect(mining.get("radius")).toBe("75");

  await page.locator("#os-query").fill("6A Fuel Scoop");
  await page.locator("#os-near").fill("Shinrarta Dezhra");
  await page.locator("#os-go").click();
  await expect.poll(() => api.matching("GET", "/api/station-search").length).toBe(1);
  const outfitting = new URLSearchParams(api.matching("GET", "/api/station-search")[0].search);
  expect(outfitting.get("q")).toBe("6A Fuel Scoop");
  expect(outfitting.get("system")).toBe("Shinrarta Dezhra");

  await page.reload();
  await expect(page.locator("#commander")).toHaveText("CMDR Test Pilot");
  await expect(page.locator("#mn-radius")).toHaveValue("75");
  await expect(page.locator("#os-query")).toHaveValue("6A Fuel Scoop");
  await expect(page.locator("#mn-near")).toHaveValue("");
  await expect(page.locator("#os-near")).toHaveValue("");
  expect(pageErrors).toEqual([]);
});

test("engineering catalog filters recipes and renders grade and material state", async ({
  page,
}) => {
  const { pageErrors } = await bootDesktop(page);
  await activateDesktopTab(page, "engineering");

  await expect(page.locator("#ep-blueprint")).toHaveValue("fsd-increased-range");
  await expect(page.locator("#ep-target option")).toHaveCount(5);
  await expect(page.locator("#ep-target")).toHaveValue("5");
  await expect(page.locator("#engplan-list")).toContainText("Frame Shift Drive · Increased Range");
  await expect(page.locator("#engplan-materials")).toContainText("Datamined Wake Exceptions");

  await page.locator("#ep-search").fill("felicity range");
  await expect(page.locator("#ep-blueprint option")).toHaveCount(1);
  await page.locator("#ep-search").fill("armor");
  await expect(page.locator("#ep-blueprint option")).toHaveCount(0);
  await expect(page.locator("#ep-pin")).toBeDisabled();
  expect(pageErrors).toEqual([]);
});

test("material finder answers offline, then searches traders and prospecting sites", async ({
  page,
}) => {
  const { api, pageErrors } = await bootDesktop(page);
  await activateDesktopTab(page, "engineering");

  // The column, the trades and the drop sources all resolve from the catalog
  // payload already in the browser — typing must not touch the network.
  await page.locator("#mf-material").fill("Phosphorus");
  await expect(page.locator("#mf-answer")).toContainText("Raw category 2");
  await expect(page.locator("#mf-answer")).toContainText("Chromium");
  await expect(page.locator("#mf-answer")).toContainText("27×");
  await expect(page.locator("#mf-answer")).toContainText("Mining (ice rings)");
  expect(api.matching("GET", "/api/material-traders")).toHaveLength(0);
  expect(api.matching("GET", "/api/material-bodies")).toHaveLength(0);

  await page.locator("#mf-go").click();
  await expect.poll(() => api.matching("GET", "/api/material-traders").length).toBe(1);
  await expect.poll(() => api.matching("GET", "/api/material-bodies").length).toBe(1);
  const traders = new URLSearchParams(api.matching("GET", "/api/material-traders")[0].search);
  expect(traders.get("kind")).toBe("raw");
  const bodies = new URLSearchParams(api.matching("GET", "/api/material-bodies")[0].search);
  expect(bodies.get("material")).toBe("Phosphorus");
  expect(bodies.get("min_share")).toBe("10");

  await expect(page.locator("#mf-traders-table tbody")).toContainText("Jameson Memorial");
  await expect(page.locator("#mf-bodies-table tbody")).toContainText("Enceladus");
  await expect(page.locator("#mf-bodies-table tbody")).toContainText("11.5%");
  expect(pageErrors).toEqual([]);
});

test("OPS objective submission sends planning fields to the local API", async ({ page }) => {
  const { api, pageErrors } = await bootDesktop(page);
  await activateDesktopTab(page, "ops");

  await page.locator("#ops-objective-title").fill("Deliver expedition tritium");
  await page.locator("#ops-objective-category").selectOption("carrier");
  await page.locator("#ops-objective-minutes").fill("45");
  await page.locator("#ops-objective-system").fill("Lave");
  await page.locator("#ops-objective-save").click();

  await expect.poll(() => api.matching("POST", "/api/objectives").length).toBe(1);
  const request = api.matching("POST", "/api/objectives")[0];
  expect(request.postData).toMatchObject({
    title: "Deliver expedition tritium",
    category: "carrier",
    estimated_seconds: 2700,
    system: "Lave",
    priority: 50,
    status: "open",
  });
  await expect(page.locator("#ops-plan-status")).toContainText("Objectives changed");
  await expect(page.locator("#ops-objective-list")).toContainText("Deliver expedition tritium");
  await expect(page.locator("#ops-objective-save")).toBeEnabled();
  expect(pageErrors).toEqual([]);
});

test("specialist workflow tabs switch all four local workspaces", async ({ page }) => {
  const { pageErrors } = await bootDesktop(page);
  await activateDesktopTab(page, "specialists");

  for (const name of ["mining", "combat", "carrier", "exobiology"]) {
    const button = page.locator(`.sp-switcher [data-specialist="${name}"]`);
    await button.click();
    await expect(button).toHaveAttribute("aria-pressed", "true");
    await expect(button).toHaveAttribute("aria-selected", "true");
    await expect(page.locator(`#sp-workflow-${name}`)).toBeVisible();
  }

  expect(await page.evaluate(() => localStorage.getItem("specialistWorkflow"))).toBe("exobiology");
  await page.reload();
  await expect(page.locator("#commander")).toHaveText("CMDR Test Pilot");
  await expect(page.locator('.sp-switcher [data-specialist="exobiology"]')).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  expect(pageErrors).toEqual([]);
});

test("Legacy galaxy state produces an explicit boundary warning", async ({ page }) => {
  const { pageErrors } = await bootDesktop(page, {
    statePatch: { galaxy_mode: "legacy" },
  });

  await expect(page.locator("#galaxy-mode-banner")).toBeVisible();
  await expect(page.locator("#galaxy-mode-banner")).toContainText("LEGACY GALAXY detected");
  await expect(page.locator("#galaxy-mode-banner")).toContainText("Live community market");
  expect(pageErrors).toEqual([]);
});

test("server and per-device settings persist across reload", async ({ page }) => {
  const { api, pageErrors } = await bootDesktop(page);
  await activateDesktopTab(page, "database");

  const surfaceRow = page.locator("label.setting").filter({ hasText: "Exclude surface stations" });
  const surfaceToggle = surfaceRow.locator('input[type="checkbox"]');
  await expect(surfaceToggle).not.toBeChecked();
  await surfaceRow.locator(".switch").click();
  await expect(surfaceToggle).toBeChecked();
  await expect.poll(() => api.matching("POST", "/api/settings").length).toBe(1);

  const iceTheme = page.locator('.theme-chip[data-theme="ice"]');
  await iceTheme.click();
  await expect(iceTheme).toHaveAttribute("aria-pressed", "true");

  const interfaceScale = page
    .locator("label.setting")
    .filter({ hasText: "Interface size" })
    .locator('input[type="range"]');
  await interfaceScale.evaluate((input) => {
    input.value = "115";
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });

  await page.reload();
  await expect(page.locator("#commander")).toHaveText("CMDR Test Pilot");
  await expect(page.locator("#tab-database")).toBeVisible();
  await expect(
    page
      .locator("label.setting")
      .filter({ hasText: "Exclude surface stations" })
      .locator('input[type="checkbox"]'),
  ).toBeChecked();
  await expect(page.locator('.theme-chip[data-theme="ice"]')).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(
    page
      .locator("label.setting")
      .filter({ hasText: "Interface size" })
      .locator('input[type="range"]'),
  ).toHaveValue("115");
  expect(await page.evaluate(() => localStorage.getItem("accentTheme"))).toBe("ice");
  expect(await page.evaluate(() => localStorage.getItem("uiScale"))).toBe("115");
  expect(pageErrors).toEqual([]);
});

test("a revoked state poll locks the UI behind the pairing dialog", async ({ page }) => {
  const pageErrors = capturePageErrors(page);
  await installMockApi(page, { state401: true });
  await seedStorage(page);
  await page.goto(uiServer.baseURL);

  const gate = page.locator("#pairing-gate");
  await expect(gate).toBeVisible();
  await expect(page.locator("#pairing-title")).toHaveText("This device is not paired");
  await expect(page.locator("#pairing-message")).toContainText("revoked");
  await expect(page.locator("#pairing-retry")).toBeVisible();
  await expect(page.locator("main")).toHaveAttribute("aria-hidden", "true");
  await expect(page.locator("#pairing-retry")).toBeFocused();
  const unexpectedErrors = pageErrors.filter(
    (error) => !error.includes("status of 401 (Unauthorized)"),
  );
  expect(unexpectedErrors).toEqual([]);
});

test("canonical Windows baselines cover desktop and panel shells", async ({ page }, testInfo) => {
  test.skip(
    process.platform !== "win32" || testInfo.project.name !== "chromium",
    "Visual baselines use pinned Chromium on Windows.",
  );
  const { pageErrors } = await bootDesktop(page);

  await expect(page).toHaveScreenshot("frameshift-desktop.png", {
    fullPage: true,
    mask: [page.locator("#fp-clock"), page.locator("#fp-telemetry-at")],
  });

  await page.evaluate(() => localStorage.setItem("accentTheme", "ice"));
  await page.reload();
  await expect(page.locator("#commander")).toHaveText("CMDR Test Pilot");
  await expect(page).toHaveScreenshot("frameshift-ice.png", {
    fullPage: true,
    mask: [page.locator("#fp-clock"), page.locator("#fp-telemetry-at")],
  });

  await page.evaluate(() => localStorage.setItem("accentTheme", "elite"));
  await page.reload();
  await expect(page.locator("#commander")).toHaveText("CMDR Test Pilot");
  await page.locator("#panel-toggle").click();
  await expect(page.locator("#fp-boot")).toHaveClass(/hidden/, { timeout: 3_000 });
  await expect(page).toHaveScreenshot("frameshift-panel.png", {
    fullPage: true,
    mask: [page.locator("#fp-clock"), page.locator("#fp-telemetry-at")],
  });
  expect(pageErrors).toEqual([]);
});
