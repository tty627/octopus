# 文件夹节点格式（FolderNode）规范性文件 v1.0.1

## 文件夹节点的基本介绍

文件夹节点（FolderNode）是 Octopus 索引仓库中面向文件夹、目录层级或可被视作目录聚合单元的标准化 Markdown 索引文件。

它的作用不是复制文件夹内所有文件的内容，也不是替代叶子索引，而是在尽可能小的上下文开销下，告诉人和 Agent：

- 这个文件夹是什么；
- 它大致包含哪些文件、子文件夹和主题；
- 它在 Raw Repository 的目录树中处于什么位置；
- 它下面哪些内容值得继续展开；
- 哪些下级文件或文件夹应优先阅读；
- 哪些下级文件已经有叶子索引，哪些文本型文件需要直接摘要；
- 该文件夹是否可以被视作一个整体任务单元、资料包或最小叶片；
- 哪些内容是用户标注，哪些内容是 AI 生成。

文件夹节点应服务于 Octopus 的核心理念：以链接为中心，而不是以内容为中心。它应当保存 compact signals，即文件夹级元数据、目录树拓扑、下级节点摘要、聚合判断、阅读路径、质量提示、用户重点标记和必要链接，而不应把下级文件全文、完整 OCR、完整代码、完整表格或大量叶子索引正文直接塞入文件夹节点。

在 Octopus 中，叶子索引用于描述单个原始文件或最小内容单元；文件夹节点用于描述一个目录中下一级文件和子目录的聚合关系。FolderNode 是上层路由、聚合摘要、搜索剪枝、Markmap 输出和任务资料组合的关键入口。

## 文件夹节点与叶子索引的关系

### A. 基本分工

- 叶子索引（Leaf）：面向单个非文本型文件、复杂文件或最小内容单元，回答“这个文件是什么、是否值得打开、从哪里打开”。
- 文件夹节点（FolderNode）：面向一个文件夹，回答“这个文件夹里有什么、哪些下级节点值得继续看、目录结构如何组织”。
- 文本型文件：一般不强制生成叶子索引，FolderNode 应直接读取并摘要其 compact signals。
- 非文本型文件：一般应先生成叶子索引，FolderNode 再消费该 leaf 的 `summary_layer` 和必要正文摘要。
- 子文件夹：一般应先生成下级 FolderNode，再由上级 FolderNode 消费其 `summary_layer`。

### B. 生成顺序

文件夹节点应自底向上生成：

1. 先扫描 Raw Repository，得到真实文件树拓扑；
2. 对非文本型文件生成叶子索引；
3. 对最底层文件夹生成 FolderNode；
4. 向上逐层聚合，直到 Raw Repository 根目录；
5. 上级 FolderNode 只消费下一级文件、叶子索引和下级 FolderNode 的摘要信息，避免递归读取全部正文。

### C. 聚合原则

FolderNode 的默认聚合对象是“下一级节点”，而不是递归展开后的全部原文。

下一级节点包括：

- 直接子文件；
- 直接子文件夹；
- 直接子文件对应的叶子索引；
- 直接子文件夹对应的 FolderNode；
- 可被设置为最小叶片的软件包、项目目录或封装资料夹。

递归文件树可以保存在正文层的“目录树拓扑”中，但摘要层不应塞入完整树。

## 文件夹节点的五层文件架构

文件夹节点文件整体采用「一个开头 JSON + Markdown 正文」的结构，与叶子索引保持基本格式通用。

开头 JSON 是唯一的机器可读 JSON 区块，统一承载“摘要层”“目录卡片层”“聚合策略”等信息。为了便于 Agent 分阶段读取，该 JSON 内部必须明确区分：

- `summary_layer`：文件夹摘要层，供搜索、路由、上级文件夹聚合和 Agent 初筛使用；
- `folder_card_layer`：文件夹卡片层，供 Agent 需要定位文件夹、查看目录元数据、判断文件数量或读取路径时使用；
- `children_summary_layer`：下级节点摘要层，供 Agent 在不展开正文的情况下初步判断下级节点；
- `aggregation_policy`：聚合与读取策略，说明该 FolderNode 如何消费 leaf、文本文件和子 FolderNode；
- `extraction_policy`：分阶段读取策略，说明默认读取范围和何时读取更深层信息。

五层架构如下：

1. 摘要层
   - 位于开头唯一 JSON 的 `summary_layer` 字段中。
   - 用于快速扫描、搜索、路由、排序和上级文件夹摘要消费。
   - 只保存对任务选择有帮助的短摘要、目录类型、标签、主题、价值判断和展开建议。

