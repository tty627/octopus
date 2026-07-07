# Octopus Index Repository 自动更新方案规范性文件 v1.0.1

## 1. 文档定位

本文档规定 Octopus 对指定 Raw Repository 所对应 Index Repository 的自动更新机制。本文档版本统一为 v1.0.1。

该机制用于在不修改 Raw Repository 原始文件的前提下，定期扫描原始仓库，识别文件和文件夹变化，并按 Octopus 现有 Leaf 与 FolderNode 规范，自动维护索引仓库中的叶子索引、文件夹节点索引、仓库状态清单和维护日志。

本文档应与以下规范配合使用：

- `Octopus-面向普通用户的以链接为核心的文件管理基础设施【需求文档】 v1.0.1.md`
- `叶子索引格式（Leaf）规范性文件 v1.0.1.md`
- `文件夹节点格式（FolderNode）规范性文件 v1.0.1.md`

自动更新机制不是 RAG 数据库同步机制，也不是全文复制机制。它的核心任务是维护一套轻量、可追踪、可增量更新、可由人和 Agent 共同读取的 Markdown 索引层。Octopus MVP 是内置 Agentic 能力的索引器 CLI，优先保证 DeepSeek API 调用、CLI 索引生成、索引更新、索引检索和 Markmap 输出可用。

## 2. 核心目标

Octopus 自动更新机制应实现以下目标：

1. 定期发现 Raw Repository 的新增、修改、删除、移动、重命名和目录结构变化。
2. 只在文件内容稳定后才生成或更新索引，避免对用户正在编辑的半成品文件消耗 token。
3. 对非文本型文件优先维护 Leaf；对文件夹节点按自底向上的顺序维护 FolderNode。
4. Leaf 更新完成后，必须把直接父 FolderNode 标记为 dirty，并逐级向上标记所有祖先 FolderNode。
5. 统一使用本文档状态机中的全部状态：`unknown`、`clean`、`dirty`、`editing`、`pending_edit`、`pending_stable`、`queued`、`indexing`、`indexed`、`failed`、`retry`、`ignored`、`deleted`、`moved`、`stale`、`orphaned`。
6. 尽量通过固定程序、JSON 解析、文件元数据和 manifest 对比完成更新判断，最小化 AI/API 参与。
7. 对需要 AI 总结的部分采用节流、批处理和逐层降频策略，避免父级 FolderNode 因频繁细小变化反复调用模型。
8. 保留用户重点标记区域、维护层、用户自动化建议与提示词，不得在自动更新中覆盖用户手工内容。
9. 对失败、锁定、权限不足、路径过长、JSON 非法等情况保留旧索引并记录状态，避免破坏已有可用索引。

## 3. 核心原则

### 3.1 Raw Repository 只读原则

自动更新器不得修改 Raw Repository 中的任何原始文件、原始文件夹、原始元数据或用户内容。

允许的 Raw Repository 操作包括：

- 扫描目录结构；
- 读取文件元数据；
- 读取稳定文件内容；
- 对非文本文件执行只读解析、OCR、渲染或摘要提取；
- 检测文件是否可能正在编辑；
- 生成指向原始文件或文件夹的链接。

禁止的 Raw Repository 操作包括：

- 写入索引文件；
- 写入临时文件；
- 修改原始文件；
- 移动、重命名、删除原始文件；
- 在原始目录中生成 AI 摘要、缓存、日志或配置文件。

所有自动更新产物必须写入 Index Repository。

### 3.2 Index Repository 独立原则

Index Repository 是自动更新器的唯一写入区域。

Index Repository 至少保存：

- Leaf 索引文件；
- FolderNode 索引文件；
- 链接镜像树；
- 仓库级 manifest；
- 更新日志；
- 锁文件；
- 错误记录；
- 仓库级配置；
- 必要的缓存或派生状态。

自动扫描 Raw Repository 时，必须排除 Index Repository 本身，避免索引仓库递归扫描自己。

### 3.3 自底向上更新原则

所有依赖链更新必须遵守自底向上的顺序：

1. 先处理稳定的原始文件；
2. 对非文本型文件生成或更新 Leaf；
3. 对最底层文件夹生成或更新 FolderNode；
4. 逐级向上更新父 FolderNode；
5. 最后更新 Raw Repository 根目录对应的 FolderNode。

上级 FolderNode 不得绕过下级 Leaf 或下级 FolderNode，直接递归读取全部原始文件生成摘要。

### 3.4 暂缓半成品原则

如果文件可能正在被用户编辑，自动更新器应暂缓对该文件对应 Leaf 的更新。

暂缓的目标不是判断用户是否一定正在编辑，而是在不确定时避免消耗 token 处理半成品文件。

只要存在较强编辑中信号，自动更新器应保守地将文件标记为 `pending_edit` 或 `editing`，等待下一轮扫描确认。

### 3.5 最小 AI 参与原则

自动更新机制应优先使用确定性程序完成以下任务：

- 目录扫描；
- 元数据读取；
- 文件大小和修改时间对比；
- 快速 hash 或内容 hash；
- manifest 对比；
- JSON 机器头解析；
- Leaf 与 FolderNode 之间的 JSON 字段传递；
- FolderNode 的文件树拓扑更新；
- children_summary_layer 的机械合并；
- 链接镜像树维护；
- 状态机迁移；
- dirty 标记传播；
- 更新队列排序；
- 日志追加；
- 原子写入和校验。

这些不依赖 AI/API 的操作可以短时多次执行，并应尽量快速完成。

只有在生成或更新 Leaf、重新理解稳定原始文件内容、生成自然语言摘要、评估内容价值、判断索引阅读路径或更新 AI 生成的正文摘要时，才调用 AI/API。普通检索、筛选、Markmap 输出和 Agentic 任务执行只读取 Leaf 与 FolderNode，不直接打开非文本型原文件。

### 3.6 父级降频原则

Leaf 的状态和 JSON 信息更新可以较高频率执行。

FolderNode 的机械聚合也可以较高频率执行。

但是需要 AI 总结的 FolderNode 自然语言摘要，应随着层级向上逐步降低更新频率，以减少资源消耗。

推荐规则：

- Leaf：文件稳定后尽快更新；
- 直接父 FolderNode：可较快更新；
- 中间 FolderNode：可延迟批处理；
- 根 FolderNode：低频更新或在一批变更完成后统一更新。

## 4. 自动更新对象与非对象

### 4.1 自动更新对象

自动更新器默认处理以下对象：

