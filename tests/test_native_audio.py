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


if __name__ == "__main__":
    unittest.main()
