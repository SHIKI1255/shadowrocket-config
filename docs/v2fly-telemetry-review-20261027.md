# v2fly telemetry compatibility review — 2026-10-27

## Failure and evidence

[Scheduled run 40432894637](https://github.com/SHIKI1255/shadowrocket-config/actions/runs/40432894637)
checked out `fc8e802c91b17931e711813c029be33d25a4d23a` and stopped with
`openai:24: Unknown v2fly attribute semantics`. The number is the physical source
line (including comments/blank lines), not a generated rule index. All 130 tests
completed with 16 existing environment/platform skips; offline replay and
publication did not execute. The last successful release was
`7d5e5533f7a924ed3aea7c878c6f1d994a7456e2`.

[v2fly PR #4143](https://github.com/v2fly/domain-list-community/pull/4143),
commit `8ba15e5d51b3cbc44853f78bd2ff29162cc110ba`, added telemetry labels and retained
existing ads labels for backward compatibility on 2026-10-26 18:01:51 +08:00.
OpenAI lines 24–26 changed only by adding `@telemetry`:

- `full:browser-intake-datadoghq.com @ads @telemetry`
- `full:o33249.ingest.sentry.io @ads @telemetry`
- `full:o33249.ingest.us.sentry.io @ads @telemetry`

The producer checks all attribute semantics before its existing ads exclusion.
That guard correctly stopped the unreviewed label. This is independent of the
previous voice count transition and of Hosts-only PRs. Only the two module files
changed between the previous successful source commit and the failed source commit.

## Reviewed identities and evidence limit

| Input | Commit | SHA256 of data/openai |
| --- | --- | --- |
| Previous published snapshot | `664598567e92d797bea11bb83cf939d1f37e9512` | `ec1043e63c31bbef51efe13a3e2af29a3f66b8b4692f517d75a864cbc4a3d88d` |
| Current fixed snapshot | `fa3cb96fbc210b8d6466251a1d71b4a6e63a9c85` | `25d53e9af3e04d926210d6436bd791594ef4b86f2dc3c108510ac8059d76d969` |

The new file is 760 bytes, Git blob `d6f5f3d1b5fbcfc87e04aafcbeef5ffdc81d766c`.
Its repository commit is also the latest commit returned by the historical query
at the failure time. Fetching this immutable file reproduces the exact failure.
The failed job retained neither an artifact nor logged upstream revision/hash;
these are reconstructed source identities, not a recovered failed-run snapshot.

Network collection now prints a `public_source_snapshot` event before parsing,
containing public repository revisions and file SHA256s. It contains no response
bodies, request headers or environment values. Existing download validation remains.

## Minimal semantics change

Only the exact attribute set `{@ads, @telemetry}` is newly accepted, irrespective
of attribute order. Existing `@ads` / `@!cn` behavior stays unchanged. Standalone
`@telemetry`, additional unknown attributes, and unreviewed combinations still
stop publication. Domain syntax and regex validation execute before ads exclusion.

This acknowledges reviewed telemetry metadata without defining a new filtering
policy. The existing ads exclusion continues: these entries do not add service
rules from this source. It does not create REJECT rules or remove existing personal
helpers, including `o33249.ingest.sentry.io` and `rum.browser-intake-datadoghq.com`.

Old/new OpenAI input both contain 25 raw records, retain the same ordered 21 rules
and exclude the same 4 ads records. All three native conversions are unchanged.
No baseline, reviewed-transition, global threshold, filter list or rule order
changes are required.

## Separately reviewed upstream update

The only effective source-rule delta versus the previous release is
[v2fly #4190](https://github.com/v2fly/domain-list-community/commit/fa3cb96fbc210b8d6466251a1d71b4a6e63a9c85):
three exact Gemini in Workspace entries:

- `appsgenaiserver-pa.clients.google.com`
- `appsgenaiserver-pa.clients6.google.com`
- `appsgenaiserver-pa.googleapis.com`

Gemini raw/retained counts increase from 46 to 49. No old entry is removed or
reordered. The service description is upstream's classification; no device-level
Workspace test is claimed. All three Google-owned domain ranges already matched
Google PROXY. They now match earlier Gemini exact PROXY rules; synthetic subdomains
still match the prior Google suffix rules. This is a scoped category refinement,
not an expansion to shared parent domains or a DIRECT-to-PROXY change.

Existing Clash Gemini and Google DNS selectors both use the same configured
overseas resolvers and actual main selection group. The category change does not
change DNS servers, resolver paths or local template settings.

blackmatrix7 stays at `9b29c945b9164a02c7018ed333cb9b5bc1bcb417`.
All other downloaded rule/license bytes, including Anthropic, TikTok and the
official 150-entry voice input, match the previous release.

| Platform | Logical rules before → after | Conflicts | Routing cases | Voice cases |
| --- | --- | --- | --- | --- |
| Shadowrocket | 14333 → 14336 | 42 unchanged | 195 | 150 |
| Quantumult X | 14333 → 14336 | 42 unchanged | 195 | 150 |
| Clash | 14312 → 14315 | 39 unchanged | 223 | 150 |

Duplicate counts remain 261/261/12368 respectively. Normal source quantity,
replacement and conflict checks pass with the existing baselines.

## Validation and rollback

Nine added regression tests cover immutable snapshot hashes, ordered three-client
equivalence, attribute order, exact match scope, unknown semantics including on ads,
malformed rules, retained personal helpers, empty-set/quantity protection and public
snapshot diagnostics surviving a later parse failure. No previous assertion or skip
is removed or weakened. Local full suites: 139 run, 16 existing skips without
PyYAML; 139 run, 3 existing skips with the existing pinned PyYAML installation.

The previous input snapshot still generates byte-identical Shadowrocket config
and identical three-platform shared payload. A live build passes all gates;
the final patch must also pass offline replay with identical SHA256SUMS, PR CI,
main publication and both consumer workflows. Verify identical shared versions and
anonymous fixed/rolling download hashes before declaring publication recovered.

DNS/UDP templates, Hosts mappings, node settings, workflows and recurring tasks
are outside the patch. QX/Clash only need remote rule refresh after their releases;
the adapters and templates do not change. Client networking remains device acceptance.

Pre-change fixed releases for rollback:

- Shadowrocket: `7d5e5533f7a924ed3aea7c878c6f1d994a7456e2`
- Quantumult X: `6ebeb3e73373d8700f75ae1f3a49c363983d55d7`
- Clash: `b42b532f26cf0b834ce63008a3416216b40b59b4`

Use full commit SHA URLs for prior resources. Multi-resource clients must freeze
the internal resource links too to remain fully pinned. For a rebuilt rollback,
use the existing replay_commit workflow input with the archived producer release,
then synchronize consumers and verify hashes. Reverting the parser alone while
following the new upstream would deliberately restore the unknown-attribute stop.
