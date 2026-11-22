# Changelog

All notable changes to Jamly are documented here. This project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Release tags use `vX.Y.Z` and must match the package version reported by `python tools/version.py --check`.

## [Unreleased]

_Nothing yet._

## [0.2.5] - 2026-11-22

### Fixed
- **CI bundle smoke test survives the retained-original change.** After `0.2.4` started recording a job's local source path in `source_url`, the packaged CI smoke check (`tools/bundle_smoke.py`) stopped matching completed jobs against the submitted HTTP URL and failed with `StopIteration`. It now correlates each queued iteration by its stable `model` + `audio_format` pair, which are unique per `/add` (htdemucs/mp3 vs htdemucs_ft/wav) and unaffected by the new source-path behavior.

## [0.2.4] - 2026-11-22

### Fixed
- **Regenerate deleted stems without `--replace-existing`.** When a track's manifest is still valid but one or more audio stems were deleted, Jamly now recognises the missing stems and reprocesses automatically instead of demanding the `--replace-existing` flag. A genuinely different or checksum-modified output still requires the flag (`src/jamly/run.py`, `src/jamly/outputs.py`).
- **Original stem for URL inputs.** The retained copy of a downloaded stream (e.g. YouTube links) is now persisted beside the published stems and recorded as the job's source path, so the companion UI exposes an "Original" toggle again. Previously the retention copy never ran for URL downloads, leaving `source_path` empty (`src/jamly/run.py`).

### Changed
- **Systray tooltip.** Replaced the em-dash with a hyphen in the desktop tray status text (`src/jamly/tray.py`).

### Added
- **Processing pipeline documentation.** The Mermaid flowchart and stage breakdown previously inline in `README.md` moved to a dedicated `PROCESSING-PIPELINE.md`, which `README.md` now links to.

## [0.2.3] - 2026-11-21

### Added
- **Build-reproducibility helper — `tools/repro_dist.py`.** Stdlib-only tool that rewrites an sdist or wheel with a fixed gzip epoch, sorted tar member order, LF metadata newlines (CRLF→LF in generated `METADATA`/`WHEEL`), and self-consistent RECORD hashes so local, CI and release artifacts match byte-for-byte after identical normalization. (`tools/repro_dist.py`)

### Changed
- **Companion status text.** The companion connection label now reads "Jobs keep running even after you close this page." instead of the previous wording, making it clear that jobs outlive the browser tab. (`src/jamly/web/app.js`)

### Fixed
- **Reproducible sdist.** `MANIFEST.in` excludes `__pycache__` bytecode caches from the sdist instead of shipping dev-only `*.pyc` files that embed source mtimes, so local and CI builds stay in lockstep. (`MANIFEST.in`)

## [0.2.2] - 2026-11-21

### Added
- **Local CI mirror — `tools/dev-check.py`.** Stdlib-only script that reproduces `.github/workflows/ci.yml` step-for-step and fails fast on the first broken stage (lockstep version → `jamly-doctor` → pytest → unittest → ruff → Node extension tests → wheel build + fresh-install smoke). Heavier real-model checks opt in with `--integration`; the full browser run opts in with `--browser`.
- **Pre-commit and pre-push gates — `tools/hooks/`.** Install once with `git config core.hooksPath tools/hooks`. The pre-commit gate runs the fast, wheel-free dev-check so commits stay quick; the pre-push gate runs the full suite (wheel smoke + audio pipeline) so a push is already CI-validated. Documented in `README.md` and `AGENTS.md`.

### Fixed
- **Bundle smoke test.** Downloads from `GET /outputs` instead of a nonexistent `POST /app/download`, so native desktop archive verification succeeds across platforms (`tools/bundle_smoke.py`).
- **Browser smoke test.** Tolerates launch-page navigation while probing readiness and toggles split outputs via the player toggle (not the stem button), making headless Chrome validation reliable (`tools/browser_smoke.mjs`).

## [0.2.1] - 2026-11-21

