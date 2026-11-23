import io
import unittest
from unittest.mock import patch

from jamly.logging import Logger
from jamly.run import ItemResult, link_outputs


class LoggingTests(unittest.TestCase):
    def capture(self, quiet=False):
        logger = Logger(quiet=quiet, color=False)
        logger.stream = io.StringIO()
        return logger

    def test_stages_and_errors_are_visible(self):
        logger = self.capture()
        logger.step("download")
        logger.ok("done")
        logger.fail("failure detail")
        content = logger.stream.getvalue()
        self.assertIn("download", content)
        self.assertIn("done", content)
        self.assertIn("failure detail", content)

    def test_quiet_and_color_controls(self):
        logger = self.capture(quiet=True)
        logger.step("hidden")
        self.assertEqual(logger.stream.getvalue(), "")
        self.assertNotIn("\x1b[", logger._c("plain", "green"))
        self.assertIn("\x1b[", Logger(color=True)._c("color", "green"))

    def test_only_successful_results_are_linked(self):
        logger = self.capture()
        with patch("jamly.run._log", logger):
            link_outputs(ItemResult(
                "id", status="done",
                stem_paths={"drums": "/out/drums.wav", "no_drums": "/out/no-drums.wav"},
                meta_path="/out/metadata.json",
            ))
            link_outputs(ItemResult("bad", status="error", stem_paths={"drums": "/bad.wav"}))
        content = logger.stream.getvalue()
        self.assertIn("drums.wav", content)
        self.assertIn("no-drums.wav", content)
        self.assertIn("metadata.json", content)
        self.assertNotIn("bad.wav", content)

    def test_link_handles_cross_drive_paths_on_windows(self):
        logger = self.capture()
        with patch("jamly.logging.os.path.isabs", return_value=True), \
                patch("jamly.logging.os.path.relpath", side_effect=ValueError("path is on mount 'D:'")):
            logger.link("D:\\exports\\track_drums.wav")
        self.assertIn("D:\\exports\\track_drums.wav", logger.stream.getvalue())
