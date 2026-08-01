## Frameshift v2.9.0 — where do I get Phosphorus?

Frameshift already knew the answer. It just had no way to be asked.

The engineering catalog knew Phosphorus sits in the raw category 2 column, and
knew the trade ratios — but only if you had already pinned a blueprint that
happened to need it. The material-trader search existed, but behind a button in
the workshop card, took no material name, and showed one result of the eight it
fetched. And nobody could ask the obvious follow-up: *where do I go dig some up?*

The Engineering tab now has a **MATERIAL FINDER**. Type a name, get the answer.

### The trade answer, instantly and offline

```
Phosphorus   raw · G1   Raw category 2 column

TRADE FOR IT  at a raw material trader
  1× Chromium    G2  →   3× Phosphorus
  1× Molybdenum  G3  →   9× Phosphorus
  1× Technetium  G4  →  27× Phosphorus

WHERE IT DROPS  Surface prospecting; Mining; Mining (ice rings)
```

This appears as you type, with no network call — the trader columns ship with
the catalog. It still answers with the game closed or Spansh unreachable.

- **Every material, not just tradeable ones.** All 139 raw, manufactured and
  encoded materials resolve. Guardian and Thargoid ones name their column and
  say plainly that no trader will touch them, rather than coming back unknown.
- **Trades are ordered by what you'd actually spend.** Trading down comes first,
  nearest grade first. One Technetium buys 27 Phosphorus, but Technetium is far
  harder to come by than the Chromium that buys three.
- **The ratios are the planner's own.** The 6× up / 3× down maths is computed
  once, server-side, by the same tested code the wishlist uses.

### Where to prospect it

For raw materials, Frameshift now finds the nearest landable bodies with a high
surface concentration — sorted by distance, with a minimum concentration you
control. Sitting in Cennicenino, the top result was a body in that same system
at 16% Phosphorus, 0 ly away.

Manufactured and encoded materials skip this section; they do not occur on
surfaces, and the drop-source line above is the whole answer.

### The nearest traders, all of them

The trader search is now a proper sortable table of everything it finds —
station, system, which kind it trades, jump distance, arrival distance and pad
size — with a **Near** box to search from somewhere other than where you are.
Leave the material blank and it lists all three trader kinds at once, which is
what the workshop's **FIND TRADERS** button now does.

### Fixes

- **Your materials now update when you trade.** The game's `MaterialTrade`
  event was never handled, so trading at a trader left your inventory stale
  until the next time you launched the game.
- **The concentration floor is now real.** Spansh treats a minimum-share filter
  as a suggestion — asked for 10%, it returns bodies as low as 8.3%. The floor
  is enforced before the table is drawn, so the count matches the claim.
- **Prospecting searches are roughly eight times faster.** The query filtered
  for landable bodies, which excluded nothing — surface material shares only
  exist on landable bodies in the first place — while costing most of the run
  time. Removing it took the search from ~50s to ~6s.
- **Trader searches deep in the black now explain themselves** instead of
  failing oddly, matching how the other Spansh searches already behaved.

### Upgrade notes

- Update normally from any 2.x release. No database changes, no migration.
- The material field remembers what you last looked up; the **Near** box
  deliberately does not, so a stale location never outlives a session.
- Prospecting results come from bodies other commanders have already scanned,
  so coverage is thinner far outside the bubble. The trade half is bundled and
  always works.
