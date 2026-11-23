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


def share_path(*parts: str, checkout_relative: tuple[str, ...] | None = None) -> Path | None:
    """Locate a packaged file or directory under share/jamly/ across frozen, checkout, and installed layouts."""
    root = bundle_root()
    if root is not None:
        candidate = root.joinpath("share", "jamly", *parts)
        return candidate if candidate.exists() else None
    here = Path(__file__).resolve()
    candidates: list[Path] = []
    if checkout_relative is not None and len(here.parents) > 2:
        candidates.append(here.parents[2].joinpath(*checkout_relative))
    prefixes = [Path(sys.prefix)]
    user_base = getattr(site, "USER_BASE", None)
    if user_base:
        prefixes.append(Path(user_base))
    prefixes.extend(here.parents)
    seen = set()
    for prefix in prefixes:
        candidate = prefix.joinpath("share", "jamly", *parts)
        if candidate not in seen:
            seen.add(candidate)
            candidates.append(candidate)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def browser_assets(kind: str) -> Path:
    source = share_path("browser", kind, checkout_relative=("browser", kind))
    if source is None or not source.is_dir():
        raise FileNotFoundError("Browser assets are missing; reinstall Jamly")
    return source


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
