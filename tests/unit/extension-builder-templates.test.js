import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../ui/src/api/extensions.js", () => ({
  extensionsApi: {
    deleteExtension: vi.fn(),
    getManifest: vi.fn(),
    saveManifest: vi.fn(),
    testManifestAgainstHistory: vi.fn(),
  },
}));

import { XB_TEMPLATES } from "../../ui/src/features/extension-builder/catalog.js";
import { initExtensionBuilder, xbClose } from "../../ui/src/features/extension-builder.js";

function mountBuilder() {
  document.body.innerHTML = `
    <section id="ext-builder-card">
      <div id="xb-templates"></div>
      <button id="xb-new"></button>
      <form id="xb-form" class="hidden">
        <input id="xb-name">
        <span id="xb-id"></span>
        <div id="xb-rules"></div>
        <button id="xb-add-rule" type="button"></button>
        <button id="xb-test" type="button"></button>
        <button id="xb-cancel" type="button"></button>
        <button id="xb-save" type="submit"></button>
        <div id="xb-status"></div>
        <div id="xb-results" class="hidden"></div>
      </form>
    </section>
    <div id="extensions-status"></div>
  `;
}

/**
 * The rendered `selected` / `checked` attribute, not the IDL property.
 *
 * happy-dom does not reflect an innerHTML-set `selected` attribute into
 * `option.selected`: it reports the attributed option as unselected and points
 * `selectedIndex` at a different option entirely. Chromium resolves the same
 * markup correctly (verified: value "Location", selectedIndex 2), so the
 * attribute is both what these templates control and the portable assertion.
 *
 * @param {Element} select
 */
function selectedValue(select) {
  return select.querySelector("option[selected]")?.value;
}

/** @param {string} label */
function clickTemplate(label) {
  const chip = [...document.querySelectorAll("[data-template]")].find((button) =>
    button.textContent.trim().includes(label),
  );
  if (!chip) throw new Error(`no template chip matching ${label}`);
  chip.click();
  return chip;
}

describe("extension builder template chips", () => {
  // initExtensionBuilder() latches on a module-level flag and binds its click
  // handler to the #xb-templates element present at that moment, so the DOM is
  // mounted once and each test resets the open form instead of remounting.
  beforeAll(() => {
    mountBuilder();
    initExtensionBuilder();
  });

  beforeEach(xbClose);

  it("renders one chip per shipped template plus the blank starter", () => {
    const chips = document.querySelectorAll("[data-template]");
    expect(chips).toHaveLength(XB_TEMPLATES.length + 1);
    expect(document.querySelector('[data-template="blank"]').textContent.trim()).toBe("Blank");
  });

  it("opens the Raxxla Scanner with every rule prefilled", () => {
    clickTemplate("Raxxla Scanner");

    expect(document.querySelector("#xb-form").classList.contains("hidden")).toBe(false);
    expect(document.querySelector("#xb-name").value).toBe("Raxxla Scanner");
    expect(document.querySelector("#xb-id").textContent).toBe("raxxla-scanner");

    // A multi-rule template only survives if the seed passes the whole array;
    // the single-rule shape this replaced would render just the first rule.
    expect(document.querySelectorAll(".xb-rule")).toHaveLength(5);

    const events = [...document.querySelectorAll('[data-xb="event"]')].map(selectedValue);
    expect(events).toEqual(["FSDJump", "Location", "Scan", "CodexEntry", "Scan"]);

    // No rule fell through to the custom-event text box.
    expect(document.querySelectorAll('[data-xb="custom-event"]')).toHaveLength(0);

    const levels = [...document.querySelectorAll('[data-xb="action-level"]')].map(selectedValue);
    expect(levels).toEqual(["critical", "critical", "warn", "warn", "info"]);

    const voice = [...document.querySelectorAll('[data-xb="action-voice"]')].map((box) =>
      box.hasAttribute("checked"),
    );
    expect(voice).toEqual([true, true, true, false, false]);
  });

  it("keeps the single-rule templates working after the plural seed change", () => {
    clickTemplate("Low fuel after jump");

    expect(document.querySelector("#xb-name").value).toBe("Low fuel after jump");
    expect(document.querySelectorAll(".xb-rule")).toHaveLength(1);
    expect(selectedValue(document.querySelector('[data-xb="event"]'))).toBe("FSDJump");
  });

  it("opens an empty form from the blank starter", () => {
    clickTemplate("Blank");

    expect(document.querySelector("#xb-name").value).toBe("");
    expect(document.querySelectorAll(".xb-rule")).toHaveLength(1);
  });
});
