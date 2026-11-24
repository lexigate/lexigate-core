# Fictional month-end demo dataset

All people, departments, and the company name in this dataset are **fictitious** (デモデータ). They are not real employees or a real company.

| Item | Value |
|---|---|
| Base date | 2026-03-10 (JST) |
| Coverage | 2026-01-01 to 2026-03-09 (JST, inclusive) |
| “As of last month-end” | exclusive upper bound 2026-03-01 00:00 JST |
| Size | 16 business tables, about 6,800 rows |
| Language of labels | Japanese |

- [`semantic_map.yaml`](semantic_map.yaml) — example map for this dataset (v0).
- [`data/`](data/) — CSV files plus `expected.json` (independent reference totals) and `manifest.json` (row counts and SHA-256 hashes).
- [`generator/`](generator/) — optional source of the CSVs; see its README. Regeneration depends on a third-party CC0 sample that is not shipped here.

This revision does **not** include a loader, a database, or a query engine. Phase 1 will add a Docker Compose stack and `lexigate ask` on top of this data. Until then the files are here so the specification has a concrete, reviewable example.

A typical path-1 question on this dataset is: 「先月末時点の承認済み未払い出張精算の合計は？」
