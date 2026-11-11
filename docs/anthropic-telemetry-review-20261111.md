# Anthropic (Claude) telemetry review — 2026-11-11

The user approved handling option A-a: standalone `@telemetry` entries on the
Claude source are excluded as telemetry metadata. They do not enter any client
rule set. This review does not change DNS, nodes, Hosts mappings, schedules,
source order, filters or global size limits.

## Failure and evidence

The scheduled `Validate and publish` workflow failed at `fe801e3fe828b7173f6a9ff96498205f04651287`
from 2026-11-08 (run 42434337269) onward, including 2026-11-11 run 42942589814.
All 193 unit tests passed; the download/convert step stopped with
`BUILD STOPPED: claude:24: Unknown v2fly attribute semantics: @telemetry`.
The physical line number includes comments and blank lines. The publish job was
skipped; the failure is closed by design, not a regression.

## Upstream identities

- [v2fly PR #4268](https://github.com/v2fly/domain-list-community/pull/4268),
  commit `6766d09f4a951f4f11d822891a9736606cd910ac`, 2026-11-07T05:36:11Z,
  adds Claude Code telemetry hosts to `data/anthropic`:

  `full:http-intake.logs.us5.datadoghq.com @telemetry`
  `full:o1158394.ingest.us.sentry.io @telemetry`

- Previous reviewed Claude snapshot `bdf45acfdeaec2644257bb8947e0a0294276d5ef`
  (19 raw records, 19 retained). SHA256 `48597d05d9206b7c841be6872c7ca954685a416d7e61c9316c017ed5f28c7053`.
- New snapshot at `6766d09f4a951f4f11d822891a9736606cd910ac` (21 raw records).
  SHA256 `59ad6a25a6d9759995777fe98f2cf30675199fad00546a0ffe027b95ec0776f0`.
  This matches the `public_source_snapshot` file hash printed by the failed run,
  confirming the reviewed bytes are the ones that stopped the build.

Both new entries are third-party telemetry endpoints (Datadog HTTP intake and
Sentry ingest) used by Claude Code, not Anthropic service domains.

## Accepted semantics change

Only the exact standalone attribute set `{@telemetry}` is newly accepted, and
only when the source id is `claude`; those entries are skipped with reason
`telemetry attribute`. The existing reviewed `{@ads, @telemetry}` pair keeps the
ads exclusion, and `{@ads, @!cn}` behavior is unchanged. Standalone `@telemetry`
on any other source, `@!telemetry`, unreviewed combinations such as
`{@telemetry, @!cn}`, and additional unknown attributes still stop publication.
Domain syntax and regex validation still execute before any exclusion.

Retained Claude rules stay at 19 on all three platforms; the two telemetry
entries add no service rules, no REJECT rules and no filter changes. Existing
personal helpers are untouched. `shared/baseline.json` only updates Claude
`raw_count` from 19 to 21 for the clash and shadowrocket tables; `retained_count`
remains 19 and no `reviewed_transition` is added because the ordinary quantity
and replacement gates already pass with unchanged rules.

## Validation

- Full test suite without PyYAML (guarded skips) and with the existing pinned
  PyYAML 6.0.3 installation.
- Network build with `--previous-release` and diagnostics, offline replay with
  byte-identical `SHA256SUMS`.
- Regression tests in `tests/test_v2fly_telemetry.py` cover immutable fixture
  hashes, three-client equivalence, raw 19→21 with retained 19, exact
  exclusion reasons, fail-closed behavior outside Claude, unreviewed
  combinations, syntax-before-exclusion ordering, physical line reporting and
  empty-source protection.

Reverting the parser alone while following the new upstream would deliberately
restore the unknown-attribute stop. Rollback remains the previous published
release tree.