2. 文件夹卡片层
   - 位于开头唯一 JSON 的 `folder_card_layer` 字段中。
   - 用于记录 Raw Repository 来源、文件夹路径、URI、文件夹名、创建/修改时间、直接子项数量、递归子项数量、内容快照 ID 等信息。
   - 普通搜索和上级聚合阶段默认不读取该层；只有在需要定位文件夹或判断目录状态时才读取。

3. 下级节点摘要层
   - 位于开头唯一 JSON 的 `children_summary_layer` 字段中。
   - 用于保存直接下级文件、叶子索引和子文件夹的短摘要清单。
   - 只保存下级节点的 compact signals，不复制下级 leaf 或 FolderNode 的完整正文。

4. 正文层
   - 使用 Markdown 格式，放在开头 JSON 之后。
   - 记录文件夹级摘要、目录树拓扑、下级节点说明、阅读路径、聚合判断、质量提示和任务使用建议。
   - 正文层可以比 JSON 更详细，但仍应避免复制下级文件全文。

5. 维护层
   - 使用 Markdown 格式。
   - 普通检索、摘要提取和初始路由时不需要读取。
   - 只有在更新文件夹节点时才读取。
   - 必须保留用户提示、用户建议、用户重点标记和维护日志，不得在自动更新时覆盖用户标注。

## 文件夹节点的具体格式

### A. 机器可读层：唯一开头 JSON

推荐格式如下：

```json
{
  "schema": {
    "octopus_schema": "0.2",
    "index_type": "foldernode",
    "json_role": "unified_machine_header"
  },
  "summary_layer": {
    "name": "",
    "one_sentence_summary": "",
    "description": "",
    "folder_type": "",
    "languages": [],
    "tag_rough": [],
    "topic_keywords": [],
    "scope_boundary": "",
    "open_folder_recommendation": "",
    "why_open_folder": "",
    "recommended_entry_nodes": [],
    "quality_flags": []
  },
  "folder_card_layer": {
    "source": {
      "raw_repo_id": "",
      "folder_id": "",
      "content_snapshot_id": "",
      "raw_relative_path": "",
      "absolute_path_snapshot": ""
    },
    "metadata": {
      "folder_uri": "",
      "folder_name": "",
      "created_at": "",
      "modified_at": "",
      "direct_file_count": 0,
      "direct_folder_count": 0,
      "recursive_file_count": 0,
      "recursive_folder_count": 0,
      "total_size_bytes_estimate": 0
    },
    "links": {
      "raw_folder_link": "",
      "index_folder_link": "",
      "parent_foldernode": "",
      "child_foldernodes": [],
      "child_leaf_indexes": []
    }
  },
  "children_summary_layer": {
    "direct_children": [],
    "notable_children": [],
    "text_files_without_leaf": [],
    "non_text_files_with_leaf": [],
    "subfolders_with_foldernode": [],
    "opaque_leaf_folders": []
  },
  "aggregation_policy": {
    "generation_order": "bottom_up",
    "default_child_read_scope": "child_summary_layer_only",
    "consume_leaf_fields": [
      "summary_layer.name",
      "summary_layer.one_sentence_summary",
      "summary_layer.description",
      "summary_layer.document_type",
      "summary_layer.tag_rough",
      "summary_layer.topic_keywords",
      "summary_layer.quality_flags"
    ],
    "consume_foldernode_fields": [
      "summary_layer",
      "children_summary_layer.notable_children"
    ],
    "text_file_handling": "summarize_directly_without_leaf_when_plain_text",
    "non_text_file_handling": "consume_existing_leaf_before_folder_summary",
    "do_not_copy_child_fulltext": true
  },
  "extraction_policy": {
    "default_read_scope": "summary_layer_and_children_summary_layer",
    "read_folder_card_layer_when": [
      "user_requests_original_folder",
      "agent_needs_folder_location",
      "agent_needs_file_count_or_size_metadata",
      "agent_needs_to_open_or_export_links"
    ],
    "read_markdown_body_when": [
      "summary_layer_indicates_possible_relevance",
      "agent_needs_directory_tree",
      "agent_needs_recommended_reading_path",
      "agent_needs_child_node_rationale"
    ],
    "do_not_use_folder_card_layer_for_initial_routing": true
  }
}
```

### B. 字段说明

#### 1. `schema`

- `schema.octopus_schema`：Octopus 文件夹节点结构版本号。本文档建议使用 `"0.2"`，与当前叶子索引规范保持兼容。
- `schema.index_type`：索引类型。文件夹节点固定为 `"foldernode"`。
- `schema.json_role`：说明该 JSON 是统一机器头。建议固定为 `"unified_machine_header"`。

#### 2. `summary_layer`

`summary_layer` 是 Agent 初次检索、上级文件夹摘要聚合、搜索排序和上下文选择时应优先读取的部分。

它应保持短小、稳定、可聚合，避免写入完整路径、完整目录树、文件大小明细和大量下级节点正文。

