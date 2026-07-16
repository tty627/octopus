# Windows 安装与首次运行

## 支持范围

`2.1.0.dev0` 支持 Windows 11 x64。安装包包含 Python 运行时、PDF/Office/图片/ZIP 解析依赖、本地 OCR 和桌面资源，不需要用户安装 Node.js 或配置 API Key。

桌面界面依赖 Microsoft WebView2 Runtime。多数 Windows 11 设备已经包含该组件；缺失时应用会显示启动错误。

## 下载

从 [GitHub Releases](https://github.com/tty627/octopus/releases) 获取：

- `Octopus-2.1.0.dev0-win-x64-setup.exe`；
- 或 `Octopus-2.1.0.dev0-win-x64-portable.zip`；
- `SHA256SUMS.txt`。

不要从聊天附件、网盘转存或不明镜像运行安装包。

## 校验

开发预览版未签名，安装前应校验 SHA-256：

```powershell
Get-FileHash .\Octopus-2.1.0.dev0-win-x64-setup.exe -Algorithm SHA256
```

输出必须与 `SHA256SUMS.txt` 中对应文件一致。Hash 不一致时删除该文件并从 GitHub Release 重新下载。

## 安装版

1. 双击 setup 文件。
2. 选择当前用户安装，不需要管理员权限。
3. 默认安装到 `%LOCALAPPDATA%\Programs\Octopus`。
4. 安装完成后从开始菜单打开 Octopus。
5. 填写资料空间名称并选择原始资料文件夹。
6. 创建后进入“资料”页，等待后台同步显示文档状态。

未签名开发版可能触发 SmartScreen。确认文件来自官方 Release 且 Hash 一致后再决定是否运行；不要绕过证书无效、Hash 不一致或文件损坏警告。

## 免安装版

1. 解压 portable zip 到普通可写目录。
2. 不要在压缩包内部直接运行。
3. 双击 `Octopus.exe`。

安装版和 portable 版使用相同的用户配置、缓存和任务目录。切换入口不会自动复制或删除资料。

## 从源码启动

GitHub Release 暂不可用时，可以下载仓库 ZIP，解压后双击：

```text
start-octopus.cmd
```

首次运行会准备本地 Python 环境。后续启动不会重复安装全部依赖。

## 首次同步

Octopus 只要求原始资料文件夹，不再要求 Index 路径。内部数据位置：

```text
%LOCALAPPDATA%\Octopus\workspaces   可重建缓存和页面预览
%APPDATA%\Octopus\workspaces        用户任务
```

PDF、文本、Word、Excel、PowerPoint 和常见图片会进行正文处理。ZIP 容器中的支持格式以独立成员文档进入搜索；加密、损坏或超过安全限制的 ZIP 会显示明确警告。

## 升级、重装与卸载

升级安装前可以直接退出旧版并运行新安装包。桌面端发现本地 API 版本不一致时会重启服务。

卸载会删除程序文件，但保留：

- 原始资料；
- `%APPDATA%\Octopus` 中的配置和任务；
- `%LOCALAPPDATA%\Octopus\workspaces` 中的可重建缓存；
- V1 `*-Octopus-Index` 目录。

重装后会重新读取这些资料空间。需要彻底清除用户数据时，应先备份任务，再由用户手工删除对应目录。

## 安装仍失败

查看 [故障排查](TROUBLESHOOTING.md)，并记录：

- Windows 版本和架构；
- setup 文件 SHA-256；
- 安装器显示的具体错误；
- `%APPDATA%\Octopus\api.log` 的相关时间段。

不要公开上传 API Key、完整私人路径或原始资料。
