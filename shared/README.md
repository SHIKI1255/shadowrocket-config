# SHIKI1255-Rules 公共维护入口

这里是 Shadowrocket、Quantumult X、Clash 的唯一公共规则源。三个仓库及订阅地址不变；手机不增加分类订阅。本项目及三端仓库采用 `GPL-3.0-or-later` 许可。

## 修改入口

- `custom.list`：63 条公共业务域名规则及原因（10 条 DIRECT 在 53 条 PROXY 之前，含从停更外部源内化的微软/Bing Copilot 网关）；不加入节点或凭据。
- `sources.yaml`（`schema_version=2`）：全活跃上游来源（`v2fly/domain-list-community` tarball 递归展开、`MetaCubeX/meta-rules-dat` `geo/geosite/gfw.list`、OpenAI 官方语音 IP JSON、Telegram 官方 CIDR），分级动态门禁与平台正则转换。`telegram_ip` 合并入 `telegram`，`proxy_v2fly` 合并入 `proxy`。
- `platforms.json`：本机保护、QX 本地保护范围、16 条 Clash 个人 Windows `.exe` 进程规则。改动本地保护需要用户同步模板/覆写；仅增减既有个人集合中的进程规则，刷新远程集合即可。
- `categories.json`：稳定英文 ID 与中文说明（含新增 `xai`，移除独立 `copilot`）。ID 不含序号；显示名称不能改变执行顺序。
- `routing_cases.json`：同一输入按平台登记策略、实际命中及分类。过程、正则/no-resolve 差异不可伪装等价。
- `baseline.json`（`schema_version=2`）：分类优先级冲突白名单（`allowed_category_conflicts`）与最新基线统计；禁止为通过构建自动放宽未声明冲突。

DIRECT/PROXY 是公共策略意图。Clash PROXY 映射机场主选择组；手机使用原生策略，不创建同名额外选择组。
执行顺序：本机保护 → 个人直连 → 个人代理（含 Copilot）→ OpenAI / Claude / Gemini / xAI / 语音 / GitHub / Telegram / Google / X / TikTok / Crypto → Steam 国内 / 国际 → Apple / Microsoft / BiliBili → 通用代理（GFW + 高频海外）→ 国内（`v2fly/cn`）→ 国内 IP → 代理兜底。

## v2.0 上游架构与门禁升级

- **彻底告别已停更的 `blackmatrix7/ios_rule_script`**：全部服务域名与国内直连切换至每日更新的 `v2fly/domain-list-community`（单次 SHA 锁定 tarball 归档可达子文件，离线可复现）；Telegram IP 切换至官方 `core.telegram.org/resources/cidr.txt`（IPv4 + IPv6）；通用代理层采用 `MetaCubeX/meta-rules-dat` `geo/geosite/gfw.list` 叠加精选 `v2fly` 高频海外分类。
- **零 `USER-AGENT` 跨端偏差**：去除 HTTPS 无 MITM 下已失效的旧 `USER-AGENT` 规则，Quantumult X 的 `user_agent_priority_changes` 归零。
- **统一属性语义**：所有 `v2fly` 源统一识别 `@ads`、`@telemetry`、`@cn`、`@!cn`（先校验语法再跳过 `@ads`/`@telemetry`，未知新属性仍阻断发布）。
- **分级门禁**：
  - 静态绝对上下限（`min_rules` / `max_rules`）防止来源归零或异常暴涨；
  - 相对上一版平滑变动门禁：小源（`< 100` 条）按绝对容差 `±40` 放行且禁止 100% 整批替换；大源（`≥ 100` 条）执行 `0.75–1.60` 比例与增删比门禁；官方动态 IP 源（`voice`、`telegram_ip`）在严格 schema/公网/前缀长度校验下按合理条数区间放行；
  - 冲突检查按声明的分类优先级方向（如 `github > microsoft`、`apple/microsoft > proxy`）校验，未声明方向或反转冲突仍阻断发布。
- **Clash 精简与平滑兼容**：有效 provider 为 20 个（移除 `copilot`，新增 `xai`）；旧 URL `apple_domains.list`、`proxy_domains.list`、`china_domains.list`、`copilot.list` 继续返回空兼容占位文件，避免旧版 Clash 本地覆写刷新报 404，新覆写不加载它们。

## 数据协议与自动更新

公共发布位于本仓库 `release` 分支的 `shared/manifest.json` 与 `shared/bundle.json`，协议 `schema_version=1`。
`shared_version` 由规则数据及公共源码哈希生成；`manifest` 包含 `bundle` 哈希、来源提交、源码哈希及 `provenance` 哈希。
两消费者先解析公共 `release` 的提交 SHA，再全部使用固定 SHA 下载并校验，只解析 JSON/原生资源，不执行公共仓库脚本。

北京时间公共/Shadowrocket 每日 06:00、18:00；QX 与 Clash 每日 07:00、19:00 检查公共版本。各自 `main` 修改、手动触发仍完整验证。共同规则提交后，消费者在下一次检查跟进，必要时手动运行其 Actions。

## 验证和回退

在 `shadowrocket-config` 根目录运行 Python 单元测试，然后联网构建，再 `--replay` 离线重建。输出目录必须在源码外。
公开仓库不含节点、机场订阅链接、密码或本地完整备份。静态/CI/原生语法通过不等同于手机实际联网和自动刷新通过。
