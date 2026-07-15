# Octopus V2 故障排查

## 应用无法启动

1. 确认 Windows 11 x64 已安装 WebView2 Runtime。
2. 结束仍在运行的旧版 Octopus 后重试。
3. 如果刚升级，重新启动应用，桌面端会检查并重启版本不匹配的本地 API。
4. 查看 `%APPDATA%\Octopus\api.log` 的最后一段错误。

原始资料和任务不会因为桌面窗口启动失败而被删除。

## 安装包打不开

开发预览版未签名，Windows 可能显示 SmartScreen。先使用 `SHA256SUMS.txt` 校验安装包，不要使用来源不明的副本。

```powershell
Get-FileHash .\Octopus-2.0.0.dev1-win-x64-setup.exe -Algorithm SHA256
```

Hash 一致后，按 [Windows 安装说明](WINDOWS_INSTALLATION.md) 操作。正式签名版本不应要求绕过无效签名或损坏文件警告。

## 无法添加资料文件夹

常见原因：

- 文件夹不存在或当前用户不可读；
- 选择了已经导入的同一范围；
- 新文件夹与已有资料空间互为父子目录；
- 网络盘或同步盘暂时离线。

选择一个实际包含资料、且不与已有资料空间重叠的目录。Octopus 不需要单独选择 Index 目录。

## 同步看起来停住

长 PDF 可能正在逐页渲染或 OCR。资料页应显示当前文件、阶段、页码和总页数。不要仅因为同一文件停留数分钟就强制结束进程。

如果进度长时间完全不变化：

1. 查看文件是否加密、损坏或被其他程序独占；
2. 等待当前任务失败或完成；
3. 使用单文件“重新处理”；
4. 查看 `%APPDATA%\Octopus\api.log`。

## PDF 页面预览不可用

先确认：

- 文档状态不是“处理失败”；
- 搜索结果有可靠页码；
- 页面缓存目录中存在 PNG；
- 本地 API 仍在运行。

页面缓存位于：

```text
%LOCALAPPDATA%\Octopus\workspaces\<workspace_id>\previews
```

缓存可删除后重新处理文档，但不要删除 `%APPDATA%\Octopus\workspaces\<workspace_id>\tasks`。

## 搜索只有文件名，没有正文

Office 和图片在当前开发里程碑中可能显示“仅文件信息”。这不是失败，表示文件名和元数据可搜索，但正文解析尚未接入。

PDF 或文本显示“识别质量低”时，低质量正文不会参与排名，以避免乱码污染结果。可以按文件名查找或重新处理。

## 任务没有立即保存

任务页会显示“保存中”“已保存”“本地草稿”或“保存冲突”。关闭应用前应看到“已保存”，但本地草稿会在编辑时同步保留。

发生冲突时选择恢复本地草稿或保留服务端版本。不要重复快速点击“加入任务”或归档按钮来试图绕过冲突。

## AI 设置无法保存

- 启用辅助整理时必须有有效 API Key；
- Base URL 必须是受支持的 HTTPS 地址；
- 模型名称不能为空；
- 测试连接失败不影响本地搜索；
- 页面图像授权与文本 AI 设置分开保存并显示权威状态。

不要在 issue、截图或诊断中提交 API Key。

## 技术诊断

桌面 V2 的主要本地状态：

```text
%APPDATA%\Octopus\config.json
%APPDATA%\Octopus\api.log
%APPDATA%\Octopus\ui-state.json
%LOCALAPPDATA%\Octopus\workspaces
```

旧 CLI 诊断命令仍用于 V1 回滚资料空间：

```powershell
octopus doctor
octopus validate --format json
octopus diagnostics create --repository MyRepository --output .\octopus-diagnostics.zip
```

Octopus 不会自动上传日志或诊断文件。分享前检查其中是否包含私人路径或文件名。