- `name`：文件夹节点名称，通常使用原始文件夹名或更适合人类识别的标题。
- `one_sentence_summary`：一句话摘要，说明该文件夹最值得记住的核心内容。
- `description`：简短描述，一般 2-5 句话，说明文件夹内容范围、主要材料类型、适用场景和组织方式。
- `folder_type`：文件夹类型，例如 `"project_folder"`、`"learning_materials"`、`"reference_collection"`、`"software_package"`、`"mixed_archive"`、`"index_specification"`。
- `languages`：文件夹内主要涉及的自然语言或编程语言，例如 `["中文"]`、`["Markdown"]`、`["Python", "Markdown"]`。
- `tag_rough`：粗粒度标签，例如 `["项目文档"]`、`["学习资料"]`、`["规范文件"]`、`["代码项目"]`。
- `topic_keywords`：主题关键词，供搜索与上级文件夹摘要聚合使用。
- `scope_boundary`：范围边界，说明该文件夹覆盖什么、不覆盖什么，避免 Agent 误把相邻目录内容混入。
- `open_folder_recommendation`：是否建议继续展开该文件夹，可使用 `"high"`、`"medium"`、`"low"`、`"not_needed"`。
- `why_open_folder`：解释为什么值得或不值得继续展开。
- `recommended_entry_nodes`：建议优先读取的下级节点，保存短名称、节点类型和理由，不放完整路径明细。
- `quality_flags`：质量提示，例如 `["存在文本文件未生成leaf"]`、`["下级摘要不完整"]`、`["包含废弃方案应排除"]`、`["目录较深需注意路径长度"]`。

##### `description` 与 `one_sentence_summary` 标准

`description` 应回答“这个文件夹是什么、主要包含什么、适合什么时候打开、与上级/下级内容是什么关系”。它可以包含 2-5 句话，但不应变成完整目录清单。

`one_sentence_summary` 应回答“如果只能留一句话，这个文件夹最值得被记住的是什么”。它必须是一句话，适合被上级 FolderNode 或搜索结果直接引用。

#### 3. `folder_card_layer`

`folder_card_layer` 是文件夹卡片层，用于定位原始文件夹和索引文件夹，不用于初始路由。

- `source.raw_repo_id`：原始仓库 ID，用来标识文件夹来自哪个 Raw Repository。
- `source.folder_id`：文件夹 ID，用来标识具体文件夹，可由路径、仓库 ID 或稳定规则生成。
- `source.content_snapshot_id`：目录快照 ID，可由目录结构、文件名、大小、修改时间或内容哈希聚合生成，用于判断文件夹是否变化。
- `source.raw_relative_path`：原始文件夹相对路径，便于在仓库内部快速定位。
- `source.absolute_path_snapshot`：原始文件夹绝对路径快照，仅作本地定位辅助，不能作为唯一身份。
- `metadata.folder_uri`：文件夹 URI，可用于统一定位本地文件夹、远程文件夹或对象存储前缀。
- `metadata.folder_name`：文件夹名。
- `metadata.created_at`：文件夹创建时间，建议使用 ISO 8601 格式。
- `metadata.modified_at`：文件夹最后修改时间，建议使用 ISO 8601 格式。
- `metadata.direct_file_count`：直接子文件数量。
- `metadata.direct_folder_count`：直接子文件夹数量。
- `metadata.recursive_file_count`：递归文件数量。
- `metadata.recursive_folder_count`：递归文件夹数量。
- `metadata.total_size_bytes_estimate`：文件夹总大小估计，单位为字节；无法可靠计算时可为 `0` 或省略。
- `links.raw_folder_link`：指向 Raw Repository 中原始文件夹的链接。
- `links.index_folder_link`：指向 Index Repository 中对应索引文件夹的链接。
- `links.parent_foldernode`：父级 FolderNode 链接。
- `links.child_foldernodes`：直接子文件夹 FolderNode 链接列表。
- `links.child_leaf_indexes`：直接子文件 leaf 链接列表。

#### 4. `children_summary_layer`

`children_summary_layer` 用于在机器头中保留下级节点的轻量清单。它是 FolderNode 区别于 Leaf 的关键层。

该层只保存直接下级节点的 compact signals，避免递归塞入全部下级正文。

推荐的 `direct_children` 条目格式：

```json
{
  "child_id": "",
  "name": "",
  "node_type": "file|folder|leaf|foldernode|opaque_leaf_folder",
  "relative_name_or_path": "",
  "one_sentence_summary": "",
  "document_or_folder_type": "",
  "tag_rough": [],
  "topic_keywords": [],
  "open_recommendation": "",
  "index_link": "",
  "source_link_available": true,
  "quality_flags": []
}
```

各字段含义：

