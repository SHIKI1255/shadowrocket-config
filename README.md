# SHIKI1255-Rules | Shadowrocket

公共域名、上游来源、分类、顺序和回归用例集中在[唯一维护入口](https://github.com/SHIKI1255/shadowrocket-config/tree/main/shared)。本仓库保留客户端模板和发布；不要另建一份个人规则清单。

导入[完整配置](https://raw.githubusercontent.com/SHIKI1255/shadowrocket-config/release/shadowrocket.conf)，节点订阅仍由你维护。[可选 GeekSpeed Hosts 模块](modules/README.md)独立管理，地址和内容本轮不变。

DNS/IPv6/UDP/重写保持原设置；业务规则完整展开在配置内，不增加多个远程分流订阅。

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
