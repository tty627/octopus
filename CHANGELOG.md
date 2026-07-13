# Changelog

本文档记录 Octopus 的用户可见变更。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，产品版本遵循 SemVer 和 PEP 440。

## [Unreleased]

### Added

- 简体中文 Tkinter 首次运行向导、六格式确定性示例资料和前五条本地搜索结果。
- 只读 `RepositoryEstimate` 预检、版本化 Windows 时间/空间系数和稳定错误码。
- `UpdateProgress`、线程安全 `CancellationToken`、提交前事务回滚及 cancelled RunReport。
- `octopus upgrade check --format table|json` 与 GUI 24 小时缓存的 GitHub 稳定版检查。
- PyInstaller 6.21 共享 onedir、Inno Setup 6.7.3 每用户离线安装器及受保护签名 CI。
- 只记录阶段时间、结果、错误码和计数的本地匿名首次体验报告。
- `octopus acceptance export/summarize` 本地显式导出与多参与者匿名验收汇总。

### Changed

- 向导创建的仓库默认关闭 AI，首次流程固定为 0 次 AI 调用。
- 仓库初始化先持久化本地配置/状态，再注册全局配置；失败仅清理本次 Octopus 文件。
- watcher/API 子进程命令在冻结环境中复用当前 CLI 可执行文件。
- 显式 force 继续尊重 Office 编辑锁，但不再等待 quiet-time，保证首次构建立即完成。

### Security

- 升级检查只接受固定 `tty627/octopus` GitHub Release 路径，3 秒超时且失败不阻断。
- 发布脚本要求主程序、CLI、卸载器和安装器通过 SHA-256 Authenticode 与 RFC 3161 时间戳。
- Windows 打包工作流校验 Tag/代码/文件版本一致，并自动验证静默安装、卸载、重装和数据保留。

## [0.3.0] - 2026-07-13

### Added

- 解析证据定位符、提取统计、截断状态和有界解析输入。
- 字段加权的 SQLite FTS5 搜索、精确名称增益和查询词覆盖解释。
- 经候选节点验证的 AI 搜索引用。
- Prompt 版本、Token/成本预算和搜索缓存 Schema 迁移。
- 产品版本迭代、路线图、指标、性能、兼容性与分支治理文档。
- 可复现的 1k/10k/100k 合成数据集生成器、事务与增量基准流程。

### Changed

- FolderNode 子节点扩展先去重再分批查询，避免超过 SQLite 参数上限。
- 合并非必要的事务记录写入，保留修改目标前的回滚意图持久化。
- 事务回滚意图改为追加式 fsync journal，操作查找改为 O(1)。
- 产品版本改为由 `src/octopus/__init__.py` 单一来源驱动。

### Fixed

- 大型 FolderNode 搜索扩展可抛出 `OperationalError: too many SQL variables` 的问题。
- 大批量事务中 `record.json` 不必要的写放大。

## [0.2.0] - 内部里程碑（无正式 Tag）

### Added

- Manifest-last 可恢复事务、回滚意图和派生搜索缓存恢复。
- 不可覆盖的每次运行报告和脱敏 AI 使用量。
- dry-run、仓库校验、Provider 错误分类、重试和调用预算。

## [0.1.0] - 内部里程碑（无正式 Tag）

### Added

- Windows-first Python CLI，Raw/Index Repository 只读分离。
- Leaf、FolderNode、Manifest 和可重建 SQLite FTS5 搜索缓存。
- PDF、DOCX、XLSX、PPTX、图片/OCR 解析和可选 DeepSeek 摘要。
- 轮询 Watcher、稳定性状态机、更新日志和 Markmap 输出。

[Unreleased]: https://github.com/tty627/octopus/compare/v0.3.0...HEAD
[0.3.0]: docs/releases/v0.3.md
[0.2.0]: docs/releases/v0.2.md
[0.1.0]: docs/releases/v0.1.md
