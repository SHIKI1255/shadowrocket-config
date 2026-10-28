# SHIKI1255-Rules | Shadowrocket

公共域名、上游来源、分类、顺序和回归用例集中在[唯一维护入口](https://github.com/SHIKI1255/shadowrocket-config/tree/main/shared)。本仓库保留客户端模板和发布；不要另建一份个人规则清单。

导入[完整配置](https://raw.githubusercontent.com/SHIKI1255/shadowrocket-config/release/shadowrocket.conf)，节点订阅仍由你维护。[可选 GeekSpeed Hosts 模块](modules/README.md)独立管理，地址和内容本轮不变。

DNS/IPv6/UDP 回退/重写保持原设置；业务规则完整展开在配置内，不增加多个远程分流订阅。

## 家庭局域网节点

配置内提供一个可选节点 `Home-LAN`（家庭局域网）：SOCKS5，`192.168.5.4:7897`，无认证、无 TLS、关闭 TCP Fast Open，开启节点 UDP 转发（`udp-relay=true`）。该私网地址不包含机场凭据，是公开模板唯一允许的节点例外。

先备份手机配置，再更新上面的完整配置并使用“配置”分流模式。在家时手动选择此节点，离家或电脑关闭后切回机场节点；不自动选择、切换或回退。手机原本直连的请求仍直接访问，代理请求交给电脑，由电脑 Clash 再次分流。不要把它设置成机场节点的前置代理。

用户已确认刷新原版配置后节点会出现在首页；若手机版本未把 `[Proxy]` 中的节点加入首页，按上述地址、端口和选项手动添加一次。已有旧名“家庭局域网”或同名节点时核对并编辑原节点，避免重复；更新后选择 `Home-LAN` 并确认 UDP 转发为开启。主配置里的节点声明不替代机场订阅，也不影响独立 Hosts 模块。

家庭电脑须开启“允许局域网连接”、运行 Clash 并保持地址固定；手机需能访问该电脑的 7897 端口，并允许应用访问本地网络。只在受信任的家庭局域网使用，不设置公网端口转发。无需更改电脑 TUN、系统代理或 DNS 监听。

节点 UDP 转发已开启，原有 UDP 不支持时拒绝策略不变。开启选项不等于 UDP 端到端已验证；共享入口、防火墙及电脑实际出口都需支持相应流量。网页、语音、游戏及切回机场节点需实机验收，UDP 异常时可切回已验证的机场节点。关闭电脑或切换蜂窝网络后，本节点预期不可用；不要把不可达当作规则更新失败。

回退可直接选回机场节点，或恢复更新前的[固定版完整配置](https://raw.githubusercontent.com/SHIKI1255/shadowrocket-config/a2f37b7442c7a7dc3e0eedddcdcf40d2e536bdd3/shadowrocket.conf)（节点名“家庭局域网”、UDP 转发关闭）。首页若保留了手动节点，需同步恢复该节点参数。

## 维护与更新

公共修改只在 https://github.com/SHIKI1255/shadowrocket-config/tree/main/shared 进行。平台 DNS/基础模板在本仓库 config 中；config/shared.json 是协议/分类结构契约，结构变化需要审阅后同步本地模板。

GitHub 每日北京时间 06:17、main 修改及手动触发构建。消费者固定公共发布 SHA 并校验数据，只读取资源，不执行远程代码。[当前发布清单](https://raw.githubusercontent.com/SHIKI1255/shadowrocket-config/release/manifest.json)中的 shared_version 用于核对三端版本。沿用 30 天维护记录；不承诺构建后手机立即刷新。

下载失败、未知语法/协议、异常增删、未知冲突或用例失败均保留上一版。首次无缓存失败不算安装成功；Clash 必须核对缓存加载。

普通域名规则自动刷新；主配置、资源结构、DNS 架构及适配脚本变化需手动同步。机场订阅和 Hosts 模块仍独立维护，不把私有订阅放到 GitHub。

## 验证与回退

Python 3.12 标准库，Windows 使用 PowerShell 7；Clash 还运行 Node 适配和隔离 Mihomo 检查。

```powershell
python -B -m unittest discover -s tests -v
python -B scripts/build.py --output <新的仓库外目录> --previous-release
python -B scripts/build.py --replay <构建目录> --output <新的离线目录>
```

两次 SHA256SUMS 必须一致。固定版本将 release 替换为发布 SHA；完全冻结还需冻结内部资源地址。恢复本地备份由使用者操作，不自动重载应用。手机解析、实际联网、语音与后台刷新另行验收。

[公共规则与必要平台差异](https://github.com/SHIKI1255/shadowrocket-config/tree/main/shared) · [第三方许可](docs/third_party.md)

有限重试、失败快照、人工审核候选和通知去重见[自动发布异常处理](docs/release-resilience.md)。