- `child_id`：下级节点 ID，可由父级路径和节点名生成。
- `name`：下级节点名称。
- `node_type`：下级节点类型。
  - `"file"`：普通文本型文件，未生成 leaf，由 FolderNode 直接摘要。
  - `"leaf"`：非文本型文件或复杂文件对应的叶子索引。
  - `"foldernode"`：子文件夹对应的文件夹节点。
  - `"opaque_leaf_folder"`：被视作最小叶片、不再向下拆分的文件夹。
- `relative_name_or_path`：相对名称或短相对路径，只用于区分同名节点，不应替代 folder_card_layer 中的定位信息。
- `one_sentence_summary`：下级节点一句话摘要。
- `document_or_folder_type`：下级节点的文件类型或文件夹类型。
- `tag_rough`：下级节点粗粒度标签。
- `topic_keywords`：下级节点主题关键词。
- `open_recommendation`：是否建议展开该下级节点。
- `index_link`：对应 leaf 或 FolderNode 的索引链接；文本文件无 leaf 时可为空。
- `source_link_available`：是否存在可打开的原始文件或原始文件夹链接。
- `quality_flags`：下级节点质量提示。

`children_summary_layer` 下的辅助数组用途：

- `notable_children`：当前文件夹中最值得优先查看的下级节点。
- `text_files_without_leaf`：文本型文件清单，这些文件通常不生成 leaf，FolderNode 应直接摘要。
- `non_text_files_with_leaf`：非文本型文件及其 leaf 清单。
- `subfolders_with_foldernode`：子文件夹及其 FolderNode 清单。
- `opaque_leaf_folders`：被视作最小叶片的文件夹，例如完整软件包、封装工具、不可拆分项目目录。

#### 5. `aggregation_policy`

`aggregation_policy` 用于描述 FolderNode 的生成和聚合策略。

- `generation_order`：推荐固定为 `"bottom_up"`，表示从底层文件夹向上聚合。
- `default_child_read_scope`：默认读取下级节点的范围，建议为 `"child_summary_layer_only"`。
- `consume_leaf_fields`：生成 FolderNode 时默认从 leaf 中读取的字段。
- `consume_foldernode_fields`：生成上级 FolderNode 时默认从下级 FolderNode 中读取的字段。
- `text_file_handling`：文本文件处理方式，建议为 `"summarize_directly_without_leaf_when_plain_text"`。
- `non_text_file_handling`：非文本型文件处理方式，建议为 `"consume_existing_leaf_before_folder_summary"`。
- `do_not_copy_child_fulltext`：必须为 `true`，避免 FolderNode 退化成全文仓库。

#### 6. `extraction_policy`

`extraction_policy` 用于规定 Agent 在使用 FolderNode 时的分阶段读取方式。

- `default_read_scope`：默认读取范围，建议为 `"summary_layer_and_children_summary_layer"`。
- `read_folder_card_layer_when`：只有需要定位文件夹、打开链接、统计大小或导出路径时才读取文件夹卡片层。
- `read_markdown_body_when`：当摘要层显示可能相关、需要目录树、需要阅读路径或需要下级节点理由时才读取正文层。
- `do_not_use_folder_card_layer_for_initial_routing`：必须为 `true`，避免路径和元数据污染初始搜索排序。

## 正文层格式推荐

文件夹节点正文层使用 Markdown 格式，放在开头 JSON 之后。

正文层没有像 JSON 一样必须执行的代码字段标准，但建议包含以下部分。

### A. 文件夹摘要

这里的摘要可以比 `summary_layer.one_sentence_summary` 和 `summary_layer.description` 更详细。

建议包括：

- 文件夹主题；
- 核心内容范围；
- 主要下级节点类型；
- 与上级或同级资料的关系；
- 是否建议展开；
- 建议从哪些下级节点开始阅读；
- 是否存在废弃内容、重复内容、缺失 leaf 或不应拆分的目录。

### B. 目录树拓扑

目录树拓扑应描述 Raw Repository 的真实目录结构，而不是 Index Repository 的派生结构。

推荐格式：

````markdown
```text
Raw Repository: <raw_repo_id>
<folder_name>/
├─ file_a.md
├─ file_b.pdf -> leaf: file_b.pdf的叶子索引.md
├─ child_folder/
│  └─ child_folder文件夹的FolderNode.md
└─ software_package/ [opaque_leaf_folder]
```
````

目录树拓扑的要求：

- 应保留文件夹层级；
- 应标明非文本型文件对应的 leaf；
- 应标明子文件夹对应的 FolderNode；
- 应标明被视作最小叶片的文件夹；
- 不应把文件正文嵌入目录树；
- 目录过深时可以截断，但必须说明截断策略。

### C. 下级节点摘要表

建议用表格保存直接下级节点的摘要，便于人类阅读和 Agent 二次筛选。

