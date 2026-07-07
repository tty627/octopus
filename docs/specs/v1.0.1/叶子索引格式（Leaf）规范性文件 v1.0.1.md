# 叶子索引(Leaf)格式规范性文件 v1.0.1

## 叶子索引的基本介绍
叶子索引是 Octopus 索引仓库中面向单个原始文件或最小内容单元的标准化 Markdown 索引文件。

它的作用不是复制原始文件内容，也不是替代原始文件，而是用尽可能小的上下文开销，告诉人和 Agent：

- 这个原始文件是什么；
- 它大致讲什么；
- 该 Leaf 是否值得继续阅读；
- 如果值得继续阅读，应当优先看哪些摘要、结构地图、页码、Sheet、章节或锚点；
- 原始文件及相关附件位于什么位置；
- 哪些内容是用户标注，哪些内容是 AI 生成。

叶子索引应服务于 Octopus 的核心理念：以链接为中心，而不是以内容为中心。它应当保存 compact signals，即元数据、摘要、位置地图、质量评估、锚点、用户重点标记和附件链接，而不应把 PDF 全文、Excel 全表、图片 base64 或完整 OCR 结果直接塞入索引文件。

请注意：Octopus 的日常检索、筛选、Markmap 输出和 Agentic 任务执行只读取 Leaf 与 FolderNode 索引文件，不直接打开非文本型原文件阅读。非文本型原文件只在生成或更新 Leaf 时被只读解析。Leaf 的职责是把原文件中的可复用定位信号沉淀为索引，而不是在每次任务中再次读取原文件。

## 叶子索引的五层文件架构

叶子索引文件整体采用「一个开头 JSON + Markdown 正文」的结构。

开头 JSON 是唯一的机器可读 JSON 区块，统一承载原先“摘要层 JSON”和“附件卡片层 JSON”的信息。为了便于 Agent 分阶段读取，该 JSON 内部必须明确区分 `summary_layer` 与 `attachment_card_layer`：

- `summary_layer`：摘要层，供搜索、路由、文件夹摘要聚合和 Agent 初筛使用；
- `attachment_card_layer`：附件卡片层，供 Agent 决定打开原文件或定位附件时再读取；
- Agent 可以只读取 `summary_layer`，不读取 `attachment_card_layer` 中的信息；
- 当任务需要生成可点击 Markmap 链接、定位 Leaf 对应来源、检查版本或更新 Leaf 时，才读取 `attachment_card_layer`。普通检索不得因为任务相关而直接打开非文本型原文件。

五层架构如下：

1. 摘要层
   - 位于开头唯一 JSON 的 `summary_layer` 字段中。
   - 用于快速扫描、搜索、路由、排序和文件夹摘要消费。
   - 只保存对任务选择有帮助的短摘要、类型、标签、价值判断和阅读建议。

2. 附件卡片层
   - 统一放入开头唯一 JSON 的 `attachment_card_layer` 字段中。
   - 用于记录原始文件来源、路径、URI、文件名、扩展名、大小、创建/修改时间、内容 ID、附件列表等信息。
   - 普通搜索和文件夹摘要阶段默认不读取该层；只有在需要打开或定位原始文件时才读取。

3. 正文层
   - 使用 Markdown 格式，放在开头 JSON 之后。
   - 记录原始文件的重要信息、结构地图、页码/Sheet/章节锚点、提取质量评估等。
   - 正文层可以比摘要层更详细，但仍然不应复制完整原文。

4. 可选扩展层
   - 使用 Markdown 格式。
   - 用于后续对叶子索引进行层次细化、额外索引、局部 OCR 摘录、模型处理说明或其他扩展信息。
   - 没有扩展内容时可以省略。

5. 维护层
   - 使用 Markdown 格式。
   - 智能体在普通检索和摘要提取时不需要读取。
   - 只有在更新叶子索引时才读取。
   - 必须保留用户提示、用户建议和维护日志，不得在自动更新时覆盖用户标注。



## 叶子索引的具体格式

### A. 机器可读层：唯一开头 JSON

推荐格式如下：

```json
{
  "schema": {
    "octopus_schema": "0.2",
    "index_type": "leaf",
    "json_role": "unified_machine_header"
  },
  "summary_layer": {
    "name": "",
    "one_sentence_summary": "",
    "description": "",
    "document_type": "",
    "languages": [],
    "tag_rough": [],
    "topic_keywords": [],
    "quality_flags": []
  },
  "attachment_card_layer": {
    "source": {
      "raw_repo_id": "",
      "source_id": "",
      "content_id": "",
      "raw_relative_path": "",
      "absolute_path_snapshot": ""
    },
    "metadata": {
      "file_uri": "",
      "filename": "",
      "extension": "",
      "size_bytes": 0,
      "created_at": "",
      "modified_at": ""
    },
    "attachments": []
  },
  "extraction_policy": {
    "default_read_scope": "summary_layer_only",
    "summary_layer_required_for_folder_summary": true,
    "read_attachment_card_layer_when": [
      "user_requests_original_file",
      "agent_needs_file_location",
      "agent_needs_version_or_size_metadata",
      "agent_needs_markmap_or_export_link"
    ],
    "do_not_use_attachment_card_layer_for_initial_routing": true
  }
}
```

