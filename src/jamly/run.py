"""CLI entry point for downloading and splitting drum tracks."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import shutil
import sys
import tempfile

from .core import (
    ENGINE_MDX, build_stem_encode_cmd, build_ytdlp_extract_cmd,
    run as _run, sanitize_filename, separate_stems,
)


@dataclass
class ItemResult:
    watch_id: str
    title: str | None = None
    status: str = "pending"
    drums_path: str | None = None
    nodrums_path: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status in ("done", "skipped")


def process_one(url: str, args) -> ItemResult:
    wid = sanitize_filename(url)
    result = ItemResult(wid)
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        result.status, result.error = "error", "Install ffmpeg on PATH"
        return result
    try:
        out_dir = Path(args.output_dir).expanduser().resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="jamly-") as work:
            workdir = Path(work)
            dl_cmd = build_ytdlp_extract_cmd(
                [sys.executable, "-m", "yt_dlp"], url, str(workdir / "input.%(ext)s")
            )
            _run(dl_cmd, check=True)
            wavs = list(workdir.glob("input.*"))
            if not wavs:
                raise RuntimeError("yt-dlp did not produce an audio file")
            stems = separate_stems(str(wavs[0]), str(workdir), threads=args.threads, model=args.model)
            drums_out = out_dir / f"{wid}_drums.{args.audio_format}"
            nodrums_out = out_dir / f"{wid}_no_drums.{args.audio_format}"
            for key, dest in (("drums", drums_out), ("no_drums", nodrums_out)):
                if args.audio_format == "wav":
                    shutil.copy2(stems[key], dest)
                else:
                    cmd = build_stem_encode_cmd(ffmpeg, stems[key], str(dest), args.audio_format)
                    _run(cmd, check=True)
            result.status = "done"
            result.drums_path = str(drums_out)
            result.nodrums_path = str(nodrums_out)
    except Exception as exc:
        result.status, result.error = "error", str(exc)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jamly", description="Split drums and drumless audio")
    parser.add_argument("-i", "--input", action="append", required=True, metavar="URL")
    parser.add_argument("-o", "--output-dir", default="./output")
    parser.add_argument("--audio-format", choices=("mp3", "wav"), default="mp3")
    parser.add_argument("--model", default=ENGINE_MDX)
    parser.add_argument("-t", "--threads", type=int, default=0)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    failures = 0
    for url in args.input:
        res = process_one(url, args)
        if res.ok:
            print("OK:", res.drums_path, res.nodrums_path)
        else:
            failures += 1
            print("ERROR:", res.error, file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