- Raw Repository 中的普通文件；
- Raw Repository 中的文件夹；
- 非文本型文件对应的 Leaf；
- 复杂文本或复合资料对应的 Leaf；
- 文件夹对应的 FolderNode；
- 被声明为 `opaque_leaf_folder` 的最小叶片文件夹；
- Index Repository 中的仓库状态 manifest；
- Index Repository 中的维护日志和错误日志；
- Index Repository 中的链接镜像树。

### 4.2 默认不处理对象

自动更新器默认不处理或低优先级处理以下对象：

- Index Repository 自身；
- `.git/`；
- `.svn/`；
- `.hg/`；
- `node_modules/`；
- `.venv/`；
- `venv/`；
- `__pycache__/`；
- `.pytest_cache/`；
- `.mypy_cache/`；
- `.next/`；
- `dist/`；
- `build/`；
- `coverage/`；
- 临时文件；
- 操作系统缓存文件；
- Office 临时锁文件；
- 用户明确声明的废弃目录；
- 用户明确声明忽略的 glob；
- 过大且未授权解析的文件；
- 权限不足且无法只读访问的文件。

### 4.3 废弃目录处理

如果 Raw Repository 或工作区中存在类似 `废弃方案存放地/`、`archive/`、`deprecated/`、`old/`、`backup/` 的目录，自动更新器不应默认把这些内容纳入当前有效索引。

推荐处理方式：

- 默认标记为 `ignored` 或 `low_priority`；
- 在父 FolderNode 的聚合判断与边界中说明已排除；
- 除非用户明确要求，否则不生成 Leaf 或 FolderNode；
- 如果已经存在旧索引，应标记为 `stale` 或 `ignored`，而不是继续参与上级摘要。

## 5. 仓库配置

每个 Index Repository 应保存一份仓库级配置文件。

推荐路径：

```text
<Index Repository>/.octopus/repository-config.json
```

推荐结构：

```json
{
  "schema": {
    "octopus_schema": "0.2",
    "config_type": "repository_auto_update_config"
  },
  "repository": {
    "raw_repo_id": "",
    "raw_repository_path": "",
    "index_repository_path": "",
    "repository_name": ""
  },
  "watcher": {
    "enabled": true,
    "scan_interval_minutes": 5,
    "allowed_scan_interval_minutes": [1, 5, 15, 60],
    "initial_scan_on_startup": true,
    "run_once_mode_available": true
  },
  "stability": {
    "minimum_quiet_seconds": 120,
    "required_stable_scan_count": 2,
    "pending_edit_max_hours": 24,
    "allow_stable_readonly_open_files": true,
    "strictly_defer_suspected_editing_files": true
  },
  "update_policy": {
    "generation_order": "bottom_up",
    "leaf_update_priority": "high",
    "foldernode_mechanical_update_priority": "high",
    "foldernode_ai_summary_update_priority": "throttled_by_depth",
    "root_foldernode_ai_summary_policy": "batch_after_leaf_updates",
    "preserve_old_index_on_failure": true
  },
  "ai_policy": {
    "minimum_ai_participation": true,
    "do_not_call_ai_for_metadata_scan": true,
    "do_not_call_ai_for_json_field_propagation": true,
    "do_not_call_ai_for_tree_topology_update": true,
    "call_ai_only_when_content_summary_required": true
  },
  "ignore": {
    "default_ignore_rules_enabled": true,
    "extra_exclude_globs": [],
    "deprecated_folder_names": ["废弃方案存放地", "deprecated", "archive", "old", "backup"]
  }
}
```

### 5.1 扫描间隔

默认扫描间隔为 5 分钟。

仓库配置可允许用户选择：

- 1 分钟；
- 5 分钟；
- 15 分钟；
- 60 分钟；
- 手动触发。

1 分钟适合活跃工作目录，但必须配合稳定性等待和 AI 调用节流。

5 分钟适合作为默认值。

15 分钟适合普通资料库。

60 分钟适合低频归档目录。

### 5.2 只读稳定文件策略

对于“被打开但只读稳定”的文件，默认允许索引。

条件是：

- 没有临时锁文件；
- 没有独占打开失败；
- 文件大小在连续扫描中稳定；
- 修改时间在连续扫描中稳定；
- 最近修改时间已超过 `minimum_quiet_seconds`；
- 读取文件内容时没有出现半写入错误。

对于疑似正在编辑的文件，应严格暂缓。

### 5.3 pending 最长期限

文件处于 `pending_edit` 的最长默认期限为 24 小时。

如果超过 24 小时仍被锁定或仍无法确认稳定，自动更新器可以生成“可能过期”的索引，但必须满足以下要求：

- 不覆盖旧的可靠索引；
- 在 Leaf 或 FolderNode 中标记 `quality_flags`；
- 在 manifest 中记录 `stale_reason`；
- 在维护日志中说明该索引基于旧版本或不完整状态；
- 父级 FolderNode 聚合时应明确标注该子节点可能过期。

## 6. 仓库状态 Manifest

自动更新器必须维护仓库级 manifest，用于记录 Raw Repository 与 Index Repository 之间的映射关系、文件状态、索引状态和依赖关系。

推荐路径：

```text
<Index Repository>/.octopus/repository-state.json
```

### 6.1 manifest 顶层结构

推荐结构：

```json
{
  "schema": {
    "octopus_schema": "0.2",
    "manifest_type": "repository_state"
  },
  "repository": {
    "raw_repo_id": "",
    "raw_repository_path_snapshot": "",
    "index_repository_path_snapshot": "",
    "created_at": "",
    "last_scan_started_at": "",
    "last_scan_finished_at": "",
    "last_successful_update_at": ""
  },
  "scan": {
    "scan_generation": 0,
    "scan_interval_minutes": 5,
    "last_scan_status": "clean|partial|failed",
    "last_scan_error": ""
  },
  "nodes": {},
  "dependencies": {},
  "queues": {
    "pending_edit": [],
    "leaf_update": [],
    "foldernode_mechanical_update": [],
    "foldernode_ai_summary_update": [],
    "retry": [],
    "failed": []
  }
}
```

### 6.2 节点记录结构

manifest 中每个文件、Leaf、文件夹和 FolderNode 都应有节点记录。

推荐结构：

