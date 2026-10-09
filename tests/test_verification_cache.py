"""Persistent status tests use disposable packages, never the user's archives."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import comfy_archive as engine
import verification_cache as cache

ROOT = Path(__file__).parents[1]


class VerificationCacheTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "ComfyUI"
        self.archive = self.base / "Archive" / "Example.zip"
        self.model = self.root / "models" / "vae" / "audio.safetensors"
        self.workflow = self.root / "user" / "default" / "workflows" / "Example.json"
        self.model.parent.mkdir(parents=True)
        self.workflow.parent.mkdir(parents=True)
        self.model.write_bytes(b"fixture-model")
        self.workflow.write_text(json.dumps({"nodes": [{"id": 1, "type": "VAELoader",
            "widgets_values": [self.model.name]}]}), encoding="utf-8")
        env = patch.dict(os.environ, {engine.CONFIG_ENV_VAR: str(self.base / "config.json"),
            engine.NODE_RULES_ENV_VAR: str(ROOT / "node-rules.json")})
        env.start()
        self.addCleanup(env.stop)
        quiet = patch.object(cache.LOGGER, "disabled", True)
        quiet.start()
        self.addCleanup(quiet.stop)

    def build(self, pooled=True, missing=False):
        if missing:
            self.model.unlink()
        engine.build_package(self.workflow, self.root, self.archive, missing, False, shared_pool=pooled)
        self.manifest = engine.load_package_manifest(self.archive)
        if pooled and not missing:
            entry = next(item for item in self.manifest["files"] if item.get("storage") == "shared-object")
            self.object = engine._shared_object_path(self.archive, entry["sha256"])

    @staticmethod
    def touch(path):
        info = path.stat()
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 2_000_000_000))

    def test_verified_result_survives_a_fresh_python_process(self):
        self.build()
        result = engine.verify_package(self.archive)
        self.assertTrue(result["valid"])
        self.assertTrue(result["verification"]["persisted"])
        self.assertTrue(cache.cache_path(self.archive).is_relative_to(self.archive.parent / ".comfyshelf"))
        fresh = subprocess.run([sys.executable, "-c",
            "import json,sys; from pathlib import Path; import comfy_archive as e; import verification_cache as c; "
            "p=Path(sys.argv[1]); print(json.dumps(c.status(p,e.load_package_manifest(p))))", str(self.archive)],
            cwd=ROOT, capture_output=True, text=True, check=True, timeout=20)
        status = json.loads(fresh.stdout)
        self.assertEqual(status["state"], "verified")
        self.assertEqual(status["checked_at"], result["verification"]["checked_at"])

    def test_unknown_status_is_read_only(self):
        self.build(pooled=False)
        self.assertEqual(cache.status(self.archive, self.manifest)["state"], "unknown")
        self.assertFalse((self.archive.parent / ".comfyshelf").exists())

    def test_different_path_and_handle_timestamps_allow_verify_and_unload(self):
        self.build()
        original_fstat = os.fstat

        def windows_handle_stat(fd):
            info = original_fstat(fd)
            # Windows Python 3.13 can report change time through fstat(),
            # but creation time through stat(path). Neither file has changed.
            return SimpleNamespace(**{name: getattr(info, name) for name in dir(info)
                if name.startswith("st_")} | {"st_ctime_ns": info.st_ctime_ns + 10_000_000_000})

        with patch.object(os, "fstat", side_effect=windows_handle_stat):
            result = engine.verify_package(self.archive)
            self.assertTrue(result["valid"], result["errors"])
            self.assertEqual(cache.status(self.archive, self.manifest)["state"], "verified")
            engine.unload_package(self.archive, self.root, False, False, False)
            self.assertTrue(self.model.exists(), "Preview must not delete anything")
            engine.unload_package(self.archive, self.root, True, False, False)
        self.assertFalse(self.model.exists())
        self.assertEqual(self.object.read_bytes(), b"fixture-model")
        self.assertTrue(engine.verify_package(self.archive)["valid"])

    def test_portable_zip_verifies_with_different_handle_metadata_representation(self):
        self.build(pooled=False)
        original_fstat = os.fstat
        for field in ("st_ctime_ns", "st_dev", "st_ino"):
            with self.subTest(field=field):
                def alternative_stat(fd):
                    info = original_fstat(fd)
                    return SimpleNamespace(**{name: getattr(info, name) for name in dir(info)
                        if name.startswith("st_")} | {field: getattr(info, field) + 10_000})

                with patch.object(os, "fstat", side_effect=alternative_stat):
                    result = engine.verify_package(self.archive)
                self.assertTrue(result["valid"], result["errors"])
                self.assertEqual(cache.status(self.archive, self.manifest)["state"], "verified")

    def test_different_zip_handle_at_start_or_finish_blocks_unload(self):
        self.build()
        replacement = self.archive.with_suffix(".replacement")
        replacement.write_bytes(self.archive.read_bytes())
        info = self.archive.stat()
        os.utime(replacement, ns=(info.st_atime_ns, info.st_mtime_ns))
        self.assertNotEqual(replacement.stat().st_ino, info.st_ino)
        original_open = Path.open

        for mismatch_probe in (1, 2):
            with self.subTest(mismatch_probe=mismatch_probe):
                probes = 0

                def redirected_probe(path, *args, **kwargs):
                    nonlocal probes
                    if path == self.archive:
                        probes += 1
                        if probes == mismatch_probe:
                            return original_open(replacement, *args, **kwargs)
                    return original_open(path, *args, **kwargs)

                with patch.object(Path, "open", redirected_probe), patch.object(cache.LOGGER, "disabled", False):
                    with self.assertLogs(cache.LOGGER, level="WARNING") as captured:
                        with self.assertRaisesRegex(ValueError, "Entladen abgebrochen"):
                            engine.unload_package(self.archive, self.root, True, False, False)
                self.assertIn("Open ZIP does not match", "\n".join(captured.output))
                self.assertIn("st_ino", "\n".join(captured.output))
                self.assertEqual(self.model.read_bytes(), b"fixture-model")
                self.assertEqual(cache.status(self.archive, self.manifest)["state"], "stale")

    def test_unavailable_open_handle_metadata_blocks_unload_and_is_logged(self):
        self.build()
        with patch.object(os, "fstat", side_effect=OSError("Test metadata failure")), \
                patch.object(cache.LOGGER, "disabled", False):
            with self.assertLogs(cache.LOGGER, level="WARNING") as captured:
                with self.assertRaisesRegex(ValueError, "Entladen abgebrochen"):
                    engine.unload_package(self.archive, self.root, True, False, False)
        self.assertIn("Test metadata failure", "\n".join(captured.output))
        self.assertEqual(self.model.read_bytes(), b"fixture-model")
        self.assertEqual(cache.status(self.archive, self.manifest)["state"], "stale")
        self.assertTrue(engine.verify_package(self.archive)["valid"])

    def test_refresh_does_not_open_or_hash_model_payloads(self):
        self.build()
        engine.verify_package(self.archive)
        original_open = Path.open

        def guarded_open(path, *args, **kwargs):
            if path == self.object or path == self.model or path == self.archive:
                raise AssertionError("Status refresh must only stat payloads")
            return original_open(path, *args, **kwargs)

        with patch.object(Path, "open", guarded_open), patch.object(engine, "sha256_file", side_effect=AssertionError("No hashing")):
            self.assertEqual(cache.status(self.archive, self.manifest)["state"], "verified")

    def test_changed_zip_marks_record_stale(self):
        self.build(pooled=False)
        original = engine.verify_package(self.archive)["verification"]
        self.touch(self.archive)
        result = cache.status(self.archive, self.manifest)
        self.assertEqual(result["state"], "stale")
        self.assertEqual(result["checked_at"], original["checked_at"])

    def test_replaced_zip_with_same_size_and_mtime_needs_recheck(self):
        self.build(pooled=False)
        engine.verify_package(self.archive)
        info = self.archive.stat()
        replacement = self.archive.with_suffix(".replacement")
        replacement.write_bytes(self.archive.read_bytes())
        os.utime(replacement, ns=(info.st_atime_ns, info.st_mtime_ns))
        os.replace(replacement, self.archive)
        self.assertEqual(cache.status(self.archive, self.manifest)["state"], "stale")

    def test_pool_object_changed_missing_or_replaced_invalidates_check(self):
        self.build()
        for change in ("changed", "missing", "replaced"):
            with self.subTest(change=change):
                self.object.write_bytes(self.model.read_bytes())
                engine.verify_package(self.archive)
                if change == "changed":
                    self.touch(self.object)
                elif change == "missing":
                    self.object.unlink()
                else:
                    info = self.object.stat()
                    replacement = self.object.with_suffix(".replacement")
                    replacement.write_bytes(self.object.read_bytes())
                    os.utime(replacement, ns=(info.st_atime_ns, info.st_mtime_ns))
                    os.replace(replacement, self.object)
                self.assertEqual(cache.status(self.archive, self.manifest)["state"], "stale")

    def test_unrelated_files_and_local_model_removal_do_not_invalidate_archive(self):
        self.build()
        engine.verify_package(self.archive)
        (self.object.parent / "unrelated").write_bytes(b"unrelated")
        self.model.unlink()
        self.assertEqual(cache.status(self.archive, self.manifest)["state"], "verified")

    def test_corrupt_record_is_unknown_not_a_startup_failure(self):
        self.build()
        engine.verify_package(self.archive)
        for payload in ("not json", "[]", '{"schema":999}', '{"schema":1,"state":"verified","snapshot":{},"checked_at":"bad"}'):
            cache.cache_path(self.archive).write_text(payload, encoding="utf-8")
            self.assertEqual(cache.status(self.archive, self.manifest)["state"], "unknown")

    def test_failed_check_is_remembered_and_success_can_replace_it(self):
        self.build()
        self.object.write_bytes(b"broken")
        result = engine.verify_package(self.archive)
        self.assertFalse(result["valid"])
        self.assertEqual(cache.status(self.archive, self.manifest)["state"], "invalid")
        self.object.write_bytes(self.model.read_bytes())
        self.assertEqual(cache.status(self.archive, self.manifest)["state"], "stale")
        self.assertTrue(engine.verify_package(self.archive)["valid"])
        self.assertEqual(cache.status(self.archive, self.manifest)["state"], "verified")

    def test_missing_dependency_does_not_conflate_integrity_and_completeness(self):
        self.build(missing=True)
        result = engine.verify_package(self.archive)
        self.assertTrue(result["valid"])
        self.assertFalse(result["complete"])
        self.assertEqual(cache.status(self.archive, self.manifest)["state"], "verified")

    def test_cancelled_check_never_creates_or_replaces_a_result(self):
        self.build()

        def cancel(event):
            if event["phase"] == "verify":
                raise engine.OperationCancelled("Cancelled by test")

        with self.assertRaises(engine.OperationCancelled):
            engine.verify_package(self.archive, cancel)
        self.assertIsNone(cache.read_record(self.archive))
        previous = engine.verify_package(self.archive)["verification"]
        with self.assertRaises(engine.OperationCancelled):
            engine.verify_package(self.archive, cancel)
        self.assertEqual(cache.status(self.archive, self.manifest)["checked_at"], previous["checked_at"])

    def test_write_failure_is_reported_without_failing_the_integrity_check(self):
        self.build()
        with patch.object(cache, "_write_record", side_effect=PermissionError("Read-only archive folder")):
            result = engine.verify_package(self.archive)
        self.assertTrue(result["valid"])
        self.assertFalse(result["verification"]["persisted"])
        self.assertEqual(cache.status(self.archive, self.manifest)["state"], "unknown")

    def test_atomic_write_failure_preserves_previous_record_and_cleans_temporary(self):
        self.build()
        engine.verify_package(self.archive)
        destination = cache.cache_path(self.archive)
        previous = destination.read_bytes()
        with patch.object(cache.os, "replace", side_effect=OSError("Simulated write failure")):
            result = engine.verify_package(self.archive)
        self.assertFalse(result["verification"]["persisted"])
        self.assertEqual(destination.read_bytes(), previous)
        self.assertEqual(list(destination.parent.glob("*.tmp")), [])

    def test_change_during_verification_never_produces_green_status(self):
        self.build()
        changed = False

        def mutate(event):
            nonlocal changed
            if not changed and event["phase"] == "verify" and event["current"]:
                self.touch(self.object)
                changed = True

        with patch.object(cache.LOGGER, "disabled", False):
            with self.assertLogs(cache.LOGGER, level="WARNING") as captured:
                result = engine.verify_package(self.archive, mutate)
        self.assertIn("pool objects:", "\n".join(captured.output))
        self.assertFalse(result["valid"])
        self.assertEqual(result["verification"]["state"], "stale")
        self.assertEqual(cache.status(self.archive, self.manifest)["state"], "stale")

    def test_rename_retains_timestamp_only_for_same_file(self):
        self.build()
        checked = engine.verify_package(self.archive)["verification"]
        previous = cache.prepare_rename(self.archive, self.manifest)
        result = engine.rename_package_archive(self.archive, self.archive.parent, "Renamed")
        renamed = Path(result["path"])
        cache.finish_rename(renamed, self.manifest, previous)
        self.assertEqual(cache.status(renamed, self.manifest)["state"], "verified")
        self.assertEqual(cache.status(renamed, self.manifest)["checked_at"], checked["checked_at"])
        different = renamed.with_name("Different.zip")
        different.write_bytes(renamed.read_bytes())
        cache.finish_rename(different, self.manifest, previous)
        self.assertEqual(cache.status(different, self.manifest)["state"], "unknown")

    def test_changed_archive_cannot_carry_forward_its_old_check_on_rename(self):
        self.build()
        engine.verify_package(self.archive)
        self.touch(self.archive)
        self.assertIsNone(cache.prepare_rename(self.archive, self.manifest))

    def test_safety_verification_never_trusts_a_green_cache(self):
        self.build()
        engine.verify_package(self.archive)
        self.object.write_bytes(b"corrupt")
        record = cache.read_record(self.archive)
        record["snapshot"] = cache.snapshot(self.archive, self.manifest)
        cache._write_record(self.archive, record)  # Simulate stale/forged metadata.
        self.assertEqual(cache.status(self.archive, self.manifest)["state"], "verified")
        with self.assertRaises(ValueError):
            engine.unload_package(self.archive, self.root, True, False, False)
        self.assertEqual(self.model.read_bytes(), b"fixture-model")


if __name__ == "__main__":
    unittest.main()
