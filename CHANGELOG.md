# Changelog

All notable changes to Jamly are documented here. This project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Release tags use `vX.Y.Z` and must match the package version reported by `python tools/version.py --check`.

## [Unreleased]

_Nothing yet._

## [0.5.0] - 2026-11-24

### Added
- **Thin bottom progress bar on active Exports cards.** Stream real download byte progress (`yt-dlp --progress-template`) and per-stem mix/encode/peak-check progress alongside separation chunk progress, expose monotonic weighted `overall_progress` on `/status`, and render a subtle animated 4px bottom progress bar in the companion UI with in-place DOM updates across polls (`src/jamly/core.py`, `src/jamly/run.py`, `src/jamly/audio.py`, `src/jamly/server.py`, `src/jamly/web/app.js`, `src/jamly/web/app.css`).

### Changed
- **Cleaner Exports list controls and practice player footer.** Replaced the `Original` toggle icon with a play triangle (`42px` on export cards, uniform `34px` in the player footer), moved source link / source folder buttons inline after the track title so playback toggles stay cleanly right-aligned across rows, converted the active Cancel control to a vertically centered icon-only `×`, moved `"Remove from list"` to a borderless top-right `×` dismiss button on terminal cards, moved `"Show files in folder"` from individual cards into the practice player footer, and hid `"Retry / check files"` on completed items (`src/jamly/web/index.html`, `src/jamly/web/app.js`, `src/jamly/web/app.css`).

### Fixed
- **Keep player toggle buttons circular and consistent on Retry and secondary exports.** Retain the matching primary export in `outputs` while retrying instead of demoting it into secondary `exports`, and align `.stem-btn` pill shape and colors with `.player-toggle` (`src/jamly/server.py`, `src/jamly/web/app.css`).
- **Prevent worker crash and tray Idle desync when retrying legacy or bracketed local paths.** Check explicit local paths before URL parsing and catch malformed `urlsplit` IPv6 errors (such as Windows paths containing `[video_id]`), guard `local_audio_path()` before marking a job running in `ItemServer._process_one`, accept retained `.webm` audio files in `LOCAL_AUDIO_EXTENSIONS` / `audio/webm`, and surface `Working` / `Error` and stage percentages accurately in `tray_status()` (`src/jamly/normalize.py`, `src/jamly/server.py`, `src/jamly/http_handler.py`, `src/jamly/tray.py`, `src/jamly/web/app.js`).

## [0.4.1] - 2026-11-24

### Fixed
- **Bundle `sympy` and `mpmath` for Apple Silicon MPS separation.** Removed `sympy` and `mpmath` from PyInstaller `excludes` in `tools/Jamly.spec` so PyTorch's macOS MPS `group_norm` path (`torch._refs` → `symbolic_shapes`) works in the standalone companion app (`tools/Jamly.spec`, `tests/test_native_audio.py`).
- **Avoid Windows TCP reset on early HTTP error responses.** Drain small unread request bodies (up to 64 KiB) after flushing early `400`/`401`/`403`/`415` rejections before closing the socket so Winsock sends a clean `FIN` instead of `TCP RST` (`src/jamly/http_handler.py`, `tests/test_server.py`).

## [0.4.0] - 2026-11-24

### Changed
- **Unified single-command CLI (`jamly`).** Replaced the five separate console scripts (`jamly`, `jamly-companion`, `jamly-server`, `jamly-doctor`, `jamly-service`) and desktop bundle flag aliases (`--cli`, `--doctor`, `--service`) with one `jamly` entrypoint dispatching `companion` (default when invoked with no arguments), `split`, `server`, `doctor`, and `service` identically across PyPI, Homebrew, WinGet, AUR, and portable bundles; `jamly split` also accepts positional inputs alongside `-i` (`src/jamly/cli.py`, `src/jamly/run.py`, `pyproject.toml`, `tools/desktop_entry.py`, `packaging/`).
- **Loopback-only backend.** Restricted `jamly server` to loopback addresses (`127.0.0.1`, `localhost`, `::1`), removing `--allow-host` and non-loopback code paths (`src/jamly/server.py`, `src/jamly/http_handler.py`, `src/jamly/companion.py`, `src/jamly/companion_ui.py`, `src/jamly/tray.py`).
- **Dropped Intel macOS desktop builds.** Removed `macos-15-intel` (`darwin-x86_64`) from the companion build matrix, native build targets, Homebrew cask, and `sphn` CMake extra-build workaround (`pyproject.toml`, `.github/workflows/companions.yml`, `tools/native_audio.py`, `tools/build_companion.py`, `tools/render_homebrew.py`, `packaging/homebrew/`).
- **Playwright-captured companion preview on static site.** Replaced the hand-coded HTML/CSS companion mockup in `site/` with a live screenshot captured via `tools/capture_site_preview.mjs` (`site/companion-preview.png`, `site/index.html`, `site/styles.css`) and updated site CLI/extension pairing docs.

