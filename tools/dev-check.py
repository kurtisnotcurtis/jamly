#!/usr/bin/env python3
"""Run the whole Jamly CI suite locally before you push or release.

This reproduces ``.github/workflows/ci.yml`` step-for-step so a green run here
means a green GitHub Actions job. It is stdlib-only; it shells out to the same
commands CI runs and fails fast on the first stage that errors.

Usage::

    python tools/dev-check.py                 # full local gate (see flags)
    python tools/dev-check.py --no-build      # skip wheel build + fresh-install smoke
    python tools/dev-check.py --integration   # also run the real-model audio pipeline
    python tools/dev-check.py --browser       # also run the headless-Chrome companion smoke

The two heaviest real-model stages (audio pipeline, Chrome smoke) are opt-in so a
quick local gate stays fast; the pre-push hook enables the audio pipeline for you.
Requires Python 3.11–3.12, [uv](https://docs.astral.sh/uv/) when building a wheel,
Node 24+, and `ffmpeg`/`ffprobe`.
"""
from __future__ import annotations

import argparse
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Ordered stages mirroring .github/workflows/ci.yml. Each entry is a list of argv.
NODE_TESTS = [
    "browser/extension/jamly_client_test.js",
    "browser/extension/jamly_extension_bg_test.js",
    "browser/extension/jamly_popup_test.js",
]


def _have(name: str) -> bool:
    return shutil.which(name) is not None


def _base_interpreter(use_uv: bool) -> list[str]:
    """Interpreter argv for module/file invocations (pytest, unittest, ruff, scripts)."""
    if use_uv and _have("uv"):
        return ["uv", "run", "--locked", "python"]
    return [sys.executable]


def _runner_cmd(use_uv: bool) -> list[str]:
    """Runner prefix for console-script invocations (jamly-doctor); empty without uv."""
    if use_uv and _have("uv"):
        return ["uv", "run", "--locked"]
    return []


def run(argv: list[str], label: str, check: bool = True) -> bool:
    """Run one stage; print a header, the command, and its exit status."""
    print("\n" + "=" * 72)
    print("==> %s\n    $ %s" % (label, shlex.join(argv)))
    try:
        proc = subprocess.run(argv, cwd=str(ROOT), check=False)
    except OSError as exc:  # command missing / not executable
        print("   [skip] could not run stage: %s" % exc, file=sys.stderr)
        return False if check else True
    status = proc.returncode
    print("    [exit %d]" % status)
    return status == 0


def _wheel_smoke(use_uv: bool) -> bool:
    """Fresh-install the built wheel and exercise it, matching CI's job."""
    if not (use_uv and _have("uv")):
        print("\n" + "=" * 72)
        print("==> Wheel build + fresh-install smoke\n    [skip] uv not available")
        return True
    if not run(["uv", "build"], "Build sdist + wheel"):
        return False
    whl = sorted(ROOT.glob("dist/*.whl"))
    if not whl:
        print("    [fail] no wheel produced by `uv build`")
        return False
    # Match CI: --no-sync so uv doesn't re-resolve and overwrite the just-installed wheel.
    runner = ["uv", "run", "--no-sync"]
    installed = run(
        ["uv", "pip", "install", "--no-deps", "--force-reinstall", str(whl[-1])],
        "Install built wheel into a clean environment",
    )
    if not installed:
        return False
    state_dir = tempfile.mkdtemp(prefix="jamly-devcheck-")
    checks = [
        ["jamly-doctor"],
        ["jamly-service", "browser", "--state-dir", state_dir],
        ["python", "-c", "import jamly; assert 'site-packages' in jamly.__file__"],
    ]
    for check in checks:
        if not run(runner + check, "Exercise installed package"):
            return False
    return True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-build",
        action="store_true",
        help="Skip `uv build` + fresh-install wheel smoke (faster local runs)",
    )
    parser.add_argument(
        "--integration",
        action="store_true",
        help="Also run the real-model audio pipeline integration test",
    )
    parser.add_argument(
        "--browser",
        action="store_true",
        help="Also run the headless-Chrome companion smoke (needs Chrome)",
    )
    args = parser.parse_args(argv)

    use_uv = _have("uv")
    base = _base_interpreter(use_uv)
    runner = _runner_cmd(use_uv)
    ok = True

    ok &= run(base + ["tools/version.py", "--check"], "Lockstep version metadata")
    ok &= run(runner + ["jamly-doctor"], "jamly-doctor health check")
    ok &= run(base + ["-m", "pytest", "-q"], "pytest unit suite")
    ok &= run(base + ["-m", "unittest", "discover", "-s", "tests"], "unittest suite")
    ok &= run(base + ["-m", "ruff", "check", "."], "ruff lint")
    node_argv = ["node", "--test"] + [str(ROOT / p) for p in NODE_TESTS]
    if _have("node"):
        ok &= run(node_argv, "Node extension tests")
    else:
        print("\n[skip] Node tests (node not on PATH)")

    if not args.no_build and not _wheel_smoke(use_uv):
        ok = False

    if args.integration:
        have_audio = True
        try:  # numpy + soundfile are the pipeline's real deps
            import soundfile  # noqa: F401
        except Exception as exc:  # pragma: no cover - environment dependent
            print("\n[skip] integration pipeline (soundfile unavailable: %s)" % exc)
            have_audio = False
        if have_audio:
            ok &= run(
                base + ["-m", "unittest", "discover", "-s", "tests", "-p",
                        "integration_pipeline.py"],
                "Real-model audio pipeline (generated WAV)",
            )

    if args.browser:
        if not _have("node"):
            print("\n[skip] browser smoke (node not on PATH)")
        elif not (_have("chrome") or _have("chromium") or environ_chrome()):
            print("\n[skip] browser smoke (no Chrome binary; set CHROME_BIN)")
        else:
            ok &= run(["node", str(ROOT / "tools" / "browser_smoke.mjs")],
                      "Headless-Chrome companion workflow")

    print("\n" + "=" * 72)
    if ok:
        print("All selected stages passed.")
        return 0
    print("One or more stages failed. Fix and re-run before pushing.")
    return 1


def environ_chrome() -> bool:
    import os

    return bool(os.environ.get("CHROME_BIN"))


if __name__ == "__main__":
    raise SystemExit(main())
