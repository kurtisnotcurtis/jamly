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


native_audio = _load_native_audio()
version_tool = _load_version_tool()

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
            (temp_root / "pyproject.toml").write_text('version = "0.2.1"\n', encoding="utf-8")
            with self.assertRaises(ValueError):
                version_tool.check_versions(root=temp_root)


if __name__ == "__main__":
    unittest.main()
