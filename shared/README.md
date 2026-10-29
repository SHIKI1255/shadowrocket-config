# SHIKI1255-Rules 公共维护入口

这里是 Shadowrocket、Quantumult X、Clash 的唯一公共规则源。三个仓库及订阅地址不变；手机不增加分类订阅。

## 修改入口

- `custom.list`：48 条公共业务域名规则及原因。10 条 DIRECT 在 38 条 PROXY 之前；不加入节点或凭据。
- `sources.yaml`：21 项上游来源、固定提交方式、过滤、异常阈值及平台源格式。Apple/Proxy/China 的域名补充仍下载，输出合并到主分类。
- `platforms.json`：本机保护、QX 本地保护范围、16 条 Clash 个人进程规则。改动本地保护需要用户同步模板/覆写；仅增减既有个人集合中的进程规则，刷新远程集合即可。
- `categories.json`：稳定英文 ID 与中文说明。ID 不含序号；显示名称不能改变执行顺序。
- `routing_cases.json`：同一输入按平台登记策略、实际命中及分类。过程、UA、正则/no-resolve 差异不可伪装等价。
- `baseline.json`：人工审核数量及精确冲突登记。禁止为通过构建自动更新。

DIRECT/PROXY 是公共策略意图。Clash PROXY 映射机场主选择组；手机使用原生策略，不创建同名额外选择组。
执行顺序：本机保护 → 个人直连 → 个人代理 → OpenAI/Claude/Gemini/语音/Copilot/GitHub/Telegram/Google/X/TikTok/Crypto → Steam 国内/国际 → Apple/Microsoft/BiliBili → 通用代理 → 国内 → 国内 IP → 代理兜底。

## 本轮对齐及去重

办公、远控和国内字节的 10 条域名直连统一三端；Copilot 四后缀、Apple Relay 六项、OpenAI 16 辅助端点、五条历史登录兼容、TikTok 四项统一三端。
client-api.arkoselabs.com 和 events.statsigapi.net 统一现有手机后缀范围，Clash 的原精确记录被替换；不扩大到 arkoselabs.com/statsigapi.net 父域。
历史兼容不是当前官方必需项。已删除的 checkout.mzxnyysm.com 例外不恢复。
Windows 进程 DIRECT 在服务域名代理之前：紫鸟访问 ChatGPT 仍可能直连；Codex 访问个人办公域名仍先直连。

STOVE 版 CZN（卡厄思梦境）使用 `ssr-stove-shield.exe` 直连，E7 保留 `EpicSeven.exe` 直连。CZN 名称依据[公开客户端运行记录](https://www.reddit.com/r/winehq/comments/1qejenw/unable_to_update_game_from_stove_launcher_when/)核对（2026-10-12）；尚未本机安装及联网验收。不扩展到 STOVE 启动器或辅助进程。该规则发布至 Clash `personal_direct.list`，不加入手机规则，也不改变 DNS 或本地覆写。

类型、值、策略、选项完全相同才去重，保留首次位置及所有来源。后缀覆盖仅形成 overlaps.json 审核报告。
GitHub/字节/办公前置项即使上游重复也不移除其优先意图；不同类型、no-resolve、不同策略不合并。
Clash 有效 provider 从 23 减为 20。apple_domains/proxy_domains/china_domains 三个旧 URL 继续返回兼容文件，新覆写不加载它们。
Clash provider ID 是 shiki_<分类ID>；保留原规则下载地址和 ruleset/personal-clash 缓存路径。

## 数据协议与自动更新

公共发布位于本仓库 release 分支的 shared/manifest.json 与 shared/bundle.json，协议 schema_version=1。
shared_version 由规则数据及公共源码哈希生成；manifest 包含 bundle 哈希、来源提交、源码哈希及 provenance 哈希。
两消费者先解析公共 release 的提交 SHA，再全部使用固定 SHA 下载并校验，只解析 JSON/原生资源，不执行公共仓库脚本。
消费者各自版本清单带 shared_version；本地测试的 shared_release=local 不能发布。

北京时间公共/Shadowrocket 每日 06:17；QX 每日 06:47/18:47、Clash 每日 07:17/19:17 检查公共版本。各自 main 修改、手动触发仍完整验证。共同规则提交后，消费者在下一次检查跟进，必要时手动运行其 Actions。
三仓库及客户端资源刷新不是原子操作。GitHub 延迟时可能暂不同步；未知协议、下载/哈希/数量/冲突/回归失败都保留旧发布。
各仓库使用自己的 GITHUB_TOKEN。沿用 30 天真实维护记录；不增加机场订阅 Secret、节点自动抓取或测速选 IP。

## DNS、节点及平台边界

平台基础模板留在各自仓库。DNS 服务商与路径、IPv6/UDP、重写和节点参数不因统一而修改。
Clash 的 DNS/Fake-IP/嗅探域名选择从同一远程集合取域名，语音 IP 不作为 DNS selector；classical 只匹配其中域名的日志为预期提示。
本机保护、系统网络认证、进程/UA、QX Host 优先级及 Azure 原生正则/审核通配转换按平台保留。
Shadowrocket Hosts 模块独立人工核对；Clash Hosts 跟随机场；QX 不增加机场 Hosts。
Clash 局域网共享由界面管理，默认关闭、另一台电脑按需开启；不恢复自动备份。

## 验证和回退

在 shadowrocket-config 根目录运行 Python 单元测试，然后联网构建，再 --replay 离线重建。输出目录必须在源码外。
公共源先验证三端用例、官方语音 IP、跨客户端上游公共覆盖、数量和冲突；消费者再检查模板、缓存与原生内核。
--audit 只生成不可发布候选，审阅后才能有理由地修改 baseline.json；既有数量基线不自动刷新。
审核过的跨数量门槛更新可在对应来源登记 reviewed_transition：固定旧、新数量及完整有序规则列表的哈希，仅接受该次精确转换；其他来源及后续更新仍执行原有数量、增删和冲突门槛。不得用泛化允许项代替审核。参见 [2026-10-22 Claude 审核记录](../docs/claude-source-review-20261022.md)。
2026-10-26 官方 voice 清单由 23 个单地址扩充至 150 个，按精确过渡登记；普通上游变化、验证及回退见 [voice 审核记录](../docs/voice-source-review-20261026.md)。
2026-10-27 仅兼容审核过的 `@ads @telemetry` 组合；未知属性继续停止发布。OpenAI 转换不变，Gemini 三个精确域名单独审核，见 [属性兼容与上游审核记录](../docs/v2fly-telemetry-review-20261027.md)。
2026-10-28 官方 voice 清单由 150 个单地址扩充至 402 个，仅登记完整有序状态的精确迁移；Clash Apple 的两条顺序变化单独记录，见 [voice 与 Apple 审核记录](../docs/voice-source-review-20261028.md)。
消费者可用 --shared-dir 指定本地候选数据进行测试；不能把这种候选直接发布。

固定旧文件链接将 release 替换为完整提交 SHA。手机和 Clash 多资源若要完全冻结，必须同时冻结其内部资源/更新地址，不能只冻结入口文件。
新结构 --replay-commit 回放当前结构的历史发布；迁移前发布须使用对应旧源码重建，或直接导入原固定版本/本地备份。
回退公共规则应在 main 恢复对应源码并正常验证发布，再手动运行消费者。只回退某个客户端可能暂时失去三端一致性，须检查 shared_version。

公开仓库不含节点、机场订阅链接、密码或本地完整备份。静态/CI/原生语法通过不等同于手机实际联网和自动刷新通过。
