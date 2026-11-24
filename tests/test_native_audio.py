"""Offline tests for the checksum-pinned source download retry behavior."""
import hashlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError, URLError
from unittest.mock import MagicMock


def _load_native_audio():
    path = Path(__file__).resolve().parents[1] / "tools" / "native_audio.py"
    spec = importlib.util.spec_from_file_location("jamly_tools_native_audio", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_version_tool():
    path = Path(__file__).resolve().parents[1] / "tools" / "version.py"
    spec = importlib.util.spec_from_file_location("jamly_tools_version", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_winget_tool():
    path = Path(__file__).resolve().parents[1] / "tools" / "render_winget.py"
    spec = importlib.util.spec_from_file_location("jamly_tools_render_winget", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_icon_tool():
    path = Path(__file__).resolve().parents[1] / "tools" / "generate_icons.py"
    spec = importlib.util.spec_from_file_location("jamly_tools_generate_icons", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_tool(filename: str):
    path = Path(__file__).resolve().parents[1] / "tools" / filename
    spec = importlib.util.spec_from_file_location("jamly_tools_" + path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


native_audio = _load_native_audio()
build_companion = _load_tool("build_companion.py")
repro_dist = _load_tool("repro_dist.py")
dev_check = _load_tool("dev-check.py")
version_tool = _load_version_tool()
winget_tool = _load_winget_tool()
aur_tool = _load_tool("render_aur.py")
homebrew_tool = _load_tool("render_homebrew.py")
icon_tool = _load_icon_tool()

PAYLOAD = b"fake checksum-pinned source archive"
URL = "https://example.com/openssl.tar.gz"
ARCHIVE_NAME = Path(URL.split("/")[-1])


class FakeResponse(io.BytesIO):
    url = URL


class Clock:
    def __init__(self):
        self.delays = []

    def sleep(self, seconds):
        self.delays.append(seconds)


def _sources():
    return {
        "openssl": {
            "url": URL,
            "sha256": hashlib.sha256(PAYLOAD).hexdigest(),
        }
    }


class DownloadSourceRetryTests(unittest.TestCase):
    def setUp(self):
        self.urlopen = MagicMock()
        self.clock = Clock()

    def _download(self, cache: Path) -> Path:
        with unittest.mock.patch.object(native_audio, "SOURCES", _sources()), \
                unittest.mock.patch.object(native_audio, "urlopen", self.urlopen), \
                unittest.mock.patch.object(native_audio, "time", self.clock):
            return native_audio.download_source("openssl", cache)

    def test_wrapped_timeout_is_retried_until_success(self):
        self.urlopen.side_effect = [
            URLError(TimeoutError("timed out")),
            URLError(TimeoutError("[WinError 10060] A connection attempt failed")),
            FakeResponse(PAYLOAD),
        ]
        with tempfile.TemporaryDirectory() as root:
            archive = self._download(Path(root))
            self.assertEqual(archive.read_bytes(), PAYLOAD)
        self.assertEqual(self.urlopen.call_count, 3)
        self.assertEqual(self.clock.delays, [1, 2])

    def test_wrapped_connection_reset_is_retried(self):
        self.urlopen.side_effect = [
            URLError(ConnectionResetError(54, "Connection reset by peer")),
            FakeResponse(PAYLOAD),
        ]
        with tempfile.TemporaryDirectory() as root:
            archive = self._download(Path(root))
            self.assertEqual(archive.read_bytes(), PAYLOAD)
        self.assertEqual(self.urlopen.call_count, 2)
        self.assertEqual(self.clock.delays, [1])

    def test_wrapped_connection_refused_from_mirror_is_retried(self):
        self.urlopen.side_effect = [
            URLError(ConnectionRefusedError(10061, "Connection refused")),
            FakeResponse(PAYLOAD),
        ]
        with tempfile.TemporaryDirectory() as root:
            archive = self._download(Path(root))
            self.assertEqual(archive.read_bytes(), PAYLOAD)
        self.assertEqual(self.urlopen.call_count, 2)
        self.assertEqual(self.clock.delays, [1])

    def test_bare_timeout_during_copy_is_retried(self):
        self.urlopen.side_effect = [TimeoutError("timed out"), FakeResponse(PAYLOAD)]
        with tempfile.TemporaryDirectory() as root:
            archive = self._download(Path(root))
            self.assertEqual(archive.read_bytes(), PAYLOAD)
        self.assertEqual(self.urlopen.call_count, 2)

    def test_non_transient_error_is_not_retried(self):
        self.urlopen.side_effect = HTTPError(URL, 404, "Not Found", {}, None)
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(HTTPError):
                self._download(Path(root))
        self.assertEqual(self.urlopen.call_count, 1)
        self.assertEqual(self.clock.delays, [])

    def test_retries_are_exhausted_then_raised(self):
        self.urlopen.side_effect = URLError(TimeoutError("timed out"))
        with tempfile.TemporaryDirectory() as root:
            cache = Path(root)
            with self.assertRaises(URLError):
                self._download(cache)
            self.assertFalse((cache / ARCHIVE_NAME).exists())
            self.assertFalse(list(cache.glob("*.partial")))
        self.assertEqual(self.urlopen.call_count, 3)
        self.assertEqual(self.clock.delays, [1, 2])

    def test_valid_cached_archive_is_not_redownloaded(self):
        with tempfile.TemporaryDirectory() as root:
            cache = Path(root)
            (cache / ARCHIVE_NAME).write_bytes(PAYLOAD)
            archive = self._download(cache)
            self.assertEqual(archive.read_bytes(), PAYLOAD)
        self.assertEqual(self.urlopen.call_count, 0)

    def test_downloaded_checksum_mismatch_raises_and_cleans_partial(self):
        self.urlopen.side_effect = [FakeResponse(b"corrupted-payload")]
        with tempfile.TemporaryDirectory() as root:
            cache = Path(root)
            with self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
                self._download(cache)
            self.assertFalse((cache / ARCHIVE_NAME).exists())
            self.assertFalse(list(cache.glob("*.partial")))

    def test_cached_archive_checksum_mismatch_raises(self):
        with tempfile.TemporaryDirectory() as root:
            cache = Path(root)
            (cache / ARCHIVE_NAME).write_bytes(b"tampered-cache")
            with self.assertRaisesRegex(RuntimeError, "Cached source checksum mismatch"):
                self._download(cache)
        self.assertEqual(self.urlopen.call_count, 0)

    def test_insecure_http_redirect_is_rejected_and_cleans_partial(self):
        class InsecureResponse(io.BytesIO):
            url = "http://example.com/openssl.tar.gz"

        self.urlopen.side_effect = [InsecureResponse(PAYLOAD)]
        with tempfile.TemporaryDirectory() as root:
            cache = Path(root)
            with self.assertRaisesRegex(RuntimeError, "insecure"):
                self._download(cache)
            self.assertFalse((cache / ARCHIVE_NAME).exists())
            self.assertFalse(list(cache.glob("*.partial")))


class VersionToolTests(unittest.TestCase):
    def test_repository_versions_are_in_lockstep(self):
        from jamly import __version__

        checked = version_tool.check_versions()
        self.assertEqual(checked, __version__)
        self.assertEqual(version_tool.check_versions(tag="v" + __version__), __version__)

    def test_mismatched_tag_is_rejected(self):
        with self.assertRaises(ValueError):
            version_tool.check_versions(tag="v99.99.99")

    def test_set_version_updates_all_files_in_lockstep(self):
        repo_root = Path(__file__).resolve().parents[1]
        rel_paths = (
            "src/jamly/__init__.py",
            "pyproject.toml",
            "uv.lock",
            "browser/extension/manifest.json",
            "browser/userscripts/jamly.user.js",
            "tools/Jamly.spec",
        )
        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            for rel in rel_paths:
                dest = temp_root / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text((repo_root / rel).read_text(encoding="utf-8"), encoding="utf-8")
            updated = version_tool.set_version("0.2.0", root=temp_root)
            self.assertEqual(updated, "0.2.0")
            self.assertEqual(version_tool.check_versions(root=temp_root, tag="v0.2.0"), "0.2.0")
            spec_text = (temp_root / "tools" / "Jamly.spec").read_text(encoding="utf-8")
            self.assertIn('"CFBundleShortVersionString": __version__', spec_text)
            self.assertIn('"CFBundleVersion": __version__', spec_text)
            (temp_root / "pyproject.toml").write_text('version = "0.2.1"\n', encoding="utf-8")
            with self.assertRaises(ValueError):
                version_tool.check_versions(root=temp_root)


class WingetManifestTests(unittest.TestCase):
    def test_renders_manifests_and_zip_archive(self):
        import zipfile

        digest = "ab" * 32
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            out_dir = base / "winget"
            zip_path = base / "winget-manifests-0.1.0.zip"
            manifest_dir = winget_tool.render_winget_manifests(
                "v0.1.0",
                digest,
                out_dir,
                release_date="2026-11-17",
                zip_path=zip_path,
            )
            self.assertEqual(manifest_dir, out_dir / "kurtisnotcurtis.Jamly" / "0.1.0")
            installer_text = (manifest_dir / "kurtisnotcurtis.Jamly.installer.yaml").read_text(encoding="utf-8")
            self.assertIn("PackageVersion: 0.1.0", installer_text)
            self.assertIn("ReleaseDate: 2026-11-17", installer_text)
            self.assertIn("InstallerSha256: " + digest.upper(), installer_text)
            self.assertIn("RelativeFilePath: Jamly\\Jamly.exe", installer_text)
            self.assertNotIn("__", installer_text)
            with zipfile.ZipFile(zip_path) as zf:
                names = sorted(zf.namelist())
            self.assertEqual(
                names,
                [
                    "kurtisnotcurtis.Jamly/0.1.0/kurtisnotcurtis.Jamly.installer.yaml",
                    "kurtisnotcurtis.Jamly/0.1.0/kurtisnotcurtis.Jamly.locale.en-US.yaml",
                    "kurtisnotcurtis.Jamly/0.1.0/kurtisnotcurtis.Jamly.yaml",
                ],
            )


class AurPackageTests(unittest.TestCase):
    def test_renders_aur_package_and_zip_archive(self):
        import hashlib
        import zipfile

        digest = "AB" * 32
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            out_dir = base / "aur"
            zip_path = base / "aur-jamly-bin-0.1.1.zip"
            pkg_dir = aur_tool.render_aur_package(
                "v0.1.1",
                digest,
                out_dir,
                zip_path=zip_path,
            )
            self.assertEqual(pkg_dir, out_dir / "jamly-bin")
            pkgbuild_text = (pkg_dir / "PKGBUILD").read_text(encoding="utf-8")
            srcinfo_text = (pkg_dir / ".SRCINFO").read_text(encoding="utf-8")
            desktop_digest = hashlib.sha256((pkg_dir / "jamly.desktop").read_bytes()).hexdigest()
            icon_digest = hashlib.sha256((pkg_dir / "jamly.svg").read_bytes()).hexdigest()
            self.assertIn("pkgver=0.1.1", pkgbuild_text)
            self.assertIn(digest.lower(), pkgbuild_text)
            self.assertIn(desktop_digest, pkgbuild_text)
            self.assertIn(icon_digest, pkgbuild_text)
            self.assertIn("options=('!strip' '!debug')", pkgbuild_text)
            self.assertNotIn("__", pkgbuild_text)
            self.assertIn("pkgver = 0.1.1", srcinfo_text)
            self.assertIn("sha256sums = " + digest.lower(), srcinfo_text)
            self.assertIn("sha256sums = " + desktop_digest, srcinfo_text)
            self.assertIn("sha256sums = " + icon_digest, srcinfo_text)
            self.assertNotIn("__", srcinfo_text)
            with zipfile.ZipFile(zip_path) as zf:
                names = sorted(zf.namelist())
            self.assertEqual(
                names,
                [
                    "jamly-bin/.SRCINFO",
                    "jamly-bin/PKGBUILD",
                    "jamly-bin/jamly.desktop",
                    "jamly-bin/jamly.svg",
                ],
            )


class HomebrewCaskTests(unittest.TestCase):
    def test_renders_homebrew_cask_and_zip_archive(self):
        import zipfile

        arm_digest = "AA" * 32
        intel_digest = "BB" * 32
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            out_dir = base / "homebrew"
            zip_path = base / "homebrew-tap-0.1.1.zip"
            cask_path = homebrew_tool.render_homebrew_cask(
                "v0.1.1",
                arm_digest,
                intel_digest,
                out_dir,
                zip_path=zip_path,
            )
            self.assertEqual(cask_path, out_dir / "Casks" / "jamly.rb")
            cask_text = cask_path.read_text(encoding="utf-8")
            self.assertIn('version "0.1.1"', cask_text)
            self.assertIn(arm_digest.lower(), cask_text)
            self.assertIn(intel_digest.lower(), cask_text)
            self.assertIn('command_wrapper "jamly"', cask_text)
            self.assertIn('command_wrapper "jamly-runtime"', cask_text)
            self.assertNotIn("__", cask_text)
            with zipfile.ZipFile(zip_path) as zf:
                names = sorted(zf.namelist())
            self.assertEqual(names, ["Casks/jamly.rb"])


class BundleIconTests(unittest.TestCase):
    def test_ico_contains_valid_multi_resolution_entries(self):
        import struct

        ico_path = Path(__file__).resolve().parents[1] / "tools" / "jamly.ico"
        self.assertTrue(ico_path.is_file())
        data = ico_path.read_bytes()
        reserved, ico_type, count = struct.unpack_from("<HHH", data, 0)
        self.assertEqual(reserved, 0)
        self.assertEqual(ico_type, 1)
        self.assertEqual(count, len(icon_tool.ICO_SIZES))

        sizes = []
        for index in range(count):
            width, height, _cc, _res, planes, bpp, byte_len, offset = struct.unpack_from(
                "<BBBBHHII", data, 6 + index * 16,
            )
            self.assertEqual(width, height)
            self.assertEqual(planes, 1)
            self.assertEqual(bpp, 32)
            self.assertGreater(byte_len, 0)
            self.assertLessEqual(offset + byte_len, len(data))
            actual_size = 256 if width == 0 else width
            sizes.append(actual_size)
            payload = data[offset:offset + byte_len]
            if actual_size >= 256:
                self.assertEqual(payload[:8], b"\x89PNG\r\n\x1a\n")
            else:
                self.assertEqual(struct.unpack_from("<I", payload, 0)[0], 40)
        self.assertEqual(tuple(sizes), icon_tool.ICO_SIZES)

    def test_icns_contains_valid_png_chunks(self):
        import struct

        icns_path = Path(__file__).resolve().parents[1] / "tools" / "jamly.icns"
        self.assertTrue(icns_path.is_file())
        data = icns_path.read_bytes()
        magic, total_len = struct.unpack_from(">4sI", data, 0)
        self.assertEqual(magic, b"icns")
        self.assertEqual(total_len, len(data))

        tags = []
        pos = 8
        while pos < len(data):
            tag, chunk_len = struct.unpack_from(">4sI", data, pos)
            self.assertGreater(chunk_len, 16)
            self.assertLessEqual(pos + chunk_len, len(data))
            self.assertEqual(data[pos + 8:pos + 16], b"\x89PNG\r\n\x1a\n")
            tags.append(tag)
            pos += chunk_len
        self.assertEqual(pos, len(data))
        self.assertEqual(tuple(tags), tuple(tag for tag, _size in icon_tool.ICNS_CHUNKS))

    def test_jamly_spec_assigns_icons_to_binaries_and_macos_bundle(self):
        spec_text = (Path(__file__).resolve().parents[1] / "tools" / "Jamly.spec").read_text(encoding="utf-8")
        self.assertIn('root / "tools" / "jamly.ico"', spec_text)
        self.assertIn('root / "tools" / "jamly.icns"', spec_text)
        self.assertEqual(spec_text.count("icon=win_icon"), 2)
        self.assertIn("icon=mac_icon", spec_text)

    def test_icon_builders_encode_dib_and_containers(self):
        import base64
        import struct

        fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
        fake_b64 = base64.b64encode(fake_png).decode("ascii")
        full = {}
        for size in icon_tool.ICO_SIZES:
            rgba = bytes([10, 20, 30, 0 if size == 16 else 255]) * (size * size)
            full[str(size)] = {
                "png": fake_b64,
                "rgba": base64.b64encode(rgba).decode("ascii"),
            }
        ico_bytes = icon_tool.build_ico(full)
        self.assertEqual(struct.unpack_from("<HHH", ico_bytes, 0), (0, 1, len(icon_tool.ICO_SIZES)))

        mac = {str(size): fake_b64 for _tag, size in icon_tool.ICNS_CHUNKS}
        icns_bytes = icon_tool.build_icns(mac)
        magic, length = struct.unpack_from(">4sI", icns_bytes, 0)
        self.assertEqual(magic, b"icns")
        self.assertEqual(length, len(icns_bytes))


class NativeSourcesArchiveTests(unittest.TestCase):
    def test_build_native_sources_archive_writes_single_platform_independent_zip(self):
        import sys
        import zipfile

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            archives = base / "companions"
            downloads = base / "downloads"
            archives.mkdir()
            downloads.mkdir()

            fake_sources = {}
            expected_tarballs = []
            for idx, name in enumerate(native_audio.SOURCES):
                tarball_name = f"{name}-{idx}.tar.gz"
                payload = f"payload-{name}".encode("utf-8")
                (downloads / tarball_name).write_bytes(payload)
                fake_sources[name] = {
                    "url": f"https://example.com/{tarball_name}",
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
                expected_tarballs.append(tarball_name)

            download_mod = sys.modules[build_companion.download_source.__module__]
            with unittest.mock.patch.object(build_companion, "SOURCES", fake_sources), \
                    unittest.mock.patch.object(download_mod, "SOURCES", fake_sources):
                result = build_companion.build_native_sources_archive(archives, "0.1.4", downloads)

            self.assertEqual(result, archives / "Jamly-0.1.4-native-sources.zip")
            self.assertTrue(result.is_file())
            with zipfile.ZipFile(result) as zf:
                names = set(zf.namelist())
                self.assertEqual(
                    names,
                    set(expected_tarballs) | {"native-sources.json", "native_audio.py", "README.txt"},
                )
                for info in zf.infolist():
                    self.assertEqual(info.compress_type, zipfile.ZIP_STORED)


class ReproDistAndDevCheckTests(unittest.TestCase):
    def test_repro_dist_normalizes_wheel_and_sdist_idempotently(self):
        import base64
        import tarfile
        import zipfile

        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp)
            wheel = dist / "jamly_audio_sep-0.2.5-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("jamly/__init__.py", b"__version__ = '0.2.5'\n")
                zf.writestr("jamly_audio_sep-0.2.5.dist-info/METADATA", b"Name: jamly-audio-sep\r\nVersion: 0.2.5\r\n")
                zf.writestr("jamly_audio_sep-0.2.5.dist-info/RECORD", b"stale,,\n")

            repro_dist._canonical_wheel(wheel)
            first_whl = wheel.read_bytes()
            repro_dist._canonical_wheel(wheel)
            self.assertEqual(wheel.read_bytes(), first_whl)
            with zipfile.ZipFile(wheel) as zf:
                import stat
                for info in zf.infolist():
                    mode = info.external_attr >> 16
                    self.assertTrue(stat.S_ISREG(mode), f"{info.filename} missing S_IFREG: {oct(mode)}")
                    self.assertEqual(mode & stat.S_IWGRP, 0, f"{info.filename} unexpectedly group-writable: {oct(mode)}")
                meta = zf.read("jamly_audio_sep-0.2.5.dist-info/METADATA")
                self.assertNotIn(b"\r\n", meta)
                record = zf.read("jamly_audio_sep-0.2.5.dist-info/RECORD").decode("utf-8")
                expected_digest = base64.urlsafe_b64encode(hashlib.sha256(meta).digest()).rstrip(b"=").decode("ascii")
                self.assertIn(f"jamly_audio_sep-0.2.5.dist-info/METADATA,sha256={expected_digest},{len(meta)}", record)

            sdist = dist / "jamly_audio_sep-0.2.5.tar.gz"
            with tarfile.open(sdist, "w:gz") as tf:
                for name, payload in (
                    ("jamly_audio_sep-0.2.5/z.txt", b"z"),
                    ("jamly_audio_sep-0.2.5/PKG-INFO", b"Name: jamly-audio-sep\r\n"),
                ):
                    info = tarfile.TarInfo(name)
                    info.size = len(payload)
                    tf.addfile(info, io.BytesIO(payload))

            repro_dist._canonical_sdist(sdist)
            first_sdist = sdist.read_bytes()
            repro_dist._canonical_sdist(sdist)
            self.assertEqual(sdist.read_bytes(), first_sdist)
            with tarfile.open(sdist, "r:gz") as tf:
                names = [member.name for member in tf.getmembers()]
                self.assertEqual(names, sorted(names))
                self.assertNotIn(b"\r\n", tf.extractfile("jamly_audio_sep-0.2.5/PKG-INFO").read())

    def test_dev_check_selects_current_version_wheel_over_stale_wheels(self):
        from jamly import __version__

        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp)
            stale = dist / "jamly_audio_sep-0.9.9-py3-none-any.whl"
            current = dist / f"jamly_audio_sep-{__version__}-py3-none-any.whl"
            stale.write_bytes(b"stale")
            current.write_bytes(b"current")
            self.assertEqual(dev_check._current_wheel(dist), current)

    def test_package_render_sha256_file_streams_chunks(self):
        package_render = _load_tool("package_render.py")
        with tempfile.TemporaryDirectory() as tmp:
            sample = Path(tmp) / "archive.bin"
            payload = b"jamly-streamed-digest" * 100
            sample.write_bytes(payload)
            self.assertEqual(package_render.sha256_file(sample), hashlib.sha256(payload).hexdigest())

    def test_release_section_opens_new_unreleased_above_dated_release(self):
        import sys
        tools_dir = str(Path(__file__).resolve().parents[1] / "tools")
        if tools_dir not in sys.path:
            sys.path.insert(0, tools_dir)
        release_tool = _load_tool("release.py")
        sample = (
            "# Changelog\n\n"
            "## [Unreleased]\n\n"
            "### Fixed\n"
            "- Hardened startup.\n\n"
            "## [0.2.6] - 2026-11-23\n\n"
            "- Previous release.\n"
        )
        updated = release_tool._release_section(sample, "0.2.7")
        unreleased_pos = updated.index("## [Unreleased]")
        new_rel_pos = updated.index("## [0.2.7] - ")
        old_rel_pos = updated.index("## [0.2.6] - ")
        self.assertLess(unreleased_pos, new_rel_pos)
        self.assertLess(new_rel_pos, old_rel_pos)
        self.assertIn("- Hardened startup.", updated[new_rel_pos:old_rel_pos])

    def test_linux_cpu_torch_pin_matches_uv_lock_and_verifies_hash_and_https(self):
        import re
        torch_installer = _load_tool("install_linux_cpu_torch.py")
        pin = torch_installer.load_pin()
        lock_text = (Path(__file__).resolve().parents[1] / "uv.lock").read_text(encoding="utf-8")
        match = re.search(r'\[\[package\]\]\s+name = "torch"\s+version = "([^"]+)"', lock_text)
        self.assertIsNotNone(match)
        self.assertEqual(pin["version"], match.group(1))
        self.assertEqual(len(pin["sha256"]), 64)

        fake_wheel = b"fake-torch-wheel"
        good_pin = {
            "url": "https://download.pytorch.org/whl/cpu/torch-2.2.2%2Bcpu-cp312-cp312-linux_x86_64.whl",
            "sha256": hashlib.sha256(fake_wheel).hexdigest(),
        }

        class GoodResp(io.BytesIO):
            url = good_pin["url"]

        class HttpResp(io.BytesIO):
            url = "http://download.pytorch.org/whl/cpu/torch-2.2.2%2Bcpu-cp312-cp312-linux_x86_64.whl"

        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            with unittest.mock.patch.object(torch_installer, "urlopen", return_value=GoodResp(fake_wheel)):
                whl = torch_installer.download_verified_wheel(good_pin, dest)
                self.assertEqual(whl.read_bytes(), fake_wheel)
            with unittest.mock.patch.object(torch_installer, "urlopen", return_value=GoodResp(b"wrong-bytes")):
                with self.assertRaisesRegex(RuntimeError, "SHA-256 mismatch"):
                    torch_installer.download_verified_wheel(good_pin, dest / "bad")
            with unittest.mock.patch.object(torch_installer, "urlopen", return_value=HttpResp(fake_wheel)):
                with self.assertRaisesRegex(RuntimeError, "insecure"):
                    torch_installer.download_verified_wheel(good_pin, dest / "http")

    def test_native_audio_component_flags_and_pruning(self):
        with unittest.mock.patch.object(native_audio.os, "cpu_count", return_value=8):
            self.assertEqual(native_audio.default_jobs(), 8)
        with unittest.mock.patch.object(native_audio.os, "cpu_count", return_value=None):
            self.assertEqual(native_audio.default_jobs(), 4)

        flags = native_audio.ffmpeg_component_flags()
        self.assertIn("--disable-avdevice", flags)
        self.assertIn("--disable-hwaccels", flags)
        self.assertIn("--disable-encoders", flags)
        self.assertIn("--disable-decoders", flags)
        self.assertIn("--disable-filters", flags)
        self.assertIn("--disable-bsfs", flags)
        joined = " ".join(flags)
        self.assertIn("libmp3lame", joined)
        self.assertIn("aresample", joined)
        self.assertIn("astats", joined)
        self.assertIn("aac_adtstoasc", joined)
        self.assertIn("real_144", joined)
        self.assertNotIn("mp3_header_decompress", joined)

        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            prefix = work / "install"
            (work / ".jamly-native-build").write_text("", encoding="utf-8")
            (work / "built.json").write_text("{}", encoding="utf-8")
            (work / "build.log").write_text("ok\n", encoding="utf-8")
            (work / "downloads").mkdir()
            (work / "downloads" / "ffmpeg.tar.xz").write_bytes(b"tar")
            (work / "source" / "ffmpeg").mkdir(parents=True)
            (work / "source" / "ffmpeg" / "foo.o").write_bytes(b"obj")
            (prefix / "bin").mkdir(parents=True)
            (prefix / "bin" / "ffmpeg").write_bytes(b"bin")
            (prefix / "bin" / "ffprobe").write_bytes(b"bin")
            (prefix / "include" / "openssl").mkdir(parents=True)
            (prefix / "include" / "openssl" / "ssl.h").write_text("", encoding="utf-8")
            (prefix / "share" / "man").mkdir(parents=True)
            (prefix / "lib" / "pkgconfig").mkdir(parents=True)
            (prefix / "lib" / "pkgconfig" / "openssl.pc").write_text("", encoding="utf-8")
            (prefix / "lib" / "libcrypto.a").write_bytes(b"static")
            (prefix / "lib" / "libsndfile.la").write_text("", encoding="utf-8")
            (prefix / "lib" / "libsndfile.so.1").write_bytes(b"shared")

            native_audio._prune_build_tree(work, prefix)

            self.assertFalse((work / "source").exists())
            self.assertFalse((prefix / "include").exists())
            self.assertFalse((prefix / "share").exists())
            self.assertFalse((prefix / "lib" / "pkgconfig").exists())
            self.assertFalse((prefix / "lib" / "libcrypto.a").exists())
            self.assertFalse((prefix / "lib" / "libsndfile.la").exists())
            self.assertTrue((prefix / "bin" / "ffmpeg").is_file())
            self.assertTrue((prefix / "bin" / "ffprobe").is_file())
            self.assertTrue((prefix / "lib" / "libsndfile.so.1").is_file())
            self.assertTrue((work / "downloads" / "ffmpeg.tar.xz").is_file())
            self.assertTrue((work / "built.json").is_file())
            self.assertTrue((work / "build.log").is_file())

    def test_linux_companion_archive_prefers_pigz_then_gzip_then_tarfile(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            output = base / "desktop"
            (output / "Jamly").mkdir(parents=True)
            (output / "Jamly" / "jamly-runtime").write_bytes(b"exe")
            archives = base / "companions"
            archives.mkdir()

            with unittest.mock.patch.object(build_companion.sys, "platform", "linux"), \
                    unittest.mock.patch.object(build_companion.os, "name", "posix"), \
                    unittest.mock.patch.object(
                        build_companion.shutil, "which",
                        side_effect=lambda name: f"/usr/bin/{name}" if name in ("tar", "pigz") else None,
                    ), \
                    unittest.mock.patch.object(build_companion.subprocess, "run") as run_mock:
                artifact = build_companion._create_companion_archive(output, archives, "Jamly-0.3.0-linux-x86_64")
            self.assertEqual(artifact, archives / "Jamly-0.3.0-linux-x86_64.tar.gz")
            self.assertEqual(
                run_mock.call_args.args[0],
                ["tar", "-I", "pigz -6", "-cf", str(artifact), "-C", str(output), "Jamly"],
            )

            with unittest.mock.patch.object(build_companion.sys, "platform", "linux"), \
                    unittest.mock.patch.object(build_companion.os, "name", "posix"), \
                    unittest.mock.patch.object(build_companion.shutil, "which", return_value=None):
                fallback = build_companion._create_companion_archive(output, archives, "Jamly-0.3.0-fallback")
            self.assertTrue(fallback.is_file())
            import tarfile
            with tarfile.open(fallback, "r:gz") as tf:
                self.assertIn("Jamly/jamly-runtime", tf.getnames())

    def test_dev_check_includes_options_node_test(self):
        self.assertIn("browser/extension/jamly_options_test.js", dev_check.NODE_TESTS)


if __name__ == "__main__":
    unittest.main()
