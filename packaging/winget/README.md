# Winget Packaging for Jamly

These templates are used by [`.github/workflows/companions.yml`](../../.github/workflows/companions.yml) to generate ready-to-submit `winget-pkgs` manifests for each GitHub Release.

## Submitting to `microsoft/winget-pkgs`

1. Download the generated `winget-manifests-<version>.zip` from the GitHub Release assets (or render locally with `python tools/render_winget.py --version <version> --sha256-amd64 <sha256>`).
2. Validate locally on Windows 11:
   ```powershell
   winget validate --manifest .\kurtisnotcurtis.Jamly\<version>\
   ```
3. Test local installation:
   ```powershell
   winget settings --enable LocalManifestFiles
   winget install --manifest .\kurtisnotcurtis.Jamly\<version>\
   ```
4. Submit a PR to `microsoft/winget-pkgs` under:
   `manifests/k/kurtisnotcurtis/Jamly/<version>/`
   (or run `wingetcreate submit .\kurtisnotcurtis.Jamly\<version>\`).

## Automating on Tagged Releases

[`.github/workflows/companions.yml`](../../.github/workflows/companions.yml) automatically runs `wingetcreate submit` on `v*` tags when `WINGET_TOKEN` is configured:

1. Create a **classic** GitHub Personal Access Token with the `public_repo` scope (`wingetcreate` does not support fine-grained tokens).
2. In `kurtisnotcurtis/jamly` **Settings → Secrets and variables → Actions → Secrets**, add it as `WINGET_TOKEN`.
