# Official ChatGPT voice expansion review — 2026-10-28

## Failure and source identity

[Run 40614935229](https://github.com/SHIKI1255/shadowrocket-config/actions/runs/40614935229)
stopped at the unchanged quantity gate: voice raw_count 150 -> 402.
Its 139 tests passed with 16 existing environment-dependent skips; offline
reproduction and publication did not execute. The last successful release is
`53cbff9ab95d393a39639835f51b61b1b36b86ce`.

The failed run saved public input hashes in its log, not a downloadable artifact.
All 44 public inputs were independently recovered and matched against those
logged hashes. The mutable official voice endpoint returned byte-identical data.
The source is [chatgpt-voice.json](https://openai.com/chatgpt-voice.json), linked by
[OpenAI's network requirements](https://help.openai.com/en/articles/9247338-network-recommendations-for-chatgpt-errors-on-web-and-apps).

| Input | Previous release | Reviewed failure snapshot |
| --- | --- | --- |
| creationTime (UTC) | 2026-10-25T19:25:06.552421+00:00 | 2026-10-27T22:28:21.782457+00:00 |
| Downloaded bytes SHA256 | 000da4875ec5ffc62d165de5d2a305a22850693d319d0ecc03b5dc91b8515d8c | d2923918cc4cd6bf382f379b1601af871fbff66d8fefb81d4457f2ccba1ec959 |
| Raw / retained / unique | 150 / 150 / 150 | 402 / 402 / 402 |

The JSON schema remains creationTime plus prefixes. All entries are canonical
public IPv4 /32 addresses. There are 252 additions, no deletions, duplicate keys,
duplicate prefixes, overlapping ranges, IPv6 or broadened prefixes. All 150 old
entries retain their relative order. The fixture preserves every address,
creationTime and source order, with normalized JSON whitespace.

Two adjacent pairs could be combined into 9.205.248.20/31 and
134.138.50.120/31 (400 entries after lossless aggregation). They are not
duplicates; production preserves all 402 original /32 entries.

This is an official address-list expansion, not parser inflation or a cloud-ASN
expansion. Actual server reachability and mobile voice performance remain untested.

## Exact approval and unchanged guards

Only the two active voice baselines (shadowrocket and clash) become 402.
Quantumult X consumes the shared mobile source. The previous permission approved
23 -> 150 only. The static count gate ran before previous-release comparison,
and the old permission's starting state would not match the current 150 entries.
Updating counts alone would still fail the previous-release addition gate.

The new reviewed_transition approves only these ordered rule states:

- from_sha256: `eb4a1c6d29cd30bb29ee70afc32f8d75b8a52ad6dc32a325d8ae60718c9adef5`
- to_sha256: `b6a91feb1ae7cb6c24e01958d87211f94a2380f85ef932e8f08c4d5e0a62b398`

These hashes cover raw_count, retained_count and the complete ordered rule list,
not raw JSON formatting. No parser, threshold or conflict permission changes.
min_ratio=0.8, max_ratio=1.5, max_removed_ratio=0.2 and max_added_ratio=0.5 stay intact.
After the approved release, ordinary comparisons resume against 402.
Changed members, ordering, options or counts in the first transition still fail.
Historical 23 -> 150 tests remain, alongside the active transition and subsequent
321/322/603/604 count-boundary tests. Unknown syntax still blocks publication.

## Separate Apple source review

The failure snapshot pins:

- blackmatrix7: `0b6897dc17e0c513d25449c935a9694818e32f0c`
- v2fly: `1b81d3cd34096e5d8cac6bca5935e80f30e990f0`

Besides the two copies of voice, only the
[Clash Apple input](https://github.com/blackmatrix7/ios_rule_script/blob/0b6897dc17e0c513d25449c935a9694818e32f0c/rule/Clash/Apple/Apple.list)
changes bytes: its update comment and the positions of DOMAIN-SUFFIX,apple.news
and DOMAIN-SUFFIX,appsto.re. Both move within the same DIRECT source to follow
apple-dns.net. The 1,592-record multiset, selectors, policies and skip records are
identical; there are no additions or deletions. Retain upstream order without
local sorting or new conflict allowances. Other source and license bytes match
the previous release, including OpenAI, Claude and Gemini.

## Impact, verification and rollback

For the reviewed snapshot, merged logical rules change:

- Shadowrocket / Quantumult X: 14,336 -> 14,588; duplicates 261, conflicts 42 unchanged.
- Clash: 14,315 -> 14,567; duplicates 12,368, conflicts 39 unchanged.

The existing 195 / 195 / 223 routing cases pass in memory. Each of the 402 voice
addresses must hit its exact voice selector, not merely a same-policy fallback.
Shadowrocket and Clash preserve no-resolve; QX retains its documented conversion
to native ip-cidr/proxy without no-resolve. The IP rules remain destination-wide,
without adding a port filter. Clash voice remains absent from DNS selectors.

Release acceptance requires the complete test suite, network build, byte-identical
offline SHA256SUMS, actual PR/main CI and anonymous fixed/rolling download checks.
Consumers must reference the producer's new release commit and shared_version.
Source inspection and static routing checks do not establish device connectivity.

DNS, UDP, Hosts, nodes, templates, source order, workflows and automations are
outside the patch. No private subscription is needed.

Retain these pre-change releases for rollback:

- Shadowrocket: `53cbff9ab95d393a39639835f51b61b1b36b86ce`
- Quantumult X: `cd3c1a4fef9bf27f7449f76dc4e8e06d5fb2ab3c`
- Clash: `abd0371ab869e3b003574e43a3b5949bae53e339`

Fixed links replace release with the full commit SHA. To freeze multi-resource
clients, freeze their internal resource URLs too. A source rollback must rebuild
the archived 150-entry inputs with their corresponding approved source revision;
simply reverting counts while downloading the live 402-entry source will correctly
fail again. Validate and republish deliberately, then synchronize consumers.
