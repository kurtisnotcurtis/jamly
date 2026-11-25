"""Build a sample-aligned drums/drumless pair with one shared clipping-prevention gain."""
from __future__ import annotations

from contextlib import ExitStack
import math
from pathlib import Path
import re
import shutil

import numpy as np
import soundfile as sf

from .core import build_stem_encode_cmd, check_cancelled, run

BLOCK_FRAMES = 65536


def _validate_stems(stems: dict[str, str], target_stem: str) -> None:
    if target_stem == "all":
        if len(stems) < 2:
            raise ValueError("At least two stems are required")
        return
    if target_stem not in stems or len(stems) < 2:
        if target_stem == "drums":
            raise ValueError("A drums stem and at least one non-drum stem are required")
        raise ValueError("A %s stem and at least one other stem are required" % target_stem)


def _open_matching_stems(stack: ExitStack, stems: dict[str, str],
                         reference_key: str | None) -> tuple[dict, tuple[int, int, int]]:
    inputs = {name: stack.enter_context(sf.SoundFile(path)) for name, path in stems.items()}
    reference = inputs[reference_key] if reference_key else next(iter(inputs.values()))
    shape = (reference.frames, reference.samplerate, reference.channels)
    if not reference.frames:
        raise ValueError("The separator produced empty audio")
    if any((file.frames, file.samplerate, file.channels) != shape for file in inputs.values()):
        raise ValueError("Separated stems have different lengths, sample rates, or channels")
    return inputs, shape


def mix_pair(stems: dict[str, str], workdir: Path, cancel_event=None, *,
             target_stem: str = "drums", on_progress=None) -> tuple[dict, dict]:
    _validate_stems(stems, target_stem)
    export_all = target_stem == "all"
    complement = "no_" + target_stem
    paths = (
        {name: Path(path) for name, path in stems.items()}
        if export_all
        else {
            target_stem: workdir / ("pair-" + target_stem + ".wav"),
            complement: workdir / ("pair-no-" + target_stem + ".wav"),
        }
    )
    peak = 0.0
    processed = 0
    last_percent = -1
    with ExitStack() as stack:
        inputs, shape = _open_matching_stems(stack, stems, None if export_all else target_stem)
        total_frames = shape[0]
        outputs = (
            None if export_all else {
                name: stack.enter_context(sf.SoundFile(
                    path, "w", samplerate=shape[1], channels=shape[2], subtype="FLOAT"
                )) for name, path in paths.items()
            }
        )
        while True:
            check_cancelled(cancel_event)
            blocks = {name: file.read(BLOCK_FRAMES, dtype="float64", always_2d=True)
                      for name, file in inputs.items()}
            block_len = len(next(iter(blocks.values())))
            if not block_len:
                break
            if not all(np.isfinite(block).all() for block in blocks.values()):
                raise ValueError("The separator produced non-finite samples")
            if export_all:
                peak = max(peak, *(float(np.abs(block).max()) for block in blocks.values()))
            else:
                backing = sum(block for name, block in blocks.items() if name != target_stem)
                peak = max(peak, float(np.abs(blocks[target_stem]).max()), float(np.abs(backing).max()))
                outputs[target_stem].write(blocks[target_stem])
                outputs[complement].write(backing)
            processed += block_len
            if on_progress is not None and total_frames > 0:
                pct = min(100, int(round(processed / total_frames * 100)))
                if pct > last_percent:
                    last_percent = pct
                    on_progress(pct)
    return paths, {"frames": shape[0], "sample_rate": shape[1], "channels": shape[2],
                   "unscaled_peak": peak}


def write_scaled(source: Path, output: Path, gain: float, cancel_event=None) -> None:
    with sf.SoundFile(source) as incoming, sf.SoundFile(
        output, "w", samplerate=incoming.samplerate, channels=incoming.channels, subtype="FLOAT"
    ) as outgoing:
        for block in incoming.blocks(blocksize=BLOCK_FRAMES, dtype="float64", always_2d=True):
            check_cancelled(cancel_event)
            outgoing.write(block * gain)


def decoded_peak(path: Path, ffmpeg: str, sample_rate: int, cancel_event=None) -> float:
    result = run([
        ffmpeg, "-nostdin", "-hide_banner", "-i", str(path),
        "-af", "aresample=%d,astats=metadata=0:reset=0" % (sample_rate * 4),
        "-f", "null", "-",
    ], check=True, cancel_event=cancel_event)
    values = re.findall(r"Peak level dB:\s*([-+\w.]+)", result.stderr)
    if not values:
        raise RuntimeError("ffmpeg did not report a decoded audio peak")
    levels = [float(value) for value in values]
    if any(math.isnan(value) or value == math.inf for value in levels):
        raise ValueError("Encoded audio has an invalid peak")
    return max(10 ** (value / 20) for value in levels)


def render_pair(stems: dict[str, str], workdir: Path, destinations: dict[str, Path],
                fmt: str, ffmpeg: str, cancel_event=None, *,
                target_stem: str = "drums", on_progress=None) -> dict:
    last_percent = -1

    def report(pct: int) -> None:
        nonlocal last_percent
        if on_progress is None:
            return
        clamped = max(1, min(99, int(pct)))
        if clamped > last_percent:
            last_percent = clamped
            on_progress(clamped)

    mix_weight = 60 if fmt == "wav" else 25
    raw, info = mix_pair(
        stems, workdir, cancel_event, target_stem=target_stem,
        on_progress=(lambda pct: report(round(pct * mix_weight / 100))) if on_progress else None,
    )
    ceiling = 10 ** (-1 / 20) if fmt == "mp3" else 0.999
    gain = min(1.0, ceiling / info["unscaled_peak"]) if info["unscaled_peak"] else 1.0
    stem_count = max(1, len(raw))
    encode_end = 99 if fmt == "wav" else 75
    for _attempt in range(3):
        for index, (name, source) in enumerate(raw.items(), 1):
            output = destinations[name]
            if fmt == "wav":
                if gain == 1.0:
                    shutil.move(str(source), str(output))
                else:
                    write_scaled(source, output, gain, cancel_event)
            else:
                scaled_input = source
                if gain != 1.0:
                    scaled_input = workdir / ("scaled-" + name + ".wav")
                    write_scaled(source, scaled_input, gain, cancel_event)
                cmd = build_stem_encode_cmd(ffmpeg, str(scaled_input), str(output), fmt)
                run(cmd, check=True, cancel_event=cancel_event)
            report(round(mix_weight + (encode_end - mix_weight) * (index / stem_count)))
        if fmt == "wav":
            peak = info["unscaled_peak"] * gain
            break
        peaks = []
        dest_paths = list(destinations.values())
        dest_count = max(1, len(dest_paths))
        for index, path in enumerate(dest_paths, 1):
            peaks.append(decoded_peak(path, ffmpeg, info["sample_rate"], cancel_event))
            report(round(encode_end + (99 - encode_end) * (index / dest_count)))
        peak = max(peaks)
        if peak <= ceiling * 1.00001:
            break
        gain *= ceiling / peak * 0.999
    else:
        raise RuntimeError("The MP3 pair could not meet the decoded peak target")
    return dict(info, gain=gain, peak=peak, peak_ceiling=ceiling, format=fmt)
