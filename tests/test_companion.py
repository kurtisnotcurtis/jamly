import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from jamly import companion, runtime


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