### Fixed
- **Active `.player-toggle` hover state.** Kept active circular stem toggles visibly highlighted while hovered (`src/jamly/web/app.css`).

## [0.3.1] - 2026-11-24

### Added
- **Playwright E2E tests and extension options unit tests.** Added a fast deterministic Playwright E2E suite (`tests/e2e/companion.e2e.test.mjs`, `tests/e2e/mock_server.py`) covering launch-file pairing, settings persistence, error/retry, live progress/cancel, local uploads, multi-preset practice player playback and stem switching, search/status/model filters, export folder/clear/rescan actions, manual pairing, clean shutdown, and unpacked browser extension pairing/origin enforcement/popup submission; added unit tests for `jamly_options.js` (`browser/extension/jamly_options_test.js`); and wired both into `tools/dev-check.py` (`--e2e`) and `.github/workflows/ci.yml`.

### Changed
- **Faster Companion package CI builds.** Restricted bundled FFmpeg builds to required audio/cover-art components and OpenSSL builds to static libraries (`build_libs` / `install_dev` with `no-apps no-docs no-legacy`), parallelized independent native dependency downloads and builds using all available CPU cores, pruned intermediate native build trees before caching, switched Linux companion archiving to `pigz -6` / `gzip -6`, excluded unused PyTorch compiler/dev packages in `tools/Jamly.spec`, added prefix fallback caching for `uv` (avoiding `sphn` rebuilds across lockfile bumps on Intel macOS), skipped native toolchain installs on cache hits, and cached Python notices and smoke-test checkpoints on non-release builds (`tools/native_audio.py`, `tools/build_companion.py`, `tools/Jamly.spec`, `.github/workflows/companions.yml`).

### Fixed
- **Enforce extension origin authorization during pairing and status checks.** Added `POST /handshake` support and switched `jamlyClient.verifyBackend` to `POST /handshake` so Chromium/Edge always sends the `Origin` header even when the extension holds `host_permissions`, rejecting unauthorized extension origins at pairing time before any token is sent (`src/jamly/http_handler.py`, `browser/extension/jamly_client.js`, `src/jamly/web/jamly_client.js`).
- **Avoid macOS bind DNS stalls and extension options form hydration races.** Overrode `_HTTPServer.server_bind` to skip `socket.getfqdn()` reverse-DNS lookups on server startup (`src/jamly/server.py`) and prevented asynchronous `chrome.storage.local.get` hydration from overwriting values already typed into the extension options page (`browser/extension/jamly_options.js`).

## [0.3.0] - 2026-11-23

### Changed
- **Practice player in static site companion preview.** Showcase the docked practice player and stem toggles in the GitHub Pages companion preview (`site/index.html`, `site/styles.css`).
- **Breaking CLI, API, and extension-pairing hardening.** Removed `--server-token` in favor of `JAMLY_SERVER_TOKEN` and `--server-token-file`; enforced a 32-character minimum pairing token length; stopped inferring `--state-dir` from `--queue`; stopped returning the master pairing token from `POST /app/extension` (now returns `token_command` and manages authorized extension origins); restricted `POST /app/launch` to master-token callers; required explicit authorization (`--allow-origin` or companion UI setup) for `chrome-extension://` and `moz-extension://` origins; required `/handshake` `client_proof` verification before the extension and userscript attach credentials; and added 60-minute duration / 512 MB media caps with bounded download and separation timeouts (`src/jamly/config.py`, `src/jamly/server.py`, `src/jamly/http_handler.py`, `src/jamly/companion.py`, `src/jamly/run.py`, `src/jamly/core.py`, `src/jamly/web/`, `browser/`).

