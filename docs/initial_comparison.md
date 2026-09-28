# 初始配置差异报告（2026-09-28）

A = shadowrocket-optimized.conf；B = shadowrocket-optimized (2).conf。使用同一固定上游版本展开比较。

General 与 Host 的值和顺序逐项相同，仅新增 update-url。URL Rewrite 去除单引号，保留路径、查询参数和片段。

这是静态匹配分析；`UNRESOLVED` 表示需要目标 IP / GeoIP 判断，不能直接理解为直连。

| 配置 | 展开规则 | 精确去重后 | 同选择器策略冲突 |
| --- | ---: | ---: | ---: |
| A | 14337 | 14047 | 44 |
| B | 14382 | 14073 | 42 |
| new | 14111 | 14111 | 42 |

## 分流与规则来源变化

| 用例 | A | B | 优化后 | 优化后的首个相关规则 |
| --- | --- | --- | --- | --- |
| OpenAI stats | UNRESOLVED | PROXY | PROXY | `DOMAIN-SUFFIX,oaistatsig.com,PROXY` |
| ChatGPT Azure | DIRECT | PROXY | PROXY | `DOMAIN-WILDCARD,chatgpt-async-webps-prod-*.webpubsub.azure.com,PROXY` |
| Prism legacy | UNRESOLVED | UNRESOLVED | PROXY | `DOMAIN-SUFFIX,crixet.com,PROXY` |
| WorkOS cdn | UNRESOLVED | PROXY | PROXY | `DOMAIN,cdn.workos.com,PROXY` |
| WorkOS forwarder | UNRESOLVED | PROXY | PROXY | `DOMAIN,forwarder.workos.com,PROXY` |
| WorkOS setup | UNRESOLVED | PROXY | PROXY | `DOMAIN,setup.workos.com,PROXY` |
| WorkOS images | UNRESOLVED | PROXY | PROXY | `DOMAIN,images.workoscdn.com,PROXY` |
| WorkOS imgix | UNRESOLVED | PROXY | PROXY | `DOMAIN,workos.imgix.net,PROXY` |
| Datadog exact helper | UNRESOLVED | UNRESOLVED | PROXY | `DOMAIN,rum.browser-intake-datadoghq.com,PROXY` |
| Email link | UNRESOLVED | UNRESOLVED | PROXY | `DOMAIN-SUFFIX,ct.sendgrid.net,PROXY` |
| Claude upload | UNRESOLVED | PROXY | PROXY | `DOMAIN-SUFFIX,claudeusercontent.com,PROXY` |
| Claude MCP content | UNRESOLVED | PROXY | PROXY | `DOMAIN-SUFFIX,claudemcpcontent.com,PROXY` |
| Claude CDN | UNRESOLVED | PROXY | PROXY | `DOMAIN,servd-anthropic-website.b-cdn.net,PROXY` |
| Copilot new | UNRESOLVED | UNRESOLVED | PROXY | `DOMAIN-SUFFIX,copilot.com,PROXY` |
| Copilot cloud | DIRECT | DIRECT | PROXY | `DOMAIN-SUFFIX,copilot.cloud.microsoft,PROXY` |
| Apple shared helper | DIRECT | PROXY | PROXY | `DOMAIN,humb.apple.com,PROXY` |
| Domestic ByteDance snssdk | PROXY | DIRECT | DIRECT | `DOMAIN-SUFFIX,snssdk.com,DIRECT` |
| Domestic ByteDance bytedapm | PROXY | DIRECT | DIRECT | `DOMAIN-SUFFIX,bytedapm.com,DIRECT` |
| Shared/scope: auth0.com | PROXY | PROXY | UNRESOLVED | `FINAL,PROXY` |
| Shared/scope: login.auth0.com | PROXY | PROXY | UNRESOLVED | `FINAL,PROXY` |
| Shared/scope: stripe.com | PROXY | PROXY | UNRESOLVED | `FINAL,PROXY` |
| Shared/scope: api.stripe.com | PROXY | PROXY | UNRESOLVED | `FINAL,PROXY` |
| Shared/scope: sentry.io | PROXY | PROXY | UNRESOLVED | `FINAL,PROXY` |
| Shared/scope: other.ingest.sentry.io | PROXY | PROXY | UNRESOLVED | `FINAL,PROXY` |
| Shared/scope: algolia.net | PROXY | PROXY | UNRESOLVED | `FINAL,PROXY` |
| Shared/scope: launchdarkly.com | PROXY | PROXY | UNRESOLVED | `FINAL,PROXY` |
| Shared/scope: identrust.com | PROXY | PROXY | UNRESOLVED | `FINAL,PROXY` |
| Shared/scope: observeit.net | PROXY | PROXY | UNRESOLVED | `FINAL,PROXY` |
| Shared/scope: segment.io | PROXY | PROXY | UNRESOLVED | `FINAL,PROXY` |
| Shared/scope: static.cloudflareinsights.com | PROXY | PROXY | UNRESOLVED | `FINAL,PROXY` |
| Shared/scope: browser-intake-datadoghq.com | PROXY | PROXY | UNRESOLVED | `FINAL,PROXY` |
| Shared/scope: openaicom.imgix.net | PROXY | PROXY | PROXY | `DOMAIN,openaicom.imgix.net,PROXY` |
| Shared/scope: sub.openaicom.imgix.net | PROXY | PROXY | UNRESOLVED | `FINAL,PROXY` |
| Shared/scope: openaiapi-site.azureedge.net | PROXY | PROXY | PROXY | `DOMAIN,openaiapi-site.azureedge.net,PROXY` |
| Shared/scope: sub.openaiapi-site.azureedge.net | PROXY | PROXY | DIRECT | `DOMAIN-SUFFIX,azureedge.net,DIRECT` |
| Shared/scope: random-openaicom-api.example | PROXY | PROXY | UNRESOLVED | `FINAL,PROXY` |
| Shared/scope: cp4.cloudflare.com | PROXY | PROXY | PROXY | `DOMAIN,cp4.cloudflare.com,PROXY` |
| Shared/scope: ct.sendgrid.net | UNRESOLVED | UNRESOLVED | PROXY | `DOMAIN-SUFFIX,ct.sendgrid.net,PROXY` |
| Removed fixed AI IP: 24.199.123.28 | PROXY | PROXY | UNRESOLVED | `FINAL,PROXY` |
| Removed fixed AI IP: 64.23.132.171 | PROXY | PROXY | UNRESOLVED | `FINAL,PROXY` |
| Removed cloud ASN: 14061 | PROXY | PROXY | UNRESOLVED | `FINAL,PROXY` |
| Removed cloud ASN: 20473 | PROXY | PROXY | UNRESOLVED | `FINAL,PROXY` |

