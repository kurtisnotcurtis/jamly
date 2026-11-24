import http.client
import json
import os
from pathlib import Path
import re
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from urllib.parse import unquote, urlencode, urlsplit

from filelock import FileLock

from jamly import companion, config, core, server
from jamly.outputs import PROCESSING_VERSION, checksum
from jamly.run import ItemResult
from jamly.watch_queue import QueueError, WatchQueue

TEST_TOKEN = "test-token-0123456789abcdef0123456789"
TEST_EXT_ORIGIN = "chrome-extension://" + "a" * 32


class ServerTests(unittest.TestCase):
    wid = "dQw4w9WgXcQ"
    source = "https://youtu.be/dQw4w9WgXcQ"

    def start(self, process=None, *, extra_args=None):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        argv = [
            "--port", "0", "--queue", str(root / "queue.json"),
            "--state-dir", str(root),
            "--output-dir", str(root / "output"),
        ]
        if extra_args:
            argv.extend(extra_args)
        args = server.build_parser().parse_args(argv)
        self.enterContext(patch.dict(os.environ, {"JAMLY_SERVER_TOKEN": TEST_TOKEN}))
        self.enterContext(patch.object(
            server, "process_one", side_effect=process or
            (lambda wid, args, **kwargs: ItemResult(wid, status="done"))
        ))
        app = server.ItemServer(args)
        thread = threading.Thread(target=app.start, daemon=True)
        thread.start()

        def cleanup():
            app.stop()
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
        self.addCleanup(cleanup)
        self.app, self.root, self.thread = app, root, thread
        return app

    def request(self, method, path, body=None, *, auth=True, headers=None, raw=False):
        headers = dict(headers or {})
        if auth:
            headers["Authorization"] = "Bearer " + TEST_TOKEN
        data = None
        if method == "POST":
            headers.setdefault("Content-Type", "application/json")
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.app._server.server_address[1], timeout=3
        )
        try:
            connection.request(method, path, body=data, headers=headers)
            response = connection.getresponse()
            content = response.read()
            return response.status, content if raw else json.loads(content) if content else {}, dict(response.getheaders())
        finally:
            connection.close()

    def wait_for(self, predicate):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            _, state, _ = self.request("GET", "/status")
            if predicate(state):
                return state
            time.sleep(.02)
        self.fail("Timed out waiting for job state")

    def test_pairing_is_required_for_status_and_queue_mutation(self):
        self.start()
        self.assertEqual(self.request("GET", "/healthz", auth=False)[0], 200)
        self.assertEqual(self.request("GET", "/status", auth=False)[0], 401)
        self.assertEqual(self.request("GET", "/items")[0], 404)
        self.assertEqual(self.request("POST", "/add", {"inputs": [self.source]}, auth=False)[0], 401)
        self.assertEqual(self.app.queue.items, [])

    def test_companion_assets_are_public_but_contain_no_pairing_secrets(self):
        self.start()
        code, page, headers = self.request("GET", "/app/", auth=False, raw=True)
        self.assertEqual(code, 200)
        self.assertIn(b"Separation preset", page)
        self.assertIn(b'<link rel="icon" href="/app/jamly.svg" type="image/svg+xml">', page)
        self.assertNotIn(self.app._token.encode(), page)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertIn("img-src 'self'", headers["Content-Security-Policy"])
        icon_code, icon_bytes, icon_headers = self.request("GET", "/app/jamly.svg", auth=False, raw=True)
        self.assertEqual(icon_code, 200)
        self.assertEqual(icon_headers["Content-Type"], "image/svg+xml")
        self.assertIn(b"<svg", icon_bytes)
        self.assertEqual(self.request("GET", "/app/../../server-token", auth=False)[0], 404)
        self.assertEqual(self.request("POST", "/app/launch", {}, auth=False)[0], 401)

    def test_launcher_verifies_backend_and_queue_before_sending_credentials(self):
        self.start()
        received = []
        original = server.QueueStatusHandler._allowed_request

        def capture(handler, *args, **kwargs):
            received.append(handler.headers.get("Authorization"))
            return original(handler, *args, **kwargs)

        with patch.object(server.QueueStatusHandler, "_allowed_request", capture):
            with self.assertRaisesRegex(RuntimeError, "identity"):
                companion._connect(self.app.base_url, TEST_TOKEN, self.root / "different-queue.json")
            self.assertEqual(received, [None])
            received.clear()
            self.assertTrue(companion._connect(self.app.base_url, TEST_TOKEN, Path(self.app.queue_path)))
            self.assertEqual(received, [None])
            companion.request(self.app.base_url, TEST_TOKEN, "/app/launch", {})
            self.assertEqual(received, [None, "Bearer " + TEST_TOKEN])

    def test_launcher_pairs_through_a_private_single_use_file_not_a_url(self):
        self.start()
        code, launch, _ = self.request("POST", "/app/launch", {})
        self.assertEqual(code, 200)
        url = urlsplit(launch["launch_file"])
        self.assertEqual(url.query, "")
        self.assertEqual(url.fragment, "")
        path = Path(unquote(url.path))
        if sys.platform == "win32":
            path = Path(unquote(url.path).lstrip("/"))
        ticket = re.search(r'name="ticket" value="([^"]+)"', path.read_text()).group(1)
        if sys.platform != "win32":
            self.assertEqual(path.stat().st_mode & 0o077, 0)
        body = urlencode({"ticket": ticket}).encode()
        headers = {"Content-Type": "Application/X-WWW-Form-Urlencoded; charset=utf-8", "Origin": "null"}
        code, page, response_headers = self.request("POST", "/app/session", body, auth=False,
                                                   headers=headers, raw=True)
        self.assertEqual(code, 200)
        self.assertIn("nonce-", response_headers["Content-Security-Policy"])
        self.assertFalse(path.exists())
        token = re.search(rb'setItem\("jamly-token", "([^"]+)"\)', page).group(1).decode()
        self.assertNotEqual(token, self.app._token)
        self.assertEqual(self.request("GET", "/status", auth=False, headers={
            "Authorization": "Bearer " + token, "Origin": self.app.base_url,
        })[0], 200)
        # A 24-hour companion session cannot fetch the permanent master token or mint new launch files.
        ext_code, ext_payload, _ = self.request("POST", "/app/extension", {}, auth=False, headers={
            "Authorization": "Bearer " + token, "Origin": self.app.base_url,
        })
        self.assertEqual(ext_code, 200)
        self.assertNotIn("token", ext_payload)
        self.assertIn("token_command", ext_payload)
        self.assertNotIn(self.app._token, json.dumps(ext_payload))
        self.assertEqual(self.request("POST", "/app/launch", {}, auth=False, headers={
            "Authorization": "Bearer " + token, "Origin": self.app.base_url,
        })[0], 403)
        self.assertEqual(self.request("POST", "/app/session", body, auth=False, headers=headers)[0], 400)
        with patch("jamly.companion_ui.time.monotonic", return_value=time.monotonic() + 86401):
            self.assertEqual(self.request("GET", "/status", auth=False, headers={
                "Authorization": "Bearer " + token,
            })[0], 401)

    def test_settings_are_persistent_validated_and_do_not_change_queued_options(self):
        self.start()
        self.request("POST", "/add", {"inputs": [self.source]})
        self.wait_for(lambda state: state["done"] == 1)
        options = dict(self.app.queue.get(self.wid)["options"])
        settings = {"model": "htdemucs_ft", "audio_format": "wav", "output_dir": str(self.root / "other")}
        code, saved, _ = self.request("POST", "/settings", settings)
        self.assertEqual((code, saved), (200, settings))
        self.assertEqual(self.request("GET", "/status")[1]["defaults"], settings)
        self.assertEqual(self.app.queue.get(self.wid)["options"], options)
        args = config.parse_configured_args(server.build_parser(),
                                            ["--state-dir", str(self.root)], preferences=True)
        self.assertEqual(args.model, "htdemucs_ft")
        self.assertEqual(args.audio_format, "wav")
        for invalid in ({"output_dir": "../relative"}, {"model": "other"}, {"audio_format": None},
                        {"server_token": "do-not-change"}, {"output_dir": False}):
            self.assertEqual(self.request("POST", "/settings", invalid)[0], 400)
        with patch.object(server, "write_private_json", side_effect=OSError("disk full")):
            self.assertEqual(self.request("POST", "/settings", {"model": "htdemucs"})[0], 503)
        self.assertEqual(self.app.args.model, "htdemucs_ft")

    def test_unrelated_origin_or_host_is_rejected_even_with_token(self):
        with patch.object(server.socket, "getfqdn", side_effect=AssertionError("reverse DNS lookup must be skipped")):
            self.start()
        nonce = "ab" * 32
        for headers in ({"Origin": "https://unrelated.example"}, {"Host": "unrelated.example"},
                        {"Origin": "null"}, {"Host": "localhost:invalid"},
                        {"Origin": "http://127.0.0.2:%d" % self.app._server.server_address[1]},
                        {"Origin": "chrome-extension://[invalid"},
                        {"Origin": "chrome-extension://example"},
                        {"Origin": TEST_EXT_ORIGIN}):
            code, _, _ = self.request("POST", "/add", {"inputs": [self.source]}, headers=headers)
            self.assertEqual(code, 403)
        # Unpaired extension origins must not receive a /handshake proof either.
        for method in ("GET", "POST"):
            code, _, _ = self.request(method, "/handshake?nonce=" + nonce, b"" if method == "POST" else None,
                                      auth=False, headers={"Origin": TEST_EXT_ORIGIN})
            self.assertEqual(code, 403)
        # Early 403/401/415 rejections with multi-packet unread POST bodies must drain small bodies
        # cleanly rather than triggering TCP RST on Windows before the client reads the JSON error.
        large_payload = {"inputs": [self.source], "pad": "x" * 32768}
        for _ in range(5):
            code, body, _ = self.request("POST", "/add", large_payload, headers={"Origin": "https://unrelated.example"})
            self.assertEqual(code, 403)
            self.assertIn("error", body)
            code, body, _ = self.request("POST", "/add", large_payload, auth=False)
            self.assertEqual(code, 401)
            self.assertIn("error", body)
            code, body, _ = self.request("POST", "/add", b"x" * 32768, headers={"Content-Type": "text/plain"})
            self.assertEqual(code, 415)
            self.assertIn("error", body)
        # Oversized rejected requests (> 64 KiB) must return 403 and close immediately without draining.
        port = self.app._server.server_address[1]
        started = time.monotonic()
        with socket.create_connection(("127.0.0.1", port), timeout=3) as sock:
            sock.sendall((
                "POST /add HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{port}\r\n"
                "Origin: https://unrelated.example\r\n"
                "Content-Type: application/json\r\n"
                "Content-Length: 10000000\r\n\r\n"
            ).encode("ascii"))
            chunks = []
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
        self.assertTrue(b"".join(chunks).startswith(b"HTTP/1.1 403"))
        self.assertLess(time.monotonic() - started, 0.8)
        self.assertEqual(self.app.queue.items, [])

    def test_extension_preflight_and_handshake_require_explicit_allowed_origin(self):
        self.start()
        nonce = "cd" * 32
        # Unpaired extension origin is rejected on OPTIONS and /handshake.
        self.assertEqual(self.request("OPTIONS", "/add", auth=False, headers={
            "Origin": TEST_EXT_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        })[0], 403)
        self.assertEqual(self.request("POST", "/handshake?nonce=" + nonce, b"", auth=False, headers={
            "Origin": TEST_EXT_ORIGIN,
        })[0], 403)
        # Authorizing the exact extension origin via /app/extension enables preflight and handshake.
        code, setup, _ = self.request("POST", "/app/extension", {"allow_origin": TEST_EXT_ORIGIN})
        self.assertEqual(code, 200)
        self.assertEqual(setup["allowed_origins"], [TEST_EXT_ORIGIN])
        code, _, headers = self.request("OPTIONS", "/add", auth=False, headers={
            "Origin": TEST_EXT_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        })
        self.assertEqual(code, 204)
        self.assertEqual(headers["Access-Control-Allow-Origin"], TEST_EXT_ORIGIN)
        self.assertIn("Authorization", headers["Access-Control-Allow-Headers"])
        expected_proof = config.client_pairing_proof(
            TEST_TOKEN, nonce, self.app._server.server_address[1]
        )
        for method in ("GET", "POST"):
            hs_code, hs_body, _ = self.request(method, "/handshake?nonce=" + nonce,
                                               b"" if method == "POST" else None,
                                               auth=False, headers={"Origin": TEST_EXT_ORIGIN})
            self.assertEqual(hs_code, 200)
            self.assertEqual(hs_body["client_proof"], expected_proof)
        # POST /handshake rejects request bodies just like GET.
        bad_code, _, _ = self.request("POST", "/handshake?nonce=" + nonce, {"unexpected": True},
                                      auth=False, headers={"Origin": TEST_EXT_ORIGIN})
        self.assertEqual(bad_code, 400)

    def test_invalid_payloads_return_client_errors(self):
        self.start()
        for body in (None, [], {"inputs": "url"}, {"inputs": [3]}, {"inputs": []},
                     {"inputs": [self.source], "titles": []},
                     {"inputs": [self.source], "model": "untrusted"},
                     {"inputs": [self.source], "model": None},
                     {"inputs": [self.source], "model": []},
                     {"inputs": [self.source], "audio_format": "flac"},
                     {"inputs": [self.source], "replace_existing": "true"}, b"{invalid"):
            with self.subTest(body=body):
                code, payload, _ = self.request("POST", "/add", body)
                self.assertEqual(code, 400)
                self.assertIn("error", payload)
        self.assertEqual(self.app.queue.items, [])

    def test_selected_model_and_format_are_persisted_and_used_on_retry(self):
        seen = []

        def process(wid, args, **_kwargs):
            seen.append((args.model, args.audio_format))
            return ItemResult(wid, status="error", error="retry this job")

        self.start(process)
        code, _, _ = self.request("POST", "/add", {
            "inputs": [self.source], "model": "htdemucs_ft", "audio_format": "wav",
        })
        self.assertEqual(code, 202)
        self.wait_for(lambda state: state["failed"] == 1)
        self.assertEqual(WatchQueue.from_path(self.app.queue_path).get(self.wid)["options"]["model"], "htdemucs_ft")
        self.request("POST", "/retry/" + self.wid, {})
        state = self.wait_for(lambda state: state["failed"] == 1 and len(seen) == 2)
        self.assertEqual(seen, [("htdemucs_ft", "wav"), ("htdemucs_ft", "wav")])
        self.assertEqual(state["jobs"][0]["model"], "htdemucs_ft")

    def test_stage_progress_and_original_source_reach_worker(self):
        started, release = threading.Event(), threading.Event()
        seen = []

        def process(wid, args, *, progress, source_url, cancel_event):
            seen.append(source_url)
            progress(wid, "separate", 0, 0)
            started.set()
            release.wait(timeout=3)
            return ItemResult(wid, status="done")
        self.start(process)
        self.request("POST", "/add", {"inputs": [self.source]})
        try:
            self.assertTrue(started.wait(3))
            state = self.request("GET", "/status")[1]
            self.assertEqual(state["current"]["stage"], "separate")
            self.assertEqual(state["current"]["status"], "running")
        finally:
            release.set()
        state = self.wait_for(lambda state: state["done"] == 1)
        self.assertIsNone(state["current"])
        self.assertEqual(seen, [self.source])

    def test_local_source_audio_is_streamed_with_range_support_after_completion(self):
        with tempfile.TemporaryDirectory() as folder:
            source_path = Path(folder) / "track.wav"
            payload = b"WAVE\x00\x00\x00\x00jamly original audio bytes"
            source_path.write_bytes(payload)
            self.start(process=lambda wid, args, **kwargs: ItemResult(
                wid, status="done", source_path=str(source_path.resolve()),
            ))
            self.request("POST", "/add", {"inputs": [str(source_path)]})
            state = self.wait_for(lambda state: state["done"] == 1)
            job = state["jobs"][0]
            wid = job["watch_id"]
            self.assertTrue(job["has_original"])
            self.assertEqual(Path(job["source_url"]), source_path.resolve())

            code, body, headers = self.request("GET", "/source/" + unquote(wid), raw=True)
            self.assertEqual(code, 200)
            self.assertEqual(headers.get("Content-Type"), "audio/wav")
            self.assertEqual(headers.get("Accept-Ranges"), "bytes")
            self.assertEqual(body, payload)

            # Single byte Range request returns 206 Partial Content.
            code, part, headers = self.request(
                "GET", "/source/" + unquote(wid), headers={"Range": "bytes=0-7"}, raw=True,
            )
            self.assertEqual(code, 206)
            self.assertEqual(part, payload[:8])
            self.assertEqual(headers.get("Content-Range"), "bytes 0-7/%d" % len(payload))

            # Out-of-bounds Range returns 416.
            code, _, headers = self.request(
                "GET", "/source/" + unquote(wid), headers={"Range": "bytes=9999-"}, raw=True,
            )
            self.assertEqual(code, 416)
            self.assertEqual(headers.get("Content-Range"), "bytes */%d" % len(payload))

    def test_source_endpoint_never_reads_arbitrary_files_or_proxies_http(self):
        """DESIGN.md:113 / Defect 1: /source/<id> never proxies HTTP or serves unverified paths."""
        with tempfile.TemporaryDirectory() as folder:
            secret = Path(folder) / "secret.txt"
            secret.write_text("top-secret", encoding="utf-8")
            self.start(process=lambda wid, args, **kwargs: ItemResult(wid, status="done"))

            # Non-audio local file paths are rejected at enqueue.
            code, _, _ = self.request("POST", "/add", {"inputs": [str(secret)]})
            self.assertEqual(code, 400)

            # An HTTP source with no retained original_path returns 404 and never makes an HTTP request.
            self.request("POST", "/add", {"inputs": ["http://127.0.0.1:1/never-fetch"]})
            state = self.wait_for(lambda s: s["done"] == 1)
            http_wid = state["jobs"][0]["watch_id"]
            self.assertFalse(state["jobs"][0]["has_original"])
            code, _, _ = self.request("GET", "/source/" + http_wid)
            self.assertEqual(code, 404)

            # Even if original_path in a queue record were tampered to a non-audio or outside audio file, /source refuses it.
            external_m4a = Path(folder) / "private-voice-memo.m4a"
            external_m4a.write_bytes(b"ftypM4A-private")
            for tampered in (secret, external_m4a):
                with self.app._status_lock:
                    self.app.queue.finish(http_wid, "done", original_path=str(tampered))
                    self.app.queue.save()
                code, _, _ = self.request("GET", "/source/" + http_wid)
                self.assertEqual(code, 404)

    def test_error_survives_reload_and_explicit_retry_succeeds(self):
        attempts = []

        def process(wid, _args, **_kwargs):
            attempts.append(wid)
            return ItemResult(wid, status="error" if len(attempts) == 1 else "done",
                              error="temporary network failure" if len(attempts) == 1 else None)
        self.start(process)
        self.request("POST", "/add", {"inputs": [self.source]})
        state = self.wait_for(lambda state: state["failed"] == 1)
        self.assertEqual(state["done"], 0)
        loaded = WatchQueue.from_path(self.app.queue_path)
        self.assertEqual(loaded.get(self.wid)["status"], "error")
        self.assertEqual(loaded.pending_ids(), [])
        self.request("POST", "/retry/" + self.wid, {})
        self.wait_for(lambda state: state["done"] == 1)
        self.assertEqual(len(attempts), 2)

    def test_failed_persistence_does_not_enqueue_a_rejected_request(self):
        process = Mock(return_value=ItemResult(self.wid, status="done"))
        self.start(process)
        with patch.object(WatchQueue, "save", side_effect=OSError("disk full")):
            code, payload, _ = self.request("POST", "/add", {"inputs": [self.source]})
        self.assertEqual(code, 503)
        self.assertIn("disk full", payload["error"])
        self.assertEqual(self.app.queue.items, [])
        process.assert_not_called()

    def test_stop_cancels_worker_and_releases_queue_ownership(self):
        started = threading.Event()

        def process(wid, _args, *, cancel_event, **_kwargs):
            try:
                core.run([sys.executable, "-c",
                          "import time;print('ready',flush=True);time.sleep(60)"],
                         stream=True, on_line=lambda _line: started.set(), cancel_event=cancel_event)
            except InterruptedError:
                return ItemResult(wid, status="cancelled", error="stopped")
            return ItemResult(wid, status="done")
        self.start(process)
        self.request("POST", "/add", {"inputs": [self.source]})
        self.assertTrue(started.wait(3))
        with self.assertRaises(QueueError):
            server.ItemServer(self.app.args)
        self.app.stop()
        self.assertFalse(self.app._worker.is_alive())
        self.assertEqual(WatchQueue.from_path(self.app.queue_path).pending_ids(), [self.wid])
        replacement = server.ItemServer(self.app.args)
        replacement.stop()

    def test_stop_requeues_job_even_when_worker_exits_with_signal_error(self):
        """DESIGN.md:105 / Defect 4: simultaneous SIGTERM to backend and worker requeues as pending."""
        started = threading.Event()

        def process(wid, _args, *, cancel_event, **_kwargs):
            started.set()
            cancel_event.wait(timeout=5)
            return ItemResult(wid, status="error", error="Demucs failed: exit -15")

        self.start(process)
        self.request("POST", "/add", {"inputs": [self.source]})
        self.assertTrue(started.wait(3))
        self.app.stop()
        loaded = WatchQueue.from_path(self.app.queue_path)
        self.assertEqual(loaded.get(self.wid)["status"], "pending")
        self.assertIsNone(loaded.get(self.wid)["error"])

    def test_cancel_stops_running_and_queued_jobs_and_allows_retry(self):
        started = threading.Event()
        second_source = "https://youtu.be/9bZkp7q19f0"
        second_wid = "9bZkp7q19f0"
        seen = []

        def process(wid, _args, *, cancel_event, **_kwargs):
            seen.append(wid)
            if wid == self.wid and len(seen) == 1:
                try:
                    core.run([sys.executable, "-c",
                              "import time;print('ready',flush=True);time.sleep(60)"],
                             stream=True, on_line=lambda _line: started.set(), cancel_event=cancel_event)
                except InterruptedError:
                    return ItemResult(wid, status="cancelled", error="Processing was cancelled")
            return ItemResult(wid, status="done")

        self.start(process)
        self.request("POST", "/add", {"inputs": [self.source, second_source]})
        self.assertTrue(started.wait(3))

        code, payload, _ = self.request("POST", "/cancel/" + second_wid, {})
        self.assertEqual(code, 202)
        self.assertTrue(payload["cancelled"])

        code, payload, _ = self.request("POST", "/cancel/" + self.wid, {})
        self.assertEqual(code, 202)
        self.assertTrue(payload["cancelled"])

        state = self.wait_for(
            lambda s: s["current"] is None and all(j["status"] == "cancelled" for j in s["jobs"])
        )
        self.assertEqual(state["pending"], 0)
        self.assertEqual(state["failed"], 0)
        self.assertEqual(seen, [self.wid])
        self.assertIsNone(state["jobs"][0]["error"])
        self.assertIsNone(state["jobs"][1]["error"])

        code, payload, _ = self.request("POST", "/cancel/" + self.wid, {})
        self.assertEqual(code, 202)
        self.assertFalse(payload["cancelled"])

        code, _, _ = self.request("POST", "/cancel/missing-job", {})
        self.assertEqual(code, 404)

        self.request("POST", "/retry/" + self.wid, {})
        state = self.wait_for(lambda s: s["done"] == 1)
        self.assertEqual(seen, [self.wid, self.wid])
        self.assertEqual(state["pending"], 0)

    def test_user_cancel_marks_cancelled_even_if_worker_returns_error(self):
        started = threading.Event()

        def process(wid, _args, *, cancel_event, **_kwargs):
            started.set()
            cancel_event.wait(timeout=5)
            return ItemResult(wid, status="error", error="ffmpeg exited non-zero")

        self.start(process)
        self.request("POST", "/add", {"inputs": [self.source]})
        self.assertTrue(started.wait(3))
        code, payload, _ = self.request("POST", "/cancel/" + self.wid, {})
        self.assertEqual(code, 202)
        self.assertTrue(payload["cancelled"])
        state = self.wait_for(lambda s: s["current"] is None and s["jobs"][0]["status"] == "cancelled")
        self.assertEqual(state["failed"], 0)
        self.assertIsNone(state["jobs"][0]["error"])

    def test_artifacts_require_auth_and_matching_generation(self):
        def process(wid, args, **_kwargs):
            directory = Path(args.output_dir) / wid / args.audio_format
            directory.mkdir(parents=True)
            paths = {}
            for kind in ("drums", "no_drums"):
                path = directory / (kind + ".mp3")
                path.write_bytes(kind.encode())
                paths[kind] = path
            metadata = {
                "processing_version": PROCESSING_VERSION, "generation": "generation-one",
                "outputs": {kind: {"file": path.name, "sha256": checksum(path)}
                            for kind, path in paths.items()},
            }
            meta_path = directory / "metadata.json"
            meta_path.write_text(json.dumps(metadata))
            return ItemResult(wid, status="done",
                              stem_paths={k: str(v) for k, v in paths.items()},
                              meta_path=str(meta_path), generation="generation-one")
        self.start(process)
        self.request("POST", "/add", {"inputs": [self.source]})
        state = self.wait_for(lambda state: state["done"] == 1)
        output = next(item for item in state["jobs"][0]["outputs"] if item["kind"] == "drums")
        self.assertEqual(self.request("GET", output["url"], auth=False)[0], 401)
        code, body, _ = self.request("GET", output["url"], raw=True)
        self.assertEqual((code, body), (200, b"drums"))
        self.assertEqual(self.request("GET", output["url"].replace("generation-one", "stale"))[0], 409)
        self.assertEqual(self.request("GET", "/outputs/unknown-job/drums?generation=generation-one")[0], 404)
        self.assertEqual(self.request("GET", "/outputs/" + self.wid + "/../server-token")[0], 404)
        form_headers = {"Content-Type": "application/x-www-form-urlencoded", "Origin": self.app.base_url}
        retired_download = output["url"].replace("/outputs/", "/app/download/", 1)
        code, _, _ = self.request("POST", retired_download, urlencode({"token": "test-token"}).encode(),
                                  auth=False, headers=form_headers, raw=True)
        self.assertNotEqual(code, 200)

    def test_local_file_upload_retained_multi_preset_exports_and_cleanup_on_clear(self):
        """Defect 5: uploaded audio is deleted when its job is removed from the list."""
        def process(wid, args, *, source_url=None, **_kwargs):
            directory = Path(args.output_dir) / wid / args.model / args.audio_format
            directory.mkdir(parents=True, exist_ok=True)
            paths = {}
            for kind in ("drums", "no_drums"):
                path = directory / (kind + "." + args.audio_format)
                path.write_bytes((args.model + "-" + kind).encode())
                paths[kind] = path
            gen = "gen-" + args.model + "-" + args.audio_format
            metadata = {
                "processing_version": PROCESSING_VERSION, "generation": gen,
                "outputs": {kind: {"file": path.name, "sha256": checksum(path)}
                            for kind, path in paths.items()},
            }
            meta_path = directory / "metadata.json"
            meta_path.write_text(json.dumps(metadata))
            return ItemResult(wid, status="done",
                              stem_paths={k: str(v) for k, v in paths.items()},
                              meta_path=str(meta_path), generation=gen,
                              source_path=source_url)

        self.start(process)
        code, bad_upload, _ = self.request(
            "POST", "/app/upload?name=notes.txt", b"not-audio",
            headers={"Content-Type": "application/octet-stream"},
        )
        self.assertEqual(code, 400)
        self.assertIn("extension", bad_upload["error"])
        code, uploaded, _ = self.request(
            "POST", "/app/upload?name=Band%20-%20Track.wav", b"RIFF-audio-bytes",
            headers={"Content-Type": "audio/wav"},
        )
        self.assertEqual(code, 200)
        uploaded_path = Path(uploaded["source"])
        self.assertTrue(uploaded_path.is_file())
        # Re-uploading identical bytes reuses the existing content-addressed file cleanly.
        code2, uploaded2, _ = self.request(
            "POST", "/app/upload?name=Band%20-%20Track.wav", b"RIFF-audio-bytes",
            headers={"Content-Type": "audio/wav"},
        )
        self.assertEqual(code2, 200)
        self.assertEqual(uploaded2["source"], uploaded["source"])
        self.assertEqual(self.request("POST", "/add", {
            "inputs": [uploaded["source"]], "model": "htdemucs", "audio_format": "mp3",
        })[0], 202)
        first_state = self.wait_for(lambda state: state["done"] == 1)
        first_url = first_state["jobs"][0]["outputs"][0]["url"]
        self.assertEqual(self.request("POST", "/add", {
            "inputs": [uploaded["source"]], "model": "htdemucs_6s", "audio_format": "wav",
        })[0], 202)
        second_state = self.wait_for(
            lambda state: state["done"] == 1 and state["jobs"][0]["model"] == "htdemucs_6s"
        )
        job = second_state["jobs"][0]
        self.assertEqual(len(job["exports"]), 1)
        self.assertEqual(job["exports"][0]["model"], "htdemucs")
        self.assertEqual(self.request("GET", first_url, raw=True)[1], b"htdemucs-drums")
        second_url = next(item["url"] for item in job["outputs"] if item["kind"] == "drums")
        self.assertEqual(self.request("GET", second_url, raw=True)[1], b"htdemucs_6s-drums")

        # Removing the job cleans up the uploaded file (and its empty digest folder).
        code, cleared, _ = self.request("POST", "/app/clear", {"id": job["watch_id"]})
        self.assertEqual(code, 200)
        self.assertEqual(cleared["removed"], 1)
        self.assertFalse(uploaded_path.exists())
        self.assertFalse(uploaded_path.parent.exists())

    def test_orphaned_uploads_are_pruned_without_deleting_unrelated_files(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(
            os.environ, {"JAMLY_SERVER_TOKEN": TEST_TOKEN}
        ):
            state = Path(root)
            unrelated_png = state / "uploads" / "avatar.png"
            unrelated_pdf = state / "uploads" / "2024" / "invoices" / "march.pdf"
            unrelated_wav = state / "uploads" / "user-song.wav"
            unrelated_pdf.parent.mkdir(parents=True)
            unrelated_png.write_bytes(b"png")
            unrelated_pdf.write_bytes(b"pdf")
            unrelated_wav.write_bytes(b"RIFFuser")

            # Managed upload left orphaned after restart IS pruned; unrelated files are untouched.
            orphan_dir = state / "uploads" / "deadbeef01234567"
            orphan_dir.mkdir(parents=True)
            orphan_file = orphan_dir / "abandoned.wav"
            orphan_file.write_bytes(b"abandoned")
            config.write_private_json(
                state / "uploads-ledger.json",
                {"files": ["deadbeef01234567/abandoned.wav"]},
            )
            args = server.build_parser().parse_args([
                "--port", "0", "--queue", str(state / "queue.json"),
                "--state-dir", str(state),
                "--output-dir", str(state / "output"),
            ])
            app = server.ItemServer(args)
            try:
                self.assertFalse(orphan_file.exists())
                self.assertFalse(orphan_dir.exists())
                self.assertTrue(unrelated_png.is_file())
                self.assertTrue(unrelated_pdf.is_file())
                self.assertTrue(unrelated_wav.is_file())
                # Clearing a job referencing an unmanaged file inside uploads/ must NOT delete it.
                app.enqueue([str(unrelated_wav)])
                wid = app.queue.items[0]["watch_id"]
                with app._status_lock:
                    app.queue.finish(wid, "done", original_path=str(unrelated_wav))
                    app.queue.save()
                app.clear_jobs(wid)
                self.assertTrue(unrelated_wav.is_file())
            finally:
                app.stop()

    def test_queue_flag_alone_never_adopts_project_directory_as_state_dir(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(
            os.environ,
            {
                "JAMLY_SERVER_TOKEN": TEST_TOKEN,
                "JAMLY_STATE_DIR": str(Path(root) / "isolated-state"),
                "JAMLY_SCOPE": "",
                "JAMLY_QUEUE": "",
            },
        ):
            project = Path(root) / "my-project"
            (project / "uploads").mkdir(parents=True)
            avatar = project / "uploads" / "avatar.png"
            avatar.write_bytes(b"keep-me")
            args = server.build_parser().parse_args([
                "--port", "0", "--queue", str(project / "jamly_queue.json"),
                "--output-dir", str(Path(root) / "output"),
            ])
            app = server.ItemServer(args)
            try:
                self.assertEqual(app.state_dir.resolve(), (Path(root) / "isolated-state").resolve())
                self.assertTrue(avatar.is_file())
            finally:
                app.stop()

    def test_rescan_rejects_malformed_manifests_and_external_originals(self):
        self.start()
        out_root = self.root / "output"
        outside_m4a = self.root / "outside-secret.m4a"
        outside_m4a.write_bytes(b"secret-m4a")

        def write_manifest_case(wid, extra):
            meta_dir = out_root / ".jamly" / wid / "htdemucs" / "mp3"
            meta_dir.mkdir(parents=True, exist_ok=True)
            drums = out_root / f"{wid}_drums.mp3"
            nodrums = out_root / f"{wid}_no_drums.mp3"
            drums.write_bytes(b"drums")
            nodrums.write_bytes(b"nodrums")
            manifest = {
                "watch_id": wid,
                "source_url": "https://youtu.be/" + wid,
                "title": "Track",
                "processing_version": PROCESSING_VERSION,
                "generation": "gen-" + wid,
                "model": "htdemucs",
                "stem": "drums",
                "format": "mp3",
                "outputs": {
                    "drums": {"file": drums.name, "sha256": checksum(drums)},
                    "no_drums": {"file": nodrums.name, "sha256": checksum(nodrums)},
                },
            }
            manifest.update(extra)
            (meta_dir / "metadata.json").write_text(json.dumps(manifest), encoding="utf-8")

        write_manifest_case("badSource01", {"source_url": 123})
        write_manifest_case("extOrig0001", {"original": str(outside_m4a)})

        code, rescanned, _ = self.request("POST", "/app/rescan", {})
        self.assertEqual(code, 200)
        self.assertEqual(rescanned["restored"], 1)
        # Queue reloads cleanly (badSource01 was skipped).
        reloaded = WatchQueue.from_path(self.app.queue_path)
        self.assertIsNone(reloaded.get("badSource01"))
        # extOrig0001 restored without outside original; /source returns 404.
        restored = reloaded.get("extOrig0001")
        self.assertIsNotNone(restored)
        self.assertIsNone(restored["original_path"])
        self.assertEqual(self.request("GET", "/source/extOrig0001")[0], 404)

    def test_stream_errors_do_not_corrupt_body_and_get_bodies_are_rejected(self):
        def process(wid, args, **_kwargs):
            directory = Path(args.output_dir) / ".jamly" / wid / args.model / args.audio_format
            directory.mkdir(parents=True, exist_ok=True)
            audio_root = Path(args.output_dir)
            drums = audio_root / f"{wid}_drums.mp3"
            nodrums = audio_root / f"{wid}_no_drums.mp3"
            drums.write_bytes(b"A" * (200 * 1024))
            nodrums.write_bytes(b"B" * 1024)
            manifest = {
                "watch_id": wid, "processing_version": PROCESSING_VERSION,
                "generation": "gen-stream", "model": args.model, "stem": args.stem,
                "format": args.audio_format,
                "outputs": {
                    "drums": {"file": drums.name, "sha256": checksum(drums)},
                    "no_drums": {"file": nodrums.name, "sha256": checksum(nodrums)},
                },
            }
            meta = directory / "metadata.json"
            meta.write_text(json.dumps(manifest), encoding="utf-8")
            return ItemResult(
                wid, status="done",
                stem_paths={"drums": str(drums), "no_drums": str(nodrums)},
                meta_path=str(meta), generation="gen-stream",
            )

        self.start(process)
        self.request("POST", "/add", {"inputs": [self.source]})
        state = self.wait_for(lambda s: s["done"] == 1)
        drums_url = next(o["url"] for o in state["jobs"][0]["outputs"] if o["kind"] == "drums")
        port = self.app._server.server_address[1]

        # 1. GET with a body containing a smuggled second request produces a single 400 and closes.
        smuggled = b"GET /healthz HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n"
        raw_req = (
            b"GET /healthz HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n"
            + f"Content-Length: {len(smuggled)}\r\n\r\n".encode("ascii")
            + smuggled
        )
        with socket.create_connection(("127.0.0.1", port), timeout=3) as sock:
            sock.sendall(raw_req)
            chunks = []
            while True:
                data = sock.recv(4096)
                if not data:
                    break
                chunks.append(data)
        raw_resp = b"".join(chunks)
        self.assertEqual(raw_resp.count(b"HTTP/1.1 "), 1)
        self.assertTrue(raw_resp.startswith(b"HTTP/1.1 400"))

        # 2. Export lock is released before streaming begins (slow reader does not hold the lock).
        meta_dir = Path(self.app.queue.get(self.wid)["outputs"]["metadata"]).parent
        lock_path = meta_dir.parent / ("." + meta_dir.name + ".lock")
        with socket.create_connection(("127.0.0.1", port), timeout=3) as sock:
            sock.sendall((
                f"GET {drums_url} HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{port}\r\n"
                f"Authorization: Bearer {TEST_TOKEN}\r\n\r\n"
            ).encode("ascii"))
            head = sock.recv(256)
            self.assertTrue(head.startswith(b"HTTP/1.1 200"))
            # Lock must be immediately acquirable even while the client has stopped reading.
            with FileLock(str(lock_path), timeout=0):
                pass

        # 3. A mid-stream read error after headers does NOT inject an HTTP 404 response into the body.
        real_stream = server.QueueStatusHandler._stream_open_file

        class FailingReader:
            def __init__(self, inner):
                self._inner = inner
                self._reads = 0

            def fileno(self):
                return self._inner.fileno()

            def seek(self, offset):
                return self._inner.seek(offset)

            def read(self, size):
                self._reads += 1
                if self._reads > 1:
                    raise OSError("simulated disk read failure")
                return self._inner.read(size)

            def close(self):
                self._inner.close()

        def wrap_stream(handler, file, *args, **kwargs):
            return real_stream(handler, FailingReader(file), *args, **kwargs)

        with patch.object(server.QueueStatusHandler, "_stream_open_file", wrap_stream):
            with socket.create_connection(("127.0.0.1", port), timeout=3) as sock:
                sock.sendall((
                    f"GET {drums_url} HTTP/1.1\r\n"
                    f"Host: 127.0.0.1:{port}\r\n"
                    f"Authorization: Bearer {TEST_TOKEN}\r\n\r\n"
                ).encode("ascii"))
                chunks = []
                while True:
                    data = sock.recv(8192)
                    if not data:
                        break
                    chunks.append(data)
        partial = b"".join(chunks)
        self.assertTrue(partial.startswith(b"HTTP/1.1 200"))
        self.assertNotIn(b"HTTP/1.1 404", partial)

    def test_clear_and_rescan_exports_and_open_source_folder(self):
        def process(wid, args, **_kwargs):
            meta_dir = Path(args.output_dir) / ".jamly" / wid / args.model / args.audio_format
            meta_dir.mkdir(parents=True, exist_ok=True)
            audio_dir = Path(args.output_dir)
            audio_dir.mkdir(parents=True, exist_ok=True)
            paths = {}
            for kind in ("drums", "no_drums"):
                path = audio_dir / f"{wid}_{kind}.{args.audio_format}"
                path.write_bytes(kind.encode())
                paths[kind] = path
            original = audio_dir / f"{wid}_original.{args.audio_format}"
            original.write_bytes(b"retained-original")
            metadata = {
                "watch_id": wid,
                "source_url": self.source,
                "original": str(original),
                "title": "Test Track",
                "processing_version": PROCESSING_VERSION,
                "generation": "gen-rescan",
                "model": args.model,
                "stem": args.stem,
                "format": args.audio_format,
                "outputs": {kind: {"file": path.name, "sha256": checksum(path)}
                            for kind, path in paths.items()},
            }
            meta_path = meta_dir / "metadata.json"
            meta_path.write_text(json.dumps(metadata), encoding="utf-8")
            return ItemResult(wid, status="done",
                              stem_paths={k: str(v) for k, v in paths.items()},
                              meta_path=str(meta_path), generation="gen-rescan",
                              source_path=str(original))

        self.start(process)
        output_root = self.root / "output"
        self.request("POST", "/add", {"inputs": [self.source]})
        done_state = self.wait_for(lambda state: state["done"] == 1)
        done_job = done_state["jobs"][0]
        self.assertIsInstance(done_job["requested_at"], float)
        self.assertIsInstance(done_job["started_at"], float)
        self.assertIsInstance(done_job["finished_at"], float)
        self.assertIsInstance(done_job["duration_seconds"], float)
        self.assertGreaterEqual(done_job["duration_seconds"], 0.0)

        with patch.object(server, "open_directory") as mocked_open:
            code, _, _ = self.request("POST", "/app/open-folder", {"id": self.wid, "target": "source"})
            self.assertEqual(code, 400)
            mocked_open.assert_not_called()

        code, cleared, _ = self.request("POST", "/app/clear", {"id": self.wid})
        self.assertEqual(code, 200)
        self.assertEqual(cleared["removed"], 1)
        self.assertEqual(self.request("GET", "/status")[1]["jobs"], [])
        self.assertEqual(self.request("POST", "/app/clear", {"id": self.wid})[0], 404)

        code, rescanned, _ = self.request("POST", "/app/rescan", {})
        self.assertEqual(code, 200)
        self.assertEqual(rescanned["restored"], 1)
        state = self.request("GET", "/status")[1]
        self.assertEqual(len(state["jobs"]), 1)
        self.assertEqual(state["jobs"][0]["watch_id"], self.wid)
        self.assertEqual(state["jobs"][0]["title"], "Test Track")
        self.assertEqual(state["jobs"][0]["source_url"], self.source)
        self.assertTrue(state["jobs"][0]["has_original"])
        self.assertEqual(self.request("GET", "/source/" + self.wid, raw=True)[1], b"retained-original")
        self.assertTrue((output_root / f"{self.wid}_drums.mp3").is_file())

        code, bulk_cleared, _ = self.request("POST", "/app/clear", {})
        self.assertEqual(code, 200)
        self.assertEqual(bulk_cleared["removed"], 1)
        self.assertEqual(self.request("GET", "/status")[1]["jobs"], [])

        local_file = self.root / "local-demo.wav"
        local_file.write_bytes(b"RIFFdemo")
        self.request("POST", "/add", {"inputs": [str(local_file)]})
        local_state = self.wait_for(lambda s: s["done"] == 1)
        local_wid = local_state["jobs"][0]["watch_id"]
        with patch.object(server, "open_directory") as mocked_open:
            code, _, _ = self.request("POST", "/app/open-folder", {"id": local_wid, "target": "source"})
            self.assertEqual(code, 200)
            mocked_open.assert_called_once_with(local_file.parent.resolve())
        # Clearing a job with a user-supplied local file outside uploads/ must NOT delete it.
        self.request("POST", "/app/clear", {"id": local_wid})
        self.assertTrue(local_file.is_file())

    def test_status_search_filter_and_pagination_for_large_collections(self):
        def process(wid, args, **_kwargs):
            if wid == "track-err":
                return ItemResult(wid, status="error", error="broken audio")
            return ItemResult(wid, status="done", title="Song " + wid)

        self.start(process)
        entries = []
        for idx in range(30):
            wid = f"track-{idx:03d}"
            opts = {
                "model": "htdemucs_6s" if idx % 3 == 0 else "htdemucs",
                "stem": "drums",
                "audio_format": "wav" if idx % 2 == 0 else "mp3",
            }
            entries.append((wid, f"Song {idx:03d} {'Jazz' if idx % 5 == 0 else 'Rock'}", opts))
        with self.app._status_lock:
            for idx, (wid, title, opts) in enumerate(entries):
                self.app.queue.add(wid, title=title, source_url="https://youtu.be/" + wid, options=opts)
                if idx == 0:
                    self.app.queue.finish(wid, "error", error="broken audio")
                elif idx == 1:
                    self.app.queue.finish(wid, "cancelled")
                else:
                    self.app.queue.finish(wid, "done")
            self.app.queue.save()

        code, page1, _ = self.request("GET", "/status?page=1&page_size=10")
        self.assertEqual(code, 200)
        self.assertEqual(len(page1["jobs"]), 10)
        self.assertEqual(page1["pagination"], {
            "page": 1, "page_size": 10, "total_matched": 30, "total_pages": 3,
        })

        code, page3, _ = self.request("GET", "/status?page=99&page_size=10")
        self.assertEqual(code, 200)
        self.assertEqual(page3["pagination"]["page"], 3)
        self.assertEqual(len(page3["jobs"]), 10)

        code, jazz, _ = self.request("GET", "/status?q=jazz&model=htdemucs_6s&format=wav&page=1&page_size=25")
        self.assertEqual(code, 200)
        self.assertTrue(all("Jazz" in job["title"] for job in jazz["jobs"]))
        self.assertTrue(all(job["model"] == "htdemucs_6s" and job["audio_format"] == "wav" for job in jazz["jobs"]))

        code, errs, _ = self.request("GET", "/status?status=error&page=1&page_size=25")
        self.assertEqual(code, 200)
        self.assertEqual([job["watch_id"] for job in errs["jobs"]], ["track-000"])

        self.assertEqual(self.request("GET", "/status?status=bogus")[0], 400)

    def test_original_retained_separately_and_retry_uses_canonical_url(self):
        """Defects 1 & 2: URL jobs keep canonical source_url, store retained original separately, and retry the URL."""
        wid = self.wid
        source = self.source
        seen_sources = []

        def process(wid, args, *, source_url=None, **_kwargs):
            seen_sources.append(source_url)
            fmt = args.audio_format
            output_root = Path(args.output_dir)
            output_root.mkdir(parents=True, exist_ok=True)
            meta_dir = output_root / ".jamly" / wid / args.model / fmt
            meta_dir.mkdir(parents=True, exist_ok=True)
            drum = output_root / f"{wid}_drums.{fmt}"
            nodrums = output_root / f"{wid}_no_drums.{fmt}"
            drum.write_bytes(b"drums")
            nodrums.write_bytes(b"no-drums")
            original = output_root / f"{wid}_original.{fmt}"
            original.write_bytes(b"original-audio")
            metadata = {
                "watch_id": wid,
                "source_url": source,
                "original": str(original),
                "title": "Test Track",
                "processing_version": PROCESSING_VERSION,
                "generation": "gen-retained-%d" % len(seen_sources),
                "model": args.model,
                "stem": args.stem,
                "format": fmt,
                "outputs": {
                    "drums": {"file": drum.name, "sha256": checksum(drum)},
                    "no_drums": {"file": nodrums.name, "sha256": checksum(nodrums)},
                },
            }
            (meta_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
            return ItemResult(
                wid, status="done",
                stem_paths={"drums": str(drum), "no_drums": str(nodrums)},
                meta_path=str(meta_dir / "metadata.json"),
                generation=metadata["generation"], source_path=str(original),
            )

        self.start(process)
        output_root = self.root / "output"
        self.request("POST", "/add", {"inputs": [source]})
        done = self.wait_for(lambda _s: self.app.queue.get(wid, {}).get("status") == "done")

        retained = output_root / f"{wid}_original.mp3"
        self.assertTrue(retained.is_file(), "retained original missing next to the stems")
        self.assertEqual(retained.read_bytes(), b"original-audio")

        # source_url remains canonical; original_path holds the retained copy; API exposes has_original=True.
        item = self.app.queue.get(wid)
        self.assertEqual(item["source_url"], source)
        self.assertEqual(Path(item["original_path"]), retained)
        self.assertEqual(done["jobs"][0]["source_url"], source)
        self.assertTrue(done["jobs"][0]["has_original"])

        # /source/{watch_id} streams the retained original's exact bytes.
        code, body, headers = self.request("GET", "/source/" + wid, raw=True)
        self.assertEqual(code, 200)
        self.assertTrue(headers["Content-Type"].startswith("audio/"))
        self.assertEqual(body, b"original-audio")

        # Retrying passes the canonical remote URL, NOT the retained local copy.
        self.request("POST", "/retry/" + wid, {"replace_existing": True})
        self.wait_for(lambda _s: len(seen_sources) == 2 and self.app.queue.get(wid, {}).get("status") == "done")
        self.assertEqual(seen_sources, [source, source])

    def test_url_job_still_streams_stems_independently_of_original(self):
        wid = self.wid

        def process(wid, args, **_kwargs):
            fmt = args.audio_format
            output_root = Path(args.output_dir)
            output_root.mkdir(parents=True, exist_ok=True)
            meta_dir = output_root / ".jamly" / wid / args.model / fmt
            meta_dir.mkdir(parents=True, exist_ok=True)
            drum = output_root / f"{wid}_drums.{fmt}"
            nodrums = output_root / f"{wid}_no_drums.{fmt}"
            original = output_root / f"{wid}_original.{fmt}"
            drum.write_bytes(b"drums")
            nodrums.write_bytes(b"no-drums")
            original.write_bytes(b"original-audio")
            metadata = {
                "watch_id": wid, "source_url": self.source, "original": str(original),
                "title": "Test Track", "processing_version": PROCESSING_VERSION,
                "generation": "gen-stems", "model": args.model, "stem": args.stem, "format": fmt,
                "outputs": {"drums": {"file": drum.name, "sha256": checksum(drum)},
                            "no_drums": {"file": nodrums.name, "sha256": checksum(nodrums)}},
            }
            meta_path = meta_dir / "metadata.json"
            meta_path.write_text(json.dumps(metadata), encoding="utf-8")
            return ItemResult(
                wid, status="done",
                stem_paths={"drums": str(drum), "no_drums": str(nodrums)},
                meta_path=str(meta_path), generation="gen-stems", source_path=str(original),
            )

        self.start(process)
        self.request("POST", "/add", {"inputs": [self.source]})
        done = self.wait_for(lambda _s: self.app.queue.get(wid, {}).get("status") == "done")
        drums_url = next(item["url"] for item in done["jobs"][0]["outputs"] if item["kind"] == "drums")
        code, body, _ = self.request("GET", drums_url, raw=True)
        self.assertEqual(code, 200)
        self.assertEqual(body, b"drums")


class HostTests(unittest.TestCase):
    def test_only_real_loopback_hosts_are_trusted(self):
        for host in ("127.0.0.1", "localhost", "::1"):
            self.assertTrue(server._is_loopback_host(host))
        for host in ("", "0.0.0.0", "evil.example", "127.0.0.1.evil.example"):
            self.assertFalse(server._is_loopback_host(host))

    def test_non_loopback_binds_and_allow_host_are_rejected(self):
        with self.assertRaises(SystemExit):
            server.build_parser().parse_args(["--allow-host", "proxy.example"])
        with tempfile.TemporaryDirectory() as root:
            state = Path(root)
            for host in ("0.0.0.0", "evil.example", ""):
                args = server.build_parser().parse_args([
                    "--host", host, "--port", "0",
                    "--queue", str(state / "queue.json"),
                    "--state-dir", str(state),
                    "--output-dir", str(state / "output"),
                ])
                with self.subTest(host=host), self.assertRaisesRegex(ValueError, "loopback"):
                    server.ItemServer(args)

    def test_cli_token_flag_removed_and_short_tokens_rejected(self):
        with self.assertRaises(SystemExit):
            server.build_parser().parse_args(["--server-token", TEST_TOKEN])
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {"JAMLY_SERVER_TOKEN": "a"}):
            state = Path(root)
            args = server.build_parser().parse_args([
                "--port", "0", "--queue", str(state / "queue.json"),
                "--state-dir", str(state), "--output-dir", str(state / "output"),
            ])
            with self.assertRaisesRegex(ValueError, "at least"):
                server.ItemServer(args)
