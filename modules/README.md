# 本地私有 Hosts 模块说明

本公开仓库**不再发布任何服务商入口 Hosts 模块文件或公开导入地址**，避免在公网暴露私有订阅下发的入口 IP 映射。公共分流规则与本仓库 `release` 分支保持完全独立。

## 当前方案：本机直写 iCloud Drive（零公网暴露）

* **运行位置**：仅在使用者受控的 macOS 本机运行 [sync_hosts.py](../scripts/sync_hosts.py)，读取仓外受保护目录中的私有 `--config` 与本机订阅索引；订阅链接、token、节点内容与服务商入口 IP 全程仅留在内存或个人 iCloud Drive，绝不提交到本仓库。
* **同步机制**：
  * 严格校验 `PyYAML==6.0.3`（拒绝 YAML 别名、锚点、标签、重复键）、精确 HTTPS 来源、禁止重定向、仅接受单值公网 IP；
  * 变化时等待 10 秒二次核对一致并通过数量门禁（不得减半或翻倍）；
  * 生成不含 `#!url` 的纯本地 `.sgmodule`，备份旧版至仓外 `outputs/` 目录后，以临时文件 + `fsync` + `os.replace` 原子写入 macOS iCloud Drive 的 Shadowrocket `Modules/` 目录并读回核对 SHA256；
  * 无变化时不改写文件，拒绝写入符号链接或未下载完成的 `.icloud` 占位文件。
* **命令行用法**（`<config.json>` 与 `<state-dir>` 均须位于仓库外）：

```zsh
# 首次初始化（目标尚无本地模块时显式加 --init）
.venv/bin/python -B scripts/sync_hosts.py --sync --init --config <config.json> --state-dir <state-dir>

# 日常只读核对
.venv/bin/python -B scripts/sync_hosts.py --check --config <config.json> --state-dir <state-dir>

# 日常定时同步
.venv/bin/python -B scripts/sync_hosts.py --sync --config <config.json> --state-dir <state-dir>
```

* **iOS 侧注意事项**：在 Shadowrocket 中作为本地 iCloud 模块启用（不要绑定远程 URL，也不要在手机端手工编辑同一文件以免产生 iCloud 冲突副本）；更新后如连接未自动重载，断开并重连 VPN 即可生效。

## 备用方案方向：Private 仓库 + Cloudflare Worker（方案 2A）

若后续需要恢复 Shadowrocket 原生 HTTPS 远程 URL 刷新（且保持公网零暴露），可启用备用方向 **方案 2A**：

1. 另建独立 **Private GitHub 仓库** 专门托管私有 Hosts 模块及回滚历史；
2. 生成仅限该私有仓库、仅 `Contents: Read-only` 权限的 Fine-grained PAT；
3. 部署轻量 **Cloudflare Worker**，以加密 Secret 保存只读 PAT 与随机访问口令，校验口令后在服务端代拉私有仓库最新 `.sgmodule` 并以 `Cache-Control: no-store` 返回给 Shadowrocket。

无论采用何种方案，**不得将服务商入口域名、入口 IP 或私有订阅凭据重新提交到本公开仓库**。
