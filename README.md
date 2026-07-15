# Octopus

Octopus 是一个 Windows 本地证据工作台。它不生成一堆需要用户理解的索引文件，而是直接搜索原始资料，并把结果定位到可以核验的文件、页面和正文片段。

当前开发版本为 `2.0.0.dev1`，主要面向这些场景：

- 在课程、项目或下载资料中快速找到真正相关的文件；
- 打开 PDF 的命中页面核对原文，而不是阅读不可追溯的摘要；
- 把确认过的页面和文本收集成任务；
- 将任务导出为包含来源、页码和证据片段的 Markdown。

原始资料始终只读。搜索缓存放在 Local AppData，任务放在 Roaming AppData。未获得当前资料空间的明确授权时，页面图像不会发送给视觉模型。

## 下载与安装

在 [GitHub Releases](https://github.com/tty627/octopus/releases) 下载当前版本：

- `Octopus-<版本>-win-x64-setup.exe`：普通安装包；
- `Octopus-<版本>-win-x64-portable.zip`：解压后直接运行 `Octopus.exe`；
- `SHA256SUMS.txt`：下载文件校验值。

当前 `dev` 版本未签名，Windows 可能显示 SmartScreen 提示。安装与校验步骤见 [Windows 安装说明](docs/user/WINDOWS_INSTALLATION.md)。

## 实际使用流程

### 1. 添加资料空间

首次启动时只需要选择原始资料文件夹，并填写一个便于识别的名称。Octopus 会自动创建内部缓存，不再要求用户选择或管理 Index 目录。

创建后会直接进入“资料”页。同步在后台执行，页面持续显示当前文件、处理阶段、完成数量和失败数量。原文件不会被移动、重命名或修改。

### 2. 搜索原始资料

“搜索”是应用的第一入口。可以输入：

```text
微分方程
级数
项目预算
```

搜索结果按文件名、章节标题和正文证据分层排序。每个文件只出现一次，并显示：

- 人类可读的命中原因；
- 页码或文本位置；
- 可核验的正文片段；
- 正文质量状态；
- 文件大小和相对路径。

文件夹不会作为普通结果，内部字段、索引路径和 V1 `Leaf/FolderNode` 类型不会显示在界面中。

### 3. 核对页面证据

选择 PDF 结果后，右侧检查器通过认证接口读取真实页面 PNG，可以前后翻页并核对命中片段。数学公式以原始页面图像为准，Octopus 不会伪造 LaTeX。

文本文件会显示定位到的原文片段。无法可靠定位页码时，界面会明确说明，不会猜测页码。

### 4. 加入任务并导出

点击“加入任务”后，当前文件和证据位置会进入任务。任务项保存文档身份、内容哈希、页码和摘录，不依赖旧索引节点 ID。

任务页可以：

- 编辑任务名称和目标；
- 将证据分为核心证据、补充证据和待核验；
- 确认或移除证据；
- 导出包含来源状态、页码、用途和摘录的 Markdown。

任务保存在 `%APPDATA%\Octopus\workspaces\<workspace_id>\tasks`。可重建搜索缓存损坏或被删除时，任务不会随之丢失。

### 5. 查看资料健康状态

“资料”页显示文档总数、正文可读、部分可读、识别质量低、仅文件信息和处理失败数量。可以同步整个资料空间，也可以强制重新处理单个文件。

PDF 页面依次使用 PDFium 文本、PyPDF 备用提取和本地 OCR。低质量正文不会生成摘要，也不会参与正文排名；文件名和元数据仍可搜索。

## 当前格式支持

| 类型 | `2.0.0.dev1` 能力 |
| --- | --- |
| PDF | 逐页文本提取、本地 OCR、质量评分、正文搜索、页面预览 |
| TXT、Markdown、CSV、JSON、YAML、代码等文本 | 编码识别、正文搜索、文本证据 |
| Word、Excel、PowerPoint | 文件名和元数据搜索；深度正文解析将在后续版本接入 |
| PNG、JPEG 等图片 | 文件名和元数据搜索；页面视觉处理仅在明确授权后使用 |

“仅文件信息”不是处理失败。它表示当前版本尚未对该格式抽取正文，但文件仍可以按名称和路径找到。

## 可选 AI 辅助

AI 不是搜索前提。默认本地模式可以完成导入、搜索、页面核对、任务整理和导出。

启用辅助整理后，AI 只能在本地已经检索到的候选中重排或总结，不能添加未检索到的文件，也不能改变文件名精确匹配的最高优先级。API Key 保存在 Windows 凭据管理器，不写入资料空间。

“允许发送疑难页面图像”是单独的资料空间授权。关闭时只使用 PDF 文本提取和本地 OCR，网络层不得发送页面图片。

## 数据位置

```text
原始资料文件夹/
  用户文件，Octopus 只读

%LOCALAPPDATA%\Octopus\workspaces\<workspace_id>\
  workspace.sqlite3       可重建的文档、页面、正文和 FTS5 缓存
  previews\               按内容哈希缓存的页面 PNG

%APPDATA%\Octopus\
  config.json             资料空间与本地服务配置
  service-token           本地 API 凭据
  ui-state.json           桌面导航状态
  workspaces\<workspace_id>\tasks\
                          不可丢失的用户任务
```

V1 `*-Octopus-Index` 目录只作为回滚和迁移来源记录。V2 不会继续同步、修改或向普通用户展示这些目录。

## 隐私与安全

- 本地 API 只监听回环地址，并要求 Bearer token；
- 生产界面不加载远程脚本、字体或图片；
- 原始资料、内部缓存和任务默认不上传；
- 诊断和导出由用户明确发起；
- 页面图像授权按资料空间保存，默认关闭；
- 卸载应用不会删除原始资料或 Roaming AppData 中的任务。

## 从源码运行

普通源码体验可以双击：

```text
start-octopus.cmd
```

该入口会准备兼容的 64 位 Python 和虚拟环境。前端构建产物已包含在仓库中，普通运行不需要 Node.js。

手动启动：

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -e .
.venv\Scripts\octopus-gui.exe
```

修改前端或运行完整检查时需要 Node.js 22：

```powershell
Set-Location frontend
npm ci
npm run lint
npm run typecheck
npm test
npm run e2e
npm run build

Set-Location ..
.venv\Scripts\python.exe -m pytest --cov=octopus --cov-report=term-missing
.venv\Scripts\python.exe -m ruff check src tests
.venv\Scripts\python.exe -m mypy src/octopus
```

Windows 完整打包：

```powershell
.\packaging\build_windows.ps1
```

## API 与兼容性

桌面端使用认证的本地 V2 API。V1 路由在当前版本中继续保留，用于并行回滚和旧 CLI 兼容。

- [Local API V2](docs/api/LOCAL_API_V2.md)
- [Local API V1 回滚参考](docs/api/LOCAL_API_V1.md)
- [兼容性矩阵](docs/product/COMPATIBILITY_MATRIX.md)
- [路线图](docs/product/ROADMAP.md)
- [故障排查](docs/user/TROUBLESHOOTING.md)

## 发布状态

`2.0.0.dev1` 是 Windows 开发预览版。PDF/文本证据闭环是本里程碑的主要验收范围；Office 深度正文解析、签名安装包和更广泛的干净机器验证仍属于后续发布门禁。