```json
{
  "node_id": "",
  "node_kind": "raw_file|raw_folder|leaf|foldernode|opaque_leaf_folder",
  "raw_relative_path": "",
  "index_relative_path": "",
  "parent_node_id": "",
  "child_node_ids": [],
  "state": "clean",
  "previous_state": "",
  "fingerprint": {
    "size_bytes": 0,
    "modified_at": "",
    "created_at": "",
    "quick_hash": "",
    "content_hash": "",
    "fingerprint_version": "0.1"
  },
  "stability": {
    "last_seen_at": "",
    "stable_scan_count": 0,
    "last_unstable_at": "",
    "editing_signals": [],
    "pending_since": "",
    "pending_deadline_at": ""
  },
  "indexing": {
    "last_indexed_at": "",
    "last_successful_index_at": "",
    "last_attempt_at": "",
    "retry_count": 0,
    "last_error": "",
    "generator_version": ""
  },
  "dependency": {
    "direct_parent_foldernode_id": "",
    "ancestor_foldernode_ids": [],
    "dirty_reason": ""
  }
}
```

### 6.3 manifest 的用途

manifest 用于：

- 判断文件是否变化；
- 判断文件是否稳定；
- 判断 Leaf 是否需要更新；
- 判断 FolderNode 是否需要更新；
- 识别移动和重命名；
- 保留旧索引路径；
- 跟踪失败和重试；
- 传播 dirty 状态；
- 支持下轮扫描优先处理未完成事项；
- 避免每次全量读取和全量 AI 总结。

## 7. 状态机模型

### 7.1 状态列表

自动更新器应至少支持以下状态。

| 状态 | 含义 | 是否可参与父级最终摘要 |
|---|---|---|
| `unknown` | 尚未完成首次判定 | 否 |
| `clean` | 已扫描且索引与原始状态一致 | 是 |
| `dirty` | 原始内容或下级依赖变化，需要更新索引 | 否，除非存在旧索引 |
| `editing` | 强信号显示文件正在编辑 | 否 |
| `pending_edit` | 疑似正在编辑或尚未稳定，等待后续扫描 | 否，父级可保留旧摘要 |
| `pending_stable` | 已发现变化，但尚未满足稳定扫描次数 | 否 |
| `queued` | 已进入更新队列 | 否 |
| `indexing` | 正在生成索引 | 否 |
| `indexed` | 本轮已成功生成索引，等待依赖传播或提交 | 可参与 |
| `failed` | 本轮更新失败 | 否，父级使用旧索引并标记风险 |
| `retry` | 等待重试 | 否，父级使用旧索引并标记风险 |
| `ignored` | 被排除规则忽略 | 否 |
| `deleted` | 原始文件或文件夹已删除 | 否 |
| `moved` | 疑似移动或重命名，等待迁移确认 | 视旧索引状态而定 |
| `stale` | 存在旧索引但可能过期 | 可参与，但必须标记 |
| `orphaned` | 索引存在但原始文件已找不到 | 否，除非用户请求恢复 |

### 7.2 状态迁移规则

#### 7.2.1 clean 到 dirty

当以下任一情况发生时，节点从 `clean` 变为 `dirty`：

- 文件大小变化；
- 文件修改时间变化；
- 内容 hash 变化；
- 文件路径变化但 content hash 可匹配；
- 子节点状态变化；
- Leaf 更新成功并触发父 FolderNode dirty；
- 下级 FolderNode 更新成功并触发父 FolderNode dirty；
- 用户修改了自动化建议或提示词，要求下轮更新参考。

#### 7.2.2 dirty 到 pending_edit

当 dirty 文件存在疑似编辑信号时，变为 `pending_edit`。

疑似编辑信号包括：

- 存在 Office 临时锁文件；
- 独占打开失败；
- 文件大小连续变化；
- 修改时间连续变化；
- 最近修改时间过近；
- 文件读取过程中 EOF 异常；
- 文件 parser 报告结构不完整；
- 压缩格式中央目录异常；
- 图片、PDF、Office 文件解析失败且下一轮扫描显示仍在变化。

#### 7.2.3 pending_edit 到 queued

当文件满足稳定条件时，变为 `queued`。

稳定条件包括：

- 连续扫描中文件大小稳定；
- 连续扫描中修改时间稳定；
- 不存在临时锁文件；
- 可正常只读打开；
- 最近修改时间超过冷却窗口；
- 未超过用户配置的最大文件大小或已获授权处理。

#### 7.2.4 pending_edit 到 stale

当文件 pending 超过 24 小时，仍无法确认稳定时：

- 如果已有旧索引，则旧索引标记为 `stale`；
- 可生成“可能过期”的索引；
- 不得删除旧索引；
- 父级 FolderNode 必须标记该子节点可能过期。

#### 7.2.5 queued 到 indexing

当更新器实际开始处理该节点时，状态变为 `indexing`。

同一节点不得被多个更新进程同时处理。

#### 7.2.6 indexing 到 indexed

当新索引生成、校验、原子写入全部成功后，状态变为 `indexed`。

#### 7.2.7 indexed 到 clean

当本轮 manifest 提交完成，且依赖传播完成后，状态变为 `clean`。

#### 7.2.8 indexing 到 failed

当解析失败、AI 调用失败、JSON 校验失败、原子写入失败、权限失败或其他不可恢复错误发生时，状态变为 `failed`。

旧索引必须保留。

#### 7.2.9 failed 到 retry

失败节点可根据错误类型进入 `retry`。

推荐重试策略：

- 文件锁定：下一轮扫描重试；
- 网络或 API 失败：指数退避；
- JSON 校验失败：最多重试 1-2 次；
- 权限失败：等待用户处理；
- 文件格式不支持：标记 failed，不重复消耗资源；
- 路径过长：等待路径策略或用户处理。

#### 7.2.10 deleted 到 orphaned

如果原始文件已删除但索引仍存在，可先标记为 `orphaned`。

是否删除 orphaned 索引应由用户配置决定。

## 8. 文件编辑中检测

### 8.1 总体原则

自动更新器不需要证明文件一定正在被编辑。

只要有足够信号说明文件可能处于编辑中、写入中、保存中、同步中或半成品状态，就应暂缓 Leaf 更新。

### 8.2 Office 临时锁文件检测

对 Word、Excel、PowerPoint 等 Office 文件，应优先使用临时锁文件检测。

常见模式包括：

```text
~$filename.docx
~$filename.xlsx
~$filename.pptx
~$filename.doc
~$filename.xls
~$filename.ppt
```

检测规则：

- 如果同目录下存在与目标文件对应的 `~$` 临时文件，目标文件标记为 `editing` 或 `pending_edit`；
- `~$` 临时文件本身应标记为 `ignored`；
- 不应为 `~$` 临时文件生成 Leaf；
- 当临时锁文件消失后，目标文件仍需通过稳定性等待，不能立刻更新。

### 8.3 独占打开检测

某些文件在编辑期间会被应用程序锁定。

