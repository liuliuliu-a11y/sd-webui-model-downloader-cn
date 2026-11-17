import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import os
from pathlib import Path
import random
import re
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlsplit

import requests


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "model-downloader-cn.py"
modules = types.ModuleType("modules")
modules.scripts = types.ModuleType("modules.scripts")
modules.script_callbacks = types.SimpleNamespace(on_ui_tabs=lambda callback: None)
modules.shared = types.SimpleNamespace(cmd_opts=types.SimpleNamespace())
paths = types.ModuleType("modules.paths_internal")
paths.models_path = "models"
paths.data_path = "."
with patch.dict(sys.modules, {
    "modules": modules,
    "modules.scripts": modules.scripts,
    "modules.paths_internal": paths,
}):
    spec = importlib.util.spec_from_file_location("model_downloader", SCRIPT)
    downloader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(downloader)

ARIA2_EXECUTABLE = downloader.find_aria2c()


def response(data=None, content=b"weights", content_type="application/octet-stream", status=200):
    result = Mock()
    result.status_code = status
    result.ok = status < 400
    result.headers = {"Content-Type": content_type, "Content-Length": str(len(content))}
    result.json.return_value = data
    result.iter_content.return_value = [content]
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=False)
    return result


class DownloaderTests(unittest.TestCase):
    def setUp(self):
        self.aria2_lookup = patch.object(downloader, "find_aria2c", return_value=None)
        self.aria2_mock = self.aria2_lookup.start()
        self.addCleanup(self.aria2_lookup.stop)
        self.version = {
            "id": 20,
            "name": "v2",
            "modelId": 10,
            "model": {"name": "Example", "type": "LORA"},
            "files": [{
                "name": "example.safetensors", "primary": True, "type": "Model",
                "downloadUrl": "https://civitai.com/api/download/models/20?fileId=30",
                "hashes": {"SHA256": hashlib.sha256(b"weights").hexdigest()},
            }],
            "images": [],
        }
        self.model = {"id": 10, "name": "Example", "type": "LORA", "modelVersions": [self.version]}

    @patch.object(downloader.requests, "get")
    def test_official_api_and_authorization_header(self, get):
        get.return_value = response(self.model)
        ok, detail = downloader.request_civitai_detail("https://civitai.com/models/10/example", "secret")
        self.assertTrue(ok, detail)
        self.assertEqual(get.call_args.args[0], "https://civitai.com/api/v1/models/10")
        self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], "Bearer secret")
        self.assertIn("timeout", get.call_args.kwargs)
        self.assertNotIn("secret", detail["version"]["file"]["downloadUrl"])

    @patch.object(downloader.requests, "get")
    def test_selected_version(self, get):
        get.side_effect = [response(self.model), response(dict(self.version, id=21))]
        ok, detail = downloader.request_civitai_detail("https://civitai.com/models/10/example?modelVersionId=21")
        self.assertTrue(ok, detail)
        self.assertEqual(get.call_args.args[0], "https://civitai.com/api/v1/model-versions/21")
        self.assertEqual(detail["version"]["id"], 21)

    @patch.object(downloader.requests, "get")
    def test_download_link_as_input(self, get):
        get.return_value = response(self.version)
        ok, detail = downloader.request_civitai_detail("https://civitai.com/api/download/models/20")
        self.assertTrue(ok, detail)
        self.assertEqual(get.call_args.args[0], "https://civitai.com/api/v1/model-versions/20")

    @patch.object(downloader.requests, "get")
    def test_reject_foreign_host_before_sending_token(self, get):
        ok, _ = downloader.request_civitai_detail("https://civitai.com.evil.test/models/10", "secret")
        self.assertFalse(ok)
        get.assert_not_called()

    @patch.object(downloader.requests, "get")
    def test_network_error_is_actionable(self, get):
        get.side_effect = requests.Timeout()
        ok, message = downloader.request_civitai_detail("https://civitai.com/models/10")
        self.assertFalse(ok)
        self.assertIn("超时", message)

    @patch.object(downloader.requests, "get")
    def test_rejected_key(self, get):
        get.return_value = response(status=401)
        ok, message = downloader.request_civitai_detail("https://civitai.com/models/10", "secret")
        self.assertFalse(ok)
        self.assertIn("API Key", message)
        self.assertNotIn("secret", message)

    @patch.object(downloader.requests, "get")
    def test_primary_model_file_selected(self, get):
        self.version["files"].insert(0, {"name": "training.zip", "type": "Training Data", "downloadUrl": "https://civitai.com/api/download/models/20"})
        get.return_value = response(self.model)
        ok, detail = downloader.request_civitai_detail("https://civitai.com/models/10")
        self.assertTrue(ok, detail)
        self.assertEqual(detail["version"]["file"]["name"], "example.safetensors")

    @patch.object(downloader.requests, "get")
    def test_archived_model_has_clear_error(self, get):
        get.return_value = response(dict(self.model, modelVersions=[]))
        ok, message = downloader.request_civitai_detail("https://civitai.com/models/10")
        self.assertFalse(ok)
        self.assertIn("版本", message)

    @patch.object(downloader.requests, "get")
    def test_foreign_download_host_rejected(self, get):
        message = downloader.download("LORA", "x.safetensors", "https://evil.test/file", None, "secret")
        self.assertIn("下载地址", message)
        get.assert_not_called()

    @patch.object(downloader.requests, "get")
    def test_path_traversal_rejected(self, get):
        message = downloader.download("LORA", "../x.safetensors", "https://civitai.com/api/download/models/20", None)
        self.assertIn("文件名", message)
        get.assert_not_called()

    @patch.object(downloader.requests, "get")
    def test_stream_download_and_duplicate(self, get):
        get.return_value = response()
        with tempfile.TemporaryDirectory() as folder, patch.object(downloader, "get_model_path", return_value=folder):
            args = ("LORA", "x.safetensors", "https://civitai.com/api/download/models/20", None, "secret")
            message = downloader.download(*args, sha256=hashlib.sha256(b"weights").hexdigest())
            self.assertIn("下载成功", message)
            self.assertEqual((Path(folder) / "x.safetensors").read_bytes(), b"weights")
            self.assertFalse((Path(folder) / "x.safetensors.part").exists())
            self.assertIn("已经存在", downloader.download(*args))
            self.assertEqual(get.call_count, 1)

    @patch.object(downloader.requests, "get")
    def test_html_response_never_becomes_model(self, get):
        get.return_value = response(content=b"<html>login</html>", content_type="text/html")
        with tempfile.TemporaryDirectory() as folder, patch.object(downloader, "get_model_path", return_value=folder):
            message = downloader.download("LORA", "x.safetensors", "https://civitai.com/api/download/models/20", None)
            self.assertIn("下载失败", message)
            self.assertFalse((Path(folder) / "x.safetensors").exists())

    @patch.object(downloader.requests, "get")
    def test_hash_failure_keeps_final_path_clear(self, get):
        get.return_value = response()
        with tempfile.TemporaryDirectory() as folder, patch.object(downloader, "get_model_path", return_value=folder):
            message = downloader.download("LORA", "x.safetensors", "https://civitai.com/api/download/models/20", None, sha256="0" * 64)
            self.assertIn("校验", message)
            self.assertFalse((Path(folder) / "x.safetensors").exists())

    @patch.object(downloader.requests, "get")
    def test_interrupted_download_can_retry(self, get):
        interrupted = response()
        interrupted.iter_content.side_effect = requests.ConnectionError()
        get.side_effect = [interrupted, response()]
        with tempfile.TemporaryDirectory() as folder, patch.object(downloader, "get_model_path", return_value=folder):
            args = ("LORA", "x.safetensors", "https://civitai.com/api/download/models/20", None)
            self.assertIn("下载失败", downloader.download(*args))
            self.assertFalse((Path(folder) / "x.safetensors").exists())
            self.assertIn("下载成功", downloader.download(*args))

    @patch.object(downloader.requests, "get")
    def test_mismatched_model_version_rejected(self, get):
        get.side_effect = [response(self.model), response(dict(self.version, id=21, modelId=99))]
        ok, message = downloader.request_civitai_detail("https://civitai.com/models/10?modelVersionId=21")
        self.assertFalse(ok)
        self.assertIn("不匹配", message)

    @patch.object(downloader.requests, "get")
    def test_short_transfer_never_becomes_final_model(self, get):
        short_response = response()
        short_response.headers["Content-Length"] = "999"
        get.return_value = short_response
        with tempfile.TemporaryDirectory() as folder, patch.object(downloader, "get_model_path", return_value=folder):
            message = downloader.download("LORA", "x.safetensors", "https://civitai.com/api/download/models/20", None)
            self.assertIn("传输不完整", message)
            self.assertFalse((Path(folder) / "x.safetensors").exists())

    @patch.object(downloader.requests, "get")
    def test_concurrent_download_is_rejected(self, get):
        with downloader.DOWNLOAD_LOCK:
            message = downloader.download("LORA", "x.safetensors", "https://civitai.com/api/download/models/20", None)
        self.assertIn("正在下载", message)
        get.assert_not_called()

    @patch.object(downloader.requests, "get")
    def test_preview_failure_clears_download_state(self, get):
        get.return_value = response(status=401)
        result = downloader.preview("https://civitai.com/models/10", "secret")
        self.assertEqual(len(result), 13)
        self.assertIsNone(result[10])
        self.assertEqual(result[11], "")
        self.assertFalse(result[12]["interactive"])

    def test_api_key_excluded_from_webui_saved_defaults(self):
        blocks, label, tab_id = downloader.on_ui_tabs()[0]
        key = next(component for component in blocks.blocks.values() if getattr(component, "label", None) == "Civitai API Key")
        self.assertEqual(label, "模型下载")
        self.assertEqual(tab_id, "model_downloader_cn_tab")
        self.assertEqual(key.type, "password")
        self.assertEqual(key.value, "")
        self.assertTrue(key.do_not_save_to_config)

    @patch.object(downloader.requests, "get")
    def test_aria2_receives_signed_url_without_account_key(self, get):
        resolved = response()
        resolved.url = "https://b2.civitai.com/file?signature=example"
        get.return_value = resolved
        process = Mock()
        process.stdout = iter(["[#123456 3B/7B(42%) CN:16 DL:1024B ETA:1s]\n"])
        process.wait.return_value = 0
        process.poll.return_value = 0
        process.__enter__ = Mock(return_value=process)
        process.__exit__ = Mock(return_value=False)
        progress = Mock()
        tasks = []
        def start_process(command, **kwargs):
            task_path = next(argument.split("=", 1)[1] for argument in command if argument.startswith("--input-file="))
            tasks.append(Path(task_path).read_text(encoding="utf-8"))
            return process
        with tempfile.TemporaryDirectory() as folder:
            partial_file = str(Path(folder) / "x.safetensors.part")
            with patch.object(downloader.subprocess, "Popen", side_effect=start_process) as popen:
                used = downloader.download_with_aria2("aria2c.exe", "https://civitai.com/api/download/models/20", downloader.request_options("secret"), partial_file, progress)
        self.assertTrue(used)
        command = popen.call_args.args[0]
        self.assertIn("--max-connection-per-server=16", command)
        self.assertIn("--split=16", command)
        self.assertIn("--continue=true", command)
        self.assertNotIn("secret", str(popen.call_args))
        self.assertNotIn("--input-file=-", command)
        task = tasks[0]
        self.assertIn(resolved.url, task)
        self.assertNotIn("secret", task)
        self.assertNotIn("Authorization", task)
        self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], "Bearer secret")
        self.assertGreater(progress.call_count, 0)

    @patch.object(downloader.requests, "get")
    def test_aria2_failure_is_not_published_as_model(self, get):
        resolved = response()
        resolved.url = "https://b2.civitai.com/file?signature=example"
        get.return_value = resolved
        process = Mock()
        process.stdout = iter(["error status=403 uri=https://example.test/?signature=secret\n"])
        process.wait.return_value = 22
        process.poll.return_value = 22
        process.__enter__ = Mock(return_value=process)
        process.__exit__ = Mock(return_value=False)
        with tempfile.TemporaryDirectory() as folder, patch.object(downloader, "get_model_path", return_value=folder):
            self.aria2_mock.return_value = "aria2c.exe"
            with patch.object(downloader.subprocess, "Popen", return_value=process):
                message = downloader.download("LORA", "x.safetensors", "https://civitai.com/api/download/models/20", None, "secret")
            self.assertIn("下载失败", message)
            self.assertNotIn("secret", message)
            self.assertFalse((Path(folder) / "x.safetensors").exists())

    @patch.object(downloader.requests, "get")
    def test_direct_authenticated_file_uses_python_transfer(self, get):
        direct = response()
        direct.url = "https://civitai.com/api/download/models/20"
        get.return_value = direct
        with patch.object(downloader.subprocess, "Popen") as popen:
            used = downloader.download_with_aria2("aria2c.exe", direct.url, downloader.request_options("secret"), "x.part", Mock())
        self.assertFalse(used)
        popen.assert_not_called()

    @unittest.skipUnless(ARIA2_EXECUTABLE, "aria2 executable is unavailable")
    def test_real_aria2_resumes_existing_partial_with_multiple_ranges(self):
        payload = random.Random(42).randbytes(32 * 1024 * 1024)
        ranges = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                start, end = 0, len(payload) - 1
                header = self.headers.get("Range")
                if header:
                    match = re.fullmatch(r"bytes=(\d+)-(\d*)", header)
                    start = int(match.group(1))
                    end = min(int(match.group(2)) if match.group(2) else end, end)
                    ranges.append((start, end))
                self.send_response(206 if header else 200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(end - start + 1))
                if header:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{len(payload)}")
                self.end_headers()
                try:
                    for offset in range(start, end + 1, 65536):
                        self.wfile.write(payload[offset:min(offset + 65536, end + 1)])
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        url = f"http://127.0.0.1:{server.server_port}/model"

        def fixture_urlsplit(value):
            address = urlsplit(value)
            # Only this isolated loopback fixture uses HTTP. Production requires HTTPS.
            return address._replace(scheme="https") if address.hostname == "127.0.0.1" else address

        try:
            with tempfile.TemporaryDirectory() as folder:
                partial_file = Path(folder) / "model.part"
                partial_file.write_bytes(payload[:4 * 1024 * 1024])
                with patch.object(downloader, "urlsplit", side_effect=fixture_urlsplit), patch.dict(os.environ, {"NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}):
                    used = downloader.download_with_aria2(ARIA2_EXECUTABLE, url, downloader.request_options(), str(partial_file), Mock())
                self.assertTrue(used)
                self.assertEqual(hashlib.sha256(partial_file.read_bytes()).digest(), hashlib.sha256(payload).digest())
                self.assertGreater(len(ranges), 1)
                self.assertGreaterEqual(min(start for start, _ in ranges), 4 * 1024 * 1024)
        finally:
            server.shutdown()
            server.server_close()
            worker.join()


if __name__ == "__main__":
    unittest.main()