### Fixed
- **Retained original for URL inputs.** The manifest now keeps the canonical `source_url` (the YouTube/stream URL) instead of overwriting it with the retained local path; the copied original is recorded under a new manifest `original` key and is still surfaced via `/source/{watch_id}`. Also serves retained WAV audio with the canonical `audio/wav` Content-Type across platforms, where Linux's `mimetypes` otherwise reported `audio/x-wav`. (`src/jamly/run.py`, `src/jamly/server.py`).

## [0.2.0] - 2026-11-21

### Added
- **Practice toggle buttons on export cards.** Each export card now offers the same icon-only, label-free circular toggles as the sticky player (Original → Drums → Drumless), reusing the drum/drumless icons from the list. The sticky player gained its own `.player-toggles` group with spacing from the `.control-row` elements and vertically centered alignment (`src/jamly/web/app.js`, `app.css`, `index.html`).
- **Retained original audio for URL inputs.** YouTube/stream jobs previously lost their source once yt-dlp's temp download was discarded. The extracted original is now copied to `.jamly/{watch_id}/{normalized}_original{ext}` beside the published stems; its local path is recorded under a new manifest `original` key (its canonical `source_url` is preserved) and served via `/source/{watch_id}` (local-file inputs keep their own source path).
- **`/source/{watch_id}` backend route.** Streams the retained original as a local file, or proxies an `http(s)` source while online. Combined with the client's new `sourceURL()` method (`browser/extension/jamly_client.js`), this makes the "Original" toggle work for URL jobs without a network dependency.
- **`WatchQueue.get()` default argument** and a `source_url` parameter on `finish()`.

### Changed
- Jobs that were previously offered only as drums/drumless now also expose an **"Original"** playback source when it is retained locally.

### Documentation
- Added a **"Processing Pipeline"** section to the README with a Mermaid flowchart covering input normalization/dedupe, smart-cache reuse, metadata via yt-dlp/ffprobe, yt-dlp extraction, Demucs separation, the ffmpeg phase-aligned residual mix, and `publish_directory` + `metadata.json`.

## [0.1.4] - 2026-11-19

### Fixed
- **Publish-secret handling in CI.** GitHub Actions now reads publish secrets from job environment variables instead of step `if` expressions, so secret values are not evaluated or leaked through conditional checks during Homebrew/AUR/WinGet packaging (`github/workflows/companions.yml`).

## [0.1.3] - 2026-11-19

### Added
- **Job timing in the companion UI.** The web companion now shows how long each job has been processing, alongside the existing queue state.
- **Automated publishing on tagged releases.** Homebrew tap, AUR package, and WinGet manifest are published automatically when a release tag is pushed (`github/workflows/companions.yml`).

### Changed
- Simplified the companion UI title for clearer labeling.

## [0.1.2] - 2026-11-19

### Added
- **PyPI distribution.** CLI install instructions now point to `jamly-audio-sep` on PyPI, and the Homebrew tap and AUR package are documented in the README (`README.md`).

### Changed
- Updated companion UI export controls, site copy, and release preparation for the new PyPI publish path.

## [0.1.1] - 2026-11-18

### Added
- **WinGet distribution.** WinGet manifest templates, release rendering, and symlink resolution (`tools/`). Desktop binaries now embed the Jamly icon natively in both Windows and Linux companion bundles.

### Changed
- **Jamly branding overhaul.** New logo mark (a split-drum/trumpet design with a jam note), a "raspberry jam" color palette across the site and companion UI, a larger hero logo, centered README logo and header links, and a shortened GitHub nav link. Terminal commands and comments are highlighted on the site tabs, and mobile layout and navigation were improved on the static site.

## [0.1.0] - 2026-11-13

### Added
- **Initial release.** The first packaged Jamly, covering core features built up since July 2022:
  - A CLI drum/drumless track splitter using Demucs separation and a phase-aligned ffmpeg residual mix (`src/jamly`).
  - An optional loopback web companion UI with single-use launch-file ticket pairing, plus a Chrome Manifest V3 extension and YouTube/YouTube Music userscript helpers.
  - Native desktop bundling from source (ffmpeg, ffprobe, LAME, OpenSSL) and PyInstaller one-dir packaging that uses CPU-only PyTorch on Linux (`tools/`, `Jamly.spec`).
  - A static showcase site with GitHub Pages deployment and CI matrix, tested by pytest, unittest, Node, and Ruff.