自动更新器可尝试以只读方式打开文件，并在操作系统允许时尝试检测是否可获得共享读权限或独占读探测。

规则：

- 如果只读打开失败，标记为 `pending_edit` 或 `failed_permission`；
- 如果独占打开失败但只读打开成功，结合文件类型和修改稳定性判断；
- 不得为了检测锁状态而强制关闭用户程序；
- 不得修改文件锁；
- 不得写入探测内容。

### 8.4 文件大小和修改时间稳定性检测

对于无法通过临时锁文件判断的普通文件，应使用稳定性检测。

推荐规则：

- 当前扫描记录 `size_bytes` 与 `modified_at`；
- 下一次扫描再次记录；
- 如果二者均未变化，则 `stable_scan_count + 1`；
- 如果任一变化，则 `stable_scan_count = 0`，并记录 `last_unstable_at`；
- 只有当 `stable_scan_count >= required_stable_scan_count` 且最近修改时间超过 `minimum_quiet_seconds`，才认为文件稳定。

默认值：

- `required_stable_scan_count = 2`
- `minimum_quiet_seconds = 120`

### 8.5 快速 hash 检测

对较小文件，可计算完整 hash。

对较大文件，可计算快速 hash。

快速 hash 可由以下信息组合：

- 文件大小；
- 修改时间；
- 文件头部若干字节；
- 文件尾部若干字节；
- 必要时抽样中间块；
- 文件相对路径。

快速 hash 只用于变更初筛，不应作为长期唯一身份。

### 8.6 内容 hash 检测

当需要识别重命名或移动时，应尽量计算内容 hash。

内容 hash 用于：

- 识别同一文件移动到新路径；
- 识别同一文件重命名；
- 将旧 Leaf 迁移到新索引路径；
- 避免删除旧索引后重新生成导致 token 浪费。

对于超大文件，可先使用快速 hash 识别候选，再在必要时计算完整 hash。

### 8.7 格式完整性检测

部分文件可通过格式结构判断是否可能半写入。

示例：

- `.zip`、`.docx`、`.xlsx`、`.pptx`：本质为压缩包，可检测 central directory 是否完整；
- `.pdf`：可检测 EOF 标记和 xref 结构是否可解析；
- 图片：可检测文件头、尺寸和解码是否成功；
- SQLite 数据库：可检测文件头和只读连接；
- JSON/YAML/TOML：可检测语法是否完整；
- Markdown：通常可直接读取，但如果文件仍在持续变化，应等待稳定。

格式不完整时应标记为 `pending_edit`，而不是立即标记为永久失败。

### 8.8 同步盘与云盘文件检测

如果 Raw Repository 位于 OneDrive、Dropbox、iCloud、坚果云或其他同步盘，自动更新器应额外注意：

- 文件可能处于下载占位状态；
- 文件可能正在同步；
- 修改时间可能由同步进程改变；
- 文件可读但内容尚未完整落盘。

推荐规则：

- 对同步盘路径启用更长 `minimum_quiet_seconds`；
- 对解析失败但元数据持续变化的文件标记 `pending_edit`；
- 对占位文件标记 `pending_sync` 或 `pending_edit`；
- 不强制触发下载，除非用户明确允许。

## 9. 自动更新流程

### 9.1 第一阶段：轻量扫描

自动更新器每 5 分钟或按仓库配置扫描 Raw Repository。

轻量扫描只读取：

- 目录结构；
- 文件名；
- 文件扩展名；
- 文件大小；
- 创建时间；
- 修改时间；
- 文件属性；
- 必要的快速 hash；
- 临时锁文件存在情况；
- 可读性状态。

轻量扫描不得调用 AI/API。

轻量扫描不得读取大文件全文。

轻量扫描不得生成自然语言摘要。

轻量扫描的输出是当前扫描快照。

### 9.2 第二阶段：变更判定

自动更新器将当前扫描快照与上次 manifest 对比。

需要判断：

- 新增文件；
- 新增文件夹；
- 修改文件；
- 删除文件；
- 删除文件夹；
- 重命名文件；
- 移动文件；
- 重命名文件夹；
- 移动文件夹；
- 文件类型变化；
- 目录结构变化；
- 文件疑似正在编辑；
- 文件从 pending 变为稳定；
- 索引文件缺失；
- 索引文件损坏；
- 用户标注区域被修改；
- 仓库配置被修改。

### 9.3 第三阶段：稳定性等待

对以下文件标记为 `pending_edit` 或 `pending_stable`，暂不生成 Leaf：

- 正在变化的文件；
- 存在 Office 临时锁文件的文件；
- 独占打开失败的文件；
- 连续扫描中文件大小变化的文件；
- 连续扫描中修改时间变化的文件；
- 最近修改时间距离当前时间小于冷却窗口的文件；
- 格式结构疑似不完整的文件；
- 云盘同步状态不稳定的文件。

对于 pending 文件：

- 不生成新 Leaf；
- 不覆盖旧 Leaf；
- 不触发最终父级 AI 摘要；
- 可以把直接父 FolderNode 标记为 `dirty_pending_child`；
- 父 FolderNode 可以保留旧摘要，并在质量提示中标记存在待更新子节点。

### 9.4 第四阶段：生成更新队列

对稳定变更生成更新队列。

队列至少包括：

- `leaf_update`
- `foldernode_mechanical_update`
- `foldernode_ai_summary_update`
- `retry`
- `pending_edit`
- `deleted`
- `move_or_rename`

排序规则：

1. 非文本型文件 Leaf 优先；
2. 复杂文本或复合资料 Leaf 次之；
3. 文本型文件不生成 Leaf，直接标记父 FolderNode dirty；
4. 子文件夹结构变化按树深度从深到浅排序；
5. FolderNode 机械聚合按深度从深到浅排序；
6. FolderNode AI 摘要按深度从深到浅排序，但越靠近根目录节流越强；
7. retry 队列按错误类型和重试次数排序；
8. pending_edit 队列只在文件稳定后进入实际更新队列。

### 9.5 第五阶段：自底向上更新

自底向上更新分为两个层次：

1. 机械更新；
2. AI 摘要更新。

机械更新包括：

- 解析 Leaf JSON；
- 提取 `summary_layer`；
- 提取 `one_sentence_summary`；
- 提取 `description`；
- 提取 `document_type`；
- 提取 `quality_flags`；
- 更新 `children_summary_layer`；
- 更新目录树拓扑；
- 更新链接；
- 更新状态字段；
- 更新 manifest。

AI 摘要更新包括：

