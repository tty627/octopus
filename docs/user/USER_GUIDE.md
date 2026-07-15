# Octopus 1.1 开发版用户指南

## 产品边界

Octopus 读取你选择的资料空间，在完全分离的 Index Repository 中生成可搜索 Markdown 索引。
正常更新、搜索、任务包、诊断、迁移和 Plugin 工作流都不得写 Raw。Windows 11 x64 是正式
支持平台；不需要 API Key 即可安装、索引、校验、本地搜索和建立任务包。

## 首次使用

1. 安装后启动 Octopus。没有资料空间时会直接进入三步向导。
2. 选择现有资料文件夹；Octopus 自动建议独立索引位置，并明确显示不会修改原文件。
3. 查看文件数、格式、空间、阻断项和提醒后建立资料空间；也可以选择“使用示例资料”。
4. 首批结果可用后进入工作台，后台索引继续完成，不必等待整个目录处理结束。
5. 在工作台输入文件名、内容线索或任务描述，进入搜索与证据检查流程。

测试产品时可使用首次运行向导的确定性六格式样例。生产资料不要放进 Index，Index 也不要放进 Raw。

## 日常工作流

- 搜索始终先返回本地结果；启用“AI 任务辅助”后，AI 只补充排序和解释，失败时保留本地结果。
- 在“设置 > AI 服务”中可以为当前资料空间填写服务商、Base URL、模型和 API Key；密钥保存在 Windows 凭据管理器中，不写入 Index 配置。
- 单击结果只更新右侧证据检查器；点击“加入任务包”或按 `Ctrl+Enter` 才确认加入。
- 证据检查器显示命中原因、页/节/Sheet 等锚点、路径、修改时间、抽取质量和打开来源动作。
- 任务包默认包含“核心资料、补充资料、待核验”三个槽位，可改名、增删、拖动，并提供键盘上移/下移。
- 草稿在 800ms 后自动保存；服务断开时暂存本地草稿，恢复后进行 revision 校验。
- Markdown 导出保存可作为 Markmap 输入的大纲；Package 只复制本次再次勾选的已确认资料。
- “资料空间”页提供同步、失败重试、校验和搜索修复；正常状态不会持续打扰。
- `Ctrl+F` 聚焦搜索，`Ctrl+N` 打开任务包，`Ctrl+Enter` 加入当前证据，`Esc` 关闭证据抽屉。

## 备份与恢复

Raw 按你的既有备份策略管理。Index 可以重建，但若其中包含用户手写保护区，也应作为普通本地资料备份。不要只复制更新中的 `.octopus/transactions` 子目录；应在更新停止后复制整个 Index。

遇到问题时依次使用：

```powershell
octopus validate --format json
octopus report --last --format markdown
octopus diagnostics create --repository MyRepository --output .\octopus-diagnostics.zip
```

迁移默认 dry-run，显式应用和回滚见[本地诊断与迁移恢复](DIAGNOSTICS_AND_RECOVERY.md)。紧急产品回退见[紧急回滚手册](../support/EMERGENCY_ROLLBACK.md)。

## 任务包与 Plugin

1.1 的任务包保存在 `<Index>/.octopus/task-packs/`，默认只是来源引用，不复制原件。Package
导出复用内置 Plugin，权限必须显式授予，并且只复制导出对话框中再次确认的节点。Plugin
Worker 不是恶意代码操作系统沙箱，只运行内置或你已审阅的本地 Plugin。

## 隐私与支持

运行报告和诊断默认留在本机。诊断包不包含路径、文件名、查询、内容、仓库/节点 ID、错误消息或凭据；准备手工分享副本仍需要 `--consent`，命令不会自动上传。正式边界、严重度和报告方式见[支持政策](../support/SUPPORT_POLICY.md)。