### Fixed
- **Never delete unrelated user files on startup or list removal.** Isolate temporary upload files under `uploads/.jamly-tmp/` and track Jamly-created uploads in an explicit ledger (`uploads-ledger.json`) so startup pruning and `/app/clear` never delete unrelated files (`src/jamly/config.py`, `src/jamly/server.py`, `src/jamly/http_handler.py`).
- **Gate PyPI and GitHub Releases on CI, tag refs, and reproducible pinned inputs.** Make `ci.yml` reusable via `workflow_call` and gate both `pypi.yml` and `companions.yml` releases on it; restrict PyPI publishing to `v*` tags; disable native-build cache restore/save and force clean source rebuilds on release tags; SHA-256-pin the Linux CPU-only PyTorch wheel (`tools/linux-cpu-torch.json`, `tools/install_linux_cpu_torch.py`); and refuse to clobber existing GitHub Releases (`.github/workflows/ci.yml`, `.github/workflows/pypi.yml`, `.github/workflows/companions.yml`, `tools/native_audio.py`, `tools/build_companion.py`).
- **Regenerate stems when a local audio file changes in place.** Record source file size, mtime, and SHA-256 in the manifest and invalidate local cache hits when the file changes (`src/jamly/run.py`).
- **Fix export publishing edge cases and lock contention.** Resolve `audio_directory` from the final `.jamly` path component, delete superseded stem filenames on replacement after title changes, and release the export file lock before streaming downloads over HTTP (`src/jamly/outputs.py`, `src/jamly/http_handler.py`).
- **Harden export rescanning and `/source` path checks.** Validate manifest field types in `_discover_manifest` and `WatchQueue.restore_export`, restrict `manifest["original"]` resolution strictly to the export directory, and verify `/source/<id>` paths against the job's output directory or normalized local source (`src/jamly/outputs.py`, `src/jamly/server.py`, `src/jamly/watch_queue.py`, `src/jamly/http_handler.py`).
- **Prevent stream-error response corruption and GET body smuggling.** Mark response headers sent before writing streamed chunks so mid-stream read errors close the socket instead of appending an HTTP 404 to the body, and reject `GET`/`OPTIONS` requests carrying a body or `Transfer-Encoding` with `400` and `Connection: close` (`src/jamly/http_handler.py`).
- **Private queue permissions.** Write queue files with `0600` permissions in `0700` state directories (`src/jamly/watch_queue.py`, `src/jamly/config.py`).
- **Pipeline, service, and tooling fixes.** Decode `file://` URIs once instead of twice (`src/jamly/normalize.py`); skip reading open non-TTY stdin when `-i` inputs are passed (`src/jamly/run.py`); prevent infinite restarts on config-error exit 2 in systemd and launchd definitions (`src/jamly/service.py`); set `S_IFREG` (`0o100000`) instead of group-write (`0x10`) in wheel normalization (`tools/repro_dist.py`); open new `[Unreleased]` sections above dated releases (`tools/release.py`); mark tracked Git hooks executable (`tools/hooks/`); and add negative checksum/HTTPS download tests with `.partial` cleanup on insecure redirects (`tools/native_audio.py`, `tests/test_native_audio.py`).

## [0.2.6] - 2026-11-23

