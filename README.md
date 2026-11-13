# <img src="site/jamly-mark.svg" alt="" width="34" height="34" align="absmiddle"> Jamly 🎸 🪓 🥁

**[Website & Setup Guide](https://kurtisnotcurtis.github.io/jamly/) · [Download Releases](https://github.com/kurtisnotcurtis/jamly/releases) · [Architecture (DESIGN.md)](DESIGN.md)**

Split any song or video into isolated **drums** and **drumless** practice tracks—or **karaoke** (`vocals` + `no_vocals`), **bass**, **guitar**, **piano**, or **all individual stems**—100% locally on your computer.

- **Practice, karaoke, or transcribe**: Mute the drums (or vocals/bass/guitar) to play or sing along, solo any stem to learn parts note-for-note, or export the full multi-track stem pack.
- **URLs or local audio**: Pass a media URL supported by `yt-dlp` or drop in local `.wav`, `.mp3`, `.flac`, `.m4a`, `.ogg`, or `.opus` files.
- **Phase-accurate & private**: Runs Meta's Hybrid Transformer Demucs v4 models locally on your CPU or GPU with shared peak-safe gain staging—no cloud uploads, subscriptions, or per-minute limits.

> *Note: Stem separation is an estimate and can contain bleed or artifacts depending on the mix; "drumless" is not a guarantee of perfect removal.*

---

## Quick Start

### Option A: Desktop Companion (No Python setup required)

Self-contained archives for **macOS** (Apple Silicon & Intel), **Windows x64**, and **Linux x64** bundle Python, Demucs, `yt-dlp`, Deno, `ffmpeg`, and `ffprobe` out of the box.

1. Download the archive for your OS from [Releases](https://github.com/kurtisnotcurtis/jamly/releases) (or the **Companion packages** GitHub Actions workflow).
2. Launch the companion:
   - **macOS 13+**: Extract the zip, move `Jamly.app` to `Applications`, and open it.
   - **Windows**: Extract the **entire** zip and run `Jamly.exe` (keep all files in the extracted folder together).
   - **Linux (glibc 2.35+)**: Extract the `.tar.gz` and run `./Jamly` inside the extracted folder.
3. Jamly opens your browser to the local companion page automatically:
   - Paste a track URL or local file path—or click **Choose files** to upload audio from your computer.
   - Pick a **Preset** and **Format** (`MP3` or `WAV`), then click **Split drums**.
   - Click **Show files** to open the folder on disk, or **Save a copy** to download directly in your browser.

*(Closing the browser tab does not interrupt running jobs; reopen `Jamly` anytime to reconnect, or click **Quit companion** to stop the background worker. Note: preview builds are unsigned, so macOS Gatekeeper or Windows SmartScreen may prompt on first launch.)*

---

### Option B: CLI & Python Install (via `uv`)

If you prefer the command line or already use Python (3.11 or 3.12), install [uv](https://docs.astral.sh/uv/getting-started/installation/) and `ffmpeg`:

```text
macOS:          brew install ffmpeg
Debian/Ubuntu:  sudo apt install ffmpeg
Windows:        winget install Gyan.FFmpeg
```

Then install Jamly and verify your environment:

```sh
uv tool install --python 3.12 git+https://github.com/kurtisnotcurtis/jamly.git
jamly-doctor
```

From there, you can launch the local web companion or split tracks directly from your terminal:

```sh
# Open the local web companion UI
jamly-companion

# Or split directly from the CLI
jamly -i "https://youtu.be/VIDEO_ID"
jamly -i "https://youtu.be/VIDEO_ID" --stem vocals
jamly -i "/path/to/song.flac" --model htdemucs_ft --audio-format wav
jamly -i "/path/to/song.flac" --model htdemucs_6s --stem guitar
```

---

## Separation Presets & Output Layout

| Preset | CLI Flag | How It Works & Tradeoff |
|---|---|---|
| **Balanced** *(default)* | `--model htdemucs` | Fast 4-stem Hybrid Transformer Demucs; computes `no_drums` by phase-aligned residual subtraction (`mix - drums`). |
| **Higher quality** | `--model htdemucs_ft` | Fine-tuned 4-stem ensemble; cleaner transient isolation, takes **~4× longer** and downloads larger weights on first use. |
| **6-stem (HTDemucs)** | `--model htdemucs_6s` | 6-stem Demucs (`drums`, `bass`, `guitar`, `piano`, `vocals`, `other`); single-pass speed and sums all 5 non-drum stems. |

Model weights download automatically on first use of each preset. Completed exports are saved directly in `~/Music/jamly` by default (with internal cache manifests tucked inside a hidden `.jamly/` folder so your music folder stays clean):

```text
~/Music/jamly/
  Artist - Song [id]_drums.mp3
  Artist - Song [id]_no_drums.mp3
```

Re-running a track with a new preset or format keeps your earlier exports intact alongside the new one.

---

## Guides & Reference

<details>
<summary><strong>Optional Browser Extension & Userscript (One-click from YouTube)</strong></summary>

<br>

The companion works out of the box by pasting URLs or picking local files, but you can also add the unpacked Chrome/Edge extension to send the active tab in one click:

1. In the companion UI, expand **Optional Chrome / Edge extension** and click **Prepare extension**.
2. Open `chrome://extensions` (or `edge://extensions`), enable **Developer mode**, click **Load unpacked**, and select the displayed folder.
3. Open the extension's **Pair / settings** page and paste the displayed backend URL (`http://127.0.0.1:8765`) and private pairing token.
4. Click the Jamly toolbar icon on a YouTube video and select **Use current page** → **Split drums**.

**CLI-only setup**: Run `jamly-server`, then run `jamly-service browser` (prints the unpacked extension folder) and `jamly-service token` (prints the pairing token).

**Optional Userscript**: Run `jamly-service userscript` to export a lightweight YouTube / YouTube Music button for Tampermonkey or Violentmonkey. Use its **Pair Jamly** menu command to set your token.

</details>

<details>
<summary><strong>Full CLI Reference & Caching Rules</strong></summary>

<br>

```sh
jamly -i "https://youtu.be/VIDEO_ID"
jamly -i "/path/to/local_song.wav"
jamly -i URL -i ANOTHER_URL --audio-format wav
jamly -i URL --model htdemucs_ft
jamly -i URL --model htdemucs_6s
jamly -i URL --replace-existing
```

- **Piped inputs**: Newline-separated URLs, IDs, or file paths can be piped via `stdin`. For a bare ID starting with `-`, pass `-i=-VIDEO_ID`.
- **Single-recording policy**: Playlists, search results, multi-item pages, and live/upcoming streams are rejected; pass individual recordings. Cookie/account login is not used.
- **Resume & retry**: Running `jamly` with no `-i` arguments processes any pending items and retries failed CLI jobs once.
- **Smart cache**: Checksum-valid exports are reused without re-downloading or re-separating unless `--replace-existing` is passed.

| Option | Default | Description |
|---|---|---|
| `-o`, `--output-dir DIR` | `~/Music/jamly` | Directory for exported audio files |
| `--stem {drums,vocals,bass,guitar,piano,other,all}` | `drums` | Target stem pair (`<stem>` + `no_<stem>`), such as `vocals` for karaoke, or `all` for every individual stem |
| `--audio-format {mp3,wav}` | `mp3` | `320 kbps` MP3 (checked with 1 dB decoded peak headroom) or `float32` WAV |
| `--model {htdemucs,htdemucs_ft,htdemucs_6s}` | `htdemucs` | Demucs v4 preset (`guitar` and `piano` require `htdemucs_6s`) |
| `--device {auto,cpu,cuda,mps}` | `auto` | Inference device selection |
| `-t`, `--threads N` | `0` | Parallel Demucs worker threads (higher values use more RAM) |
| `--state-dir DIR` / `--queue FILE` | Per-user app data | Override state directory or queue file path |
| `--dry-run` | `false` | Preview what would be processed without modifying state |

</details>

<details>
<summary><strong>Background Login Service & Updating</strong></summary>

<br>

### Start a CLI installation automatically at login

```sh
jamly-service install
jamly-service status
jamly-service restart
jamly-service stop
jamly-service uninstall
```

`jamly-service install` registers a per-user `systemd` unit on Linux, a `LaunchAgent` on macOS, or a user logon task on Windows using absolute paths and your detected `ffmpeg` location. Pass `--dry-run` to preview the generated service definition, or pass pipeline defaults during install:

```sh
jamly-service install --audio-format wav --output-dir "/path/to/music" --port 8765
```

### Updating Jamly

- **Desktop package**: Click **Quit companion**, replace the extracted folder/`Jamly.app` with the new release archive, and launch it again. Your queue, settings, and exports are stored outside the application directory and are preserved.
- **CLI install**:
  ```sh
  uv tool upgrade jamly-audio-sep
  jamly-doctor
  jamly-service browser
  jamly-service restart
  ```
  Reload the unpacked browser extension in `chrome://extensions` after exporting updated assets.

</details>

<details>
<summary><strong>Local HTTP API</strong></summary>

<br>

The local backend speaks **API version 3** on `127.0.0.1`. All job, settings, upload, and download endpoints require `Authorization: Bearer <token>`.

| Endpoint | Purpose |
|---|---|
| `GET /healthz` | Public health & `api_version` check (exposes no paths or credentials) |
| `GET /status` (or `/items`) | Queue counts, active stage + percentage progress, and job records with download URLs |
| `POST /add` | `{"inputs":["URL_OR_PATH"],"model":"htdemucs_ft","stem":"vocals","audio_format":"wav","replace_existing":false}` |
| `POST /app/upload?name=song.wav` | Stream raw audio bytes (`audio/*` or `application/octet-stream`) into private state and return its local path |
| `POST /settings` | Persist default `model`, `stem`, `audio_format`, and `output_dir` |
| `POST /retry/<id>` | `{"replace_existing":false}` |
| `GET /outputs/<id>/<kind>?generation=...` | Download an exported stem (`drums`, `no_drums`, `vocals`, `no_vocals`, etc.) or `metadata` for a completed generation |

</details>

<details>
<summary><strong>Development, Testing & Packaging</strong></summary>

<br>

### Local development & tests

Requires Python 3.11–3.12, [uv](https://docs.astral.sh/uv/), `ffmpeg`/`ffprobe`, and Node 24+:

```sh
uv sync --locked
uv run --locked python tools/version.py --check
uv run --locked pytest
uv run --locked python -m unittest discover -s tests
uv run --locked ruff check .
node --test browser/extension/jamly_client_test.js browser/extension/jamly_extension_bg_test.js browser/extension/jamly_popup_test.js
uv build
```

To run the end-to-end integration and headless Chrome smoke checks with real Demucs separation on synthesized audio:

```sh
uv run --locked python -m unittest discover -s tests -p integration_pipeline.py
node tools/browser_smoke.mjs
```

### Building native desktop archives

Build on the target OS with `uv`-managed Python 3.12 and a C/C++ toolchain (`build-essential pkg-config perl` on Linux, Xcode CLT + `pkg-config` on macOS, or MSYS2 `MINGW64` on Windows):

```sh
export UV_PROJECT_ENVIRONMENT=.venv-bundle
uv sync --locked --group bundle --managed-python --python 3.12
uv run --no-sync python tools/build_companion.py
uv run --no-sync python tools/bundle_smoke.py --real-models
```

See [DESIGN.md](DESIGN.md) for architecture, security boundaries, and release versioning details, and [AGENTS.md](AGENTS.md) for contributor guidelines.

</details>

---

*Only download or process media you are authorized to use, and follow the source site's terms of service.*
