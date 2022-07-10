"""Unit tests for normalize.py YouTube watch ID extraction."""
import unittest

from jamly.normalize import InputError, WATCH_ID_RE, extract_watch_id, normalize_inputs


class WatchIdPatternTests(unittest.TestCase):
    def test_valid_ids(self):
        for i in ["dQw4w9WgXcQ", "aB3-_xZ90qR", "0" * 11, "z" * 11]:
            self.assertTrue(WATCH_ID_RE.match(i), i)

    def test_invalid_ids(self):
        for i in ["", "tooShort", "dQw4w9WgXcQx", "has space", "bad?char", "1234567890"]:
            self.assertFalse(WATCH_ID_RE.match(i), i)


class ExtractTests(unittest.TestCase):
    def test_bare_id_and_urls(self):
        self.assertEqual(extract_watch_id("dQw4w9WgXcQ"), "dQw4w9WgXcQ")
        self.assertEqual(
            extract_watch_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=30s"),
            "dQw4w9WgXcQ",
        )
        self.assertEqual(extract_watch_id("https://youtu.be/dQw4w9WgXcQ"), "dQw4w9WgXcQ")
        self.assertEqual(extract_watch_id("https://www.youtube.com/shorts/dQw4w9WgXcQ"), "dQw4w9WgXcQ")

    def test_invalid_raises(self):
        for bad in [None, "", "   ", "not-a-link-123", "https://example.com/foo"]:
            with self.assertRaises(InputError):
                extract_watch_id(bad)

    def test_dedupe_preserves_order(self):
        ids = normalize_inputs(["dQw4w9WgXcQ", "https://youtu.be/dQw4w9WgXcQ", "DQW4W9WGXCQ"])
        self.assertEqual(ids, ["dQw4w9WgXcQ", "DQW4W9WGXCQ"])
