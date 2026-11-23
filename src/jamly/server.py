"""Paired local HTTP API with one queue owner and one cancellable worker."""
from __future__ import annotations

import argparse
import hmac
from http.server import ThreadingHTTPServer
import os
from pathlib import Path
import re
import signal
import socket
import sys
import threading
from urllib.parse import quote, urlsplit

from .companion_ui import CompanionUI
from .config import (
    API_VERSION, add_pipeline_arguments, instance_path, job_arguments, pairing_token,
    parse_configured_args, pipeline_options, runtime_paths, token_command,
    validate_preferences, write_private_json,
)
from .http_handler import QueueStatusHandler, _is_loopback_host
from .models import (
    ALL_OUTPUT_KINDS, DEFAULT_MODEL, DEFAULT_STEM, model_choices, stem_choices,
    validate_model, validate_stem,
)
from .normalize import make_input
from .outputs import audio_directory, read_manifest, resolve_output_path
from .run import ItemResult, process_one
from .runtime import open_directory
from .tray import create_tray
from .watch_queue import QueueError, WatchQueue, queue_owner


def _authorized(header: str, token: str) -> bool:
    parts = header.split(None, 1)
    return (len(parts) == 2 and parts[0].lower() == "bearer"
            and hmac.compare_digest(parts[1].strip().encode(), token.encode()))


def _port(value: str) -> int:
    port = int(value)
    if not 0 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 0 and 65535")
    return port


def _upload_path(raw: str | None, uploads_root: Path) -> Path | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    if urlsplit(raw).scheme.lower() in ("http", "https"):
        return None
    candidate = Path(raw)
    if not candidate.is_absolute():
        return None
    try:
        resolved = candidate.resolve()
    except OSError:
        return None
    return resolved if resolved.is_relative_to(uploads_root) else None


