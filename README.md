# Jamly

Split a YouTube song into isolated **drums** and **drumless** (`no_drums`) practice tracks locally using [Demucs v3](https://github.com/facebookresearch/demucs) (`mdx_extra_q`), `yt-dlp`, and `ffmpeg`.

## Requirements

- Python 3.9 or 3.10
- `ffmpeg` (5.0+) on `PATH`

## Usage

```sh
pip install -e .
jamly -i "https://youtu.be/dQw4w9WgXcQ" -o ./output
```

Outputs `drums.mp3` and `no_drums.mp3` at 320 kbps (`libmp3lame`).
