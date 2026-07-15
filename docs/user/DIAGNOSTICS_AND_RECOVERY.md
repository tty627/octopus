# 本地诊断与迁移恢复

> 本文仅适用于 V1 Raw/Index 回滚 CLI。V2 桌面资料空间请先查看
> [V2 故障排查](TROUBLESHOOTING.md)；不要对 `%LOCALAPPDATA%\Octopus\workspaces`
> 套用本文的 Index、Leaf、FolderNode 或 migration run 操作。

## 保存诊断包

桌面端“状态中心”选择“保存本地诊断包…”，或运行：

```powershell
octopus diagnostics create --repository MyRepository --output .\octopus-diagnostics.zip
octopus diagnostics inspect .\octopus-diagnostics.zip
```

诊断包只包含产品/Schema 版本、操作系统与 Python/SQLite 版本、匿名仓库序号、节点/队列计数，以及最近运行的状态、耗时、数值统计和规范化错误码。它不包含：

- Raw/Index 路径、文件名、节点 ID 或仓库 ID；
- 查询、摘要、证据摘录、文件内容或错误消息；
- API Key、Bearer token、用户名或计算机名。

创建和检查始终在本地完成。Octopus 没有自动上传诊断包的端点。若决定手工分享，可创建一份带同意回执的新副本：

```powershell
octopus diagnostics prepare-share .\octopus-diagnostics.zip `
  --output .\octopus-diagnostics-consented.zip `
  --consent
```

缺少 `--consent` 时命令拒绝执行；即使成功也只写本地文件，不会联网。

## 迁移 dry-run、应用与回滚

默认迁移命令只生成计划：

```powershell
octopus migrate --all --format json
octopus migrate --all --apply --format json
```

应用前，每个目标都复制到本次 run 目录并记录迁移前、备份和迁移后的 SHA-256。迁移中任一步失败会自动恢复全部目标。成功后需要回退时使用报告中的 32 位 run ID：

```powershell
octopus migrate --rollback MIGRATION_RUN_ID --format markdown
```

若备份校验失败、报告路径不属于已注册仓库、迁移后的目标已经被修改，或该 run 已回滚，命令会拒绝覆盖。迁移和回滚只处理全局配置/Index 元数据，不读取或写入 Raw。

## 权限与同步盘恢复

看到 `scan_access_denied` 时，先确认同步客户端已下载文件、当前用户可读，并关闭占用应用；随后重试更新。Octopus 会保留之前的节点身份和索引，不会因为一次暂时不可读就把整棵子树标记为删除。
