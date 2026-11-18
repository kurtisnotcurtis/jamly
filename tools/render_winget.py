"""Render WinGet multi-file manifests for a tagged Jamly release."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ID = "kurtisnotcurtis.Jamly"
SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def render_winget_manifests(
    version: str,
    sha256_amd64: str,
    output_dir: Path,
    *,
    release_date: str | None = None,
    template_dir: Path | None = None,
    zip_path: Path | None = None,
) -> Path:
    ver = version.strip().removeprefix("v")
    if not SEMVER_RE.match(ver):
        raise ValueError("Invalid SemVer version: %r" % version)
    digest = sha256_amd64.strip()
    if not SHA256_RE.match(digest):
        raise ValueError("Invalid SHA-256 hex digest: %r" % sha256_amd64)
    digest_upper = digest.upper()
    date_value = (release_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")).strip()
    if not DATE_RE.match(date_value):
        raise ValueError("Invalid release date (expected YYYY-MM-DD): %r" % release_date)

    templates = template_dir or (ROOT / "packaging" / "winget" / PACKAGE_ID)
    yaml_files = sorted(templates.glob("*.yaml"))
    if len(yaml_files) != 3:
        raise ValueError("Expected 3 WinGet manifest templates in %s" % templates)

    manifest_dir = output_dir / PACKAGE_ID / ver
    manifest_dir.mkdir(parents=True, exist_ok=True)
    rendered_paths: list[Path] = []
    for template in yaml_files:
        content = template.read_text(encoding="utf-8")
        content = content.replace("__VERSION__", ver)
        content = content.replace("__RELEASE_DATE__", date_value)
        content = content.replace("__SHA256_AMD64__", digest_upper)
        target = manifest_dir / template.name
        target.write_text(content, encoding="utf-8", newline="\n")
        rendered_paths.append(target)

    if zip_path is not None:
        zip_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path in rendered_paths:
                arcname = f"{PACKAGE_ID}/{ver}/{path.name}"
                zf.write(path, arcname)

    return manifest_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="Release version (e.g. 0.1.0)")
    parser.add_argument("--sha256-amd64", required=True, help="SHA-256 hex digest of the Windows x64 zip")
    parser.add_argument("--release-date", default=None, help="UTC release date (YYYY-MM-DD)")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "dist" / "winget",
        help="Output directory for rendered manifests",
    )
    parser.add_argument("--zip-path", type=Path, default=None, help="Optional path for winget-manifests zip")
    args = parser.parse_args(argv)
    manifest_dir = render_winget_manifests(
        args.version,
        args.sha256_amd64,
        args.output_dir,
        release_date=args.release_date,
        zip_path=args.zip_path,
    )
    print(manifest_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
