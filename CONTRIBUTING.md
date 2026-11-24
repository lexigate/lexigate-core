# Contributing to LexiGate Core

Thank you for considering a contribution. This repository is the **open core**:
specifications, documentation, the upcoming scanner / local demo CLI, and
interface contracts. Enterprise plug-ins are not accepted here.

## Before you start

- Read [README.md](README.md), [docs/architecture.md](docs/architecture.md), and
  the `semantic_map` specification under [`spec/`](spec/).
- Open an issue first for anything larger than a typo or a small schema fix.
- Please do not send product code that generates unconstrained SQL, or that
  phones home to a license server. See the architecture document for the
  dual-path and Mode A defaults.

## Developer Certificate of Origin (DCO)

Every commit must be signed off under the
[Developer Certificate of Origin](https://developercertificate.org/),
version 1.1. We do not require a CLA at this time.

Add a `Signed-off-by` line to each commit, using the same name and email as
the commit author:

```
git commit -s -m "Your message"
```

which appends:

```
Signed-off-by: Your Name <your.email@example.com>
```

By signing off, you certify that you wrote the change or have the right to
submit it under the Apache License, Version 2.0, as stated in the DCO.

## Pull requests

- Keep changes focused. Documentation, schema, and code each prefer their own PR
  when the diff is large.
- Do not commit secrets, credentials, personal data, or customer schemas.
- New specification fields should be backward compatible or clearly marked as
  a breaking change with a version bump (`semantic_version`).
- Tests (when present) should pass locally.

## License and trademarks

Contributions are licensed under Apache License 2.0. The name LexiGate is a
trademark; see [TRADEMARKS.md](TRADEMARKS.md).
