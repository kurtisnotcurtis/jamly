"""Paths and subprocess entry points shared by source and frozen installations."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import site
import subprocess
import sys


def bundle_root() -> Path | None:
    return Path(sys._MEIPASS) if getattr(sys, "frozen", False) else None


def module_command(module: str, *args: str, executable: str | None = None) -> list[str]:
    if bundle_root() is not None:
        executable = executable or str(
            Path(sys.executable).resolve().with_name("jamly-runtime.exe" if os.name == "nt" else "jamly-runtime")
        )
        return [executable, "--module", module, *args]
    return [executable or sys.executable, "-m", module, *args]


def bundled_binary(name: str) -> Path | None:
    root = bundle_root()
    return root / "bin" / (name + (".exe" if os.name == "nt" else "")) if root else None


def browser_assets(kind: str) -> Path:
    root = bundle_root()
    if root is not None:
        source = root / "share" / "jamly" / "browser" / kind
        if source.is_dir():
            return source
        raise FileNotFoundError("Browser assets are missing; reinstall Jamly")
    here = Path(__file__).resolve()
    candidates = [here.parents[2] / "browser" / kind]
    prefixes = [Path(sys.prefix)]
    user_base = getattr(site, "USER_BASE", None)
    if user_base:
        prefixes.append(Path(user_base))
    # Also check ancestor prefixes of this installed module (covers --user and custom --target prefixes).
    prefixes.extend(here.parents)
    seen = set()
    for prefix in prefixes:
        candidate = prefix / "share" / "jamly" / "browser" / kind
        if candidate not in seen:
            seen.add(candidate)
            candidates.append(candidate)
    for source in candidates:
        if source.is_dir():
            return source
    raise FileNotFoundError("Browser assets are missing; reinstall Jamly")


def open_directory(path: Path) -> None:
    if not path.is_dir():
        raise ValueError("The export directory no longer exists")
    if os.name == "nt":
        os.startfile(str(path))
        return
    opener = "/usr/bin/open" if sys.platform == "darwin" else shutil.which("xdg-open")
    if not opener:
        raise RuntimeError("No desktop folder opener is installed; use the displayed output path")
    subprocess.run([opener, str(path)], check=True, timeout=10,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
