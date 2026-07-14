# v0.5 检索工程评测基线

## 结论

`0.5.0a1` 在 2026-07-14 通过版本化离线检索门槛。该结果是可复现的工程评测，
不是用户研究，也不代表真实用户定位时间。

| 指标 | 门槛 | 实测 |
| --- | ---: | ---: |
| Top-5 命中率 | ≥80% | 100%（10/10） |
| MRR | ≥0.65 | 1.00 |
| Top-5 任务失败 | — | 0 |
| 文件名基线检查步数减少 | ≥30% | 84.1% |
| 解释契约失败 | 0 | 0 |
| 单文件增量更新 P95 | ≤30 秒 | 48.8 ms |

## 评测资产

- 数据集：`benchmarks/datasets/search-value-v1.json`
- 数据集标识/版本：`octopus-search-value` / `1.0.0`
- 检索算法：`fts5-bm25-explain-v1`
- 工程报告：`.octopus-dev/benchmarks/search-value-v05.json`（本地生成，不入库）
- 增量报告：`.octopus-dev/benchmarks/incremental-v05.json`（本地生成，不入库）

数据集包含 12 个确定性生成文档和 10 个任务，覆盖中英文、DOCX、XLSX、同名文件、
过期索引、错误码和精确数值。评测禁用网络 AI，报告不记录临时目录或用户路径。

## 解释契约

每个相关结果必须同时提供源相对路径、可打开 URI、字段级命中证据和解析证据；过期任务
还必须带 `stale_index` 风险标志。API 和 CLI 返回同一完整结果模型，
`octopus search --open-result N` 使用契约中的打开目标。

## 复现

```powershell
octopus evaluate-search `
  --output .octopus-dev\benchmarks\search-value-v05.json `
  --enforce

python -m benchmarks.benchmark_incremental `
  --repeats 5 --warmups 1 `
  --output .octopus-dev\benchmarks\incremental-v05.json `
  --enforce
```

数据集会在隔离临时目录中物化 Raw/Index Repository，AI 固定关闭，并在评测结束后清理。
如需排查，可用 `--workspace <空目录>` 保留生成仓库。

## 限制

- “检查步数减少”按确定性文件名排序基线计算，不等同于真人用时减少。
- 数据集规模小且由项目维护者标注，适合回归门禁，不足以证明外部效度。
- 真人定位时间研究已由产品负责人取消，因此没有采集、推断或伪造相应指标。
