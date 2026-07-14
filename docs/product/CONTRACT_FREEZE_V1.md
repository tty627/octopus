# v1 契约冻结说明

`0.9.0rc1` 冻结 v1.0 首发所依赖的持久化和公开边界。机器可读真值位于 [`contract-freeze-v1.json`](contract-freeze-v1.json)，`octopus release-audit` 将它与运行时常量逐项比较。

## 冻结范围

- 全局配置、Repository Config/State 与 Markdown Index Schema 均为 `0.2`。
- Local API 为 `1.0`；v1 内只能新增端点和可选字段。
- Plugin API 为 `1.0`，四项权限名和宿主代理语义冻结；Python Worker 仍不是恶意代码 OS 沙箱。
- Search Report 为 `1.0`；SQLite 搜索缓存 `0.5` 可删除重建，不属于不可逆持久化承诺。
- 诊断包为 `1.0`，继续禁止路径、内容、查询、消息和凭据进入包体。

## 变更规则

- v1.0 之前发现 P0/P1 契约缺陷时，只允许修复发布阻断，并同步更新冻结 JSON、兼容矩阵、迁移说明和测试。
- v1.0 后删除字段、改变既有含义、扩大默认 Plugin 权限或让旧版本误写新格式，必须进入新的主版本或提供显式双写/迁移窗口。
- 新增可选字段不得改变旧客户端对既有响应的解释；未知较新持久化 Schema 必须在写前拒绝。
