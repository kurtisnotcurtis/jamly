from contextlib import ExitStack
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from filelock import FileLock
import numpy as np
import soundfile as sf

from jamly import outputs, run
from jamly.bootstrap import ToolStatus
from jamly.core import RunResult
from jamly.normalize import Input, make_input
from jamly.watch_queue import WatchQueue


class MetadataTests(unittest.TestCase):
    def test_metadata_fields_and_option_terminator(self):
        data = {"title": "Band - Song", "artist": "Band", "duration": 12.5}
        with patch.object(run, "_run", return_value=RunResult([], 0, json.dumps(data), "")) as called:
            result = run._fetch_item_metadata("yt-dlp", "-abcdefghij")
        self.assertEqual(result["title"], "Band - Song")
        self.assertEqual(result["duration_seconds"], 12)
        self.assertEqual(called.call_args.args[0][-2:], ["--", "-abcdefghij"])

    def test_collections_and_live_media_are_rejected(self):
        cases = [
            {"_type": "playlist", "entries": [{"url": "first"}, {"url": "second"}]},
            {"_type": "multi_video", "entries": []},
            {"is_live": True},
            {"is_upcoming": True},
        ]
        for data in cases:
            with self.subTest(data=data), patch.object(
                run, "_run", return_value=RunResult([], 0, json.dumps(data), "")
            ):
                with self.assertRaises(ValueError):
                    run._fetch_item_metadata("yt-dlp", "https://example.test/media")

    def test_metadata_errors_are_not_silently_discarded(self):
        with patch.object(run, "_run", return_value=RunResult([], 1, "", "network failed")):
            with self.assertRaisesRegex(RuntimeError, "network failed"):
                run._fetch_item_metadata("yt-dlp", "https://example.test/media")


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.wid = "dQw4w9WgXcQ"
        self.args = run.build_parser().parse_args([
            "-o", str(self.root / "output"), "--audio-format", "wav",
            "--state-dir", str(self.root / "state"),
        ])
        self.drum_level = .3
        self.patches = ExitStack()
        self.addCleanup(self.patches.close)
        for target, replacement in (
            ("detect_ffmpeg", lambda **_kwargs: ToolStatus("ffmpeg", "/tools", True)),
            ("detect_engine", lambda: "htdemucs"),
            ("_fetch_item_metadata", lambda *_args: {
                "title": "Band - Song", "artist": "Band", "duration_seconds": 1,
            }),
            ("download_audio", self.download),
            ("separate_stems", self.separate),
        ):
            self.patches.enter_context(patch.object(run, target, side_effect=replacement))

    def download(self, _command, workdir, _cancel):
        path = workdir / "source.wav"
        sf.write(path, np.zeros((100, 2)), 44100, subtype="FLOAT")
        return path

    def separate(self, _audio, workdir, *_args, **_kwargs):
        result = {}
        for name, gain in (("drums", self.drum_level), ("bass", .2), ("vocals", .2), ("other", .2)):
            path = Path(workdir) / (name + ".wav")
            sf.write(path, np.full((100, 2), gain), 44100, subtype="FLOAT")
            result[name] = str(path)
        return result

    def process(self, **kwargs):
        return run.process_one(self.wid, self.args, source_url="https://youtu.be/" + self.wid, **kwargs)

    def test_complete_pair_has_manifest_and_live_stages(self):
        stages = []
        result = self.process(progress=lambda _id, stage, *_args: stages.append(stage))
        self.assertEqual(result.status, "done", result.error)
        self.assertEqual(stages, ["metadata", "download", "separate", "mix", "publish"])
        directory = Path(result.meta_path).parent
        self.assertEqual(Path(result.outputs["drums"]).parent, self.root / "output")
        self.assertEqual(directory, self.root / "output" / ".jamly" / self.wid / "htdemucs" / "wav")
        manifest = outputs.read_manifest(directory)
        self.assertEqual(manifest["source_url"], "https://youtu.be/" + self.wid)
        self.assertEqual(manifest["generation"], result.generation)
        self.assertEqual(sf.info(result.outputs["drums"]).subtype, "FLOAT")
        self.assertEqual(sf.info(result.outputs["no_drums"]).subtype, "FLOAT")
        self.assertFalse(any(path.name.startswith(".wav-") for path in directory.parent.iterdir()))

    def test_url_download_retains_original_beside_stems_and_exposes_source_path(self):
        result = self.process()
        self.assertEqual(result.status, "done", result.error)
        self.assertIsNotNone(result.source_path)
        original = Path(result.source_path)
        self.assertTrue(original.exists(), "retained original was not persisted beside the stems")
        self.assertEqual(sf.info(original).subtype, "FLOAT")
        manifest = outputs.read_manifest(Path(result.meta_path).parent)
        self.assertEqual(manifest["original"], original.name)
        # A cache hit (skipped) still exposes the retained original path from manifest["original"].
        skipped = self.process()
        self.assertEqual(skipped.status, "skipped")
        self.assertEqual(skipped.source_path, str(original))
        # Legacy absolute paths in manifest["original"] continue to resolve cleanly.
        legacy = run._resolve_manifest_original(Path(result.meta_path).parent, {"original": str(original)})
        self.assertEqual(legacy, str(original))

    def test_valid_existing_pair_is_reusable_without_network_or_dependencies(self):
        first = self.process()
        with patch.object(run, "ytdlp_command", side_effect=AssertionError("network not needed")):
            repeated = self.process()
        self.assertEqual(repeated.status, "skipped")
        self.assertEqual(repeated.generation, first.generation)

    def test_quality_selection_reaches_worker_and_preserves_both_cached_pairs(self):
        balanced = self.process()
        self.args.model = "htdemucs_ft"
        with patch.object(run, "separate_stems", side_effect=self.separate) as separate:
            quality = self.process()
        self.assertEqual(quality.status, "done", quality.error)
        self.assertEqual(separate.call_args.kwargs["model"], "htdemucs_ft")
        self.assertNotEqual(balanced.outputs["drums"], quality.outputs["drums"])
        self.assertEqual(outputs.read_manifest(Path(quality.meta_path).parent)["model"], "htdemucs_ft")
        self.assertEqual(self.process().generation, quality.generation)
        self.args.model = "htdemucs"
        self.assertEqual(self.process().generation, balanced.generation)

    def test_all_stems_export_preserves_existing_drums_pair_gain_and_files(self):
        """DESIGN.md:41 / Defect 3: exporting 'all' stems must not overwrite a drums/drumless pair."""
        def loud_separate(_audio, workdir, *_args, **_kwargs):
            # Sum of bass+vocals+other = 1.5 > 1.0 -> drums pair applies gain ~0.666,
            # while 'all' stems peak is 0.5 -> gain 1.0.
            result = {}
            for name, level in (("drums", 0.3), ("bass", 0.5), ("vocals", 0.5), ("other", 0.5)):
                path = Path(workdir) / (name + ".wav")
                sf.write(path, np.full((100, 2), level, dtype=np.float32), 44100, subtype="FLOAT")
                result[name] = str(path)
            return result

        with patch.object(run, "separate_stems", side_effect=loud_separate):
            pair = self.process()
            self.assertEqual(pair.status, "done", pair.error)
            pair_drums_path = Path(pair.outputs["drums"])
            pair_nodrums_path = Path(pair.outputs["no_drums"])
            pair_drums_before = sf.read(pair_drums_path)[0]
            pair_nodrums_before = sf.read(pair_nodrums_path)[0]
            ratio_before = float(pair_drums_before.max() / pair_nodrums_before.max())
            pair_manifest_before = outputs.read_manifest(Path(pair.meta_path).parent)

            self.args.stem = "all"
            all_res = self.process()
            self.assertEqual(all_res.status, "done", all_res.error)
            all_drums_path = Path(all_res.outputs["drums"])
            self.assertNotEqual(all_drums_path, pair_drums_path)
            self.assertIn("[all]", all_drums_path.name)

            # Original drums pair audio and gain ratio remain completely intact.
            pair_drums_after = sf.read(pair_drums_path)[0]
            ratio_after = float(pair_drums_after.max() / pair_nodrums_before.max())
            self.assertAlmostEqual(ratio_before, ratio_after, places=5)
            self.assertEqual(
                outputs.read_manifest(Path(pair.meta_path).parent)["outputs"]["drums"]["sha256"],
                pair_manifest_before["outputs"]["drums"]["sha256"],
            )

            self.args.stem = "drums"
            reused = self.process()
            self.assertEqual(reused.status, "skipped")
            self.assertEqual(reused.generation, pair.generation)

    def test_output_lock_waits_for_concurrent_reader_and_formats_timeout_cleanly(self):
        """Defect 6: brief lock contention succeeds; sustained contention hides raw lock paths."""
        first = self.process()
        self.assertEqual(first.status, "done", first.error)
        directory = Path(first.meta_path).parent
        lock_file = directory.parent / ("." + directory.name + ".lock")

        # Brief reader lock held on another thread releases while process_one waits -> succeeds cleanly.
        acquired = threading.Event()

        def hold_briefly():
            with FileLock(str(lock_file), timeout=1):
                acquired.set()
                time.sleep(0.15)

        holder = threading.Thread(target=hold_briefly, daemon=True)
        holder.start()
        self.assertTrue(acquired.wait(2))
        try:
            repeated = self.process()
        finally:
            holder.join(timeout=2)
        self.assertEqual(repeated.status, "skipped", repeated.error)

        # Sustained lock held on another thread past timeout fails cleanly without exposing a raw lock path.
        acquired.clear()
        release = threading.Event()

        def hold_until_released():
            with FileLock(str(lock_file), timeout=1):
                acquired.set()
                release.wait(timeout=5)

        sustained = threading.Thread(target=hold_until_released, daemon=True)
        sustained.start()
        self.assertTrue(acquired.wait(2))
        try:
            with patch.object(run, "OUTPUT_LOCK_TIMEOUT", 0.2):
                blocked = self.process()
        finally:
            release.set()
            sustained.join(timeout=2)
        self.assertEqual(blocked.status, "error")
        self.assertIn("in use", blocked.error)
        self.assertNotIn(".lock", blocked.error)

    def test_mismatched_model_manifest_is_not_reused(self):
        first = self.process()
        path = Path(first.meta_path)
        manifest = json.loads(path.read_text())
        manifest["model"] = "htdemucs_ft"
        path.write_text(json.dumps(manifest))
        repeated = self.process()
        self.assertEqual(repeated.status, "error")
        self.assertIn("identity", repeated.error)

    def test_read_only_audio_is_reusable_without_write_access(self):
        first = self.process()
        self.assertEqual(first.status, "done", first.error)
        paths = [Path(first.outputs["drums"]), Path(first.outputs["no_drums"])]
        try:
            for path in paths:
                path.chmod(0o444)
            self.assertEqual(self.process().status, "skipped")
        finally:
            for path in paths:
                path.chmod(0o644)

    def test_deleted_outputs_are_regenerated(self):
        first = self.process()
        import shutil
        shutil.rmtree(Path(first.meta_path).parent)
        repeated = self.process()
        self.assertEqual(repeated.status, "done", repeated.error)
        self.assertNotEqual(first.generation, repeated.generation)

    def test_partial_or_modified_output_requires_replace(self):
        result = self.process()
        Path(result.outputs["drums"]).write_bytes(b"modified")
        repeated = self.process()
        self.assertEqual(repeated.status, "error")
        self.assertIn("--replace-existing", repeated.error)
        self.args.replace_existing = True
        replaced = self.process()
        self.assertEqual(replaced.status, "done", replaced.error)
        outputs.read_manifest(Path(replaced.meta_path).parent)

    def test_deleted_stems_are_regenerated_without_replace_flag(self):
        result = self.process()
        directory = Path(result.meta_path).parent
        for path in (Path(result.outputs["drums"]), Path(result.outputs["no_drums"])):
            self.assertTrue(path.is_file())
            path.unlink()
        repeated = self.process()
        self.assertEqual(repeated.status, "done", repeated.error)
        self.assertNotEqual(result.generation, repeated.generation)
        outputs.read_manifest(directory)
        self.assertTrue(Path(result.outputs["drums"]).is_file())
        self.assertTrue(Path(result.outputs["no_drums"]).is_file())

    def test_failed_directory_swap_restores_old_generation(self):
        first = self.process()
        directory = Path(first.meta_path).parent
        out_root = self.root / "output"
        old = {path.name: path.read_bytes() for path in out_root.iterdir() if path.is_file()}
        self.args.replace_existing = True
        self.drum_level = .7
        real_replace = os.replace

        def fail_publish(source, target):
            if Path(target) == directory and Path(source) != outputs.previous_directory(directory):
                raise PermissionError("locked destination")
            return real_replace(source, target)

        with patch.object(outputs.os, "replace", side_effect=fail_publish):
            failed = self.process()
        self.assertEqual(failed.status, "error")
        self.assertEqual(old, {path.name: path.read_bytes() for path in out_root.iterdir() if path.is_file()})
        outputs.read_manifest(directory)

    def test_interrupted_directory_swap_is_recovered(self):
        first = self.process()
        directory = Path(first.meta_path).parent
        os.replace(directory, outputs.previous_directory(directory))
        recovered = self.process()
        self.assertEqual(recovered.status, "skipped")
        self.assertEqual(recovered.generation, first.generation)

    def test_failed_inference_keeps_existing_pair(self):
        first = self.process()
        self.args.replace_existing = True
        with patch.object(run, "separate_stems", side_effect=RuntimeError("inference failed")):
            failed = self.process()
        self.assertEqual(failed.status, "error")
        self.assertEqual(outputs.read_manifest(Path(first.meta_path).parent)["generation"], first.generation)

    def test_local_audio_file_is_processed_without_ytdlp_or_network(self):
        local_file = self.root / "Drummer - Local Groove.wav"
        sf.write(local_file, np.ones((22050, 2)) * 0.2, 44100, subtype="FLOAT")
        inp = run.normalize_inputs_full([str(local_file)])[0]
        with patch.object(run, "ytdlp_command", side_effect=AssertionError("yt-dlp not needed for local files")):
            result = run.process_one(inp.watch_id, self.args, source_url=inp.source_url)
        self.assertEqual(result.status, "done", result.error)
        self.assertEqual(result.title, "Drummer - Local Groove")
        self.assertTrue(Path(result.outputs["drums"]).name.startswith("Drummer - Local Groove ["))
        self.assertEqual(result.source_path, str(local_file.resolve()))
        manifest = outputs.read_manifest(Path(result.meta_path).parent)
        self.assertEqual(manifest["uploader"], "local file")


