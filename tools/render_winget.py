"""Render WinGet multi-file manifests for a tagged Jamly release."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import re
import sys

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from package_render import (  # noqa: E402
    ROOT,
    normalize_semver,
    normalize_sha256,
    render_template_files,
    write_package_zip,
)

PACKAGE_ID = "kurtisnotcurtis.Jamly"
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
    ver = normalize_semver(version)
    digest_upper = normalize_sha256(sha256_amd64, uppercase=True)
    date_value = (release_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")).strip()
    if not DATE_RE.match(date_value):
        raise ValueError("Invalid release date (expected YYYY-MM-DD): %r" % release_date)

    templates = template_dir or (ROOT / "packaging" / "winget" / PACKAGE_ID)
    yaml_files = sorted(templates.glob("*.yaml"))
    if len(yaml_files) != 3:
        raise ValueError("Expected 3 WinGet manifest templates in %s" % templates)

    manifest_dir = output_dir / PACKAGE_ID / ver
    rendered_paths = render_template_files(
        yaml_files,
        manifest_dir,
        {
            "__VERSION__": ver,
            "__RELEASE_DATE__": date_value,
            "__SHA256_AMD64__": digest_upper,
        },
    )

    if zip_path is not None:
        write_package_zip(rendered_paths, output_dir, zip_path)

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
