# Current design

## Product boundary

Jamly is a local exporter with a companion interface and an optional browser extension.
It accepts individual media URLs or local audio files and produces reusable stem pairs (`drums`/`no_drums`
by default, or `vocals`/`no_vocals` for karaoke, `bass`, `guitar`, `piano`, `other`, or `all` individual stems).
The companion works without an extension; the extension is a convenience for submitting the current page.
A static showcase website lives under `site/` for GitHub Pages and is excluded from Python wheels,
browser extension exports, and desktop companion archives.

A lightweight practice player with synchronized playback, A-B loops, speed adjustment and a count-in
is a possible later direction, **not implemented or scheduled**. Browser-only inference is descoped.
Cloud accounts, a full practice platform, additional engines and marketplace publication are not part
of this release.

## Pipeline

`run.process_one` validates one media item (fetching remote metadata and audio through the installed
interpreter's yt-dlp, or probing local audio files directly via ffprobe/soundfile without network
access), invokes an isolated Demucs worker, and publishes the requested stem exports. Collections,
remote `file://` hosts, and active live streams are rejected before download. Deno is resolved from
the same Python environment.

`demucs_worker` uses the Demucs API, emits chunk-level progress percentages, and writes its raw
estimates as float32 WAV with soundfile. There is no per-stem rescaling or clamping. `models` defines
three presets: Balanced (`htdemucs`, default), Higher quality (`htdemucs_ft`), and 6-stem
(`htdemucs_6s`). All three use the same pipeline and output contract. `htdemucs_ft` runs the
fine-tuned model bag and takes approximately four times longer; `htdemucs_6s` separates six sources
(`drums`, `bass`, `other`, `vocals`, `guitar`, `piano`) so guitar/piano targets can be isolated and
any complement track sums the remaining five stems. Model and stem choices are captured in each
job, survive retries and are recorded in its manifest.

`audio` validates matching frame count, sample rate and channels, then sums every complement stem
for the selected target (`--stem`, defaulting to `drums`, or `vocals` for karaoke, `bass`, `guitar`,
`piano`, `other`, or `all` individual stems).
It processes these files in blocks, rejects non-finite samples, and applies one shared gain to
all final outputs. WAV remains float32. MP3 uses 320 kbps encoding and a decoded, 4x-oversampled
peak check targeting -1 dBFS; any additional attenuation is shared and re-encoded from the raw stems.

## Output transaction

Exported audio files live directly in `<output>/` (`Artist - Song [id]_<stem>.<ext>`, with ` [<model>]`
appended when a non-default model preset is selected), while internal manifests (`metadata.json`,
including a generation id and SHA-256 checksums) live under `<output>/.jamly/<stable-id>/<model>/[<stem>/]<format>/`.

A per-track/model/format lock prevents overlapping publishers. All output files are completed in a
sibling staging directory under `.jamly/`, then atomically published into `<output>/` alongside the
manifest swap. Replacement backs up any superseded manifest and audio files before swapping the new
generation into place; a failed swap restores the backup, and an interrupted swap is recovered before
the next attempt.

Readers never receive a mixture of files from a partially published directory. During replacement
there can be a short interval with no directory. Authenticated downloads check the generation
under the same lock and return 409 if the result is being replaced or superseded.

Existing outputs are reusable only when their processing version, identity, model, format and checksums
match. Cache lookup precedes network/model work. Metadata and audio are published together. Selecting
another preset neither reuses nor replaces the first preset's files. Older layouts remain untouched.

## Queue and lifecycle

The CLI or backend holds an exclusive process lock for the lifetime of queue ownership. Concurrent
standalone CLI/backend writers are refused rather than silently overwriting one another. Revision
checks also reject stale direct saves.

Job records retain the stable id, source URL, options, status, attempts, error and output generation.
States are `pending`, `running`, `done`, `skipped`, `error`, and `cancelled`. A legacy JSON done-set
is readable, but new error records are not treated as successful completions.

Explicit submission requeues terminal jobs and captures the requested settings. Pending/running
duplicates are ignored. Server errors require explicit retry; interrupted running jobs resume after
restart. CLI invocation without inputs retries failed jobs once, not in an infinite loop.

The backend persists `running` before starting expensive work. Progress stages remain in memory.
Subprocesses run in owned process groups; cancellation terminates the owned tree, cleans staging
files and returns interrupted work to the queue. Shutdown joins the worker before releasing ownership.
Queue persistence errors are exposed, not converted into successful acceptance.

## Companion, local API and browser

API version 3 uses finite JSON snapshots. The backend owns all durable state. The local companion page
polls while visible; the extension service worker handles requests on demand and its popup polls while
open. Neither browser page owns the inference process.

`companion` serializes launches, checks for the authenticated backend associated with its queue,
and reconnects or starts a separate process. The backend publishes a private instance descriptor.
Before sending its pairing credential, the launcher verifies a fresh HMAC challenge bound to the
backend address and queue. A stale port occupied by another application cannot collect the token.
Closing a page or the short-lived launcher does not stop jobs. Quit invokes orderly shutdown;
SIGTERM also cancels processing and preserves unfinished work for restart.

