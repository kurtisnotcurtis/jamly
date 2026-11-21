#!/usr/bin/env python3
"""Coordinate a Jamly release on top of ``tools/version.py`` lockstep metadata.

The changelog keeps a conventional `Keep a Changelog`-style ``## [Unreleased]``
block at the top where changes accumulate as they land. A release is cut in one
atomic step: every versioned file (Python, browser extension, userscript,
desktop bundle) is bumped to the new version **and** the ``[Unreleased]`` block
is dated into its own section while a fresh empty ``[Unreleased]`` opens on top.

Commands
--------
``python tools/release.py new X.Y.Z``
    Cut the release: set every file to ``X.Y.Z``, rename the top ``[Unreleased]``
    section to ``## [X.Y.Z] - <today>``, and open a fresh empty ``[Unreleased]``.
    Prints the exact commit + annotated-tag commands (tag must read ``vX.Y.Z``).

``python tools/release.py bump major|minor|patch``
    Start development on the next version: increment the lockstep version by the
    given Semantic-Version part and open a fresh empty ``[Unreleased]`` section so
    changes can accumulate under that number before it is cut.

Both commands leave the working tree staged-ready; they only touch files, not Git.
"""
from __future__ import annotations

import argparse
import datetime
import re
import sys
from pathlib import Path

import version

ROOT = Path(__file__).resolve().parents[1]
CHANGELOG = ROOT / "CHANGELOG.md"
UNRELEASED_RE = re.compile(r"^##\s*\[Unreleased\]\s*$", re.MULTILINE)
DATED_SECTION_RE = re.compile(r"^##\s*\[")
PLACEHOLDER = "_Nothing yet._"


def _increment(current: str, kind: str) -> str:
    parts = [int(p) for p in current.split(".")]
    if len(parts) != 3:
        raise ValueError("Version %r is not MAJOR.MINOR.PATCH" % current)
    major, minor, patch = parts
    if kind == "major":
        major += 1
        minor = patch = 0
    elif kind == "minor":
        minor += 1
        patch = 0
    elif kind == "patch":
        patch += 1
    else:
        raise ValueError("Increment must be one of major|minor|patch, got %r" % kind)
    return "%d.%d.%d" % (major, minor, patch)


def _ensure_unreleased(text: str) -> str:
    if UNRELEASED_RE.search(text):
        return text
    lines = text.splitlines(keepends=True)
    block = "## [Unreleased]\n\n%s\n\n" % PLACEHOLDER
    idx = next((i for i, line in enumerate(lines) if DATED_SECTION_RE.match(line)), None)
    if idx is None:
        return text + block
    lines.insert(idx, block)
    return "".join(lines)


def _release_section(text: str, version_str: str) -> str:
    """Move the top ``[Unreleased]`` block under a dated header and open a new one."""
    date = datetime.date.today().isoformat()
    lines = text.splitlines(keepends=True)
    idx = next((i for i, line in enumerate(lines) if UNRELEASED_RE.match(line)), None)
    if idx is None:
        lines = _ensure_unreleased("".join(lines)).splitlines(keepends=True)
        idx = next(i for i, line in enumerate(lines) if UNRELEASED_RE.match(line))

    end = idx + 1
    while end < len(lines) and not DATED_SECTION_RE.match(lines[end]):
        end += 1
    body = "".join(lines[idx + 1:end]).strip("\n")
    carried = [] if body in ("", PLACEHOLDER) else list(lines[idx + 1:end])
    while carried and carried[0].strip() == "":
        carried.pop(0)

    dated_line = "## [%s] - %s\n" % (version_str, date)
    new_lines = [dated_line, "\n"]
    if carried:
        new_lines += carried + ["\n"]
    new_lines += ["## [Unreleased]\n\n%s\n" % PLACEHOLDER, "\n"]
    new_lines += lines[end:]
    return "".join(new_lines)


def _bump_change_log(text: str) -> str:
    """Ensure a fresh ``[Unreleased]`` block exists at the top for new changes."""
    return _ensure_unreleased(text)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    new_parser = subparsers.add_parser("new", help="Cut a release at X.Y.Z")
    new_parser.add_argument("version", metavar="X.Y.Z", help="Release version to cut")

    bump_parser = subparsers.add_parser(
        "bump", help="Start dev on the next version (increment + open Unreleased)"
    )
    bump_parser.add_argument(
        "kind",
        choices=["major", "minor", "patch"],
        help="Semantic-Version part to increment",
    )
    args = parser.parse_args(argv)

    try:
        if args.command == "new":
            return _do_new(args.version)
        if args.command == "bump":
            return _do_bump(args.kind)
    except ValueError as exc:
        print("release error: %s" % exc, file=sys.stderr)
        return 1
    return 0


def _do_new(target: str) -> int:
    version_str = version.validate_semver(target)
    previous = version.check_versions()  # proves lockstep integrity before mutating
    version.set_version(version_str)

    changelog_text = CHANGELOG.read_text(encoding="utf-8") if CHANGELOG.exists() else ""
    changelog_text = _release_section(changelog_text, version_str)
    CHANGELOG.write_text(changelog_text, encoding="utf-8")

    print("Released %s (previous lockstep was %s)." % (version_str, previous))
    print("Bumped: python tools/version.py --set %s  (already applied)" % version_str)
    print("CHANGELOG.md dated the [Unreleased] section and opened a new one on top.")
    tag = "v%s" % version_str
    print()
    print("Next steps:")
    print("  git add -A")
    print('  git commit -m "Release %s"' % tag.lstrip("v"))
    print("  git tag -a %s -m 'Release %s' --signing-key default" % (tag, tag.lstrip("v")))
    print("  git push origin main --tags")
    print("  python tools/version.py --check --tag %s" % tag)
    return 0


def _do_bump(kind: str) -> int:
    current = version.check_versions()
    target = _increment(current, kind)
    version.set_version(target)

    changelog_text = CHANGELOG.read_text(encoding="utf-8") if CHANGELOG.exists() else ""
    CHANGELOG.write_text(_bump_change_log(changelog_text), encoding="utf-8")

    print("Bumped %s -> %s (%s increment)." % (current, target, kind))
    print("CHANGELOG.md now opens a fresh [Unreleased] section for these changes.")
    print()
    print("Next steps:")
    print("  git add -A")
    print('  git commit -m "Bump version to %s"' % target)
    print("  python tools/version.py --check")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