推荐格式：

```markdown
| 下级节点 | 类型 | 一句话摘要 | 建议动作 | 索引状态 | 质量提示 |
|---|---|---|---|---|---|
| README.md | text_file | 项目说明文本。 | 可直接读取 | 无 leaf，文本直读 | 无 |
| notes.pdf | leaf | 某主题的 PDF 笔记。 | 需要时打开 leaf | 已有 leaf | OCR 一般 |
| assets/ | foldernode | 图片附件目录。 | 低优先级展开 | 已有 FolderNode | 内容零散 |
```

字段说明：

- `下级节点`：直接下级文件或文件夹名称。
- `类型`：`text_file`、`leaf`、`foldernode`、`opaque_leaf_folder` 等。
- `一句话摘要`：下级节点最核心的信息。
- `建议动作`：继续展开、读取 leaf、直接读取文本、忽略、仅定位等。
- `索引状态`：是否已有 leaf、是否已有 FolderNode、是否文本直读、是否缺失索引。
- `质量提示`：OCR、结构、路径、重复、废弃、权限等提示。

### D. 推荐阅读路径

推荐阅读路径用于告诉 Agent 在任务不明确或需要快速了解文件夹时，应按什么顺序展开。

推荐格式：

```markdown
1. 先读 `需求文档.md`，建立项目目标和术语。
2. 再读 `叶子索引格式规范性文件.md`，理解 leaf 的机器头与正文层格式。
3. 最后根据任务需要展开 `示例资料/` 或 `实现方案/`。
```

推荐阅读路径应体现：

- 先读总纲，再读细节；
- 先读规范，再读实例；
- 先读 FolderNode，再读 leaf；
- 只在生成或更新 Leaf 时只读解析原始非文本型文件；日常使用只读取 Leaf 与 FolderNode；
- 避免默认展开废弃目录。

### E. 聚合判断与边界

该部分用于说明当前文件夹为什么被这样总结，以及哪些内容被排除。

建议包括：

- 本 FolderNode 的覆盖范围；
- 被明确排除的目录或文件，例如 `废弃方案存放地/`；
- 文本型文件是否直接摘要；
- 非文本型文件是否已依赖 leaf；
- 是否存在作为整体处理的软件包或项目目录；
- 是否存在路径过深、文件过多、命名混乱、重复版本等风险。

### F. 质量评估

FolderNode 应评估的是“文件夹级索引质量”，而不是单个非文本文件的 OCR 质量。

示例：

```markdown
| 项目 | 结果 |
|---|---|
| 目录扫描完整性 | 已扫描直接子项与递归结构 |
| leaf 覆盖情况 | 非文本型文件均已有 leaf |
| 文本文件处理 | 纯文本文件由 FolderNode 直接摘要 |
| 子文件夹覆盖情况 | 子文件夹均已有 FolderNode |
| 废弃内容处理 | 已排除 `废弃方案存放地/` |
| 路径风险 | 暂无过深路径 |
| 建议 | 可作为上级聚合与搜索入口 |
```

### G. 用户重点标记区域

此处内容拥有最高优先级。用户可以自行标注其他重要信息。

相关智能体不得删除、覆盖或擅自改写该区域。智能体在更新文件夹节点时可以参考用户在这里写入的内容。

推荐格式：

```markdown
### 用户重点标记区域

<!-- 用户写入内容开始 -->

<!-- 用户写入内容结束 -->
```

## 文本型文件与非文本型文件的处理规则

### A. 文本型文件

纯文本文件通常不需要生成 leaf。

包括但不限于：

- `.txt`
- `.md`
- `.py`
- `.js`
- `.ts`
- `.json`
- `.yaml`
- `.toml`
- `.csv`（内容较简单时）
- 其他可被直接稳定读取的源码或配置文件

FolderNode 对文本型文件的责任是：

- 直接读取文本文件内容；
- 提取短摘要、主题词、文件用途和质量提示；
- 把摘要写入 `children_summary_layer.text_files_without_leaf` 与正文层下级节点摘要表；
- 不复制完整文本；
- 对长文本只保留结构摘要和关键锚点。

### B. 非文本型文件

非文本型文件一般需要先生成 leaf，再由 FolderNode 消费 leaf。

包括但不限于：

- PDF；
- Word；
- Excel；
- PowerPoint；
- 图片；
- 扫描件；
- 音视频；
- 压缩包；
- 数据库文件；
- Markdown + 图片附件形成的复合资料；
- 任何需要特殊解析、OCR、渲染或人工确认的文件。

FolderNode 对非文本型文件的责任是：

- 不直接复制非文本型文件内容；
- 优先读取对应 leaf 的 `summary_layer`；
- 必要时读取 leaf 正文层的摘要、结构地图和质量评估；
- 把 leaf 的 compact signals 聚合进 `children_summary_layer.non_text_files_with_leaf`；
- 在正文层说明是否建议进一步阅读 Leaf 索引。

