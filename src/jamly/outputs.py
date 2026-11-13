"""Publish complete track directories, with rollback and interrupted-swap recovery."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil

from .models import DEFAULT_MODEL, DEFAULT_STEM, expected_output_kinds

PROCESSING_VERSION = 2


def checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def audio_directory(directory: Path) -> Path:
    parts = directory.parts
    if ".jamly" in parts:
        index = parts.index(".jamly")
        return Path(*parts[:index])
    return directory


def resolve_output_path(directory: Path, name: str) -> Path:
    # Staged directories hold audio files locally until publish_directory moves them to audio_directory.
    local = directory / name
    if local.exists() or local.is_symlink():
        return local
    return audio_directory(directory) / name


def read_manifest(directory: Path, *, verify_hashes: bool = True) -> dict:
    data = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("invalid output manifest")
    if data.get("processing_version") != PROCESSING_VERSION:
        raise ValueError("outputs were made by a different processing version")
    if not isinstance(data.get("generation"), str) or not data["generation"]:
        raise ValueError("output generation is missing")
    stem = data.get("stem", DEFAULT_STEM)
    model = data.get("model", DEFAULT_MODEL)
    expected = set(expected_output_kinds(stem, model))
    if not isinstance(data.get("outputs"), dict) or set(data["outputs"]) != expected:
        raise ValueError("the output pair is incomplete")
    for record in data["outputs"].values():
        if not isinstance(record, dict):
            raise ValueError("invalid output record")
        name = record.get("file")
        if not isinstance(name, str) or Path(name).name != name or name in (".", ".."):
            raise ValueError("invalid output filename")
        path = resolve_output_path(directory, name)
        if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
            raise ValueError("missing output: %s" % path)
        if verify_hashes and checksum(path) != record.get("sha256"):
            raise ValueError("output checksum mismatch: %s" % path)
    return data


def previous_directory(destination: Path) -> Path:
    return destination.with_name("." + destination.name + ".previous")


def _restore_flat_audio(directory: Path) -> None:
    audio_root = audio_directory(directory)
    if audio_root == directory or not (directory / "metadata.json").is_file():
        return
    try:
        data = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
        outputs = data.get("outputs", {}) if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return
    audio_root.mkdir(parents=True, exist_ok=True)
    for record in outputs.values():
        name = record.get("file") if isinstance(record, dict) else None
        if isinstance(name, str) and (directory / name).is_file():
            os.replace(directory / name, audio_root / name)


def recover_directory(destination: Path) -> None:
    backup = previous_directory(destination)
    if not backup.exists():
        return
    if not destination.exists():
        os.replace(backup, destination)
        _restore_flat_audio(destination)
    else:
        read_manifest(destination)
        shutil.rmtree(backup)


def publish_directory(staged: Path, destination: Path) -> None:
    """The caller holds the track/format lock. Readers see a whole generation or no directory."""
    manifest = read_manifest(staged)
    for path in staged.iterdir():
        if path.is_file():
            # Windows' FlushFileBuffers requires a handle opened for writing.
            with path.open("rb+") as file:
                os.fsync(file.fileno())
    audio_root = audio_directory(destination)
    backup = previous_directory(destination)
    recover_directory(destination)
    if destination.exists():
        if audio_root != destination and (destination / "metadata.json").is_file():
            try:
                old_data = json.loads((destination / "metadata.json").read_text(encoding="utf-8"))
                for rec in (old_data.get("outputs") or {}).values():
                    fname = rec.get("file") if isinstance(rec, dict) else None
                    if isinstance(fname, str) and (audio_root / fname).is_file():
                        shutil.copy2(audio_root / fname, destination / fname)
            except (OSError, ValueError):
                pass
        os.replace(destination, backup)
    try:
        os.replace(staged, destination)
        if audio_root != destination:
            audio_root.mkdir(parents=True, exist_ok=True)
            published_hashes = {}
            for rec in manifest["outputs"].values():
                fname = rec["file"]
                published_hashes[fname] = rec["sha256"]
                if (destination / fname).is_file():
                    os.replace(destination / fname, audio_root / fname)
            wid = manifest.get("watch_id")
            model = manifest.get("model")
            model_dir = (audio_root / ".jamly" / wid / model) if (wid and model) else None
            if model_dir and model_dir.is_dir():
                for other_meta in model_dir.rglob("metadata.json"):
                    if other_meta.parent == destination or ".previous" in other_meta.parent.name:
                        continue
                    try:
                        other_data = json.loads(other_meta.read_text(encoding="utf-8"))
                        changed = False
                        for other_rec in (other_data.get("outputs") or {}).values():
                            fname = other_rec.get("file") if isinstance(other_rec, dict) else None
                            if fname in published_hashes and other_rec.get("sha256") != published_hashes[fname]:
                                other_rec["sha256"] = published_hashes[fname]
                                changed = True
                        if changed:
                            other_meta.write_text(
                                json.dumps(other_data, ensure_ascii=False, indent=2), encoding="utf-8"
                            )
                    except (OSError, ValueError):
                        pass
    except OSError:
        if destination.exists():
            shutil.rmtree(destination, ignore_errors=True)
        if backup.exists():
            os.replace(backup, destination)
            _restore_flat_audio(destination)
        raise
    if backup.exists():
        shutil.rmtree(backup)
