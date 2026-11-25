import sys
import tempfile
import unittest
from unittest.mock import patch

from jamly import server, tray


class TrayTests(unittest.TestCase):
    def test_find_icon_path_locates_repo_ico(self):
        path = tray.find_icon_path()
        self.assertIsNotNone(path)
        self.assertTrue(path.is_file())
        self.assertEqual(path.name, "jamly.ico")

    def test_tray_status_formats_idle_queued_and_active_stages(self):
        with tempfile.TemporaryDirectory() as root:
            args = server.build_parser().parse_args(["--state-dir", root, "--port", "0"])
            app = server.ItemServer(args)
            try:
                self.assertEqual(tray.tray_status(app), ("Jamly - Idle", "Status: Idle"))

                with app._status_lock:
                    app.queue.add("a", source_url="https://youtu.be/a")
                    app.queue.add("b", source_url="https://youtu.be/b")
                    app.queue.finish("b", "done")
                self.assertEqual(tray.tray_status(app), ("Jamly - 1 queued", "Status: 1 queued"))

                with app._status_lock:
                    app._current = {"watch_id": "a", "stage": "separate", "progress": 42, "cancelling": False}
                self.assertEqual(
                    tray.tray_status(app),
                    ("Jamly - Separating audio (42%) · 1 queued", "Status: Separating audio (42%) · 1 queued"),
                )

                with app._status_lock:
                    app._current = {"watch_id": "a", "stage": "separate", "progress": 42, "cancelling": True}
                self.assertEqual(
                    tray.tray_status(app),
                    ("Jamly - Cancelling... · 1 queued", "Status: Cancelling... · 1 queued"),
                )

                with app._status_lock:
                    app._current = None
                    app.queue.finish("a", "done")
                self.assertEqual(
                    tray.tray_status(app),
                    ("Jamly - Idle (2 ready)", "Status: Idle (2 ready)"),
                )

                with app._status_lock:
                    app.queue.add("c", source_url="https://youtu.be/c")
                    app.queue.mark_running("c")
                self.assertEqual(
                    tray.tray_status(app),
                    ("Jamly - Working", "Status: Working"),
                )

                with app._status_lock:
                    app._current = {"watch_id": "c", "stage": "download", "progress": 60, "cancelling": False}
                self.assertEqual(
                    tray.tray_status(app),
                    ("Jamly - Downloading audio (60%)", "Status: Downloading audio (60%)"),
                )

                with app._status_lock:
                    app._error = "Cannot persist queue state"
                self.assertEqual(
                    tray.tray_status(app),
                    ("Jamly - Error (check Jamly)", "Status: Error (check Jamly)"),
                )
            finally:
                app.stop()

    def test_create_tray_returns_null_when_disabled(self):
        with tempfile.TemporaryDirectory() as root:
            args = server.build_parser().parse_args(["--state-dir", root, "--port", "0"])
            app = server.ItemServer(args)
            try:
                self.assertIsInstance(tray.create_tray(app, enabled=False), tray.NullTray)
            finally:
                app.stop()

    def test_open_companion_uses_private_launch_file(self):
        with tempfile.TemporaryDirectory() as root:
            args = server.build_parser().parse_args(["--state-dir", root, "--port", "0"])
            app = server.ItemServer(args)
            try:
                with patch.object(tray.webbrowser, "open", return_value=True) as mocked_open:
                    tray.open_companion(app)
                mocked_open.assert_called_once()
                uri = mocked_open.call_args.args[0]
                self.assertTrue(uri.startswith("file:"))
                self.assertNotIn("token", uri)
            finally:
                app.stop()

    @unittest.skipUnless(sys.platform == "win32", "Win32 tray is Windows-only")
    def test_windows_tray_starts_and_stops_cleanly(self):
        with tempfile.TemporaryDirectory() as root:
            args = server.build_parser().parse_args(["--state-dir", root, "--port", "0"])
            app = server.ItemServer(args)
            try:
                icon = tray.create_tray(app, enabled=True)
                self.assertIsInstance(icon, tray.WindowsTray)
                icon.start()
                if tray._user32.GetShellWindow():
                    self.assertIsNotNone(icon._hwnd)
                icon.stop()
                self.assertIsNone(icon._hwnd)
            finally:
                app.stop()
