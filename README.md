# Octopus

Octopus 是一个本地优先、以链接为中心的文件索引工具。它会扫描用户选择的 Raw Repository，
且不会修改其中的内容；随后在独立的 Index Repository 中生成紧凑的 Markdown Leaf 和
FolderNode 索引，让用户或智能体无需反复打开非文本原文件即可检索这些索引。

当前正式打 Tag 的版本是 `0.3.0`；v0.4-v0.9 均为已关闭的工程里程碑。
当前开发版本为 `1.1.0.dev0`，以 React/TypeScript + Windows WebView2 任务工作台替换旧桌面界面；
`1.0.0` 是尚未公开发布的工程最终候选版本。
v0.1 和 v0.2 是内部里程碑，没有正式 Git Release Tag。

## 产品与发布文档

- [产品版本迭代总规范](<docs/product/Octopus产品版本迭代总规范.md>)
- [产品设计方案](docs/product/OCTOPUS_PRODUCT_DESIGN_PROPOSAL.md)与
  [前端 UI/UX 草案](docs/product/OCTOPUS_FRONTEND_UI_UX_DRAFT.md)
- [路线图](docs/product/ROADMAP.md)、[指标](docs/product/METRICS.md)，以及
  [性能基线](docs/product/PERFORMANCE_BASELINE.md) / [搜索评估基线](docs/product/SEARCH_EVALUATION_BASELINE.md)
- [版本、分支与兼容性](docs/product/VERSIONING_AND_COMPATIBILITY.md)
- [发布计划与里程碑记录](docs/releases/)和[变更日志](CHANGELOG.md)
- [Windows 安装说明](docs/user/WINDOWS_INSTALLATION.md)、
  [1.0 用户指南](docs/user/USER_GUIDE.md)与[故障排查](docs/user/TROUBLESHOOTING.md)
- [规范性产品与文件格式规格](docs/specs/README.md)

`docs/specs/` 下的 `v1.0.1` 是规格修订号，并非软件发布版本号。

## v1.1 核心任务闭环（开发版）

- Windows 桌面端改为 React/TypeScript + WebView2，本地 FastAPI 在 `/ui/` 托管全部静态资源；
  生产环境不加载远程脚本、字体或图片。
- 首次使用通过三步资料空间向导完成目录选择、只读预检和异步建立索引，也可直接创建示例资料。
- 工作台提供本地结果先到、AI 可选后补的分组搜索，固定证据检查器展示路径、锚点、质量和打开动作。
- 任务包以独立 `1.0` Schema 持久化到 Index，支持 revision 冲突、自动保存、本地草稿、槽位排序、
  Markdown 导出、Package 确认导出和归档。
- Local API v1 契约版本保持 `1.0`；新增接口与可选字段不改变原有字段语义，Raw 仍保持只读。

## v1.0 工程最终版

- 通过 CLI、Local API v1 和 Windows 桌面端提供稳定的 Raw 只读索引、可恢复的
  Manifest-last 更新、可解释的本地搜索，以及无 API Key 时的自动降级。
- 冻结 v1 持久化、API、Plugin 和诊断契约，提供受保护的迁移回滚，并明确支持
  v0.6-v0.9 作为兼容升级来源。
- 提供 Package/Timeline Plugin、本地无内容诊断、支持严重度策略和紧急回滚流程。
- 自动化发布审计会绑定版本、契约、P0/P1 登记表、文档、构建清单和 SHA-256 证据。
- 详见 [1.0 工程最终报告](docs/releases/v1.0-engineering-report.md)。当前不声称已经完成
  签名安装包、干净虚拟机/Defender 证据、真实历史版本升级、Tag 或公开发布。

## v0.9 工程 RC

- 以机器可读格式冻结持久化 Schema、Local API、Search Report、诊断和 Plugin API 权限等
  v1 契约。
- `octopus release-audit` 可发现版本、契约、阻断项、文档和工件漂移。
- 提供机器可读的 P0/P1 登记表、支持严重度/角色策略和紧急回滚手册。
- 功能冻结后，仅允许影响发布的正确性、安全性、升级和一致性修复进入 RC。
- 已签名安装包、干净虚拟机上的历史版本演练及具名人工负责人仍属于明确的外部门禁；
  本工程 RC 不声称已经完成这些验证。

## v0.8 工程里程碑

