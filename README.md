# Octopus

Octopus 是一款面向本地文件的资料检索与任务整理工具。它把散落在文件夹里的 PDF、Office 文档、图片、Markdown 和代码文件整理成一个可搜索、可核验、可交付的资料空间。

它不是一个只给答案的聊天机器人。Octopus 更关注答案从哪里来：每条搜索结果都会保留原文件路径、页码、工作表、段落或 OCR 位置等证据，用户确认后再把资料加入任务包，最终导出为结构化 Markdown 或独立文件包。

整个过程默认在本机完成。Octopus 不会修改你的原始资料，也不会自动上传文件。

## 它能解决什么问题

当项目资料越来越多时，真正困难的通常不是“有没有这个文件”，而是：

- 最终版本究竟是哪一份；
- 某个结论来自哪一页、哪个表格或哪次会议；
- 准备汇报、审计或交付时应该收集哪些资料；
- 搜索到的内容是否可靠，是否还需要人工核验；
- 如何把查找结果整理成一个可以继续使用和交付的资料集合。

Octopus 将这些步骤整合为一条工作流：

1. 选择资料文件夹，建立只读资料空间；
2. 在本机生成独立索引，不改动原始目录；
3. 搜索文件名、正文、项目、人名、时间或任务描述；
4. 在证据检查器中核对命中原因和原文位置；
5. 将确认过的资料加入任务包并分类整理；
6. 导出 Markdown 大纲或经过再次确认的文件副本。

## 核心功能

### 本地资料空间

- 通过三步向导选择资料目录、检查文件规模并建立索引；
- Raw 原始资料与 Index 索引目录严格分离；
- 建立索引前显示文件数量、支持格式、磁盘空间、阻断项和警告；
- 支持示例资料，新用户无需准备文件即可体验完整流程；
- 首批结果可用后即可开始搜索，剩余索引在后台继续处理。

### 可核验搜索

- 本地结果优先返回，不依赖网络或 API Key；
- 结果确定性分为“核心资料、相关文件夹、补充资料、需要核验”；
- 支持按文件类型、路径、状态、质量和修改时间筛选；
- 证据检查器展示摘要、命中原因、文件路径、更新时间和内容标识；
- PDF 页码、Excel Sheet、文档段落、幻灯片和 OCR 区域都可以作为证据位置；
- 单击结果只查看证据，只有明确确认后才会加入任务包。

### 任务包

任务包用于把一次搜索变成一个可以继续编辑和交付的资料集合。

- 默认提供“核心资料、补充资料、待核验”三个槽位；
- 支持新增、删除、改名和拖动排序；
- 保存加入原因、证据锚点、来源状态和确认状态；
- 草稿自动保存，服务暂时断开时会保留本地编辑；
- 多处编辑发生版本冲突时，可以重新载入或保留本地草稿；
- 删除任务包实际执行归档，避免误删工作成果；
- 可导出兼容 Markmap 的 Markdown；
- Package 导出只复制用户再次勾选的已确认资料，待核验项目默认不选中。

### 可选 AI 辅助

AI 只用于对已有候选结果进行重排和解释，不负责替代本地索引，也不能自动把资料标记为“已确认”。

没有 API Key、网络不可用或 AI 返回无效引用时，Octopus 会保留完整的本地结果并自动降级。所有 AI 内容都会明确标记为“AI 建议”。

### 健康检查与恢复

资料空间页面提供同步、失败重试、完整性校验、搜索缓存重建和本地诊断。正常状态只显示最近同步时间，只有来源不可访问、索引异常或任务失败时才会突出提醒。

## 与普通 RAG / GraphRAG 工具的区别

Octopus 不把产品中心放在“和文档聊天”上，而是放在可验证的资料工作流上：

- **原始资料只读**：索引、任务包和运行状态全部保存在独立目录；
- **证据优先**：搜索结果必须能回到具体文件和证据位置；
- **用户确认**：AI 可以建议，但不能替用户确认资料；
- **面向交付**：搜索结果可以继续整理为任务包，而不是停留在一次问答；
- **本地可恢复**：索引可校验、重建和诊断，服务断开不会直接丢失草稿；
- **渐进式使用**：不启用 AI 也能完成索引、搜索、核验和导出。

Octopus 当前不试图生成一个覆盖所有资料的复杂知识图谱。它优先解决更常见的问题：快速找到资料、判断是否可信，并整理成可实际使用的成果。

## 支持的文件

Octopus 可以处理：

- PDF，包括扫描页 OCR；
- Word：`.docx`；
- Excel：`.xlsx`、`.xlsm`；
- PowerPoint：`.pptx`；
- 图片：PNG、JPEG、BMP、GIF、TIFF、WebP；
- 文本与数据：TXT、Markdown、CSV、JSON、YAML、TOML、XML、HTML；
- 常见代码文件：Python、JavaScript、TypeScript、Java、Go、Rust、C/C++、Shell、PowerShell、SQL 等。

