"""Render a Homebrew Cask definition for a tagged Jamly release."""
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
    write_package_zip,
)


def render_homebrew_cask(
    version: str,
    sha256_arm64: str,
    sha256_x86_64: str,
    output_dir: Path,
    *,
    template_path: Path | None = None,
    zip_path: Path | None = None,
) -> Path:
    ver = normalize_semver(version)
    arm64_digest = normalize_sha256(sha256_arm64, uppercase=False)
    x86_64_digest = normalize_sha256(sha256_x86_64, uppercase=False)

    template = template_path or (ROOT / "packaging" / "homebrew" / "Casks" / "jamly.rb")
    if not template.is_file():
        raise ValueError("Missing Homebrew cask template: %s" % template)

    casks_dir = output_dir / "Casks"
    rendered = render_template_files(
        [template],
        casks_dir,
        {
            "__VERSION__": ver,
            "__SHA256_ARM64__": arm64_digest,
            "__SHA256_X86_64__": x86_64_digest,
        },
    )
    if zip_path is not None:
        write_package_zip(rendered, output_dir, zip_path)
    return rendered[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="Release version (e.g. 0.1.0)")
    parser.add_argument("--sha256-arm64", required=True, help="SHA-256 hex digest of the macOS arm64 zip")
    parser.add_argument("--sha256-x86_64", required=True, help="SHA-256 hex digest of the macOS x86_64 zip")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "dist" / "homebrew",
        help="Output directory for rendered Homebrew tap files",
    )
    parser.add_argument("--zip-path", type=Path, default=None, help="Optional path for homebrew-tap zip")
    args = parser.parse_args(argv)
    cask_path = render_homebrew_cask(
        args.version,
        args.sha256_arm64,
        args.sha256_x86_64,
        args.output_dir,
        zip_path=args.zip_path,
    )
    print(cask_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
