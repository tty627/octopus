# Octopus Plugin SDK v1

Plugin SDK v1 让扩展通过脱敏资源和声明式操作复用 Octopus 能力。它在 v0.7 以开发者预览引入，并于 `0.9.0rc1` 冻结为 v1.0 稳定候选；当前 API 版本为 `1.0`。它不是面向不可信恶意代码的完整操作系统沙箱。

## 最小示例

一个 Plugin 是包含 `plugin.json` 和 Python 入口文件的目录：

```json
{
  "schema_version": "1.0",
  "plugin_id": "example.timeline",
  "name": "Example Timeline",
  "version": "1.0.0",
  "plugin_api": ">=1.0,<2.0",
  "entrypoint": "plugin.py",
  "description": "Build a timeline.",
  "permissions": ["index.timeline", "export.write"]
}
```

入口从 `OCTOPUS_PLUGIN_REQUEST` 读取 JSON，并把响应 JSON 写入 `OCTOPUS_PLUGIN_RESPONSE`。入口不能直接写导出目录；宿主验证响应中的全部操作后才执行文件写入或复制。

```python
import json
import os
from pathlib import Path

request = json.loads(Path(os.environ["OCTOPUS_PLUGIN_REQUEST"]).read_text("utf-8"))
response = {
    "summary": "Timeline ready.",
    "operations": [
        {"operation": "export_text", "path": "timeline.md", "content": "# Timeline\n"}
    ],
}
Path(os.environ["OCTOPUS_PLUGIN_RESPONSE"]).write_text(json.dumps(response), "utf-8")
```

## 权限

| 权限 | Plugin 收到的能力 | 不会收到的能力 |
| --- | --- | --- |
| `index.query` | 查询结果的节点 ID、名称、索引类型、摘要、状态、风险和最多五条证据摘录 | Raw/Index 绝对路径、源 URI、完整内部状态 |
| `index.timeline` | 节点 ID、文件基名、类型、状态和修改时间 | Raw 相对目录、源 URI、文件内容 |
| `export.write` | 请求宿主在空的授权目录中写入不超过 1 MB 的 UTF-8 文本 | 任意文件系统写权限、覆盖已有文件 |
| `export.copy_confirmed` | 请求宿主复制本次调用中用户明确确认的查询节点 | 源绝对路径、未确认节点复制、Raw 写权限 |

Manifest 中声明的权限必须全部通过 `--grant` 显式授予。版本不兼容或权限缺失时，宿主在创建子进程之前拒绝执行。

## 请求与响应契约

请求顶层字段包括：

- `plugin_api_version`：当前为 `1.0`。
- `invocation_id`：不含路径或用户内容的随机调用标识。
- `plugin`：Plugin ID 与版本。
- `resources`：只包含已声明并授权的脱敏资源。

响应只接受：

- `summary`：简短结果说明；进入报告前会做路径和凭据模式脱敏。
- `operations`：最多 1000 个 `export_text` 或 `copy_source` 操作。

导出路径必须是安全的 POSIX 相对路径，不得包含 `..`、绝对路径或重复目标。宿主先预检全部操作，再执行第一次写入，因此权限或确认失败不会留下部分结果。

## CLI

```powershell
octopus plugin list
octopus plugin inspect .\my-plugin
octopus plugin run .\my-plugin `
  --repository MyRepository `
  --export .\plugin-output `
  --grant index.timeline `
  --grant export.write
```

Package 参考 Plugin 还需要查询和逐节点确认：

```powershell
octopus plugin run .\plugins\package `
  --repository MyRepository `
  --query "项目预算" `
  --confirm NODE_ID `
  --grant index.query `
  --grant export.write `
  --grant export.copy_confirmed `
  --export .\package-output
```

每次调用的导出目录必须为空。`octopus plugin inspect` 只解析和验证 Manifest，不执行入口代码。

## 进程与安全边界

- Plugin 在独立子进程中运行；崩溃和超时不会进入核心更新事务。
- 子进程继承一个最小环境，不继承 `DEEPSEEK_API_KEY`、认证头或父进程的其他凭据。
- Python 审计钩子阻止 Plugin 读取授权请求、Plugin/运行时目录以外的文件，阻止响应文件以外的写入，并阻止 socket、子进程、`os.system` 和动态库加载事件。
- 宿主不把 Raw 路径或源 URI交给 Plugin；源文件复制由宿主依据节点 ID、显式确认和内部 URI 完成。
- stdout/stderr 有大小上限，并在进入错误报告前脱敏绝对路径和常见凭据格式。

Python 审计钩子不是安全内核，也不能替代 Windows Sandbox、低完整性令牌、AppContainer 或虚拟机。v0.7 仅支持随 Octopus 发布或由用户审阅后本地安装的 Plugin；不自动下载或执行市场中的未知代码。

## 兼容性

Plugin 必须使用 PEP 440 范围声明 `plugin_api`。Octopus v0.7 对 Plugin API `1.x` 采用以下策略：

- 增加可选请求字段或可选响应字段可以保持次版本兼容。
- 删除字段、改变字段语义或扩大默认权限需要新的 Plugin API 主版本。
- 不兼容 Plugin 被明确禁用；宿主不对 Manifest 或操作做隐式迁移。

参考实现位于 [`plugins/package`](../../plugins/package/) 和 [`plugins/timeline`](../../plugins/timeline/)。
