# Security policy

## Scope

Faultiva is a research artifact for fault diagnosis on gate-level netlists. It runs entirely
offline, makes no network calls, and has no authentication, no server component, and no
telemetry.

It loads `.npz` and `.joblib` files from its own `models/` and `data/` directories.
`joblib.load` executes pickled objects, so **only load a bundle you trust**. Verify integrity
before first use:

```bash
sha256sum -c SHA256SUMS
```

## Not a safety-critical tool

Do not use Faultiva as the sole basis for certification, safety, or manufacturing decisions.
Its acceptance contract was **not met** on independent circuits, and localization returns
candidate sets rather than single sites. Confirm any result with independent analysis before
acting on it.

## Reporting a vulnerability

Open a GitHub issue for anything non-sensitive. For something you would rather not disclose
publicly, use GitHub's private vulnerability reporting on this repository.