- 重新总结 Leaf 正文层；
- 重新总结 FolderNode 文件夹摘要；
- 更新推荐阅读路径；
- 更新聚合判断；
- 更新自然语言质量评估；
- 更新 Leaf/FolderNode 的索引阅读建议、摘要理由或 `why_open_folder`。

默认顺序：

1. 更新所有稳定的 Leaf；
2. Leaf 成功后标记直接父 FolderNode dirty；
3. dirty 标记逐级向上传播到祖先 FolderNode；
4. 更新最底层 FolderNode 的机械聚合；
5. 更新最底层 FolderNode 的 AI 摘要；
6. 逐层向上更新 FolderNode；
7. 根 FolderNode 可延迟到本批次末尾统一更新。

### 9.6 第六阶段：提交索引快照

所有相关更新完成后，自动更新器提交新快照。

提交动作包括：

- 原子写入 Leaf；
- 原子写入 FolderNode；
- 更新 manifest；
- 追加更新日志；
- 清理已完成队列项；
- 保留失败队列项；
- 保留 pending_edit 队列项；
- 将成功节点状态改为 `clean`；
- 将失败节点状态改为 `failed` 或 `retry`；
- 将长期 pending 节点状态改为 `stale`。

### 9.7 第七阶段：下轮重试

下一轮扫描开始时，自动更新器应优先处理：

- 上轮 `pending_edit` 且现在稳定的文件；
- 上轮 `retry` 的节点；
- 上轮已成功 Leaf 但未完成父级 FolderNode 更新的节点；
- 上轮 dirty parent；
- 上轮移动或重命名待确认节点；
- 上轮 failed 但错误类型可恢复的节点。

自动更新器不得因为存在 failed 节点而每轮全量重建所有索引。

## 10. Leaf 更新规则

### 10.1 需要 Leaf 的对象

以下对象通常需要 Leaf：

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
- 复杂 CSV；
- 需要特殊解析、OCR、渲染或人工确认的文件；
- 被视作不可拆分整体的 `opaque_leaf_folder`。

### 10.2 不强制生成 Leaf 的对象

以下对象通常不强制生成 Leaf：

- `.txt`
- `.md`
- `.py`
- `.js`
- `.ts`
- `.json`
- `.yaml`
- `.toml`
- 简单 `.csv`
- 其他可稳定直接读取的纯文本文件。

这些文件由父 FolderNode 直接摘要为 compact signals。

### 10.3 Leaf 更新前检查

更新 Leaf 前必须确认：

- 原始文件未被忽略；
- 原始文件存在；
- 原始文件可只读访问；
- 原始文件满足稳定性条件；
- 原始文件未处于 `pending_edit`；
- 若存在旧 Leaf，已读取并保护用户重点标记区域和维护层；
- 若文件移动或重命名，已尽量通过 content hash 识别旧 Leaf；
- Index Repository 中目标路径可写；
- 当前仓库未被其他更新进程锁定。

### 10.4 Leaf 机器头更新要求

自动更新后的 Leaf 机器头应符合 Leaf 规范，并建议补充以下字段：

```json
{
  "update_control": {
    "index_status": "unknown|clean|dirty|editing|pending_edit|pending_stable|queued|indexing|indexed|failed|retry|ignored|deleted|moved|stale|orphaned",
    "last_seen_at": "",
    "last_indexed_at": "",
    "raw_fingerprint": "",
    "pending_reason": "",
    "generator_version": "",
    "ai_summary_updated_at": "",
    "mechanical_metadata_updated_at": ""
  }
}
```

其中：

- `update_control` 用于自动更新器判断后续是否需要重新处理；
- `raw_fingerprint` 应与 manifest 中对应值一致或可追踪。

### 10.5 Leaf 用户区域保护

自动更新 Leaf 时必须保留：

- 用户重点标记区域；
- 用户自动化叶子索引建议与提示词；
- 维护日志；
- 用户手工修改过且被标记为用户内容的部分。

推荐做法：

1. 读取旧 Leaf；
2. 抽取用户保护区；
3. 生成新的 AI 内容；
4. 将用户保护区合并回新 Leaf；
5. 追加维护日志；
6. 校验保护区未丢失；
7. 原子替换旧 Leaf。

### 10.6 Leaf 更新成功后的依赖传播

Leaf 更新成功后，必须执行：

1. 将 Leaf 状态标记为 `indexed`；
2. 将直接父 FolderNode 标记为 `dirty`；
3. dirty 原因记录为 `child_leaf_updated`；
4. 将所有祖先 FolderNode 标记为 `dirty` 或 `dirty_descendant_updated`；
5. 将直接父 FolderNode 放入机械更新队列；
6. 按节流策略将祖先 FolderNode 放入机械更新队列或 AI 摘要队列。

## 11. FolderNode 更新规则

### 11.1 FolderNode 更新类型

FolderNode 更新分为：

1. 机械聚合更新；
2. AI 摘要更新。

### 11.2 机械聚合更新

机械聚合更新不应调用 AI/API。

它可以通过程序瞬时完成。

机械聚合更新包括：

- 扫描直接子项；
- 读取子 Leaf 的开头 JSON；
- 读取子 FolderNode 的开头 JSON；
- 提取子节点 `summary_layer`；
- 更新 `children_summary_layer.direct_children`；
- 更新 `children_summary_layer.notable_children`；
- 更新 `children_summary_layer.text_files_without_leaf`；
- 更新 `children_summary_layer.non_text_files_with_leaf`；
- 更新 `children_summary_layer.subfolders_with_foldernode`；
- 更新 `children_summary_layer.opaque_leaf_folders`；
- 更新目录树拓扑；
- 更新 `folder_card_layer.metadata`；
- 更新 `folder_card_layer.links`；
- 更新 `content_snapshot_id`；
- 更新质量提示；
- 更新 manifest 中的依赖关系。

### 11.3 AI 摘要更新

AI 摘要更新只在需要自然语言重新总结时执行。

触发条件包括：

- 下级节点摘要发生实质变化；
- 新增重要 Leaf；
- 删除重要 Leaf；
- 子 FolderNode 的主题变化；
- 用户修改 FolderNode 自动化建议；
- 旧摘要过期；
- 根 FolderNode 到达批处理更新时间；
- 用户手动触发刷新。

AI 摘要更新不应读取所有下级原文。

默认读取范围：

- 当前 FolderNode 的旧摘要；
- 当前 FolderNode 的用户重点标记区域；
- 直接子节点的 `summary_layer`；
- 直接子节点的 `children_summary_layer.notable_children`；
- 必要的目录树拓扑；
- 必要的质量提示。

### 11.4 父级 FolderNode 降频策略

