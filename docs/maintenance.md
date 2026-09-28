# 维护与回退

## 常见修改

个人网站直连/代理：在 `rules/custom.list` 加入 `DOMAIN,具体主机,DIRECT` 或 `DOMAIN-SUFFIX,域名,PROXY`。
DOMAIN 只匹配该主机；DOMAIN-SUFFIX 会包含子域名。具体例外放在同文件的宽泛规则之前。
不要把登录服务的整个云厂商、ASN 或公共服务父域加入 AI 规则。
保存后在 `tests/routing_cases.json` 加入实际要保证的域名用例，避免后续上游覆盖。

DNS/IPv6 等设置在 `config/base.conf` 修改。来源顺序、过滤或固定版本在 `config/sources.yaml` 修改。
不要直接修改 release 分支中的生成配置；下一次发布会覆盖它。
新来源或已确认的来源数量大变化，需要一起人工审阅验证基线，不自动把失败视为可接受。

## 本地构建

在源码目录、PowerShell 7、现有 Python 3.12 下执行：

```powershell
python -B -m unittest discover -s tests -v
# 使用唯一 Id；产物进入本项目 workbench 分区，目录已有内容时构建会拒绝覆盖。
./artifact.ps1 -Action New -Kind outputs -Id build_yyyymmdd_nn
python -B scripts/build.py --output D:/CodexProjects/codex_workbench/projects/shadowrocket-config/outputs/build_yyyymmdd_nn/release
python -B scripts/build.py --replay D:/CodexProjects/codex_workbench/projects/shadowrocket-config/outputs/build_yyyymmdd_nn/release --output D:/CodexProjects/codex_workbench/projects/shadowrocket-config/outputs/build_yyyymmdd_nn/replay
```

两次 `SHA256SUMS` 应完全一致。非 Windows 或未接入本地治理的机器直接传入一个仓库外的空输出目录即可。
构建本身不需要 GitHub 凭据；本地匿名 API 受 GitHub 频率限制。Actions 使用仓库自带的 GITHUB_TOKEN，不能把令牌写进配置。
源码 edits 使用正常文本编辑；提交只暂存本次涉及文件。

## 更新被阻止时

先查看 Actions 中的 `BUILD STOPPED` / `PUBLISH STOPPED`。旧 release 不会被替换。

| 原因 | 处理 |
| --- | --- |
| 下载失败、HTTP 错误 | 检查上游可达性，恢复后手动运行；不以空内容替代 |
| 规则数量骤变或大量替换 | 核对上游差异和来源文件是否改版；必要时暂时固定旧提交 |
| 未知语法/标签/正则/include | 理解语义后补充精确转换和对应测试，不扩大匹配凑数 |
| 新策略冲突 | 确认优先级及影响；更新精确冲突登记和关键用例 |
| Routing regression | 查看失败域名对应首个规则，修复顺序/过滤，不改预期值掩盖问题 |
| Stale build / 并发更新 | 让最新 main 重新运行；发布使用非强制快进 |

需要更新基线时使用 `scripts/build.py --audit --output <仓库外新目录>`，只生成候选审计文件，不生成可发布配置。
逐项检查 `candidate_baseline.json` 的数量、策略及 winner_source；审阅 `source_stats.json` 中被过滤的记录。
确认确属有意变化后更新 `tests/source_baseline.json`，附上原因及测试，再正常构建。
42 条首版冲突登记覆盖同一选择器的异策略；域名父子包含、关键词、UA/IP 的全部可能交叉没有被穷尽，手机验收仍有必要。

## 冻结与回退

1. 在 release 的 Git 历史选定上一个已验证提交，记下完整 SHA；其 `sources.lock.json` 包含所有输入版本。
2. 手机急需恢复时，可导入 `https://raw.githubusercontent.com/SHIKI1255/shadowrocket-config/<release提交SHA>/shadowrocket.conf`。
   注意其中 update-url 仍指向滚动 release；要长期冻结，先关闭手机自动配置更新，或者在自己的冻结副本中移除 update-url。
3. 服务端恢复旧输入：在 Actions 的手动运行中，`replay_commit` 填入该 release 完整 SHA。
   同样经过当前语法、冲突、数量及分流检查；不能用回退绕开校验。当前源码与旧输入不兼容时，先恢复相应源码版本。
4. 防止下一次每日任务又升级：把 `sources.yaml` 两个仓库的 ref 固定为旧 lock 内的提交号；
   把 voice.url 暂改为该 release SHA 下的 `snapshots/voice.txt` 固定地址。修复后再恢复 master 和 OpenAI 官方 URL。
5. 回退也发布一个新的 release 提交，保留历史，不 force push、不删除旧版本。`--replay` 可完全离线复现归档输入。

回放和回退只重用来源字节，当前 base/custom/测试仍由 main 决定。完全复现需要同时使用 manifest 所对应的源码版本；
发布提交消息包含其 main SHA，manifest 保存影响生成内容的源码文件哈希。

## 手机验收清单（尚待实机执行）

1. 保留原 A/B 配置；下载新远程配置，手动更新并编译，确认没有语法报错。
2. 配置路由模式下检查 ChatGPT 登录、文件上传、语音（Wi-Fi/蜂窝，节点 UDP 能力）；Claude 内容上传、Gemini、Copilot 新入口。
3. 检查 Apple Intelligence 与普通 Apple 服务；国内网站、局域网、SteamCN、snssdk/bytedapm、个人直连例外。
4. 查看请求日志，确认实际命中策略；对失败服务区分规则、DNS、节点地区/UDP、账号限制。
5. 启用配置自动更新和 iOS 后台刷新；下一次服务器成功发布后，确认手机确实获取到新版本。

GitHub 定时发布可能延迟；手机后台刷新受系统调度约束。手动或 push 构建通过不等于已经观察到次日 schedule 事件。
如果 GitHub 因长时间无活动停用了定时任务，在 Actions 页面重新启用；不通过无意义提交伪造更新。

## 本机文件与边界

源码：`D:/CodexProjects/shadowrocket-config`。本次原件备份、首次构建、缓存和报告分别位于该项目 workbench 的 inputs / outputs / cache / reports 中。
原 Downloads 文件不改动。治理登记只新增本仓库一项；不提交其他治理仓库已有改动。
`artifact.ps1` 与 vendored `tools/governance/artifacts.psm1` 仅用于本机产物生命周期，不触发安装或环境切换。