### B. 字段说明

#### 1. `schema`

- `schema.octopus_schema`：Octopus 叶子索引结构版本号。本文档修改版建议使用 `"0.2"`。
- `schema.index_type`：索引类型。叶子索引固定为 `"leaf"`。
- `schema.json_role`：说明该 JSON 是统一机器头。建议固定为 `"unified_machine_header"`。

#### 2. `summary_layer`

`summary_layer` 是 Agent 初次检索、文件夹摘要聚合、搜索排序和上下文选择时应优先读取的部分。

它应当保持短小、稳定、可聚合，避免写入路径、文件大小、绝对位置等附件卡片信息。

- `name`：叶子索引名称，通常使用原始文件名或更适合人类识别的标题。
- `description`：简短描述，一般 1-3 句话，说明文件内容、用途和适用场景。
- `one_sentence_summary`：一句话摘要，应尽量压缩为一句完整判断，便于搜索结果展示和上级文件夹摘要引用。
- `document_type`：文档类型，例如 `"pdf"`、`"word"`、`"excel"`、`"image"`、`"ppt"`、`"markdown_with_assets"`、`"software_package"`。
- `languages`：文件涉及的自然语言或编程语言，例如 `["中文"]`、`["Python"]`、`["Markdown", "YAML"]`。
- `tag_rough`：粗粒度标签，例如 `["学习资料"]`、`["项目文档"]`、`["合同"]`、`["代码说明"]`。
- `topic_keywords`：主题关键词，供搜索与文件夹摘要聚合使用。
- `quality_flags`：质量提示，例如 `["OCR质量一般"]`、`["表格结构复杂"]`、`["公式识别不可靠"]`。

##### `description` 与 `one_sentence_summary` 标准
`description` 应回答“这是什么、主要有什么、适合什么时候用”。它可以包含 3-5 句话，可以视作文件的简要摘要，但不应是详细摘要，也不应包含完整目录。这里的内容会被文件夹摘要直接摘取聚合。
`one_sentence_summary` 应回答“如果只能留一句话，这个文件最值得被记住的是什么”。它必须是一句话，适合被文件夹摘要直接引用，可以视作比 `description` 更短、更精炼的摘要。

#### 3. `attachment_card_layer`

`attachment_card_layer` 是附件卡片层，用于定位原始文件和附件，不用于初始路由。

- `source.raw_repo_id`：原始仓库 ID，用来标识文件来自哪个 Raw Repository。
- `source.source_id`：原始文件 ID，用来标识具体文件。
- `source.content_id`：原始文件内容 ID，通常可存放内容哈希值，用于判断内容是否变化。
- `source.raw_relative_path`：原始文件相对路径，便于在仓库内部快速定位文件。
- `source.absolute_path_snapshot`：原始文件绝对路径快照，仅作本地定位辅助，不能作为唯一身份。
- `metadata.file_uri`：文件 URI，可用于统一定位本地文件、远程文件或对象存储中的文件。
- `metadata.filename`：文件名，例如 `"README.md"`。
- `metadata.extension`：文件扩展名，例如 `".md"`、`".pdf"`、`".xlsx"`。
- `metadata.size_bytes`：文件大小，单位为字节。
- `metadata.created_at`：文件创建时间，建议使用 ISO 8601 格式。
- `metadata.modified_at`：文件最后修改时间，建议使用 ISO 8601 格式。
- `attachments`：附件列表。若一个叶子索引对应多个相关文件、图片、外链、缩略图或 OCR 文件，可在此处记录。

`attachments` 推荐格式：

```json
[
  {
    "attachment_id": "",
    "role": "original",
    "file_uri": "",
    "raw_relative_path": "",
    "filename": "",
    "extension": "",
    "size_bytes": 0,
    "remarks": ""
  }
]
```

### C. 正文层

正文层使用 Markdown 格式，放在开头 JSON 之后，没有类似 JSON 的强制字段标准。

正文层应记录摘要层无法容纳、但对定位原始文件内容有帮助的信息，例如：

