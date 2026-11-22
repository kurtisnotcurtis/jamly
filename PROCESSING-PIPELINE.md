# Processing Pipeline

Every input flows through the same local pipeline, whether it comes from the companion UI, the CLI, or the browser extension. See [README.md](README.md) for an overview and [DESIGN.md](DESIGN.md) for architecture details.

```mermaid
flowchart TD
    A["URL / ID or local-file inputs"] --> B["normalize_inputs_full — validate ids and resolve a stable watch_id"]
    B --> C["Dedupe by watch_id"]
    C --> Q["Add pending jobs to the queue"]
    Q --> P{"Smart cache check"}
    P -- "checksum valid, not --replace-existing" --> S(["Reuse existing export"])
    P -- "new job" --> M{"Fetch metadata"}
    M --> L{Local file?}
    L -- yes --> F1["ffprobe: read audio + duration from the file"]
    L -- no --> F2["yt-dlp: fetch title, artist, uploader, duration"]
    F2 --> D["yt-dlp: extract audio to a temp workspace"]
    F1 --> D2["Use local file directly as source"]
    D --> R{URL input?}
    R -- yes --> O["Retain original beside stems: .jamly/{watch_id}/<name>_original + record manifest source_url"]
    R -- no --> SE["Demucs v4: separate all stems"]
    D2 --> SE
    O --> SE
    SE --> MIX["ffmpeg: phase-aligned mix of the target pair (stem + no_<stem> residual subtraction)"]
    MIX --> PUB["publish_directory to ~/Music/jamly/.jamly/{watch_id}/{model}/{stem}/{format}"]
    PUB --> MANI["Write metadata.json manifest with a sha256 per stem and a generation id"]
    MANI --> DONE(["Done: playable in the player and Exports list"])
    S --> DONE
```

## Pipeline stages

- **Input normalization**: URLs, bare IDs, and local file paths are all resolved to a stable `watch_id`; duplicate sources are dropped.
- **Smart cache**: when an export's checksum already matches the manifest and `--replace-existing` is not passed, the job is skipped and reused without re-downloading or re-separating.
- **Metadata**: `yt-dlp` handles URLs/IDs; `ffprobe` reads local files directly.
- **Original retained**: for URL inputs, the extracted audio is copied next to the stems (named with an `_original` suffix) so it can be replayed as "Original" even offline; local-file inputs keep their own source path.
- **Separation & mix**: Demucs v4 separates all stems, then `ffmpeg` builds the requested pair (`drums`/`no_drums`, or vocals/bass/guitar/piano and their residuals) with phase-aligned subtraction.
