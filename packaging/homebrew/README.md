# Homebrew Tap Packaging for Jamly (`kurtisnotcurtis/homebrew-tap`)

These templates are used by [`.github/workflows/companions.yml`](../../.github/workflows/companions.yml) to generate a ready-to-commit Homebrew Cask (`homebrew-tap-<version>.zip`) for each GitHub Release.

A personal Homebrew tap is used while `Jamly.app` remains an unsigned, unnotarized preview build, because `Homebrew/homebrew-cask` requires Apple Developer ID signing, notarization, and upstream notability thresholds.

## Setting Up `kurtisnotcurtis/homebrew-tap`

1. Create a public GitHub repository named `homebrew-tap` under `kurtisnotcurtis` (`https://github.com/kurtisnotcurtis/homebrew-tap`).
2. Download `homebrew-tap-<version>.zip` from the GitHub Release assets (or render locally):
   ```sh
   python tools/render_homebrew.py --version <version> --sha256-arm64 <arm64_sha256>
   ```
3. Copy `Casks/jamly.rb` into the `homebrew-tap` repository, commit, and push to `main`.
4. Test installation and cask linting on macOS:
   ```sh
   brew tap kurtisnotcurtis/tap
   brew audit --cask kurtisnotcurtis/tap/jamly
   brew install --cask kurtisnotcurtis/tap/jamly
   ```
5. Once live, users can install and upgrade via:
   ```sh
   brew install --cask kurtisnotcurtis/tap/jamly
   ```

## Automating on Tagged Releases

[`.github/workflows/companions.yml`](../../.github/workflows/companions.yml) automatically updates `kurtisnotcurtis/homebrew-tap` on `v*` tags when `HOMEBREW_TAP_DEPLOY_KEY` is configured:

1. Generate an SSH deploy key pair (`ssh-keygen -t ed25519 -N "" -f homebrew_tap_deploy`).
2. In `kurtisnotcurtis/homebrew-tap` **Settings → Deploy keys**, add `homebrew_tap_deploy.pub` with **Allow write access** enabled.
3. In `kurtisnotcurtis/jamly` **Settings → Secrets and variables → Actions → Secrets**, add `HOMEBREW_TAP_DEPLOY_KEY` containing the private key.