- 文件内容摘要；
- 文件结构；
- 章节、页码、Sheet、幻灯片、图片区域等内部索引；
- 提取质量；
- 重要锚点；
- 用户重点标记区域。

正文层推荐格式见下文“叶子索引正文层的格式推荐”。

### D. 可选扩展层

该层仅用于后续对叶子索引进行层次细化、局部补充或扩展处理。
这里只是一个预留结构空间。
没有扩展内容时可以省略。

### E. 维护层

维护层使用 Markdown 格式。
普通检索、文件夹摘要聚合和初始路由时不应读取维护层。只有在更新叶子索引时才读取维护层。
维护层建议格式如下：

```markdown
## 维护层

### 用户的自动化叶子索引建议与提示词

<!-- 用户可在此处写入下一次更新索引时希望智能体注意的事项。相关智能体不得删除或覆盖本区域。 -->

### 维护日志

- 2026-07-03：创建叶子索引。
```

维护层的关键要求：

- 用户写入的建议、提示词和重点标注不得被自动更新覆盖；
- 每次 AI 更新索引时，应在维护日志中追加记录；
- 不应把维护日志写入 `summary_layer`；
- 不应让维护层内容影响普通搜索排序，除非用户明确要求。

## 叶子索引正文层的格式推荐

在索引正文层中，没有一个如同 JSON 一样必须执行的代码标准。

正文层应像正常 Markdown 文件一样使用 `#`、`##`、`###` 等分级标题区分层次。

为了便于后续索引搜索聚合，建议正文层包含以下类型的信息。

### A. 摘要

这里的摘要相较于 `summary_layer.one_sentence_summary` 和 `summary_layer.description` 可以更长。

正文层摘要应根据附件内容的长度和复杂度来写，但仍应避免复制原文全文。

建议包括：

- 文件主题；
- 核心内容；
- 适合解决的问题；
- 与用户资料体系中其他内容的关系；
- 是否建议继续阅读该 Leaf 索引。

### B. 文件结构与内部索引

对于 PDF、书籍、讲义或扫描件，可精确到页码：

```markdown
* 《书名或文件名》
    ** 第一章：AAA
        *** 1.1 AAA-1
            **** P1：知识点1、知识点2
            **** P2：知识点1、知识点2
        *** 1.2 AAA-2
            **** P3：知识点3
```

对于 Excel，可说明：

- 每张 Sheet 的名称；
- 每张 Sheet 的用途；
- 关键行列；
- 是否存在公式、透视表、图表或隐藏表；
- 是否建议查看 Leaf 中的表格定位信息；只有重新生成 Leaf 时才只读解析原表。

对于 PPT，可说明：

- 幻灯片编号；
- 每页主题；
- 图表/图片/流程图所在页；
- 演示用途。

对于图片或扫描件，可说明：

- 图片内容；
- 可识别文字；
- 关键区域；
- OCR 可靠性；
- 是否需要在重新生成 Leaf 或人工校验时查看原图。

### C. 提取质量

如果原始文件是 PDF、Word、Excel、PPT、图片、扫描件、手写笔记或其他非纯文本文件，应评估提取质量。

示例：

```markdown
| 项目 | 结果 |
|---|---|
| 文本层 | 无原生文本，依赖 OCR |
| OCR 质量 | 中等 |
| 手写识别 | 可识别标题和部分中文，公式识别较弱 |
| 页码识别 | 可靠 |
| 表格识别 | 无 |
| 公式识别 | 不可靠 |
| 建议 | 涉及公式推导时应优先查看 Leaf 的定位信息；必要的人工原文件核验不属于 Octopus 日常 Agentic 检索流程 |
```

### D. 用户重点标记区域

此处内容拥有最高优先级。用户可以自行标注其他重要信息。

相关智能体不得删除、覆盖或擅自改写该区域。智能体在更新这个文件的叶子索引的时候还可以参考用户在这里写入的内容。

推荐格式：

```markdown
### 用户重点标记区域

<!-- 用户写入内容开始 -->

<!-- 用户写入内容结束 -->
```

## Agent 读取策略【生成输出结果】
1. 初始搜索阶段
   - 只读取开头 JSON 中的 `summary_layer`。
   - 不读取 `attachment_card_layer`。

2. 候选文件筛选阶段
   - 如果摘要层足以判断无关，则停止读取该叶子索引。
   - 如果摘要层显示可能相关，则继续读取正文层的摘要、结构地图和提取质量。
   - 仍不必读取附件卡片层，除非最终确定确实相关

3. 生成输出结果
    - 如果摘要层和正文层显示该 Leaf 值得纳入结果，则读取附件卡片层，获取用于 Markmap、导出或索引维护的链接、大小、版本等信息；普通任务流程仍不直接读取非文本型原文件。