### C. 可作为最小叶片的文件夹

某些文件夹虽然在文件系统上是目录，但在语义上应视作不可拆分的整体。

例如：

- 完整软件包；
- 独立 Git 仓库；
- CLI 工具源码包；
- 带固定内部结构的导出包；
- 用户指定“不往下拆”的资料包；
- 深度过大且不适合逐层索引的目录。

这类目录可被标记为 `opaque_leaf_folder`。

处理规则：

- 可以在 FolderNode 中把该目录列为最小叶片；
- 可以为该目录生成一个 leaf，`document_type` 或 `folder_type` 标记为 `"software_package"`、`"archive_folder"` 或 `"opaque_leaf_folder"`；
- 不默认递归拆分内部文件；
- 若用户任务需要代码审查、运行、修改或深入分析，再进入该目录。

## Agent 读取策略

### 1. 初始搜索阶段

Agent 默认只读取：

- `summary_layer`
- `children_summary_layer.notable_children`
- 必要时读取 `children_summary_layer.direct_children` 的短摘要

Agent 不应在初始搜索阶段读取：

- `folder_card_layer`
- 完整正文层
- 下级 leaf 正文
- 下级原始文件内容
- 维护层

### 2. 候选文件夹筛选阶段

如果 `summary_layer` 足以判断该文件夹无关，则停止读取该 FolderNode。

如果 `summary_layer` 显示可能相关，则继续读取：

- 正文层的文件夹摘要；
- 下级节点摘要表；
- 推荐阅读路径；
- 聚合判断与边界。

此时仍不必读取 `folder_card_layer`，除非已经需要定位原始文件夹或生成可点击链接。

### 3. 下级节点展开阶段

当需要继续展开时：

- 对文本型文件：可直接读取原文本，但应按任务需要截取；
- 对非文本型文件：先读取对应 leaf；
- 对子文件夹：先读取对应 FolderNode；
- 对 `opaque_leaf_folder`：除非任务明确需要，否则不继续递归展开。

### 4. 生成输出结果阶段

当需要给用户输出可点击链接、Markmap、文件包、引用路径或索引阅读建议时，才读取：

- `folder_card_layer.links`
- 下级 leaf 的 `attachment_card_layer`
- Leaf、FolderNode 或必要来源位置链接
- 必要的文件大小、时间、版本信息

请注意：该阶段仍只使用 Leaf 与 FolderNode 作为日常阅读对象；非文本型原文件只在生成或更新 Leaf 时被只读解析，不作为普通检索和 Markmap 输出的阅读来源。

### 5. 更新索引阶段

当重新生成或更新 FolderNode 时，必须读取并保留：

- 用户重点标记区域；
- 用户的自动化文件夹节点建议与提示词；
- 维护日志；
- 用户手工修改过的阅读建议或边界说明。

## 生成 FolderNode 的推荐流程

### STEP 01：确认范围

确认当前 FolderNode 对应的 Raw Repository 文件夹范围。

需要记录：

- Raw Repository ID；
- 当前文件夹相对路径；
- 是否排除废弃目录；
- 是否排除 `.git`、缓存、构建产物、临时文件；
- 是否存在用户指定的最小叶片文件夹。

### STEP 02：扫描直接子项

扫描当前文件夹的直接子文件和直接子文件夹。

需要区分：

- 文本型文件；
- 非文本型文件；
- 子文件夹；
- 废弃目录；
- 隐藏目录；
- 可作为最小叶片的目录。

### STEP 03：消费下级索引

对于不同下级节点使用不同来源：

- 文本型文件：直接读取文本并摘要；
- 非文本型文件：读取已有 leaf 的 `summary_layer`；
- 子文件夹：读取下级 FolderNode 的 `summary_layer`；
- 没有 leaf 的非文本型文件：标记质量提示，必要时先补 leaf；
- 没有 FolderNode 的子文件夹：标记质量提示，必要时先生成下级 FolderNode。

### STEP 04：生成机器头 JSON

生成唯一开头 JSON，至少包括：

- `schema`
- `summary_layer`
- `folder_card_layer`
- `children_summary_layer`
- `aggregation_policy`
- `extraction_policy`

JSON 必须保持合法、可解析、无注释。

### STEP 05：生成正文层

正文层至少建议包括：

- 文件夹摘要；
- 目录树拓扑；
- 下级节点摘要表；
- 推荐阅读路径；
- 聚合判断与边界；
- 质量评估；
- 用户重点标记区域。

### STEP 06：生成维护层

维护层至少包括：

- 用户的自动化文件夹节点建议与提示词；
- 维护日志。