父 FolderNode 的更新频率可以低于 Leaf。

推荐策略：

| 层级 | 机械聚合 | AI 摘要 |
|---|---|---|
| 直接父 FolderNode | 尽快更新 | 可短延迟更新 |
| 祖父 FolderNode | 批处理更新 | 延迟更新 |
| 更高层 FolderNode | 批处理更新 | 低频更新 |
| 根 FolderNode | 每批次末尾更新 | 低频或手动触发 |

示例：

- Leaf 更新后 0-1 分钟内更新直接父 FolderNode 的机械聚合；
- 5-15 分钟内批量更新中间 FolderNode；
- 根 FolderNode 每 30-60 分钟或本轮批处理完成后更新一次 AI 摘要；
- 如果只是文件大小、路径链接等元数据变化，可不触发父级 AI 摘要。

### 11.5 pending 子节点处理

如果某个子节点处于 `pending_edit`，父 FolderNode 应：

- 不读取该子节点半成品原文；
- 不生成基于半成品内容的最终摘要；
- 保留旧子节点摘要；
- 在 `children_summary_layer.direct_children` 中标记 `index_status: "pending_edit"`；
- 在质量提示中说明该子节点待更新；
- 可将父节点标记为 `stale` 或 `dirty_pending_child`；
- 不阻塞其他稳定子节点的机械聚合。

### 11.6 FolderNode 机器头补充字段

建议在 FolderNode 机器头中为自动更新补充：

```json
{
  "update_control": {
    "index_status": "unknown|clean|dirty|editing|pending_edit|pending_stable|queued|indexing|indexed|failed|retry|ignored|deleted|moved|stale|orphaned",
    "last_seen_at": "",
    "last_mechanical_update_at": "",
    "last_ai_summary_update_at": "",
    "content_snapshot_id": "",
    "dirty_reasons": [],
    "pending_child_count": 0,
    "failed_child_count": 0,
    "generator_version": ""
  }
}
```

### 11.7 FolderNode 用户区域保护

自动更新 FolderNode 时必须保留：

- 用户重点标记区域；
- 用户自动化文件夹节点建议与提示词；
- 维护日志；
- 用户手工修改过的边界说明；
- 用户手工修改过的推荐阅读建议，除非用户允许重写。

## 12. 移动与重命名处理

### 12.1 基本原则

文件或文件夹移动、重命名时，应尽量通过 content hash 识别并迁移旧 Leaf 或 FolderNode，而不是简单视作删除 + 新增。

这样可以减少：

- token 浪费；
- 旧用户标注丢失；
- 维护日志断裂；
- 上级 FolderNode 大量无意义重写。

### 12.2 文件重命名识别

当扫描发现：

- 旧路径文件消失；
- 新路径出现文件；
- 文件大小一致；
- 快速 hash 一致；
- 内容 hash 一致或高度可信；

则可判定为重命名或移动。

处理方式：

1. 迁移旧 Leaf 到新 Index 路径；
2. 更新 `attachment_card_layer.source.raw_relative_path`；
3. 更新 `metadata.filename`；
4. 更新 `metadata.file_uri`；
5. 保留用户重点标记区域；
6. 追加维护日志；
7. 标记父 FolderNode dirty；
8. 如果原父 FolderNode 与新父 FolderNode 不同，两者都标记 dirty。

### 12.3 文件夹重命名识别

文件夹移动或重命名可通过子文件集合相似度判断。

参考信号：

- 子文件 content hash 集合高度一致；
- 子文件数量相近；
- 总大小相近；
- 目录结构相似；
- 旧文件夹消失且新文件夹出现时间接近。

处理方式：

- 迁移对应 FolderNode；
- 更新 `folder_card_layer.source.raw_relative_path`；
- 更新 `folder_card_layer.metadata.folder_name`；
- 更新子节点相对路径；
- 标记旧父与新父 FolderNode dirty；
- 保留原维护层。

### 12.4 无法确认移动时

如果无法可靠确认移动或重命名，应保守处理：

- 旧节点标记 `orphaned` 或 `deleted_pending_confirmation`；
- 新节点按新增处理；
- 不立即删除旧索引；
- 在日志中记录候选匹配信息；
- 等待用户或后续扫描确认。

## 13. 删除处理

### 13.1 文件删除

当原始文件删除时：

- 对应 Leaf 不应立即强制删除；
- manifest 中标记原始节点 `deleted`；
- 对应 Leaf 标记 `orphaned`；
- 父 FolderNode 标记 dirty；
- 父 FolderNode 更新时说明该子节点已删除或已从有效索引中移除。

是否物理删除 orphaned Leaf，应由仓库配置决定。

推荐默认策略：

- 保留 orphaned 索引 30 天；
- 低优先级展示；
- 不参与普通搜索；
- 用户可手动清理。

### 13.2 文件夹删除

当原始文件夹删除时：

- 对应 FolderNode 标记 `orphaned`；
- 子 Leaf 和子 FolderNode 可批量标记为 `orphaned`；
- 父 FolderNode 标记 dirty；
- 不立即删除索引树；
- 记录删除事件。

## 14. 原子写入与校验

### 14.1 原子写入流程

所有 Leaf、FolderNode、manifest 和日志更新均应采用原子写入策略。

推荐流程：

1. 读取旧文件；
2. 抽取用户保护区；
3. 生成新内容；
4. 写入临时文件；
5. 校验临时文件；
6. 备份或保留旧文件；
7. 原子替换目标文件；
8. 更新 manifest；
9. 追加日志。

### 14.2 校验要求

Leaf 与 FolderNode 写入前必须校验：

- 开头 JSON 合法；
- 只存在一个强制机器可读 JSON；
- `schema.index_type` 正确；
- 必须字段存在；
- 用户重点标记区域未丢失；
- 维护层未丢失；
- Markdown 标题结构基本完整；
- 链接路径不指向 Index Repository 自身作为 Raw 内容；
- 旧索引失败时仍可回滚。

### 14.3 失败保护

如果新索引生成失败：

- 不替换旧索引；
- 旧索引继续可用；
- 节点状态标记 `failed`；
- 错误进入 retry 或 failed 队列；
- 父节点使用旧摘要并标记风险。

## 15. 更新锁

为避免多个进程同时更新同一个 Index Repository，自动更新器应使用仓库级锁。

推荐路径：

```text
<Index Repository>/.octopus/update.lock
```

锁文件应记录：

```json
{
  "pid": "",
  "host": "",
  "started_at": "",
  "operation": "scan|update|commit",
  "raw_repo_id": "",
  "index_repository_path": ""
}
```

