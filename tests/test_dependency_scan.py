import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import comfy_archive as engine
import dependency_scan as scan


class DependencyScanTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.comfy = self.base / "ComfyUI"
        self.library = self.base / "Library"
        self.archive = self.library / "Incomplete.zip"
        self.library.mkdir()
        (self.comfy / "models" / "vae").mkdir(parents=True)
        env = patch.dict(os.environ, {engine.CONFIG_ENV_VAR: str(self.base / "config.json"),
            engine.NODE_RULES_ENV_VAR: str(Path(__file__).parents[1] / "node-rules.json")})
        env.start()
        self.addCleanup(env.stop)

    def package(self, names, *, root=None, output=None):
        root = root or self.comfy
        workflow = root / "user" / "default" / "workflows" / "Example.json"
        workflow.parent.mkdir(parents=True, exist_ok=True)
        workflow.write_text(json.dumps({"nodes": [{"id": i, "type": "VAELoader", "widgets_values": [name]}
                                                   for i, name in enumerate(names)]}), encoding="utf-8")
        engine.build_package(workflow, root, output or self.archive, True, False, shared_pool=True)

    def local(self, name="shared.safetensors", content=b"fixture-model", root=None):
        path = (root or self.comfy) / "models" / "vae" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def peer(self, name="shared.safetensors", content=b"fixture-model", title="Peer"):
        root = self.base / title
        self.local(name, content, root)
        self.package([name], root=root, output=self.library / f"{title}.zip")

    def inspect(self):
        return scan.scan_dependencies(self.archive, self.comfy, self.library)

    def complete(self, report=None, progress=None):
        return scan.complete_dependencies(self.archive, self.comfy, self.library,
                                          (report or self.inspect())["token"], progress)

    def test_scan_is_read_only_and_does_not_hash_models(self):
        self.package(["shared.safetensors"])
        self.local()
        before = {str(p): p.read_bytes() for p in self.library.rglob("*") if p.is_file()}
        with patch.object(engine, "sha256_file", side_effect=AssertionError("Scan must not hash models")):
            report = self.inspect()
        self.assertEqual(report["counts"], {"included": 0, "found": 1, "missing": 0, "ambiguous": 0})
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.library.rglob("*") if p.is_file()})

    def test_qwen_style_shared_and_new_files_complete_without_duplicate_objects(self):
        self.package(["shared.safetensors", "new.safetensors"])
        self.peer()
        self.local()
        self.local("new.safetensors", b"new fixture")
        original = self.archive.read_bytes()
        result = self.complete()
        self.assertEqual(result["added"], 2)
        self.assertTrue(result["complete"])
        self.assertEqual(Path(result["backup"]).read_bytes(), original)
        self.assertEqual(result["reused_object_bytes"], len(b"fixture-model"))
        objects = [p for p in (self.library / ".comfyshelf" / "objects" / "sha256").rglob("*") if p.is_file()]
        self.assertEqual(len(objects), 2)
        self.assertTrue(engine.verify_package(self.archive)["valid"])
        self.assertEqual(self.inspect()["counts"]["included"], 2)
        self.assertEqual((self.comfy / "models/vae/shared.safetensors").read_bytes(), b"fixture-model")

    def test_pool_only_match_can_be_linked_without_local_models(self):
        self.package(["shared.safetensors"])
        self.peer()
        report = self.inspect()
        self.assertEqual(report["rows"][0]["source"], "pool")
        result = self.complete(report)
        self.assertTrue(result["complete"])
        self.assertFalse((self.comfy / "models/vae/shared.safetensors").exists())

    def test_multiple_packages_with_same_digest_are_one_match(self):
        self.package(["shared.safetensors"])
        self.peer(title="First")
        self.peer(title="Second")
        self.assertEqual(self.inspect()["counts"]["found"], 1)

    def test_multiple_digests_for_same_name_are_ambiguous(self):
        self.package(["shared.safetensors"])
        self.peer(title="First")
        self.peer(content=b"another version", title="Second")
        self.assertEqual(self.inspect()["counts"]["ambiguous"], 1)
        original = self.archive.read_bytes()
        with self.assertRaises(ValueError):
            self.complete()
        self.assertEqual(self.archive.read_bytes(), original)

    def test_local_and_pool_hash_conflict_preserves_package(self):
        self.package(["shared.safetensors"])
        self.peer()
        self.local(content=b"a different local model")
        original = self.archive.read_bytes()
        with self.assertRaises(scan.DependencyError) as raised:
            self.complete()
        self.assertEqual(raised.exception.code, "dependency_conflict")
        self.assertEqual(self.archive.read_bytes(), original)

    def test_explicit_subfolders_are_preserved(self):
        self.package(["nested/shared.safetensors"])
        self.local("nested/shared.safetensors")
        self.complete()
        manifest = engine.load_package_manifest(self.archive)
        self.assertIn("models/vae/nested/shared.safetensors", [e["path"] for e in manifest["files"]])

    def test_explicit_subfolder_is_not_satisfied_by_unrelated_basename(self):
        self.package(["nested/shared.safetensors"])
        self.peer()
        self.assertEqual(self.inspect()["counts"]["missing"], 1)

    def test_stale_scan_cannot_apply(self):
        self.package(["shared.safetensors"])
        local = self.local()
        report = self.inspect()
        local.write_bytes(b"changed")
        original = self.archive.read_bytes()
        with self.assertRaises(scan.DependencyError) as raised:
            self.complete(report)
        self.assertEqual(raised.exception.code, "dependency_stale")
        self.assertEqual(self.archive.read_bytes(), original)

    def test_partial_completion_keeps_missing_dependencies(self):
        self.package(["shared.safetensors", "still-missing.safetensors"])
        self.local()
        result = self.complete()
        self.assertFalse(result["complete"])
        self.assertEqual([m["name"] for m in result["missing"]], ["still-missing.safetensors"])

    def test_corrupt_pool_object_is_not_linked(self):
        self.package(["shared.safetensors"])
        self.peer()
        obj = Path(self.inspect()["rows"][0]["pool"]["path"])
        obj.write_bytes(b"X" * obj.stat().st_size)
        original = self.archive.read_bytes()
        with self.assertRaises(scan.DependencyError) as raised:
            self.complete()
        self.assertEqual(raised.exception.code, "dependency_integrity")
        self.assertEqual(self.archive.read_bytes(), original)

    def test_cancel_before_publish_preserves_original_and_backup(self):
        self.package(["shared.safetensors"])
        self.local()
        original = self.archive.read_bytes()
        def cancel(event):
            if event["phase"] == "dependency-save":
                raise engine.OperationCancelled("Test cancellation")
        with self.assertRaises(engine.OperationCancelled):
            self.complete(progress=cancel)
        self.assertEqual(self.archive.read_bytes(), original)
        backups = list(self.library.glob("*.zip.bak"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)
        self.assertEqual(list(self.library.glob("*.tmp")), [])

    def test_failed_atomic_replace_preserves_original(self):
        self.package(["shared.safetensors"])
        self.local()
        original = self.archive.read_bytes()
        replace = os.replace
        def fail_publish(source, target):
            if Path(target) == self.archive:
                raise OSError("Simulated publish failure")
            return replace(source, target)
        with patch.object(os, "replace", side_effect=fail_publish):
            with self.assertRaises(OSError):
                self.complete()
        self.assertEqual(self.archive.read_bytes(), original)
        self.assertEqual(list(self.library.glob("*.tmp")), [])

    def test_scan_rejects_package_outside_library(self):
        self.package(["shared.safetensors"])
        with self.assertRaises(ValueError):
            scan.scan_dependencies(self.archive, self.comfy, self.base)

    def test_empty_scan_cannot_publish(self):
        self.package(["missing.safetensors"])
        with self.assertRaises(ValueError):
            self.complete()
        self.assertEqual(list(self.library.glob("*.zip.bak")), [])

    def test_existing_pool_object_without_peer_is_reused_by_hash(self):
        self.package(["shared.safetensors"])
        local = self.local()
        engine._store_file_as_object(self.archive, local)
        with patch.object(engine, "_store_file_as_object", side_effect=AssertionError("Must not copy an existing object")):
            result = self.complete()
        self.assertEqual(result["new_object_bytes"], 0)

    def test_workflow_hash_mismatch_is_rejected_before_parsing(self):
        self.package(["shared.safetensors"])
        # Rewrite a disposable fixture, keeping its old workflow checksum.
        with zipfile.ZipFile(self.archive) as z:
            entries = {name: z.read(name) for name in z.namelist()}
        workflow = next(n for n in entries if n.startswith("user/"))
        entries[workflow] = b'{"nodes": []}'
        with zipfile.ZipFile(self.archive, "w") as z:
            for name, data in entries.items():
                z.writestr(name, data)
        with self.assertRaisesRegex(ValueError, "workflow JSON failed"):
            self.inspect()

    def test_api_format_workflow_is_supported(self):
        workflow = self.comfy / "user/default/workflows/API.json"
        workflow.parent.mkdir(parents=True)
        workflow.write_text(json.dumps({"1": {"class_type": "VAELoader", "inputs": {"vae_name": "shared.safetensors"}}}), encoding="utf-8")
        engine.build_package(workflow, self.comfy, self.archive, True, False, shared_pool=True)
        self.local()
        self.assertTrue(self.complete()["complete"])

    def test_not_enough_space_stops_before_writing(self):
        self.package(["shared.safetensors"])
        self.local()
        report = self.inspect()
        original = self.archive.read_bytes()
        from types import SimpleNamespace
        with patch.object(scan.shutil, "disk_usage", return_value=SimpleNamespace(free=0)):
            with self.assertRaises(scan.DependencyError) as raised:
                self.complete(report)
        self.assertEqual(raised.exception.code, "dependency_space")
        self.assertEqual(self.archive.read_bytes(), original)
        self.assertFalse((self.library / ".comfyshelf").exists())

    def test_duplicate_workflow_references_only_add_one_file(self):
        self.package(["shared.safetensors", "shared.safetensors"])
        self.local()
        self.assertEqual(self.complete()["added"], 1)
        manifest = engine.load_package_manifest(self.archive)
        self.assertEqual(len([e for e in manifest["files"] if e.get("role") == "model"]), 1)

    def test_incomplete_token_is_not_authority_to_choose_new_candidates(self):
        self.package(["shared.safetensors"])
        self.peer()
        report = self.inspect()
        self.peer(content=b"other model", title="Later")
        with self.assertRaises(scan.DependencyError) as raised:
            self.complete(report)
        self.assertEqual(raised.exception.code, "dependency_stale")

    def test_changed_zip_after_scan_is_rejected(self):
        self.package(["shared.safetensors"])
        self.local()
        report = self.inspect()
        stat = self.archive.stat()
        os.utime(self.archive, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))
        with self.assertRaises(scan.DependencyError):
            self.complete(report)

    def test_unsafe_dependency_is_never_completed(self):
        self.package(["../outside.safetensors"])
        self.assertEqual(self.inspect()["rows"][0]["state"], "missing")
        self.assertEqual(self.inspect()["rows"][0]["reason"], "unsafe-path")

    def test_cancel_while_hashing_leaves_archive_and_no_backup(self):
        self.package(["shared.safetensors"])
        self.local()
        original = self.archive.read_bytes()
        def cancel(event):
            if event["phase"] == "dependency-hash":
                raise engine.OperationCancelled("Test cancel")
        with self.assertRaises(engine.OperationCancelled):
            self.complete(progress=cancel)
        self.assertEqual(self.archive.read_bytes(), original)
        self.assertEqual(list(self.library.glob("*.zip.bak")), [])


if __name__ == "__main__":
    unittest.main()
