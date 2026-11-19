"""Shared validation, template rendering, and archive helpers for package manifests."""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def normalize_semver(version: str) -> str:
    """Validate a SemVer string (with optional leading 'v') and return X.Y.Z."""
    ver = version.strip().removeprefix("v")
    if not SEMVER_RE.match(ver):
        raise ValueError("Invalid SemVer version: %r" % version)
    return ver


def normalize_sha256(digest: str, *, uppercase: bool = False) -> str:
    """Validate a 64-character hex SHA-256 digest and normalize its case."""
    cleaned = digest.strip()
    if not SHA256_RE.match(cleaned):
        raise ValueError("Invalid SHA-256 hex digest: %r" % digest)
    return cleaned.upper() if uppercase else cleaned.lower()


def sha256_bytes(data: bytes) -> str:
    """Return the lowercase SHA-256 hex digest of an in-memory payload."""
    return hashlib.sha256(data).hexdigest()


def render_template_files(
    templates: list[Path],
    output_dir: Path,
    replacements: dict[str, str],
) -> list[Path]:
    """Render UTF-8 template files with placeholder substitutions and LF line endings."""
    output_dir.mkdir(parents=True, exist_ok=True)
    rendered: list[Path] = []
    for template in templates:
        content = template.read_text(encoding="utf-8")
        for token, value in replacements.items():
            content = content.replace(token, value)
        target = output_dir / template.name
        target.write_text(content, encoding="utf-8", newline="\n")
        rendered.append(target)
    return rendered


def write_package_zip(paths: list[Path], relative_to: Path, zip_path: Path) -> None:
    """Write rendered manifest files into a zip archive rooted at relative_to."""
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in paths:
            zf.write(path, path.relative_to(relative_to).as_posix())
