# Octopus

Octopus 是一个面向学习和研究的 Windows 本地资料工作台。它直接搜索原始资料与 ZIP 内部成员，把结果定位到可核验的页面、段落、表格、单元格、幻灯片、图片或文本行，再把确认过的证据整理成可引用的研究资料包。

当前开发版本为 `2.1.0.dev1`，主要用于：

- 在 PDF、Office、图片、文本和 ZIP 资料中检索正文；
- 打开命中位置核对原文，不依赖不可追溯的摘要；
- 用文献综述、课程报告或自由研究模板组织证据；
- 编辑引用元数据，并以 GB/T 7714-2015 或 APA 生成参考文献；
- 导出包含研究正文、BibTeX、任务快照和来源清单的研究资料包。

原始资料始终只读。搜索缓存和 ZIP 成员临时副本位于 Local AppData，资料包任务位于 Roaming AppData。AI 不是搜索或导出的前提，未获得资料空间的明确授权时，页面图像不会发送给视觉模型。

## 下载与安装

在 [GitHub Releases](https://github.com/tty627/octopus/releases) 下载当前版本：

- `Octopus-<版本>-win-x64-setup.exe`：普通安装包；
- `Octopus-<版本>-win-x64-portable.zip`：解压后运行 `Octopus.exe`；
- `SHA256SUMS.txt`：下载文件校验值。

当前 `dev` 版本未签名，Windows 可能显示 SmartScreen 提示。安装与校验步骤见 [Windows 安装说明](docs/user/WINDOWS_INSTALLATION.md)。

## 实际使用流程

### 1. 添加资料空间

首次启动时选择原始资料文件夹，并填写便于识别的名称。Octopus 自动创建内部缓存，不要求用户选择或管理 Index 目录，也不会移动、重命名或修改原文件。

首次同步在后台执行。此后本地文件变化以 5 秒防抖增量处理，并每 10 分钟做一次一致性扫描；不支持可靠文件事件的网络盘会退化为轮询。资料页会显示处理进度、质量、警告和失败原因。

### 2. 搜索原始资料和 ZIP 成员

搜索支持文件名、标题、正文及证据位置，并可按修改时间、正文质量、索引状态、物理文件或 ZIP 来源、资料包归属筛选。结果中的 ZIP 成员使用虚拟路径表示，例如：

```text
课程资料.zip!/论文/数值分析.pdf
```

每个结果显示命中原因、通用证据位置、正文片段、质量状态和来源路径。搜索结果可以多选后批量加入资料包；AI 只能建议候选，不能替用户标记为已确认。

### 3. 核对证据

PDF 结果通过认证接口显示真实页面 PNG，可前后翻页并核对命中片段。数学公式以原始页面为准，Octopus 不伪造 LaTeX。

其他格式使用与文件结构相符的位置：DOCX 使用段落或表格，XLSX/XLSM 使用 Sheet 和单元格范围，PPTX 使用幻灯片和演讲者备注，图片使用原图与本地 OCR 区域，文本使用行号。Office 没有可靠页面信息时不会伪造页码。

ZIP 成员在搜索、预览、引用和资料包中保持独立身份。需要交给桌面应用打开时，Octopus 才将该成员物化为 Local AppData 中的只读临时副本，绝不写回外层 ZIP。

### 4. 建立研究资料包

导航中的“资料包”提供三种模板：

- 文献综述：背景、核心文献、方法与数据、主要结论、相反证据、研究缺口；
- 课程报告：论点、课程材料、分析、反例、结论；
- 自由研究：可自由调整槽位。

槽位可以增删、改名和排序。每条资料保存来源身份、证据位置、摘录、用途、核验状态、引用元数据、确认时哈希和新鲜度。来源内容变化、ZIP 成员重命名或出现歧义时，原摘录会保留，但状态转为“待重新核验”。

引用元数据可以人工编辑。默认样式是 GB/T 7714-2015，也可以切换 APA；Octopus 不会自动联网补全作者、DOI 或 URL。

研究资料包导出为 ZIP，包含：

```text
research.md
references.bib
task.json
manifest.json
```

导出默认不复制原文件。只有显式勾选后，才会复制已确认且当前可访问的来源；对于 ZIP，只复制选中的成员，不复制整个外层压缩包。

### 5. 处理来源变化

首页和变化日志显示新增、修改、移动、删除与解析警告，并标明受影响的资料包。同步任务支持取消；应用重启后会把中断任务重新排队，并通过哈希跳过已经完成的文件。

清理缓存、重建索引或移除资料空间配置不会删除原始资料或资料包任务。

## 当前格式支持

| 类型 | `2.1.0.dev1` 能力 | 证据位置 |
| --- | --- | --- |
| PDF | PDFium、PyPDF、本地 OCR、正文搜索、页面预览 | 页码 |
| TXT、Markdown、CSV、JSON、YAML、代码等文本 | 流式读取、多编码识别、正文搜索 | 文本行 |
| DOCX | 标题、段落和表格正文 | 段落、表格 |
| XLSX、XLSM | Sheet、单元格值和公式文本 | Sheet、单元格范围 |
| PPTX | 幻灯片正文和演讲者备注 | 幻灯片 |
| PNG、JPEG、TIFF、WebP、BMP | 原图预览和本地 OCR | 图片或 OCR 区域 |
| ZIP | 容器与独立成员检索，最多一层嵌套 ZIP | 成员自身的位置 |

解析器会记录名称和版本。升级后，即使文件修改时间没有变化，旧的“仅文件信息”记录也会按新解析器自动重建。所有解析器都有字节、页数、OCR、单元格、时间和取消预算；超限时会显示明确警告，而不是静默截断。

## ZIP 安全边界

默认限制为 10,000 个成员、单成员 100 MiB、总展开 512 MiB、压缩比 100:1，并且只支持外层 ZIP 和最多一层嵌套 ZIP。Office 文件优先交给语义解析器，不会把内部 XML 当作普通成员展示。

Octopus 不使用 `extractall`，并拒绝路径穿越、盘符、NUL、链接和异常压缩方法。加密、分卷、SFX、损坏或超过预算的 ZIP 仅保留可用元数据并说明原因；损坏 ZIP 会保留上次可用索引并标记过期。旧 ZIP 的中文名称仅在明显乱码且没有碰撞时尝试 CP936 恢复，并记录编码风险。

成员临时缓存默认保留 24 小时、上限 2 GiB，按 LRU 清理。删除该缓存不会修改原 ZIP，也不会删除资料包。

## 可选 AI 辅助

本地模式可以完成导入、搜索、证据核对、资料包整理、引用编辑和导出。启用辅助整理后，AI 只能处理本地已经检索到的候选，不能添加未检索到的来源或自动确认资料。

API Key 保存在 Windows 凭据管理器。远程 Base URL 必须使用 HTTPS；只有 `localhost`、`127.0.0.1` 和 `::1` 可以使用 HTTP。“允许发送疑难页面图像”是单独的资料空间授权，默认关闭。

## 数据位置

```text
原始资料文件夹/
  用户文件与 ZIP，Octopus 只读

%LOCALAPPDATA%\Octopus\workspaces\<workspace_id>\
  workspace.sqlite3       可重建的文档、位置、正文、变化日志和 FTS5 缓存
  previews\               按内容哈希缓存的页面 PNG
  member-cache\           ZIP 成员的短期只读副本

%APPDATA%\Octopus\
  config.json             资料空间与本地服务配置
  service-token           本地 API 凭据
  ui-state.json           桌面导航状态
  workspaces\<workspace_id>\tasks\
                          不可丢失的研究资料包任务
```

V1 `*-Octopus-Index` 目录只作为回滚和迁移来源记录。V2 不会继续同步、修改或向普通用户展示这些目录。

## 隐私与安全

- 本地 API 只监听回环地址，并要求 Bearer token；
- 生产界面不加载远程脚本、字体或图片；
- 原始资料、内部缓存和资料包默认不上传；
- 诊断、导出和打开原文件都由用户明确发起；
- 页面图像授权按资料空间保存，默认关闭；
- 卸载应用不会删除原始资料或 Roaming AppData 中的资料包。

## 从源码运行

普通源码体验可以双击：

```text
start-octopus.cmd
```

该入口会准备兼容的 64 位 Python 和虚拟环境。修改前端或运行完整检查时需要 Node.js 22。

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -e .
.venv\Scripts\octopus-gui.exe

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
- [用户指南](docs/user/USER_GUIDE.md)
- [故障排查](docs/user/TROUBLESHOOTING.md)

## 发布状态

`2.1.0.dev1` 是未签名的 Windows 开发预览版。PDF、文本、首批 Office、图片、ZIP 和研究资料包闭环属于本里程碑范围；签名安装包、Defender 干净机、Windows 10/网络盘发布矩阵和真实研究用户验收仍是正式版门禁。
