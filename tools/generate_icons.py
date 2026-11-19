"""Generate desktop application icons (.ico and .icns) from site/jamly-mark.svg."""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SVG_PATH = ROOT / "site" / "jamly-mark.svg"
ICO_PATH = ROOT / "tools" / "jamly.ico"
ICNS_PATH = ROOT / "tools" / "jamly.icns"

ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
ICNS_CHUNKS = (
    (b"icp4", 16),
    (b"ic11", 32),
    (b"icp5", 32),
    (b"ic12", 64),
    (b"icp6", 64),
    (b"ic07", 128),
    (b"ic13", 256),
    (b"ic08", 256),
    (b"ic14", 512),
    (b"ic09", 512),
    (b"ic10", 1024),
)


def find_browser() -> str:
    explicit = os.environ.get("JAMLY_BROWSER")
    if explicit:
        return explicit
    candidates = [
        shutil.which("msedge"),
        shutil.which("chrome"),
        shutil.which("chromium"),
        shutil.which("google-chrome"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise RuntimeError("No Chromium/Edge executable found to rasterize site/jamly-mark.svg")


def rasterize_svg(svg_path: Path) -> dict[str, dict]:
    svg_b64 = base64.b64encode(svg_path.read_bytes()).decode("ascii")
    ico_sizes = json.dumps(list(ICO_SIZES))
    icns_sizes = json.dumps(sorted({size for _tag, size in ICNS_CHUNKS}))
    html = f"""<!doctype html>
<html>
<body>
<pre id="out"></pre>
<script>
const icoSizes = {ico_sizes};
const icnsSizes = {icns_sizes};
const img = new Image();
img.onload = () => {{
  const full = {{}};
  for (const s of icoSizes) {{
    const c = document.createElement("canvas");
    c.width = s;
    c.height = s;
    const ctx = c.getContext("2d");
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(img, 0, 0, s, s);
    const png = c.toDataURL("image/png").split(",", 2)[1];
    const rgba = ctx.getImageData(0, 0, s, s).data;
    let bin = "";
    for (let i = 0; i < rgba.length; i++) bin += String.fromCharCode(rgba[i]);
    full[String(s)] = {{ png, rgba: btoa(bin) }};
  }}
  const mac = {{}};
  for (const s of icnsSizes) {{
    const c = document.createElement("canvas");
    c.width = s;
    c.height = s;
    const ctx = c.getContext("2d");
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";
    const pad = s * (100 / 1024);
    const inner = s - 2 * pad;
    ctx.drawImage(img, pad, pad, inner, inner);
    mac[String(s)] = c.toDataURL("image/png").split(",", 2)[1];
  }}
  document.getElementById("out").textContent = JSON.stringify({{ full, mac }});
}};
img.src = "data:image/svg+xml;base64,{svg_b64}";
</script>
</body>
</html>
"""
    browser = find_browser()
    with tempfile.TemporaryDirectory(prefix="jamly-icons-") as tmp:
        html_path = Path(tmp) / "render.html"
        html_path.write_text(html, encoding="utf-8")
        proc = subprocess.run(
            [
                browser,
                "--headless",
                "--disable-gpu",
                "--no-sandbox",
                "--virtual-time-budget=2000",
                "--dump-dom",
                html_path.as_uri(),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    marker = '<pre id="out">'
    start = proc.stdout.find(marker)
    end = proc.stdout.find("</pre>", start)
    if start == -1 or end == -1:
        raise RuntimeError("Headless browser did not produce rasterized icon payloads")
    return json.loads(proc.stdout[start + len(marker):end])


def rgba_to_ico_dib(size: int, rgba: bytes) -> bytes:
    if len(rgba) != size * size * 4:
        raise ValueError(f"Expected {size * size * 4} RGBA bytes, got {len(rgba)}")
    and_stride = ((size + 31) // 32) * 4
    xor_rows = bytearray()
    and_rows = bytearray()
    for y in range(size - 1, -1, -1):
        row_start = y * size * 4
        and_row = bytearray(and_stride)
        for x in range(size):
            px = row_start + x * 4
            r, g, b, a = rgba[px], rgba[px + 1], rgba[px + 2], rgba[px + 3]
            xor_rows.extend((b, g, r, a))
            if a == 0:
                and_row[x // 8] |= 1 << (7 - (x % 8))
        and_rows.extend(and_row)
    header = struct.pack(
        "<IIIHHIIIIII",
        40,  # biSize
        size,  # biWidth
        size * 2,  # biHeight (XOR + AND)
        1,  # biPlanes
        32,  # biBitCount
        0,  # biCompression (BI_RGB)
        len(xor_rows) + len(and_rows),  # biSizeImage
        0,
        0,
        0,
        0,
    )
    return header + bytes(xor_rows) + bytes(and_rows)


def build_ico(full_rasters: dict[str, dict]) -> bytes:
    entries: list[tuple[int, bytes]] = []
    for size in ICO_SIZES:
        item = full_rasters[str(size)]
        if size >= 256:
            payload = base64.b64decode(item["png"])
        else:
            payload = rgba_to_ico_dib(size, base64.b64decode(item["rgba"]))
        entries.append((size, payload))

    header = struct.pack("<HHH", 0, 1, len(entries))
    offset = 6 + 16 * len(entries)
    directory = bytearray()
    blobs = bytearray()
    for size, payload in entries:
        dim = 0 if size >= 256 else size
        directory.extend(struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(payload), offset))
        blobs.extend(payload)
        offset += len(payload)
    return header + bytes(directory) + bytes(blobs)


def build_icns(mac_rasters: dict[str, str]) -> bytes:
    chunks = bytearray()
    for ostype, size in ICNS_CHUNKS:
        png = base64.b64decode(mac_rasters[str(size)])
        chunks.extend(struct.pack(">4sI", ostype, 8 + len(png)))
        chunks.extend(png)
    return struct.pack(">4sI", b"icns", 8 + len(chunks)) + bytes(chunks)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--svg", type=Path, default=SVG_PATH)
    parser.add_argument("--ico", type=Path, default=ICO_PATH)
    parser.add_argument("--icns", type=Path, default=ICNS_PATH)
    args = parser.parse_args(argv)

    rasters = rasterize_svg(args.svg)
    args.ico.write_bytes(build_ico(rasters["full"]))
    args.icns.write_bytes(build_icns(rasters["mac"]))
    print(f"Wrote {args.ico} ({args.ico.stat().st_size} bytes)")
    print(f"Wrote {args.icns} ({args.icns.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
