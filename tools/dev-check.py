#!/usr/bin/env python3
"""Run the Jamly CI suite locally (and from GitHub Actions) before pushing or releasing.

This is the single source of truth for the CI verification stages. It is stdlib-only,
shells out to the project tools, and fails fast on the first stage that errors.

Usage::

    python tools/dev-check.py                 # full local gate (see flags)
    python tools/dev-check.py --no-build      # skip wheel build + fresh-install smoke
    python tools/dev-check.py --e2e           # also run Playwright companion + extension E2E tests
    python tools/dev-check.py --integration   # also run the real-model audio pipeline
    python tools/dev-check.py --browser       # also run the headless-Chrome companion smoke

The two heaviest real-model stages (audio pipeline, Chrome smoke) are opt-in so a
quick local gate stays fast; the pre-push hook enables the audio pipeline for you.
Requires Python 3.11-3.12, [uv](https://docs.astral.sh/uv/) when building a wheel,
Node 24+, and `ffmpeg`/`ffprobe`.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]

NODE_TESTS = [
    "browser/extension/jamly_client_test.js",
    "browser/extension/jamly_extension_bg_test.js",
    "browser/extension/jamly_options_test.js",
    "browser/extension/jamly_popup_test.js",
]


def _have(name: str) -> bool:
    return shutil.which(name) is not None


def _have_chromium() -> bool:
    if os.environ.get("CHROME_BIN") and Path(os.environ["CHROME_BIN"]).is_file():
        return True
    for name in ("google-chrome", "google-chrome-stable", "chrome", "chromium", "chromium-browser",
                 "microsoft-edge", "msedge"):
        if _have(name):
            return True
    candidates = {
        "darwin": (
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ),
        "win32": (
            os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"), r"Google\Chrome\Application\chrome.exe"),
            os.path.join(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"), r"Google\Chrome\Application\chrome.exe"),
            os.path.join(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"), r"Microsoft\Edge\Application\msedge.exe"),
            os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"), r"Microsoft\Edge\Application\msedge.exe"),
        ),
    }.get(sys.platform, ())
    return any(Path(path).is_file() for path in candidates)


def _base_interpreter(use_uv: bool) -> list[str]:
    """Interpreter argv for module/file invocations (pytest, ruff, scripts)."""
    if use_uv and _have("uv"):
        return ["uv", "run", "--locked", "python"]
    return [sys.executable]


def _runner_cmd(use_uv: bool) -> list[str]:
    """Runner prefix for console-script invocations (jamly doctor); empty without uv."""
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


def _current_wheel(dist_dir: Path) -> Path | None:
    """Return the built wheel matching the repository's current version, or newest fallback."""
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        import version as version_tool
        current = version_tool.check_versions(ROOT)
    except Exception:
        current = None
    if current:
        exact = sorted(dist_dir.glob(f"jamly_audio_sep-{current}-*.whl"))
        if exact:
            return exact[-1]
    wheels = sorted(dist_dir.glob("*.whl"), key=lambda p: p.stat().st_mtime)
    return wheels[-1] if wheels else None


def _wheel_smoke(use_uv: bool) -> bool:
    """Fresh-install the built wheel and exercise it."""
    if not (use_uv and _have("uv")):
        print("\n" + "=" * 72)
        print("==> Wheel build + fresh-install smoke\n    [skip] uv not available")
        return True
    dist_dir = ROOT / "dist"
    for stale in list(dist_dir.glob("*.whl")) + list(dist_dir.glob("*.tar.gz")):
        stale.unlink(missing_ok=True)
    if not run(["uv", "build"], "Build sdist + wheel"):
        return False
    if not run([sys.executable, str(ROOT / "tools" / "repro_dist.py")], "Normalize dist artifacts"):
        return False
    whl = _current_wheel(dist_dir)
    if whl is None:
        print("    [fail] no wheel produced by `uv build`")
        return False
    # --no-sync so uv doesn't re-resolve and overwrite the just-installed wheel.
    runner = ["uv", "run", "--no-sync"]
    installed = run(
        ["uv", "pip", "install", "--no-deps", "--force-reinstall", str(whl)],
        "Install built wheel into a clean environment",
    )
    if not installed:
        return False
    with tempfile.TemporaryDirectory(prefix="jamly-devcheck-") as state_dir:
        checks = [
            ["jamly", "doctor"],
            ["jamly", "service", "browser", "--state-dir", state_dir],
            ["python", "-c", "import jamly; assert 'site-packages' in jamly.__file__"],
        ]
        for check in checks:
            if not run(runner + check, "Exercise installed package"):
                return False
    return True


def _fail() -> int:
    print("\n" + "=" * 72)
    print("Stage failed. Fix and re-run before pushing.")
    return 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-build",
        action="store_true",
        help="Skip `uv build` + fresh-install wheel smoke (faster local runs)",
    )
    parser.add_argument(
        "--e2e",
        action="store_true",
        help="Also run the fast Playwright companion + extension E2E tests (needs Chrome/Edge)",
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

    stages = [
        (base + ["tools/version.py", "--check"], "Lockstep version metadata"),
        (runner + ["jamly", "doctor"], "jamly doctor health check"),
        (base + ["-m", "pytest", "-q"], "pytest unit suite"),
        (base + ["-m", "ruff", "check", "."], "ruff lint"),
    ]
    for cmd, label in stages:
        if not run(cmd, label):
            return _fail()

    node_argv = ["node", "--test"] + [str(ROOT / p) for p in NODE_TESTS]
    if _have("node"):
        if not run(node_argv, "Node extension tests"):
            return _fail()
    else:
        print("\n[skip] Node tests (node not on PATH)")

    if not args.no_build and not _wheel_smoke(use_uv):
        return _fail()

    if args.integration:
        integration_base = ["uv", "run", "--no-sync", "python"] if (use_uv and not args.no_build) else base
        probe = subprocess.run(
            integration_base + ["-c", "import soundfile"],
            cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
        if probe.returncode != 0:
            print("\n[skip] integration pipeline (soundfile unavailable in target environment)")
        elif not run(
            integration_base + ["-m", "unittest", "discover", "-s", "tests", "-p",
                                "integration_pipeline.py"],
            "Real-model audio pipeline (generated WAV)",
        ):
            return _fail()

    if args.e2e:
        e2e_pw = ROOT / "tests" / "e2e" / "node_modules" / "playwright-core"
        if not _have("node"):
            print("\n[skip] Playwright E2E (node not on PATH)")
        elif not e2e_pw.is_dir():
            print("\n[skip] Playwright E2E (install once with `npm ci --prefix tests/e2e`)")
        elif not _have_chromium():
            print("\n[skip] Playwright E2E (no Chrome/Chromium/Edge binary; set CHROME_BIN)")
        else:
            if not run(["node", "--test", str(ROOT / "tests" / "e2e" / "companion.e2e.test.mjs")],
                       "Playwright companion + extension E2E"):
                return _fail()

    if args.browser:
        if not _have("node"):
            print("\n[skip] browser smoke (node not on PATH)")
        elif not _have_chromium():
            print("\n[skip] browser smoke (no Chrome binary; set CHROME_BIN)")
        else:
            if not run(["node", str(ROOT / "tools" / "browser_smoke.mjs")],
                       "Headless-Chrome companion workflow"):
                return _fail()

    print("\n" + "=" * 72)
    print("All selected stages passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
