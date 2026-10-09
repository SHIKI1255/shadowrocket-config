# Shadowrocket 配置

[![构建与发布](https://github.com/SHIKI1255/shadowrocket-config/actions/workflows/update.yml/badge.svg)](https://github.com/SHIKI1255/shadowrocket-config/actions/workflows/update.yml)

个人 Shadowrocket 分流配置：国内优先直连，通用代理保持原有顺序，AI 与 TikTok 定向维护。
**公开仓库只保存分流规则与设置，不包含代理节点、机场订阅、账号或密钥。**

## 导入链接

```text
https://raw.githubusercontent.com/SHIKI1255/shadowrocket-config/release/shadowrocket.conf
```

在 Shadowrocket 的「配置」页面按 URL 添加远程配置，下载后选择使用；首页全局路由选择「配置」，PROXY 跟随首页所选节点。
这是配置地址，需在配置页面导入。请保留原配置，先手动更新并编译一次，再启用配置的自动后台更新（建议每天）及 iOS 的后台 App 刷新。
手机上的设置名称和后台执行时机依 Shadowrocket / iOS 版本而定；服务器定时发布与手机主动拉取是两个环节，不能保证同一时刻完成。
远程更新会覆盖手机上对该配置的本地修改，长期规则请改下面三个源码文件。

## 可选 Hosts 模块

[GeekSpeed Hosts 模块及安装、更新、回退说明](modules/README.md)独立维护服务商 Clash 订阅的完整 16 条 Hosts，当前有三条被节点直接使用。
模块需在 Shadowrocket 的模块管理中单独安装，不会随上面的主配置自动导入；原机场节点订阅继续使用。

```text
https://raw.githubusercontent.com/SHIKI1255/shadowrocket-config/main/modules/GeekSpeed-Hosts.sgmodule
```

模块直接从 `main/modules/` 分发，不进入每日生成的 `release` 配置。需要更新时让 Codex 在本机拉取、核对并发布，手机再手动刷新模块；不增加同步脚本、定时任务或 GitHub 订阅 Secret。
已有的 BGP 手动 Hosts 验证不等于独立模块已通过实机验收；详见模块说明中的验收状态。

## 日常维护只看这三处

| 文件 | 维护内容 |
| --- | --- |
| [config/base.conf](config/base.conf) | General、DNS、IPv6、Host、URL Rewrite；保留 `{{RULES}}` 占位符 |
| [rules/custom.list](rules/custom.list) | 局域网、基础服务、关键端点及兼容规则，优先级最高 |
| [config/sources.yaml](config/sources.yaml) | 来源、相对顺序、过滤条件和版本固定 |

`sources.yaml` 使用 **JSON 形式的 YAML 1.2**，因此只有 Python 标准库也能解析。编辑时保留双引号和逗号，不要混入普通 YAML 缩进语法。
`ref: "master"` 表示每天检查上游最新提交；构建开始时只解析一次提交号，后续所有文件都从固定 SHA 下载。
把 `ref` 换成 40 位提交号可以冻结该来源。`release/sources.lock.json` 记录每次实际使用的版本和哈希。

修改 main 后自动构建；通过检查才更新 release。也可在 Actions 页面手动运行。
定时计划为**每天北京时间 06:17**（UTC 22:17）；GitHub 调度可能延迟。
公开仓库长时间无活动可能被 GitHub 停用定时工作流，需在 Actions 页面重新启用，详见 [GitHub 调度说明](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)。

## 来源与分流顺序

1. 本机、局域网、基础服务、Apple Intelligence/Copilot/登录兼容端点。
2. v2fly OpenAI、Anthropic、Google DeepMind；OpenAI 官方语音 IP；经过白名单收窄的 blackmatrix7 Copilot。
3. 原有 blackmatrix7 GitHub、Telegram、Google、Twitter；v2fly TikTok。
4. blackmatrix7 Crypto、SteamCN、Steam、Apple、Microsoft、BiliBili、Proxy、China，最后 GEOIP CN 与 FINAL。

Apple / Proxy / China 的普通 `.list` 和 `_Domain.list` 一并保留并展开；只删除完全相同的规则，不按父域压缩，也不重排。
v2fly 的 `@ads` 记录不额外引入；不增加广告拦截。TikTok 的 `bytedapm.com` 被排除，和 `snssdk.com` 保持原 B 配置的国内分流。
Copilot 白名单保留当前 16 条微软/Bing 专属规则；新专属条目需要人工确认后加入，不自动采纳共享父域和云 ASN。
OpenAI 唯一已知正则使用原配置的明确 Azure 通配符承接；其他正则、include 或未知标签均停止构建。

2026-10-09 已删除用途未确认的 `checkout.mzxnyysm.com` 个人直连例外；该域名及子域名恢复正常分流，不新增拒绝或强制代理规则。历史比较报告保留当时的记录，不代表现行规则。
`humb.apple.com` 是同时被 OpenAI 文档列出的共享 Apple 端点，保留原 B 配置的代理选择。
`cp4.cloudflare.com` 原本已有通用代理覆盖，本次仅显式保障。
不新增 CapCut、Trae、MITM、证书或脚本。

## 发布保护与验收范围

- 下载失败、未知格式、关键分流回归、新增未登记策略冲突均停止发布。
- 每个来源的原始和保留数量都对照人工审阅基线：减少超过 20% 或增加超过 50% 阻断；对上一版额外检查同数量但大批替换的情况。
- main 保存源码和验证基线；release 保存完整配置、原始输入快照、版本、哈希和许可证。配置不再包含远程 RULE-SET / DOMAIN-SET。
- 先通过所有检查和离线重建一致性验证，再用一次 Git 引用更新发布。失败时上一个 release 不变；并发更新拒绝强制覆盖。
- 自动化是静态规则校验，不是 Shadowrocket 原生编译器。首版检查覆盖 84 个分流案例和 23 个语音 IP；手机编译、实际登录/上传/语音及后台更新仍需用户设备验收。

General 和 Host 保留原值（仅新增 update-url）；`fallback-dns-server = system` 等设置以兼容为先，不承诺严格无 DNS 泄漏。
默认 `dns-server` 使用经代理的 Cloudflare/Google；`direct-dns-server` 使用 AliDNS/DoH.pub；`proxy-dns-server` 专门解析节点入口域名，同样使用 AliDNS/DoH.pub，并非“代理网站专用 DNS”。局域网和网络认证域名继续指定系统解析，参见 [Shadowrocket 更新说明](https://t.me/s/shadowrocketnews?after=931)。Hosts 模块的节点入口效果须独立实测，不据此改变其他本地解析开关。
语音优先使用 UDP 3478，节点需要支持相应传输；配置不能补足节点能力，参见 [OpenAI 官方网络要求](https://help.openai.com/en/articles/9247338-network-recommendations-for-chatgpt-errors-on-web-and-apps)。

2026-10-09 复核后保留 61 条手写规则。GitHub 下载保障、`byteoversea.com`、`ibytedtos.com` 刻意前置；相同的上游规则在生成时去重，不删除这些优先匹配保障。五条 Arkose/Statsig/Featuregates 记录是历史登录兼容，当前必要性未实机确认，不标为官方必需项。OpenAI 官方清单的 30 个域名模式及 `ws.chatgpt.com` 已有明确覆盖。上游来源、过滤与顺序保持原样，不新增共享 CDN 或历史 Apple 域名排除项。

本次 98 个分流用例中，53 个同时核对实际命中规则；原有 42 项已登记冲突不变。同一上游快照下合并规则从 14,153 条减为 14,152 条。回退可使用[变更前完整配置](https://raw.githubusercontent.com/SHIKI1255/shadowrocket-config/8ac38a96d80e76af9e4accf4693345b887866757/shadowrocket.conf)，但其内置更新地址仍指向滚动 release；临时回退期间暂停该配置的自动更新，避免下次刷新覆盖旧版。Hosts 模块单独回退，详见模块说明。

## 说明与复核

- [维护、检查和回退](docs/maintenance.md)
- [原 A / B → 优化版差异](docs/initial_comparison.md)
- [完整 108 项比较数据](docs/initial_comparison.json)
- [第三方归属及许可](docs/third_party.md)

生成器仅用 Python 3.12 标准库，无需 pip 安装。Windows 本地使用 PowerShell 7；治理目录和产物管理不参与 GitHub 运行。

## 30 天维护记录

工作流现在在验证和发布结束后检查 `.github/maintenance.json`，首次初始化，之后每满 30 天提交一次真实维护记录。记录包含本次验证/发布结果、运行链接和当前 release 提交，规则未变时不会改动订阅内容。失败时可记录失败但不能发布无效配置。

使用仓库 GITHUB_TOKEN，仅受信任的 main 分支任务可写入；并发修改时跳过旧任务，维护提交不会循环触发构建。若定时工作流已被 GitHub 停用，需在 Actions 启用后手动运行；保活不保证 GitHub 调度永不延迟。现有分流、DNS 和订阅链接保持原样。
