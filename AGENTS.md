# Contributing

- Application code is under `src/jamly`; Python tests are under `tests`.
- Use package imports (`from .core import ...` internally, `from jamly.core import ...` in tests).
- Install with `uv sync --locked`. Run pytest, unittest and Ruff through `uv run --locked`.
- Browser code is under `browser/`; its Node tests run with `node --test`.
- The companion page is under `src/jamly/web`; it exports stems and offers in-page practice playback of
  finished exports (no browser downloads). It is not a full practice platform.
- `tools/` builds native desktop archives. Use `uv sync --locked --group bundle` and then
  `uv run --no-sync` for packaging commands so the build dependencies are not pruned mid-build.
- Exercise relocated bundles with an empty PATH and both models. Keep native sources, notices
  and checksums alongside distribution artifacts; never commit binaries or weights.
- Keep authentication out of URLs. The companion's automatic pairing uses a private, single-use file.
- Keep fast tests offline and deterministic. Use temporary directories and explicit process cleanup.
- Test external-tool contracts with real audio in the dedicated integration check, not only mocked
  command strings. Put each unittest assertion on its own statement.
- Preserve source levels, paired-output integrity, durable job states, and explicit error reporting.
- Keep subprocess arguments as lists. Never put secrets into source, logs, URLs, or shell commands.
- Keep README focused on install/use and DESIGN focused on current behavior. Git history records
  superseded designs.
- Do not commit environments, model weights, output audio, logs, or temporary probes.
- Keep `src/jamly/__init__.py`, `pyproject.toml`, `uv.lock`, `browser/extension/manifest.json`,
  `browser/userscripts/jamly.user.js`, and `tools/Jamly.spec` in lockstep SemVer (`X.Y.Z`) using
  `python tools/version.py --set X.Y.Z` and verify with `python tools/version.py --check`.
  Release tags use `vX.Y.Z` and must match the package version. Keep `PROCESSING_VERSION` and
  `api_version` separate from release versions.
- Verify locally before pushing with `python tools/dev-check.py` (a stdlib-only mirror of
  `.github/workflows/ci.yml`). The pre-commit hook runs it wheel-free; the pre-push hook adds the
  real-model audio pipeline. Install both once with `git config core.hooksPath tools/hooks`.
- Cut a release with `python tools/release.py new X.Y.Z`, which bumps every lockstep file and dates
  the top `## [Unreleased]` changelog section (opening a fresh empty one). Start dev on the next
  version first with `python tools/release.py bump minor|patch|major`; accumulate changes under
  `## [Unreleased]`, then cut. Commit, tag `vX.Y.Z` (annotated), and push tags.