- 提供基于校验和的 Schema 迁移备份、失败自动恢复和显式受保护回滚。
- 可从 CLI、Local API 或桌面端生成不含内容、路径和凭据的本地诊断；手工分享仍需单独的
  同意凭据，且诊断不会自动上传。
- 提供机器可读的兼容性矩阵，覆盖 v0.6/v0.7 升级来源以及全部持久化/公开契约边界。
- 加固长 Unicode 路径、暂时不可访问的同步子树和源目录权限失败场景。
- 提供绝对与相对性能门禁，任何超过 10% 的回退都需要具名批准。
- 未执行真实签名安装包升级、公开 Beta 用户群或 Windows 机器矩阵，也未在
  [v0.8 记录](docs/releases/v0.8.md)中声称完成这些验证。

## v0.7 工程里程碑

- 提供 Plugin API v1 Manifest、版本协商和执行前显式权限授权。
- 对查询和时间线资源进行脱敏；Plugin 不会收到 Raw 绝对路径或源 URI。
- 由宿主代理文本导出与复制，并限制为本次调用中明确确认的搜索节点。
- Plugin 在独立 Worker 进程中运行，使用最小化环境、有限且脱敏的日志，以及针对文件系统、
  网络和进程事件的 Python 审计保护。
- Wheel 中包含 Package 和 Timeline 参考 Plugin，并通过端到端验证。
- 详见 [Plugin SDK v1 开发者预览契约](docs/plugins/PLUGIN_SDK_V1.md)。该边界不等同于针对
  恶意代码的操作系统级沙箱。

## v0.6 工程里程碑

- 提供由服务支持的 Tkinter 桌面端，覆盖仓库列表/创建、更新/重试、校验、搜索缓存修复、
  状态中心、本地/AI 降级搜索，以及打开源文件或索引。
- 提供稳定的回环 [Local API v1 契约](docs/api/LOCAL_API_V1.md)，支持契约版本握手、
  异步任务和仓库创建。
- 提供可操作的服务、锁、迁移和 AI 降级状态，以及键盘快捷键和 DPI 缩放。
- 桌面层仅包含工作流和展示逻辑；仓库修改仍由共享的 API/Engine 边界负责。

## v0.5 工程里程碑

- 提供带版本的离线中英文 DOCX/XLSX 搜索任务，覆盖重名文件和陈旧数据。
- 在 CLI 和 Local API JSON 中提供字段级匹配摘录、抽取证据、源相对路径、风险标记和稳定的
  打开目标。
- `octopus evaluate-search --enforce` 可在没有 API Key 的情况下执行 Top-5、MRR、任务失败、
  检查步骤和解释契约门禁。
- `octopus search --open-result N` 可打开选中的源文件或索引目标。
- 将纯文本文件作为独立结果，并依据 Manifest 代次增量刷新缓存。
- 60 项 `octopus-retrieval-v1` 套件达到 54/60 Hit@5（90.0%）；聚焦的 10 项解释套件达到
  100% Top-5 和 MRR 1.00，且没有契约失败。
- 搜索 Schema `0.6` 会自动重建旧的可丢弃缓存，并补充内容标识、修改时间和大小筛选元数据。

## v0.4 能力

- 提供简体中文 Tkinter 首次运行向导，以及确定性的六种格式样例仓库。
- 对文件、格式、时间、磁盘和 AI 调用进行只读预检；向导创建的仓库始终默认关闭 AI。
- 提供单调递增的索引进度、安全的提交前取消、不可变的已取消 RunReport，以及无需修改
  Raw 文件的一键重试。
- 提供本地 Top-5 搜索，并可打开生成的索引或原始源文件。
- 通过 GUI 和 `octopus upgrade check` 提供有缓存、非阻塞的 GitHub 稳定版检查。
- 提供 PyInstaller 6.21 共享 onedir 构建，以及基于 Inno Setup 6.7.1 的每用户离线安装包流水线。

v0.4 里程碑结束时尚未完成 Authenticode、干净虚拟机/Defender 验证或真人用户群验证；
[v0.4 记录](docs/releases/v0.4.md)也没有声称完成这些检查。

## 从 v0.3 继承的核心能力

- 严格分离 Raw/Index，并提供受保护的 Raw 只读访问层。
- 提供增量 Manifest，支持 v1.0.1 状态集、稳定性检查、Office 锁检测、指纹、队列、
  移动提示、失败记录和孤立项跟踪。
