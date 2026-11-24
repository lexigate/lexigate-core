# Security policy

Please **do not** open a public issue for a vulnerability.

## How to report

1. Prefer a private [GitHub Security Advisory](https://github.com/lexigate/lexigate-core/security/advisories/new) on this repository (available after the repository is published).
2. If that form is unavailable, contact the maintainers through the [LexiGate GitHub organization](https://github.com/lexigate) without including secrets in a public issue or pull request.

Include a description, affected version or commit, and steps to reproduce. We will acknowledge the report as soon as we can and keep you informed of the fix. Please give us a reasonable window before any public disclosure.

## Scope notes

- LexiGate is designed so that **raw business data stays in the customer VPC**. Reports about a path that would exfiltrate row-level data, bypass read-only controls, or skip RLS/session context are in scope.
- This repository currently publishes specifications, documentation, and fictional demo data. Please still report issues in the published contracts (for example, a `semantic_map` or MCP schema that would encourage unsafe defaults).
- Do not test against systems you do not own. The fictional demo data in `examples/monthend/` is the only dataset shipped here.
