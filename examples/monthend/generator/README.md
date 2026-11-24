# Month-end demo generator

This directory contains the deterministic generator that produced the CSV files in `../data/`.

**You do not need to run it** in order to use the published dataset. The CSVs are the artifact.

Regenerating the CSVs requires a local PostgreSQL database that already has the
[HR Sample Database](https://databasesample.com/datasets/hr-sample-database) (CC0-1.0, v2, 100k tier)
loaded as schema `sample_raw_100k`. That sample is **not** shipped in this repository. The generator
selects a few departments and employees, re-keys them to virtual ids, and replaces names with
fictitious Japanese names. Cross-domain records (attendance, trips, claims, payments) are synthetic,
using a fixed seed (`20260310`).

If you do not have that sample, keep using the published CSVs.