- 支持 PDF、DOCX、XLSX、PPTX 和图像抽取；扫描页使用本地 RapidOCR/ONNX Runtime。
- 提供页面、标题、工作表、幻灯片、表格和 OCR 输出的解析器证据定位及有限抽取诊断。
- 确定性渲染 Leaf 和自底向上的 FolderNode Markdown，并保护用户编辑区域。
- 提供可重建、可自动迁移的 SQLite FTS5 缓存，支持字段权重、精确名称加权、查询词覆盖和
  中英文混合词。
- 支持可选的 DeepSeek 生成，以及带候选结果引文验证的完整搜索重排。
- 提供带版本的 Prompt，并限制每次运行的调用次数、输入 Token、输出 Token 和配置价格成本。
- 使用 Manifest-last Index 事务，并支持自动回滚或派生缓存恢复。
- 提供不可变的逐次运行报告，包含聚合且不泄露密钥的 AI 使用量与错误遥测。
- 提供面向自动化和后续桌面客户端的只读 dry-run 与仓库校验。
- 通过 `markmap-cli` 离线渲染 Markmap HTML。
- 提供 Windows 轮询 Watcher、仓库锁和更新日志。

## 运行要求

Windows 11 x64 离线安装包内置 Python、OCR 和文档解析器。最终用户完成首次运行流程时，
不需要安装 Python、Node.js，也不需要 API Key。

源码/开发环境要求：

- Python 3.12+
- 仅在需要渲染 HTML Markmap 时使用带 `npx` 的 Node.js
- 仅 AI 摘要和可选的 `search --mode auto` 需要 `DEEPSEEK_API_KEY`；本地搜索和自动降级
  不需要 Key

## 开发环境安装

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
octopus doctor
```

API Key 不会写入仓库配置：

```powershell
$env:DEEPSEEK_API_KEY = Read-Host "DeepSeek API Key" -MaskInput
```

## 快速开始

在源码检出目录中，使用以下命令启动首次运行向导：

```powershell
octopus-gui
```

高级 CLI 示例：

```powershell
octopus init --raw "D:\MyFiles" --index "D:\MyFiles-Octopus-Index" --name "MyFiles"
octopus update --once
octopus update --dry-run --format json
octopus validate --format json
octopus report --last --format markdown
octopus search "项目需求"
octopus search "项目需求" --format json --open-result 1
octopus search --mode auto "找到最重要的项目需求和相关材料" --format report-json
octopus search --full "找到最重要的项目需求和相关材料" --format report-json
octopus search --full "找到最重要的项目需求和相关材料" --markmap result.html
octopus evaluate-search --output .octopus-dev\benchmarks\search-value.json --enforce
octopus watch start
octopus watch status
octopus watch stop
octopus upgrade check --format json
octopus evaluate retrieval --tasks benchmarks/retrieval/v1/tasks.jsonl --judgments benchmarks/retrieval/v1/judgments.jsonl --enforce
octopus evaluate study --tasks benchmarks/retrieval/v1/tasks.jsonl --output study.jsonl
octopus evaluate summarize --records study.jsonl --output study-summary.json
```

首次自动观察可能会把近期修改的文件保留在 `pending_stable` 状态。显式使用
`--force`/初始化会跳过静默期延迟，但 Office `~$` 锁文件等强编辑信号永远不会被跳过。
后续自动更新需要达到配置的连续稳定观察次数。

## 生成的仓库元数据

所有运行文件都位于 `<Index Repository>/.octopus/`：

- `repository-config.json` 和 `repository-state.json`
- `search.sqlite3`（可丢弃；可使用 `octopus rebuild-search` 重建）
- `update-log.md` 和 `update-events.jsonl`
- `transactions/<run_id>/record.json` 和不可变的 `runs/<run_id>.json` 报告
- `update.lock` 和 `watch.pid`

在 Windows 上，Raw 文件和文件夹以 `.url` 快捷方式表示。v0.5 搜索结果会同时给出生成的
索引路径，以及指向原始源文件的稳定打开目标。

## 范围边界

v0.4 不包括自动安装更新、完整的 v0.6 桌面搜索 UI、托盘或开机启动行为、自动安装 Windows
服务、macOS/Linux 安装包或 ARM64 工件。不支持的非文本格式会生成元数据 Leaf，并带有
明确的质量/错误标记。
