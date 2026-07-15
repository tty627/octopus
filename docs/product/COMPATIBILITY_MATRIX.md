# Octopus 2.0 兼容矩阵

本矩阵描述 `2.0.0.dev1` 的实际支持边界。V2 与 V1 在当前版本中并行存在，V2 是默认桌面产品，V1 仅用于回滚和旧 CLI 兼容。

## 平台

| 环境 | 状态 | 说明 |
| --- | --- | --- |
| Windows 11 x64、NTFS、本地每用户安装 | 支持 | 桌面、PDFium、OCR、页面预览和安装验证主环境 |
| OneDrive 或其他同步盘中的原始资料 | 有条件支持 | 目录必须在线且当前用户可读；短暂离线时同步可能失败，可恢复后重试 |
| 网络盘、FAT/exFAT、Windows 10 | 未承诺 | 可运行不等于已完成发布矩阵验证 |
| Windows ARM64 | 不支持 | 当前只发布 x64 工件 |
| macOS、Linux | 无桌面支持 | 核心模块可能可开发运行，但不属于当前产品发布范围 |

## 文件格式

| 类型 | 文件名/元数据 | 正文检索 | 页面证据 |
| --- | --- | --- | --- |
| PDF | 支持 | 支持，PDFium/PyPDF/本地 OCR | 支持认证 PNG 预览 |
| TXT、Markdown、CSV、JSON、代码等文本 | 支持 | 支持 | 文本片段 |
| Word、Excel、PowerPoint | 支持 | `dev1` 暂未接入 | 暂无 |
| PNG、JPEG 等图片 | 支持 | `dev1` 暂未接入 | 仅在后续视觉流程且明确授权后使用 |

未抽取正文的 Office 和图片显示“仅文件信息”，不应显示为处理失败。

## V2 契约

| 契约 | 当前版本 | 持久性 | 回退策略 |
| --- | --- | --- | --- |
| V2 资料空间/global config | `2.0` | 用户配置 | 保留 V1 repositories 字段，V2 workspaces 独立存储 |
| V2 SQLite workspace | `2.0` | 可重建缓存 | 删除后从原始资料重新同步 |
| V2 task | `2.0` | 不可丢失用户数据 | revision 冲突保护；来源变化后标记待重新确认 |
| Local API V2 | `2.0` | 当前桌面契约 | `/v2/contract` 可认证读取；只增加兼容字段 |
| Local API V1 | `1.0` | 一个版本周期的回滚契约 | 旧 CLI 和 V1 路由继续可用 |
| V1 Markdown/Leaf/FolderNode | 旧契约 | 只读迁移来源 | 不继续同步，不自动删除 |

## 数据位置

| 数据 | 默认位置 | 卸载保留 | 可重建 |
| --- | --- | --- | --- |
| 原始资料 | 用户选择的目录 | 是 | 否 |
| SQLite 与页面预览 | `%LOCALAPPDATA%\Octopus\workspaces` | 是 | 是 |
| 任务 | `%APPDATA%\Octopus\workspaces\<id>\tasks` | 是 | 否 |
| 全局配置和本地 token | `%APPDATA%\Octopus` | 是 | 部分 |
| V1 Index | 用户原有 `*-Octopus-Index` | 是 | V2 不修改 |

## 升级与回退

- `2.0.0.dev0 -> 2.0.0.dev1`：原地升级支持，任务和缓存位置不变；
- V1 资料空间：启动时记录为 V2 workspace，旧 Index 保留只读；
- V1 任务：按内容哈希、相对路径和页码迁移；无法确认的项目保留并标记来源待重新确认；
- 回退到 V1：旧 Index 未被 V2 修改，但 V2 新任务不会自动反向写入 V1；
- 新版本遇到更高 task schema 时拒绝写入，避免旧程序覆盖未来数据。

## 发布限制

`2.0.0.dev1` 是未签名开发预览。GitHub Actions 在 Windows/Python 3.12/Inno Setup 6.7.1 上构建并验证安装、卸载、重装、PDFium、OCR、页面预览和凭据读取。正式签名、Defender 干净机矩阵和 Office 深度解析仍是后续门禁。
