"""Render AUR PKGBUILD, .SRCINFO, desktop entry, and icon for a tagged Jamly release."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from package_render import (  # noqa: E402
    ROOT,
    normalize_semver,
    normalize_sha256,
    render_template_files,
    sha256_bytes,
    write_package_zip,
)

PACKAGE_NAME = "jamly-bin"


def render_aur_package(
    version: str,
    sha256_x86_64: str,
    output_dir: Path,
    *,
    template_dir: Path | None = None,
    icon_path: Path | None = None,
    zip_path: Path | None = None,
) -> Path:
    ver = normalize_semver(version)
    tarball_digest = normalize_sha256(sha256_x86_64, uppercase=False)
    templates = template_dir or (ROOT / "packaging" / "aur" / PACKAGE_NAME)
    desktop_template = templates / "jamly.desktop"
    pkgbuild_template = templates / "PKGBUILD"
    srcinfo_template = templates / ".SRCINFO"
    for required in (desktop_template, pkgbuild_template, srcinfo_template):
        if not required.is_file():
            raise ValueError("Missing AUR template file: %s" % required)

    source_icon = icon_path or (ROOT / "site" / "jamly-mark.svg")
    if not source_icon.is_file():
        raise ValueError("Missing Jamly SVG icon: %s" % source_icon)

    package_dir = output_dir / PACKAGE_NAME
    package_dir.mkdir(parents=True, exist_ok=True)

    icon_text = source_icon.read_text(encoding="utf-8")
    icon_target = package_dir / "jamly.svg"
    icon_target.write_text(icon_text, encoding="utf-8", newline="\n")
    icon_digest = sha256_bytes(icon_target.read_bytes())

    desktop_paths = render_template_files([desktop_template], package_dir, {})
    desktop_digest = sha256_bytes(desktop_paths[0].read_bytes())

    rendered = render_template_files(
        [pkgbuild_template, srcinfo_template],
        package_dir,
        {
            "__VERSION__": ver,
            "__SHA256_LINUX_TARBALL__": tarball_digest,
            "__SHA256_DESKTOP__": desktop_digest,
            "__SHA256_ICON__": icon_digest,
        },
    )
    all_paths = [*rendered, *desktop_paths, icon_target]
    if zip_path is not None:
        write_package_zip(all_paths, output_dir, zip_path)
    return package_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="Release version (e.g. 0.1.0)")
    parser.add_argument("--sha256-x86_64", required=True, help="SHA-256 hex digest of the Linux x64 tar.gz")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "dist" / "aur",
        help="Output directory for rendered AUR package files",
    )
    parser.add_argument("--zip-path", type=Path, default=None, help="Optional path for aur-jamly-bin zip")
    args = parser.parse_args(argv)
    package_dir = render_aur_package(
        args.version,
        args.sha256_x86_64,
        args.output_dir,
        zip_path=args.zip_path,
    )
    print(package_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