class QueueRunnerTests(unittest.TestCase):
    def test_completed_source_is_reprocessed_with_new_destination_and_format(self):
        with tempfile.TemporaryDirectory() as root:
            path = str(Path(root) / "queue.json")
            item = Input("dQw4w9WgXcQ", "https://youtu.be/dQw4w9WgXcQ")
            with patch.object(run, "gather_inputs", return_value=[item]), patch.object(
                run, "process_one", return_value=run.ItemResult(item.watch_id, status="done")
            ) as process:
                self.assertEqual(run.main(["--queue", path, "-o", root + "/first"]), 0)
                self.assertEqual(run.main(["--queue", path, "-o", root + "/second",
                                           "--audio-format", "wav", "--replace-existing"]), 0)
            self.assertEqual(process.call_count, 2)
            effective = process.call_args.args[1]
            self.assertEqual(effective.output_dir, str(Path(root) / "second"))
            self.assertEqual(effective.audio_format, "wav")
            self.assertTrue(effective.replace_existing)
            self.assertEqual(process.call_args.kwargs["source_url"], item.source_url)

    def test_cli_retry_is_bounded_to_one_attempt_and_skips_cancelled_jobs(self):
        """DESIGN.md:77 / Defect 7: bare jamly run retries failed jobs once and skips cancelled ones."""
        with tempfile.TemporaryDirectory() as root:
            path = str(Path(root) / "queue.json")
            queue = WatchQueue(path)
            queue.add("failed-job", source_url="https://example.test/fail")
            queue.finish("failed-job", "error", error="temporary failure")
            queue.add("cancelled-job", source_url="https://example.test/cancel")
            queue.finish("cancelled-job", "cancelled")
            queue.save()

            seen = []

            def fake_process(wid, *_args, **_kwargs):
                seen.append(wid)
                return run.ItemResult(wid, status="done")

            with patch.object(run, "gather_inputs", return_value=[]), patch.object(
                run, "process_one", side_effect=fake_process
            ):
                self.assertEqual(run.main(["--queue", path]), 0)
            self.assertEqual(seen, ["failed-job"])
            reloaded = WatchQueue.from_path(path)
            self.assertEqual(reloaded.get("failed-job")["status"], "done")
            self.assertEqual(reloaded.get("cancelled-job")["status"], "cancelled")

    def test_dry_run_does_not_create_queue_files(self):
        with tempfile.TemporaryDirectory() as root:
            path = str(Path(root) / "queue.json")
            with patch.object(run, "gather_inputs", return_value=[Input("test", "https://example.test/audio")]):
                self.assertEqual(run.main(["--queue", path, "--dry-run"]), 0)
            self.assertEqual(list(Path(root).iterdir()), [])

    def test_karaoke_vocals_and_all_stems_publish_expected_outputs(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "Demo - Track.wav"
            sf.write(source, np.zeros((2205, 2), dtype=np.float32), 44100)
            inp = make_input(str(source))
            args = run.build_parser().parse_args([
                "-o", root, "--audio-format", "wav", "--stem", "vocals",
            ])

            def fake_separate(_audio, work, *_args, **_kwargs):
                paths = {}
                for name in ("drums", "bass", "other", "vocals"):
                    p = Path(work) / (name + ".wav")
                    sf.write(p, np.full((2205, 2), 0.4, dtype=np.float32), 44100)
                    paths[name] = str(p)
                return paths

            with patch.object(run, "detect_ffmpeg", return_value=ToolStatus("ffmpeg", root, True)), \
                    patch.object(run, "detect_engine", return_value="htdemucs"), \
                    patch.object(run, "separate_stems", side_effect=fake_separate):
                res = run.process_one(inp.watch_id, args, source_url=inp.source_url)
                self.assertEqual(res.status, "done")
                self.assertEqual(set(res.outputs), {"vocals", "no_vocals", "metadata"})
                self.assertTrue(Path(res.outputs["vocals"]).name.endswith("_vocals.wav"))
                self.assertTrue(Path(res.outputs["no_vocals"]).name.endswith("_no_vocals.wav"))

                all_args = run.build_parser().parse_args([
                    "-o", root, "--audio-format", "wav", "--stem", "all",
                ])
                all_res = run.process_one(inp.watch_id, all_args, source_url=inp.source_url)
                self.assertEqual(all_res.status, "done")
                self.assertEqual(set(all_res.outputs), {"drums", "bass", "other", "vocals", "metadata"})
                self.assertIn("[all]", Path(all_res.outputs["vocals"]).name)

                reused_vocals = run.process_one(inp.watch_id, args, source_url=inp.source_url)
                self.assertEqual(reused_vocals.status, "skipped")

                bad_args = run.build_parser().parse_args([
                    "-o", root, "--audio-format", "wav", "--stem", "guitar",
                ])
                bad_res = run.process_one(inp.watch_id, bad_args, source_url=inp.source_url)
                self.assertEqual(bad_res.status, "error")
                self.assertIn("6-stem", bad_res.error)
