"""Durable job state, explicit retries, and exclusive queue ownership."""
from __future__ import annotations

from contextlib import contextmanager
import copy
import json
import os
from pathlib import Path
import threading
import time
import uuid

from filelock import FileLock, Timeout

from .models import DEFAULT_AUDIO_FORMAT, DEFAULT_MODEL, DEFAULT_STEM
from .normalize import is_valid_stable_id


class QueueError(RuntimeError):
    pass


@contextmanager
def queue_owner(path: str):
    Path(path).absolute().parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = FileLock(str(Path(path).absolute()) + ".owner.lock")
    try:
        lock.acquire(timeout=0)
    except Timeout as exc:
        raise QueueError(
            "This queue is already owned by another Jamly process. "
            "Submit through the browser/API or stop that process before using the CLI."
        ) from exc
    try:
        yield
    finally:
        lock.release()


class WatchQueue:
    STATUSES = {"pending", "running", "done", "skipped", "error", "cancelled"}

    def __init__(self, path: str = "", items=None, *, _shared: bool = False):
        self.path = str(Path(path).absolute()) if path else ""
        self._items = list(items or []) if _shared else copy.deepcopy(items or [])
        self._by_id = {item["watch_id"]: item for item in self._items}
        self._index = {item["watch_id"]: idx for idx, item in enumerate(self._items)}
        self._revision = 0
        self._save_lock = threading.Lock()

    def _clone_entry(self, watch_id: str) -> dict | None:
        idx = self._index.get(watch_id)
        if idx is None:
            return None
        cloned = copy.deepcopy(self._items[idx])
        self._items[idx] = cloned
        self._by_id[watch_id] = cloned
        return cloned

    @classmethod
    def _parse_record(cls, record: object, done: set[str]) -> dict:
        if not isinstance(record, dict):
            raise ValueError("each job must be an object")
        wid = record.get("watch_id")
        if not is_valid_stable_id(wid):
            raise ValueError("job has an invalid stable id")
        status = record.get("status", "done" if wid in done else "pending")
        if status not in cls.STATUSES:
            raise ValueError("unknown job status: %r" % status)
        # Only a job interrupted while running is automatically resumed.
        if status == "running":
            status = "pending"
        source = record.get("source_url") or wid
        if not isinstance(source, str):
            raise ValueError("source_url must be a string")
        original_path = record.get("original_path")
        if original_path is not None and not isinstance(original_path, str):
            raise ValueError("original_path must be a string")
        options = record.get("options", {})
        if not isinstance(options, dict):
            raise ValueError("job options must be an object")
        exports = record.get("exports", {})
        if not isinstance(exports, dict):
            raise ValueError("job exports must be an object")
        return dict(
            record, watch_id=wid, source_url=source, original_path=original_path,
            status=status, options=options, exports=exports, title=record.get("title"),
        )

    @classmethod
    def from_path(cls, path: str) -> "WatchQueue":
        queue = cls(path)
        if not Path(path).exists():
            return queue
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("items"), list):
                raise ValueError("expected an object with an items list")
            done = data.get("done", [])
            if not isinstance(done, list):
                raise ValueError("done must be a list")
            done_set = set(done)
            queue._revision = int(data.get("revision", 0))
            seen = set()
            for record in data["items"]:
                entry = cls._parse_record(record, done_set)
                wid = entry["watch_id"]
                if wid in seen:
                    continue
                seen.add(wid)
                queue._index[wid] = len(queue._items)
                queue._items.append(entry)
                queue._by_id[wid] = entry
        except (OSError, ValueError, TypeError) as exc:
            raise QueueError("Cannot load queue %s: %s" % (path, exc)) from exc
        return queue

    def add(self, watch_id: str, title=None, source_url=None, *, retry=False, options=None) -> bool:
        if not is_valid_stable_id(watch_id):
            raise ValueError("invalid stable id")
        now = time.time()
        previous = self._by_id.get(watch_id)
        if previous is not None:
            if previous["status"] in ("pending", "running") or not retry:
                return False
            updated = self._clone_entry(watch_id)
            updated.update(
                status="pending", error=None, outputs={}, generation=None,
                source_url=source_url or updated["source_url"],
                options=copy.deepcopy(options if options is not None else updated["options"]),
                requested_at=now, started_at=None, finished_at=None,
                duration_seconds=None, updated_at=now,
            )
            if title:
                updated["title"] = title
            return True
        entry = {
            "watch_id": watch_id, "source_url": source_url or watch_id, "original_path": None,
            "title": title, "status": "pending", "error": None, "outputs": {}, "exports": {},
            "options": copy.deepcopy(options or {}),
            "attempts": 0, "requested_at": now, "started_at": None,
            "finished_at": None, "duration_seconds": None, "updated_at": now,
        }
        self._index[watch_id] = len(self._items)
        self._items.append(entry)
        self._by_id[watch_id] = entry
        return True

    def add_all(self, watch_ids, titles=None, source_urls=None, *, retry=False, options=None) -> int:
        titles, source_urls = list(titles or []), list(source_urls or [])
        return sum(self.add(
            wid, titles[index] if index < len(titles) else None,
            source_urls[index] if index < len(source_urls) else None,
            retry=retry, options=options,
        ) for index, wid in enumerate(watch_ids))

    def get(self, watch_id: str, default=None) -> dict | None:
        item = self._by_id.get(watch_id)
        return copy.deepcopy(item) if item is not None else default

    def copy(self) -> "WatchQueue":
        result = WatchQueue(self.path, items=self._items, _shared=True)
        result._revision = self._revision
        return result

    def remove(self, watch_id: str) -> bool:
        if watch_id not in self._by_id:
            return False
        self._by_id.pop(watch_id, None)
        self._items = [item for item in self._items if item["watch_id"] != watch_id]
        self._index = {item["watch_id"]: idx for idx, item in enumerate(self._items)}
        return True

    def clear_terminal(self) -> int:
        before = len(self._items)
        self._items = [item for item in self._items if item["status"] in ("pending", "running")]
        self._by_id = {item["watch_id"]: item for item in self._items}
        self._index = {item["watch_id"]: idx for idx, item in enumerate(self._items)}
        return before - len(self._items)

    @staticmethod
    def _export_key(model: str, stem: str, audio_format: str) -> str:
        return (
            "%s/%s" % (model, audio_format)
            if stem == DEFAULT_STEM
            else "%s/%s/%s" % (model, stem, audio_format)
        )

    @staticmethod
    def _new_restored_entry(watch_id: str, *, source_url: str | None, original_path: str | None,
                            title: str | None, model: str, stem: str, audio_format: str,
                            outputs: dict[str, str], generation: str, finished_at: float | None,
                            export_key: str, export_entry: dict) -> dict:
        return {
            "watch_id": watch_id,
            "source_url": source_url or watch_id,
            "original_path": original_path,
            "title": title,
            "status": "done",
            "error": None,
            "outputs": dict(outputs),
            "generation": generation,
            "exports": {export_key: export_entry},
            "options": {"model": model, "stem": stem, "audio_format": audio_format},
            "attempts": 1,
            "requested_at": None,
            "started_at": None,
            "finished_at": finished_at,
            "duration_seconds": None,
            "updated_at": finished_at or time.time(),
        }

    @staticmethod
    def _should_promote_export(entry: dict, *, model: str, stem: str, audio_format: str, generation: str) -> bool:
        if entry["status"] in ("pending", "running"):
            return False
        if not entry.get("outputs"):
            return True
        opts = entry.get("options") or {}
        same_selection = (
            opts.get("model", DEFAULT_MODEL) == model
            and opts.get("stem", DEFAULT_STEM) == stem
            and opts.get("audio_format", "mp3") == audio_format
        )
        return same_selection and entry.get("generation") != generation

    def restore_export(
        self,
        watch_id: str,
        *,
        source_url: str | None = None,
        original_path: str | None = None,
        title: str | None = None,
        model: str = DEFAULT_MODEL,
        stem: str = DEFAULT_STEM,
        audio_format: str = DEFAULT_AUDIO_FORMAT,
        outputs: dict[str, str],
        generation: str,
        finished_at: float | None = None,
    ) -> bool:
        if not is_valid_stable_id(watch_id):
            raise ValueError("invalid stable id")
        if source_url is not None and not isinstance(source_url, str):
            raise ValueError("source_url must be a string")
        if original_path is not None and not isinstance(original_path, str):
            raise ValueError("original_path must be a string")
        if title is not None and not isinstance(title, str):
            raise ValueError("title must be a string")
        if not isinstance(generation, str) or not generation:
            raise ValueError("generation must be a non-empty string")
        if not isinstance(outputs, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in outputs.items()
        ):
            raise ValueError("outputs must be a string mapping")
        export_key = self._export_key(model, stem, audio_format)
        export_entry = {
            "model": model, "stem": stem, "audio_format": audio_format,
            "outputs": dict(outputs), "generation": generation,
        }
        existing = self._by_id.get(watch_id)
        if existing is None:
            new_entry = self._new_restored_entry(
                watch_id, source_url=source_url, original_path=original_path, title=title,
                model=model, stem=stem, audio_format=audio_format, outputs=outputs,
                generation=generation, finished_at=finished_at,
                export_key=export_key, export_entry=export_entry,
            )
            self._index[watch_id] = len(self._items)
            self._items.append(new_entry)
            self._by_id[watch_id] = new_entry
            return True
        updated = copy.deepcopy(existing)
        changed = False
        exports = dict(updated.get("exports") or {})
        if exports.get(export_key) != export_entry:
            exports[export_key] = export_entry
            updated["exports"] = exports
            changed = True
        if title and not updated.get("title"):
            updated["title"] = title
            changed = True
        if source_url and updated.get("source_url") == watch_id and source_url != watch_id:
            updated["source_url"] = source_url
            changed = True
        if original_path and not updated.get("original_path"):
            updated["original_path"] = original_path
            changed = True
        if finished_at and updated.get("finished_at") is None and updated["status"] in ("done", "skipped"):
            updated["finished_at"] = finished_at
            changed = True
        if self._should_promote_export(updated, model=model, stem=stem, audio_format=audio_format, generation=generation):
            updated.update(
                status="done", error=None, outputs=dict(outputs), generation=generation,
                options={"model": model, "stem": stem, "audio_format": audio_format},
                finished_at=finished_at if finished_at is not None else updated.get("finished_at"),
                updated_at=finished_at or time.time(),
            )
            changed = True
        if changed:
            idx = self._index[watch_id]
            self._items[idx] = updated
            self._by_id[watch_id] = updated
        return changed

    def mark_running(self, watch_id: str) -> None:
        self.finish(watch_id, "running")
        now = time.time()
        item = self._by_id.get(watch_id)
        if item is not None:
            item["attempts"] = item.get("attempts", 0) + 1
            item["started_at"] = now
            item["finished_at"] = None
            item["duration_seconds"] = None
            if item.get("requested_at") is None:
                item["requested_at"] = now

    def finish(self, watch_id: str, status: str, *, error=None, outputs=None,
               title=None, generation=None, original_path=None) -> None:
        if status not in self.STATUSES:
            raise ValueError("unknown job status")
        item = self._clone_entry(watch_id)
        if item is None:
            raise QueueError("Unknown job: %s" % watch_id)
        now = time.time()
        item.update(status=status, error=error, updated_at=now)
        if status in ("done", "skipped", "error", "cancelled"):
            item["finished_at"] = now
            started = item.get("started_at")
            item["duration_seconds"] = (
                round(max(0.0, now - started), 2)
                if isinstance(started, (int, float))
                else None
            )
        if outputs is not None:
            item["outputs"] = dict(outputs)
        if title:
            item["title"] = title
        if original_path:
            item["original_path"] = original_path
        if generation is not None:
            item["generation"] = generation
        if status in ("done", "skipped") and item.get("outputs") and item.get("generation"):
            opts = item.get("options") or {}
            model = opts.get("model", DEFAULT_MODEL)
            stem = opts.get("stem", DEFAULT_STEM)
            audio_format = opts.get("audio_format", DEFAULT_AUDIO_FORMAT)
            exports = dict(item.get("exports") or {})
            export_key = self._export_key(model, stem, audio_format)
            exports[export_key] = {
                "model": model,
                "stem": stem,
                "audio_format": audio_format,
                "outputs": dict(item["outputs"]),
                "generation": item["generation"],
            }
            item["exports"] = exports

    def done_ids(self) -> list[str]:
        return [item["watch_id"] for item in self._items if item["status"] in ("done", "skipped")]

    def pending_ids(self) -> list[str]:
        return [item["watch_id"] for item in self._items if item["status"] == "pending"]

    def pending_items(self) -> list[dict]:
        return [copy.deepcopy(item) for item in self._items if item["status"] == "pending"]

    @property
    def items(self) -> list[dict]:
        return copy.deepcopy(self._items)

    def save(self) -> None:
        if not self.path:
            return
        path = Path(self.path)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._save_lock, FileLock(self.path + ".write.lock", timeout=5):
            try:
                current = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
                if not isinstance(current, dict):
                    raise ValueError("queue must be an object")
            except ValueError as exc:
                raise QueueError("Queue changed to an invalid document; refusing to overwrite it") from exc
            if current.get("revision", 0) != self._revision:
                raise QueueError("Queue changed in another process; refusing to overwrite newer jobs")
            next_revision = self._revision + 1
            data = {"revision": next_revision, "updated_at": time.time(), "items": self._items}
            temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
            try:
                fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as file:
                    json.dump(data, file, ensure_ascii=False, indent=2)
                    file.flush()
                    os.fsync(file.fileno())
                os.replace(temporary, path)
                self._revision = next_revision
            finally:
                temporary.unlink(missing_ok=True)

    def __len__(self) -> int:
        return len(self._items)
