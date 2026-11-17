# Agent 安装与维护说明

## 项目用途

这是 tzwm/sd-webui-model-downloader-cn 的改进版，提供 Civitai 官方 API、账号 API Key 鉴权和 aria2 多连接下载。扩展入口位于 `scripts/model-downloader-cn.py`，安装入口位于 `install.py`。

## 安装到现有 WebUI

1. 找到用户正在使用的 Automatic1111 WebUI 根目录，核对 `launch.py`、`modules` 和 `extensions`。
2. 将本仓库克隆到该 WebUI 的 `extensions/sd-webui-model-downloader-cn`。已有该目录时，先核对 Git remote、工作区状态和正在运行的下载任务，再决定更新方式。
3. 使用该 WebUI 自带的 Python 运行本仓库的 `install.py --json`。秋叶整合包通常使用 `<WebUI>/python/python.exe`，普通安装通常使用 `<WebUI>/venv/Scripts/python.exe`。
4. 再运行 `install.py --check --json`，要求退出码为 0 且 JSON 的 `status` 为 `ready`。
5. 当前生成和下载任务结束后重载 WebUI，并刷新浏览器。检查“模型下载”标签页和版本标识。
6. 用户在本地页面的“Civitai 账号”中填写自己的 API Key。账号密钥由用户管理。
7. 使用用户指定的模型链接执行“预览 → 下载”。下载完成以正式模型文件、成功提示和 SHA256 校验为准。

## 命令与结果

```powershell
& '<WebUI>/python/python.exe' '<Extension>/install.py' --json
& '<WebUI>/python/python.exe' '<Extension>/install.py' --check --json
```

命令为非交互式，可重复执行。退出码 0 表示 aria2 就绪，退出码 1 表示缺少组件或安装失败。JSON 写到 stdout，安装诊断写到 stderr。`--check` 执行检查。

Windows 64 位的安装脚本从 aria2 官方 GitHub Release 下载固定版本，校验压缩包及可执行文件的 SHA256，并在扩展自身的 `bin` 下安装。Linux 和其他平台可使用系统安装的 `aria2c`。

## 下载状态

正式模型路径由 WebUI 的目录配置和模型类型决定。Checkpoint 默认保存到 `models/Stable-diffusion`，LoRA 默认保存到 `models/Lora`。

`<模型文件名>.part` 保存下载中的数据，`.part.aria2` 保存分段进度。重试同一个模型时保留这两个文件，以便续传。成功下载并通过校验后，扩展将 `.part` 重命名为正式文件。

## 修改与发布

保留原作者署名和仓库中的 AGPL-3.0 许可。发布包包含完整修改源码、安装脚本和使用说明。aria2 的 GPL 许可保存在 `bin/aria2-COPYING.txt`。

仅提交本项目源码、测试和文档。账号密钥、代理凭据、模型文件和本地备份属于运行时数据。处理日志与截图时检查密钥输入框和下载签名地址。

## 验证

```powershell
& '<WebUI>/python/python.exe' -m unittest discover -s tests -q
& '<WebUI>/python/python.exe' install.py --check --json
git diff --check
```

测试包含真实 aria2 的本地 HTTP Range 下载及断点续传，测试文件使用临时目录。发布说明应区分已验证的功能和实际线路测速结果。
