## Frameshift v2.8.0 — exact species, not a guess

The Explore bio table used to price a body as a range, because a surface map
only ever reveals the *genus* — and a genus spans species worth wildly
different amounts. Bacterium runs from 1.0M to 9.1M credits. That range was
often the least useful thing on the row.

But other commanders have usually already landed there and scanned the actual
species. Frameshift now reads that, so the row tells you what the body is
really worth before you burn the fuel.

### Exact species where anyone has scanned

Arrive in a system and bodies now show the precise species, its value, its
colour variant, the sample spacing you need for the colony rule, and how many
sites people have logged:

```
Col 285 Sector LH-A b15-3 B 1
  ◆ Fonticulua Campestris  1,000,000 cr · Amethyst · 500 m · 3 sites
  ◆ Bacterium Vesicula     1,000,000 cr · Gold · 500 m · 6 sites
  full survey ≈2,000,000 cr
```

Previously that same row read `Fonticulua 1M–?` and `Bacterium 1M–9.1M` — a
ceiling nine times the true value.

- **`full survey`** totals what the body pays once every known species on it
  is sampled, so you can judge a detour at a glance.
- **Nothing is overstated.** Where commanders have identified fewer species
  than the body has signals, the row says so — `+3 signals nobody has
  identified yet` — rather than implying the list is the whole body.
- **The genus range is still there** as the fallback, for bodies that have
  been mapped but never actually visited. Roughly one body in twenty.
- Your own scans still take precedence over anything community-sourced, and
  species data is added even to bodies you mapped yourself — your surface
  scan could only ever tell you the genus.

### Fixes

- **Cactoida and Anemone lost their value ranges.** The two data sources name
  genera differently, and these two slipped through the mapping to display as
  an unpriced `Cactoids` / `Anemones`. Found by checking every genus name
  across roughly 1,500 bodies rather than spot-checking a few.
- **Geology is no longer mistaken for biology.** Geysers, lava spouts, crashed
  ships, surface settlements and Thargoid sites all travel in the same list as
  organics. They are now filtered out — which also correctly separates the
  `Fumarole` geology marker from the `Fumerola` plant genus.

### Upgrade notes

- Update normally from any 2.x release. No database schema changes and no
  manual migration steps.
- No extra work on arrival: this replaces the existing lookup rather than
  adding a second one, and falls back to the old path if it ever fails.
- Sample coordinates are deliberately not included. A well-surveyed body
  carries hundreds of them, and they would bloat every state update.
