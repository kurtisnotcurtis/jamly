"""Run Demucs in an isolated process and preserve its unscaled floating-point estimates."""
from __future__ import annotations

import argparse
from pathlib import Path

from .models import DEFAULT_MODEL, MODELS, model_stems


def resolve_device(device: str) -> str:
    if device != "auto":
        return device
    import torch
    return ("cuda" if torch.cuda.is_available()
            else "mps" if torch.backends.mps.is_available() else "cpu")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("audio")
    parser.add_argument("--out", required=True)
    parser.add_argument("-n", "--name", choices=tuple(MODELS), default=DEFAULT_MODEL)
    parser.add_argument("-j", "--jobs", type=int, default=0)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda", "mps"), default="auto")
    args = parser.parse_args(argv)

    import soundfile as sf
    from demucs.api import Separator

    last_percent = -1

    def on_callback(data: dict) -> None:
        nonlocal last_percent
        if not isinstance(data, dict) or data.get("state") != "end":
            return
        audio_length = int(data.get("audio_length") or 0)
        if audio_length <= 0:
            return
        models = max(1, int(data.get("models") or 1))
        model_idx = max(0, int(data.get("model_idx_in_bag") or 0))
        offset = max(0, int(data.get("segment_offset") or 0))
        chunk_end = min(audio_length, offset + int(separator.samplerate * 8))
        fraction = (model_idx + chunk_end / audio_length) / models
        percent = max(1, min(99, int(round(fraction * 100))))
        if percent > last_percent:
            last_percent = percent
            print("JAMLY_PROGRESS:%d" % percent, flush=True)

    device = resolve_device(args.device)
    separator = Separator(
        model=args.name, device=device, jobs=args.jobs, progress=True, callback=on_callback
    )
    _original, stems = separator.separate_audio_file(args.audio)
    output = Path(args.out)
    output.mkdir(parents=True, exist_ok=True)
    for name in model_stems(args.name):
        samples = stems[name].detach().cpu().numpy().T
        # Demucs' file writer rescales/clamps individual stems; the pair gets one shared gain later.
        sf.write(output / (name + ".wav"), samples, separator.samplerate, subtype="FLOAT")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
