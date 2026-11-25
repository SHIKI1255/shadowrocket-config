# 第三方来源与许可证

生成配置是经过修改的集合：按服务排序、递归展开并转换 `v2fly`、组合 `MetaCubeX` GFW 域名集、合并官方 IP 网段、保留个人例外、精确去重。不是上游官方发行版。

| 来源 | 使用范围 | 许可/权利说明 |
| --- | --- | --- |
| [v2fly/domain-list-community](https://github.com/v2fly/domain-list-community) | AI、GitHub、Telegram 域名、Google、X、TikTok、Crypto、Steam、Apple、Microsoft、BiliBili、高频海外补充及国内直连 `cn` | MIT，完整版权和许可随 release 的 `licenses/v2fly.txt` 提供 |
| [MetaCubeX/meta-rules-dat](https://github.com/MetaCubeX/meta-rules-dat) | `meta` 分支 `geo/geosite/gfw.list` 通用代理域名集 | GPL-3.0，完整原文随 release 的 `licenses/metacubex.txt` 提供 |
| [OpenAI voice IP](https://openai.com/chatgpt-voice.json) | 官方语音 IP 数据 | 公开技术数据，未发现独立开源许可证；不宣称 OpenAI 授予了 GPL/MIT 许可 |
| [Telegram CIDR](https://core.telegram.org/resources/cidr.txt) | 官方 Telegram IPv4/IPv6 网段数据 | 公开技术数据，未发现独立开源许可证；不宣称 Telegram 授予了 GPL/MIT 许可 |
| [OpenAI 网络要求](https://help.openai.com/en/articles/9247338-network-recommendations-for-chatgpt-errors-on-web-and-apps) | 少量辅助域名事实 | 人工维护的技术事实和引用，不复制文档正文 |
| [Apple 企业网络资料](https://support.apple.com/en-us/101555) | Apple Intelligence 域名事实 | 人工维护的技术事实和引用，不复制文档正文 |
| [ACL4SSR](https://github.com/ACL4SSR/ACL4SSR/blob/master/Clash/Ruleset/TikTok.list) | 从原配置保留 4 条兼容规则 | 致谢历史来源，无运行时下载依赖；不代表采用其完整规则库 |

本项目原创生成器、规则编排及文档按 `GPL-3.0-or-later` 提供。第三方代码/规则保留原许可。
release 的 `sources.lock.json` 记录上游完整提交号、固定地址及 SHA-256；`snapshots/` 保存确切输入，可用 `main` 的 `scripts/build.py --replay` 离线重建。不得把整个集合重新标成无条件 MIT。

上游项目名字仅用于归属，不表示任何服务商认可该分流配置。官方网络允许列表也不等同于保证代理可用。
