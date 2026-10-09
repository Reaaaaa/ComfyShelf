import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import comfy_archive as engine


class PortableExportTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        isolated = patch.dict(os.environ, {
            engine.CONFIG_ENV_VAR: str(self.base / "config.json"),
            engine.NODE_RULES_ENV_VAR: str(Path(__file__).parents[1] / "node-rules.json"),
        })
        isolated.start()
        self.addCleanup(isolated.stop)
        self.root = self.base / "ComfyUI"
        self.model = self.root / "models" / "vae" / "nested" / "example.safetensors"
        self.model.parent.mkdir(parents=True)
        self.model.write_bytes(b"tiny-model-fixture")
        self.workflow = self.root / "user" / "default" / "workflows" / "Example.json"
        self.workflow.parent.mkdir(parents=True)
        self.workflow.write_text(json.dumps({"nodes": [{"id": 1, "type": "VAELoader",
            "widgets_values": ["nested/example.safetensors"]}]}), encoding="utf-8")
        self.source = self.base / "Archive" / "Example.zip"
        self.output = self.base / "Chosen destination" / "Travel.zip"

    def build(self, pooled=False, incomplete=False):
        if incomplete:
            self.model.unlink()
        engine.build_package(self.workflow, self.root, self.source, incomplete, False, shared_pool=pooled)

    def test_preview_is_read_only_and_normalizes_extension(self):
        self.build(pooled=True)
        with patch.object(engine, "sha256_file", side_effect=AssertionError("Preview must not hash")):
            report = engine.preview_portable_export(self.source, self.output.with_suffix(""))
        self.assertEqual(report["output_path"], str(self.output))
        self.assertEqual(report["model_bytes"], self.model.stat().st_size)
        self.assertGreater(report["bytes_required"], report["model_bytes"])
        self.assertTrue(report["complete"])
        self.assertEqual(report["blockers"], [])
        self.assertFalse(self.output.parent.exists())

    def test_both_package_types_export_and_restore_without_original_library(self):
        for pooled in (False, True):
            with self.subTest(pooled=pooled):
                if not self.source.exists():
                    self.build()
                if pooled:
                    engine.deduplicate_package(self.source)
                output = self.output.with_name(f"Travel-{pooled}.zip")
                before = self.source.read_bytes()
                result = engine.export_portable_package(self.source, output)
                self.assertEqual(self.source.read_bytes(), before)
                self.assertTrue(result["verification"]["valid"])
                self.assertEqual(result["verification"]["archive"], str(output))
                with zipfile.ZipFile(output) as archive:
                    member = "models/vae/nested/example.safetensors"
                    self.assertEqual(archive.read(member), self.model.read_bytes())
                    if pooled:
                        self.assertEqual(archive.getinfo(member).compress_type, zipfile.ZIP_STORED)
                    manifest = json.loads(archive.read("comfy-package.json"))
                    self.assertFalse(any(item.get("storage") for item in manifest["files"]))
                # Resolving a pool object must never be needed for this exported ZIP.
                with patch.object(engine, "_shared_object_path", side_effect=AssertionError("Not portable")):
                    self.assertTrue(engine.verify_package(output)["valid"])
                    destination = self.base / f"AnotherComfy-{pooled}"
                    destination.mkdir()
                    engine.restore_package(output, destination, True, False)
                    self.assertEqual((destination / member).read_bytes(), self.model.read_bytes())

    def test_incomplete_export_requires_explicit_acceptance_and_keeps_missing_names(self):
        self.build(incomplete=True)
        report = engine.preview_portable_export(self.source, self.output)
        self.assertFalse(report["complete"])
        self.assertEqual(report["missing"][0]["name"], "nested/example.safetensors")
        with self.assertRaisesRegex(ValueError, "explicitly accept"):
            engine.export_portable_package(self.source, self.output)
        self.assertFalse(self.output.exists())
        result = engine.export_portable_package(self.source, self.output, allow_incomplete=True)
        self.assertTrue(result["verification"]["valid"])
        self.assertFalse(result["verification"]["complete"])
        self.assertEqual(engine.load_package_manifest(self.output)["missing"], report["missing"])

    def test_existing_destination_and_source_are_preserved(self):
        self.build()
        self.output.parent.mkdir()
        self.output.write_bytes(b"keep-existing-file")
        with self.assertRaises(FileExistsError):
            engine.export_portable_package(self.source, self.output)
        self.assertEqual(self.output.read_bytes(), b"keep-existing-file")
        before = self.source.read_bytes()
        with self.assertRaisesRegex(ValueError, "source_target"):
            engine.export_portable_package(self.source, self.source, force=True)
        self.assertEqual(self.source.read_bytes(), before)

    def test_model_pool_cannot_be_an_export_destination(self):
        self.build(pooled=True)
        output = self.source.parent / ".comfyshelf" / "objects" / "Travel.zip"
        with self.assertRaisesRegex(ValueError, "pool_target"):
            engine.export_portable_package(self.source, output)
        self.assertFalse(output.exists())

    def test_missing_pool_payload_blocks_even_with_allow_incomplete(self):
        self.build(pooled=True)
        entry = next(item for item in engine.load_package_manifest(self.source)["files"] if item.get("storage"))
        engine._shared_object_path(self.source, entry["object_sha256"]).unlink()
        with self.assertRaisesRegex(ValueError, "missing_file"):
            engine.export_portable_package(self.source, self.output, allow_incomplete=True)
        self.assertFalse(self.output.exists())

    def test_insufficient_space_blocks_before_writing(self):
        self.build()
        usage = type("Usage", (), {"free": 1})()
        with patch.object(engine.shutil, "disk_usage", return_value=usage):
            with self.assertRaisesRegex(ValueError, "space"):
                engine.export_portable_package(self.source, self.output)
        self.assertFalse(self.output.parent.exists())

    def test_cancellation_during_copy_removes_only_temporary_file(self):
        for pooled in (False, True):
            with self.subTest(pooled=pooled):
                if not self.source.exists():
                    self.build()
                if pooled:
                    engine.deduplicate_package(self.source)
                before = self.source.read_bytes()

                def cancel(event):
                    if event["phase"] == "pack":
                        raise engine.OperationCancelled("Cancelled by test")

                with self.assertRaises(engine.OperationCancelled):
                    engine.export_portable_package(self.source, self.output, progress=cancel)
                self.assertEqual(self.source.read_bytes(), before)
                self.assertFalse(self.output.exists())
                self.assertEqual(list(self.output.parent.iterdir()), [])

    def test_verification_failure_does_not_replace_existing_destination(self):
        self.build()
        self.output.parent.mkdir()
        self.output.write_bytes(b"keep-original")
        with patch.object(engine, "verify_package", side_effect=[{"valid": True}, {"valid": False}]):
            with self.assertRaisesRegex(ValueError, "integrity verification"):
                engine.export_portable_package(self.source, self.output, force=True)
        self.assertEqual(self.output.read_bytes(), b"keep-original")
        self.assertEqual(list(self.output.parent.iterdir()), [self.output])

    def test_cancellation_immediately_before_publish_leaves_no_zip(self):
        self.build()

        def cancel(event):
            if event["message"] == "Publishing verified portable ZIP":
                raise engine.OperationCancelled("Cancelled by test")

        with self.assertRaises(engine.OperationCancelled):
            engine.export_portable_package(self.source, self.output, progress=cancel)
        self.assertEqual(list(self.output.parent.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
