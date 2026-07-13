# Windows 安装与首次运行

## 支持范围

正式支持 Windows 11 Home/Pro x64。Windows 10 和 ARM64 不在 v0.4 发布矩阵中。
离线安装器已包含 Python、Office/PDF 解析器和 OCR/ONNX 模型；首次流程不需要 Node.js、
API Key 或管理员权限。

## 安装前验证

从 GitHub Release 下载安装器和 `SHA256SUMS.txt`，在 PowerShell 中验证：

```powershell
Get-FileHash .\Octopus-0.4.0-win-x64-setup.exe -Algorithm SHA256
Get-AuthenticodeSignature .\Octopus-0.4.0-win-x64-setup.exe | Format-List Status,SignerCertificate,TimeStamperCertificate
```

Hash 必须与校验文件一致，签名状态必须是 `Valid`，并包含时间戳证书。未通过时不要运行。

## 安装与首次结果

1. 以标准用户运行安装器。程序安装到 `%LOCALAPPDATA%\Programs\Octopus`。
2. 从开始菜单打开 Octopus。
3. 选择“使用内置示例”或自己的资料目录；索引目录必须与资料目录同级且不嵌套。
4. 阅读文件数、格式、P50/P95 时间、磁盘和 `0` 次 AI 预检后开始。
5. 完成后搜索关键词，选择前五条结果之一，再打开索引结果或原文件。

示例会复制到“文档”目录中的唯一文件夹；若名称已存在会改用新编号，绝不覆盖。向导创建
的仓库默认关闭 AI。取消发生在提交前时，Octopus 回滚未提交索引并保留仓库注册，可一键
重试；处理 OCR 文件时会先安全完成当前文件。

## 命令行与更新检查

安装目录包含 `octopus-cli.exe`，并提供用户命令入口 `octopus.cmd`。Windows 文件名不区分
大小写，因此 GUI `Octopus.exe` 与 CLI 不能同时以仅大小写不同的名称存放在共享目录。

```powershell
octopus version
octopus upgrade check --format table
octopus upgrade check --format json
```

更新检查只访问 `api.github.com/repos/tty627/octopus/releases/latest`，3 秒超时并缓存 24
小时。它只显示发布说明和浏览器入口，不会下载或安装；离线或限流不影响启动和索引。

## 卸载与重装

卸载器会停止 Octopus 启动的 watcher/API 后台进程并删除程序文件，但保留：

- `%APPDATA%\Octopus` 配置、升级缓存和本地验收记录；
- 用户 Raw、Index 和示例资料；
- 任何非 Octopus 用户内容。

重装后程序会读取原配置并识别已注册仓库。
