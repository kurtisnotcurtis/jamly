"""Publish complete track directories, with rollback and interrupted-swap recovery."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil

from .models import DEFAULT_MODEL, DEFAULT_STEM, expected_output_kinds
from .normalize import LOCAL_AUDIO_EXTENSIONS

PROCESSING_VERSION = 2


def checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def audio_directory(directory: Path) -> Path:
    parts = directory.parts
    for index in range(len(parts) - 1, -1, -1):
        if parts[index] == ".jamly":
            return Path(*parts[:index]) if index > 0 else Path(".")
    return directory


def resolve_output_path(directory: Path, name: str) -> Path:
    # Staged directories hold audio files locally until publish_directory moves them to audio_directory.
    local = directory / name
    if local.exists() or local.is_symlink():
        return local
    return audio_directory(directory) / name


def resolve_manifest_original(directory: Path, manifest: dict) -> Path | None:
    """Resolve a manifest's retained original audio file strictly inside its export directory."""
    recorded = manifest.get("original")
    if not isinstance(recorded, str) or not recorded.strip():
        return None
    audio_root = audio_directory(directory).resolve()
    raw = Path(recorded)
    if raw.is_absolute():
        candidate = raw
    else:
        if raw.name != recorded or recorded in (".", ".."):
            return None
        candidate = audio_root / recorded
    if candidate.is_symlink() or not candidate.is_file():
        return None
    if candidate.suffix.lower() not in LOCAL_AUDIO_EXTENSIONS:
        return None
    try:
        resolved = candidate.resolve()
    except OSError:
        return None
    if resolved.parent != audio_root:
        return None
    return audio_directory(directory) / resolved.name


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


def _manifest_output_names(directory: Path) -> set[str]:
    if not (directory / "metadata.json").is_file():
        return set()
    try:
        data = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
        outputs = data.get("outputs") if isinstance(data, dict) else None
    except (OSError, ValueError):
        return set()
    if not isinstance(outputs, dict):
        return set()
    names = set()
    for record in outputs.values():
        name = record.get("file") if isinstance(record, dict) else None
        if isinstance(name, str) and Path(name).name == name and name not in (".", ".."):
            names.add(name)
    return names


def _remove_stale_flat_audio(audio_root: Path, old_names: set[str], new_names: set[str]) -> None:
    for name in old_names - new_names:
        target = audio_root / name
        if target.is_file() and not target.is_symlink():
            target.unlink(missing_ok=True)


def _restore_flat_audio(directory: Path) -> None:
    audio_root = audio_directory(directory)
    if audio_root == directory:
        return
    names = _manifest_output_names(directory)
    if not names:
        return
    audio_root.mkdir(parents=True, exist_ok=True)
    for name in names:
        if (directory / name).is_file():
            os.replace(directory / name, audio_root / name)


def recover_directory(destination: Path) -> None:
    backup = previous_directory(destination)
    if not backup.exists():
        return
    if not destination.exists():
        os.replace(backup, destination)
        _restore_flat_audio(destination)
    else:
        manifest = read_manifest(destination)
        audio_root = audio_directory(destination)
        if audio_root != destination:
            new_names = {rec["file"] for rec in manifest["outputs"].values()}
            _remove_stale_flat_audio(audio_root, _manifest_output_names(backup), new_names)
        shutil.rmtree(backup)


def _fsync_files(directory: Path) -> None:
    for path in directory.iterdir():
        if path.is_file():
            # Windows' FlushFileBuffers requires a handle opened for writing.
            with path.open("rb+") as file:
                os.fsync(file.fileno())


def _backup_flat_audio(destination: Path, audio_root: Path) -> set[str]:
    if audio_root == destination:
        return set()
    names = _manifest_output_names(destination)
    for fname in names:
        if (audio_root / fname).is_file():
            shutil.copy2(audio_root / fname, destination / fname)
    return names


def _move_flat_audio(destination: Path, audio_root: Path, manifest: dict) -> list[str]:
    if audio_root == destination:
        return []
    audio_root.mkdir(parents=True, exist_ok=True)
    moved = []
    for rec in manifest["outputs"].values():
        fname = rec["file"]
        if (destination / fname).is_file():
            os.replace(destination / fname, audio_root / fname)
            moved.append(fname)
    return moved


def publish_directory(staged: Path, destination: Path) -> None:
    """The caller holds the track/format lock. Readers see a whole generation or no directory."""
    manifest = read_manifest(staged)
    _fsync_files(staged)
    audio_root = audio_directory(destination)
    backup = previous_directory(destination)
    recover_directory(destination)
    old_names: set[str] = set()
    if destination.exists():
        old_names = _backup_flat_audio(destination, audio_root)
        os.replace(destination, backup)
    new_names = {rec["file"] for rec in manifest["outputs"].values()}
    moved: list[str] = []
    try:
        os.replace(staged, destination)
        moved = _move_flat_audio(destination, audio_root, manifest)
        if audio_root != destination:
            _remove_stale_flat_audio(audio_root, old_names, new_names)
    except OSError:
        if audio_root != destination:
            for fname in set(moved) - old_names:
                (audio_root / fname).unlink(missing_ok=True)
        if destination.exists():
            shutil.rmtree(destination, ignore_errors=True)
        if backup.exists():
            os.replace(backup, destination)
            _restore_flat_audio(destination)
        raise
    if backup.exists():
        shutil.rmtree(backup)
