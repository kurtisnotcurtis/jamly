# AUR Packaging for Jamly (`jamly-bin`)

These templates are used by [`.github/workflows/companions.yml`](../../.github/workflows/companions.yml) to generate a ready-to-push Arch User Repository package (`aur-jamly-bin-<version>.zip`) for each GitHub Release.

`jamly-bin` repackages the self-contained Linux x64 companion tarball (`Jamly-<version>-linux-x86_64.tar.gz`) into `/opt/jamly-bin/` with `/usr/bin/jamly`, a desktop entry, and the Jamly icon. `options=('!strip' '!debug')` is set because stripping PyInstaller executables removes the embedded archive.

## Submitting or Updating `jamly-bin` on AUR

1. Register an account and add your SSH public key on [aur.archlinux.org](https://aur.archlinux.org/).
2. Clone the AUR package repository (first push creates the package if it does not exist):
   ```sh
   git clone ssh://aur@aur.archlinux.org/jamly-bin.git
   ```
3. Download `aur-jamly-bin-<version>.zip` from the GitHub Release assets (or render locally):
   ```sh
   python tools/render_aur.py --version <version> --sha256-x86_64 <sha256>
   ```
4. Copy the rendered files (`PKGBUILD`, `.SRCINFO`, `jamly.desktop`, `jamly.svg`) into the cloned `jamly-bin` repository, then verify on Arch Linux before pushing:
   ```sh
   makepkg --printsrcinfo > .SRCINFO
   makepkg -si
   namcap PKGBUILD jamly-bin-*.pkg.tar.zst
   ```
5. Commit and push to `master` on AUR:
   ```sh
   git add PKGBUILD .SRCINFO jamly.desktop jamly.svg
   git commit -m "jamly-bin: release <version>"
   git push origin master
   ```

## Automating on Tagged Releases

[`.github/workflows/companions.yml`](../../.github/workflows/companions.yml) automatically pushes `jamly-bin` on `v*` tags when `AUR_SSH_PRIVATE_KEY` is configured:

1. Generate an SSH key pair (`ssh-keygen -t ed25519 -N "" -f aur_deploy`) and add the public key to your account on [aur.archlinux.org](https://aur.archlinux.org/).
2. In `kurtisnotcurtis/jamly` **Settings → Secrets and variables → Actions → Secrets**, add `AUR_SSH_PRIVATE_KEY` containing the private key.
