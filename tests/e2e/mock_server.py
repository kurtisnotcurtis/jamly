"""Live ItemServer harness with a fast deterministic worker for Playwright E2E tests."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import sys
import threading
import uuid

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from jamly import server  # noqa: E402
from jamly.bootstrap import detect_ffmpeg  # noqa: E402
from jamly.core import build_stem_encode_cmd, run as run_cmd  # noqa: E402
from jamly.models import DEFAULT_MODEL, DEFAULT_STEM, expected_output_kinds  # noqa: E402
from jamly.normalize import local_audio_path  # noqa: E402
from jamly.outputs import PROCESSING_VERSION, checksum  # noqa: E402
from jamly.run import ItemResult, _stem_file_prefix  # noqa: E402


def _write_tone(path: Path, freq: float, fmt: str, ffmpeg: str | None, *, seconds: float = 0.5) -> None:
    frames = max(1, int(round(44100 * seconds)))
    t = np.arange(frames, dtype=np.float32) / 44100.0
    wave = 0.25 * np.sin(2.0 * np.pi * freq * t).astype(np.float32)
    stereo = np.stack((wave, wave), axis=1)
    if fmt == "wav" or not ffmpeg:
        sf.write(path, stereo, 44100, subtype="FLOAT")
        return
    tmp_wav = path.with_suffix(".tmp.wav")
    try:
        sf.write(tmp_wav, stereo, 44100, subtype="FLOAT")
        cmd = build_stem_encode_cmd(ffmpeg, str(tmp_wav), str(path), fmt)
        run_cmd(cmd, check=True)
    finally:
        tmp_wav.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--tone-seconds", type=float, default=0.5)
    args = parser.parse_args()

    state_dir = args.state_dir.resolve()
    output_dir = args.output_dir.resolve()
    state_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    opened_log = state_dir / "opened-folders.json"
    opened_folders: list[str] = []
    attempts: dict[str, int] = {}
    attempts_lock = threading.Lock()

    ffmpeg_status = detect_ffmpeg()
    exe_suffix = ".exe" if os.name == "nt" else ""
    ffmpeg_exe = (
        str(Path(ffmpeg_status.path) / ("ffmpeg" + exe_suffix))
        if ffmpeg_status.ok and ffmpeg_status.path
        else None
    )

    def fake_open_directory(directory: Path) -> None:
        opened_folders.append(str(Path(directory).resolve()))
        opened_log.write_text(json.dumps(opened_folders), encoding="utf-8")

    def fake_process_one(wid: str, job_args, progress=None, source_url=None, cancel_event=None) -> ItemResult:
        source = source_url or wid
        with attempts_lock:
            count = attempts.get(wid, 0) + 1
            attempts[wid] = count

        if "example.test/fail" in source and count == 1:
            return ItemResult(wid, title="Flaky Track", status="error", error="Simulated separation failure")

        if "example.test/slow" in source:
            if progress:
                progress(wid, "separate", 35, 100)
            if cancel_event is not None:
                cancel_event.wait(timeout=30)
                if cancel_event.is_set():
                    return ItemResult(wid, title="Slow Track", status="cancelled", error="Processing was cancelled")
            return ItemResult(wid, title="Slow Track", status="done")

        model = getattr(job_args, "model", DEFAULT_MODEL)
        stem = getattr(job_args, "stem", DEFAULT_STEM)
        fmt = getattr(job_args, "audio_format", "wav")
        out_root = Path(job_args.output_dir).expanduser().absolute()
        out_root.mkdir(parents=True, exist_ok=True)

        local_src = local_audio_path(source)
        if local_src is not None:
            title = local_src.stem
        elif "example.test/fail" in source:
            title = "Flaky Track"
        elif "example.test/" in source:
            slug = source.rsplit("/", 1)[-1].replace("-", " ").replace("_", " ").strip() or wid
            title = "Artist - " + slug.title()
        else:
            title = "Track " + wid

        base_dir = out_root / ".jamly" / wid / model
        meta_dir = (base_dir if stem == DEFAULT_STEM else base_dir / stem) / fmt
        meta_dir.mkdir(parents=True, exist_ok=True)
        prefix = _stem_file_prefix(wid, ("Artist", title), model=model, stem=stem)

        stem_paths: dict[str, str] = {}
        manifest_outputs: dict[str, dict[str, str]] = {}
        freqs = (220.0, 330.0, 440.0, 550.0, 660.0, 770.0)
        for index, kind in enumerate(expected_output_kinds(stem, model)):
            dest = out_root / f"{prefix}_{kind}.{fmt}"
            _write_tone(dest, freqs[index % len(freqs)], fmt, ffmpeg_exe, seconds=args.tone_seconds)
            stem_paths[kind] = str(dest)
            manifest_outputs[kind] = {"file": dest.name, "sha256": checksum(dest)}

        orig_name = None
        source_path = None
        if local_src is not None and local_src.is_file():
            source_path = str(local_src)
        else:
            orig_dest = out_root / f"{_stem_file_prefix(wid, ('Artist', title))}_original.wav"
            _write_tone(orig_dest, 180.0, "wav", None, seconds=args.tone_seconds)
            orig_name = orig_dest.name
            source_path = str(orig_dest)

        generation = uuid.uuid4().hex
        manifest = {
            "watch_id": wid,
            "source_url": source,
            "title": title,
            "processing_version": PROCESSING_VERSION,
            "generation": generation,
            "model": model,
            "stem": stem,
            "format": fmt,
            "outputs": manifest_outputs,
        }
        if orig_name is not None:
            manifest["original"] = orig_name
        meta_path = meta_dir / "metadata.json"
        meta_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return ItemResult(
            wid,
            title=title,
            status="done",
            meta_path=str(meta_path),
            generation=generation,
            source_path=source_path,
            stem_paths=stem_paths,
        )

    server.process_one = fake_process_one
    server.open_directory = fake_open_directory

    server_args = server.build_parser().parse_args([
        "--host", "127.0.0.1",
        "--port", "0",
        "--state-dir", str(state_dir),
        "--queue", str(state_dir / "jamly_queue.json"),
        "--output-dir", str(output_dir),
        "--audio-format", "wav",
        "--no-tray",
    ])
    app = server.ItemServer(server_args)
    launch_file = app.ui.launch(app.base_url)

    def on_signal(_signum, _frame):
        threading.Thread(target=app.stop, daemon=True).start()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    info = {
        "url": app.base_url,
        "launch_file": launch_file.as_uri(),
        "launch_path": str(launch_file),
        "token": app._token,
        "state_dir": str(state_dir),
        "output_dir": str(output_dir),
        "opened_log": str(opened_log),
    }

    def emit_ready():
        if app._serving_ready.wait(timeout=15):
            print(json.dumps(info), flush=True)

    threading.Thread(target=emit_ready, daemon=True).start()
    try:
        app.start()
    finally:
        app.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
