# Changelog

## 2.2.0 — 2026-09-26

- Added `convert --jobs N` for independent subject staging with Python spawn processes. Default 1; range 1–16; capped by the manifest subject count.
- Kept source selection, transformation algorithms, numerical thresholds, actual XCP-D reader checks, dataset locking, and single-transaction publication.
- Bounded pending jobs; stop new submissions after an observed failure, cancel unstarted work and wait for running workers before returning.
- Record requested/effective concurrency, thread environment, worker PID and timing. Preserve manifest order in summary reports.
- Log checksum, staging, reader-check and publication phases; retain failure phase/type, including KeyboardInterrupt diagnostics.
- Added genuine multi-process equivalence, copy/symlink, failure and rollback tests.
- Public repository packaging adds a Bash Docker launcher, platform/migration/troubleshooting documentation, generic manifests and launcher tests. Scientific Python code matches the locally validated 2.2.0 tool; no research data or individual project audits are distributed.

## 2.1.1 — 2026-09-26

- Standardized BOLD-first input examples.
- Fixed working-directory anatomy fallback accidentally adding unpublished BOLD runs.
- Recover an available fallback boldref before generating one from the first BOLD frame.
- Check explicit fallback paths; resolve relative source_dataset against the manifest directory.
- Clarified container/local paths, optional mounts and historical versus reusable settings.

## 2.1.0 — 2026-09-26

- Added `--layout inplace`, exclusive locking, compatibility updates with backups, journalled publication and `rollback`.
- Preserve original metadata, participants and unselected subjects/tasks.
- Validate the combined published dataset and reject conflicting scientific files.

## 2.0.0

- Explicit source manifests, independent copy/symlink input preparation, RAS-to-LPS conversion and numerical inverse validation.
- Validate geometry, temporal/confound correspondence, 36P columns and the actual XCP-D 26.2.0 reader.
