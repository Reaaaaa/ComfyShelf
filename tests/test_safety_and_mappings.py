"""Safety regressions use tiny files in temporary directories, never real models."""
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

APP_DIR = Path(__file__).parents[1]
sys.path.insert(0, str(APP_DIR))
import comfy_archive as engine


class SafetyAndMappingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "ComfyUI"
        self.models = self.root / "models" / "vae"
        self.workflows = self.root / "user" / "default" / "workflows"
        self.archive = self.base / "Archive"
        for path in (self.models, self.workflows, self.archive):
            path.mkdir(parents=True)
        self.rules_path = self.base / "rules.json"
        self.rules_path.write_text('{"schema_version":1,"nodes":{}}', encoding="utf-8")
        env = patch.dict(os.environ, {engine.NODE_RULES_ENV_VAR: str(self.rules_path)})
        env.start()
        self.addCleanup(env.stop)

    def package(self, name="Example", filenames=("one.safetensors", "two.safetensors", "three.safetensors")):
        paths = []
        nodes = []
        for i, filename in enumerate(filenames):
            path = self.models.joinpath(*filename.replace("\\", "/").split("/"))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"test model {name} {i}".encode())
            paths.append(path)
            nodes.append({"id": i + 1, "type": "VAELoader", "widgets_values": [filename]})
        workflow = self.workflows / f"{name}.json"
        workflow.write_text(json.dumps({"nodes": nodes}), encoding="utf-8")
        archive = self.archive / f"{name}.zip"
        engine.build_package(workflow, self.root, archive, False, False, shared_pool=True)
        return archive, paths, workflow

    def test_access_preflight_blocks_all_removals(self):
        archive, paths, _ = self.package()
        issue = {"path": str(paths[1]), "code": "file_locked", "reason": "File is in use"}
        original = engine._deletion_access_issue
        with patch.object(engine, "_deletion_access_issue", side_effect=lambda p: issue if p == paths[1] else original(p)):
            preview = engine.unload_package(archive, self.root, False, False, False)
            self.assertEqual(preview["removal_issues"], [issue])
            self.assertTrue(preview["blockers"])
            with self.assertRaises(engine.RemovalIncompleteError) as caught:
                engine.unload_package(archive, self.root, True, False, False)
        self.assertEqual(caught.exception.result["removed"], [])
        self.assertEqual(set(caught.exception.result["remaining"]), set(map(str, paths)))
        self.assertTrue(all(path.exists() for path in paths))

    def test_late_delete_failure_reports_partial_result_and_retry_is_safe(self):
        archive, paths, _ = self.package()
        original = Path.unlink

        def fail_second(path, *args, **kwargs):
            if path == paths[1]:
                exc = PermissionError("simulated sharing violation")
                exc.winerror = 32
                raise exc
            return original(path, *args, **kwargs)

        with patch.object(Path, "unlink", fail_second):
            with self.assertRaises(engine.RemovalIncompleteError) as caught:
                engine.unload_package(archive, self.root, True, False, False)
        result = caught.exception.result
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["removed"], [str(paths[0])])
        self.assertEqual(result["remaining"], list(map(str, paths[1:])))
        self.assertEqual(result["removal_issues"][0]["code"], "file_locked")
        self.assertFalse(paths[0].exists())
        self.assertTrue(all(path.exists() for path in paths[1:]))
        self.assertTrue(engine.verify_package(archive)["valid"])
        retried = engine.unload_package(archive, self.root, True, False, False)
        self.assertEqual(retried["removed"], list(map(str, paths[1:])))

    def test_change_after_first_removal_keeps_modified_file(self):
        archive, paths, _ = self.package()

        def progress(event):
            if event["phase"] == "remove" and event.get("file") == paths[0].name:
                paths[1].write_bytes(b"changed concurrently; must be kept")

        with self.assertRaises(engine.RemovalIncompleteError) as caught:
            engine.unload_package(archive, self.root, True, False, False, progress)
        self.assertEqual(caught.exception.result["removed"], [str(paths[0])])
        self.assertEqual(caught.exception.result["removal_issues"][0]["code"], "file_changed")
        self.assertEqual(paths[1].read_bytes(), b"changed concurrently; must be kept")
        self.assertTrue(paths[2].exists())

    def test_switch_preserves_actual_restore_result_when_removal_fails(self):
        old, old_paths, _ = self.package("Old")
        new, new_paths, _ = self.package("New", ("new.safetensors",))
        new_paths[0].unlink()
        original = Path.unlink

        def fail_second(path, *args, **kwargs):
            if path == old_paths[1]:
                raise PermissionError("simulated late denial")
            return original(path, *args, **kwargs)

        with patch.object(Path, "unlink", fail_second):
            with self.assertRaises(engine.RemovalIncompleteError) as caught:
                engine.switch_package(new, [old], self.root, True, False, False)
        result = caught.exception.result
        self.assertEqual(result["action"], "switch")
        self.assertFalse(result["restore"]["dry_run"])
        self.assertIn(str(new_paths[0]), result["restore"]["restored"])
        self.assertEqual(result["removed"], [str(old_paths[0])])
        self.assertTrue(new_paths[0].exists())
        self.assertTrue(all(path.exists() for path in old_paths[1:]))
        self.assertTrue(engine.verify_package(old)["valid"])

    @unittest.skipUnless(os.name == "nt", "Windows delete-sharing semantics")
    def test_real_windows_file_handle_blocks_preflight_without_changing_file(self):
        import ctypes
        from ctypes import wintypes
        archive, paths, _ = self.package()
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                      wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        before = paths[1].read_bytes()
        handle = kernel.CreateFileW(str(paths[1]), 0x80000000, 0x3, None, 3, 0, None)
        self.assertNotEqual(handle, ctypes.c_void_p(-1).value)
        try:
            issue = engine._deletion_access_issue(paths[1])
            self.assertEqual(issue["code"], "file_locked")
            with self.assertRaises(engine.RemovalIncompleteError):
                engine.unload_package(archive, self.root, True, False, False)
            self.assertTrue(all(path.exists() for path in paths))
        finally:
            kernel.CloseHandle(handle)
        self.assertEqual(paths[1].read_bytes(), before)
        self.assertIsNone(engine._deletion_access_issue(paths[1]))

    @unittest.skipUnless(os.name == "nt", "Windows read-only attribute")
    def test_readonly_file_is_not_mistaken_for_removable(self):
        _, paths, _ = self.package(filenames=("read-only.safetensors",))
        try:
            os.chmod(paths[0], stat.S_IREAD)
            self.assertEqual(engine._deletion_access_issue(paths[0])["code"], "read_only")
        finally:
            os.chmod(paths[0], stat.S_IWRITE)

    def test_nested_paths_roundtrip_through_pool_export_import_and_restore(self):
        for separator in ("/", "\\"):
            with self.subTest(separator=separator):
                archive, paths, workflow = self.package(filenames=(separator.join(("SDXL", "realism", "model.safetensors")),))
                unrelated = self.models / "other" / "model.safetensors"
                unrelated.parent.mkdir(exist_ok=True)
                unrelated.write_bytes(b"different model with same basename")
                before = paths[0].read_bytes()
                manifest = engine.load_package_manifest(archive)
                self.assertEqual(manifest["assets"][0]["archive_path"], "models/vae/SDXL/realism/model.safetensors")
                engine.unload_package(archive, self.root, True, False, False)
                self.assertFalse(paths[0].exists())
                self.assertTrue(unrelated.exists())
                portable = self.base / f"portable-{ord(separator)}.zip"
                engine.export_portable_package(archive, portable)
                imports = self.base / f"Imports-{ord(separator)}"
                imports.mkdir()
                report = engine.inspect_import_package(portable, imports, self.root)
                self.assertEqual(report["missing"], [])
                self.assertEqual(report["assets"][0]["resolution"], "exact-path")
                imported = engine.import_package(portable, self.root, imports, False, False)
                engine.restore_package(Path(imported["output_path"]), self.root, True, False)
                self.assertEqual(paths[0].read_bytes(), before)
                self.assertEqual(unrelated.read_bytes(), b"different model with same basename")
                archive.unlink()  # only this test's tiny ZIP; the next subtest rebuilds it

    def test_explicit_subfolder_mismatch_is_not_silently_replaced(self):
        path = self.models / "other" / "model.safetensors"
        path.parent.mkdir()
        path.write_bytes(b"not the requested location")
        asset = engine.Asset("vae", "wanted/model.safetensors", "1", "VAELoader", "widgets_values[0]", "known-node-rule", ("vae",))
        resolved, alternatives = engine.resolve_asset(asset, self.root)
        self.assertIsNone(resolved)
        self.assertEqual(alternatives, [path])
        self.assertEqual(asset.resolution, "path-mismatch")
        source = self.base / "mismatch.zip"
        workflow = {"nodes": [{"id": 1, "type": "VAELoader", "widgets_values": [asset.filename]}]}
        with zipfile.ZipFile(source, "w") as z:
            z.writestr("user/default/workflows/Example.json", json.dumps(workflow))
            z.writestr("models/vae/other/model.safetensors", b"not wanted")
        report = engine.inspect_import_package(source, self.archive)
        self.assertEqual(report["matched_count"], 0)
        self.assertIn("path-mismatch", [w["code"] for w in report["parser_warnings"]])

    def test_basename_fallback_and_ambiguous_names_are_reported(self):
        path = self.models / "nested" / "audio.safetensors"
        path.parent.mkdir()
        path.write_bytes(b"audio")
        data = {"nodes": [{"id": 1, "type": "VAELoader", "widgets_values": [path.name]}]}
        workflow = self.workflows / "example.json"
        workflow.write_text(json.dumps(data), encoding="utf-8")
        report = engine.inspect_workflow(workflow, self.root, False)
        self.assertEqual(report["assets"][0]["resolved_path"], str(path))
        self.assertEqual(report["parser_warnings"][0]["code"], "basename-fallback")
        second = self.models / "other" / path.name
        second.parent.mkdir()
        second.write_bytes(b"another")
        report = engine.inspect_workflow(workflow, self.root, False)
        self.assertIsNone(report["assets"][0]["resolved_path"])
        self.assertEqual(report["parser_warnings"][0]["code"], "ambiguous-path")

    def test_local_rules_support_unusual_keys_wildcards_and_api_inputs(self):
        rules = {"schema_version": 1, "nodes": {"ExampleCustomLoader": {
            "ui": [{"path": ["properties", "adapters", "*", "file"], "kind": "lora", "categories": ["loras"]}],
            "api": [{"path": ["inputs", "unusual_key"], "kind": "vae", "categories": ["vae"]}],
        }}}
        self.rules_path.write_text(json.dumps(rules), encoding="utf-8")
        assets, _ = engine.parse_ui_workflow({"nodes": [{"id": 7, "type": "ExampleCustomLoader",
            "properties": {"adapters": [{"file": "a.safetensors"}, {"file": "b.safetensors"}]}}]})
        self.assertEqual([a.filename for a in assets], ["a.safetensors", "b.safetensors"])
        self.assertTrue(all(a.kind == "lora" and a.detection == "custom-node-rule" for a in assets))
        self.assertEqual(assets[1].source_field, "properties.adapters[1].file")
        api_assets, _ = engine.parse_api_prompt({"9": {"class_type": "ExampleCustomLoader", "inputs": {"unusual_key": "v.safetensors"}}})
        self.assertEqual(api_assets[0].kind, "vae")
        self.assertEqual(api_assets[0].detection, "custom-node-rule")

    def test_invalid_rules_fail_closed_and_cannot_escape_models(self):
        for category in ("../outside", "C:/outside", "/outside", "vae/../other"):
            with self.subTest(category=category):
                self.rules_path.write_text(json.dumps({"schema_version": 1, "nodes": {"Loader": {"ui": [
                    {"path": ["widgets_values", 0], "kind": "model", "categories": [category]}
                ]}}}), encoding="utf-8")
                with self.assertRaises(ValueError):
                    engine.parse_ui_workflow({"nodes": []})
        for filename in ("../outside.safetensors", "C:\\outside.safetensors", "/outside.safetensors"):
            asset = engine.Asset("model", filename, "1", "Loader", "file", "extension-heuristic", ())
            self.assertEqual(engine.resolve_asset(asset, self.root), (None, []))
            self.assertEqual(asset.resolution, "unsafe-path")

    def test_parser_warnings_survive_archiving_and_import(self):
        path = self.models / "audio.safetensors"
        path.write_bytes(b"audio")
        workflow = self.workflows / "heuristic.json"
        workflow.write_text(json.dumps({"nodes": [
            {"id": 1, "type": "FancyVae", "widgets_values": [path.name]},
            {"id": 2, "type": "DynamicLoader", "widgets_values": ["computed-at-runtime"]},
        ]}), encoding="utf-8")
        archive = self.archive / "heuristic.zip"
        engine.build_package(workflow, self.root, archive, False, False)
        manifest = engine.load_package_manifest(archive)
        self.assertEqual({w["code"] for w in manifest["parser_warnings"]}, {"heuristic", "unresolved-loader"})
        self.assertEqual(manifest["assets"][0]["detection"], "extension-heuristic")
        report = engine.inspect_import_package(archive, self.base)
        self.assertEqual({w["code"] for w in report["parser_warnings"]}, {"heuristic", "unresolved-loader"})

    def test_shared_reference_uses_actual_path_even_when_model_kind_differs(self):
        archive, paths, _ = self.package(filenames=("shared.safetensors",))
        # The generic node identifies this as 'model', the package as 'vae'.
        other = self.workflows / "other.json"
        other.write_text(json.dumps({"nodes": [{"id": 4, "type": "OtherLoader", "widgets_values": [paths[0].name]}]}), encoding="utf-8")
        preview = engine.unload_package(archive, self.root, False, False, False)
        self.assertEqual(preview["candidates"][0]["shared_by"], [str(other)])
        self.assertTrue(preview["blockers"])

    def test_invalid_rules_cannot_silently_disable_shared_model_protection(self):
        archive, paths, _ = self.package()
        self.rules_path.write_text("not JSON", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "loader mappings"):
            engine.unload_package(archive, self.root, True, False, False)
        self.assertTrue(all(path.exists() for path in paths))

    def test_non_model_image_input_is_not_reported_as_unknown_model_loader(self):
        workflow = {"nodes": [{"id": 1, "type": "LoadImage", "widgets_values": ["photo.png"]}]}
        assets, _ = engine.parse_ui_workflow(workflow)
        self.assertEqual(engine.parser_warnings(workflow, assets), [])


if __name__ == "__main__":
    unittest.main()