维护层不得进入普通检索排序，除非用户明确要求。

## 与 Markmap 输出的关系

FolderNode 是生成 Markmap 的重要中间结构。Octopus MVP 的最小可用有效输出是符合 Markmap 输入规范、并能通过 Markmap 开源包自动转换为带链接 mindmap 的 Markdown。

用于 Markmap 时，FolderNode 应提供：

- 文件夹层级；
- 下级节点标题；
- 下级节点一句话摘要；
- 可点击的 Leaf 或 FolderNode 索引链接；
- 推荐阅读顺序；
- 不建议展开或已废弃的节点提示。

Markmap 输出不应直接使用 Raw Repository 的完整文件内容，而应优先使用 FolderNode 与 Leaf 中的 compact signals。

推荐 Markmap 生成顺序：

1. 根 FolderNode 作为思维导图根节点；
2. 直接子 FolderNode 或 leaf 作为第二层节点；
3. 只展开与任务相关的分支；
4. 非文本型文件链接到 Leaf 索引，不直接链接为日常阅读原文件；
5. 文本型文件可链接到原始文本文件或对应索引锚点；
6. 废弃目录默认隐藏或低优先级展示。

## 文件夹节点的关键原则

1. FolderNode 是路由和聚合，不是全文仓库

不要把下级文件全文、完整 OCR、完整代码库或完整 leaf 正文全部塞进 FolderNode。

2. 默认只存 compact signals

即文件夹摘要、下级节点摘要、目录树拓扑、推荐阅读路径、质量提示和必要链接。

3. 自底向上生成

先生成 leaf 和底层 FolderNode，再聚合到上级 FolderNode，避免上级节点直接递归读取所有原始文件。

4. 直接下级优先

FolderNode 的主要摘要对象是直接下级节点；递归结构只作为目录树拓扑存在，不应替代下级 FolderNode。

5. 文本文件可直接摘要，非文本文件优先消费 leaf

纯文本文件通常不生成 leaf；非文本型文件应先有 leaf，再被 FolderNode 聚合。

6. 用户标注必须和 AI 生成内容分离

重新生成文件夹节点时，用户标注、用户建议和维护日志不能被覆盖。

7. 每个 FolderNode 都要能被上级 FolderNode 消费

主题、范围、下级摘要、价值判断、展开建议和质量提示应结构化，并优先放入 `summary_layer` 与 `children_summary_layer`。

8. 每个 FolderNode 都要告诉 Agent 是否值得继续展开

这是节省 token 和避免上下文污染的核心。对应字段是 `summary_layer.open_folder_recommendation` 与 `summary_layer.why_open_folder`。

9. 文件夹卡片层不得污染初始路由

路径、大小、绝对路径快照、URI 和链接列表等信息应放在 `folder_card_layer`，初始搜索和上级聚合阶段默认不读取。

10. 唯一 JSON 原则

文件夹节点文件只保留一个强制机器可读 JSON，并放在文件开头。

11. 可以把文件夹视作最小叶片

当一个目录是完整软件包、独立 Git 仓库、封装工具或用户指定不可拆分资料包时，可以标记为 `opaque_leaf_folder`，避免错误拆散。

12. 废弃内容应被明确排除或低优先级处理

如果目录中存在废弃方案、历史草案、缓存或构建产物，FolderNode 应在聚合判断中说明排除策略，避免污染当前有效索引。

## 文件命名建议

FolderNode 文件命名应让人能直观看出它对应哪个文件夹。

推荐命名：

```text
<文件夹名>文件夹的FolderNode索引总结.md
```

或：

```text
<文件夹名>文件夹的Markdown索引总结.md
```

对于根目录，可使用：

```text
Raw Repository根目录的FolderNode索引总结.md
```

本文档作为规范文件，使用：

```text
文件夹节点格式（FolderNode）规范性文件 v1.0.1.md
```

## 示例：最小 FolderNode 模板

