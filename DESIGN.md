# Current design

## Product boundary

Jamly is a local CLI exporter that accepts individual media URLs and produces reusable
`drums` and `no_drums` audio pairs using Hybrid Transformer Demucs v4 (`htdemucs` and `htdemucs_ft`).

## Pipeline

`run.process_one` validates one media item through the installed interpreter's `yt-dlp`,
invokes an isolated `demucs_worker` subprocess, and publishes a matched `drums`/`no_drums` pair.
Playlists, collections, and active live streams are rejected before download.

`demucs_worker` uses `demucs.api.Separator` and writes raw float32 WAV stems with `soundfile`
so individual stems are not rescaled or clamped before summing `bass + other + vocals`.

`audio` validates matching frame count, sample rate, and channel count, sums all non-drum stems
in 64-bit float blocks, rejects non-finite samples, and applies one shared peak-safe gain to both
outputs. MP3 exports use 320 kbps LAME encoding and a 4x-oversampled decoded peak check targeting -1 dBFS.

## Output transaction

Each `<output>/<stable-id>/<model>/<format>` directory is staged in a sibling directory, fsynced,
and atomically swapped into place with `.previous` backup recovery and SHA-256 manifest verification.
