# Windows 安装与首次运行

## 支持范围

正式支持 Windows 11 Home/Pro x64。Windows 10 和 ARM64 不在 1.1 发布矩阵中。
离线安装器已包含 Python、Office/PDF 解析器和 OCR/ONNX 模型；首次流程不需要 Node.js、
API Key 或管理员权限。桌面界面使用系统 WebView2 Runtime；缺失时应用会显示安装指引。

## 安装前验证

`1.1.0.dev0` 本轮生成的是 unsigned 开发安装包，不应被当作正式签名发布。使用随包生成的
`SHA256SUMS.txt` 在 PowerShell 中验证完整性：

```powershell
Get-FileHash .\Octopus-1.1.0.dev0-win-x64-setup.exe -Algorithm SHA256
```

Hash 必须与校验文件一致。正式发布包还必须通过 Authenticode 与时间戳验证；开发包不会显示
有效签名，因此仅用于本机开发验证。

## 免安装运行

下载 `Octopus-<版本>-win-x64-portable.zip` 后，解压到任意可写目录，直接双击
`Octopus.exe`。免安装版与安装版使用相同的 `%APPDATA%\Octopus` 配置和资料空间，不需要
Python、Node.js 或管理员权限；不要在压缩包内部直接运行。

如果 GitHub 暂时没有 Release，可以在仓库页面点击 **Code → Download ZIP**，解压源码后
双击根目录的 `start-octopus.cmd`。首次运行会自动准备本地 Python 环境，后续直接启动。

## 安装与首次结果

1. 以标准用户运行安装器。程序安装到 `%LOCALAPPDATA%\Programs\Octopus`。
2. 从开始菜单打开 Octopus。
3. 选择“使用示例资料”或自己的资料文件夹；索引目录必须与资料目录分离且不互相嵌套。
4. 阅读文件数、格式、时间、磁盘和阻断项预检后开始。
5. 首批结果可用后进入工作台；搜索结果会按用途分组，单击后先在证据检查器核验。
6. 明确加入任务包后可编辑槽位、导出 Markdown，或只复制再次确认的来源副本。

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