不支持内容抽取的文件仍可作为元数据节点进入索引，并会明确标记质量状态。

## 安装与启动

当前桌面版本为 `1.1.0.dev0`，正式支持 Windows 11 x64。应用使用系统 WebView2 Runtime；如果电脑缺少该组件，启动时会显示安装指引。

Windows 安装包已经包含 Python、文档解析器和本地 OCR 运行环境。普通用户不需要单独安装 Python、Node.js 或配置 API Key。

1. 从 GitHub Releases 下载 Windows 安装包；
2. 运行安装程序；
3. 从开始菜单启动 Octopus；
4. 选择自己的资料文件夹，或使用示例资料；
5. 通过预检后建立资料空间并开始搜索。

`1.1.0.dev0` 当前提供的是未签名开发安装包，Windows 可能显示 SmartScreen 提示。发布文件应使用仓库提供的 `SHA256SUMS.txt` 校验完整性。

详细步骤见 [Windows 安装与首次运行](docs/user/WINDOWS_INSTALLATION.md)。

## 基本使用

在工作台输入文件名、内容线索或需要完成的任务，例如：

```text
查找最终版报价和审批记录
准备季度项目汇报需要的进展、预算与风险资料
整理最近一次范围变更的决策依据
```

搜索完成后：

1. 单击结果，在右侧检查摘要、来源和证据位置；
2. 点击“打开来源”查看原始文件；
3. 点击“加入任务包”确认使用这项资料；
4. 在底部托盘或任务包页面调整分类和顺序；
5. 导出 Markdown，或再次勾选后导出 Package。

常用快捷键：

- `Ctrl+F`：聚焦搜索；
- `Ctrl+N`：打开任务包；
- `Ctrl+Enter`：将当前证据加入任务包；
- `Esc`：关闭窄窗口中的证据抽屉。

完整说明见 [用户指南](docs/user/USER_GUIDE.md)。

## 命令行使用

安装版同时提供 `octopus` 命令。桌面端和 CLI 使用同一套索引引擎与资料空间配置。

```powershell
octopus version
octopus doctor

octopus init --raw "D:\ProjectFiles" --index "D:\ProjectFiles-Octopus-Index" --name "项目资料"
octopus update --once
octopus search "最终版报价"
octopus search "季度项目进展" --format json --open-result 1
octopus validate --format json
octopus report --last --format markdown
octopus rebuild-search
```

可选 AI 搜索使用环境变量提供密钥，密钥不会写入资料空间配置：

```powershell
$env:DEEPSEEK_API_KEY = Read-Host "DeepSeek API Key" -MaskInput
octopus search --mode auto "整理项目风险和对应证据"
```

## 数据保存在哪里

假设原始资料目录是 Raw，索引目录是 Index：

```text
Raw/
  原始文件，由用户管理，Octopus 只读

Index/
  生成的 Markdown 索引
  .octopus/
    repository-config.json
    repository-state.json
    search.sqlite3
    task-packs/
    runs/
    transactions/
```

`search.sqlite3` 是可重建的搜索缓存。任务包保存在 `<Index>/.octopus/task-packs/`。卸载 Octopus 不会删除 Raw、Index 或 `%APPDATA%\Octopus` 中的用户配置，重新安装后可以重新发现已有资料空间。

## 隐私与安全边界

- Octopus 默认只监听本机回环地址；
- Local API 使用每次进程启动生成的 Bearer token；
- token 只通过受限桌面桥保存在内存中，不写入前端存储；
- 生产界面不加载远程脚本、字体或图片；
- 诊断默认留在本机，不会自动上传；
- Package 导出必须由用户再次确认文件和目标目录；
- Raw 目录在正常索引、搜索、任务包和诊断流程中保持只读。

## 当前范围

Octopus 当前主要支持 Windows 11 x64 和单资料空间任务包。跨资料空间搜索、最近变化时间线、关系图、Agent 交接以及独立 Markmap HTML 尚未包含在当前桌面版本中。

## 从源码运行

开发环境需要 Python 3.12+ 和 Node.js 22。

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"

Set-Location frontend
npm ci
npm run build
Set-Location ..

octopus doctor
octopus-gui
```

前端开发服务器：

```powershell
Set-Location frontend
npm run dev
```

运行主要检查：

```powershell
pytest --cov=octopus --cov-report=term-missing
ruff check .
mypy src

Set-Location frontend
npm run lint
npm run typecheck
npm test
```

## 相关文档

- [用户指南](docs/user/USER_GUIDE.md)
- [Windows 安装说明](docs/user/WINDOWS_INSTALLATION.md)
- [故障排查](docs/user/TROUBLESHOOTING.md)
- [Local API v1](docs/api/LOCAL_API_V1.md)
- [产品设计方案](docs/product/OCTOPUS_PRODUCT_DESIGN_PROPOSAL.md)
- [前端 UI/UX 设计草案](docs/product/OCTOPUS_FRONTEND_UI_UX_DRAFT.md)