规则：

- 同一 Index Repository 同时只能有一个写入型更新进程；
- 只读搜索可以与更新并行，但应读取已提交快照；
- 如果锁超时，应检测进程是否仍存在；
- 不得盲目删除锁；
- 删除陈旧锁必须记录日志。

## 16. 日志

### 16.1 更新日志

推荐路径：

```text
<Index Repository>/.octopus/update-log.md
```

日志应记录：

- 扫描开始时间；
- 扫描结束时间；
- 发现的新增、修改、删除、移动数量；
- pending_edit 文件数量；
- Leaf 更新数量；
- FolderNode 机械更新数量；
- FolderNode AI 摘要更新数量；
- failed 节点；
- retry 节点；
- stale 节点；
- 本轮是否提交成功。

### 16.2 机器日志

可选路径：

```text
<Index Repository>/.octopus/update-events.jsonl
```

每行一个事件：

```json
{
  "timestamp": "",
  "event_type": "",
  "node_id": "",
  "state_before": "",
  "state_after": "",
  "message": "",
  "error": ""
}
```

机器日志用于调试和后续 UI 展示。

## 17. AI/API 调用策略

### 17.1 不应调用 AI 的场景

以下场景不应调用 AI：

- 定时扫描；
- 文件元数据读取；
- 文件稳定性判断；
- Office 临时锁检测；
- manifest 对比；
- 快速 hash；
- content hash；
- JSON 机器头解析；
- Leaf 到 FolderNode 的字段传递；
- FolderNode 目录树拓扑更新；
- dirty 状态传播；
- 队列排序；
- 日志追加；
- 原子写入校验。

### 17.2 可以调用 AI 的场景

以下场景可以调用 AI：

- 新非文本文件首次生成 Leaf；
- 非文本文件内容实质变化后更新 Leaf；
- 复杂文本文件需要结构化摘要；
- FolderNode 需要重新生成自然语言文件夹摘要；
- FolderNode 需要更新推荐阅读路径；
- 用户明确要求重新总结；
- 旧摘要质量不足；
- 文件类型需要 OCR、版面理解、表格理解或多模态解析。

### 17.3 AI 调用节流

AI 调用应遵守：

- 单轮扫描最大 AI 调用数量；
- 单文件最大 token 预算；
- 单 FolderNode 最大 child 数量摘要预算；
- 根 FolderNode 低频更新；
- failed API 调用指数退避；
- 同一文件短时间内不重复 AI 总结；
- 如果只有路径或元数据变化，不触发 AI 总结。

### 17.4 AI 输出校验

AI 生成内容写入前必须经过程序校验。

至少校验：

- JSON 合法；
- 字段类型正确；
- Markdown 不包含大段原文复制；
- 未覆盖用户区域；
- 未把附件卡片层信息写入摘要层；
- 未把完整 OCR 或全文塞入 compact signals；
- 未违反 Leaf 或 FolderNode 的读取策略。

## 18. 链接镜像树更新

Index Repository 的链接镜像树应反映 Raw Repository 的目录结构，但不得复制原始文件内容。

自动更新器应维护：

- 原始文件对应的链接；
- 非文本文件对应的 Leaf；
- 文件夹对应的 FolderNode；
- `opaque_leaf_folder` 的整体链接；
- 删除或移动后的 orphaned 状态。

暂缓项：长路径、任意深度目录映射、稳定 ID 物理路径和短路径映射方案暂时搁置，不作为 MVP 阻塞项。MVP 先采用常规目录深度下的链接镜像树方案，并在遇到路径过深时记录为风险或 failed/retry，不强行设计完整兼容方案。

## 19. 默认排除规则

自动更新器默认排除：

```text
.git/
.svn/
.hg/
node_modules/
.venv/
venv/
__pycache__/
.pytest_cache/
.mypy_cache/
.next/
dist/
build/
coverage/
*.tmp
*.temp
*.swp
*.lock
~$*
.DS_Store
Thumbs.db
desktop.ini
废弃方案存放地/
```

这些规则可由仓库配置扩展。

但用户明确指定的路径优先级最高。

## 20. CLI 行为建议

Octopus MVP 是内置 Agentic 能力的索引器 CLI。MVP 优先级如下：

1. 能读取仓库配置并扫描 Raw Repository；
2. 能调用 DeepSeek API 生成或更新必要 Leaf 与 FolderNode；
3. 能维护 manifest、队列、状态机和更新日志；
4. 能执行 `octopus search --full`，只基于 Leaf 与 FolderNode 搜索和筛选；
5. 能输出符合 Markmap 输入规范的 Markdown；
6. 能调用 Markmap 开源包自动转换为含链接 mindmap。

非文本型原文件只在生成或更新 Leaf 时被只读解析；普通搜索、Agentic 任务执行和 Markmap 输出不直接打开非文本型原文件。

### 20.1 推荐命令

```text
octopus watch start
octopus watch stop
octopus watch status
octopus update --once
octopus update --scan-only
octopus update --leaf-only
octopus update --foldernode-only
octopus update --retry
octopus update --force <path>
octopus update --explain <path>
```

### 20.2 命令含义

- `octopus watch start`：启动定期扫描。
- `octopus watch stop`：停止定期扫描。
- `octopus watch status`：查看当前 watcher、队列、pending、failed 状态。
- `octopus update --once`：执行一次完整自动更新流程。
- `octopus update --scan-only`：只执行轻量扫描和 manifest 对比，不生成索引。
- `octopus update --leaf-only`：只更新 Leaf。
- `octopus update --foldernode-only`：只更新 FolderNode。
- `octopus update --retry`：优先处理 retry 队列。
- `octopus update --force <path>`：强制更新指定路径，但仍应保护用户区域。
- `octopus update --explain <path>`：解释指定文件或文件夹为什么 clean、dirty、pending、ignored 或 failed。

## 21. 示例流程

### 21.1 Word 文件正在编辑

Raw Repository 中存在：

```text
论文笔记.docx
~$论文笔记.docx
```

处理流程：

1. 扫描器发现 `~$论文笔记.docx`；
2. `~$论文笔记.docx` 标记为 `ignored`；
3. `论文笔记.docx` 标记为 `pending_edit`；
4. 不生成或更新 Leaf；
5. 旧 Leaf 保留；
6. 父 FolderNode 标记 `dirty_pending_child`；
7. 下一轮扫描锁文件消失后，继续等待稳定扫描次数；
8. 稳定后进入 Leaf 更新队列。

### 21.2 Markdown 文件修改

Raw Repository 中 `README.md` 修改。

