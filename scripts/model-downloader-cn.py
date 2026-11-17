from modules.paths_internal import models_path, data_path
from modules import script_callbacks, shared
from PIL import Image
from io import BytesIO
from urllib.parse import parse_qs, urlsplit
import numpy as np
import gradio as gr
import requests
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import threading


API_URL = "https://civitai.com/api/v1/"
VERSION = "v1.1.4-local-auth-aria2"
DOWNLOAD_LOCK = threading.Lock()


class DownloaderError(Exception):
    pass


def request_options(api_key=""):
    key = (api_key or "").strip()
    if any(character.isspace() for character in key):
        raise DownloaderError("API Key 含有空白字符，请重新复制完整密钥。")
    headers = {"User-Agent": "sd-webui-model-downloader-cn/1.1.4-local-auth"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return {"headers": headers, "timeout": (10, 60)}


def check_response(response):
    messages = {
        401: "账号验证失败，请在 Civitai 账号设置中生成有效的 API Key。",
        403: "Civitai 拒绝访问，请确认账号拥有该模型的下载权限，并检查网络代理。",
        404: "模型或版本不存在，可能已经下架或设为私有。",
        429: "Civitai 请求过于频繁，请稍后重试。",
    }
    if response.status_code >= 400:
        raise DownloaderError(messages.get(response.status_code, f"Civitai 返回 HTTP {response.status_code}，请稍后重试。"))


def network_message(error):
    if isinstance(error, requests.Timeout):
        return "连接 Civitai 超时，请检查网络和本机代理。"
    return "连接 Civitai 失败，请检查网络和本机代理。"


def api_get(path, api_key=""):
    with requests.get(API_URL + path, **request_options(api_key)) as response:
        check_response(response)
        try:
            data = response.json()
        except ValueError:
            raise DownloaderError("Civitai 返回的数据格式异常，请检查网络后重试。") from None
        if not isinstance(data, dict):
            raise DownloaderError("Civitai 返回的数据格式异常，请稍后重试。")
        return data


def process_image(url):
    # Cover image hosts receive no account authorization header.
    with requests.get(url, timeout=(10, 30)) as response:
        check_response(response)
        with Image.open(BytesIO(response.content)) as image:
            return image.convert("RGB")


def get_model_path(model_type):
    co = shared.cmd_opts
    pj = os.path.join
    model_type_dir = {
        "Checkpoint": ["ckpt_dir", pj(models_path, "Stable-diffusion")],
        "LORA": ["lora_dir", pj(models_path, "Lora")],
        "TextualInversion": ["embeddings_dir", pj(data_path, "embeddings")],
        "Hypernetwork": ["hypernetwork_dir", pj(models_path, "hypernetworks")],
        "LoCon": ["lyco_dir", pj(models_path, "LyCORIS")],
        "VAE": ["vae_dir", pj(models_path, "VAE")],
    }
    directory = model_type_dir.get(model_type)
    if directory is None:
        return None
    return getattr(co, directory[0], None) or directory[1]


def parse_model_url(url):
    address = urlsplit((url or "").strip())
    if address.scheme != "https" or address.netloc.lower() not in ("civitai.com", "www.civitai.com"):
        raise DownloaderError("请填写 https://civitai.com/models/模型编号 形式的模型链接。")
    model_match = re.fullmatch(r"/models/(\d+)(?:/[^/]*)?/?", address.path)
    version_match = re.fullmatch(r"/api/(?:v1/model-versions|download/models)/(\d+)/?", address.path)
    if version_match:
        return None, version_match.group(1)
    if not model_match:
        raise DownloaderError("无法识别模型链接，请复制 Civitai 模型详情页的地址。")
    version_id = parse_qs(address.query).get("modelVersionId", [None])[0]
    if version_id is not None and not version_id.isdigit():
        raise DownloaderError("链接中的 modelVersionId 应为数字。")
    return model_match.group(1), version_id


def validate_download_url(url):
    address = urlsplit(url or "")
    if (address.scheme != "https" or address.netloc.lower() != "civitai.com"
            or not re.fullmatch(r"/api/download/models/\d+/?", address.path)):
        raise DownloaderError("下载地址无效，请重新预览模型以获取 Civitai 官方下载地址。")


def request_civitai_detail(url, api_key=""):
    try:
        model_id, version_id = parse_model_url(url)
        if model_id:
            model = api_get(f"models/{model_id}", api_key)
            versions = model.get("modelVersions") or []
            if not versions:
                raise DownloaderError("该模型没有可访问的版本，可能已归档或下架。")
            version = next((item for item in versions if str(item.get("id")) == version_id), None)
            if version_id and version is None:
                version = api_get(f"model-versions/{version_id}", api_key)
                if str(version.get("modelId")) != model_id:
                    raise DownloaderError("链接中的模型与版本不匹配，请重新复制模型页面地址。")
            version = version or versions[0]
        else:
            version = api_get(f"model-versions/{version_id}", api_key)
            model = version.get("model") or {}
        files = [item for item in (version.get("files") or [])
                 if item.get("type") in ("Model", "Pruned Model", "VAE") and item.get("downloadUrl")]
        if not files:
            raise DownloaderError("该版本没有可下载的模型文件，请确认账号权限或选择其他版本。")
        file = next((item for item in files if item.get("primary")), files[0])
        validate_download_url(file["downloadUrl"])
        images = version.get("images") or []
        return True, {
            "name": model.get("name") or "未命名模型",
            "type": model.get("type") or "",
            "creator": model.get("creator") or {},
            "tags": model.get("tags") or [],
            "description": model.get("description") or version.get("description") or "",
            "version": dict(version, file=file, image=images[0] if images else {}),
        }
    except (DownloaderError, ValueError) as error:
        return False, str(error)
    except requests.RequestException as error:
        return False, network_message(error)


def resp_to_components(resp):
    if resp is None:
        return [None] * 10
    version = resp["version"]
    image = process_image(version["image"]["url"]) if version["image"].get("url") else None
    return [
        resp["name"], resp["type"], ", ".join(version.get("trainedWords") or []),
        resp["creator"].get("username", ""), ", ".join(resp["tags"]),
        version.get("updatedAt") or version.get("publishedAt") or "",
        resp["description"], image, version["file"]["name"], version["file"]["downloadUrl"],
    ]


def preview(url, api_key=""):
    ok, resp = request_civitai_detail(url, api_key)
    if not ok:
        return [resp] + resp_to_components(None) + ["", gr.update(interactive=False)]
    message = f"预览成功，点击下载按钮\n{resp['version']['file']['name']}"
    try:
        components = resp_to_components(resp)
    except (requests.RequestException, DownloaderError, OSError, ValueError):
        resp["version"]["image"] = {}
        components = resp_to_components(resp)
        message += "\n预览图暂时无法加载，模型仍可下载。"
    supported = get_model_path(resp["type"]) is not None
    if not supported:
        message += f"\n暂不支持自动保存此模型类型 {resp['type']}。"
    sha256 = (resp["version"]["file"].get("hashes") or {}).get("SHA256", "")
    return [message] + components + [sha256, gr.update(interactive=supported)]


def find_aria2c():
    executable = "aria2c.exe" if os.name == "nt" else "aria2c"
    bundled = os.path.join(os.path.dirname(os.path.dirname(__file__)), "bin", executable)
    return bundled if os.path.isfile(bundled) else shutil.which(executable)


def download_with_aria2(executable, url, options, partial_file, progress):
    # Resolve account authorization with requests, which strips it on cross-host redirects.
    # aria2 receives only the resulting signed file URL in a temporary task file.
    with requests.get(url, stream=True, **options) as response:
        check_response(response)
        content_type = response.headers.get("Content-Type", "").lower()
        if "text/" in content_type or "json" in content_type:
            raise DownloaderError("服务器返回了网页或错误信息，请检查 API Key、账号权限和代理。")
        resolved_url = response.url
    address = urlsplit(resolved_url)
    if address.hostname in ("civitai.com", "www.civitai.com"):
        return False
    if address.scheme != "https" or not address.hostname or re.search(r"[\r\n\t]", resolved_url):
        raise DownloaderError("Civitai 返回的文件地址格式异常，请重新预览后重试。")
    proxy = requests.utils.select_proxy(resolved_url, requests.utils.get_environ_proxies(resolved_url))
    task = f"{resolved_url}\n  dir={os.path.dirname(os.path.abspath(partial_file))}\n  out={os.path.basename(partial_file)}\n"
    if proxy:
        if re.search(r"[\r\n]", proxy):
            raise DownloaderError("本机代理配置格式异常。")
        task += f"  all-proxy={proxy}\n"
    command = [
        executable, "--no-conf=true", "--continue=true",
        "--max-connection-per-server=16", "--split=16", "--min-split-size=1M",
        "--file-allocation=none", "--auto-file-renaming=false", "--allow-overwrite=true",
        "--connect-timeout=10", "--timeout=30", "--max-tries=3", "--retry-wait=2",
        "--summary-interval=1", "--human-readable=false", "--enable-color=false",
        "--truncate-console-readout=false", "--console-log-level=warn", "--download-result=hide",
        "--auto-save-interval=1", "--no-netrc=true", f"--stop-with-process={os.getpid()}",
    ]
    # Match requests' proxy selection, including system proxy and no_proxy rules.
    environment = {key: value for key, value in os.environ.items()
                   if key.lower() not in ("http_proxy", "https_proxy", "all_proxy", "no_proxy")}
    progress(0, desc="aria2 正在连接，最多 16 连接")
    # aria2 1.37.0 on Windows has a control-file bug when its input is stdin.
    with tempfile.TemporaryDirectory(prefix="model-downloader-aria2-") as task_directory:
        task_file = os.path.join(task_directory, "download.txt")
        with open(task_file, "w", encoding="utf-8") as output:
            output.write(task)
        command.append(f"--input-file={task_file}")
        with subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1, env=environment,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        ) as process:
            try:
                for line in process.stdout:
                    status = re.search(r"(\d+)(?:B)?/(\d+)(?:B)?\((\d+)%\)", line)
                    if status:
                        received, total, percent = map(int, status.groups())
                        connections = re.search(r"CN:(\d+)", line)
                        speed = re.search(r"DL:([^\s\]]+)", line)
                        description = f"aria2 已下载 {received / 1048576:.1f}/{total / 1048576:.1f} MB"
                        if connections:
                            description += f"，{connections.group(1)} 连接"
                        if speed:
                            description += f"，{speed.group(1)}/s"
                        progress(min(percent / 100, 1), desc=description)
                return_code = process.wait()
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait()
    if return_code:
        raise DownloaderError(f"aria2 下载失败（错误码 {return_code}），请检查网络后重试，已保存的断点可继续使用。")
    return True


