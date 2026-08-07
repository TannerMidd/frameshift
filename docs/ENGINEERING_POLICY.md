# Engineering policy

## Zero-cost requirement

Frameshift's core application, development workflow, test suite, build, update
path, and release process must remain usable without paying for a service,
subscription, certificate, API key, or proprietary developer tool.

- Runtime and development dependencies must be free and open source.
- Required hosted automation may use the repository host's included features,
  but must not rely on a paid tier.
- A proposed integration with an ongoing cost must remain optional and must
  have a complete zero-cost path for building, testing, releasing, and using
  Frameshift.
- Usage-based APIs, commercial code-signing certificates, and paid telemetry
  are not acceptable as mandatory infrastructure.

This is a project constraint, not a temporary budget preference. Changes that
violate it must not be merged unless this policy is deliberately changed by the
project owner.

## Local-first defaults

Commander data remains local by default. External network features must be
clearly scoped, resilient when unavailable, and avoid requiring a Frameshift
account. Secrets and signing private keys must never be committed or packaged.
