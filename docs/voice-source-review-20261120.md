# Official ChatGPT voice list shrink review — 2026-11-20

## Failure and source identity

The scheduled `Validate and publish` run
[43790681758](https://github.com/SHIKI1255/shadowrocket-config/actions/runs/43790681758)
stopped at the unchanged quantity gate: `Abnormal voice raw_count: 540 -> 411`.
Unit tests passed; the download/convert step stopped, and publication was skipped.
The review-only candidate is PR #52; this document records the accepted
snapshot-level classification review.

The source remains the official
[chatgpt-voice.json](https://openai.com/chatgpt-voice.json), linked by OpenAI's
network requirements. The endpoint returned the same 411-prefix list on every
check between 2026-11-16 and this review, so the shrink is not a transient
read.

| Input | Previous release | Reviewed snapshot |
| --- | --- | --- |
| creationTime (UTC) | 2026-11-12T06:31:19 | 2026-11-16T01:33:53 |
| Raw / retained / unique | 540 / 540 / 540 | 411 / 411 / 411 |

## Snapshot-level classification review

- The JSON schema remains `creationTime` plus `prefixes`; every entry is a
  canonical public IPv4 `/32`; there are no IPv6 entries, private networks,
  duplicate prefixes, overlapping ranges or broadened prefixes.
- Exactly 129 addresses were removed and none added. Retained addresses keep
  their previous relative order.
- Removed addresses concentrate in Azure-style `/16` families (9.205, 20.113,
  20.163, 20.215, 20.226, 134.138 and similar); the retained set keeps the same
  family mix. This is an official address-list shrink, not parser inflation,
  cloud-ASN expansion or local filtering.
- `collapse_addresses` can combine only two adjacent pairs into `/31` ranges
  (409 entries after lossless aggregation). Production keeps the original 411
  `/32` entries; no aggregation is applied, consistent with earlier reviews.

## Exact approval and unchanged guards

Only the two active voice baselines (shadowrocket and clash) become 411.
Quantumult X consumes the shared mobile source, and Clash keeps the voice
provider PROXY without a DNS selector. The reviewed transition approves only
these ordered rule states:

- from_sha256: `5430bb229a1a721291576f0382127ec7193878f1dbfc818c7aa4892940b3d3f7`
- to_sha256: `020f85cc14349ac30fd10e28b8e61311def35d9a4b0b1b5e9a595c05a869979d`

No parser, threshold, conflict or source changes are made. `min_ratio=0.8`,
`max_ratio=1.5`, `max_removed_ratio=0.2` and `max_added_ratio=0.5` stay intact.
After the approved release, ordinary comparisons resume against 411. The
fixtures `tests/fixtures/voice-20261112.json` and
`tests/fixtures/voice-20261116.json` preserve both states, and
`ReviewedVoice411Transition` covers the exact transition, the 129-removal
identity, quantity boundaries and the inherited three-client equivalence
checks.

Actual server reachability and mobile voice performance remain untested; the
static review does not claim device connectivity.
