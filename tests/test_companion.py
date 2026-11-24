import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from jamly import companion, companion_ui, runtime, tray


class CompanionLauncherTests(unittest.TestCase):
    def test_module_command_resolves_symlinked_frozen_launcher(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            bundle = base / "package" / "Jamly"
            bundle.mkdir(parents=True)
            launcher = bundle / ("Jamly.exe" if os.name == "nt" else "Jamly")
            launcher.write_text("launcher")
            runtime_name = "jamly-runtime.exe" if os.name == "nt" else "jamly-runtime"
            links = base / "links"
            links.mkdir()
            alias = links / ("jamly.exe" if os.name == "nt" else "jamly")
            with patch.object(runtime, "bundle_root", return_value=bundle / "_internal"), patch.object(
                runtime.sys, "executable", str(alias)
            ), patch.object(Path, "resolve", return_value=launcher):
                command = runtime.module_command("jamly.server", "--port", "8765")
            self.assertEqual(command, [str(bundle / runtime_name), "--module", "jamly.server", "--port", "8765"])

    def test_compact_cli_override_is_normalized_before_updating_running_backend(self):
        with tempfile.TemporaryDirectory() as root:
            state = Path(root)
            private = state / "browser-sessions"
            private.mkdir()
            launch = private / "launch-test.html"
            launch.write_text("test launch")
            saved = []

            def request(_url, _token, path, body):
                if path == "/settings":
                    if not Path(body["output_dir"]).is_absolute():
                        raise ValueError("The API requires absolute output paths")
                    saved.append(body)
                    return body
                self.assertEqual(path, "/app/launch")
                return {"launch_file": launch.as_uri()}

            with patch.dict(os.environ, {"JAMLY_SERVER_TOKEN": "", "JAMLY_QUEUE": ""}), patch.object(
                companion, "ensure_backend", return_value="http://127.0.0.1:8765"
            ), patch.object(companion, "request", side_effect=request):
                code = companion.main([
                    "--state-dir", root, "--no-browser", "-orelative-exports", "--model=htdemucs_ft",
                ])
            self.assertEqual(code, 0)
            self.assertEqual(saved, [{
                "model": "htdemucs_ft", "output_dir": str(Path("relative-exports").absolute()),
            }])

    def test_companion_ui_serves_favicon_and_allows_self_images_in_csp(self):
        with tempfile.TemporaryDirectory() as root:
            ui = companion_ui.CompanionUI(Path(root))
            html_bytes, html_type = ui.asset("/app/")
            icon_bytes, icon_type = ui.asset("/app/jamly.svg")
            repo_icon = Path(__file__).resolve().parents[1] / "site" / "jamly-mark.svg"
            self.assertEqual(html_type, "text/html; charset=utf-8")
            self.assertIn(b'<link rel="icon" href="/app/jamly.svg" type="image/svg+xml">', html_bytes)
            self.assertIn("img-src 'self'", companion_ui.CSP)
            self.assertIn("media-src 'self' blob:", companion_ui.CSP)
            self.assertEqual(icon_type, "image/svg+xml")
            self.assertEqual(
                icon_bytes.decode("utf-8").replace("\r\n", "\n"),
                repo_icon.read_text(encoding="utf-8").replace("\r\n", "\n"),
            )
            client_bytes, client_type = ui.asset("/app/jamly_client.js")
            repo_client = Path(__file__).resolve().parents[1] / "browser" / "extension" / "jamly_client.js"
            self.assertEqual(client_type, "text/javascript; charset=utf-8")
            self.assertEqual(
                client_bytes.decode("utf-8").replace("\r\n", "\n"),
                repo_client.read_text(encoding="utf-8").replace("\r\n", "\n"),
            )

    def test_browser_assets_falls_back_to_user_base_install(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            fake_runtime = base / "site-packages" / "jamly" / "runtime.py"
            fake_runtime.parent.mkdir(parents=True)
            fake_runtime.write_text("", encoding="utf-8")
            user_assets = base / "user-base" / "share" / "jamly" / "browser" / "extension"
            user_assets.mkdir(parents=True)
            user_icon = base / "user-base" / "share" / "jamly" / "jamly.ico"
            user_icon.write_bytes(b"ico")
            with patch.object(runtime, "bundle_root", return_value=None), \
                    patch.object(runtime, "__file__", str(fake_runtime)), \
                    patch.object(runtime.sys, "prefix", str(base / "empty-prefix")), \
                    patch.object(runtime.site, "USER_BASE", str(base / "user-base")):
                self.assertEqual(runtime.browser_assets("extension"), user_assets)
                self.assertEqual(tray.find_icon_path(), user_icon)
