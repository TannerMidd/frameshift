import { beforeEach, describe, expect, it } from "vitest";

import { renderBio } from "../../ui/src/features/bio.js";

function mountBio() {
  document.body.innerHTML = `
    <div id="explo-total"></div>
    <div id="explo-summary"></div>
    <div id="explo-empty"></div>
    <ul id="explo-top"></ul>
    <div id="bio-sampling-card" class="hidden"></div>
    <div id="bio-sampling"></div>
    <ul id="bio-vault"></ul>
    <div id="bio-vault-empty"></div>
    <div id="bio-vault-total"></div>
    <div id="bio-empty"></div>
    <table id="bio-table"><tbody></tbody></table>
  `;
}

/** @param {object} signal */
function snapshotWith(signal) {
  return { bio: { system_signals: [signal], sampling: null, vault: { items: [], total: 0 } } };
}

const rowText = () => document.querySelector("#bio-table tbody tr").textContent;

describe("explore bio table species precedence", () => {
  beforeEach(mountBio);

  it("shows the exact species and its precise value when someone has scanned it", () => {
    renderBio(
      snapshotWith({
        body: "Xinca 5 a",
        count: 2,
        genuses: [],
        community_species: [
          {
            name: "Concha Aureolas",
            genus: "Concha",
            value: 7774700,
            variant: "Grey",
            colony_m: 150,
            sites: 29,
          },
          {
            name: "Bacterium Alcyoneum",
            genus: "Bacterium",
            value: 1658500,
            variant: "Lime",
            colony_m: 500,
            sites: 47,
          },
        ],
        species_value: 9433200,
      }),
    );

    const text = rowText();
    expect(text).toContain("Concha Aureolas");
    expect(text).toContain("7,774,700 cr");
    expect(text).toContain("Grey");
    expect(text).toContain("150 m");
    expect(text).toContain("29 sites");
    expect(text).toContain("Bacterium Alcyoneum");
    // The whole point: a precise figure, never the genus range.
    expect(text).not.toMatch(/1–9\.1M|1M–/);
    expect(text).toContain("full survey ≈9,433,200 cr");
  });

  it("says how many signals nobody has identified rather than implying the list is complete", () => {
    renderBio(
      snapshotWith({
        body: "Partly Known 1",
        count: 5,
        genuses: [],
        community_species: [
          { name: "Bacterium Acies", genus: "Bacterium", value: 1000000, sites: 9 },
        ],
        species_value: 1000000,
      }),
    );

    expect(rowText()).toContain("+4 signals nobody has identified yet");
  });

  it("uses the singular form for a single unidentified signal and one site", () => {
    renderBio(
      snapshotWith({
        body: "Single 1",
        count: 2,
        genuses: [],
        community_species: [
          { name: "Bacterium Acies", genus: "Bacterium", value: 1000000, sites: 1 },
        ],
        species_value: 1000000,
      }),
    );

    const text = rowText();
    expect(text).toContain("1 site");
    expect(text).not.toContain("1 sites");
    expect(text).toContain("+1 signal nobody has identified yet");
    // A lone species has no survey total worth showing.
    expect(text).not.toContain("full survey");
  });

  it("falls back to the community genus range when nobody has sampled the body", () => {
    renderBio(
      snapshotWith({
        body: "Unsampled 1",
        count: 1,
        genuses: [],
        community_species: [],
        community_genuses: [{ name: "Bacterium", min_value: 1000000, max_value: 9116600 }],
      }),
    );

    const text = rowText();
    expect(text).toContain("Bacterium");
    expect(text).toContain("1M–9.1M");
    expect(text).toContain("community");
  });

  it("prefers exact species over your own genus-level map", () => {
    renderBio(
      snapshotWith({
        body: "Mapped 1",
        count: 1,
        // Your own surface map only ever reveals the genus.
        genuses: [{ name: "Concha", min_value: 1054800, max_value: 7774700 }],
        community_species: [
          { name: "Concha Aureolas", genus: "Concha", value: 7774700, variant: "Grey", sites: 3 },
        ],
        species_value: 7774700,
      }),
    );

    const text = rowText();
    expect(text).toContain("Concha Aureolas");
    expect(text).toContain("7,774,700 cr");
    expect(text).not.toContain("1.1M–7.8M");
  });

  it("still renders a body with signals but no biology data at all", () => {
    renderBio(snapshotWith({ body: "Bare 1", count: 3, genuses: [] }));
    expect(rowText()).toContain("not mapped yet");
  });
});
