# Changelog

本文档记录 Octopus 的用户可见变更。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，产品版本遵循 SemVer 和 PEP 440。

## [Unreleased] - v0.3.0.dev0

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

[Unreleased]: docs/releases/v0.3.md
[0.2.0]: docs/releases/v0.2.md
[0.1.0]: docs/releases/v0.1.md
