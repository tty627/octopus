# Windows 首次运行故障排查

向导先显示可执行的恢复动作；“显示技术详情”会展开稳定错误码和异常文本。报告错误时可
提供错误码，但不要提交文件路径、文件名、查询、内容或 API Key。

| 错误码 | 含义 | 恢复动作 |
| --- | --- | --- |
| `raw_missing` | 资料目录不存在 | 重新选择现有目录 |
| `raw_unreadable` | 无法读取部分或全部资料 | 关闭占用程序并检查当前用户读取权限 |
| `index_nested` | Raw/Index 相同或互相嵌套 | 选择同级、独立的 Index 目录 |
| `index_not_empty` | 新 Index 路径已有内容 | 选择新的空目录；不要手动清空不认识的目录 |
| `index_permission` | Index 位置不可写 | 改用“文档”或当前用户可写磁盘 |
| `disk_space` | 可用空间低于预检要求 | 释放空间或选择其他磁盘后重新预检 |
| `repository_locked` | 另一个更新任务正在运行 | 等待任务结束；仅在确认进程已退出后重试 |
| `parser_failure` | 单文件解析/OCR 失败 | 保留基础索引，关闭损坏/加密文件后用 CLI 重试 |
| `network_ai` | AI 网络或凭据不可用 | 首次向导不使用 AI；高级仓库可关闭 AI 后重试 |
| `unknown` | 未分类错误 | 展开技术详情并附本地 RunReport 报告问题 |

## 安全取消

点击取消后按钮会立即禁用。扫描、Leaf 和 FolderNode 阶段会在安全检查点终止；单个 OCR
调用不会被强杀。提交阶段开始后不能取消，以免产生半提交状态。取消报告位于 Index 的
`.octopus/runs/`，状态为 `cancelled`，Raw 文件哈希不应变化。

## 更新检查不可用

离线、GitHub 限流、超时或异常响应只会显示“暂时无法检查更新”。它不会阻止向导、索引
或搜索。需要立即重查时可运行 `octopus upgrade check --format json`。

## 技术诊断

```powershell
octopus doctor
octopus validate --format json
octopus report --last --format markdown
octopus diagnostics create --repository MyRepository --output .\octopus-diagnostics.zip
```

推荐优先使用脱敏诊断包；其字段范围、显式分享同意和迁移恢复步骤见
[本地诊断与迁移恢复](DIAGNOSTICS_AND_RECOVERY.md)。Octopus 不会自动上传这些文件。
