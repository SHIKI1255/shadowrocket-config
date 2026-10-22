# Claude source review — 2026-10-22

The user approved reviewing the upstream change discovered while repairing the
main build's optional-PyYAML test boundary. This review does not change DNS,
nodes, Hosts mappings, schedules, source order, filters, or global size limits.

## Compared inputs

- Previous published release: `33e295a7e51b0ed13545e2aadbf134fc498b4d0e`.
- Reviewed v2fly snapshot: `5b6a77d14fb904a1255f02e2e1b2bcc36ed31f83`.
- Anthropic change: [v2fly PR 4116](https://github.com/v2fly/domain-list-community/pull/4116),
  merged as `bdf45acfdeaec2644257bb8947e0a0294276d5ef`.
- blackmatrix7 snapshot: `f9e403a75bbc4bee939c6d8dd766cf78f1b512f3`.

The previous published Claude source had 9 rules; the new source has 19.
The older static baseline was 8 for Shadowrocket and 9 for Clash, explaining the
first error `Abnormal claude raw_count: 8 -> 19`. Only Claude's two count
baselines are updated to 19. All existing conflict records remain unchanged.

## Accepted additions

Each addition is a DOMAIN-SUFFIX rule routed through PROXY in the Claude category:

- ant.dev
- anthropic.team
- antspace.dev
- chatbotclaude.com
- claude.app
- claude.new
- claude.site
- claudeacademy.com
- claudepages.dev
- claudestudio.com

There are no removals, shared-service parent domains, ASN rules or new policy
conflicts in this change. Existing source and category order is retained.
Root and child-host routing cases require the exact suffix rule and Claude category
on all three platforms.

These are accepted community classifications, not a claim that every domain is
officially required for every Claude feature. The upstream author supplied
allocation and nameserver evidence. A fresh public DNS check found seven of the
ten new roots at 160.79.104.10, also used by claude.com; ARIN identifies the
[160.79.104.0–160.79.111.255 allocation](https://rdap.arin.net/registry/ip/160.79.104.10)
as Anthropic, PBC. The remaining three (ant.dev, anthropic.team,
chatbotclaude.com) returned no root A record and shared claude.com's nameserver
pair; this is supporting evidence, not independent ownership proof.
[claudeacademy.com](https://claudeacademy.com/) redirects to
https://academy.claude.com/. No private subscriptions were consulted.

The same upstream snapshot adds `full:o33249.ingest.us.sentry.io @ads` to OpenAI.
The existing ads exclusion removes it, so OpenAI's retained rule set is unchanged.
All other retained source rule sets are identical to the previous release.
Clash's upstream Apple source reorders some of its existing 1,593 rules: its rule
set is identical, and the moved domain rules all retain DIRECT within the same
Apple category. This is an upstream order change, not byte-identical output.
Cross-category order and the conflict allowlist are unchanged. Shadowrocket and
Quantumult X have no corresponding Apple order change; their rule text changes
only by the ten approved Claude additions.

## Exact transition guard

The previous-release size and set-difference checks are retained. An explicitly
reviewed transition is registered only for Claude in each source platform.
Its fingerprint covers raw count, retained count and the complete ordered rule
list, serialized with the existing deterministic JSON encoder:

- Before: `3c55f91b630ea75919b4eb502916f84b511e07c4e1ec55873d61a02df59edfef`.
- After: `48c97b9f45197186a22aa19ccea38cdcaa09653ff8afd6e85e7bccf9f234e261`.

Only this exact old-to-new state is accepted across the size threshold. Any extra,
missing, replaced or reordered rule during this transition is rejected. Other
sources retain their original previous-release checks. After the 19-rule state
is published, subsequent changes use the ordinary size and replacement gates.
The global thresholds and conflict allowlist are not broadened. Unknown
transitions require another explicit review; audit outputs remain unpublishable.

## CI dependency repair

Four existing recovery test bodies and assertions remain unchanged. Their
synthetic JSON subscriptions run through the standard-library parser everywhere,
and additionally through the real pinned PyYAML parser when installed.
No skip is added. The dedicated Hosts job still installs PyYAML 6.0.3 and tests
duplicate YAML keys, aliases, tags, source checks and publication failure paths.
Hosts test changes now trigger the main workflow as well as the Hosts workflow.
Module-only changes continue to use the independent Hosts workflow.
