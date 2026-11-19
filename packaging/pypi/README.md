# PyPI Packaging for Jamly (`jamly-audio-sep`)

The [`.github/workflows/pypi.yml`](../../.github/workflows/pypi.yml) workflow builds the source distribution and wheel from [`pyproject.toml`](../../pyproject.toml) and publishes via OIDC Trusted Publishing (no long-lived API tokens required).

## One-time Setup (TestPyPI & PyPI)

1. **Create GitHub Environments** in **Settings → Environments**:
   - `testpypi`
   - `pypi` (optionally require manual reviewer approval)
2. **Register a Pending Trusted Publisher** on [TestPyPI](https://test.pypi.org/manage/account/publishing/) and [PyPI](https://pypi.org/manage/account/publishing/):
   - **PyPI Project Name**: `jamly-audio-sep`
   - **Owner**: `kurtisnotcurtis`
   - **Repository name**: `jamly`
   - **Workflow name**: `pypi.yml`
   - **Environment name**: `testpypi` (on TestPyPI) / `pypi` (on PyPI)
3. **Test on TestPyPI first** via **Actions → Publish to PyPI → Run workflow** (`target: testpypi`), then verify installation:
   ```sh
   uv tool install --python 3.12 --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ jamly-audio-sep
   ```
4. **Enable automatic tag publishing**:
   - Run the workflow manually once with `target: pypi` (or add `PYPI_PUBLISH_ENABLED` = `true` under **Settings → Secrets and variables → Actions → Variables**).
   - Once `PYPI_PUBLISH_ENABLED` is `true`, pushing a `vX.Y.Z` tag publishes `jamly-audio-sep` automatically after verifying lockstep versions.
5. **Post-publish doc follow-up**: Update Option B in [`README.md`](../../README.md) from `git+https://github.com/kurtisnotcurtis/jamly.git` to `jamly-audio-sep`.

## Local Build Verification

```sh
uv build
```
