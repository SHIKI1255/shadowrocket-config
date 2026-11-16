# 上游数量变化待人工确认

此 PR 只保存审核材料，不修改生效基线。不要把合并本报告当成放行。

确认新增、删除、顺序和覆盖范围合理后，另行应用 proposed-baseline.json 中对应的精确迁移，保留现有阈值，补充测试，再通过完整联网构建、离线复现和发布检查。

运行及完整公开源快照（public-evidence，保留 7 天）：https://github.com/SHIKI1255/shadowrocket-config/actions/runs/43790681758

原始源码：54a85b0b869a52a90907938e530c8ec3a51cc6d0
前一发布：2f93bd7a73f5d1cc70284b4d796dd9f66f3f9cf0

若源码、上游或前一发布已变化，必须重新生成审核，不能套用旧候选。
