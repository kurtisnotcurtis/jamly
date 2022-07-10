"""Subprocess and filename helpers for yt-dlp, Demucs v3, and ffmpeg."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Sequence

ENGINE_MDX = "mdx_extra_q"
FORMAT_WAV = "wav"
FORMAT_MP3 = "mp3"


@dataclass
class RunResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def run(args: Sequence[str], check: bool = False) -> RunResult:
    cmd = [str(arg) for arg in args]
    try:
        proc = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except FileNotFoundError:
        return RunResult(cmd, -1, "", "NOT FOUND: %s" % cmd[0])
    result = RunResult(cmd, proc.returncode, proc.stdout or "", proc.stderr or "")
    if check and not result.ok:
        raise RuntimeError("command failed (exit %d): %s" % (
            result.returncode, result.stderr.strip() or cmd[0]))
    return result


def build_ytdlp_extract_cmd(
    ytdlp_exe: str | Sequence[str],
    url: str,
    output_template: str = "%(title)s.%(ext)s",
    ffmpeg_dir: str = "",
) -> list[str]:
    prefix = [ytdlp_exe] if isinstance(ytdlp_exe, str) else list(ytdlp_exe)
    cmd = prefix + [
        "--ignore-config", "--no-playlist",
        "-f", "bestaudio/best", "--extract-audio", "--audio-format", "wav",
        "--audio-quality", "0", "--newline",
    ]
    if ffmpeg_dir:
        cmd += ["--ffmpeg-location", ffmpeg_dir]
    return cmd + ["-o", output_template, url]


def build_demucs_cmd(
    python_exe: str,
    input_audio: str,
    workdir: str,
    model_name: str = ENGINE_MDX,
    threads: int = 0,
) -> list[str]:
    cmd = [
        python_exe, "-m", "demucs.separate",
        "-n", model_name,
        "--two-stems", "drums",
        "-o", workdir,
    ]
    if threads:
        cmd += ["-j", str(threads)]
    return cmd + [input_audio]


def build_stem_encode_cmd(
    ffmpeg_exe: str,
    input_wav: str,
    output_path: str,
    fmt: str = FORMAT_MP3,
) -> list[str] | None:
    if fmt == FORMAT_WAV:
        return None
    if fmt != FORMAT_MP3:
        raise ValueError("Unsupported audio format: %s" % fmt)
    return [
        ffmpeg_exe, "-y", "-i", os.path.abspath(input_wav),
        "-c:a", "libmp3lame", "-b:a", "320k", output_path,
    ]


def sanitize_filename(name: str) -> str:
    name = str(name).replace("/", "-").replace("\\", "-")
    cleaned = " ".join("".join(
        char for char in name if ord(char) >= 32 and char not in ':*?"<>|'
    ).split()).strip(" .")
    return cleaned[:180].rstrip(" .") or "untitled"


def separate_stems(audio: str, workdir: str, threads: int = 0,
                   model: str = ENGINE_MDX) -> dict[str, str]:
    cmd = build_demucs_cmd(sys.executable, audio, workdir, model_name=model, threads=threads)
    result = run(cmd)
    if not result.ok:
        raise RuntimeError("Demucs failed: %s" % result.stderr.strip())
    track_dir = Path(workdir) / model / Path(audio).stem
    paths = {
        "drums": str(track_dir / "drums.wav"),
        "no_drums": str(track_dir / "no_drums.wav"),
    }
    if not all(Path(p).is_file() for p in paths.values()):
        raise RuntimeError("Demucs did not produce drums.wav and no_drums.wav")
    return paths
