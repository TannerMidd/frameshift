# Frameshift extension API v1

Frameshift extensions are local folders under `data/extensions`. The folder
name and manifest `id` must match. Declarative extensions need no install step,
account or API key and cannot execute code.

```json
{
  "id": "example.hull-alerts",
  "name": "Example hull alerts",
  "version": "1.0.0",
  "api_version": 1,
  "permissions": ["read:journal", "emit:alert"],
  "rules": [
    {
      "event": "HullDamage",
      "when": {"Health": {"max": 0.25}},
      "action": {
        "type": "alert",
        "level": "red",
        "code": "example-low-hull",
        "text": "Hull is critical ({Health})"
      }
    }
  ]
}
```

Supported permissions are:

- `read:journal` — receive matching journal events.
- `read:state` — include the current Frameshift snapshot for an approved
  process adapter.
- `emit:alert` — emit a cockpit alert.
- `emit:objective` — suggest an objective for the commander objective engine.

Conditions support exact values or `exists`, `eq`, `in`, `min` and `max`.
Actions can interpolate journal values with `{Field}` or `{Nested.Field}`.

## Process adapters

An advanced extension may specify a command relative to its own directory:

```json
{"command": ["adapter.exe"], "permissions": ["read:journal", "emit:alert"]}
```

Process adapters are disabled until an administrator explicitly approves them
in Frameshift. Approval is stored in Frameshift-owned state outside the pack
and is bound to a SHA-256 fingerprint of every file in the reviewed pack.
An `APPROVED` file shipped inside a pack has no effect, and any pack-content
change automatically requires fresh approval.

Frameshift sends one JSON document on stdin and accepts a JSON action or list
of actions on stdout. Each invocation has a three-second timeout. The
permission list limits data and actions exposed by Frameshift, but this is not
an operating-system sandbox; inspect and approve only code you trust. Revoking
approval stops future process invocations without deleting the pack.
