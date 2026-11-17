# SD WebUI Model Downloader — API Auth + aria2

基于 [tzwm/sd-webui-model-downloader-cn](https://github.com/tzwm/sd-webui-model-downloader-cn) 改进的 Automatic1111 WebUI 模型下载扩展。原作者为 **@tzwm**，本改进版由 **@liuliuliu-a11y** 维护。

使用 Civitai 官方 API 和用户自己的 API Key 获取模型信息，再通过 aria2 多连接下载模型。操作流程为“粘贴模型链接 → 预览 → 下载”。

## 为什么做这个改进版

原项目让用户可以在 WebUI 内预览模型、自动选择保存目录并下载文件。上游 README 后来注明，Civitai 调整了登录验证，旧版接口已无法正常工作。本次本地排查也遇到了旧中转服务连接失败和模型文件传输中断。

这个改进版面向已经拥有 Civitai 账号、希望继续在原有 WebUI 工作流中下载模型的用户。我们将账号鉴权和模型信息查询接到官方 API，恢复原版可选的 aria2 多连接下载思路，并补上可验证的断点续传和文件校验。

## 借鉴关系与修改范围

| 来源 | 具体内容 |
| --- | --- |
| 原作者 @tzwm 的项目 | WebUI 扩展结构、“模型下载”标签页、链接预览与下载流程、模型类型识别、保存目录规则、模型信息和预览图展示 |
| 原版已有的下载方案 | 优先调用 aria2，使用最多 16 连接分段下载的思路 |
| 本改进版的实现 | Civitai 官方 REST API、页面会话 API Key、账号鉴权后获取文件地址、aria2 进程接入、断点文件管理、SHA256 校验、错误处理和安装验证 |
| 面向 AI / Agent 的补充 | `AGENTS.md` 安装约定、可复制的安装请求、非交互式 `install.py`、JSON 结果和真实引擎续传测试 |

本仓库沿用原项目名称和上游历史，原作者署名及 AGPL-3.0 许可随源码保留。原版 README 完整保存在 `docs/UPSTREAM_README.md`，便于了解原始项目。

## 改进内容

- 对接 Civitai 官方模型和模型版本 API。
- 在可折叠的“Civitai 账号”区域填写 API Key，密钥保存在当前页面会话中。
- 使用 Authorization Bearer 鉴权，账号密钥用于 Civitai 官方请求。
- 自动识别模型类型并保存到 WebUI 配置的模型目录。
- aria2 最多 16 连接分段下载，支持 `.part` 和 `.part.aria2` 断点续传。
- 下载后校验 Civitai 提供的 SHA256，校验通过后生成正式模型文件。
- 提供 Windows 64 位 aria2 自动安装脚本，以及供 Agent 使用的非交互式命令和 JSON 检查结果。
- 预览图请求失败时继续提供模型下载功能。

下载使用 Civitai 官方文件服务器，速度由本机网络、代理线路、服务器限速及资源大小共同决定。访问链路需要能够连接 Civitai 及其文件 CDN。

## 安装

### 通过 WebUI 安装

在 WebUI 的“扩展 → 从网址安装”中填写本仓库地址。

```text
https://github.com/liuliuliu-a11y/sd-webui-model-downloader-cn
```

安装后重启 WebUI。扩展的 `install.py` 会检查 aria2，Windows 64 位环境会按需下载官方组件，并校验 SHA256。

### 命令行安装

在现有 WebUI 根目录执行。

```powershell
git clone https://github.com/liuliuliu-a11y/sd-webui-model-downloader-cn.git extensions/sd-webui-model-downloader-cn
```

使用该 WebUI 的 Python 运行安装脚本。秋叶整合包示例如下。

```powershell
& '.\python\python.exe' '.\extensions\sd-webui-model-downloader-cn\install.py' --json
& '.\python\python.exe' '.\extensions\sd-webui-model-downloader-cn\install.py' --check --json
```

普通 venv 安装可使用 `venv/Scripts/python.exe`。安装检查应返回退出码 0 和 `"status": "ready"`。当前生成与下载任务结束后，重载 WebUI 并刷新浏览器。

Linux、macOS 和其他平台可通过系统包管理器安装 `aria2`，并确保 `aria2c` 可从 PATH 调用。扩展在 aria2 可用时使用多连接下载，并保留 Python 下载路径供兼容场景使用。

## 让 Agent 帮你安装

将以下内容发给你的 Agent。

```text
请将 https://github.com/liuliuliu-a11y/sd-webui-model-downloader-cn
安装到我正在使用的 Automatic1111 Stable Diffusion WebUI。

先阅读仓库的 AGENTS.md，确认现有 WebUI 根目录和它自带的 Python。
将扩展安装到 extensions/sd-webui-model-downloader-cn。
使用 WebUI 的 Python 执行 install.py --json，再执行 install.py --check --json。
检查结果需要为 ready。当前任务结束后重载 WebUI，并验证“模型下载”标签页。
API Key 由我在本地页面填写。请说明安装路径、aria2 版本和验证结果。
```

完整的 Agent 工作约定见 [AGENTS.md](AGENTS.md)。安装脚本的 `--help` 提供参数说明，`--check` 执行组件检查，`--json` 提供机器可读结果。

Agent 可直接读取以下纯文本入口。

```text
https://raw.githubusercontent.com/liuliuliu-a11y/sd-webui-model-downloader-cn/main/AGENTS.md
https://raw.githubusercontent.com/liuliuliu-a11y/sd-webui-model-downloader-cn/main/README.md
```

## 使用

1. 登录 Civitai，在 [账号设置](https://civitai.com/user/account) 的 API Keys 区域创建密钥。
2. 打开 WebUI 的“模型下载”，展开“Civitai 账号”，在密码输入框填写 API Key。
3. 粘贴 Civitai 模型页面链接，点击“预览”，核对模型和文件信息后点击“下载”。

API Key 会在页面刷新或 WebUI 重载后清空，需要重新填写。扩展通过本机环境及系统代理配置访问网络。

带有 `?modelVersionId=版本编号` 的页面链接会选择指定模型版本。使用模型页面默认链接时，扩展选择 API 返回列表中的第一个版本。请核对预览结果中的文件名。

### 默认保存位置

| 模型类型 | WebUI 下的默认目录 |
| --- | --- |
| Checkpoint | `models/Stable-diffusion` |
| LoRA | `models/Lora` |
| LyCORIS / LoCon | `models/LyCORIS` |
| VAE | `models/VAE` |
| Textual Inversion | `embeddings` |
| Hypernetwork | `models/hypernetworks` |

WebUI 启动参数配置的自定义目录优先生效。

### 下载完成与续传

下载期间会生成 `<模型文件名>.part` 和对应的 `.part.aria2` 进度文件。再次下载相同文件时，aria2 会读取断点继续传输。

完整下载并通过 SHA256 校验后，扩展会生成正式的 `.safetensors`、`.ckpt` 或其他模型文件，并显示下载成功。已存在的正式模型会显示文件路径，方便检查。

## 验证范围

开发验证覆盖官方 API 请求、账号鉴权头、版本选择、错误响应、文件校验、下载中断、重复下载，以及真实 aria2 进程的 HTTP Range 分段和续传。

一次 Civitai 公共 LoRA 下载测试建立了 16 条连接，下载 18,986,312 字节并通过 SHA256 校验，耗时约 132 秒。该结果对应测试时的线路状态。

本机还通过 aria2 续传完成了 `Realistic_Vision_V5.1_Hyper_pruned.safetensors`，文件大小为 2,132,625,894 字节，SHA256 与 Civitai 官方元数据一致。测试覆盖 Windows 秋叶整合包 WebUI 1.10.1、Python 3.11.9、Gradio 3.41.2 和 aria2 1.37.0。

```powershell
& '<WebUI Python>' -m unittest discover -s tests -q
& '<WebUI Python>' install.py --check --json
```

## 上游与许可

原项目和界面设计来自 **@tzwm**。保留原始 [AGPL-3.0 许可](LICENSE)，原版说明保存在 [上游 README](docs/UPSTREAM_README.md)。

本改进版增加官方 API 适配、账号鉴权、aria2 接入、断点续传校验和 Agent 安装支持。aria2 来自 [官方 Release](https://github.com/aria2/aria2/releases/tag/release-1.37.0)，组件许可保存在 `bin/aria2-COPYING.txt`。Windows 安装包 SHA256 同时可在 [Microsoft WinGet 清单](https://github.com/microsoft/winget-pkgs/blob/master/manifests/a/aria2/aria2/1.37.0/aria2.aria2.installer.yaml)核对。
