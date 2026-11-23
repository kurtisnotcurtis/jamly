"""Download one media item, separate it, and publish one matched audio pair."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time
import uuid

from filelock import FileLock, Timeout

from .audio import render_pair
from .bootstrap import detect_ffmpeg, ytdlp_command, ytdlp_options
from .config import add_pipeline_arguments, job_arguments, pipeline_options, runtime_paths
from .core import (
    build_ytdlp_extract_cmd, check_cancelled, command_prefix, detect_engine,
    run as _run, sanitize_filename, separate_stems,
)
from .logging import Logger
from .models import DEFAULT_MODEL, DEFAULT_STEM, expected_output_kinds, validate_model, validate_stem
from .naming import meta_dict, parse_title_artist, stem_prefix
from .normalize import Input, local_audio_path, normalize_inputs_full
from .outputs import (
    PROCESSING_VERSION, audio_directory, checksum, publish_directory, read_manifest_structural,
    recover_directory, resolve_output_path,
)
from .watch_queue import QueueError, WatchQueue, queue_owner

_log = Logger()
METADATA_FETCH_TIMEOUT = 60
OUTPUT_LOCK_TIMEOUT = 10.0


@dataclass
class ItemResult:
    watch_id: str
    title: str | None = None
    status: str = "pending"
    stage: str = ""
    meta_path: str | None = None
    error: str | None = None
    generation: str | None = None
    source_path: str | None = None
    stem_paths: dict[str, str] | None = None

    @property
    def ok(self) -> bool:
        return self.status in ("done", "skipped")

    @property
    def finished(self) -> bool:
        return self.status in ("done", "skipped", "error", "cancelled")

    @property
    def outputs(self) -> dict[str, str]:
        result = dict(self.stem_paths or {})
        if self.meta_path:
            result["metadata"] = self.meta_path
        return result


def build_download_cmd(ytdlp_exe, watch_id: str, workdir: str,
                       source_url: str | None = None, ffmpeg_dir: str = "") -> list[str]:
    cmd = build_ytdlp_extract_cmd(
        ytdlp_exe, source_url or watch_id, str(Path(workdir) / (watch_id + ".%(ext)s")),
        ffmpeg_dir,
    )
    index = cmd.index("--")
    return cmd[:index] + ["--no-simulate", "--print", "after_move:JAMLY_FILE:%(filepath)j"] + cmd[index:]


def _fetch_item_metadata(ytdlp_exe, source_url: str, cancel_event=None) -> dict:
    cmd = command_prefix(ytdlp_exe) + ytdlp_options()
    cmd += ["--dump-single-json", "--skip-download", "--", source_url]
    result = _run(cmd, cancel_event=cancel_event, timeout=METADATA_FETCH_TIMEOUT)
    if not result.ok:
        raise RuntimeError("Cannot read media metadata: %s" % result.stderr.strip())
    try:
        data = json.loads(result.stdout)
    except ValueError as exc:
        raise ValueError("yt-dlp returned invalid metadata") from exc
    if not isinstance(data, dict):
        raise ValueError("yt-dlp returned invalid metadata")
    if data.get("_type") in ("playlist", "multi_video") or "entries" in data:
        raise ValueError("Use an individual media URL; playlists, search results and collections are not supported")
    if data.get("is_live") or data.get("is_upcoming"):
        raise ValueError("Live or upcoming media is not supported; use a completed recording")
    duration = data.get("duration")
    if duration is not None and (isinstance(duration, bool) or not isinstance(duration, (int, float))):
        raise ValueError("Invalid media duration")
    return {
        "id": data.get("id"), "title": data.get("title"),
        "artist": data.get("artist") or data.get("creator"),
        "uploader": data.get("uploader") or data.get("channel"),
        "duration_seconds": int(duration) if duration is not None else None,
    }


def _fetch_local_metadata(ffprobe_exe: str, path: Path, cancel_event=None) -> dict:
    check_cancelled(cancel_event)
    if not path.exists() or not path.is_file():
        raise ValueError("Local audio file not found: %s" % path)
    if path.stat().st_size == 0:
        raise ValueError("Local audio file is empty: %s" % path)
    if Path(ffprobe_exe).is_file():
        cmd = [
            ffprobe_exe, "-v", "quiet", "-print_format", "json",
            "-show_format", "-show_streams", str(path),
        ]
        result = _run(cmd, cancel_event=cancel_event, timeout=METADATA_FETCH_TIMEOUT)
        if not result.ok:
            raise ValueError("Cannot read local audio file: %s" % path.name)
        try:
            data = json.loads(result.stdout)
        except ValueError as exc:
            raise ValueError("ffprobe returned invalid metadata") from exc
        streams = data.get("streams") if isinstance(data, dict) else None
        if not isinstance(streams, list) or not any(
            isinstance(stream, dict) and stream.get("codec_type") == "audio" for stream in streams
        ):
            raise ValueError("Local file does not contain a readable audio stream: %s" % path.name)
        fmt = data.get("format") if isinstance(data.get("format"), dict) else {}
        raw_tags = fmt.get("tags") if isinstance(fmt.get("tags"), dict) else {}
        tags = {str(k).lower(): v.strip() for k, v in raw_tags.items() if isinstance(v, str) and v.strip()}
        raw_duration = fmt.get("duration")
        duration = None
        if raw_duration is not None:
            try:
                duration = int(float(raw_duration))
            except (TypeError, ValueError):
                duration = None
        return {
            "id": path.stem,
            "title": tags.get("title") or path.stem,
            "artist": tags.get("artist") or tags.get("album_artist"),
            "uploader": "local file",
            "duration_seconds": duration,
        }
    import soundfile as sf
    try:
        info = sf.info(str(path))
    except Exception as exc:
        raise ValueError("Cannot read local audio file: %s" % path.name) from exc
    if not info.frames or not info.samplerate:
        raise ValueError("Local audio file has no audio frames: %s" % path.name)
    return {
        "id": path.stem,
        "title": path.stem,
        "artist": None,
        "uploader": "local file",
        "duration_seconds": int(info.frames // info.samplerate),
    }


def download_audio(command: list[str], workdir: Path, cancel_event=None) -> Path:
    outputs = []

    def on_line(line):
        if line.startswith("JAMLY_FILE:"):
            outputs.append(Path(json.loads(line.removeprefix("JAMLY_FILE:"))).resolve())

    result = _run(command, stream=True, on_line=on_line, cancel_event=cancel_event)
    # yt-dlp's single-download limit may terminate with 101 after a successful transfer.
    if result.returncode not in (0, 101):
        raise RuntimeError("Audio download failed: %s" % result.stderr.strip())
    if len(outputs) != 1 or outputs[0].parent != workdir.resolve() or not outputs[0].is_file():
        raise RuntimeError("Expected exactly one downloaded audio file")
    return outputs[0]


def _stem_file_prefix(watch_id: str, parsed=None, *, model: str = DEFAULT_MODEL,
                      stem: str = DEFAULT_STEM) -> str:
    safe_id = sanitize_filename(watch_id)
    prefix = sanitize_filename(stem_prefix(watch_id, parsed))
    model_tag = (" [%s]" % model) if model != DEFAULT_MODEL else ""
    stem_tag = " [all]" if stem == "all" else ""
    tags = model_tag + stem_tag
    if parsed:
        budget = 180 - len(safe_id.encode("utf-8")) - len(tags.encode("utf-8")) - 3
        prefix = prefix.encode("utf-8")[:budget].decode("utf-8", "ignore").rstrip()
        prefix += " [%s]" % safe_id
    return prefix + tags


def stem_destination_paths(output_dir: str, watch_id: str, fmt: str, parsed=None, *,
                           stem: str = DEFAULT_STEM, model: str = DEFAULT_MODEL) -> tuple[dict[str, Path], Path]:
    prefix = _stem_file_prefix(watch_id, parsed, model=model, stem=stem)
    directory = Path(output_dir)
    destinations = {
        kind: directory / (prefix + "_" + kind + "." + fmt)
        for kind in expected_output_kinds(stem, model)
    }
    return destinations, directory / "metadata.json"


def _resolve_manifest_original(directory: Path, manifest: dict) -> str | None:
    recorded = manifest.get("original")
    if not isinstance(recorded, str) or not recorded.strip():
        return None
    candidate = Path(recorded)
    if not candidate.is_absolute():
        candidate = audio_directory(directory) / recorded
    if candidate.is_file() and not candidate.is_symlink():
        return str(candidate)
    return None


def result_from_manifest(wid: str, directory: Path, manifest: dict, status: str, *,
                         local_source: Path | None = None) -> ItemResult:
    outputs_map = manifest.get("outputs", {})
    stem_paths = {kind: str(resolve_output_path(directory, rec["file"]))
                  for kind, rec in outputs_map.items()}
    source_path = _resolve_manifest_original(directory, manifest)
    if source_path is None and local_source is not None and local_source.is_file() and not local_source.is_symlink():
        source_path = str(local_source)
    return ItemResult(
        wid, title=manifest.get("title") or wid, status=status,
        meta_path=str(directory / "metadata.json"), generation=manifest["generation"],
        source_path=source_path, stem_paths=stem_paths,
    )


def _existing_output(wid: str, directory: Path, *, model: str, stem: str,
                     audio_format: str) -> dict | None:
    """Return the manifest of an identical track that can be skipped, or None to regenerate.

    A matching identity whose stems are all present and hash-valid is safe to skip. When the same
    track's audio was deleted (a missing output), None lets the caller reprocess without requiring
    --replace-existing, since there is no distinct artifact being overwritten. Structurally invalid
    or differently-identified outputs still raise so the caller can ask for --replace-existing.
    """
    try:
        manifest = read_manifest_structural(directory)
    except (OSError, ValueError, KeyError) as exc:
        raise ValueError("Incomplete or outdated outputs; use --replace-existing: %s" % exc) from exc
    if not (manifest["watch_id"] == wid and manifest.get("format") == audio_format
            and manifest.get("model") == model and manifest.get("stem", DEFAULT_STEM) == stem):
        raise ValueError(
            "Incomplete or outdated outputs; use --replace-existing: output identity does not match")
    for record in manifest["outputs"].values():
        path = resolve_output_path(directory, record["file"])
        if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
            # A stem was deleted: regenerate it without demanding --replace-existing.
            return None
    for record in manifest["outputs"].values():
        path = resolve_output_path(directory, record["file"])
        if checksum(path) != record.get("sha256"):
            raise ValueError(
                "Incomplete or outdated outputs; use --replace-existing: output checksum mismatch: %s" % path)
    return manifest


@contextmanager
def _output_lock(directory: Path, cancel_event=None, timeout: float | None = None):
    lock = FileLock(str(directory.parent / ("." + directory.name + ".lock")))
    wait_limit = OUTPUT_LOCK_TIMEOUT if timeout is None else timeout
    deadline = time.monotonic() + wait_limit
    while True:
        check_cancelled(cancel_event)
        remaining = max(0.0, deadline - time.monotonic())
        try:
            lock.acquire(timeout=min(0.1, remaining))
            break
        except Timeout:
            if time.monotonic() >= deadline:
                raise
    try:
        yield
    finally:
        lock.release()


def _persist_original(audio: Path, target: Path) -> str:
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".orig-", delete=False) as tmp:
            temp_path = Path(tmp.name)
        shutil.copy2(audio, temp_path)
        with temp_path.open("rb+") as file:
            os.fsync(file.fileno())
        os.replace(temp_path, target)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    return str(target)


def _resolve_output_dir(wid: str, args) -> tuple[str, str, Path]:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", wid):
        raise ValueError("Invalid stable input id")
    if args.audio_format not in ("mp3", "wav"):
        raise ValueError("Unsupported audio format")
    model = validate_model(getattr(args, "model", DEFAULT_MODEL))
    stem = validate_stem(getattr(args, "stem", DEFAULT_STEM), model)
    out_root = Path(args.output_dir).expanduser().absolute()
    base_dir = out_root / ".jamly" / wid / model
    directory = (base_dir if stem == DEFAULT_STEM else base_dir / stem) / args.audio_format
    directory.parent.mkdir(parents=True, exist_ok=True)
    return model, stem, directory


def _resolve_tools(args) -> tuple[str, str, str]:
    ffmpeg_dir = detect_ffmpeg(
        ffmpeg_dirs=[args.ffmpeg_dir] if getattr(args, "ffmpeg_dir", None) else []
    ).path
    if not ffmpeg_dir:
        raise RuntimeError("Install ffmpeg and ffprobe on PATH; run jamly-doctor for details")
    if not detect_engine():
        raise RuntimeError("Demucs is missing; reinstall Jamly")
    exe_suffix = ".exe" if os.name == "nt" else ""
    ffmpeg = str(Path(ffmpeg_dir) / ("ffmpeg" + exe_suffix))
    ffprobe = str(Path(ffmpeg_dir) / ("ffprobe" + exe_suffix))
    return ffmpeg_dir, ffmpeg, ffprobe


def _write_manifest(meta_path: Path, *, wid: str, source: str, metadata: dict, parsed,
                    model: str, stem: str, audio_format: str, audio_info: dict,
                    destinations: dict[str, Path], orig_target: Path | None) -> dict:
    manifest = dict(
        meta_dict(watch_id=wid, source_url=source, title=metadata.get("title"),
                  artist=metadata.get("artist") or (parsed[0] if parsed else None),
                  uploader=metadata.get("uploader"), duration=metadata.get("duration_seconds")),
        processing_version=PROCESSING_VERSION, generation=uuid.uuid4().hex,
        model=model, stem=stem, format=audio_format, audio=audio_info,
        outputs={name: {"file": path.name, "sha256": checksum(path)}
                 for name, path in destinations.items()},
    )
    if orig_target is not None:
        manifest["original"] = orig_target.name
    with meta_path.open("w", encoding="utf-8") as file:
        json.dump(manifest, file, ensure_ascii=False, indent=2)
        file.flush()
        os.fsync(file.fileno())
    return manifest


def process_one(wid: str, args, progress=None, source_url=None, cancel_event=None) -> ItemResult:
    result = ItemResult(wid)
    source = source_url or wid

    def stage(name: str):
        check_cancelled(cancel_event)
        result.status = result.stage = name
        if progress is not None:
            progress(wid, name, 0, 0)

    try:
        model, stem, directory = _resolve_output_dir(wid, args)
        local_source = local_audio_path(source)
        with _output_lock(directory, cancel_event):
            recover_directory(directory)
            if directory.exists() and not args.replace_existing:
                manifest = _existing_output(wid, directory, model=model, stem=stem,
                                            audio_format=args.audio_format)
                if manifest is not None:
                    return result_from_manifest(wid, directory, manifest, "skipped",
                                                local_source=local_source)

        stage("metadata")
        ffmpeg_dir, ffmpeg, ffprobe = _resolve_tools(args)
        if local_source is not None:
            metadata = _fetch_local_metadata(ffprobe, local_source, cancel_event)
            command = None
        else:
            command = ytdlp_command()
            metadata = _fetch_item_metadata(command, source, cancel_event)
        result.title = metadata.get("title") or wid
        parsed = parse_title_artist(metadata.get("title"))
        if not parsed and metadata.get("artist") and metadata.get("title"):
            parsed = (metadata["artist"], metadata["title"])
        with tempfile.TemporaryDirectory(prefix="jamly-") as work, tempfile.TemporaryDirectory(
            prefix="." + args.audio_format + "-", dir=directory.parent
        ) as staged:
            workdir, staged_dir = Path(work), Path(staged)
            stage("download")
            audio = (
                local_source if local_source is not None else download_audio(
                    build_download_cmd(command, wid, work, source, ffmpeg_dir), workdir, cancel_event
                )
            )
            stage("separate")
            stems = separate_stems(
                str(audio), work, args.threads,
                getattr(args, "device", "auto"), cancel_event,
                ffmpeg_dir=ffmpeg_dir, model=model,
                on_progress=(lambda pct: progress(wid, "separate", pct, 100)) if progress else None,
            )
            stage("mix")
            destinations, meta = stem_destination_paths(
                staged, wid, args.audio_format, parsed, stem=stem, model=model
            )
            audio_info = render_pair(stems, workdir, destinations, args.audio_format,
                                     ffmpeg, cancel_event, target_stem=stem)
            orig_target = None
            if local_source is None:
                orig_ext = audio.suffix.lower() or "." + args.audio_format
                orig_target = (
                    Path(audio_directory(directory))
                    / ("%s_original%s" % (_stem_file_prefix(wid, parsed), orig_ext))
                )
            manifest = _write_manifest(
                Path(meta), wid=wid, source=source, metadata=metadata, parsed=parsed,
                model=model, stem=stem, audio_format=args.audio_format, audio_info=audio_info,
                destinations=destinations, orig_target=orig_target,
            )
            stage("publish")
            with _output_lock(directory, cancel_event):
                publish_directory(staged_dir, directory)
                retained = (
                    _persist_original(Path(audio), orig_target)
                    if orig_target is not None and audio is not None and Path(audio).exists()
                    else None
                )
            result = result_from_manifest(wid, directory, manifest, "done", local_source=local_source)
            if retained is not None:
                result.source_path = retained
            return result
    except InterruptedError as exc:
        result.status, result.error = "cancelled", str(exc)
    except Timeout:
        result.status = "error"
        result.error = "Outputs are currently in use by another process or download; try again shortly"
    except Exception as exc:
        result.status, result.error = "error", str(exc)
    return result


def gather_inputs(args) -> list[Input]:
    sources = list(args.input or [])
    if not sys.stdin.isatty():
        try:
            sources.extend(sys.stdin.read().splitlines())
        except (ValueError, OSError):
            # Test runners and some GUI launchers supply an unreadable stdin.
            if not sources:
                raise ValueError("Cannot read stdin; pass inputs with -i")
    return normalize_inputs_full(sources)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jamly", description="Create a drums/drumless audio pair")
    add_pipeline_arguments(parser)
    parser.add_argument("-i", "--input", action="append", metavar="URL_OR_ID")
    parser.add_argument("--dry-run", action="store_true", help="Preview pending jobs without changing state")
    return parser


def link_outputs(result: ItemResult) -> None:
    if result.ok:
        for path in result.outputs.values():
            _log.link(path)


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    _state, queue_path = runtime_paths(args)
    path = str(queue_path)
    try:
        inputs = gather_inputs(args)
        if args.dry_run:
            queue = WatchQueue.from_path(path)
            print("Would process:", ", ".join(dict.fromkeys(
                queue.pending_ids() + [item.watch_id for item in inputs])) or "(none)")
            return 0
        with queue_owner(path):
            queue = WatchQueue.from_path(path)
            if inputs:
                queue.add_all([item.watch_id for item in inputs],
                              source_urls=[item.source_url for item in inputs],
                              retry=True, options=pipeline_options(args))
            else:
                for item in queue.items:
                    if item["status"] == "error":
                        queue.add(item["watch_id"], retry=True)
            pending = queue.pending_items()
            if not pending:
                _log.warn("No input or pending jobs; pass an individual media URL with -i")
                return 2
            queue.save()
            failures = 0
            for item in pending:
                wid = item["watch_id"]
                queue.mark_running(wid)
                queue.save()
                try:
                    result = process_one(
                        wid, job_arguments(args, item.get("options", {})),
                        progress=lambda _wid, stage, index, total: _log.step(
                            "%s: %s (%d%%)" % (_wid, stage, index) if total else "%s: %s" % (_wid, stage)
                        ),
                        source_url=item["source_url"],
                    )
                except KeyboardInterrupt:
                    queue.finish(wid, "pending")
                    queue.save()
                    return 130
                queue.finish(wid, result.status, error=result.error, title=result.title,
                             outputs=result.outputs, generation=result.generation,
                             original_path=result.source_path)
                queue.save()
                if result.ok:
                    _log.ok("%s: %s" % (wid, result.status))
                    link_outputs(result)
                else:
                    failures += 1
                    _log.fail("%s: %s" % (wid, result.error))
            return 1 if failures else 0
    except (OSError, ValueError, QueueError) as exc:
        _log.fail(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
