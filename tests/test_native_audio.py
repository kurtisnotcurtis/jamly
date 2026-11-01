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


native_audio = _load_native_audio()

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


if __name__ == "__main__":
    unittest.main()