### Fixed
- **Prevent arbitrary file reads and HTTP proxying through `/source/<id>`.** Reject non-audio local paths in input normalization, reject local file paths on non-loopback binds, remove the HTTP proxy branch from `/source/<id>`, and serve only verified `original_path` files recorded on completed jobs (`src/jamly/normalize.py`, `src/jamly/server.py`, `src/jamly/run.py`).
- **Keep canonical `source_url` intact across completions and retries.** Store the retained original audio path in a dedicated `original_path` job field (and restore it during export rescanning from `manifest["original"]`), so URL retries reprocess the original URL instead of the retained local copy (`src/jamly/watch_queue.py`, `src/jamly/server.py`, `src/jamly/run.py`, `src/jamly/web/app.js`).
- **Tag `all`-stems exports in output filenames.** Include `[all]` in exported filenames for `--stem all` so exporting all stems never overwrites an existing stem pair (such as `drums`/`no_drums`) at a different gain, removing the sibling-manifest checksum patching code (`src/jamly/run.py`, `src/jamly/outputs.py`).
- **Requeue in-flight jobs reliably on service shutdown.** Add `KillMode=mixed` to the Linux systemd unit, raise cancellation when a failing subprocess exits after `cancel_event` is set, and requeue any non-completed job during server shutdown unless explicitly cancelled by the user (`src/jamly/service.py`, `src/jamly/core.py`, `src/jamly/server.py`).
- **Clean up uploaded audio files.** Delete private uploaded copies in `state_dir/uploads/` when their job is cleared from the list and prune orphaned uploads on backend startup (`src/jamly/server.py`).
- **Avoid lock-contention failures during playback/retry.** Hold the per-output file lock in `process_one` only during cache-check and publish with cancel-aware waiting, and report sustained contention cleanly without exposing raw `.lock` paths (`src/jamly/run.py`).
- **Do not re-run cancelled jobs on bare `jamly` invocation.** Retry only `error` jobs when invoked without `-i` inputs, matching `DESIGN.md` (`src/jamly/run.py`).
- **Handle cross-drive output paths on Windows.** Fall back to the absolute path in `Logger.link` when `os.path.relpath` raises `ValueError` across drives (`src/jamly/logging.py`).
- **Companion UI fixes.** Return the promise from `playOutput` (and wrap sticky player toggles in `action`) so audio load failures reach the error banner; keep secondary-export toggles in sync when playing secondary exports; remove stale `loadedmetadata` listeners on rapid track switches; update live elapsed times in place instead of rebuilding `#jobs` every 2s; honor the server's normalized `Content-Type` header; implement HTTP byte-Range (`206`/`416`) streaming; stop "Open folder" and "Rescan" from silently saving form values as defaults; remove dead/broken CSS rules; ship `jamly_client.js` as package data and share `runtime.share_path` across browser assets and the Windows tray icon (`src/jamly/web/app.js`, `src/jamly/web/app.css`, `src/jamly/http_handler.py`, `src/jamly/companion_ui.py`, `src/jamly/runtime.py`, `src/jamly/tray.py`).
- **Relocatable manifests, path normalization, and pre-push integration check.** Store `manifest["original"]` as a relocatable basename (still reading legacy absolute paths), normalize `instance_path` consistently with `pairing_proof` on Windows, guard missing `SystemRoot` in `_terminate`, return `404` for unknown job IDs on `/outputs/`, write `server.json` atomically via `write_private_json`, probe `soundfile` inside the target environment in `tools/dev-check.py --integration`, and wire `tools/repro_dist.py` into `dev-check.py` and `pypi.yml` (`src/jamly/run.py`, `src/jamly/config.py`, `src/jamly/core.py`, `src/jamly/http_handler.py`, `src/jamly/service.py`, `tools/dev-check.py`, `.github/workflows/pypi.yml`).
- **Cancel/re-upload edge cases, streaming release hashes, and browser polish.** Ensure user-cancelled jobs are recorded as `cancelled` even when the worker reports a failure, reuse identical content-addressed upload files instead of replacing an open file on Windows, stream release SHA-256 digests in chunks, keep the docs site copy-button label stable on rapid clicks, omit `Content-Type` on userscript `GET` requests, and align `PROCESSING-PIPELINE.md` and `README.md` with float64 stem summation and manifest publish order (`src/jamly/server.py`, `src/jamly/http_handler.py`, `tools/package_render.py`, `.github/workflows/companions.yml`, `site/script.js`, `browser/userscripts/jamly.user.js`, `PROCESSING-PIPELINE.md`, `README.md`).

### Changed
- **Two-row companion player layout.** Move the active track title/subtitle, stem toggles, and close button into a header row above the audio transport and widened speed slider, giving both the scrubber and speed bar significantly more room (`src/jamly/web/index.html`, `src/jamly/web/app.css`).
- **Simplification, modularization, queue performance, and CI hardening.** Split `QueueStatusHandler` into `src/jamly/http_handler.py` with a route table, unified `_media_type()` header parsing, and Range-capable file streaming; unified `audio.mix_pair`, centralized audio format, stable-ID, and local-extension validation, decomposed high-complexity helpers across `run.py`, `audio.py`, `normalize.py`, `config.py`, `watch_queue.py`, `http_handler.py`, `server.py`, and `core.py`, and centralized `API_VERSION`; made `WatchQueue` copy-on-write and removed legacy residue (`done` list writes, `mark_done`/`is_complete`/`count_pending`/`default_queue_path`/`normalize_inputs`, `ItemResult.drums_path`/`nodrums_path`, `/items`, `ac_server*`, unreachable preference branch, unused `WatchQueue.PENDING`/`DONE`); shared model/stem selection helpers between the companion UI and popup; imported `jamly.__version__` and `sndfile_library` in `tools/Jamly.spec`; made `tools/dev-check.py` fail fast, dropped duplicate `unittest` runs, invoked it directly from `ci.yml`, pinned all GitHub Actions to commit SHAs, and checksum-pinned `wingetcreate.exe`.

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
