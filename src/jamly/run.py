"""Download one media item, separate it, and publish one matched audio pair."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import uuid

from filelock import FileLock

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
    PROCESSING_VERSION, checksum, publish_directory, read_manifest, recover_directory,
    resolve_output_path,
)
from .watch_queue import QueueError, WatchQueue, queue_owner

_log = Logger()
METADATA_FETCH_TIMEOUT = 60


@dataclass
class ItemResult:
    watch_id: str
    title: str | None = None
    status: str = "pending"
    stage: str = ""
    drums_path: str | None = None
    nodrums_path: str | None = None
    meta_path: str | None = None
    error: str | None = None
    generation: str | None = None
    stem_paths: dict[str, str] | None = None

    @property
    def ok(self) -> bool:
        return self.status in ("done", "skipped")

    @property
    def finished(self) -> bool:
        return self.status in ("done", "skipped", "error", "cancelled")

    @property
    def outputs(self) -> dict[str, str]:
        if self.stem_paths:
            result = dict(self.stem_paths)
            if self.meta_path:
                result["metadata"] = self.meta_path
            return result
        return {name: path for name, path in (
            ("drums", self.drums_path), ("no_drums", self.nodrums_path), ("metadata", self.meta_path)
        ) if path}


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


def _stem_file_prefix(watch_id: str, parsed=None, *, model: str = DEFAULT_MODEL) -> str:
    safe_id = sanitize_filename(watch_id)
    prefix = sanitize_filename(stem_prefix(watch_id, parsed))
    model_tag = (" [%s]" % model) if model != DEFAULT_MODEL else ""
    if parsed:
        budget = 180 - len(safe_id.encode("utf-8")) - len(model_tag.encode("utf-8")) - 3
        prefix = prefix.encode("utf-8")[:budget].decode("utf-8", "ignore").rstrip()
        prefix += " [%s]" % safe_id
    return prefix + model_tag


def stem_destination_paths(output_dir: str, watch_id: str, fmt: str, parsed=None, *,
                           stem: str = DEFAULT_STEM, model: str = DEFAULT_MODEL) -> tuple[dict[str, Path], Path]:
    prefix = _stem_file_prefix(watch_id, parsed, model=model)
    directory = Path(output_dir)
    destinations = {
        kind: directory / (prefix + "_" + kind + "." + fmt)
        for kind in expected_output_kinds(stem, model)
    }
    return destinations, directory / "metadata.json"


def result_from_manifest(wid: str, directory: Path, manifest: dict, status: str) -> ItemResult:
    outputs_map = manifest.get("outputs", {})
    stem_paths = {kind: str(resolve_output_path(directory, rec["file"]))
                  for kind, rec in outputs_map.items()}
    return ItemResult(
        wid, title=manifest.get("title") or wid, status=status,
        drums_path=stem_paths.get("drums"),
        nodrums_path=stem_paths.get("no_drums"),
        meta_path=str(directory / "metadata.json"), generation=manifest["generation"],
        stem_paths=stem_paths,
    )


def process_one(wid: str, args, progress=None, source_url=None, cancel_event=None) -> ItemResult:
    result = ItemResult(wid)
    source = source_url or wid

    def stage(name: str):
        check_cancelled(cancel_event)
        result.status = result.stage = name
        if progress is not None:
            progress(wid, name, 0, 0)

    try:
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
        with FileLock(str(directory.parent / ("." + args.audio_format + ".lock")), timeout=0):
            recover_directory(directory)
            if directory.exists() and not args.replace_existing:
                try:
                    manifest = read_manifest(directory)
                    if (manifest["watch_id"] != wid or manifest["format"] != args.audio_format
                            or manifest.get("model") != model
                            or manifest.get("stem", DEFAULT_STEM) != stem):
                        raise ValueError("output identity does not match")
                except (OSError, ValueError, KeyError) as exc:
                    raise ValueError("Incomplete or outdated outputs; use --replace-existing: %s" % exc) from exc
                return result_from_manifest(wid, directory, manifest, "skipped")

            stage("metadata")
            ffmpeg_dir = detect_ffmpeg(
                ffmpeg_dirs=[args.ffmpeg_dir] if getattr(args, "ffmpeg_dir", None) else []
            ).path
            if not ffmpeg_dir:
                raise RuntimeError("Install ffmpeg and ffprobe on PATH; run jamly-doctor for details")
            ffmpeg = str(Path(ffmpeg_dir) / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg"))
            ffprobe = str(Path(ffmpeg_dir) / ("ffprobe.exe" if os.name == "nt" else "ffprobe"))
            if not detect_engine():
                raise RuntimeError("Demucs is missing; reinstall Jamly")
            local_source = local_audio_path(source)
            if local_source is not None:
                metadata = _fetch_local_metadata(ffprobe, local_source, cancel_event)
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
                if local_source is not None:
                    audio = local_source
                else:
                    audio = download_audio(
                        build_download_cmd(command, wid, work, source, ffmpeg_dir), workdir, cancel_event
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
                manifest = dict(
                    meta_dict(watch_id=wid, source_url=source, title=metadata.get("title"),
                              artist=metadata.get("artist") or (parsed[0] if parsed else None),
                              uploader=metadata.get("uploader"), duration=metadata.get("duration_seconds")),
                    processing_version=PROCESSING_VERSION, generation=uuid.uuid4().hex,
                    model=model, stem=stem, format=args.audio_format, audio=audio_info,
                    outputs={name: {"file": path.name, "sha256": checksum(path)}
                             for name, path in destinations.items()},
                )
                with Path(meta).open("w", encoding="utf-8") as file:
                    json.dump(manifest, file, ensure_ascii=False, indent=2)
                    file.flush()
                    os.fsync(file.fileno())
                stage("publish")
                publish_directory(staged_dir, directory)
                return result_from_manifest(wid, directory, manifest, "done")
    except InterruptedError as exc:
        result.status, result.error = "cancelled", str(exc)
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
                    if item["status"] in ("error", "cancelled"):
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
                             outputs=result.outputs, generation=result.generation)
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
