# Changelog

All notable changes to Jamly are documented here. This project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Release tags use `vX.Y.Z` and must match the package version reported by `python tools/version.py --check`.

## [0.2.2] - 2026-11-21

### Added
- **Local CI mirror — `tools/dev-check.py`.** Stdlib-only script that reproduces `.github/workflows/ci.yml` step-for-step and fails fast on the first broken stage (lockstep version → `jamly-doctor` → pytest → unittest → ruff → Node extension tests → wheel build + fresh-install smoke). Heavier real-model checks opt in with `--integration`; the full browser run opts in with `--browser`.
- **Pre-commit and pre-push gates — `tools/hooks/`.** Install once with `git config core.hooksPath tools/hooks`. The pre-commit gate runs the fast, wheel-free dev-check so commits stay quick; the pre-push gate runs the full suite (wheel smoke + audio pipeline) so a push is already CI-validated. Documented in `README.md` and `AGENTS.md`.

### Fixed
- **Bundle smoke test.** Downloads from `GET /outputs` instead of a nonexistent `POST /app/download`, so native desktop archive verification succeeds across platforms (`tools/bundle_smoke.py`).
- **Browser smoke test.** Tolerates launch-page navigation while probing readiness and toggles split outputs via the player toggle (not the stem button), making headless Chrome validation reliable (`tools/browser_smoke.mjs`).


## [Unreleased]

_Nothing yet._

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

