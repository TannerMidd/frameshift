## Frameshift v2.1.1 — migration and Panel polish

This patch focuses on the first-launch experience after upgrading to 2.1. It
does not add any account, API key, hosted service or setup step.

### Visible, trustworthy commander reconstruction

- Commander-history reconstruction now reports its current phase, completed
  journal count and automatic retry state, so a long local migration no longer
  looks like a frozen application.
- The current cockpit is assembled privately and published once, only after
  journal replay and preserved-data finalization both succeed. Historical
  systems, balances and cargo no longer rotate through the live UI during
  startup.
- Appended journal events are retained while reconstruction is running, and a
  complete final journal record is accepted even when the file has no trailing
  newline. Incomplete JSON remains queued until Elite finishes writing it.
- Temporarily locked journal or SQLite files retry automatically. A missing or
  corrected journal folder can recover while Frameshift remains open, without
  allowing an incomplete folder to be tailed directly into the public cockpit.
- Duplicate default-profile rows left by an interrupted 2.1 migration are
  adopted or safely deduplicated instead of blocking commander activation.

### Cleaner Panel startup

- Panel remains the default on fresh devices.
- Refreshing a Panel device now applies the Panel preference before first
  paint, eliminating the brief Desktop-layout flash.
- Migration progress includes accessible progress semantics and respects the
  browser's reduced-motion preference.

### Upgrade notes

- Update normally from Frameshift 2.1.0; no settings reset or manual database
  work is required.
- Existing journals, commander data, paired devices and local extensions are
  preserved.
- Frameshift still operates locally and requires no additional login,
  companion service or API key.
