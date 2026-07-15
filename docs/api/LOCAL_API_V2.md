# Octopus Local API V2

V2 API 服务于本地证据工作台。它默认只监听 `127.0.0.1`，所有资料、搜索、预览、设置和任务接口都要求桌面运行时提供的 Bearer token。

## Contract

- 产品版本：随 `octopus.__version__` 发布；
- V2 资料空间模型：`documents -> pages -> passages`；
- 原始资料只读；
- 搜索缓存可重建；
- 任务是独立的用户数据；
- V1 路由继续存在一个回滚周期。

请求头：

```http
Authorization: Bearer <local-service-token>
Content-Type: application/json
```

token 保存在 `%APPDATA%\Octopus\service-token`。它只用于当前用户的本地回环服务，不应发送到其他程序或网站。

## Workspaces

```http
GET  /v2/workspaces
POST /v2/workspaces
GET  /v2/workspaces/{workspace_id}
POST /v2/workspaces/{workspace_id}/sync
```

创建资料空间：

```json
{
  "raw_path": "D:\\Downloads\\AAA",
  "name": "AAA"
}
```

V2 不接收 `index_path`。内部缓存固定放在 `%LOCALAPPDATA%\Octopus\workspaces\<workspace_id>`。

## Jobs And Documents

```http
GET  /v2/jobs?workspace_id={workspace_id}
GET  /v2/jobs/{job_id}
GET  /v2/workspaces/{workspace_id}/documents
GET  /v2/workspaces/{workspace_id}/documents/{document_id}
POST /v2/workspaces/{workspace_id}/documents/{document_id}/reprocess
```

同步和重新处理返回 `ServiceJob`。`progress` 包含发现、处理、OCR、完成和失败计数；长 PDF 会报告当前页和总页数。

## Search

```http
POST /v2/workspaces/{workspace_id}/search
```

```json
{
  "query": "微分方程",
  "mode": "local",
  "limit": 50,
  "extensions": [".pdf", ".txt"],
  "readability": ["readable", "partial"]
}
```

`mode` 可以是 `local` 或 `assisted`。辅助模式只能重排本地候选集，不能增加未检索到的文档。

每个 `SearchResultV2` 代表一个文档，包含最佳证据和最多两个补充证据，不包含索引路径或 FolderNode 类型。`indexing_state` 与资料文档接口一致：`indexed` 表示正文已建立索引，`metadata_only` 表示当前仅文件名和元数据可搜索，`failed` 表示最近一次处理失败。

## Page Preview

```http
GET /v2/workspaces/{workspace_id}/documents/{document_id}/pages/{page}/preview
```

响应为 `image/png`。该接口要求认证，桌面端通过授权请求读取 Blob 后显示页面。页码无法可靠定位时不会伪造预览地址。

可选查询参数 `highlight` 会在 PDFium 文本层能够可靠定位时，把匹配文字直接标在返回的页面 PNG 上。参数最长 200 个字符，页面缓存文件名只保存查询哈希。

## Tasks

```http
GET  /v2/workspaces/{workspace_id}/tasks
POST /v2/workspaces/{workspace_id}/tasks
GET  /v2/workspaces/{workspace_id}/tasks/{task_id}
PUT  /v2/workspaces/{workspace_id}/tasks/{task_id}
POST /v2/workspaces/{workspace_id}/tasks/{task_id}/archive
GET  /v2/workspaces/{workspace_id}/tasks/{task_id}/markdown
```

保存和归档使用 `expected_revision`。修订不匹配时返回 `409`，调用方必须重新读取或显式恢复本地草稿，不能覆盖较新的任务。

任务项使用：

```text
document_id + content_hash + page_number + excerpt
```

文档删除或内容变化后，任务项会保留并标记为来源待重新确认，不会静默丢失。

## AI And Vision Authorization

```http
GET  /v2/workspaces/{workspace_id}/ai-settings
PUT  /v2/workspaces/{workspace_id}/ai-settings
POST /v2/workspaces/{workspace_id}/ai-settings/test
GET  /v2/workspaces/{workspace_id}/vision-authorization
PUT  /v2/workspaces/{workspace_id}/vision-authorization
```

视觉授权默认关闭。服务端在授权关闭时不得将任何页面图像发送给模型，即使 AI 文本辅助已启用。

## Errors

- `401`：本地 token 缺失或无效；
- `404`：资料空间、文档、页面或任务不存在；
- `409`：同步已在运行，或任务 revision 已变化；
- `422`：路径重叠、请求字段无效或 AI 配置缺少必要凭据。

错误响应可能包含技术细节。桌面端应将其映射为与当前操作相关的用户文案，不应直接展示内部路径、风险代码或 Python 异常。
