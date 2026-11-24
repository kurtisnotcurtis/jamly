#!/usr/bin/env python3
"""Download, verify by SHA-256, and install the pinned Linux CPU-only PyTorch wheel."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import unquote, urlsplit
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
PIN_FILE = Path(__file__).with_name("linux-cpu-torch.json")


def load_pin(path: Path = PIN_FILE) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download_verified_wheel(pin: dict, destination: Path) -> Path:
    url = pin["url"]
    expected = pin["sha256"].lower()
    if urlsplit(url).scheme != "https":
        raise RuntimeError("Refusing non-HTTPS PyTorch wheel URL: %s" % url)
    filename = Path(unquote(urlsplit(url).path)).name
    if not filename.endswith(".whl"):
        raise RuntimeError("Invalid PyTorch wheel filename: %s" % filename)
    destination.mkdir(parents=True, exist_ok=True)
    wheel = destination / filename
    partial = wheel.with_suffix(".whl.partial")
    try:
        with urlopen(url, timeout=300) as response, partial.open("wb") as file:
            if urlsplit(response.url).scheme != "https":
                raise RuntimeError("Refusing an insecure PyTorch wheel download")
            shutil.copyfileobj(response, file)
        actual = sha256_file(partial)
        if actual != expected:
            raise RuntimeError(
                "PyTorch CPU wheel SHA-256 mismatch: expected %s, got %s" % (expected, actual)
            )
        partial.replace(wheel)
    finally:
        partial.unlink(missing_ok=True)
    return wheel


def main() -> int:
    pin = load_pin()
    if sys.platform != "linux" or platform.machine().lower() != "x86_64":
        raise RuntimeError("Linux CPU PyTorch pin is only for linux x86_64")
    py_tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
    if py_tag != pin["python_tag"]:
        raise RuntimeError(
            "Python tag %s does not match pinned Linux CPU torch tag %s" % (py_tag, pin["python_tag"])
        )
    installed_torch = importlib.metadata.version("torch").split("+", 1)[0]
    if installed_torch != pin["version"]:
        raise RuntimeError(
            "Installed torch %s does not match tools/linux-cpu-torch.json pin %s; update the pin and SHA-256"
            % (installed_torch, pin["version"])
        )
    nvidia_pkgs = sorted(
        dist.metadata["Name"]
        for dist in importlib.metadata.distributions()
        if dist.metadata["Name"].startswith("nvidia-")
    )
    if nvidia_pkgs:
        subprocess.run(["uv", "pip", "uninstall", *nvidia_pkgs], check=True)
    with tempfile.TemporaryDirectory(prefix="jamly-cpu-torch-") as tmp:
        wheel = download_verified_wheel(pin, Path(tmp))
        subprocess.run(
            ["uv", "pip", "install", "--no-deps", "--force-reinstall", str(wheel)],
            check=True,
        )
    actual_version = importlib.metadata.version("torch")
    if actual_version != pin["wheel_version"]:
        raise RuntimeError(
            "Expected installed torch %s, got %s" % (pin["wheel_version"], actual_version)
        )
    print("Installed verified Linux CPU torch %s" % actual_version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