## 有意收窄的共享服务

- 删除 AI 集合对 auth0.com、stripe.com、sentry.io、algolia.net、launchdarkly.com、identrust.com、observeit.net、segment.io 整个父域的强制代理。部分域名仍命中通用 Proxy，这是保留通用库的结果。
- 保留官方要求的 intercom.io、intercomcdn.com 范围以及 ct.sendgrid.net；Stripe、Sentry、WorkOS、Cloudflare 等使用列明端点。
- Arkose、Statsig、Featuregates 保留为独立兼容规则。humb.apple.com 是共享 Apple 端点，保留 B 的代理选择。
- Copilot 上游 51 条中保留 16 条微软/Bing 专属记录；复制的 OpenAI 规则、共享父域、泛关键词和云 ASN 不再从 Copilot 引入。
- 不再按 AS14061/AS20473 或两个旧固定 IP 认定 AI 流量，改用官方 voice JSON 的 23 条当前网络前缀。
- cp4.cloudflare.com 原先已经代理，本次是显式保障，不是修复漏分流。
- 保留 snssdk.com、bytedapm.com 国内分流；不新增 CapCut、Trae、REJECT 或 MITM。跳过 @ads 记录并不代表广告被拦截，父域仍可能覆盖它们。

## 冲突基线

登记 42 个现有同选择器异策略组合，保留先匹配规则。例如 Apple/Microsoft 优先直连、TikTok 优先代理。不是将所有冲突自动放行；新增冲突或优先来源变化将阻断发布。域名父子覆盖通过关键回归案例检查，不声称穷尽所有可能流量。

## 可复核输入

- blackmatrix7/ios_rule_script: `5c22b5056f6288c27935489f74f8681fadbdba87`
- v2fly/domain-list-community: `bcea25493ed28c387660fe49ce1ceb242d2efca0`
- ACL4SSR/ACL4SSR: `bd00af4d515306cb24b951d51c4a7984f3fbe321`

完整逐用例结果和原文件 SHA-256 见同目录 initial_comparison.json。手机编译、登录、上传、语音、实际 UDP 支持和后台自动刷新尚待实机验收。
