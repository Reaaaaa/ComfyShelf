import importlib.util
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).parents[1] / "comfy_archive.py"
SPEC = importlib.util.spec_from_file_location("comfy_archive", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class ParserTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        isolated = patch.dict(os.environ, {
            MODULE.CONFIG_ENV_VAR: str(Path(temp.name) / "config.json"),
            MODULE.NODE_RULES_ENV_VAR: str(SCRIPT.parent / "node-rules.json"),
        })
        isolated.start()
        self.addCleanup(isolated.stop)

    def test_package_archive_rename_is_safe_and_does_not_rewrite_zip(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            archive_root = base / "Archive"
            archive_root.mkdir()
            source = archive_root / "Alter Name.zip"
            original_bytes = b"PK\x03\x04unchanged-package-content"
            source.write_bytes(original_bytes)

            result = MODULE.rename_package_archive(source, archive_root, "Neuer Name.zip")

            target = archive_root / "Neuer Name.zip"
            self.assertTrue(result["changed"])
            self.assertEqual(result["name"], "Neuer Name")
            self.assertFalse(source.exists())
            self.assertEqual(target.read_bytes(), original_bytes)

    def test_package_archive_rename_rejects_unsafe_names_locations_and_collisions(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            archive_root = base / "Archive"
            archive_root.mkdir()
            source = archive_root / "Workflow.zip"
            source.write_bytes(b"zip")

            for invalid_name in ("", "bad:name", "CON", "name.", "../escape"):
                with self.subTest(name=invalid_name):
                    with self.assertRaises(ValueError):
                        MODULE.rename_package_archive(source, archive_root, invalid_name)

            (archive_root / "Belegt.zip").write_bytes(b"other")
            with self.assertRaises(FileExistsError):
                MODULE.rename_package_archive(source, archive_root, "belegt")

            outside = base / "Outside.zip"
            outside.write_bytes(b"outside")
            with self.assertRaises(ValueError):
                MODULE.rename_package_archive(outside, archive_root, "Nope")

    def test_ui_workflow_extracts_models_and_only_used_custom_packages(self):
        workflow = {
            "nodes": [
                {
                    "id": 1,
                    "type": "UNETLoader",
                    "widgets_values": ["music/model.safetensors", "default"],
                    "properties": {"cnr_id": "comfy-core", "ver": "1"},
                },
                {
                    "id": 2,
                    "type": "FancyLoraNode",
                    "widgets_values": ["style.safetensors", 1.0],
                    "properties": {"cnr_id": "Fancy-Pack", "ver": "abc"},
                },
            ],
            "extra": {"node_versions": {"Unused-Pack": "def"}},
        }
        assets, custom_nodes = MODULE.parse_ui_workflow(workflow)
        self.assertEqual([a.filename for a in assets], ["music/model.safetensors", "style.safetensors"])
        self.assertEqual(assets[0].kind, "diffusion_model")
        self.assertEqual(assets[1].detection, "extension-heuristic")
        self.assertEqual(custom_nodes, [MODULE.CustomNode("Fancy-Pack", "abc")])

    def test_api_prompt_uses_named_inputs(self):
        prompt = {
            "4": {
                "class_type": "VAELoader",
                "inputs": {"vae_name": "audio_vae.safetensors"},
            }
        }
        assets, _ = MODULE.parse_api_prompt(prompt)
        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0].kind, "vae")
        self.assertEqual(assets[0].source_field, "vae_name")

    def test_only_referenced_file_is_resolved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            models = root / "models" / "vae"
            models.mkdir(parents=True)
            wanted = models / "wanted.safetensors"
            wanted.write_bytes(b"wanted")
            (models / "unused.safetensors").write_bytes(b"unused")
            asset = MODULE.Asset(
                kind="vae",
                filename="wanted.safetensors",
                source_node_id="1",
                source_node_type="VAELoader",
                source_field="widgets_values[0]",
                detection="known-node-rule",
                search_categories=("vae",),
            )
            resolved, alternatives = MODULE.resolve_asset(asset, root)
            self.assertEqual(resolved, wanted)
            self.assertEqual(alternatives, [])

    def test_pack_creates_overlay_with_custom_node_inventory_and_verify_passes(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "ComfyUI"
            models = root / "models" / "vae"
            workflows = root / "user" / "default" / "workflows"
            custom = root / "custom_nodes" / "Example-Pack"
            models.mkdir(parents=True)
            workflows.mkdir(parents=True)
            custom.mkdir(parents=True)
            (models / "audio.safetensors").write_bytes(b"model-content")
            (custom / "__init__.py").write_text("NODE_CLASS_MAPPINGS = {}\n", encoding="utf-8")
            git_dir = custom / ".git"
            git_dir.mkdir()
            (git_dir / "config").write_text("excluded", encoding="utf-8")
            workflow = workflows / "music.json"
            workflow.write_text(
                json.dumps(
                    {
                        "nodes": [
                            {
                                "id": 1,
                                "type": "VAELoader",
                                "widgets_values": ["audio.safetensors"],
                                "properties": {"cnr_id": "Example-Pack", "ver": "abc"},
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            output = base / "music.zip"
            pack_events = []
            result = MODULE.build_package(
                workflow, root, output, False, False, progress=pack_events.append
            )
            self.assertTrue(result["complete"])
            self.assertTrue(any(event["phase"] == "pack" for event in pack_events))
            self.assertTrue(
                any(
                    event["phase"] == "pack"
                    and event.get("total")
                    and event.get("current") == event["total"]
                    for event in pack_events
                )
            )
            with zipfile.ZipFile(output) as archive:
                names = archive.namelist()
                self.assertIn("models/vae/audio.safetensors", names)
                self.assertNotIn("custom_nodes/Example-Pack/__init__.py", names)
                self.assertNotIn("custom_nodes/Example-Pack/.git/config", names)
                self.assertIn("custom-nodes.txt", names)
                self.assertIn("user/default/workflows/music.json", names)
                self.assertIn("comfy-package.json", names)
                custom_text = archive.read("custom-nodes.txt").decode("utf-8")
                self.assertIn("Example-Pack", custom_text)
                self.assertIn("Revision: abc", custom_text)
                manifest = json.loads(archive.read("comfy-package.json"))
                self.assertEqual(manifest["custom_nodes_policy"], "record-only")
                self.assertFalse(manifest["custom_nodes"][0]["packaged"])
                self.assertEqual(manifest["custom_nodes"][0]["node_types"], ["VAELoader"])
            verify_events = []
            verified = MODULE.verify_package(output, progress=verify_events.append)
            self.assertTrue(verified["valid"])
            self.assertTrue(verified["complete"])
            self.assertTrue(any(event["phase"] == "verify" for event in verify_events))
            self.assertEqual(verify_events[-1]["phase"], "complete")

    def test_allow_missing_is_explicit_and_strict_mode_aborts(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "ComfyUI"
            (root / "models" / "vae").mkdir(parents=True)
            workflow = root / "missing.json"
            workflow.write_text(
                json.dumps(
                    {
                        "nodes": [
                            {
                                "id": 1,
                                "type": "VAELoader",
                                "widgets_values": ["missing.safetensors"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                MODULE.build_package(workflow, root, base / "strict.zip", False, False)
            self.assertFalse((base / "strict.zip").exists())

            output = base / "partial.zip"
            result = MODULE.build_package(workflow, root, output, True, False)
            self.assertFalse(result["complete"])
            self.assertEqual(result["missing"][0]["name"], "missing.safetensors")
            verified = MODULE.verify_package(output)
            self.assertTrue(verified["valid"])
            self.assertFalse(verified["complete"])

    def test_shared_object_pool_deduplicates_and_can_export_portable_zip(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "ComfyUI"
            archive_root = base / "Archive"
            model = root / "models" / "vae" / "shared.safetensors"
            workflows = root / "user" / "default" / "workflows"
            model.parent.mkdir(parents=True)
            workflows.mkdir(parents=True)
            archive_root.mkdir()
            model.write_bytes(b"same-large-model-payload")

            archives = []
            for name in ("Workflow A", "Workflow B"):
                workflow = workflows / f"{name}.json"
                workflow.write_text(json.dumps({"nodes": [{"id": 1, "type": "VAELoader", "widgets_values": ["shared.safetensors"]}]}), encoding="utf-8")
                package = archive_root / f"{name}.zip"
                MODULE.build_package(workflow, root, package, False, False)
                archives.append(package)

            first = MODULE.deduplicate_package(archives[0])
            second = MODULE.deduplicate_package(archives[1])
            self.assertTrue(first["changed"])
            self.assertEqual(first["new_object_bytes"], len(b"same-large-model-payload"))
            self.assertEqual(second["reused_object_bytes"], len(b"same-large-model-payload"))
            self.assertEqual(len(list((archive_root / ".comfyshelf" / "objects" / "sha256").glob("*/*"))), 1)

            with zipfile.ZipFile(archives[0]) as thin_archive:
                self.assertNotIn("models/vae/shared.safetensors", thin_archive.namelist())
                thin_manifest = json.loads(thin_archive.read("comfy-package.json"))
                self.assertEqual(thin_manifest["storage_mode"], "shared-object-pool")
                self.assertEqual(thin_manifest["files"][1]["storage"], MODULE.SHARED_OBJECT_STORAGE)
            self.assertTrue(MODULE.verify_package(archives[0])["valid"])

            model.unlink()
            restored = MODULE.restore_package(archives[0], root, True, False)
            self.assertEqual(model.read_bytes(), b"same-large-model-payload")
            self.assertIn(str(model), restored["restored"])

            portable = archive_root / "Portable Exports" / "Workflow A.zip"
            export = MODULE.export_portable_package(archives[0], portable)
            self.assertTrue(export["verification"]["valid"])
            with zipfile.ZipFile(portable) as portable_archive:
                self.assertIn("models/vae/shared.safetensors", portable_archive.namelist())
                portable_manifest = json.loads(portable_archive.read("comfy-package.json"))
                self.assertEqual(portable_manifest["storage_mode"], "embedded-portable")
                self.assertNotIn("storage", portable_manifest["files"][1])

    def test_build_can_write_directly_to_shared_pool_without_full_zip_payload(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "ComfyUI"
            archive_root = base / "Archive"
            model = root / "models" / "vae" / "direct.safetensors"
            workflow = root / "user" / "default" / "workflows" / "Direct.json"
            model.parent.mkdir(parents=True)
            workflow.parent.mkdir(parents=True)
            archive_root.mkdir()
            model.write_bytes(b"direct-to-pool")
            workflow.write_text(json.dumps({"nodes": [{"id": 1, "type": "VAELoader", "widgets_values": [model.name]}]}), encoding="utf-8")

            package = archive_root / "Direct.zip"
            result = MODULE.build_package(workflow, root, package, False, False, shared_pool=True)
            self.assertEqual(result["storage_mode"], "shared-object-pool")
            with zipfile.ZipFile(package) as archive:
                self.assertNotIn("models/vae/direct.safetensors", archive.namelist())
            self.assertTrue(MODULE.verify_package(package)["valid"])
            self.assertEqual(len(list((archive_root / ".comfyshelf" / "objects" / "sha256").glob("*/*"))), 1)

    def test_modified_package_can_be_reindexed_verified_and_imported(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "ComfyUI"
            archive_root = base / "Archive"
            incoming = base / "Incoming"
            (root / "models" / "diffusion_models").mkdir(parents=True)
            incoming.mkdir()
            workflow = root / "user" / "default" / "workflows" / "video.json"
            workflow.parent.mkdir(parents=True)
            workflow.write_text(
                json.dumps(
                    {
                        "nodes": [
                            {
                                "id": 1,
                                "type": "UNETLoader",
                                "widgets_values": ["video_fp16.safetensors", "default"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            original = incoming / "original.zip"
            source = incoming / "video.zip"
            MODULE.build_package(workflow, root, original, True, False)
            with zipfile.ZipFile(original) as old, zipfile.ZipFile(source, "w") as wrapped:
                for info in old.infolist():
                    wrapped.writestr(f"video/{info.filename}", old.read(info))
            with zipfile.ZipFile(source, "a") as archive:
                archive.writestr(
                    "video/models/models/diffusion_models/video_fp16.safetensors",
                    b"new-model-content",
                )
                archive.writestr(
                    "video/models/vae/not-used.safetensors", b"must-not-be-imported"
                )

            preview = MODULE.inspect_import_package(source, archive_root, root)
            self.assertEqual(preview["matched_count"], 1)
            self.assertEqual(preview["missing"], [])
            self.assertEqual(len(preview["extra_models"]), 1)
            self.assertEqual(preview["direct_load"]["bytes_required"], len(b"new-model-content"))

            events = []
            result = MODULE.import_package(
                source, root, archive_root, False, False, progress=events.append
            )
            imported = Path(result["output_path"])
            self.assertTrue(result["complete"])
            self.assertTrue(result["verification"]["valid"])
            self.assertTrue(any(event["phase"] == "import" for event in events))
            with zipfile.ZipFile(imported) as archive:
                self.assertIn(
                    "models/diffusion_models/video_fp16.safetensors", archive.namelist()
                )
                self.assertNotIn("models/vae/not-used.safetensors", archive.namelist())
                manifest = json.loads(archive.read("comfy-package.json"))
                self.assertTrue(manifest["complete"])
                self.assertTrue(manifest["imported_and_resealed"])

            load_source = incoming / "video-load.zip"
            load_source.write_bytes(source.read_bytes())
            loaded = MODULE.import_package(
                load_source, root, archive_root, False, False, True
            )
            local_model = root / "models" / "diffusion_models" / "video_fp16.safetensors"
            self.assertTrue(loaded["loaded"])
            self.assertEqual(local_model.read_bytes(), b"new-model-content")

    def test_import_rejects_unsafe_zip_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            archive_path = base / "unsafe.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("../outside.safetensors", b"unsafe")
            with self.assertRaisesRegex(ValueError, "unsicheren Pfad"):
                MODULE.inspect_import_package(archive_path, base / "Archive")

    def test_pack_cancellation_removes_temporary_package(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "ComfyUI"
            model = root / "models" / "vae" / "audio.safetensors"
            model.parent.mkdir(parents=True)
            model.write_bytes(b"model-content" * 1024)
            workflow = root / "workflow.json"
            workflow.write_text(
                json.dumps({"nodes": [{"id": 1, "type": "VAELoader", "widgets_values": [model.name]}]}),
                encoding="utf-8",
            )
            output = base / "cancelled.zip"

            def cancel(event):
                if event["phase"] == "pack" and int(event.get("current") or 0) > 0:
                    raise MODULE.OperationCancelled("cancelled")

            with self.assertRaises(MODULE.OperationCancelled):
                MODULE.build_package(workflow, root, output, False, False, cancel)
            self.assertFalse(output.exists())
            self.assertEqual(list(base.glob("*.tmp")), [])

    def test_switch_loads_target_before_removing_old_models(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "ComfyUI"
            models = root / "models" / "vae"
            workflows = root / "user" / "default" / "workflows"
            models.mkdir(parents=True)
            workflows.mkdir(parents=True)
            old_model = models / "old.safetensors"
            new_model = models / "new.safetensors"
            old_model.write_bytes(b"old-model")
            new_model.write_bytes(b"new-model")
            old_workflow = workflows / "old.json"
            new_workflow = workflows / "new.json"
            old_workflow.write_text(
                json.dumps({"nodes": [{"id": 1, "type": "VAELoader", "widgets_values": [old_model.name]}]}),
                encoding="utf-8",
            )
            new_workflow.write_text(
                json.dumps({"nodes": [{"id": 1, "type": "VAELoader", "widgets_values": [new_model.name]}]}),
                encoding="utf-8",
            )
            old_archive = base / "old.zip"
            new_archive = base / "new.zip"
            MODULE.build_package(old_workflow, root, old_archive, False, False)
            MODULE.build_package(new_workflow, root, new_archive, False, False)
            new_model.unlink()

            preview = MODULE.switch_package(
                new_archive, [old_archive], root, False, False, False
            )
            self.assertEqual(preview["bytes_required"], len(b"new-model"))
            self.assertEqual(preview["bytes_reclaimable"], len(b"old-model"))
            self.assertTrue(old_model.exists())
            result = MODULE.switch_package(
                new_archive, [old_archive], root, True, False, False
            )
            self.assertTrue(new_model.exists())
            self.assertFalse(old_model.exists())
            self.assertEqual(result["removed"], [str(old_model.resolve())])
            self.assertEqual(MODULE.load_package_manifest(new_archive)["storage_mode"], "shared-object-pool")
            self.assertEqual(MODULE.load_package_manifest(old_archive)["storage_mode"], "shared-object-pool")

    def test_unload_is_preview_first_and_removes_only_models(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "ComfyUI"
            models = root / "models" / "vae"
            workflows = root / "user" / "default" / "workflows"
            custom = root / "custom_nodes" / "Example-Pack"
            models.mkdir(parents=True)
            workflows.mkdir(parents=True)
            custom.mkdir(parents=True)
            model = models / "audio.safetensors"
            model.write_bytes(b"model-content")
            (custom / "__init__.py").write_text("keep me", encoding="utf-8")
            workflow = workflows / "music.json"
            workflow.write_text(
                json.dumps(
                    {
                        "nodes": [
                            {
                                "id": 1,
                                "type": "VAELoader",
                                "widgets_values": ["audio.safetensors"],
                                "properties": {"cnr_id": "Example-Pack", "ver": "abc"},
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            archive = base / "music.zip"
            MODULE.build_package(workflow, root, archive, False, False)

            preview = MODULE.unload_package(archive, root, False, False, False)
            self.assertTrue(preview["dry_run"])
            self.assertTrue(preview["pool_preparation_required"])
            self.assertEqual(preview["bytes_reclaimable"], len(b"model-content"))
            self.assertTrue(model.exists())

            result = MODULE.unload_package(archive, root, True, False, False)
            self.assertFalse(model.exists())
            self.assertEqual(result["removed"], [str(model.resolve())])
            self.assertTrue(workflow.exists())
            self.assertTrue((custom / "__init__.py").exists())
            manifest = MODULE.load_package_manifest(archive)
            self.assertEqual(manifest["storage_mode"], "shared-object-pool")
            model_entry = next(item for item in manifest["files"] if item["role"] == "model")
            self.assertTrue(MODULE._shared_object_path(archive, model_entry["sha256"]).is_file())

            restore_preview = MODULE.restore_package(archive, root, False, False)
            self.assertTrue(restore_preview["dry_run"])
            self.assertEqual(len(restore_preview["candidates"]), 1)
            self.assertFalse(model.exists())
            restore_result = MODULE.restore_package(archive, root, True, False)
            self.assertEqual(restore_result["restored"], [str(model.resolve())])
            self.assertEqual(model.read_bytes(), b"model-content")

    def test_entire_library_optimization_reuses_shared_models(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "ComfyUI"
            archive_root = base / "Archive"
            model = root / "models" / "vae" / "shared.safetensors"
            workflows = root / "user" / "default" / "workflows"
            model.parent.mkdir(parents=True)
            workflows.mkdir(parents=True)
            archive_root.mkdir()
            model.write_bytes(b"one-shared-model")
            for name in ("First", "Second"):
                workflow = workflows / f"{name}.json"
                workflow.write_text(json.dumps({"nodes": [{"id": 1, "type": "VAELoader", "widgets_values": [model.name]}]}), encoding="utf-8")
                MODULE.build_package(workflow, root, archive_root / f"{name}.zip", False, False)

            preview = MODULE.inspect_library_optimization(archive_root)
            self.assertEqual(preview["package_count"], 2)
            self.assertEqual(preview["model_count"], 2)
            result = MODULE.optimize_library(archive_root)
            self.assertEqual(result["optimized_count"], 2)
            self.assertEqual(len(list((archive_root / ".comfyshelf" / "objects" / "sha256").glob("*/*"))), 1)
            self.assertEqual(MODULE.inspect_library_optimization(archive_root)["package_count"], 0)

    def test_unload_blocks_shared_model_without_explicit_override(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "ComfyUI"
            models = root / "models" / "vae"
            workflows = root / "user" / "default" / "workflows"
            models.mkdir(parents=True)
            workflows.mkdir(parents=True)
            model = models / "shared.safetensors"
            model.write_bytes(b"shared")
            workflow_data = {
                "nodes": [{"id": 1, "type": "VAELoader", "widgets_values": ["shared.safetensors"]}]
            }
            workflow = workflows / "one.json"
            workflow.write_text(json.dumps(workflow_data), encoding="utf-8")
            (workflows / "two.json").write_text(json.dumps(workflow_data), encoding="utf-8")
            archive = base / "one.zip"
            MODULE.build_package(workflow, root, archive, False, False)

            preview = MODULE.unload_package(archive, root, False, False, False)
            self.assertTrue(preview["candidates"][0]["shared_by"])
            self.assertTrue(preview["blockers"])
            with self.assertRaises(ValueError):
                MODULE.unload_package(archive, root, True, False, False)
            self.assertTrue(model.exists())
            MODULE.unload_package(archive, root, True, False, True)
            self.assertFalse(model.exists())

    def test_unload_blocks_changed_local_file(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "ComfyUI"
            models = root / "models" / "vae"
            workflows = root / "user" / "default" / "workflows"
            models.mkdir(parents=True)
            workflows.mkdir(parents=True)
            model = models / "audio.safetensors"
            model.write_bytes(b"original")
            workflow = workflows / "music.json"
            workflow.write_text(
                json.dumps(
                    {"nodes": [{"id": 1, "type": "VAELoader", "widgets_values": ["audio.safetensors"]}]}
                ),
                encoding="utf-8",
            )
            archive = base / "music.zip"
            MODULE.build_package(workflow, root, archive, False, False)
            model.write_bytes(b"changed")

            preview = MODULE.unload_package(archive, root, False, False, False)
            self.assertTrue(preview["conflicts"])
            with self.assertRaises(ValueError):
                MODULE.unload_package(archive, root, True, False, False)
            self.assertTrue(model.exists())

    def test_restore_blocks_model_hash_conflict_without_overwriting(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "ComfyUI"
            models = root / "models" / "vae"
            workflows = root / "user" / "default" / "workflows"
            models.mkdir(parents=True)
            workflows.mkdir(parents=True)
            model = models / "audio.safetensors"
            model.write_bytes(b"archived")
            workflow = workflows / "music.json"
            workflow.write_text(
                json.dumps(
                    {"nodes": [{"id": 1, "type": "VAELoader", "widgets_values": ["audio.safetensors"]}]}
                ),
                encoding="utf-8",
            )
            archive = base / "music.zip"
            MODULE.build_package(workflow, root, archive, False, False)
            model.unlink()
            model.write_bytes(b"different-local-file")

            preview = MODULE.restore_package(archive, root, False, False)
            self.assertTrue(preview["conflicts"])
            self.assertTrue(preview["blockers"])
            with self.assertRaises(ValueError):
                MODULE.restore_package(archive, root, True, False)
            self.assertEqual(model.read_bytes(), b"different-local-file")

    def test_restore_incomplete_package_requires_explicit_override(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "ComfyUI"
            vae_dir = root / "models" / "vae"
            (root / "models" / "diffusion_models").mkdir(parents=True)
            workflows = root / "user" / "default" / "workflows"
            vae_dir.mkdir(parents=True)
            workflows.mkdir(parents=True)
            model = vae_dir / "audio.safetensors"
            model.write_bytes(b"available")
            workflow = workflows / "music.json"
            workflow.write_text(
                json.dumps(
                    {
                        "nodes": [
                            {"id": 1, "type": "VAELoader", "widgets_values": ["audio.safetensors"]},
                            {"id": 2, "type": "UNETLoader", "widgets_values": ["missing.safetensors"]},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            archive = base / "partial.zip"
            MODULE.build_package(workflow, root, archive, True, False)
            MODULE.unload_package(archive, root, True, True, False)
            self.assertFalse(model.exists())

            preview = MODULE.restore_package(archive, root, False, False)
            self.assertTrue(preview["blockers"])
            with self.assertRaises(ValueError):
                MODULE.restore_package(archive, root, True, False)
            MODULE.restore_package(archive, root, True, True)
            self.assertEqual(model.read_bytes(), b"available")

    def test_restore_instant_link_modes(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "ComfyUI"
            archive_root = base / "Archive"
            archive_root.mkdir(parents=True)
            model_dir = root / "models" / "checkpoints"
            model_dir.mkdir(parents=True)
            workflow_dir = root / "user" / "default" / "workflows"
            workflow_dir.mkdir(parents=True)
            (root / "custom_nodes").mkdir()

            model = model_dir / "instant_model.safetensors"
            model_bytes = b"instant-load-test-content-12345678"
            model.write_bytes(model_bytes)

            workflow = workflow_dir / "instant_flow.json"
            workflow.write_text(
                json.dumps({"nodes": [{"id": 1, "type": "CheckpointLoaderSimple", "widgets_values": ["instant_model.safetensors"]}]}),
                encoding="utf-8"
            )

            archive = archive_root / "InstantFlow.zip"
            MODULE.build_package(workflow, root, archive, False, False, shared_pool=True)

            # Unload model so it's only in pool
            MODULE.unload_package(archive, root, True, False, True)
            self.assertFalse(model.exists())

            # Preview with link_mode="hardlink"
            preview = MODULE.restore_package(archive, root, False, False, link_mode="hardlink")
            self.assertEqual(preview["bytes_linkable"], len(model_bytes))

            # Restore with hardlink
            res = MODULE.restore_package(archive, root, True, False, link_mode="hardlink")
            self.assertTrue(model.exists())
            self.assertEqual(res["restored_methods"].get(str(model.resolve())), "hardlink")
            self.assertGreater(model.stat().st_nlink, 1)

            # Re-restore when already identical file (samefile)
            res_same = MODULE.restore_package(archive, root, True, False, link_mode="hardlink")
            self.assertIn(str(model.resolve()), res_same["identical"])
            self.assertEqual(res_same["restored"], [])

            # Unload and restore with copy
            model.unlink()
            res_copy = MODULE.restore_package(archive, root, True, False, link_mode="copy")
            self.assertEqual(res_copy["restored_methods"].get(str(model.resolve())), "copy")
            self.assertEqual(model.stat().st_nlink, 1)
            self.assertEqual(model.read_bytes(), model_bytes)

    def test_hardlink_unload_and_switch_avoids_rehashing(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "ComfyUI"
            checkpoints = root / "models" / "checkpoints"
            checkpoints.mkdir(parents=True)
            archive_root = base / "Archive"
            archive_root.mkdir()

            model = checkpoints / "FastModel.safetensors"
            model_bytes = b"model-for-fast-switch"
            model.write_bytes(model_bytes)

            workflow = root / "fast_flow.json"
            workflow.write_text(
                json.dumps({"1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": model.name}}}),
                encoding="utf-8"
            )

            archive = archive_root / "FastFlow.zip"
            MODULE.build_package(workflow, root, archive, False, False, shared_pool=True)

            # Unload model so it's in shared pool
            MODULE.unload_package(archive, root, True, False, True)
            self.assertFalse(model.exists())

            # Restore model with hardlink
            MODULE.restore_package(archive, root, True, False, link_mode="hardlink")
            self.assertTrue(model.exists())
            self.assertGreater(model.stat().st_nlink, 1)

            # Track calls to sha256_file
            original_sha256 = MODULE.sha256_file
            hashed_targets = []
            def spy_sha256(path, *args, **kwargs):
                hashed_targets.append(Path(path).resolve())
                return original_sha256(path, *args, **kwargs)

            with patch.object(MODULE, "sha256_file", side_effect=spy_sha256):
                # Unloading a hardlink should recognize samefile and NOT call sha256_file on the model!
                preview = MODULE.unload_package(archive, root, False, False, False)
                self.assertNotIn(model.resolve(), hashed_targets, "Hardlink model must use samefile fast-path without rehashing")
                self.assertEqual(len(preview["candidates"]), 1)


    def test_configuration_saves_link_mode(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "ComfyUI"
            (root / "models").mkdir(parents=True)
            (root / "user").mkdir()
            (root / "custom_nodes").mkdir()
            archive = base / "Archive"
            config_path = base / "settings" / "config.json"

            _, _, _ = MODULE.set_installation_config(
                "main", "Main Comfy", root, archive, config_path, link_mode="symlink"
            )
            active = MODULE.active_installation(config_path)
            self.assertEqual(active["link_mode"], "symlink")

            _, _, _ = MODULE.set_installation_config(
                "main", "Main Comfy", root, archive, config_path, link_mode="copy"
            )
            active = MODULE.active_installation(config_path)
            self.assertEqual(active["link_mode"], "copy")

    def test_safe_model_target_allows_symlink_to_external_storage_pool(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            comfy = base / "Comfy"
            models = comfy / "models" / "bbox"
            models.mkdir(parents=True)
            external = base / "ExternalPool" / "object.bin"
            external.parent.mkdir(parents=True)
            external.write_bytes(b"pool-object")

            target_file = models / "face_yolov8m.pt"
            try:
                os.symlink(external, target_file)
            except OSError:
                pass

            target = MODULE._safe_model_target(comfy, "models/bbox/face_yolov8m.pt")
            self.assertEqual(target.name, "face_yolov8m.pt")
            self.assertEqual(target.parent.resolve(), models.resolve())

    def test_configuration_is_saved_atomically_and_reloaded(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "ComfyUI"
            (root / "models").mkdir(parents=True)
            (root / "user").mkdir()
            (root / "custom_nodes").mkdir()
            archive = base / "Archive"
            config_path = base / "settings" / "config.json"

            _, validation, saved = MODULE.set_installation_config(
                "main", "Main Comfy", root, archive, config_path
            )
            self.assertTrue(validation["valid"])
            self.assertTrue(archive.is_dir())
            self.assertEqual(saved, config_path)
            active = MODULE.active_installation(config_path)
            self.assertEqual(active["id"], "main")
            self.assertEqual(Path(active["comfy_root"]), root.resolve())
            self.assertEqual(MODULE.select_comfy_root(None, None, config_path), root.resolve())

    def test_configuration_rejects_nested_archive(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "ComfyUI"
            (root / "models").mkdir(parents=True)
            nested_archive = root / "archives"
            nested_archive.mkdir()
            validation = MODULE.validate_installation_paths(root, nested_archive)
            self.assertFalse(validation["valid"])
            self.assertTrue(any("nicht ineinander" in error for error in validation["errors"]))

    def test_pack_uses_configured_archive_when_output_is_omitted(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "ComfyUI"
            models = root / "models" / "vae"
            workflows = root / "user" / "default" / "workflows"
            (root / "custom_nodes").mkdir(parents=True)
            models.mkdir(parents=True)
            workflows.mkdir(parents=True)
            (models / "audio.safetensors").write_bytes(b"audio")
            workflow = workflows / "Configured Music.json"
            workflow.write_text(
                json.dumps(
                    {"nodes": [{"id": 1, "type": "VAELoader", "widgets_values": ["audio.safetensors"]}]}
                ),
                encoding="utf-8",
            )
            archive_root = base / "Archive"
            config_path = base / "config.json"
            MODULE.set_installation_config("main", "Main", root, archive_root, config_path)
            exit_code = MODULE.main(
                ["--config-file", str(config_path), "pack", str(workflow)]
            )
            self.assertEqual(exit_code, 0)
            self.assertTrue((archive_root / "Configured Music.zip").is_file())

    def test_package_archive_delete_permanent_and_trash(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            archive_root = base / "Archive"
            archive_root.mkdir()
            source = archive_root / "Workflow.zip"
            source.write_bytes(b"PK\x03\x04zip")

            with patch.object(MODULE, "_send_to_trash_nt") as trash_mock:
                result_trash = MODULE.delete_package_archive(source, archive_root, send_to_trash=True)
                self.assertEqual(result_trash["name"], "Workflow")
                self.assertTrue(result_trash["trashed"])
                if os.name == "nt":
                    trash_mock.assert_called_once_with(source.resolve())

            source2 = archive_root / "Permanent.zip"
            source2.write_bytes(b"PK\x03\x04zip")
            result_perm = MODULE.delete_package_archive(source2, archive_root, send_to_trash=False)
            self.assertEqual(result_perm["name"], "Permanent")
            self.assertFalse(result_perm["trashed"])
            self.assertFalse(source2.exists())

    def test_package_archive_delete_rejects_unsafe_locations(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            archive_root = base / "Archive"
            archive_root.mkdir()
            outside = base / "Outside.zip"
            outside.write_bytes(b"PK\x03\x04zip")

            with self.assertRaises(ValueError):
                MODULE.delete_package_archive(outside, archive_root)

            not_a_zip = archive_root / "not_zip.txt"
            not_a_zip.write_text("hello")
            with self.assertRaises(ValueError):
                MODULE.delete_package_archive(not_a_zip, archive_root)

    def test_package_archive_delete_with_orphaned_pool_models(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            archive_root = base / "Archive"
            archive_root.mkdir()

            sha_orphan = "a" * 64
            sha_shared = "b" * 64

            pool_orphan = archive_root / ".comfyshelf" / "objects" / "sha256" / sha_orphan[:2] / sha_orphan
            pool_orphan.parent.mkdir(parents=True, exist_ok=True)
            pool_orphan.write_bytes(b"orphan")

            pool_shared = archive_root / ".comfyshelf" / "objects" / "sha256" / sha_shared[:2] / sha_shared
            pool_shared.parent.mkdir(parents=True, exist_ok=True)
            pool_shared.write_bytes(b"shared")

            target_zip = archive_root / "Target.zip"
            with zipfile.ZipFile(target_zip, "w") as zf:
                zf.writestr(
                    "comfy-package.json",
                    json.dumps({
                        "package_type": "comfyui-workflow-overlay",
                        "schema_version": 2,
                        "assets": [
                            {"filename": "model1.safetensors", "sha256": sha_orphan, "size": 6, "kind": "checkpoint"},
                            {"filename": "model2.safetensors", "sha256": sha_shared, "size": 6, "kind": "checkpoint"},
                        ]
                    })
                )

            sibling_zip = archive_root / "Sibling.zip"
            with zipfile.ZipFile(sibling_zip, "w") as zf:
                zf.writestr(
                    "comfy-package.json",
                    json.dumps({
                        "package_type": "comfyui-workflow-overlay",
                        "schema_version": 2,
                        "assets": [
                            {"filename": "model2.safetensors", "sha256": sha_shared, "size": 6, "kind": "checkpoint"},
                        ]
                    })
                )

            inspection = MODULE.inspect_archive_deletion(target_zip, archive_root)
            self.assertEqual(len(inspection["orphaned_models"]), 1)
            self.assertEqual(inspection["orphaned_models"][0]["sha256"], sha_orphan)
            self.assertEqual(inspection["orphaned_bytes"], 6)

            res = MODULE.delete_package_archive(
                target_zip, archive_root, send_to_trash=False, delete_orphaned_models=True
            )
            self.assertEqual(res["orphaned_deleted_count"], 1)
            self.assertFalse(target_zip.exists())
            self.assertFalse(pool_orphan.exists())
            self.assertTrue(pool_shared.exists())
            self.assertTrue(sibling_zip.exists())

    def test_png_workflow_extraction_text_and_itxt(self):
        import struct
        import zlib

        def make_png(meta, use_itxt=False, compress=False):
            def chunk(t, d):
                crc = zlib.crc32(t + d) & 0xffffffff
                return struct.pack(">I", len(d)) + t + d + struct.pack(">I", crc)
            chunks = [
                b"\x89PNG\r\n\x1a\n",
                chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)),
            ]
            for k, v in meta.items():
                if use_itxt:
                    kb, vb = k.encode("utf-8"), v.encode("utf-8")
                    d = kb + b"\x00\x01\x00\x00\x00" + zlib.compress(vb) if compress else kb + b"\x00\x00\x00\x00\x00" + vb
                    chunks.append(chunk(b"iTXt", d))
                else:
                    chunks.append(chunk(b"tEXt", k.encode("latin-1") + b"\x00" + v.encode("utf-8")))
            chunks.extend([chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00\xff")), chunk(b"IEND", b"")])
            return b"".join(chunks)

        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            # tEXt chunk
            png_text = folder / "workflow_text.png"
            png_text.write_bytes(make_png({"workflow": json.dumps({"nodes": [{"id": 1, "type": "CheckpointLoaderSimple"}]})}))
            loaded1 = MODULE.load_workflow(png_text)
            self.assertIn("nodes", loaded1)
            self.assertEqual(MODULE.workflow_format(loaded1), "comfyui-ui-workflow")

            # iTXt compressed chunk
            png_itxt = folder / "workflow_itxt.png"
            png_itxt.write_bytes(make_png({"prompt": json.dumps({"1": {"class_type": "KSampler", "inputs": {}}})}, use_itxt=True, compress=True))
            loaded2 = MODULE.load_workflow(png_itxt)
            self.assertIn("1", loaded2)
            self.assertEqual(MODULE.workflow_format(loaded2), "comfyui-api-prompt")

            # Non-PNG file
            not_png = folder / "bad.png"
            not_png.write_bytes(b"not a png image")
            with self.assertRaises(ValueError):
                MODULE.load_workflow(not_png)

            # PNG without ComfyUI metadata
            empty_png = folder / "empty.png"
            empty_png.write_bytes(make_png({"Author": "Artist"}))
            with self.assertRaises(ValueError):
                MODULE.load_workflow(empty_png)

    def test_png_workflow_packaging_and_thumbnail(self):
        import struct
        import zlib

        def make_png(meta):
            def chunk(t, d):
                crc = zlib.crc32(t + d) & 0xffffffff
                return struct.pack(">I", len(d)) + t + d + struct.pack(">I", crc)
            chunks = [
                b"\x89PNG\r\n\x1a\n",
                chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)),
            ]
            for k, v in meta.items():
                chunks.append(chunk(b"tEXt", k.encode("latin-1") + b"\x00" + v.encode("utf-8")))
            chunks.extend([chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00\xff")), chunk(b"IEND", b"")])
            return b"".join(chunks)

        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            comfy_root = base / "ComfyUI"
            archive_root = base / "Archive"
            model_dir = comfy_root / "models" / "checkpoints"
            wf_dir = comfy_root / "user" / "default" / "workflows"
            model_dir.mkdir(parents=True)
            wf_dir.mkdir(parents=True)
            archive_root.mkdir()

            model_file = model_dir / "base.safetensors"
            model_file.write_bytes(b"model data")

            png_file = wf_dir / "Art.png"
            wf_content = {
                "nodes": [
                    {"id": 1, "type": "CheckpointLoaderSimple", "widgets_values": ["base.safetensors"]}
                ]
            }
            png_file.write_bytes(make_png({"workflow": json.dumps(wf_content)}))

            zip_pkg = archive_root / "Art.zip"
            MODULE.build_package(png_file, comfy_root, zip_pkg, allow_missing=False, force=False)
            self.assertTrue(zip_pkg.exists())

            manifest = MODULE.load_package_manifest(zip_pkg)
            self.assertEqual(manifest["thumbnail"], "user/default/workflows/Art.png")

            thumb_data = MODULE.get_package_thumbnail(zip_pkg)
            self.assertIsNotNone(thumb_data)
            self.assertTrue(thumb_data.startswith("data:image/png;base64,"))

    def test_json_workflow_companion_thumbnail(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            comfy_root = base / "ComfyUI"
            archive_root = base / "Archive"
            model_dir = comfy_root / "models" / "checkpoints"
            wf_dir = comfy_root / "user" / "default" / "workflows"
            model_dir.mkdir(parents=True)
            wf_dir.mkdir(parents=True)
            archive_root.mkdir()

            (model_dir / "model.safetensors").write_bytes(b"test")

            json_file = wf_dir / "Design.json"
            json_file.write_text(json.dumps({
                "nodes": [{"id": 1, "type": "CheckpointLoaderSimple", "widgets_values": ["model.safetensors"]}]
            }))
            # Companion thumbnail image
            (wf_dir / "Design.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")

            zip_pkg = archive_root / "Design.zip"
            MODULE.build_package(json_file, comfy_root, zip_pkg, allow_missing=False, force=False)
            manifest = MODULE.load_package_manifest(zip_pkg)
            self.assertEqual(manifest["thumbnail"], "thumbnail.png")

            thumb_data = MODULE.get_package_thumbnail(zip_pkg)
            self.assertIsNotNone(thumb_data)
            self.assertTrue(thumb_data.startswith("data:image/png;base64,"))

    def test_git_remote_url_reading_and_normalization(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            node_dir = base / "custom_node"
            node_dir.mkdir()
            git_dir = node_dir / ".git"
            git_dir.mkdir()

            config = git_dir / "config"
            config.write_text(
                '[core]\n\trepositoryformatversion = 0\n'
                '[remote "origin"]\n\turl = https://github.com/ltdrdata/ComfyUI-Impact-Pack.git\n\tfetch = +refs/heads/*:refs/remotes/origin/*\n',
                encoding="utf-8",
            )
            url = MODULE._read_git_remote_url(node_dir)
            self.assertEqual(url, "https://github.com/ltdrdata/ComfyUI-Impact-Pack")

            # SSH normalization
            config.write_text(
                '[remote "origin"]\n\turl = git@github.com:example/My-Node.git\n',
                encoding="utf-8",
            )
            url = MODULE._read_git_remote_url(node_dir)
            self.assertEqual(url, "https://github.com/example/My-Node")

            # Missing git
            other = base / "no_git"
            other.mkdir()
            self.assertIsNone(MODULE._read_git_remote_url(other))

    def test_custom_node_resolution_flexible_matching(self):
        with tempfile.TemporaryDirectory() as temp:
            comfy_root = Path(temp)
            custom_nodes = comfy_root / "custom_nodes"
            custom_nodes.mkdir()

            (custom_nodes / "ComfyUI-Impact-Pack").mkdir()
            (custom_nodes / "WAS_Node_Suite").mkdir()
            (custom_nodes / "efficiency-nodes-comfyui").mkdir()

            # Exact / case-insensitive
            self.assertEqual(
                MODULE.resolve_custom_node("comfyui-impact-pack", comfy_root),
                custom_nodes / "ComfyUI-Impact-Pack",
            )
            # Hyphen/underscore normalized & prefix stripped
            self.assertEqual(
                MODULE.resolve_custom_node("impact-pack", comfy_root),
                custom_nodes / "ComfyUI-Impact-Pack",
            )
            # WAS suite
            self.assertEqual(
                MODULE.resolve_custom_node("was-node-suite-comfyui", comfy_root),
                custom_nodes / "WAS_Node_Suite",
            )
            # Efficiency nodes
            self.assertEqual(
                MODULE.resolve_custom_node("efficiency-nodes", comfy_root),
                custom_nodes / "efficiency-nodes-comfyui",
            )
            # Not found
            self.assertIsNone(MODULE.resolve_custom_node("non-existent-node", comfy_root))

    def test_scan_and_enrich_custom_nodes(self):
        with tempfile.TemporaryDirectory() as temp:
            comfy_root = Path(temp)
            custom_nodes = comfy_root / "custom_nodes"
            custom_nodes.mkdir()

            node_dir = custom_nodes / "ComfyUI-Impact-Pack"
            node_dir.mkdir()
            git_dir = node_dir / ".git"
            git_dir.mkdir()
            (git_dir / "config").write_text(
                '[remote "origin"]\n\turl = https://github.com/ltdrdata/ComfyUI-Impact-Pack.git\n',
                encoding="utf-8",
            )

            # scan_installed_custom_nodes
            installed = MODULE.scan_installed_custom_nodes(comfy_root)
            self.assertEqual(len(installed), 1)
            self.assertEqual(installed[0]["folder"], "ComfyUI-Impact-Pack")
            self.assertEqual(installed[0]["git_url"], "https://github.com/ltdrdata/ComfyUI-Impact-Pack")

            # enrich_custom_nodes
            raw_nodes = [
                {"package": "impact-pack", "revision": "v1.0"},
                {"package": "missing-pack", "revision": "v2.0"},
            ]
            enriched = MODULE.enrich_custom_nodes(raw_nodes, comfy_root)
            self.assertEqual(len(enriched), 2)

            self.assertTrue(enriched[0]["installed"])
            self.assertEqual(enriched[0]["installed_folder"], "ComfyUI-Impact-Pack")
            self.assertEqual(enriched[0]["git_url"], "https://github.com/ltdrdata/ComfyUI-Impact-Pack")
            self.assertEqual(enriched[0]["repository_url"], "https://github.com/ltdrdata/ComfyUI-Impact-Pack")
            self.assertIn("impact-pack", enriched[0]["search_url"])

            self.assertFalse(enriched[1]["installed"])
            self.assertIsNone(enriched[1]["installed_folder"])
            self.assertIsNone(enriched[1]["git_url"])
            self.assertIsNone(enriched[1]["repository_url"])
            self.assertIn("missing-pack", enriched[1]["search_url"])


if __name__ == "__main__":
    unittest.main()
