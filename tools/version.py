"""Verify or update lockstep release versions across Python, browser, and desktop metadata."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")

INIT_PY = ROOT / "src" / "jamly" / "__init__.py"
PYPROJECT_TOML = ROOT / "pyproject.toml"
UV_LOCK = ROOT / "uv.lock"
MANIFEST_JSON = ROOT / "browser" / "extension" / "manifest.json"
USERSCRIPT_JS = ROOT / "browser" / "userscripts" / "jamly.user.js"
JAMLY_SPEC = ROOT / "tools" / "Jamly.spec"


def validate_semver(version: str) -> str:
    value = version.strip()
    if not SEMVER_RE.match(value):
        raise ValueError("Version must be strict MAJOR.MINOR.PATCH (e.g. 0.1.0), got %r" % version)
    return value


def read_versions(root: Path = ROOT) -> dict[str, str]:
    init_text = (root / "src" / "jamly" / "__init__.py").read_text(encoding="utf-8")
    init_match = re.search(r'^__version__\s*=\s*"([^"]+)"', init_text, re.MULTILINE)
    if not init_match:
        raise ValueError("Missing __version__ in src/jamly/__init__.py")

    pyproject_text = (root / "pyproject.toml").read_text(encoding="utf-8")
    pyproject_match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject_text, re.MULTILINE)
    if not pyproject_match:
        raise ValueError("Missing version in pyproject.toml")

    uv_lock_text = (root / "uv.lock").read_text(encoding="utf-8")
    uv_match = re.search(
        r'name\s*=\s*"jamly-audio-sep"\r?\nversion\s*=\s*"([^"]+)"',
        uv_lock_text,
    )
    if not uv_match:
        raise ValueError("Missing jamly-audio-sep version in uv.lock")

    manifest_data = json.loads((root / "browser" / "extension" / "manifest.json").read_text(encoding="utf-8"))
    manifest_version = manifest_data.get("version")
    if not isinstance(manifest_version, str):
        raise ValueError("Missing version in browser/extension/manifest.json")

    userscript_text = (root / "browser" / "userscripts" / "jamly.user.js").read_text(encoding="utf-8")
    userscript_match = re.search(r"^//\s*@version\s+(\S+)", userscript_text, re.MULTILINE)
    if not userscript_match:
        raise ValueError("Missing @version in browser/userscripts/jamly.user.js")

    spec_text = (root / "tools" / "Jamly.spec").read_text(encoding="utf-8")
    short_match = re.search(r'"CFBundleShortVersionString":\s*"([^"]+)"', spec_text)
    bundle_match = re.search(r'"CFBundleVersion":\s*"([^"]+)"', spec_text)
    if not short_match or not bundle_match:
        raise ValueError("Missing CFBundle version strings in tools/Jamly.spec")

    return {
        "src/jamly/__init__.py": init_match.group(1),
        "pyproject.toml": pyproject_match.group(1),
        "uv.lock": uv_match.group(1),
        "browser/extension/manifest.json": manifest_version,
        "browser/userscripts/jamly.user.js": userscript_match.group(1),
        "tools/Jamly.spec#CFBundleShortVersionString": short_match.group(1),
        "tools/Jamly.spec#CFBundleVersion": bundle_match.group(1),
    }


def check_versions(root: Path = ROOT, tag: str | None = None) -> str:
    versions = read_versions(root)
    distinct = set(versions.values())
    if len(distinct) != 1:
        details = ", ".join("%s=%s" % item for item in sorted(versions.items()))
        raise ValueError("Version mismatch across files: " + details)
    version = validate_semver(next(iter(distinct)))
    if tag is not None:
        cleaned_tag = tag.removeprefix("refs/tags/")
        expected = "v" + version
        if cleaned_tag != expected:
            raise ValueError("Release tag %r does not match package version %r (expected %r)"
                             % (cleaned_tag, version, expected))
    return version


def set_version(new_version: str, root: Path = ROOT) -> str:
    version = validate_semver(new_version)

    init_path = root / "src" / "jamly" / "__init__.py"
    init_text = re.sub(
        r'^(__version__\s*=\s*)"[^"]+"',
        r'\g<1>"' + version + '"',
        init_path.read_text(encoding="utf-8"),
        count=1,
        flags=re.MULTILINE,
    )
    init_path.write_text(init_text, encoding="utf-8")

    pyproject_path = root / "pyproject.toml"
    pyproject_text = re.sub(
        r'^(version\s*=\s*)"[^"]+"',
        r'\g<1>"' + version + '"',
        pyproject_path.read_text(encoding="utf-8"),
        count=1,
        flags=re.MULTILINE,
    )
    pyproject_path.write_text(pyproject_text, encoding="utf-8")

    uv_path = root / "uv.lock"
    uv_text = re.sub(
        r'(name\s*=\s*"jamly-audio-sep"\r?\nversion\s*=\s*)"[^"]+"',
        r'\g<1>"' + version + '"',
        uv_path.read_text(encoding="utf-8"),
        count=1,
    )
    uv_path.write_text(uv_text, encoding="utf-8")

    manifest_path = root / "browser" / "extension" / "manifest.json"
    manifest_text = re.sub(
        r'^(\s*"version":\s*)"[^"]+"',
        r'\g<1>"' + version + '"',
        manifest_path.read_text(encoding="utf-8"),
        count=1,
        flags=re.MULTILINE,
    )
    manifest_path.write_text(manifest_text, encoding="utf-8")

    userscript_path = root / "browser" / "userscripts" / "jamly.user.js"
    userscript_text = re.sub(
        r"^(//\s*@version\s+)\S+",
        r"\g<1>" + version,
        userscript_path.read_text(encoding="utf-8"),
        count=1,
        flags=re.MULTILINE,
    )
    userscript_path.write_text(userscript_text, encoding="utf-8")

    spec_path = root / "tools" / "Jamly.spec"
    spec_text = spec_path.read_text(encoding="utf-8")
    spec_text = re.sub(
        r'("CFBundleShortVersionString":\s*)"[^"]+"',
        r'\g<1>"' + version + '"',
        spec_text,
        count=1,
    )
    spec_text = re.sub(
        r'("CFBundleVersion":\s*)"[^"]+"',
        r'\g<1>"' + version + '"',
        spec_text,
        count=1,
    )
    spec_path.write_text(spec_text, encoding="utf-8")

    return check_versions(root)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", help="Verify all versioned files match")
    group.add_argument("--set", metavar="X.Y.Z", help="Set version across all tracked files")
    parser.add_argument("--tag", default=None, help="Verify that a Git tag (e.g. v0.1.0) matches the version")
    args = parser.parse_args(argv)
    try:
        if args.set:
            version = set_version(args.set)
            if args.tag:
                check_versions(tag=args.tag)
        else:
            version = check_versions(tag=args.tag)
    except ValueError as exc:
        print("version error: %s" % exc, file=sys.stderr)
        return 1
    print(version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
