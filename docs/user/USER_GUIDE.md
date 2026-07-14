# Octopus 1.0 用户指南

## 产品边界

Octopus 读取你选择的 Raw Repository，在完全分离的 Index Repository 中生成可搜索 Markdown 索引。正常更新、搜索、诊断、迁移和 Plugin 工作流都不得写 Raw。Windows 11 x64 是 1.0 正式支持平台；不需要 API Key 即可安装、索引、校验和本地搜索。

## 首次使用

1. 安装后启动 Octopus，选择“添加”。
2. 选择现有资料目录作为 Raw；选择一个空的、与 Raw 不同且不互相嵌套的 Index 目录。
3. 保持 AI 关闭完成首次构建。状态中心显示待稳定、失败/重试、孤立节点和最近运行结果。
4. 在“搜索”输入中英文关键词，查看推荐原因、证据、风险和推荐打开目标。

测试产品时可使用首次运行向导的确定性六格式样例。生产资料不要放进 Index，Index 也不要放进 Raw。

## 日常工作流

- “更新索引”扫描新增、修改、移动和删除；事务在 Manifest 最后提交，失败时保留上一个承诺状态。
- “重试失败项”只处理重试队列；Office 文件仍在编辑时会等待稳定，不强读 `~$` 锁目标。
- “校验”检查 Manifest、Markdown、路径引用和搜索缓存；“修复搜索缓存”只重建派生 SQLite，不改 Raw。
- 搜索默认完全本地。允许 AI 增强后，缺少 Key、网络或有效证据时自动降级到本地结果。
- F5 刷新，Ctrl+F 聚焦搜索，Ctrl+N 添加仓库。

## 备份与恢复

Raw 按你的既有备份策略管理。Index 可以重建，但若其中包含用户手写保护区，也应作为普通本地资料备份。不要只复制更新中的 `.octopus/transactions` 子目录；应在更新停止后复制整个 Index。

遇到问题时依次使用：

```powershell
octopus validate --format json
octopus report --last --format markdown
octopus diagnostics create --repository MyRepository --output .\octopus-diagnostics.zip
```

迁移默认 dry-run，显式应用和回滚见[本地诊断与迁移恢复](DIAGNOSTICS_AND_RECOVERY.md)。紧急产品回退见[紧急回滚手册](../support/EMERGENCY_ROLLBACK.md)。

## Plugin

1.0 随附 Package 和 Timeline 参考 Plugin。权限必须显式授予；Package 只复制本次调用明确确认的节点。Plugin Worker 不是恶意代码操作系统沙箱，只运行内置或你已审阅的本地 Plugin，不要执行未知下载代码。

## 隐私与支持

运行报告和诊断默认留在本机。诊断包不包含路径、文件名、查询、内容、仓库/节点 ID、错误消息或凭据；准备手工分享副本仍需要 `--consent`，命令不会自动上传。正式边界、严重度和报告方式见[支持政策](../support/SUPPORT_POLICY.md)。
