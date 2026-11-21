# Changelog

All notable changes to Jamly are documented here. This project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Release tags use `vX.Y.Z` and must match the package version reported by `python tools/version.py --check`.

## [0.2.0] - 2026-11-21

### Added
- **Practice toggle buttons on export cards.** Each export card now offers the same icon-only, label-free circular toggles as the sticky player (Original → Drums → Drumless), reusing the drum/drumless icons from the list. The sticky player gained its own `.player-toggles` group with spacing from the `.control-row` elements and vertically centered alignment (`src/jamly/web/app.js`, `app.css`, `index.html`).
- **Retained original audio for URL inputs.** YouTube/stream jobs previously lost their source once yt-dlp's temp download was discarded. The extracted original is now copied to `.jamly/{watch_id}/{normalized}_original{ext}` beside the published stems, and its local path is recorded in the manifest `source_url` (local-file inputs keep their own source path).
- **`/source/{watch_id}` backend route.** Streams the retained original as a local file, or proxies an `http(s)` source while online. Combined with the client's new `sourceURL()` method (`browser/extension/jamly_client.js`), this makes the "Original" toggle work for URL jobs without a network dependency.
- **`WatchQueue.get()` default argument** and a `source_url` parameter on `finish()`.

### Changed
- Jobs that were previously offered only as drums/drumless now also expose an **"Original"** playback source when it is retained locally.

### Documentation
- Added a **"Processing Pipeline"** section to the README with a Mermaid flowchart covering input normalization/dedupe, smart-cache reuse, metadata via yt-dlp/ffprobe, yt-dlp extraction, Demucs separation, the ffmpeg phase-aligned residual mix, and `publish_directory` + `metadata.json`.

## [0.1.x]

Earlier releases (0.1.2–0.1.4) were tracked by git history and release tags before a dedicated changelog existed.