def _discover_manifest(meta_root: Path, meta_path: Path) -> dict | None:
    rel_parts = meta_path.relative_to(meta_root).parts
    if any(part.startswith(".") for part in rel_parts):
        return None
    try:
        manifest = read_manifest(meta_path.parent, verify_hashes=False)
    except (OSError, ValueError):
        return None
    wid = manifest.get("watch_id") or rel_parts[0]
    if not isinstance(wid, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", wid):
        return None
    audio_format = manifest.get("format")
    if audio_format not in ("mp3", "wav"):
        audio_format = meta_path.parent.name if meta_path.parent.name in ("mp3", "wav") else "mp3"
    outputs = {
        kind: str(resolve_output_path(meta_path.parent, rec["file"]))
        for kind, rec in manifest["outputs"].items()
    }
    outputs["metadata"] = str(meta_path)
    original_path = None
    recorded_orig = manifest.get("original")
    if isinstance(recorded_orig, str) and recorded_orig.strip():
        orig_candidate = Path(recorded_orig)
        if not orig_candidate.is_absolute():
            orig_candidate = audio_directory(meta_path.parent) / recorded_orig
        if orig_candidate.is_file() and not orig_candidate.is_symlink():
            original_path = str(orig_candidate)
    return {
        "watch_id": wid,
        "source_url": manifest.get("source_url") or wid,
        "original_path": original_path,
        "title": manifest.get("title"),
        "model": manifest.get("model", DEFAULT_MODEL),
        "stem": manifest.get("stem", DEFAULT_STEM),
        "audio_format": audio_format,
        "outputs": outputs,
        "generation": manifest["generation"],
        "finished_at": meta_path.stat().st_mtime,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Jamly local backend")
    add_pipeline_arguments(parser)
    parser.add_argument("--config", help="JSON configuration written by jamly-service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=_port, default=8765)
    parser.add_argument("--server-token", default=None, help="Override the saved pairing token")
    parser.add_argument("--allow-origin", action="append", default=[],
                        help="Additional exact browser origin; never use '*'")
    parser.add_argument("--allow-host", action="append", default=[],
                        help="Additional HTTP Host name for an explicitly configured proxy/LAN")
    parser.add_argument("--no-tray", action="store_true",
                        help="Disable the desktop system tray icon")
    return parser


class _HTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def service_actions(self):
        super().service_actions()
        if hasattr(self, "item_server"):
            self.item_server._serving_ready.set()

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError, TimeoutError)):
            return
        super().handle_error(request, client_address)


class _IPv6Server(_HTTPServer):
    address_family = socket.AF_INET6


class ItemServer:
    def __init__(self, args):
        self.args = args
        explicit_token = args.server_token or os.environ.get("JAMLY_SERVER_TOKEN")
        args.host = args.host.strip()
        if not args.host or not _is_loopback_host(args.host) and not explicit_token:
            raise ValueError("Non-loopback binds require an explicit --server-token")
        if "*" in args.allow_origin:
            raise ValueError("Use exact --allow-origin values, not '*'")
        self.state_dir, queue_path = runtime_paths(args)
        self.queue_path = str(queue_path)
        self._owner = queue_owner(self.queue_path)
        self._owner.__enter__()
        try:
            self.queue = WatchQueue.from_path(self.queue_path)
            self._token = pairing_token(self.state_dir, explicit_token)
            self._status_lock = threading.RLock()
            self._stop = threading.Event()
            self._wake = threading.Event()
            self._serving_ready = threading.Event()
            self._stop_lock = threading.Lock()
            self._worker = None
            self._serving = False
            self._serve_ident = None
            self._closed = False
            self._current = None
            self._current_cancel = None
            self._cancelled_ids = set()
            self._tray = None
            self._error = None
            self.ui = CompanionUI(self.state_dir, enabled=_is_loopback_host(args.host))
            self._prune_orphaned_uploads()
            server_type = _IPv6Server if ":" in args.host else _HTTPServer
            self._server = server_type((args.host, args.port), QueueStatusHandler)
            self._server.item_server = self
        except BaseException:
            self._owner.__exit__(*sys.exc_info())
            raise

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return "http://%s:%d" % ("[" + host + "]" if ":" in host else host, port)

    def authorized(self, header: str) -> bool:
        return _authorized(header, self._token) or self.ui.authorize(header)

    def _uploads_root(self) -> Path:
        return (self.state_dir / "uploads").resolve()

    def _referenced_upload_paths(self) -> set[Path]:
        uploads_root = self._uploads_root()
        referenced = set()
        for item in self.queue._items:
            for field in ("source_url", "original_path"):
                path = _upload_path(item.get(field), uploads_root)
                if path is not None:
                    referenced.add(path)
        return referenced

    def _cleanup_uploads(self, candidates: list[str]) -> None:
        if not candidates:
            return
        uploads_root = self._uploads_root()
        if not uploads_root.is_dir():
            return
        referenced = self._referenced_upload_paths()
        for raw in candidates:
            path = _upload_path(raw, uploads_root)
            if path is None or path in referenced:
                continue
            try:
                path.unlink(missing_ok=True)
                parent = path.parent
                if parent != uploads_root and parent.is_dir() and not any(parent.iterdir()):
                    parent.rmdir()
            except OSError:
                pass

    def _prune_orphaned_uploads(self) -> None:
        uploads_root = self._uploads_root()
        if not uploads_root.is_dir():
            return
        referenced = self._referenced_upload_paths()
        for entry in list(uploads_root.rglob("*")):
            if not entry.is_file():
                continue
            try:
                if entry.name.startswith(".upload-") or entry.resolve() not in referenced:
                    entry.unlink(missing_ok=True)
            except OSError:
                pass
        for entry in sorted(uploads_root.iterdir(), reverse=True):
            if entry.is_dir():
                try:
                    if not any(entry.iterdir()):
                        entry.rmdir()
                except OSError:
                    pass

    def save_preferences(self, settings: dict) -> dict:
        settings = validate_preferences(settings)
        with self._status_lock:
            if self._stop.is_set():
                raise QueueError("Server is shutting down")
            target_model = settings.get("model", getattr(self.args, "model", DEFAULT_MODEL))
            target_stem = settings.get("stem", getattr(self.args, "stem", DEFAULT_STEM))
            validate_stem(target_stem, target_model)
            current = {key: value for key, value in pipeline_options(self.args).items()
                       if key in ("model", "audio_format", "output_dir")}
            current.update(settings)
            if target_stem != DEFAULT_STEM or "stem" in settings:
                current["stem"] = target_stem
            write_private_json(self.state_dir / "preferences.json", current)
            self.args.stem = target_stem
            for key, value in current.items():
                setattr(self.args, key, value)
            return current

    def show_folder(self, wid: str | None = None, *, target: str = "output") -> None:
        if target not in ("output", "source"):
            raise ValueError("Folder target must be output or source")
        with self._status_lock:
            if wid is None:
                if target != "output":
                    raise ValueError("Source folder requires a job id")
                directory = Path(self.args.output_dir)
                directory.mkdir(parents=True, exist_ok=True)
            else:
                item = self.queue.get(wid)
                if target == "source":
                    source = (item or {}).get("source_url")
                    if not source:
                        raise ValueError("This job has no recorded source")
                    if urlsplit(source).scheme.lower() in ("http", "https"):
                        raise ValueError("Remote URLs do not have a local source folder")
                    source_path = Path(source)
                    if not source_path.exists():
                        raise ValueError("The original local audio file no longer exists")
                    directory = source_path if source_path.is_dir() else source_path.parent
                else:
                    meta_path = (item or {}).get("outputs", {}).get("metadata")
                    if not meta_path and item:
                        for exp in reversed(list((item.get("exports") or {}).values())):
                            if exp.get("outputs", {}).get("metadata"):
                                meta_path = exp["outputs"]["metadata"]
                                break
                    if not meta_path:
                        raise ValueError("This job has no completed export")
                    directory = audio_directory(Path(meta_path).parent)
        open_directory(directory)

    def clear_jobs(self, wid: str | None = None) -> int:
        with self._status_lock:
            if self._error or self._stop.is_set():
                raise QueueError(self._error or "Server is shutting down")
            if wid is not None:
                item = self.queue.get(wid)
                if item is None:
                    raise KeyError(wid)
                if item["status"] in ("pending", "running"):
                    raise ValueError("Cannot remove a queued or running job from the list")
                candidates = [item.get("source_url"), item.get("original_path")]
                removed = int(self._commit(lambda queue: queue.remove(wid)))
                self._cleanup_uploads(candidates)
                return removed
            terminal = [item for item in self.queue._items if item["status"] not in ("pending", "running")]
            if not terminal:
                return 0
            candidates = [
                value for item in terminal for value in (item.get("source_url"), item.get("original_path"))
            ]
            removed = self._commit(lambda queue: queue.clear_terminal())
            self._cleanup_uploads(candidates)
            return removed

    def rescan_exports(self) -> int:
        with self._status_lock:
            if self._error or self._stop.is_set():
                raise QueueError(self._error or "Server is shutting down")
            meta_root = Path(self.args.output_dir) / ".jamly"
            if not meta_root.is_dir():
                return 0
            discovered = [
                entry for meta_path in sorted(meta_root.rglob("metadata.json"))
                if (entry := _discover_manifest(meta_root, meta_path)) is not None
            ]
            if not discovered:
                return 0
            candidate = self.queue.copy()
            restored = sum(int(candidate.restore_export(**entry)) for entry in discovered)
            if restored:
                candidate.save()
                self.queue = candidate
            return restored

    def _commit(self, change):
        candidate = self.queue.copy()
        value = change(candidate)
        candidate.save()
        self.queue = candidate
        return value

    def enqueue(self, inputs, titles=None, replace=False, *, model=None, audio_format=None,
                stem=None) -> int:
        normalized = [make_input(source) for source in inputs]
        if not self.ui.enabled and any(item.watch_id.startswith("file-") for item in normalized):
            raise ValueError("Local file paths require a loopback backend")
        if model is not None:
            validate_model(model)
        if audio_format is not None and audio_format not in ("mp3", "wav"):
            raise ValueError("audio_format must be mp3 or wav")
        if stem is not None:
            validate_stem(stem)
        with self._status_lock:
            options = dict(pipeline_options(self.args), replace_existing=replace or self.args.replace_existing)
            if model is not None:
                options["model"] = model
            if audio_format is not None:
                options["audio_format"] = audio_format
            if stem is not None:
                options["stem"] = stem
            validate_stem(options.get("stem", DEFAULT_STEM), options.get("model", DEFAULT_MODEL))
            if self._error or self._stop.is_set():
                raise QueueError(self._error or "Server is shutting down")
            added = self._commit(lambda queue: queue.add_all(
                [item.watch_id for item in normalized], titles,
                [item.source_url for item in normalized], retry=True, options=options,
            ))
        self._wake.set()
        return added

    def retry(self, wid: str, replace: bool = False) -> bool:
        with self._status_lock:
            if self._error or self._stop.is_set():
                raise QueueError(self._error or "Server is shutting down")
            item = self.queue.get(wid)
            if item is None:
                raise KeyError(wid)
            options = dict(item.get("options", {}), replace_existing=replace)
            added = self._commit(lambda queue: queue.add(wid, retry=True, options=options))
        self._wake.set()
        return added

    def cancel(self, wid: str) -> bool:
        with self._status_lock:
            if self._error or self._stop.is_set():
                raise QueueError(self._error or "Server is shutting down")
            item = self.queue.get(wid)
            if item is None:
                raise KeyError(wid)
            if item["status"] == "pending":
                self._commit(lambda queue: queue.finish(wid, "cancelled"))
                return True
            if item["status"] == "running" and self._current and self._current["watch_id"] == wid:
                self._cancelled_ids.add(wid)
                self._current["cancelling"] = True
                if self._current_cancel is not None:
                    self._current_cancel.set()
                return True
            return False

    def _process_one(self) -> bool:
        with self._status_lock:
            pending = self.queue.pending_items()
            if not pending or self._stop.is_set():
                return False
            item = pending[0]
            wid = item["watch_id"]
            cancel_event = threading.Event()
            self._current_cancel = cancel_event
            self._cancelled_ids.discard(wid)
            self._commit(lambda queue: queue.mark_running(wid))
            running = self.queue.get(wid) or {}
            self._current = {"watch_id": wid, "stage": "metadata", "status": "running",
                             "title": item.get("title"), "error": None, "progress": None,
                             "started_at": running.get("started_at"), "cancelling": False}

        def progress(_wid, stage, index, total):
            with self._status_lock:
                if self._current:
                    self._current["stage"] = stage
                    self._current["progress"] = int(index) if total else None

        try:
            result = process_one(
                wid, job_arguments(self.args, item.get("options", {})),
                source_url=item["source_url"], progress=progress, cancel_event=cancel_event,
            )
        except Exception as exc:
            result = ItemResult(wid, status="error", error=str(exc))
        with self._status_lock:
            user_cancelled = wid in self._cancelled_ids
            self._cancelled_ids.discard(wid)
            self._current_cancel = None
            if self._stop.is_set() and not user_cancelled and result.status not in ("done", "skipped"):
                status, error = "pending", None
            elif user_cancelled and result.status == "cancelled":
                status, error = "cancelled", None
            else:
                status, error = result.status, result.error
            self._commit(lambda queue: queue.finish(
                wid, status, error=error,
                title=result.title, outputs=result.outputs, generation=result.generation,
                original_path=result.source_path,
            ))
            self._current = None
        return True

    def _loop(self):
        while not self._stop.is_set():
            try:
                if self._process_one():
                    continue
            except Exception as exc:
                with self._status_lock:
                    self._error = "Cannot persist queue state; fix storage and restart: %s" % exc
                print(self._error, file=sys.stderr)
                return
            self._wake.wait()
            self._wake.clear()

    @staticmethod
    def _parse_status_query(query: dict | None) -> tuple[str, str, str, str, int, int] | None:
        if not query:
            return None
        q = (query.get("q") or [""])[0].strip().lower()
        status_filter = (query.get("status") or ["all"])[0].strip().lower()
        if status_filter not in ("all", "active", "done", "error", "cancelled"):
            raise ValueError("status filter must be all, active, done, error, or cancelled")
        model_filter = (query.get("model") or ["all"])[0].strip()
        if model_filter != "all":
            validate_model(model_filter)
        format_filter = (query.get("format") or ["all"])[0].strip().lower()
        if format_filter not in ("all", "mp3", "wav"):
            raise ValueError("format filter must be all, mp3, or wav")
        try:
            page = int((query.get("page") or ["1"])[0])
            page_size = int((query.get("page_size") or ["25"])[0])
        except ValueError as exc:
            raise ValueError("page and page_size must be integers") from exc
        if page < 1:
            page = 1
        page_size = max(1, min(100, page_size))
        return q, status_filter, model_filter, format_filter, page, page_size

    @staticmethod
    def _matches_filters(item: dict, q: str, status_filter: str, model_filter: str, format_filter: str) -> bool:
        status = item["status"]
        if status_filter == "active" and status not in ("pending", "running"):
            return False
        if status_filter == "done" and status not in ("done", "skipped"):
            return False
        if status_filter in ("error", "cancelled") and status != status_filter:
            return False
        options = item.get("options") or {}
        exports = (item.get("exports") or {}).values()
        if model_filter != "all":
            if options.get("model", DEFAULT_MODEL) != model_filter and not any(
                exp.get("model", DEFAULT_MODEL) == model_filter for exp in exports
            ):
                return False
        if format_filter != "all":
            if options.get("audio_format", "mp3") != format_filter and not any(
                exp.get("audio_format", "mp3") == format_filter for exp in exports
            ):
                return False
        if q:
            haystack = "\n".join(
                str(part) for part in (item.get("title"), item.get("watch_id"), item.get("source_url"), item.get("error"))
                if part
            ).lower()
            if q not in haystack:
                return False
        return True

    @staticmethod
    def _serialize_outputs(wid: str, outputs: dict, generation: str) -> list[dict]:
        return [
            {
                "kind": kind,
                "name": Path(path).name,
                "url": "/outputs/%s/%s?generation=%s" % (
                    quote(wid, safe=""), kind, quote(generation, safe="")
                ),
            }
            for kind, path in (outputs or {}).items()
            if kind in ALL_OUTPUT_KINDS
        ]

    @classmethod
    def _serialize_job(cls, item: dict) -> dict:
        record = {key: item.get(key) for key in (
            "watch_id", "title", "source_url", "status", "error", "attempts",
            "requested_at", "started_at", "finished_at", "duration_seconds", "updated_at",
        )}
        record["has_original"] = bool(item.get("original_path"))
        record["outputs"] = []
        record["exports"] = []
        record["model"] = item.get("options", {}).get("model", DEFAULT_MODEL)
        record["stem"] = item.get("options", {}).get("stem", DEFAULT_STEM)
        record["audio_format"] = item.get("options", {}).get("audio_format", "mp3")
        active_generation = None
        if item["status"] in ("done", "skipped") and item.get("generation"):
            active_generation = item["generation"]
            record["outputs"] = cls._serialize_outputs(item["watch_id"], item.get("outputs", {}), active_generation)
        for exp in (item.get("exports") or {}).values():
            gen = exp.get("generation")
            if not gen or gen == active_generation:
                continue
            exp_outputs = cls._serialize_outputs(item["watch_id"], exp.get("outputs", {}), gen)
            if exp_outputs:
                record["exports"].append({
                    "model": exp.get("model", DEFAULT_MODEL),
                    "stem": exp.get("stem", DEFAULT_STEM),
                    "audio_format": exp.get("audio_format", "mp3"),
                    "generation": gen,
                    "outputs": exp_outputs,
                })
        return record

    def snapshot(self, query: dict | None = None) -> dict:
        parsed = self._parse_status_query(query)
        with self._status_lock:
            items = self.queue._items
            pending_count = done_count = failed_count = 0
            running, pending, terminal = [], [], []
            for idx, item in enumerate(items):
                st = item["status"]
                if st == "pending":
                    pending_count += 1
                    pending.append(item)
                elif st == "running":
                    running.append(item)
                else:
                    if st in ("done", "skipped"):
                        done_count += 1
                    elif st == "error":
                        failed_count += 1
                    terminal.append((item.get("finished_at") or item.get("updated_at") or 0, idx, item))
            terminal.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
            ordered = running + pending + [entry[2] for entry in terminal]
            pagination = None
            if parsed is None:
                visible = ordered[:100 + len(running) + len(pending)]
            else:
                q, status_filter, model_filter, format_filter, page, page_size = parsed
                matched = [
                    item for item in ordered
                    if self._matches_filters(item, q, status_filter, model_filter, format_filter)
                ]
                total_matched = len(matched)
                total_pages = max(1, (total_matched + page_size - 1) // page_size)
                page = min(page, total_pages)
                start = (page - 1) * page_size
                visible = matched[start:start + page_size]
                pagination = {
                    "page": page, "page_size": page_size,
                    "total_matched": total_matched, "total_pages": total_pages,
                }
            payload = {
                "api_version": API_VERSION, "pending": pending_count,
                "done": done_count, "failed": failed_count,
                "total": len(items), "current": dict(self._current) if self._current else None,
                "jobs": [self._serialize_job(item) for item in visible],
                "error": self._error, "models": model_choices(),
                "stems": stem_choices(),
                "defaults": {
                    key: value for key, value in pipeline_options(self.args).items()
                    if key in ("model", "audio_format", "output_dir")
                    or (key == "stem" and value != DEFAULT_STEM)
                },
            }
            if pagination is not None:
                payload["pagination"] = pagination
            return payload

    def start(self):
        with self._stop_lock:
            if self._stop.is_set() or self._closed:
                return
            if self.ui.enabled:
                write_private_json(instance_path(self.state_dir, Path(self.queue_path)), {
                    "port": self._server.server_address[1], "host": self._server.server_address[0],
                    "pid": os.getpid(),
                })
            self._serving = True
            self._serve_ident = threading.get_ident()
            self._worker = threading.Thread(target=self._loop, name="jamly-worker", daemon=True)
            self._worker.start()
        try:
            self._server.serve_forever(poll_interval=0.05)
        finally:
            self._serving = False
            self._serving_ready.set()

    def stop(self):
        with self._stop_lock:
            if self._closed:
                return
            self._stop.set()
            with self._status_lock:
                if self._current_cancel is not None:
                    self._current_cancel.set()
            self._wake.set()
            serving = self._serving and self._serve_ident != threading.get_ident()
        if serving:
            self._serving_ready.wait(timeout=2)
            self._server.shutdown()
        with self._stop_lock:
            if self._closed:
                return
            if self._worker is not None:
                self._worker.join(timeout=15)
                if self._worker.is_alive():
                    raise RuntimeError("The worker has not stopped; queue ownership was retained")
            with self._status_lock:
                self._server.server_close()
                if self.ui.enabled:
                    instance_path(self.state_dir, Path(self.queue_path)).unlink(missing_ok=True)
                self.ui.close()
                self._owner.__exit__(None, None, None)
                self._closed = True
            if self._tray is not None:
                self._tray.stop()


def main(argv=None) -> int:
    parser = build_parser()
    try:
        args = parse_configured_args(parser, argv, preferences=True)
        server = ItemServer(args)
    except (OSError, ValueError, QueueError) as exc:
        print("jamly-server: %s" % exc, file=sys.stderr)
        return 2
    print("Jamly listening on %s:%d" % (args.host, server._server.server_address[1]), flush=True)
    print("Pair browsers with: " + token_command(server.state_dir), flush=True)
    tray = create_tray(server, enabled=not getattr(args, "no_tray", False))
    server._tray = tray
    tray.start()

    def interrupt(_signum, _frame):
        raise KeyboardInterrupt

    previous_signal = signal.signal(signal.SIGTERM, interrupt)
    try:
        server.start()
    except KeyboardInterrupt:
        pass
    finally:
        tray.stop()
        server.stop()
        signal.signal(signal.SIGTERM, previous_signal)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
