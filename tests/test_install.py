from contextlib import redirect_stdout
from io import BytesIO, StringIO
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("extension_installer", Path(__file__).resolve().parents[1] / "install.py")
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


class InstallerTests(unittest.TestCase):
    def test_ready_json_and_idempotent_install(self):
        ready = {"status": "ready", "engine": "aria2", "version": "1.37.0", "path": "bin/aria2c.exe"}
        output = StringIO()
        with patch.object(installer, "existing_engine", return_value=ready), patch.object(installer, "download_archive") as download:
            with redirect_stdout(output):
                code = installer.main(["--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "ready")
        download.assert_not_called()

    def test_missing_check_is_non_mutating(self):
        output = StringIO()
        with patch.object(installer, "existing_engine", return_value=None), patch.object(installer, "download_archive") as download:
            with redirect_stdout(output):
                code = installer.main(["--check", "--json"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output.getvalue())["status"], "missing")
        download.assert_not_called()

    def test_error_result_is_json(self):
        output = StringIO()
        with patch.object(installer, "install_engine", side_effect=RuntimeError("Check the proxy and retry")):
            with redirect_stdout(output):
                code = installer.main(["--json"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output.getvalue())["status"], "error")

    def test_archive_checksum_is_checked(self):
        content = b"test archive bytes"
        with patch.object(installer, "urlopen", return_value=BytesIO(content)):
            with patch.object(installer, "ARCHIVE_SHA256", hashlib.sha256(content).hexdigest()):
                self.assertEqual(installer.download_archive(), content)

    def test_invalid_archive_is_retried_and_rejected(self):
        with patch.object(installer, "urlopen", side_effect=lambda *args, **kwargs: BytesIO(b"invalid")) as open_url:
            with self.assertRaises(RuntimeError):
                installer.download_archive()
        self.assertEqual(open_url.call_count, 3)

    def test_empty_directory_has_no_bundled_engine(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(installer, "ROOT", Path(folder)), patch.object(installer.shutil, "which", return_value=None):
                self.assertIsNone(installer.existing_engine())


if __name__ == "__main__":
    unittest.main()
