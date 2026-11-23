"""Unit tests for queue.py (persistent, resumable input queue). No network/GPU."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from jamly.watch_queue import QueueError, WatchQueue, queue_owner


class DedupTests(unittest.TestCase):
    def test_add_dedupes_by_watch_id_order_preserved(self):
        # WatchQueue stores already-normalized ids (gather_inputs normalizes URL->id upstream);
        # so dedup is by exact id, and insertion order is preserved.
        q = WatchQueue()
        self.assertTrue(q.add("aBcDeFg1234"))
        self.assertFalse(q.add("aBcDeFg1234"), "exact duplicate rejected")
        self.assertTrue(q.add("zYxWvUt5678"))
        self.assertEqual(q.pending_ids(), ["aBcDeFg1234", "zYxWvUt5678"])

    def test_add_all_returns_newly_added_count(self):
        q = WatchQueue()
        ids = ["a1", "b2", "a1", "c3"]
        self.assertEqual(q.add_all(ids), 3)   # a1 counted once
        self.assertEqual(len(q), 3)

    def test_remove_drops_item(self):
        q = WatchQueue()
        q.add("x")
        self.assertTrue(q.remove("x"))
        self.assertFalse(q.remove("x"))
        self.assertNotIn("x", q.done_ids())


class PersistenceTests(unittest.TestCase):
    def _tmp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return directory.name

    def test_save_reload_roundtrip_without_legacy_done_list(self):
        d = self._tmp()
        q = WatchQueue(path=os.path.join(d, "queue.json"))
        for wid in ("a", "b", "c"):
            self.assertTrue(q.add(wid))
        q.finish("b", "done")
        q.save()
        raw = json.loads(Path(q.path).read_text(encoding="utf-8"))
        self.assertNotIn("done", raw)
        reloaded = WatchQueue.from_path(q.path)
        self.assertEqual(reloaded.pending_ids(), ["a", "c"])
        self.assertEqual(reloaded.done_ids(), ["b"])
        q.add("d")
        q.save()
        again = WatchQueue.from_path(q.path)
        self.assertIn("d", again.pending_ids())
        self.assertIn("b", reloaded.done_ids())

    def test_legacy_done_set_is_still_readable_on_load(self):
        d = self._tmp()
        path = Path(d) / "legacy.json"
        path.write_text(json.dumps({
            "items": [{"watch_id": "done_id"}, {"watch_id": "todo_id"}],
            "done": ["done_id"],
        }), encoding="utf-8")
        reloaded = WatchQueue.from_path(str(path))
        self.assertEqual(reloaded.done_ids(), ["done_id"])
        self.assertEqual(reloaded.pending_ids(), ["todo_id"])

    def test_source_url_and_original_path_survive_reload_independently(self):
        d = self._tmp()
        q = WatchQueue(path=os.path.join(d, "queue.json"))
        q.add("src-abc", source_url="https://soundcloud.com/artist/song")
        q.finish("src-abc", "done", original_path="/exports/song_original.m4a")
        q.save()
        reloaded = WatchQueue.from_path(q.path)
        record = reloaded.get("src-abc")
        self.assertEqual(record["source_url"], "https://soundcloud.com/artist/song")
        self.assertEqual(record["original_path"], "/exports/song_original.m4a")

    def test_from_missing_file_is_empty(self):
        q = WatchQueue.from_path(os.path.join(self._tmp(), "does_not_exist.json"))
        self.assertEqual(len(q), 0)

    def test_invalid_json_is_reported_without_erasing_history(self):
        path = os.path.join(self._tmp(), "bad.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{not valid json")
        with self.assertRaises(QueueError):
            WatchQueue.from_path(path)

    def test_copy_on_write_does_not_mutate_original_queue(self):
        q = WatchQueue()
        q.add("job1", source_url="https://example.test/1")
        q.add("job2", source_url="https://example.test/2")
        candidate = q.copy()
        candidate.mark_running("job1")
        candidate.finish("job1", "done", outputs={"drums": "/out/d.wav"}, generation="g1")
        candidate.add("job3")
        self.assertEqual(q.get("job1")["status"], "pending")
        self.assertEqual(q.get("job1")["attempts"], 0)
        self.assertIsNone(q.get("job3"))


class AccessorsTests(unittest.TestCase):
    def _tmp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return directory.name

    def test_done_ids_tracks_completed_and_survives_reload(self):
        d = self._tmp()
        q = WatchQueue(path=os.path.join(d, "queue.json"))
        for wid in ("a", "b", "c"):
            self.assertTrue(q.add(wid))
        self.assertEqual(q.done_ids(), [])

        q.finish("a", "done")
        q.finish("c", "done")
        self.assertEqual(q.done_ids(), ["a", "c"])
        self.assertEqual(len(q.items), 3)

        q.save()
        reloaded = WatchQueue.from_path(q.path)
        self.assertEqual(reloaded.done_ids(), ["a", "c"])
        self.assertIn("b", reloaded.pending_ids())

    def test_items_exposes_raw_records_with_expected_keys(self):
        d = self._tmp()
        q = WatchQueue(path=os.path.join(d, "queue.json"))
        q.add("vid123")
        recs = {i["watch_id"]: i for i in q.items}
        self.assertEqual(len(recs), 1)
        self.assertIn("title", recs["vid123"])
        self.assertIn("status", recs["vid123"])
        q.finish("vid123", "done")
        self.assertEqual(len(q.items), 1)
        self.assertEqual(q.items[0]["status"], "done")
        self.assertIn("vid123", q.done_ids())


class RecoveryTests(unittest.TestCase):
    def test_errors_are_durable_and_only_explicitly_retried(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "queue.json")
            queue = WatchQueue(path)
            queue.add("job", source_url="https://example.test/a")
            queue.finish("job", "error", error="network failed")
            queue.save()
            loaded = WatchQueue.from_path(path)
            self.assertEqual(loaded.get("job")["status"], "error")
            self.assertEqual(loaded.pending_ids(), [])
            self.assertEqual(loaded.done_ids(), [])
            self.assertTrue(loaded.add("job", retry=True))
            self.assertEqual(loaded.pending_ids(), ["job"])

    def test_done_job_can_be_resubmitted_with_different_options(self):
        queue = WatchQueue()
        queue.add("job", options={"model": "htdemucs", "audio_format": "mp3", "output_dir": "/first"})
        queue.finish("job", "done", outputs={"drums": "/first/d.mp3"}, generation="gen-1")
        self.assertTrue(queue.add(
            "job", retry=True, options={"model": "htdemucs_6s", "audio_format": "wav", "output_dir": "/second"}
        ))
        record = queue.get("job")
        self.assertEqual(record["options"]["output_dir"], "/second")
        self.assertEqual(record["options"]["audio_format"], "wav")
        self.assertEqual(record["options"]["model"], "htdemucs_6s")
        self.assertIn("htdemucs/mp3", record["exports"])
        self.assertEqual(record["exports"]["htdemucs/mp3"]["generation"], "gen-1")

    def test_interrupted_running_job_is_resumed(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "queue.json")
            queue = WatchQueue(path)
            queue.add("job")
            queue.mark_running("job")
            queue.save()
            self.assertEqual(WatchQueue.from_path(path).pending_ids(), ["job"])

    def test_stale_writer_cannot_lose_newer_jobs(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "queue.json")
            first, second = WatchQueue(path), WatchQueue(path)
            first.add("first")
            first.save()
            second.add("second")
            with self.assertRaises(QueueError):
                second.save()
            self.assertEqual(WatchQueue.from_path(path).pending_ids(), ["first"])

    def test_queue_has_only_one_owner_across_processes(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "queue.json")
            code = (
                "import sys\nfrom jamly.watch_queue import queue_owner,QueueError\n"
                "try:\n with queue_owner(sys.argv[1]): pass\n"
                "except QueueError: sys.exit(7)\n"
            )
            with queue_owner(path):
                result = subprocess.run([sys.executable, "-c", code, path], timeout=10)
            self.assertEqual(result.returncode, 7)
            with queue_owner(path):
                pass

    def test_clear_terminal_preserves_active_and_restore_export_repopulates(self):
        queue = WatchQueue()
        queue.add("active")
        queue.mark_running("active")
        queue.add("finished", source_url="https://youtu.be/finished")
        queue.finish("finished", "done", outputs={"drums": "/out/d.mp3"}, generation="gen-1")
        queue.add("failed")
        queue.finish("failed", "error", error="boom")

        removed = queue.clear_terminal()
        self.assertEqual(removed, 2)
        self.assertEqual([item["watch_id"] for item in queue.items], ["active"])

        changed = queue.restore_export(
            "finished",
            source_url="https://youtu.be/finished",
            original_path="/out/finished_original.m4a",
            title="Finished Song",
            model="htdemucs_ft",
            stem="vocals",
            audio_format="wav",
            outputs={"vocals": "/out/v.wav", "no_vocals": "/out/nv.wav", "metadata": "/out/.jamly/finished/meta.json"},
            generation="gen-2",
        )
        self.assertTrue(changed)
        restored = queue.get("finished")
        self.assertEqual(restored["status"], "done")
        self.assertEqual(restored["title"], "Finished Song")
        self.assertEqual(restored["original_path"], "/out/finished_original.m4a")
        self.assertIn("htdemucs_ft/vocals/wav", restored["exports"])
        self.assertFalse(queue.restore_export(
            "finished",
            source_url="https://youtu.be/finished",
            original_path="/out/finished_original.m4a",
            title="Finished Song",
            model="htdemucs_ft",
            stem="vocals",
            audio_format="wav",
            outputs={"vocals": "/out/v.wav", "no_vocals": "/out/nv.wav", "metadata": "/out/.jamly/finished/meta.json"},
            generation="gen-2",
        ))

    def test_job_records_requested_started_finished_and_duration(self):
        with patch("jamly.watch_queue.time.time", return_value=100.0) as clock:
            queue = WatchQueue()
            queue.add("job")
            added = queue.get("job")
            self.assertEqual(added["requested_at"], 100.0)
            self.assertIsNone(added["started_at"])
            self.assertIsNone(added["finished_at"])
            self.assertIsNone(added["duration_seconds"])
            clock.return_value = 105.0
            queue.mark_running("job")
            running = queue.get("job")
            self.assertEqual(running["started_at"], 105.0)
            clock.return_value = 117.5
            queue.finish("job", "done", outputs={"drums": "/out/d.mp3"}, generation="gen-1")
            done = queue.get("job")
            self.assertEqual(done["finished_at"], 117.5)
            self.assertEqual(done["duration_seconds"], 12.5)
            clock.return_value = 200.0
            self.assertTrue(queue.add("job", retry=True))
            retried = queue.get("job")
            self.assertEqual(retried["requested_at"], 200.0)
            self.assertIsNone(retried["started_at"])
            self.assertIsNone(retried["finished_at"])
            self.assertIsNone(retried["duration_seconds"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
