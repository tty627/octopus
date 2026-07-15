# Changelog

本文档记录 Octopus 的用户可见变更。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，产品版本遵循 SemVer 和 PEP 440。

## [Unreleased]

No changes yet.

## [1.1.0.dev0] - 2026-07-15 (development build)

### Added

- React/TypeScript + WebView2 桌面工作台，包含资料空间向导、工作台、搜索、证据检查器、任务包和健康恢复入口。
- 本地结果先到、AI 可选后补的搜索交互，以及类型、路径、状态、质量和修改时间筛选。
- 单资料空间任务包 `1.0` Schema、原子保存、revision 冲突、归档、800ms 自动保存和断线本地草稿。
- 任务包确定性 Markdown/Markmap 兼容导出，以及只复制再次确认来源的 Package 异步导出。
- Local API 资料空间预检、示例资料和任务包端点；搜索结果新增可选内容标识、修改时间和大小。
- Windows 一键源码启动入口，自动准备 64 位 Python、虚拟环境和运行依赖，无需 Node.js。
- 与安装器并列发布的 Windows portable zip，解压后可直接运行并经过 CLI/GUI 烟雾验证。

### Changed

- `octopus-gui` 与 `Octopus.exe` 直接启动 WebView2 界面，不再提供 Tkinter 回退入口。
- 可重建搜索缓存 Schema 从 `0.5` 升至 `0.6`，首次使用自动重建，不迁移 Raw 或 Markdown 索引。
- Windows 构建和 CI 在 Python 门禁前增加 Node 22、ESLint、TypeScript、Vitest、前端构建与 Playwright 检查。

### Security

- WebView bootstrap token 仅保存在进程内存；Native bridge 仅开放目录选择、用户确认的文本保存、本地 URI 打开和窗口状态白名单。
- `/ui/` 静态资源使用本地 CSP 与安全响应头，生产界面不依赖远程资源。

## [1.0.0] - 2026-07-14 (engineering final; not tagged or publicly released)

### Added

- v0.9 v1 契约冻结清单、`octopus release-audit` 和版本/阻断/文档/工件一致性检查。
- 机器可读 P0/P1 登记、支持分级/角色政策与紧急回滚手册。
- wheel/sdist 工程 RC Build Manifest 与 SHA256SUMS 独立审计链路。
- v0.8 可校验迁移备份、失败自动恢复与 `migrate --rollback RUN_ID` 显式回滚。
- 默认仅本地的脱敏诊断包、桌面/Local API 入口和手工分享同意回执。
- v0.6/v0.7 升级来源与持久化/API/Plugin 契约兼容矩阵。
- 长中文路径、同步盘暂不可读子树保留、源文件权限失败和性能回归审批门禁。
- v0.7 Plugin API v1 Manifest、版本协商、显式权限授予和 `octopus plugin list/inspect/run` CLI。
- 脱敏的索引查询/时间线能力，以及由宿主复核的文本导出和确认节点复制操作。
- 独立 Plugin Worker、最小环境、日志脱敏与文件系统/网络/子进程审计边界。
- 随 wheel 分发的 Package/Timeline 参考 Plugin 和端到端、越权、崩溃隔离回归。
- v0.6 服务型 Tkinter 桌面端：仓库列表/创建、更新/重试、校验、搜索修复、状态中心和一键打开。
- 稳定 Local API v1 契约握手、API 仓库创建端点及桌面 HTTP 客户端。
- 服务/锁/迁移/AI 降级可执行提示、键盘快捷键和 DPI 缩放契约。
- 版本化离线检索评测集与 `octopus evaluate-search --enforce` 工程门禁。
- 搜索结果的源相对路径、字段级命中证据、解析证据、风险标志和稳定打开 URI。
- `octopus search --open-result N` 一键打开结果源文件或索引。
- v0.5 工程先行的统一可解释 `SearchReport`、纯文本文件结果、证据/风险与推荐打开目标。
- 版本化 60 任务中英检索集、Top-5/MRR 评测器及匿名反平衡用户研究记录/汇总工具。
- 简体中文 Tkinter 首次运行向导、六格式确定性示例资料和前五条本地搜索结果。
- 只读 `RepositoryEstimate` 预检、版本化 Windows 时间/空间系数和稳定错误码。
- `UpdateProgress`、线程安全 `CancellationToken`、提交前事务回滚及 cancelled RunReport。
- `octopus upgrade check --format table|json` 与 GUI 24 小时缓存的 GitHub 稳定版检查。
- PyInstaller 6.21 共享 onedir、Inno Setup 6.7.3 每用户离线安装器及受保护签名 CI。
- 只记录阶段时间、结果、错误码和计数的本地匿名首次体验报告。
- `octopus acceptance export/summarize` 本地显式导出与多参与者匿名验收汇总。

### Changed

- 搜索派生库 Schema 升至 `0.5`，旧缓存自动重建；CLI/API JSON 返回完整解释契约。
- 搜索缓存按提交事务增量刷新并绑定 Manifest generation；AI 不可用或无有效证据时自动降级到本地结果。
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
[1.0.0]: docs/releases/v1.0.md
[0.3.0]: docs/releases/v0.3.md
[0.2.0]: docs/releases/v0.2.md
[0.1.0]: docs/releases/v0.1.md
