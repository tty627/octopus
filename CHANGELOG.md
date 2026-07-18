# Changelog

本文档记录 Octopus 的用户可见变更。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，产品版本遵循 SemVer 和 PEP 440。

## [Unreleased]

## [2.1.0.dev1] - 2026-07-18 (development build)

### Added

- 新增可取消、可重试并显示阶段进度的研究与资料包后台任务中心。
- 新增页面级视觉分析预检、AI Provider 能力探测，以及带完整性校验和有效期的导出制品下载。

### Changed

- 强化专业研究工作流：统一异步研究、任务提案、来源核验、引用导出和 Windows 桌面端交互。
- 并发资料包导出使用隔离文件，避免不同引用格式或来源选项互相覆盖。

### Fixed

- 无有效证据引用的 AI 研究回答会安全降级到本地检索摘要，不再作为带引用答案接受。

## [2.1.0.dev0] - 2026-07-16 (development build)

### Added

- 统一的 `SourceRef` 和 `EvidenceLocator` 来源契约，可表达物理文件、ZIP 容器与成员，以及页码、段落、表格、Sheet/单元格、幻灯片、图片和文本行定位。
- DOCX、XLSX/XLSM、PPTX 和常见图片正文解析；ZIP 成员作为独立文档进入搜索、预览、资料包和引用流程，并支持一层嵌套 ZIP。
- 文献综述、课程报告和自由研究资料包模板，可编辑引用元数据，并按 GB/T 7714-2015 或 APA 生成参考文献。
- 研究资料包 ZIP 导出，包含 `research.md`、`references.bib`、`task.json` 和 `manifest.json`，可选择复制已确认且仍可访问的来源。
- 可续跑的 AI 资料卡索引和研究目标提案；模型只能选择服务器提供的候选证据，保存前会重新核验内容哈希。
- 首页、最近活动、变化日志、可取消且可恢复的后台 Job，以及资料空间重命名、停用、排除目录、缓存重建和移除配置。

### Changed

- 导航中的“任务”升级为研究资料包，支持自定义分组、用途、核验状态、引用和来源新鲜度。
- 搜索增加修改时间、质量、索引状态、物理/ZIP 来源和资料包归属过滤，并把大资料空间的候选过滤下推到 SQLite。
- V2 SQLite、任务和 Local API 契约升级到 `2.1`，保留旧路径、页码和 `source_uri` 字段以兼容旧客户端。

### Fixed

- Windows 构建清单优先读取真实 Inno Setup 编译器版本，不再记录 Chocolatey shim 版本。
- 外层 ZIP 移动时保留成员身份；成员改名、删除、内容变化或出现歧义时，关联资料包会明确进入待重新核验状态。
- 损坏或超过资源限制的 ZIP 会保留上次可用索引并记录解析警告，不再静默丢失来源。

### Security

- ZIP 扫描拒绝路径穿越、盘符、NUL、链接、异常压缩方法和压缩炸弹，不使用 `extractall`，成员只物化到受控的 Local AppData 缓存。
- 远程 AI Base URL 只允许 HTTPS；HTTP 仅允许 `localhost`、`127.0.0.1` 和 `::1`。
- AI 仍不是本地搜索和资料包导出的前提，页面图像仍需按资料空间明确授权。

## [2.0.0.dev1] - 2026-07-16 (development build)

### Fixed

- 创建资料空间后立即进入资料页，后台解析不再因前端固定等待两分钟而显示假失败。
- 资料页持续显示文件、页码、提取阶段和 OCR 处理进度，并在任务完成后自动刷新文档和健康状态。
- 阻止父子目录重叠的资料空间重复执行解析与 OCR；同名资料空间显示可区分的路径。
- 单文件“重新处理”现在会强制重新解析，而不是被未变化文件检查跳过。
- PDF 页面预览允许安全加载本地 Blob，修复安装版中缓存图片存在但界面显示破图的问题。
- PDF 正文命中会通过 PDFium 文本层直接高亮在页面 PNG 上；仅文件名命中不再伪造正文原因或页码。
- 文件名检索优先级调整为精确、词干/前缀、包含、模糊、标题和正文；“微分方程”等查询会优先展示最直观的同名资料。
- 任务编辑会立即写入本地草稿并串行保存；切换页面、导出或归档前会刷新最新 revision，冲突时可明确恢复或放弃草稿。
- 编辑任务后立即返回列表也会先保存；失败时保留编辑器和草稿，不再短暂显示旧标题。
- 资料空间切换会取消旧搜索和迟到的设置、任务请求，避免前一个资料空间的结果或错误污染当前界面。
- 已提交查询与输入框草稿分离，旧结果的高亮和新任务标题不会被尚未执行的新输入污染。
- 页面检查器按当前页的真实命中加入证据；相邻无命中页面不再错误复用其他页片段。
- 原文件在搜索后被移动或删除时，“打开原文件”会给出可恢复提示，不再静默失败。
- 同哈希文件移动后任务来源可自动回连；删除、内容变化或多副本歧义会标记“来源待重新确认”，不会静默丢失。
- 来源失效会同步把人工核验状态降为“待核验”，任务界面和 Markdown 导出不再同时声称“已确认”。
- Office 首个里程碑中的元数据文件显示“仅文件信息”，不再误报为“识别质量低”。
- 资料健康统计不再把“仅文件信息”和“处理失败”重复计入“识别质量低”。
- 桌面窗口状态现在恢复资料空间和任务，设置读取失败时不再显示无关操作或允许覆盖未知状态。
- 任务保存和归档的 revision 检查与写入改为原子操作，避免并发请求产生假冲突或覆盖更新。

### Security

- 页面预览仍通过认证接口读取；只有用户明确授权的资料空间才允许视觉模型接收页面图像。

## [2.0.0.dev0] - 2026-07-15 (development build)

### Added

- 隐藏在 Local AppData 的 SQLite/FTS5 资料空间，按文档、页面和正文片段保存可重建缓存。
- PDFium、PyPDF 和本地 OCR 的逐页候选提取，以及可读、部分可读和识别质量低三级质量控制。
- 文档级 V2 搜索、认证 PDF 页面预览、页面前后导航和证据任务闭环。
- 保存在 Roaming AppData 的 V2 任务数据、revision 冲突保护和 V1 任务幂等迁移。
- 搜索、任务、资料、设置四入口界面，以及单文件重新处理和显式页面图像授权。

### Changed

- 产品从生成 Leaf/FolderNode 索引文件改为搜索原始资料并定位可信页面证据。
- 新建资料空间只选择原始资料文件夹；内部缓存位置不再由用户管理。
- 文件夹不再作为普通搜索结果，低质量正文不参与排名或摘要。

### Security

- 原始资料始终只读；V1 索引仅记录用于回滚，不修改、不继续同步。
- 未获得工作区视觉授权时，页面图像不会发送给模型。

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
