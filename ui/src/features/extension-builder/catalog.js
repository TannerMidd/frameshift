export const XB_EVENTS = [
  { id: "*", label: "Any event", fields: ["event", "timestamp"] },
  {
    id: "FSDJump",
    label: "Hyperspace jump (FSDJump)",
    fields: ["StarSystem", "JumpDist", "FuelLevel", "FuelUsed", "StarClass", "Population"],
  },
  {
    id: "Location",
    label: "Location (game start or arrival)",
    fields: ["StarSystem", "Body", "Docked", "SystemAllegiance", "Population"],
  },
  {
    id: "Docked",
    label: "Docked at a station",
    fields: ["StationName", "StarSystem", "StationType", "DistFromStarLS"],
  },
  { id: "Undocked", label: "Undocked", fields: ["StationName"] },
  { id: "Bounty", label: "Bounty awarded", fields: ["Reward", "Target", "VictimFaction"] },
  { id: "MissionCompleted", label: "Mission completed", fields: ["Reward", "Faction", "Name"] },
  {
    id: "MissionAccepted",
    label: "Mission accepted",
    fields: ["Faction", "Name", "Reward", "Expiry"],
  },
  {
    id: "MarketSell",
    label: "Sold commodity",
    fields: ["Type", "Count", "SellPrice", "TotalSale", "AvgPricePaid"],
  },
  {
    id: "MarketBuy",
    label: "Bought commodity",
    fields: ["Type", "Count", "BuyPrice", "TotalCost"],
  },
  {
    id: "Scan",
    label: "Body scanned",
    fields: [
      "BodyName",
      "WasDiscovered",
      "WasMapped",
      "PlanetClass",
      "StarType",
      "TerraformState",
      "Landable",
    ],
  },
  { id: "SAASignalsFound", label: "Surface signals found", fields: ["BodyName"] },
  {
    id: "CodexEntry",
    label: "Codex entry recorded",
    fields: [
      "Name_Localised",
      "Category_Localised",
      "SubCategory",
      "SubCategory_Localised",
      "Region_Localised",
      "System",
      "BodyName",
      "IsNewEntry",
    ],
  },
  {
    id: "FSSSignalDiscovered",
    label: "Signal source detected",
    fields: ["SignalName", "SignalName_Localised", "IsStation", "USSType"],
  },
  {
    id: "ScanOrganic",
    label: "Organic scanned",
    fields: ["Genus_Localised", "Species_Localised", "ScanType"],
  },
  { id: "SellOrganicData", label: "Sold exobiology data", fields: [] },
  { id: "HullDamage", label: "Hull damage", fields: ["Health", "PlayerPilot"] },
  { id: "ShieldState", label: "Shields up/down", fields: ["ShieldsUp"] },
  { id: "Interdicted", label: "Interdicted", fields: ["Submitted", "Interdictor", "IsPlayer"] },
  { id: "FuelScoop", label: "Fuel scooped", fields: ["Scooped", "Total"] },
  { id: "CollectCargo", label: "Cargo collected", fields: ["Type", "Stolen"] },
  {
    id: "CargoDepot",
    label: "Wing mission depot",
    fields: ["UpdateType", "ItemsDelivered", "TotalItemsToDeliver"],
  },
  { id: "RedeemVoucher", label: "Voucher redeemed", fields: ["Type", "Amount"] },
  { id: "ReceiveText", label: "Message received", fields: ["From", "Message", "Channel"] },
  { id: "Touchdown", label: "Surface touchdown", fields: ["Body", "OnPlanet"] },
  { id: "LaunchSRV", label: "SRV deployed", fields: ["SRVType"] },
];

export const XB_OPS = [
  { id: "eq", label: "equals" },
  { id: "in", label: "is one of (comma-separated)" },
  { id: "min", label: "is at least" },
  { id: "max", label: "is at most" },
  { id: "exists", label: "is present" },
  { id: "absent", label: "is absent" },
];

export const XB_TEMPLATES = [
  {
    label: "💰 Big bounty callout",
    name: "Big bounty callout",
    rules: [
      {
        event: "Bounty",
        conditions: [{ field: "Reward", op: "min", value: "100000" }],
        action: {
          type: "alert",
          level: "info",
          text: "Bounty {Reward} cr — {Target}",
          voice: true,
        },
      },
    ],
  },
  {
    label: "⛽ Low fuel after jump",
    name: "Low fuel after jump",
    rules: [
      {
        event: "FSDJump",
        conditions: [{ field: "FuelLevel", op: "max", value: "8" }],
        action: {
          type: "alert",
          level: "warn",
          text: "Fuel at {FuelLevel} t after jump — find a scoopable star",
          voice: true,
        },
      },
    ],
  },
  {
    label: "📦 Mission payout tracker",
    name: "Mission payout tracker",
    rules: [
      {
        event: "MissionCompleted",
        conditions: [{ field: "Reward", op: "min", value: "1000000" }],
        action: { type: "alert", level: "info", text: "{Faction} paid {Reward} cr", voice: false },
      },
    ],
  },
  {
    label: "★ First-discovery follow-up",
    name: "First discovery follow-up",
    rules: [
      {
        event: "Scan",
        conditions: [{ field: "WasDiscovered", op: "eq", value: "false" }],
        action: {
          type: "objective",
          title: "Map first discovery {BodyName}",
          category: "exploration",
        },
      },
    ],
  },
  {
    // Raxxla is Elite's oldest unsolved legend: a world nobody has ever proven
    // exists. Rules 1-2 are a tripwire that can only fire if it is real and you
    // reach it; rules 3-5 are the lore's strongest physical tells, tuned against
    // real journal history so they stay rare rather than chatty.
    label: "🔭 Raxxla Scanner",
    name: "Raxxla Scanner",
    rules: [
      {
        event: "FSDJump",
        conditions: [{ field: "StarSystem", op: "in", value: "Raxxla, RAXXLA" }],
        action: {
          type: "alert",
          level: "critical",
          text: "RAXXLA — you have jumped into {StarSystem}. Record everything.",
          voice: true,
        },
      },
      {
        event: "Location",
        conditions: [{ field: "StarSystem", op: "in", value: "Raxxla, RAXXLA" }],
        action: {
          type: "alert",
          level: "critical",
          text: "RAXXLA — you are in {StarSystem}, at {Body}. Record everything.",
          voice: true,
        },
      },
      {
        event: "Scan",
        conditions: [
          { field: "StarType", op: "eq", value: "H" },
          { field: "WasDiscovered", op: "eq", value: "false" },
        ],
        action: {
          type: "alert",
          level: "warn",
          text: "Undiscovered black hole {BodyName} — nobody has scanned it before. The legend puts a world at one.",
          voice: true,
        },
      },
      {
        event: "CodexEntry",
        conditions: [
          { field: "SubCategory", op: "eq", value: "$Codex_SubCategory_Geology_and_Anomalies;" },
          { field: "IsNewEntry", op: "exists", value: "" },
        ],
        action: {
          type: "alert",
          level: "warn",
          text: "Codex anomaly in {System}: {Name_Localised}. New to you — check whether it is new to anyone.",
          voice: false,
        },
      },
      {
        event: "Scan",
        conditions: [
          { field: "PlanetClass", op: "eq", value: "Earthlike body" },
          { field: "WasDiscovered", op: "eq", value: "false" },
        ],
        action: {
          type: "alert",
          level: "info",
          text: "First-discovery Earth-like: {BodyName}. Raxxla is said to be one.",
          voice: false,
        },
      },
    ],
  },
];
