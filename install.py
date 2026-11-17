"""Install the optional aria2 engine for this WebUI extension."""

import argparse
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import Request, urlopen
from zipfile import BadZipFile, ZipFile


ROOT = Path(__file__).resolve().parent
ARIA2_VERSION = "1.37.0"
ARCHIVE_SHA256 = "67d015301eef0b612191212d564c5bb0a14b5b9c4796b76454276a4d28d9b288"
EXECUTABLE_SHA256 = "be2099c214f63a3cb4954b09a0becd6e2e34660b886d4c898d260febfe9d70c2"
RELEASE_URL = "https://github.com/aria2/aria2/releases/download/release-1.37.0/aria2-1.37.0-win-64bit-build1.zip"
ASSET_URL = "https://api.github.com/repos/aria2/aria2/releases/assets/135654444"
ARCHIVE_PREFIX = "aria2-1.37.0-win-64bit-build1/"


def executable_version(path):
    try:
        result = subprocess.run(
            [str(path), "--version"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if result.returncode == 0 and result.stdout.startswith("aria2 version "):
            return result.stdout.splitlines()[0].removeprefix("aria2 version ")
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def existing_engine():
    name = "aria2c.exe" if os.name == "nt" else "aria2c"
    bundled = ROOT / "bin" / name
    if bundled.is_file():
        # The Windows bundle comes from the pinned official release archive.
        digest_ok = os.name != "nt" or hashlib.sha256(bundled.read_bytes()).hexdigest() == EXECUTABLE_SHA256
        version = executable_version(bundled) if digest_ok else None
        if version:
            return {"status": "ready", "engine": "aria2", "version": version, "path": str(bundled), "source": "bundled"}
    system = shutil.which(name)
    version = executable_version(system) if system else None
    if version:
        return {"status": "ready", "engine": "aria2", "version": version, "path": system, "source": "system"}
    return None


def download_archive():
    sources = [ASSET_URL, RELEASE_URL, ASSET_URL]
    for attempt, url in enumerate(sources, 1):
        data = bytearray()
        started = time.monotonic()
        request = Request(url, headers={
            "Accept": "application/octet-stream", "Range": "bytes=0-",
            "User-Agent": "sd-webui-model-downloader-cn-installer",
        })
        try:
            print(f"Downloading official aria2 {ARIA2_VERSION}, attempt {attempt}/3", file=sys.stderr)
            with urlopen(request, timeout=20) as response:
                for chunk in iter(lambda: response.read(16384), b""):
                    data.extend(chunk)
                    if len(data) > 10 * 1024 * 1024 or time.monotonic() - started > 90:
                        raise TimeoutError("Archive transfer exceeded its size or time limit")
            if hashlib.sha256(data).hexdigest() != ARCHIVE_SHA256:
                raise ValueError("Official aria2 archive SHA256 mismatch")
            return bytes(data)
        except (URLError, OSError, ValueError) as error:
            print(f"aria2 package attempt failed ({type(error).__name__})", file=sys.stderr)
    raise RuntimeError("Unable to fetch the official aria2 package. Check the system proxy and rerun install.py --json.")


def install_engine():
    current = existing_engine()
    if current:
        return current
    if os.name != "nt" or sys.maxsize <= 2 ** 32:
        raise RuntimeError("Install aria2 with your operating system package manager, then rerun install.py --check --json.")
    archive_data = download_archive()
    destination = ROOT / "bin"
    destination.mkdir(exist_ok=True)
    with ZipFile(BytesIO(archive_data)) as archive:
        executable = archive.read(ARCHIVE_PREFIX + "aria2c.exe")
        license_text = archive.read(ARCHIVE_PREFIX + "COPYING")
    if hashlib.sha256(executable).hexdigest() != EXECUTABLE_SHA256:
        raise RuntimeError("Extracted aria2 executable SHA256 mismatch")
    with tempfile.TemporaryDirectory(prefix="aria2-install-", dir=destination) as staging:
        staged_executable = Path(staging) / "aria2c.exe"
        staged_license = Path(staging) / "aria2-COPYING.txt"
        staged_executable.write_bytes(executable)
        staged_license.write_bytes(license_text)
        if executable_version(staged_executable) != ARIA2_VERSION:
            raise RuntimeError("The downloaded aria2 executable could not start")
        os.replace(staged_license, destination / "aria2-COPYING.txt")
        os.replace(staged_executable, destination / "aria2c.exe")
    result = existing_engine()
    if not result:
        raise RuntimeError("aria2 installation verification failed")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Set up aria2 for the Civitai WebUI downloader without interactive prompts.",
        epilog="Examples\n  python install.py --json\n  python install.py --check --json",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--check", action="store_true", help="Inspect the engine without downloading or modifying files.")
    parser.add_argument("--json", action="store_true", help="Print one JSON result to stdout. Diagnostics go to stderr.")
    args = parser.parse_args(argv)
    try:
        result = existing_engine() if args.check else install_engine()
        if result is None:
            result = {"status": "missing", "engine": "aria2", "next_command": "python install.py --json"}
        exit_code = 0 if result["status"] == "ready" else 1
    except (RuntimeError, OSError, BadZipFile, KeyError) as error:
        result = {"status": "error", "engine": "aria2", "message": str(error)}
        exit_code = 1
    if args.json or not sys.stdout.isatty():
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"aria2 {result['status']}")
        print(result.get("path") or result.get("message") or result.get("next_command"))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
