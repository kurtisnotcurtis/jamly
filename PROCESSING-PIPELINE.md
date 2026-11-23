# Processing Pipeline

Every input flows through the same local pipeline, whether it comes from the companion UI, the CLI, or the browser extension. See [README.md](README.md) for an overview and [DESIGN.md](DESIGN.md) for architecture details.

```mermaid
flowchart TD
    A["URL / ID or local audio file"] --> B["normalize_inputs_full — validate inputs and resolve a stable watch_id"]
    B --> C["Dedupe by watch_id and add pending jobs to the queue"]
    C --> P{"Smart cache check (under per-output lock)"}
    P -- "checksum valid, not --replace-existing" --> S(["Reuse existing export"])
    P -- "new or missing stems" --> M{"Local audio file?"}
    M -- yes --> F1["ffprobe: read audio stream metadata and duration"]
    M -- no --> F2["yt-dlp: fetch title, artist, uploader, duration"]
    F1 --> D1["Use local file directly as source"]
    F2 --> D2["yt-dlp: download audio to a temp workspace"]
    D1 --> SE["Demucs v4 worker: separate unscaled float32 stems"]
    D2 --> SE
    SE --> MIX["audio.render_pair: sum complement stems in float64 with one shared peak-safe gain (WAV float32 or 320k MP3 via ffmpeg)"]
    MIX --> MANI["Write and fsync metadata.json (SHA-256 per stem, generation id, optional original basename) in staging dir"]
    MANI --> PUB["publish_directory (under lock): swap manifest under .jamly/<id>/<model>/[<stem>/]<format>/ and move stems flat into <output>/"]
    PUB --> ORIG{"URL input?"}
    ORIG -- yes --> RET["Persist downloaded original atomically as <output>/<prefix>_original.<ext>"]
    ORIG -- no --> DONE(["Done: playable in the companion player and Exports list"])
    RET --> DONE
    S --> DONE
```

## Pipeline stages

- **Input normalization**: URLs, YouTube IDs, and supported local audio file paths are resolved to a stable `watch_id`; unsupported local file extensions and duplicates are rejected/deduped.
- **Smart cache**: when an existing manifest matches the processing version, identity, model, stem mode, format, and SHA-256 checksums (and `--replace-existing` is not set), the job is skipped and reused without re-downloading or re-separating. Missing stem files are regenerated automatically.
- **Metadata & download**: `yt-dlp` handles remote URLs/IDs; `ffprobe` probes local audio files directly with no network access.
- **Separation & mix**: Demucs v4 separates all model stems (4 or 6) as unscaled float32 WAVs. `audio.render_pair` validates matching length/rate/channels, sums all non-target stems in float64 for pair modes (or keeps all individual stems for `--stem all`), applies one shared clipping-safe gain, and encodes to float32 WAV or 320 kbps MP3 via `ffmpeg` (with a 4x-oversampled -1 dBFS decoded peak check).
- **Atomic publish & retained original**: `metadata.json` is written and fsynced in a sibling staging directory before `publish_directory` swaps the manifest under `<output>/.jamly/` and moves exported stems flat into `<output>/` (`[all]` is tagged for `--stem all` exports so stem pairs are never overwritten). For URL inputs, the downloaded original is then atomically persisted as `<output>/<prefix>_original.<ext>` and recorded as a relocatable basename in `manifest["original"]` while keeping canonical `source_url` intact.
