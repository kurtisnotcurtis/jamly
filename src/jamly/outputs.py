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


def read_manifest_structural(directory: Path) -> dict:
    """Validate a manifest's structure without requiring its audio files to still exist."""
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
    return data


def read_manifest(directory: Path, *, verify_hashes: bool = True) -> dict:
    data = read_manifest_structural(directory)
    for record in data["outputs"].values():
        path = resolve_output_path(directory, record["file"])
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


def _fsync_files(directory: Path) -> None:
    for path in directory.iterdir():
        if path.is_file():
            # Windows' FlushFileBuffers requires a handle opened for writing.
            with path.open("rb+") as file:
                os.fsync(file.fileno())


def _backup_flat_audio(destination: Path, audio_root: Path) -> None:
    if audio_root == destination or not (destination / "metadata.json").is_file():
        return
    try:
        old_data = json.loads((destination / "metadata.json").read_text(encoding="utf-8"))
        outputs = old_data.get("outputs") if isinstance(old_data, dict) else None
    except (OSError, ValueError):
        return
    if not isinstance(outputs, dict):
        return
    for rec in outputs.values():
        fname = rec.get("file") if isinstance(rec, dict) else None
        if isinstance(fname, str) and (audio_root / fname).is_file():
            shutil.copy2(audio_root / fname, destination / fname)


def _move_flat_audio(destination: Path, audio_root: Path, manifest: dict) -> None:
    if audio_root == destination:
        return
    audio_root.mkdir(parents=True, exist_ok=True)
    for rec in manifest["outputs"].values():
        fname = rec["file"]
        if (destination / fname).is_file():
            os.replace(destination / fname, audio_root / fname)


def publish_directory(staged: Path, destination: Path) -> None:
    """The caller holds the track/format lock. Readers see a whole generation or no directory."""
    manifest = read_manifest(staged)
    _fsync_files(staged)
    audio_root = audio_directory(destination)
    backup = previous_directory(destination)
    recover_directory(destination)
    if destination.exists():
        _backup_flat_audio(destination, audio_root)
        os.replace(destination, backup)
    try:
        os.replace(staged, destination)
        _move_flat_audio(destination, audio_root, manifest)
    except OSError:
        if destination.exists():
            shutil.rmtree(destination, ignore_errors=True)
        if backup.exists():
            os.replace(backup, destination)
            _restore_flat_audio(destination)
        raise
    if backup.exists():
        shutil.rmtree(backup)