处理流程：

1. 扫描器发现 mtime 或 size 变化；
2. 等待连续扫描稳定；
3. 不生成 Leaf；
4. 直接标记父 FolderNode dirty；
5. 父 FolderNode 机械更新 `text_files_without_leaf`；
6. 如 README 摘要变化较大，再触发父 FolderNode AI 摘要更新。

### 21.3 PDF 文件修改

Raw Repository 中 `讲义.pdf` 修改。

处理流程：

1. 扫描器发现 fingerprint 变化；
2. 等待文件稳定；
3. 更新 `讲义.pdf` 对应 Leaf；
4. 保留旧 Leaf 用户重点标记区域；
5. Leaf 写入成功后标记直接父 FolderNode dirty；
6. 直接父 FolderNode 机械聚合新 Leaf JSON；
7. 必要时触发父 FolderNode AI 摘要；
8. dirty 向祖先 FolderNode 传播；
9. 根 FolderNode 延迟批处理更新。

### 21.4 文件重命名

Raw Repository 中：

```text
old_name.pdf
```

变为：

```text
new_name.pdf
```

处理流程：

1. 扫描器发现 old path 删除、新 path 新增；
2. 通过 size、quick hash、content hash 判断为同一文件；
3. 迁移旧 Leaf；
4. 更新 Leaf 的 `raw_relative_path`、`filename`、`file_uri`；
5. 保留用户标注和维护日志；
6. 标记父 FolderNode dirty；
7. 更新链接镜像树。

### 21.5 pending 超过 24 小时

某 Excel 文件持续被锁定超过 24 小时。

处理流程：

1. 节点保持 `pending_edit`；
2. 超过 `pending_edit_max_hours`；
3. 如果存在旧 Leaf，则旧 Leaf 标记 `stale`；
4. 父 FolderNode 标记该 child 为 `stale`；
5. 可生成“可能过期”的索引说明；
6. 不覆盖旧可靠摘要；
7. 等用户关闭文件后再正常更新。

## 22. 与 Leaf 规范的关系

自动更新方案不改变 Leaf 的基本定位。

Leaf 仍然是：

- 面向单个原始文件或最小内容单元；
- 以 compact signals 为核心；
- 不复制全文；
- 保留唯一开头 JSON；
- 保留正文层；
- 保留维护层；
- 用于告诉人和 Agent 原始文件是否值得打开。

自动更新方案对 Leaf 的补充是：

- 增加编辑中检测；
- 增加状态机；
- 增加 fingerprint；
- 增加 update_control；
- 增加 pending/stale/failed 处理；
- 明确 Leaf 成功后必须触发父 FolderNode dirty；
- 明确用户区域保护和原子写入。

## 23. 与 FolderNode 规范的关系

自动更新方案不改变 FolderNode 的基本定位。

FolderNode 仍然是：

- 文件夹级路由和聚合；
- 自底向上生成；
- 消费直接子 Leaf 与直接子 FolderNode；
- 不复制下级全文；
- 支持 Markmap 和任务资料组合。

自动更新方案对 FolderNode 的补充是：

- 区分机械聚合更新和 AI 摘要更新；
- 允许父 FolderNode 更新频率低于 Leaf；
- 明确 pending 子节点处理；
- 明确 dirty 向祖先传播；
- 明确 `content_snapshot_id` 与 manifest 的关系；
- 明确父级降频和批处理策略。

## 24. 质量要求

自动更新完成后，应满足：

- Raw Repository 未被修改；
- Index Repository 结构完整；
- manifest 可解析；
- Leaf JSON 可解析；
- FolderNode JSON 可解析；
- 用户重点标记区域未丢失；
- 维护层未丢失；
- pending 文件未被强行总结为最终内容；
- failed 节点保留旧索引；
- dirty 父节点已正确传播；
- 根 FolderNode 不使用半成品子节点生成最终摘要；
- AI 调用只发生在确实需要自然语言摘要的地方；
- 机械 JSON 传递和目录树更新可快速完成；
- 更新日志能解释本轮做了什么、跳过了什么、失败了什么。

## 25. 最小实现版本

Octopus 自动更新机制的最小可用版本应实现：

1. 仓库配置读取；
2. 5 分钟定时扫描；
3. 手动 `update --once`；
4. 默认排除规则；
5. manifest；
6. 文件大小与修改时间对比；
7. Office 临时锁文件检测；
8. 连续扫描稳定性判断；
9. `pending_edit` 状态；
10. 非文本文件 Leaf 更新队列；
11. 文本文件父 FolderNode dirty 标记；
12. Leaf 成功后父 FolderNode dirty 传播；
13. 自底向上 FolderNode 机械聚合；
14. FolderNode AI 摘要节流；
15. 原子写入；
16. 用户区域保护；
17. 更新日志；
18. failed/retry 记录；
19. 24 小时 pending 后 stale 标记；
20. 重命名/移动的 content hash 识别。

## 26. 后续可拓展方向

后续版本可以继续拓展：

- 文件系统原生事件监听；
- 与轮询扫描结合的混合 watcher；
- UI 中展示 pending / dirty / failed 状态；
- 用户手动批准 stale 索引；
- 自动清理 orphaned 索引；
- 多 Raw Repository 绑定同一 Index Repository；
- 同一 Raw Repository 生成多套 Index Repository；
- 云盘同步状态识别；
- 更精细的文件类型解析器；
- 更细粒度的 AI 调用预算；
- Markmap 增量刷新；
- 长路径、任意深度目录映射、稳定 ID 物理路径和短路径映射方案；
- Package 插件基于索引状态打包文件；
- Timeline 插件基于 manifest 生成时间线。

## 27. 维护层

### 用户的自动化更新建议与提示词

<!-- 用户可在此处写入下一次更新本规范时希望智能体注意的事项。相关智能体不得删除或覆盖本区域。 -->

### 维护日志

- 2026-07-04：创建 Octopus Index Repository 自动更新方案规范，基于 Octopus 需求文档、Leaf 规范与 FolderNode 规范，补充 watcher 扫描、编辑中检测、状态机、自底向上更新、dirty 传播、最小 AI 参与、父级降频、manifest、原子写入与失败恢复规则。
- 2026-07-04：升级为 v1.0.1，统一自动更新状态机与队列命名；明确 MVP 是内置 Agentic 能力、可调用 DeepSeek API 的索引器 CLI；明确 Markmap 是最小可用有效输出；明确普通任务只读取 Leaf 与 FolderNode，非文本型原文件只在生成或更新 Leaf 时被只读解析；暂缓长路径等未定方案。
