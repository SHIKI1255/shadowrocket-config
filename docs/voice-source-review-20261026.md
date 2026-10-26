# Official ChatGPT voice source review — 2026-10-26

## Scope and evidence

Repair the quantity-gate failure in [run 40249230408](https://github.com/SHIKI1255/shadowrocket-config/actions/runs/40249230408).
Only the two voice count baselines and their exact reviewed transition change.
The parser, global thresholds, source order, conflict records, DNS, UDP, nodes,
Hosts mappings, workflows and local automation settings remain unchanged.

- Previous public release: `141daf07a60a810f30cb3e40867781b124d40eb0`.
- Official input: [chatgpt-voice.json](https://openai.com/chatgpt-voice.json), also linked by
  [OpenAI's network requirements](https://help.openai.com/en/articles/9247338-network-recommendations-for-chatgpt-errors-on-web-and-apps).
- Old creationTime: `2026-03-26T20:12:45.451356+00:00`.
- New creationTime: `2026-10-25T19:25:06.552421+00:00`
  (2026-10-26 03:25:06 in Beijing).
- Old downloaded bytes SHA256:
  `8f03f3c594165eeff009915c7e2beb4bdfb9d429fb1955c205e3b33ec781899a`.
- New downloaded bytes SHA256, independently read more than once:
  `000da4875ec5ffc62d165de5d2a305a22850693d319d0ecc03b5dc91b8515d8c`.

The response still contains exactly creationTime and prefixes. The 150 unique
prefixes are canonical, globally routable IPv4 /32 addresses: 23 retained,
127 added, none removed. Existing addresses retain their relative order.
There are no duplicate JSON keys, duplicate prefixes, overlapping ranges,
IPv6 entries, private addresses or broader prefixes in this snapshot.
Collapsing addresses still yields 150 /32 entries; aggregation cannot reduce the
list without broadening its scope. Tests store normalized JSON copies preserving
every address, metadata value and prefix order.

This is accepted as an official server-address expansion, not a conversion error
or cloud-ASN expansion. It does not establish successful voice connectivity.
The existing IP routing rules apply to destinations regardless of port; this
change does not introduce a port filter or alter transport/fallback settings.

## Exact transition and protection

For both shadowrocket and clash in shared/baseline.json, voice raw_count and
retained_count become 150. Quantumult X reuses the canonical mobile source.
The existing reviewed_transition code accepts only these complete ordered states:

- from_sha256:
  `4f377c5cc8a14acf0aeac4c48c7c780d8b88d7728570e1046510aaca17a780eb`
- to_sha256:
  `eb4a1c6d29cd30bb29ee70afc32f8d75b8a52ad6dc32a325d8ae60718c9adef5`

These fingerprints cover raw_count, retained_count and the complete ordered
canonical rules, including policy and no-resolve. They are not the raw-JSON
hashes above. A changed address, order, option, count or unreviewed old state
still stops the first publication. After publication, subsequent builds compare
against the new release using the normal size/addition/removal gates.

Global limits remain min_ratio=0.8, max_ratio=1.5, max_removed_ratio=0.2 and
max_added_ratio=0.5. No other baseline or conflict allowlist is changed.

## Isolated voice impact

With all other inputs fixed to the previous public release:

| Platform | Before | After | Voice entries |
|---|---:|---:|---:|
| Shadowrocket | 14,187 | 14,314 | 23 -> 150 |
| Quantumult X canonical rules | 14,187 | 14,314 | 23 -> 150 |
| Clash | 14,166 | 14,293 | 23 -> 150 |

Exactly 127 rules are added per platform; no existing rule is removed or
reordered. Exact deduplication counts remain unchanged, as do 42 registered
mobile conflicts and 39 Clash conflicts. Existing 195/195/223 routing cases pass.
Each platform matches all 150 IPs to the exact voice rule and category; all 300
neighboring addresses avoid the voice category.

Shadowrocket retains IP-CIDR with PROXY and no-resolve. Clash keeps the same
IP-CIDR selector and no-resolve in its voice provider, whose policy remains the
airport's selected main group. Quantumult X emits native ip-cidr with proxy and
continues its explicit removal of no-resolve. No provider or resource URL changes.

## Separately reviewed routine upstream changes

The live build also resolves these existing upstreams:

- blackmatrix7: `9b29c945b9164a02c7018ed333cb9b5bc1bcb417`
- v2fly: `664598567e92d797bea11bb83cf939d1f37e9512`

The [blackmatrix7 update](https://github.com/blackmatrix7/ios_rule_script/commit/9b29c945b9164a02c7018ed333cb9b5bc1bcb417)
adds these 20 DOMAIN-SUFFIX records to the existing general proxy category:

- 123av.org
- bidobido.xyz
- bikaios.xyz
- bikawebapp.com
- console.typesafe.ai
- diwodiwo.xyz
- ga.jspm.io
- huakacomic.com
- kimootoko.net
- manhuabika.com
- minkch.com
- movie.eroterest.net
- njav.com
- njav.tv
- njavtv.com
- pica-id.com
- po-kaki-to.com
- tipatipa.xyz
- wikawika.xyz
- zozovideo.com

These are scoped service domains/subdomains supplied by the already approved
general proxy source. Their existing upstream classification is retained; they
are not described as OpenAI endpoints or locally verified service ownership.
No new repository, broad cloud parent, ASN, rejection policy or local exception
is introduced. Cross-platform parity and existing conflict/quantity gates pass.

The Apple source removes DOMAIN-SUFFIX,icloud.cn,DIRECT. Its apex and subdomains
still match DOMAIN-SUFFIX,cn,DIRECT in china, with or without supplied GeoIP facts.
The business policy stays DIRECT. Clash's domain DNS selection consequently moves
from the Apple DIRECT provider to the China DIRECT provider; both use the same
existing domestic resolvers. The DNS configuration itself is unchanged.
Other Apple changes only reorder existing same-policy entries within the category.

The combined live result is therefore 14,333 Shadowrocket/QX canonical rules and
14,312 Clash rules: +127 voice, +20 general proxy, -1 redundant Apple selector.
The normal upstream changes are not included in the special voice transition and
do not receive any additional threshold or conflict exemption.

## Verification and rollback

Keep the existing unit suite and skip conditions. Additional offline fixture tests
cover the exact transition, same-count address replacement, ordering/options,
missing/extra/duplicate records, wrong old state, subsequent quantity/replacement
checks, native conversion, exact hits versus same-policy fallback, neighboring
IP exclusion, deduplication and malformed/private/broad/wrong-family inputs.
Run standard-library and existing PyYAML environments, then a full network build
and offline replay with byte-identical SHA256SUMS. Independently compare isolated
voice and live upstream deltas. CI must execute all normal validation steps.

After the public producer succeeds, run the existing QX and Clash workflows.
Require the same shared_version/shared_release, native Clash checks, and anonymous
fixed-commit plus rolling-download hashes. Preserve QX main config/rewrite and
Clash override/guard bytes. Device refresh and actual connectivity remain separate.

For immediate client fallback, use the pre-change fixed commits:
- Shadowrocket: `141daf07a60a810f30cb3e40867781b124d40eb0`
- Quantumult X: `dc0aee4c7a2ba992dc91a7cbb1ffc2a3ab8dc890`
- Clash: `0953f266164680a93faa0557f0d307b60995326c`

Freeze each remote rule URL/cache as needed; freezing only a template that still
references rolling rule URLs does not freeze its rules. Repository rollback
requires the corresponding old source and old input snapshot to be validated
together before republishing and synchronizing consumers. Merely reverting the
count to 23 while fetching the current official 150 entries will fail again.
