"""HTTP request handler and Range-capable file streaming for the local Jamly backend."""
from __future__ import annotations

import hashlib
from http.server import BaseHTTPRequestHandler
import ipaddress
import json
import mimetypes
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
from urllib.parse import parse_qs, quote, unquote, urlsplit

from filelock import FileLock, Timeout

from . import __version__
from .companion_ui import CSP
from .config import API_VERSION, client_pairing_proof, pairing_proof, token_command
from .core import sanitize_filename
from .models import ALL_OUTPUT_KINDS
from .normalize import InputError, LOCAL_AUDIO_EXTENSIONS, local_audio_path, validate_local_audio_suffix
from .outputs import checksum, read_manifest, resolve_output_path
from .watch_queue import QueueError

_STREAM_CHUNK_SIZE = 65536
_STREAM_WRITE_TIMEOUT = 30


def _is_loopback_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _audio_content_type(name: str, default: str = "audio/mpeg") -> str:
    content_type = mimetypes.guess_type(name)[0] or default
    # Windows and Linux disagree on the WAV MIME type (audio/wav vs audio/x-wav); serve
    # the canonical value so clients get a consistent Content-Type regardless of host.
    if content_type == "audio/x-wav":
        return "audio/wav"
    return content_type


def _parse_range(header: str, size: int) -> tuple[int, int] | None:
    """Parse a single HTTP byte Range header; returns (start, end) inclusive, or None if invalid."""
    if not header or size <= 0:
        return None
    spec = header.strip()
    if not spec.lower().startswith("bytes="):
        return None
    range_part = spec[6:].strip()
    if "," in range_part or "-" not in range_part:
        return None
    start_str, end_str = range_part.split("-", 1)
    start_str, end_str = start_str.strip(), end_str.strip()
    try:
        if not start_str:
            suffix_len = int(end_str)
            if suffix_len <= 0:
                return None
            return max(0, size - suffix_len), size - 1
        start = int(start_str)
        end = size - 1 if not end_str else min(int(end_str), size - 1)
    except ValueError:
        return None
    if start < 0 or start >= size or end < start:
        return None
    return start, end


def _resolve_target_outputs(item: dict, generation: str | None) -> dict | None:
    if not generation:
        return None
    if item["status"] in ("done", "skipped") and generation == item.get("generation"):
        return item.get("outputs")
    for exp in (item.get("exports") or {}).values():
        if exp.get("generation") == generation:
            return exp.get("outputs")
    return None


class QueueStatusHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    @property
    def app(self):
        return self.server.item_server

    def log_message(self, fmt, *args):
        pass

    def _media_type(self) -> str:
        return self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()

    def _is_loopback_origin(self, parsed) -> bool:
        if not (self.app.ui.enabled and parsed.scheme == "http" and not parsed.username
                and parsed.path in ("", "/") and not parsed.query and not parsed.fragment):
            return False
        try:
            requested = urlsplit("//" + self.headers.get("Host", ""))
        except ValueError:
            return False
        port = self.app._server.server_address[1]
        return (
            parsed.hostname == requested.hostname
            and _is_loopback_host(parsed.hostname or "")
            and (parsed.port or 80) == port
            and (requested.port or 80) == port
        )

    def _allowed_origin(self, origin: str) -> bool:
        if origin in self.app.allowed_origins():
            return True
        try:
            parsed = urlsplit(origin)
        except ValueError:
            return False
        return self._is_loopback_origin(parsed)

    def _reject_unexpected_body(self) -> bool:
        if self.headers.get("Transfer-Encoding"):
            self.close_connection = True
            self._json({"error": "request body is not allowed"}, 400)
            return True
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) > 1 or (len(lengths) == 1 and lengths[0].strip() != "0"):
            self.close_connection = True
            self._json({"error": "request body is not allowed"}, 400)
            return True
        return False

    def _parse_content_length(self, *, max_bytes: int, allow_zero: bool = True) -> int | None:
        if self.headers.get("Transfer-Encoding"):
            return None
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) > 1:
            return None
        raw = lengths[0] if lengths else "0"
        try:
            length = int(raw)
        except ValueError:
            return None
        min_bytes = 0 if allow_zero else 1
        if length < min_bytes or length > max_bytes:
            return None
        return length

    def _cors(self):
        origin = self.headers.get("Origin")
        if origin and self._allowed_origin(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def _json(self, body, status=200):
        self._bytes(json.dumps(body).encode("utf-8"), "application/json", status)

    def _bytes(self, data, content_type, status=200, *, policy=None, disposition=None):
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        if disposition:
            self.send_header("Content-Disposition", disposition)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        # Native same-origin POST downloads need their Origin header; no-referrer makes it "null".
        self.send_header("Referrer-Policy", "same-origin" if policy else "no-referrer")
        if policy:
            self.send_header("Content-Security-Policy", policy)
            self.send_header("X-Frame-Options", "DENY")
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def _stream_open_file(self, file, content_type: str, *, disposition: str | None = None,
                          referrer_policy: str | None = None) -> None:
        """Stream an open binary file with HTTP byte-Range support, marking _headers_sent before writing."""
        range_header = self.headers.get("Range")
        size = os.fstat(file.fileno()).st_size
        start, end = 0, size - 1
        status = 200
        if range_header is not None:
            parsed = _parse_range(range_header, size)
            if parsed is None:
                self._headers_sent = True
                self.send_response(416)
                self._cors()
                self.send_header("Content-Range", "bytes */%d" % size)
                self.send_header("Content-Length", "0")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            start, end = parsed
            status = 206
        length = max(0, end - start + 1) if size else 0
        if start:
            file.seek(start)
        self.connection.settimeout(_STREAM_WRITE_TIMEOUT)
        self._headers_sent = True
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if status == 206:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        if disposition:
            self.send_header("Content-Disposition", disposition)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if referrer_policy:
            self.send_header("Referrer-Policy", referrer_policy)
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        remaining = length
        while remaining > 0:
            chunk = file.read(min(_STREAM_CHUNK_SIZE, remaining))
            if not chunk:
                self.close_connection = True
                break
            self.wfile.write(chunk)
            remaining -= len(chunk)

    def _stream_file(self, path: Path, content_type: str, *, disposition: str | None = None,
                     referrer_policy: str | None = None) -> None:
        with path.open("rb") as file:
            self._stream_open_file(
                file, content_type, disposition=disposition, referrer_policy=referrer_policy,
            )

    def _allowed_request(self, auth=True, *, bootstrap=False):
        allowed = {"localhost", "127.0.0.1", "::1", self.app.args.host.lower(),
                   *[host.lower() for host in self.app.args.allow_host]}
        try:
            host = urlsplit("//" + self.headers.get("Host", ""))
            valid_host = (host.hostname in allowed and not host.username and not host.path
                          and not host.query and not host.fragment and (host.port is None or host.port > 0))
        except ValueError:
            valid_host = False
        origin = self.headers.get("Origin")
        origin_ok = not origin or self._allowed_origin(origin) or (bootstrap and origin == "null")
        if not valid_host or not origin_ok:
            self.close_connection = True
            self._json({"error": "Host or browser origin is not allowed"}, 403)
            return False
        if auth and not self.app.authorized(self.headers.get("Authorization", "")):
            self.close_connection = True
            self._json({"error": "Session expired or client not paired. Reopen Jamly, or pair the extension "
                        "with the token from the companion's extension setup."}, 401)
            return False
        return True

    def do_OPTIONS(self):
        if self._reject_unexpected_body():
            return
        if not self._allowed_request(auth=False):
            return
        self.send_response(204)
        self._cors()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, Range")
        if self.headers.get("Access-Control-Request-Private-Network") == "true":
            self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if self._reject_unexpected_body():
            return
        url = urlsplit(self.path)
        public = url.path in ("/", "/app", "/healthz", "/handshake") or url.path.startswith("/app/")
        if not self._allowed_request(auth=not public):
            return
        if url.path in ("/", "/app"):
            self.send_response(303)
            self.send_header("Location", "/app/")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif url.path.startswith("/app/"):
            try:
                data, kind = self.app.ui.asset(url.path)
                self._bytes(data, kind, policy=CSP)
            except OSError:
                self._json({"error": "Companion asset is missing; reinstall Jamly or check the URL"}, 404)
        elif url.path == "/handshake":
            self._get_handshake(url)
        elif url.path == "/healthz":
            self._json({"ok": not bool(self.app._error), "version": __version__,
                        "api_version": API_VERSION, "authentication_required": True})
        elif url.path == "/status":
            try:
                query = parse_qs(url.query, keep_blank_values=False) if url.query else None
                self._json(self.app.snapshot(query))
            except ValueError as exc:
                self._json({"error": str(exc)}, 400)
        elif url.path.startswith("/source/"):
            self._source(url)
        elif url.path.startswith("/outputs/"):
            self._output(url)
        else:
            self._json({"error": "not found"}, 404)

    def _get_handshake(self, url):
        try:
            values = parse_qs(url.query, strict_parsing=True, max_num_fields=1)
            nonce = values.get("nonce", [""])[0]
            if set(values) != {"nonce"} or not re.fullmatch(r"[a-f0-9]{64}", nonce):
                raise ValueError("A 32-byte hexadecimal challenge is required")
            payload = {
                "api_version": API_VERSION,
                "client_proof": client_pairing_proof(
                    self.app._token, nonce, self.app._server.server_address[1],
                ),
            }
            if self.app.ui.enabled:
                payload["proof"] = pairing_proof(
                    self.app._token, nonce, self.app.base_url, Path(self.app.queue_path),
                )
            self._json(payload)
        except ValueError as exc:
            self._json({"error": str(exc)}, 400)

    def _output(self, url):
        parts = url.path.split("/")
        if len(parts) != 4 or parts[3] not in ALL_OUTPUT_KINDS:
            self._json({"error": "unknown output"}, 404)
            return
        with self.app._status_lock:
            item = self.app.queue.get(unquote(parts[2]))
        if not item:
            self._json({"error": "unknown job"}, 404)
            return
        if item["status"] not in ("done", "skipped") and not item.get("exports"):
            self._json({"error": "outputs are not ready"}, 409)
            return
        generation = parse_qs(url.query).get("generation", [None])[0]
        target_outputs = _resolve_target_outputs(item, generation)
        if not target_outputs or not target_outputs.get("metadata"):
            self._json({"error": "output generation changed; refresh the job"}, 409)
            return
        self._headers_sent = False
        opened = None
        meta_bytes = None
        disposition = None
        content_type = None
        try:
            directory = Path(target_outputs["metadata"]).parent
            with FileLock(str(directory.parent / ("." + directory.name + ".lock")), timeout=0):
                manifest = read_manifest(directory, verify_hashes=False)
                if manifest["generation"] != generation:
                    self._json({"error": "output generation changed; refresh the job"}, 409)
                    return
                kind = parts[3]
                name = "metadata.json" if kind == "metadata" else manifest["outputs"][kind]["file"]
                path = directory / name if kind == "metadata" else resolve_output_path(directory, name)
                content_type = "application/json" if kind == "metadata" else _audio_content_type(
                    name, default="application/octet-stream"
                )
                disposition = "attachment; filename*=UTF-8''" + quote(name)
                if kind == "metadata":
                    meta_bytes = path.read_bytes()
                else:
                    opened = path.open("rb")
            if meta_bytes is not None:
                self._headers_sent = True
                self._bytes(meta_bytes, content_type, disposition=disposition)
            else:
                self._stream_open_file(opened, content_type, disposition=disposition)
        except Timeout:
            self._json({"error": "outputs are being replaced; try again shortly"}, 409)
        except (OSError, ValueError, KeyError) as exc:
            if self._headers_sent:
                self.close_connection = True
                raise
            self._json({"error": "output is unavailable: %s" % exc}, 404)
        finally:
            if opened is not None:
                opened.close()

    def _source(self, url):
        parts = url.path.split("/")
        if len(parts) != 3 or not parts[2]:
            self._json({"error": "unknown source"}, 404)
            return
        watch_id = unquote(parts[2])
        with self.app._status_lock:
            item = self.app.queue.get(watch_id)
        if not item:
            self._json({"error": "source is unavailable for this job"}, 404)
            return
        original = (item.get("original_path") or "").strip()
        if not original:
            self._json({"error": "no retained source audio available"}, 404)
            return
        path = Path(original)
        if path.is_symlink() or not path.is_file() or path.suffix.lower() not in LOCAL_AUDIO_EXTENSIONS:
            self._json({"error": "source file is unavailable"}, 404)
            return
        try:
            resolved = path.resolve()
        except OSError:
            self._json({"error": "source file is unavailable"}, 404)
            return
        job_output_dir = Path((item.get("options") or {}).get("output_dir") or self.app.args.output_dir).resolve()
        in_output_dir = resolved.is_relative_to(job_output_dir)
        if not self.app.ui.enabled:
            if not in_output_dir:
                self._json({"error": "source file is unavailable on non-loopback binds"}, 403)
                return
        elif not in_output_dir:
            try:
                expected_local = local_audio_path(item.get("source_url"))
            except InputError:
                expected_local = None
            if expected_local is None or resolved != expected_local:
                self._json({"error": "source file is unavailable"}, 404)
                return
        self._headers_sent = False
        try:
            self._stream_file(
                resolved, _audio_content_type(resolved.name), referrer_policy="same-origin"
            )
        except (OSError, ValueError) as exc:
            if self._headers_sent:
                self.close_connection = True
                raise
            self._json({"error": "source is unavailable: %s" % exc}, 404)

    def _read_json_payload(self) -> dict | None:
        length = self._parse_content_length(max_bytes=1024 * 1024, allow_zero=True)
        if length is None:
            self.close_connection = True
            self._json({"error": "invalid or oversized request body"}, 400)
            return None
        if self._media_type() != "application/json":
            self.close_connection = True
            self._json({"error": "Content-Type must be application/json"}, 415)
            return None
        self.connection.settimeout(10)
        payload = json.loads(self.rfile.read(length))
        if not isinstance(payload, dict):
            raise ValueError("body must be a JSON object")
        return payload

    def _post_launch(self, payload: dict):
        if not self.app.authorized_master(self.headers.get("Authorization", "")):
            self._json({"error": "Master pairing token is required to create a launch file"}, 403)
            return
        if payload:
            raise ValueError("Launch takes no options")
        launch = self.app.ui.launch(self.app.base_url)
        self._json({"launch_file": launch.as_uri()})

    def _post_settings(self, payload: dict):
        self._json(self.app.save_preferences(payload))

    def _post_extension(self, payload: dict):
        if not self.app.ui.enabled:
            raise ValueError("Extension setup requires a loopback backend")
        if set(payload) - {"allow_origin"}:
            raise ValueError("Extension setup accepts only an optional allow_origin")
        if "allow_origin" in payload:
            self.app.allow_extension_origin(payload["allow_origin"])
        from .service import export_browser_assets
        directory = export_browser_assets(self.app.state_dir, "extension")
        self._json({
            "directory": str(directory),
            "token_command": token_command(self.app.state_dir),
            "allowed_origins": self.app.allowed_extension_origins(),
        })

    def _post_stop(self, payload: dict):
        if not self.app.ui.enabled:
            raise ValueError("Desktop actions require a loopback backend")
        if payload:
            raise ValueError("Stop takes no options")
        self._json({"stopping": True})
        threading.Thread(target=self.app.stop, name="jamly-stop", daemon=True).start()

    def _post_open_folder(self, payload: dict):
        if not self.app.ui.enabled:
            raise ValueError("Desktop actions require a loopback backend")
        if set(payload) - {"id", "target"} or ("id" in payload and not isinstance(payload["id"], str)):
            raise ValueError("Open folder accepts only an optional job id and target")
        if "target" in payload and payload["target"] not in ("output", "source"):
            raise ValueError("Folder target must be output or source")
        self.app.show_folder(payload.get("id"), target=payload.get("target", "output"))
        self._json({"opened": True})

    def _post_clear(self, payload: dict):
        if set(payload) - {"id"} or ("id" in payload and not isinstance(payload["id"], str)):
            raise ValueError("Clear accepts only an optional job id")
        removed = self.app.clear_jobs(payload.get("id"))
        self._json({"removed": removed, "pending_count": self.app.snapshot()["pending"]})

    def _post_rescan(self, payload: dict):
        if payload:
            raise ValueError("Rescan takes no options")
        restored = self.app.rescan_exports()
        self._json({"restored": restored, "total": self.app.snapshot()["total"]})

    def _post_add(self, payload: dict):
        if set(payload) - {"inputs", "titles", "replace_existing", "model", "audio_format", "stem"}:
            raise ValueError("unknown request field")
        inputs = payload.get("inputs")
        if not isinstance(inputs, list) or not inputs or not all(
            isinstance(source, str) and source.strip() for source in inputs
        ):
            raise ValueError("inputs must be a non-empty list of non-empty strings")
        titles = payload.get("titles")
        if titles is not None and (not isinstance(titles, list) or len(titles) != len(inputs)
                                   or any(title is not None and not isinstance(title, str) for title in titles)):
            raise ValueError("titles must match the inputs list")
        replace = payload.get("replace_existing", False)
        if not isinstance(replace, bool):
            raise ValueError("replace_existing must be a boolean")
        kwargs = {key: payload[key] for key in ("model", "audio_format", "stem") if key in payload}
        added = self.app.enqueue(inputs, titles, replace, **kwargs)
        self._json({"newly_added": added, "pending_count": self.app.snapshot()["pending"]}, 202)

    def _post_retry(self, wid: str, payload: dict):
        if set(payload) - {"replace_existing"} or not isinstance(payload.get("replace_existing", False), bool):
            raise ValueError("retry accepts only a boolean replace_existing field")
        added = int(self.app.retry(wid, payload.get("replace_existing", False)))
        self._json({"newly_added": added, "pending_count": self.app.snapshot()["pending"]}, 202)

    def _post_cancel(self, wid: str, payload: dict):
        if payload:
            raise ValueError("cancel takes no options")
        cancelled = self.app.cancel(wid)
        self._json({"cancelled": cancelled, "pending_count": self.app.snapshot()["pending"]}, 202)

    _POST_ROUTES = {
        "/app/launch": _post_launch,
        "/settings": _post_settings,
        "/app/extension": _post_extension,
        "/app/stop": _post_stop,
        "/app/open-folder": _post_open_folder,
        "/app/clear": _post_clear,
        "/app/rescan": _post_rescan,
        "/add": _post_add,
    }

    def do_POST(self):
        parsed_url = urlsplit(self.path)
        path = parsed_url.path
        if path == "/app/session":
            self._companion_session()
            return
        if path == "/app/upload":
            self._upload_local_file(parsed_url.query)
            return
        if not self._allowed_request():
            return
        try:
            payload = self._read_json_payload()
            if payload is None:
                return
            handler = self._POST_ROUTES.get(path)
            if handler is not None:
                handler(self, payload)
            elif path.startswith("/retry/"):
                self._post_retry(unquote(path.removeprefix("/retry/")), payload)
            elif path.startswith("/cancel/"):
                self._post_cancel(unquote(path.removeprefix("/cancel/")), payload)
            else:
                self._json({"error": "not found"}, 404)
        except (ValueError, InputError, UnicodeError) as exc:
            self._json({"error": str(exc)}, 400)
        except KeyError:
            self._json({"error": "unknown job"}, 404)
        except (OSError, QueueError) as exc:
            self._json({"error": "Cannot persist request: %s" % exc}, 503)
        except (RuntimeError, subprocess.SubprocessError) as exc:
            self._json({"error": str(exc)}, 503)

    def _companion_session(self):
        if not self._allowed_request(auth=False, bootstrap=True):
            return
        try:
            length = self._parse_content_length(max_bytes=4096, allow_zero=False)
            if length is None:
                raise ValueError("Invalid companion form length")
            if self._media_type() != "application/x-www-form-urlencoded":
                raise ValueError("Invalid companion form content type")
            self.connection.settimeout(10)
            values = parse_qs(self.rfile.read(length).decode("utf-8"),
                              strict_parsing=True, max_num_fields=1)
            if set(values) != {"ticket"} or len(values["ticket"]) != 1:
                raise ValueError("Invalid companion form")
            page, policy = self.app.ui.session_page(values["ticket"][0])
            self._bytes(page, "text/html; charset=utf-8", policy=policy)
        except (ValueError, UnicodeError) as exc:
            self.close_connection = True
            self._json({"error": str(exc)}, 400)

    def _upload_local_file(self, query: str):
        if not self._allowed_request():
            return
        try:
            if not self.app.ui.enabled:
                raise ValueError("Local file uploads require a loopback backend")
            length = self._parse_content_length(max_bytes=512 * 1024 * 1024, allow_zero=False)
            if length is None:
                self.close_connection = True
                self._json({"error": "invalid or oversized audio file upload (max 512 MB)"}, 400)
                return
            content_type = self._media_type()
            if content_type != "application/octet-stream" and not content_type.startswith("audio/"):
                self.close_connection = True
                self._json({"error": "Content-Type must be application/octet-stream or audio/*"}, 415)
                return
            values = parse_qs(query, strict_parsing=True, max_num_fields=1)
            raw_name = values.get("name", [""])[0].strip()
            if set(values) != {"name"} or not raw_name:
                raise ValueError("Upload requires a ?name= filename query parameter")
            basename = Path(raw_name).name
            suffix = validate_local_audio_suffix(Path(basename).suffix, basename)
            safe_name = sanitize_filename(Path(basename).stem) + suffix
            uploads_root = self.app.state_dir / "uploads"
            uploads_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            tmp_dir = uploads_root / ".jamly-tmp"
            tmp_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.connection.settimeout(60)
            digest = hashlib.sha256()
            temp_path = None
            try:
                with tempfile.NamedTemporaryFile(dir=tmp_dir, prefix=".jamly-upload-", delete=False) as tmp:
                    temp_path = Path(tmp.name)
                    remaining = length
                    while remaining > 0:
                        chunk = self.rfile.read(min(_STREAM_CHUNK_SIZE, remaining))
                        if not chunk:
                            raise ValueError("Unexpected end of uploaded file stream")
                        digest.update(chunk)
                        tmp.write(chunk)
                        remaining -= len(chunk)
                    tmp.flush()
                    os.fsync(tmp.fileno())
                hex_digest = digest.hexdigest()
                dest_dir = uploads_root / hex_digest[:16]
                dest_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
                dest_path = dest_dir / safe_name
                if dest_path.exists() and not self.app.is_managed_upload(dest_path):
                    dest_path = dest_dir / (hex_digest[:8] + "-" + safe_name)
                if (dest_path.is_file() and self.app.is_managed_upload(dest_path)
                        and dest_path.stat().st_size == length and checksum(dest_path) == hex_digest):
                    pass
                else:
                    self.app.register_upload(dest_path)
                    os.replace(temp_path, dest_path)
                    temp_path = None
            finally:
                if temp_path is not None:
                    temp_path.unlink(missing_ok=True)
            self._json({"source": str(dest_path), "name": safe_name}, 200)
        except (ValueError, UnicodeError) as exc:
            self.close_connection = True
            self._json({"error": str(exc)}, 400)
        except OSError as exc:
            self.close_connection = True
            self._json({"error": "Cannot store uploaded audio file: %s" % exc}, 503)
