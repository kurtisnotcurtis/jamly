from contextlib import redirect_stderr, redirect_stdout
import io
import unittest
from unittest.mock import patch

from jamly import __version__, bootstrap, cli, companion, run, server, service


class UnifiedCliTests(unittest.TestCase):
    def test_no_args_launches_companion(self):
        with patch.object(companion, "main", return_value=0) as mocked:
            code = cli.main([])
        self.assertEqual(code, 0)
        mocked.assert_called_once_with([])

    def test_help_and_version_flags(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.main(["--help"])
        self.assertEqual(code, 0)
        help_text = stdout.getvalue()
        for command in ("companion", "split", "server", "doctor", "service"):
            self.assertIn(command, help_text)

        version_out = io.StringIO()
        with redirect_stdout(version_out):
            code = cli.main(["--version"])
        self.assertEqual(code, 0)
        self.assertEqual(version_out.getvalue().strip(), "jamly " + __version__)

    def test_subcommand_dispatch_forwards_arguments(self):
        cases = (
            ("companion", companion, ["--no-browser", "--port", "0"]),
            ("split", run, ["https://youtu.be/dQw4w9WgXcQ", "--audio-format", "wav"]),
            ("server", server, ["--port", "8765", "--no-tray"]),
            ("doctor", bootstrap, []),
            ("service", service, ["token", "--state-dir", "/tmp/jamly"]),
        )
        for name, module, forwarded in cases:
            with self.subTest(command=name), patch.object(module, "main", return_value=0) as mocked:
                code = cli.main([name, *forwarded])
            self.assertEqual(code, 0)
            mocked.assert_called_once_with(forwarded)

    def test_unknown_command_and_legacy_input_flag_report_clear_errors(self):
        err = io.StringIO()
        with redirect_stderr(err):
            code = cli.main(["unknown-cmd"])
        self.assertEqual(code, 2)
        self.assertIn("unknown command", err.getvalue())

        hint_err = io.StringIO()
        with redirect_stderr(hint_err):
            code = cli.main(["-i", "https://youtu.be/dQw4w9WgXcQ"])
        self.assertEqual(code, 2)
        self.assertIn("jamly split", hint_err.getvalue())

    def test_split_accepts_positional_and_flag_inputs_together(self):
        args = run.build_parser().parse_args([
            "https://youtu.be/dQw4w9WgXcQ",
            "-i", "https://youtu.be/9bZkp7q19f0",
            "--audio-format", "wav",
        ])
        items = run.gather_inputs(args)
        self.assertEqual([item.watch_id for item in items], ["dQw4w9WgXcQ", "9bZkp7q19f0"])
        self.assertEqual(args.audio_format, "wav")


if __name__ == "__main__":
    unittest.main()
