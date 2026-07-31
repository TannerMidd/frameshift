import { describe, expect, it } from "vitest";

import { XB_EVENTS, XB_TEMPLATES } from "../../ui/src/features/extension-builder/catalog.js";
import {
  collectManifest,
  createBuilderModel,
  xbRuleFromManifest,
} from "../../ui/src/features/extension-builder/model.js";

describe("extension builder schema model", () => {
  it("collects typed conditions, permissions, and voice actions", () => {
    const model = createBuilderModel({
      name: "Big bounty",
      rules: [
        {
          event: "Bounty",
          conditions: [
            { field: "Reward", op: "min", value: "100000" },
            { field: "Target", op: "in", value: "Pirate, 42, true" },
          ],
          action: {
            type: "alert",
            level: "warn",
            text: "Bounty {Reward}",
            voice: true,
          },
        },
      ],
    });

    const manifest = collectManifest(model, "Big bounty");

    expect(manifest.id).toBe("big-bounty");
    expect(manifest.permissions).toEqual(["read:journal", "emit:alert"]);
    expect(manifest.rules[0].when).toEqual({
      Reward: { min: 100000 },
      Target: { in: ["Pirate", 42, true] },
    });
    expect(manifest.rules[0].action).toMatchObject({
      type: "alert",
      level: "warn",
      text: "Bounty {Reward}",
      say: "Bounty {Reward}",
    });
  });

  it("maps an editable manifest rule back without losing custom events or conditions", () => {
    const editable = xbRuleFromManifest({
      event: "PluginEvent",
      when: {
        Enabled: { exists: true },
        Score: { max: 12 },
        Mode: { eq: "safe" },
      },
      action: {
        type: "objective",
        title: "Review {Mode}",
        category: "operations",
      },
    });

    expect(editable.event).toBe("");
    expect(editable.customEvent).toBe("PluginEvent");
    expect(editable.conditions).toEqual([
      { field: "Enabled", op: "exists", value: "" },
      { field: "Score", op: "max", value: "12" },
      { field: "Mode", op: "eq", value: "safe" },
    ]);
    expect(editable.action).toMatchObject({
      type: "objective",
      title: "Review {Mode}",
      category: "operations",
    });
  });
});

describe("shipped builder templates", () => {
  it("seeds every template from a rules array", () => {
    expect(XB_TEMPLATES.length).toBeGreaterThan(0);
    for (const template of XB_TEMPLATES) {
      expect(Array.isArray(template.rules), `${template.name} needs a rules array`).toBe(true);
      expect(template.rules.length).toBeGreaterThan(0);
    }
  });

  it("only references events the builder can render as a known dropdown entry", () => {
    // A rule whose event is missing from XB_EVENTS silently falls back to the
    // "Custom event…" text box and loses its {Field} insert chips.
    const known = new Set(XB_EVENTS.map((event) => event.id));
    for (const template of XB_TEMPLATES) {
      for (const rule of template.rules) {
        expect(known, `${template.name} uses unknown event ${rule.event}`).toContain(rule.event);
      }
    }
  });

  it("compiles the Raxxla Scanner into a manifest the extension host accepts", () => {
    const template = XB_TEMPLATES.find((entry) => entry.name === "Raxxla Scanner");
    expect(template).toBeDefined();

    const model = createBuilderModel({ name: template.name, rules: template.rules });
    const manifest = collectManifest(model, template.name);

    expect(manifest.id).toBe("raxxla-scanner");
    expect(manifest.api_version).toBe(1);
    expect(manifest.permissions).toEqual(["read:journal", "emit:alert"]);
    expect(manifest.rules).toHaveLength(5);

    // The two tripwire rules are the only reason this pack exists: they must
    // stay critical, spoken, and matched against the literal system name.
    const tripwires = manifest.rules.filter((rule) => rule.action.level === "critical");
    expect(tripwires).toHaveLength(2);
    expect(tripwires.map((rule) => rule.event)).toEqual(["FSDJump", "Location"]);
    for (const rule of tripwires) {
      expect(rule.when).toEqual({ StarSystem: { in: ["Raxxla", "RAXXLA"] } });
      expect(rule.action.say).toBe(rule.action.text);
    }

    // WasDiscovered arrives from the journal as a real boolean, so the builder
    // must coerce "false" rather than compare against the string.
    const blackHole = manifest.rules.find((rule) => rule.when?.StarType);
    expect(blackHole.when).toEqual({ StarType: { eq: "H" }, WasDiscovered: { eq: false } });

    const codex = manifest.rules.find((rule) => rule.event === "CodexEntry");
    expect(codex.when).toEqual({
      SubCategory: { eq: "$Codex_SubCategory_Geology_and_Anomalies;" },
      IsNewEntry: { exists: true },
    });
    expect(codex.action.say).toBeUndefined();
  });

  it("round-trips the Raxxla Scanner back into the builder for editing", () => {
    const template = XB_TEMPLATES.find((entry) => entry.name === "Raxxla Scanner");
    const manifest = collectManifest(
      createBuilderModel({ name: template.name, rules: template.rules }),
      template.name,
    );

    const reopened = manifest.rules.map(xbRuleFromManifest);

    expect(reopened.every((rule) => rule.customEvent === "")).toBe(true);
    expect(reopened[0].conditions).toEqual([
      { field: "StarSystem", op: "in", value: "Raxxla, RAXXLA" },
    ]);
    expect(reopened[3].conditions).toEqual([
      { field: "SubCategory", op: "eq", value: "$Codex_SubCategory_Geology_and_Anomalies;" },
      { field: "IsNewEntry", op: "exists", value: "" },
    ]);
  });
});
