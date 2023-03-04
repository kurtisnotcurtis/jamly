"""Report the dependencies used by the installed Jamly interpreter."""
from __future__ import annotations

from dataclasses import dataclass, replace
import importlib.metadata
import importlib.util
import os
from pathlib import Path
import shutil
import sys

from . import __version__
from .core import ENGINE_HTDEMUCS, detect_engine, run


@dataclass(frozen=True)
class ToolStatus:
    name: str
    path: str | None = None
    ok: bool = False
    note: str = ""

    @property
    def missing(self) -> bool:
        return not self.ok

    def report(self) -> str:
        return "%-12s [%s] %s%s" % (
            self.name, "OK" if self.ok else "MISSING", self.path or "-",
            " (%s)" % self.note if self.note else "",
        )


def detect_system_python() -> ToolStatus:
    return ToolStatus("python", sys.executable, True, sys.version.split()[0])


def find_ytdlp() -> ToolStatus:
    available = importlib.util.find_spec("yt_dlp") is not None
    version = importlib.metadata.version("yt-dlp") if available else ""
    return ToolStatus("yt-dlp", sys.executable if available else None, available,
                      "%s; python -m yt_dlp" % version if available else "")


def ytdlp_command() -> list[str]:
    if not find_ytdlp().ok:
        raise RuntimeError("yt-dlp is missing; reinstall Jamly in this Python environment")
    return [sys.executable, "-m", "yt_dlp"]


def ytdlp_options() -> list[str]:
    return ["--ignore-config", "--no-playlist", "--no-write-subs",
            "--socket-timeout", "30", "--retries", "3"]


def detect_ffmpeg(ffmpeg_dirs=None) -> ToolStatus:
    directories = list(ffmpeg_dirs or [])
    on_path = shutil.which("ffmpeg")
    if on_path:
        directories.append(str(Path(on_path).absolute().parent))
    suffix = ".exe" if os.name == "nt" else ""
    for directory in directories:
        if shutil.which(str(Path(directory) / ("ffmpeg" + suffix))):
            return ToolStatus("ffmpeg", str(Path(directory).absolute()), True, "ffmpeg")
    return ToolStatus("ffmpeg", note="install ffmpeg on PATH")


@dataclass(frozen=True)
class RunReport:
    python: ToolStatus
    ytdlp: ToolStatus
    ffmpeg: ToolStatus
    engine: ToolStatus

    @property
    def ready(self) -> bool:
        return all(item.ok for item in self.entries)

    @property
    def entries(self) -> tuple[ToolStatus, ...]:
        return self.python, self.ytdlp, self.ffmpeg, self.engine


def summarize() -> RunReport:
    engine = detect_engine()
    return RunReport(
        detect_system_python(), find_ytdlp(), detect_ffmpeg(),
        ToolStatus("demucs", engine, engine == ENGINE_HTDEMUCS,
                   "model weights download on first separation"),
    )