Saved UI preferences are validated and atomically persisted separately from the queue. They override
installation configuration; explicit backend CLI arguments override saved preferences at startup.
An accepted job retains its original settings even if the defaults change.

A private per-user token or a companion session is required for jobs, status and downloads. The unauthenticated health probe
contains no paths or credentials. Origin and Host checks are independent of authentication. Result
URLs refer to known completed artifacts, never arbitrary client-supplied filesystem paths.

Automatic pairing delivers a two-minute, single-use launch ticket in a private local HTML file.
The browser submits it in a POST body, receives a 24-hour session token, and stores that token in
tab-scoped sessionStorage. The launch file is removed after use; stale tickets cannot be replayed.
No credential is put in a URL, command line, log or cookie. Static UI assets expose no secrets.
Manual pairing remains available for advanced clients.

Native web downloads POST the session token in a hidden form so large files do not need to be held
in a JavaScript Blob. UI pages use a same-origin referrer policy to retain the Origin header for these
POSTs, a restrictive CSP, and frame blocking. Only the ticket-exchange endpoint permits a null
file-origin; other browser origins remain restricted. Desktop actions are limited to loopback servers.

The extension renders server text with DOM text APIs, shows failed HTTP requests, and uses the
browser download manager with an Authorization header. Tokens are not placed in result URLs.
The optional userscript is a limited status/enqueue helper, not a second full application.

## Installed environment

Companion, CLI, backend, doctor and service commands share configuration helpers. Defaults use per-user
application data and `~/Music/jamly`, not whichever directory launched the service.

`service` renders OS-specific registration definitions around the exact installed Python executable.
Windows uses a per-user logon task, macOS a LaunchAgent, and Linux a user systemd unit. Browser assets
are included in the wheel and can be copied to a stable directory for unpacked loading.

Development and CI use `uv.lock`. Fast tests exercise error/recovery contracts without downloading a
model; explicit integration checks exercise the installed pipeline and Chrome with generated audio.

## Desktop packages

`runtime` centralizes executable, asset and tool discovery. Source installs invoke interpreter-local
modules. Frozen installs dispatch a small allowlist through the bundled console runtime rather than
treating the GUI executable as a Python interpreter. The GUI launcher and worker share a PyInstaller
onedir runtime; there is no onefile extraction or source-checkout dependency at launch.

`tools/native_audio.py` builds ffmpeg, ffprobe and a minimal shared libsndfile from checksum-pinned
sources. FFmpeg links the pinned LAME and OpenSSL statically, with GPL/nonfree features disabled.
The source-built libsndfile replaces the soundfile wheel's opaque codec bundle and handles PCM;
encoded input decoding uses Demucs' sphn/FFmpeg path, and MP3 output uses FFmpeg. Corresponding sources,
build instructions, native configuration, package notices and checksums accompany the desktop artifacts.
The project code remains MIT; bundled components retain their own terms.

The locked `bundle` dependency group provides PyInstaller and the Python-notice archive reader.
Builds use uv-managed Python and collect its native dependency notices from the matching,
checksum-verified python-build-standalone release. Native dependencies and Deno are included,
and all state/exports stay outside the application. Model weights download into their upstream user
caches on first use and are not included in the application. Updates replace the application manually.

The packaging workflow builds native archives on macOS ARM64/Intel, Windows x64, and Linux x64.
Linux builds target Ubuntu 22.04's glibc baseline. The package check extracts into a new path containing
spaces, clears PATH and Python environment overrides, checks runtime/assets/pairing, and processes
generated compressed audio with both real model presets. macOS binaries are also checked for external
non-system library references.

Artifacts are unsigned previews, not a signed/notarized public distribution. Release signing credentials,
store submission and an automatic updater are not configured.

## Versioning and releases

Release versions follow Semantic Versioning (`MAJOR.MINOR.PATCH`, starting at `0.1.0`). Pre-`1.0`
releases increment `MINOR` for new features, presets, or contract changes and `PATCH` for bug fixes
and dependency updates. `tools/version.py` keeps `src/jamly/__init__.py`, `pyproject.toml`, `uv.lock`,
`browser/extension/manifest.json`, `browser/userscripts/jamly.user.js`, and `tools/Jamly.spec` in
strict lockstep; CI and tests enforce that all six match. Output cache schema (`PROCESSING_VERSION`)
and loopback control protocol (`api_version`) are versioned independently from the release number.

To cut a release, run `uv run --locked python tools/version.py --set X.Y.Z`, run the test and lint
suites, commit on `main`, and push an annotated `vX.Y.Z` tag. Tag pushes trigger `companions.yml`,
which verifies the tag against `tools/version.py`, builds and smoke-tests the four desktop companion
archives alongside their corresponding native source archives, packages the browser extension and
userscript, writes `SHA256SUMS.txt`, and publishes the GitHub Release.