````markdown
{
  "schema": {
    "octopus_schema": "0.2",
    "index_type": "foldernode",
    "json_role": "unified_machine_header"
  },
  "summary_layer": {
    "name": "test",
    "one_sentence_summary": "该文件夹聚合了 test 目录下的文本文件、非文本文件 leaf 和子文件夹 FolderNode。",
    "description": "这是 test 文件夹的标准化 FolderNode，用于描述直接下级文件与子文件夹的摘要、索引状态和推荐阅读路径。非文本型文件通过 leaf 聚合，文本型文件由本节点直接摘要。",
    "folder_type": "mixed_archive",
    "languages": ["中文"],
    "tag_rough": ["文件夹索引"],
    "topic_keywords": ["Octopus", "FolderNode", "目录摘要"],
    "scope_boundary": "仅覆盖 test 文件夹及其下级节点，不包含索引仓库自身生成物。",
    "open_folder_recommendation": "medium",
    "why_open_folder": "当任务需要了解 test 目录内容或继续定位下级资料时值得展开。",
    "recommended_entry_nodes": [],
    "quality_flags": []
  },
  "folder_card_layer": {
    "source": {
      "raw_repo_id": "raw-default",
      "folder_id": "raw-default:test",
      "content_snapshot_id": "",
      "raw_relative_path": "test",
      "absolute_path_snapshot": ""
    },
    "metadata": {
      "folder_uri": "",
      "folder_name": "test",
      "created_at": "",
      "modified_at": "",
      "direct_file_count": 0,
      "direct_folder_count": 0,
      "recursive_file_count": 0,
      "recursive_folder_count": 0,
      "total_size_bytes_estimate": 0
    },
    "links": {
      "raw_folder_link": "",
      "index_folder_link": "",
      "parent_foldernode": "",
      "child_foldernodes": [],
      "child_leaf_indexes": []
    }
  },
  "children_summary_layer": {
    "direct_children": [],
    "notable_children": [],
    "text_files_without_leaf": [],
    "non_text_files_with_leaf": [],
    "subfolders_with_foldernode": [],
    "opaque_leaf_folders": []
  },
  "aggregation_policy": {
    "generation_order": "bottom_up",
    "default_child_read_scope": "child_summary_layer_only",
    "consume_leaf_fields": [
      "summary_layer.name",
      "summary_layer.one_sentence_summary",
      "summary_layer.description",
      "summary_layer.document_type",
      "summary_layer.tag_rough",
      "summary_layer.topic_keywords",
      "summary_layer.quality_flags"
    ],
    "consume_foldernode_fields": [
      "summary_layer",
      "children_summary_layer.notable_children"
    ],
    "text_file_handling": "summarize_directly_without_leaf_when_plain_text",
    "non_text_file_handling": "consume_existing_leaf_before_folder_summary",
    "do_not_copy_child_fulltext": true
  },
  "extraction_policy": {
    "default_read_scope": "summary_layer_and_children_summary_layer",
    "read_folder_card_layer_when": [
      "user_requests_original_folder",
      "agent_needs_folder_location",
      "agent_needs_file_count_or_size_metadata",
      "agent_needs_to_open_or_export_links"
    ],
    "read_markdown_body_when": [
      "summary_layer_indicates_possible_relevance",
      "agent_needs_directory_tree",
      "agent_needs_recommended_reading_path",
      "agent_needs_child_node_rationale"
    ],
    "do_not_use_folder_card_layer_for_initial_routing": true
  }
}

# test 文件夹的 FolderNode 索引总结

## 文件夹摘要

本文件夹节点用于聚合 test 文件夹内直接下级文件和子文件夹的摘要信息。

## 目录树拓扑

```text
test/
```

## 下级节点摘要表

| 下级节点 | 类型 | 一句话摘要 | 建议动作 | 索引状态 | 质量提示 |
|---|---|---|---|---|---|

## 推荐阅读路径

1. 先查看本 FolderNode 的文件夹摘要和下级节点摘要表。
2. 如任务相关，再展开对应 leaf 或子 FolderNode。

## 聚合判断与边界

本节点仅覆盖 test 文件夹，不覆盖索引仓库自身生成物。

## 质量评估

| 项目 | 结果 |
|---|---|
| 目录扫描完整性 | 待填写 |
| leaf 覆盖情况 | 待填写 |
| 文本文件处理 | 待填写 |
| 子文件夹覆盖情况 | 待填写 |

### 用户重点标记区域

<!-- 用户写入内容开始 -->

<!-- 用户写入内容结束 -->

## 维护层

### 用户的自动化文件夹节点建议与提示词

<!-- 用户可在此处写入下一次更新文件夹节点时希望智能体注意的事项。相关智能体不得删除或覆盖本区域。 -->

### 维护日志

- 2026-07-03：创建文件夹节点。
```
````

## 维护层

### 用户的自动化文件夹节点建议与提示词

<!-- 用户可在此处写入下一次更新本规范时希望智能体注意的事项。相关智能体不得删除或覆盖本区域。 -->

### 维护日志

- 2026-07-03：创建文件夹节点格式规范，参考 `叶子索引格式（Leaf）规范性文件 v1.0.1.md` 与 Octopus 需求文档中的文件夹摘要设想，补全 FolderNode 的机器头、正文层、聚合策略、文本/非文本处理规则与读取策略。
- 2026-07-04：升级为 v1.0.1，移除 FolderNode 对原文件打开建议字段的消费；明确日常检索只读取 Leaf 与 FolderNode，非文本型原文件只在生成或更新 Leaf 时被只读解析；强化 Markmap 作为 MVP 最小有效输出。
