"""Persistent JSON watch queue with a resumable done set."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import re


class QueueError(RuntimeError):
    pass


def default_queue_path(scope_dir: str = "") -> str:
    root = Path(scope_dir).expanduser().resolve() if scope_dir else Path.cwd()
    return str(root / "jamly_queue.json")


class WatchQueue:
    PENDING = "pending"
    DONE = "done"

    def __init__(self, path: str = "", items=None, done=None):
        self.path = str(Path(path).absolute()) if path else ""
        self._items = copy.deepcopy(items or [])
        for wid in done or []:
            if not any(item["watch_id"] == wid for item in self._items):
                self.add(wid)
            self.mark_done(wid)

    @classmethod
    def from_path(cls, path: str) -> "WatchQueue":
        queue = cls(path)
        if not Path(path).exists():
            return queue
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            done = set(data.get("done", []))
            for record in data.get("items", []):
                wid = record["watch_id"]
                status = record.get("status", "done" if wid in done else "pending")
                queue._items.append({
                    "watch_id": wid,
                    "source_url": record.get("source_url") or wid,
                    "title": record.get("title"),
                    "status": status,
                })
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise QueueError("Cannot load queue %s: %s" % (path, exc)) from exc
        return queue

    def add(self, watch_id: str, title=None, source_url=None) -> bool:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", watch_id):
            raise ValueError("invalid stable id")
        if any(item["watch_id"] == watch_id for item in self._items):
            return False
        self._items.append({
            "watch_id": watch_id,
            "source_url": source_url or watch_id,
            "title": title,
            "status": "pending",
        })
        return True

    def add_all(self, watch_ids, titles=None, source_urls=None) -> int:
        titles, source_urls = list(titles or []), list(source_urls or [])
        return sum(
            self.add(
                wid,
                titles[i] if i < len(titles) else None,
                source_urls[i] if i < len(source_urls) else None,
            )
            for i, wid in enumerate(watch_ids)
        )

    def remove(self, watch_id: str) -> bool:
        before = len(self._items)
        self._items = [item for item in self._items if item["watch_id"] != watch_id]
        return len(self._items) != before

    def mark_done(self, watch_id: str) -> None:
        for item in self._items:
            if item["watch_id"] == watch_id:
                item["status"] = "done"
                return
        raise QueueError("Unknown job: %s" % watch_id)

    def is_complete(self, watch_id: str) -> bool:
        return watch_id in self.done_ids()

    def done_ids(self) -> list[str]:
        return [item["watch_id"] for item in self._items if item["status"] == "done"]

    def pending_ids(self) -> list[str]:
        return [item["watch_id"] for item in self._items if item["status"] == "pending"]

    def pending_items(self) -> list[dict]:
        return [copy.deepcopy(item) for item in self._items if item["status"] == "pending"]

    def count_pending(self) -> int:
        return len(self.pending_ids())

    @property
    def items(self) -> list[dict]:
        return copy.deepcopy(self._items)

    def save(self) -> None:
        if not self.path:
            return
        path = Path(self.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"items": self.items, "done": self.done_ids()}, indent=2),
            encoding="utf-8",
        )

    def __len__(self) -> int:
        return len(self._items)
