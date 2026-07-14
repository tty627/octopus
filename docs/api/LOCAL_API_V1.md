# Octopus Local API v1 契约

## 稳定性

v0.6 起，桌面端只通过 loopback Local API v1 调用仓库核心能力。`api_version` 为路由主版本，
`contract_version` 当前为 `1.0`；同一 v1 内只允许增加可选字段或新端点，不得删除字段、改变
既有字段类型或改变成功状态码语义。破坏性变更必须进入 `/v2`。

服务仅绑定数值 loopback 地址，除 `/v1/health` 外均要求本机 Bearer token。Token 不写入日志、
报告或 API 响应。`GET /v1/contract` 返回桌面端握手所需版本与能力列表。

## 核心端点

| 方法 | 路径 | 用途 | 成功响应 |
| --- | --- | --- | --- |
| GET | `/v1/health` | 进程与版本健康检查 | 200 |
| GET | `/v1/contract` | 契约握手与能力发现 | 200 |
| GET/POST | `/v1/repositories` | 列表 / 创建并可异步首建 | 200 / 201 |
| GET | `/v1/repositories/{id}` | 状态、队列和路径快照 | 200 |
| POST | `/v1/repositories/{id}/updates` | 更新、重试或 dry-run | 202 Job |
| POST | `/v1/repositories/{id}/search` | 统一 `SearchReport` | 200 |
| POST | `/v1/repositories/{id}/validate` | 只读校验 | 200 |
| POST | `/v1/repositories/{id}/rebuild-search` | 修复派生搜索缓存 | 202 Job |
| GET | `/v1/repositories/{id}/reports/latest` | 最近不可变运行报告 | 200/404 |
| GET | `/v1/jobs/{job_id}` | 异步任务状态 | 200 |
| GET | `/v1/migrations` | 只读迁移计划 | 200 |
| POST | `/v1/diagnostics` | 在用户指定位置创建本地脱敏诊断包 | 200 |

创建仓库固定 `ai_enabled=false` 且要求空 Index 目录。更新和修复以 Job 返回，桌面端轮询到
`succeeded` 或 `failed`；桌面进程崩溃不会取消服务中的提交事务。

## 错误与恢复

- `401`：本地凭据不匹配，桌面端应重启并重新读取凭据。
- `404`：仓库、Job 或最近报告不存在。
- `409`：同仓库已有更新任务，等待完成后重试。
- `422`：路径、迁移或互斥参数不满足；Raw 不会因此被写入。
- 连接失败：可重新启动/连接本地服务，现有仓库保持不变。

AI 请求失败时搜索仍返回 `actual_mode=degraded` 的本地结果，并在
`degradation_reason` 中提供稳定错误码。

## 工程验证

自动化测试冻结 v1 核心路由、创建/更新/搜索/校验/修复工作流、Bearer 鉴权、中文与空格路径、
Raw 字节不变、桌面控制器 API 等价性以及契约版本握手。OpenAPI 可从已鉴权的
`GET /v1/openapi.json` 获取。

诊断端点只接受已注册仓库 ID 和输出路径，响应不回显绝对路径，只返回文件名、`local_only`
和 `uploaded=false`。端点不具备上传能力；输出已存在或仓库无效时拒绝覆盖。