def download_with_requests(url, options, partial_file, progress):
    received = 0
    with requests.get(url, stream=True, **options) as response:
        check_response(response)
        content_type = response.headers.get("Content-Type", "").lower()
        if "text/" in content_type or "json" in content_type:
            raise DownloaderError("服务器返回了网页或错误信息，请检查 API Key、账号权限和代理。")
        total = int(response.headers.get("Content-Length") or 0)
        progress(0, desc="正在下载模型")
        with open(partial_file, "wb") as output:
            for chunk in response.iter_content(chunk_size=65536):
                if chunk:
                    output.write(chunk)
                    received += len(chunk)
                    fraction = min(received / total, 1) if total else 0
                    progress(fraction, desc=f"已下载 {received / 1048576:.1f} MB")
        if not received or (total and not response.headers.get("Content-Encoding") and received != total):
            raise DownloaderError("模型文件传输不完整，请重试下载。")


def download(model_type, filename, url, image_arr, api_key="", sha256="", progress=gr.Progress()):
    if not (model_type and filename and url):
        return "下载信息缺失，请先预览模型。"
    if (filename in (".", "..") or re.search(r'[<>:"/\\|?*\x00-\x1f]', filename)
            or filename.endswith((".", " "))):
        return "模型文件名无效，请重新预览模型。"
    try:
        validate_download_url(url)
        options = request_options(api_key)
    except (DownloaderError, ValueError) as error:
        return str(error)
    target_path = get_model_path(model_type)
    if not target_path:
        return f"暂不支持这种类型 {model_type}。"
    if not DOWNLOAD_LOCK.acquire(blocking=False):
        return "已有模型正在下载，请等待完成后重试。"
    try:
        os.makedirs(target_path, exist_ok=True)
        target_file = os.path.join(target_path, filename)
        if os.path.exists(target_file):
            return f"已经存在了，请检查该文件后再决定是否重新下载。\n{target_file}"
        partial_file = target_file + ".part"
        executable = find_aria2c()
        used_aria2 = executable and download_with_aria2(executable, url, options, partial_file, progress)
        if not used_aria2:
            if os.path.exists(partial_file + ".aria2"):
                raise DownloaderError("发现 aria2 下载断点，请恢复 aria2 下载组件后重试。")
            download_with_requests(url, options, partial_file, progress)
        if not os.path.isfile(partial_file) or not os.path.getsize(partial_file):
            raise DownloaderError("下载文件为空，请重试。")
        if sha256:
            progress(0.99, desc="正在校验 SHA256")
            digest = hashlib.sha256()
            with open(partial_file, "rb") as downloaded:
                for chunk in iter(lambda: downloaded.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest().lower() != sha256.lower():
                os.remove(partial_file)
                if os.path.exists(partial_file + ".aria2"):
                    os.remove(partial_file + ".aria2")
                raise DownloaderError("SHA256 校验失败，请重试下载。")
        # The lock serializes callbacks. Only complete files get the final model name.
        os.rename(partial_file, target_file)
        message = f"下载成功，保存到\n{target_file}"
        if used_aria2:
            message += "\n已使用 aria2 多连接下载。"
        if sha256:
            message += "\nSHA256 校验通过。"
        if isinstance(image_arr, np.ndarray) and image_arr.size:
            image_file = os.path.splitext(target_file)[0] + ".jpeg"
            try:
                if not os.path.exists(image_file):
                    Image.fromarray(image_arr).convert("RGB").save(image_file)
            except (OSError, ValueError):
                message += "\n预览图保存失败，模型文件已保存。"
        progress(1, desc="下载完成")
        return message
    except DownloaderError as error:
        return f"下载失败，{error}"
    except requests.RequestException as error:
        return f"下载失败，{network_message(error)}"
    except (OSError, ValueError):
        return "下载失败，请检查磁盘空间、目录权限和模型文件名，然后重试。"
    finally:
        DOWNLOAD_LOCK.release()


def request_online_docs():
    return "粘贴 Civitai 模型页面链接，点击预览后即可下载。", ""


def on_ui_tabs():
    banner, footer = request_online_docs()
    with gr.Blocks() as ui_component:
        gr.Markdown(banner)
        with gr.Accordion("Civitai 账号", open=False):
            gr.Markdown("登录后在 [Civitai 账号设置](https://civitai.com/user/account) 的 API Keys 中创建密钥并填入下方。密钥仅用于当前页面会话。")
            api_key = gr.Textbox(label="Civitai API Key", type="password", placeholder="粘贴账号设置中生成的 API Key")
            api_key.do_not_save_to_config = True
        with gr.Row() as input_component:
            with gr.Column():
                inp_url = gr.Textbox(
                    label="Civitai 模型的页面地址，不是下载链接",
                    placeholder="类似 https://civitai.com/models/28687/pen-sketch-style"
                )
                with gr.Row():
                    preview_btn = gr.Button("预览")
                    download_btn = gr.Button("下载", interactive=False)
                with gr.Row():
                    result = gr.Textbox(label="执行结果", interactive=False)
            with gr.Column() as preview_component:
                with gr.Row():
                    with gr.Column() as model_info_component:
                        name = gr.Textbox(label="名称", interactive=False)
                        model_type = gr.Textbox(label="类型", interactive=False)
                        trained_words = gr.Textbox(label="触发词", interactive=False)
                        creator = gr.Textbox(label="作者", interactive=False)
                        tags = gr.Textbox(label="标签", interactive=False)
                        updated_at = gr.Textbox(label="最近更新时间", interactive=False)
                    with gr.Column() as model_image_component:
                        image = gr.Image(show_label=False, interactive=False)
                with gr.Accordion("介绍", open=False):
                    description = gr.HTML()
        with gr.Row(visible=False):
            filename = gr.Textbox(visible=False, label="model_filename", interactive=False)
            download_url = gr.Textbox(visible=False, label="model_download_url", interactive=False)
        sha256 = gr.State("")
        with gr.Row():
            gr.Markdown(f"版本 {VERSION}\n\n作者 @tzwm\n{footer}")

        def preview_components():
            return [name, model_type, trained_words, creator, tags, updated_at, description, image]

        def file_info_components():
            return [filename, download_url]

        preview_btn.click(
            fn=preview,
            inputs=[inp_url, api_key],
            outputs=[result] + preview_components() + file_info_components() + [sha256, download_btn]
        )
        download_btn.click(
            fn=download,
            inputs=[model_type] + file_info_components() + [image, api_key, sha256],
            outputs=[result]
        )
    return [(ui_component, "模型下载", "model_downloader_cn_tab")]


script_callbacks.on_ui_tabs(on_ui_tabs)
