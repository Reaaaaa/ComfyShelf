import sys
import os
import tempfile
import threading
import time
import types
import unittest
import json
from pathlib import Path
from unittest.mock import Mock, patch


APP_DIR = Path(__file__).parents[1]
sys.path.insert(0, str(APP_DIR))

# The bridge itself is irrelevant to these API unit tests and pywebview derives a
# base URL from unittest's zip-import path in the embedded Python distribution.
webview_stub = types.ModuleType("webview")
webview_stub.FileDialog = types.SimpleNamespace(FOLDER="folder", OPEN="open", SAVE="save")
window_stub = types.ModuleType("webview.window")
window_stub.FixPoint = types.SimpleNamespace(EAST=1, WEST=2, SOUTH=4, NORTH=8)
sys.modules["webview"] = webview_stub
sys.modules["webview.window"] = window_stub

import workflow_switcher_gui as GUI

GUI.LOGGER.disabled = True


class WorkflowSwitcherGuiTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        base = Path(temp.name)
        for name, filename in (("HISTORY_PATH", "history.json"), ("PREFERENCES_PATH", "preferences.json")):
            override = patch.object(GUI, name, base / filename)
            override.start()
            self.addCleanup(override.stop)
        isolated = patch.dict(os.environ, {
            GUI.engine.CONFIG_ENV_VAR: str(base / "config.json"),
            GUI.engine.NODE_RULES_ENV_VAR: str(APP_DIR / "node-rules.json"),
        })
        isolated.start()
        self.addCleanup(isolated.stop)

    def test_show_workflow_file_uses_active_root_and_only_reveals_existing_json(self):
        api = GUI.WorkflowSwitcherApi()
        base = GUI.PREFERENCES_PATH.parent
        report = {"exists": False, "path": str(base / "workflow.json"), "token": "preview"}
        with patch.object(GUI.engine, "active_installation", return_value={"comfy_root": str(base)}), \
             patch.object(GUI.workflow_file, "inspect", return_value=report) as inspect, \
             patch.object(GUI.workflow_file, "restore") as restore, \
             patch.object(GUI, "_reveal_workflow_file") as reveal:
            self.assertEqual(api.show_workflow_file("package.zip"), {"ok": True, "data": report})
            inspect.assert_called_once_with(Path("package.zip"), base)
            reveal.assert_not_called()
            restore.assert_not_called()
            report["exists"] = True
            self.assertTrue(api.show_workflow_file("package.zip")["ok"])
            reveal.assert_called_once_with(Path(report["path"]))

    def test_restore_workflow_json_forwards_confirmation_then_reveals(self):
        api = GUI.WorkflowSwitcherApi()
        base = GUI.PREFERENCES_PATH.parent
        report = {"path": str(base / "workflow.json"), "restored": True}
        with patch.object(GUI.engine, "active_installation", return_value={"comfy_root": str(base)}), \
             patch.object(GUI.workflow_file, "restore", return_value=report) as restore, \
             patch.object(GUI, "_reveal_workflow_file") as reveal:
            self.assertTrue(api.restore_workflow_file("package.zip", "exact-token")["ok"])
            restore.assert_called_once_with(Path("package.zip"), base, "exact-token")
            reveal.assert_called_once_with(Path(report["path"]))
            reveal.reset_mock()
            restore.side_effect = GUI.workflow_file.WorkflowFileError("workflow_changed", "Changed package")
            self.assertEqual(api.restore_workflow_file("package.zip", "stale")["error_code"], "workflow_changed")
            reveal.assert_not_called()

    def test_explorer_selection_uses_argument_list_without_shell(self):
        path = GUI.PREFERENCES_PATH.parent / "Music, ä & remix.json"
        path.write_text('{"nodes": []}', encoding="utf-8")
        with patch.object(GUI.subprocess, "Popen") as launch:
            GUI._reveal_workflow_file(path)
            launch.assert_called_once_with(["explorer.exe", "/select,", str(path)], shell=False)

    def test_native_window_is_private_and_not_exposed_to_pywebview_reflection(self):
        api = GUI.WorkflowSwitcherApi()
        native_window = object()
        api._attach_window(native_window)

        self.assertFalse(hasattr(api, "window"))
        self.assertIs(api._window, native_window)
        public_data = {
            name: getattr(api, name)
            for name in dir(api)
            if not name.startswith("_") and not callable(getattr(api, name))
        }
        self.assertEqual(public_data, {})

    def test_dependency_scan_and_completion_use_configured_roots_and_token(self):
        api = GUI.WorkflowSwitcherApi()
        base = GUI.PREFERENCES_PATH.parent
        installation = {"comfy_root": str(base / "ComfyUI"), "archive_root": str(base / "Archive")}
        archive = base / "Archive" / "Example.zip"
        result = {"added": 1, "backup": str(base / "backup.zip.bak"), "new_object_bytes": 12}
        with patch.object(GUI.engine, "active_installation", return_value=installation), \
                patch.object(GUI.dependency_scan, "scan_dependencies", return_value={"token": "opaque"}) as inspect, \
                patch.object(GUI.dependency_scan, "complete_dependencies", return_value=result) as complete, \
                patch.object(api, "_record_history") as history:
            self.assertTrue(api.scan_archive_dependencies(str(archive))["ok"])
            complete.assert_not_called()
            history.assert_not_called()
            self.assertEqual(inspect.call_args.args[:3], (archive, base / "ComfyUI", base / "Archive"))
            self.assertTrue(api.complete_archive_dependencies(str(archive), "opaque")["ok"])
            self.assertEqual(complete.call_args.args[:4], (archive, base / "ComfyUI", base / "Archive", "opaque"))
            self.assertEqual(history.call_args.args[0], "dependencies")
        error = GUI.dependency_scan.DependencyError("dependency_stale", "Changed file")
        self.assertEqual(api._error_code(error), "dependency_stale")

    def test_export_destination_picker_handles_cancel_tuple_and_string(self):
        api = GUI.WorkflowSwitcherApi()
        window = Mock()
        api._attach_window(window)
        source = GUI.PREFERENCES_PATH.parent / "Example.zip"
        destination = str(source.parent / "Travel.zip")
        for selected in (None, (), (destination,), destination):
            window.create_file_dialog.return_value = selected
            response = api.choose_export_destination(str(source))
            self.assertTrue(response["ok"])
            self.assertEqual(response["data"], destination if selected else None)
            self.assertEqual(window.create_file_dialog.call_args.args, ("save",))
            self.assertEqual(window.create_file_dialog.call_args.kwargs["save_filename"], "Example - portable.zip")

    def test_portable_export_forwards_chosen_destination_and_missing_consent(self):
        api = GUI.WorkflowSwitcherApi()
        source = GUI.PREFERENCES_PATH.parent / "Example.zip"
        destination = source.parent / "Chosen" / "Travel.zip"
        result = {"output_path": str(destination), "package": "Example", "archive_size": 123}
        with patch.object(GUI.engine, "export_portable_package", return_value=result) as export:
            response = api.export_portable_archive(str(source), str(destination), True)
        self.assertTrue(response["ok"])
        self.assertEqual(export.call_args.args, (source, destination))
        self.assertTrue(export.call_args.kwargs["allow_incomplete"])
        self.assertEqual(api._last_export_dir, destination.parent)

    def test_frontend_ready_stage_releases_startup_watchdog(self):
        api = GUI.WorkflowSwitcherApi()
        response = api.report_startup_stage("frontend-ready", "unit-test")

        self.assertTrue(response["ok"])
        self.assertTrue(api._wait_for_frontend(0))

    def test_frontend_has_event_and_immediate_bridge_bootstrap_fallbacks(self):
        source = (APP_DIR / "gui" / "app.js").read_text(encoding="utf-8")

        self.assertIn("window.addEventListener('pywebviewready'", source)
        self.assertIn("queueMicrotask(() => attempt('immediate-api-check'))", source)
        self.assertIn("attempt('bridge-poll')", source)
        self.assertIn("if (app.bootstrapStarted || !nativeApiReady()", source)

    def test_ui_prioritizes_logical_model_size_over_small_package_zip_size(self):
        html = (APP_DIR / "gui" / "index.html").read_text(encoding="utf-8")
        source = (APP_DIR / "gui" / "app.js").read_text(encoding="utf-8")
        english = json.loads((APP_DIR / "gui" / "locales" / "en.json").read_text(encoding="utf-8"))
        german = json.loads((APP_DIR / "gui" / "locales" / "de.json").read_text(encoding="utf-8"))

        self.assertNotIn('id="archiveSize"', html)
        self.assertIn('id="modelSize"', html)
        self.assertIn('id="storageImpactLabel"', html)
        self.assertIn("formatBytes(item.model_bytes)", source)
        self.assertNotIn("formatBytes(item.archive_size)", source)
        self.assertEqual(english["detail.model_size"], "Model footprint")
        self.assertEqual(german["detail.model_size"], "Modellumfang")
        self.assertIn('data-i18n="detail.model_files">Model files</span>', html)
        self.assertIn('class="metric-subtitle" data-i18n="detail.model_files_hint"', html)
        self.assertEqual(english["detail.model_files"], "Model files")
        self.assertEqual(german["detail.model_files"], "Modelldateien")
        self.assertEqual(english["detail.model_files_hint"], "(Models, VAEs, LoRAs…)")
        self.assertEqual(german["detail.model_files_hint"], "(Modelle, VAEs, LoRAs…)")
        self.assertIn("{size}", english["detail.needed_to_load"])
        self.assertIn("{size}", german["detail.currently_local_bytes"])

    def test_initial_window_size_stays_inside_the_work_area(self):
        self.assertEqual(GUI._fit_window_to_work_area(1920, 1040), (1460, 920))
        self.assertEqual(GUI._fit_window_to_work_area(1280, 720), (1248, 688))
        self.assertEqual(GUI._fit_window_to_work_area(1024, 680), (992, 650))

    def test_english_is_default_and_language_preference_persists(self):
        api = GUI.WorkflowSwitcherApi()
        with tempfile.TemporaryDirectory() as temp, patch.object(GUI, "PREFERENCES_PATH", Path(temp) / "preferences.json"):
            initial = api.get_locale()
            self.assertTrue(initial["ok"])
            self.assertEqual(initial["data"]["language"], "en")
            self.assertEqual(initial["data"]["messages"]["settings.button"], "Settings")

            changed = api.set_language("de")
            self.assertTrue(changed["ok"])
            self.assertEqual(changed["data"]["messages"]["settings.button"], "Einstellungen")
            self.assertEqual(json.loads(GUI.PREFERENCES_PATH.read_text(encoding="utf-8"))["language"], "de")
            self.assertEqual(api.get_locale()["data"]["language"], "de")

    def test_api_errors_expose_stable_localization_codes(self):
        api = GUI.WorkflowSwitcherApi()
        response = api._result(lambda: (_ for _ in ()).throw(FileExistsError("localized backend detail")))
        self.assertFalse(response["ok"])
        self.assertEqual(response["error_code"], "already_exists")

    def test_partial_removal_result_reaches_frontend_and_history(self):
        api = GUI.WorkflowSwitcherApi()
        result = {"action": "switch", "package": "Example", "removed": ["a.safetensors"],
                  "remaining": ["b.safetensors"], "removal_issues": [{"path": "b.safetensors", "code": "file_locked"}],
                  "restore": {"dry_run": False, "restored": ["new.safetensors"]}}

        def fail():
            raise GUI.engine.RemovalIncompleteError(result)

        with patch.object(api, "_record_history") as history:
            response = api._operation(fail)
        self.assertFalse(response["ok"])
        self.assertEqual(response["error_code"], "removal_incomplete")
        self.assertEqual(response["details"], result)
        self.assertEqual(history.call_args.args[0], "switch_partial")

    def test_both_locales_cover_parser_and_removal_diagnostics(self):
        english = json.loads((APP_DIR / "gui" / "locales" / "en.json").read_text(encoding="utf-8"))
        german = json.loads((APP_DIR / "gui" / "locales" / "de.json").read_text(encoding="utf-8"))
        self.assertEqual(set(english), set(german))
        for key in ("removal.file_locked", "removal.target_loaded", "parser.heuristic", "parser.path-mismatch"):
            self.assertTrue(english[key])
            self.assertTrue(german[key])

    def test_initial_state_scan_returns_immediately_and_finishes_in_background(self):
        api = GUI.WorkflowSwitcherApi()
        release = threading.Event()
        finished_state = {
            "configured": True,
            "installation": {"name": "Test", "comfy_root": "C:/Comfy", "archive_root": "D:/Archive"},
            "validation": {"comfy_free": 123},
            "packages": [],
            "scan_warnings": [],
            "summary": {"loaded": 0, "archived": 0, "partial": 0, "archive_bytes": 0},
        }

        def slow_scan():
            release.wait(2)
            return finished_state

        installation = finished_state["installation"]
        with patch.object(GUI.engine, "active_installation", return_value=installation), patch.object(api, "_build_state", side_effect=slow_scan):
            started = time.perf_counter()
            response = api.get_state()
            elapsed = time.perf_counter() - started
            self.assertTrue(response["ok"])
            self.assertTrue(response["data"]["loading"])
            self.assertLess(elapsed, 0.25)
            release.set()
            api._scan_thread.join(2)

        self.assertFalse(api._state_cache["loading"])

    def test_verified_status_survives_new_api_instance_refresh_and_rename(self):
        base = GUI.PREFERENCES_PATH.parent
        comfy_root, archive_root = base / "ComfyUI", base / "Archive"
        model = comfy_root / "models" / "vae" / "audio.safetensors"
        workflow = comfy_root / "user" / "default" / "workflows" / "Music.json"
        model.parent.mkdir(parents=True)
        workflow.parent.mkdir(parents=True)
        model.write_bytes(b"tiny model")
        workflow.write_text('{"nodes":[{"id":1,"type":"VAELoader","widgets_values":["audio.safetensors"]}]}', encoding="utf-8")
        archive = archive_root / "Music.zip"
        GUI.engine.build_package(workflow, comfy_root, archive, False, False, shared_pool=True)
        first = GUI.WorkflowSwitcherApi().verify_archive(str(archive))
        self.assertTrue(first["ok"])
        checked = first["data"]["verification"]["checked_at"]
        api = GUI.WorkflowSwitcherApi()
        packages = api._scan_packages(comfy_root, archive_root)
        self.assertEqual(packages[0]["verification"]["state"], "verified")
        self.assertEqual(packages[0]["verification"]["checked_at"], checked)
        with patch.object(GUI.engine, "active_installation", return_value={"archive_root": str(archive_root)}):
            renamed = api.rename_archive(str(archive), "Renamed Music")
        self.assertTrue(renamed["ok"], renamed)
        packages = api._scan_packages(comfy_root, archive_root)
        self.assertEqual(packages[0]["verification"]["state"], "verified")
        self.assertEqual(packages[0]["verification"]["checked_at"], checked)

    def test_library_sharing_uses_manifest_hashes_without_reading_models(self):
        base = GUI.PREFERENCES_PATH.parent
        comfy_root, archive_root = base / "ComfyUI", base / "Archive"
        archive_root.mkdir()

        def model(name, digest):
            return {"path": f"models/vae/{name}.safetensors", "role": "model", "size": 5, "sha256": digest}

        manifests = {
            "A": {"files": [model("one", "a" * 64), model("alias", "a" * 64)]},
            "B renamed": {"files": [model("different-name", "A" * 64)]},
            "Different content": {"files": [model("one", "b" * 64)]},
            "Missing hash": {"files": [model("one", None)]},
            "Also missing": {"files": [model("one", None)]},
            "Bad hash": {"files": [model("one", "not-a-hash")]},
            "Also bad": {"files": [model("one", "not-a-hash")]},
        }
        for name in manifests:
            (archive_root / f"{name}.zip").write_bytes(b"mocked manifest")
        with patch.object(GUI.engine, "load_package_manifest", side_effect=lambda path: manifests[path.stem]), \
             patch.object(GUI.engine, "sha256_file", side_effect=AssertionError("Do not rehash models")):
            packages = GUI.WorkflowSwitcherApi._scan_packages(comfy_root, archive_root)
        packages = {item["name"]: item for item in packages}
        for item in packages["A"]["models"]:
            self.assertEqual(item["library_shared_by"], [{"name": "B renamed", "path": str(archive_root / "B renamed.zip")}])
        self.assertEqual(len(packages["B renamed"]["models"][0]["library_shared_by"]), 1)
        self.assertEqual(packages["B renamed"]["models"][0]["library_shared_by"][0]["name"], "A")
        for name in ("Different content", "Missing hash", "Also missing", "Bad hash", "Also bad"):
            self.assertEqual(packages[name]["models"][0]["library_shared_by"], [])

    def test_scan_skips_bad_zip_and_reports_it_without_hiding_valid_packages(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            comfy_root = base / "ComfyUI"
            archive_root = base / "Archive"
            models = comfy_root / "models" / "vae"
            workflows = comfy_root / "user" / "default" / "workflows"
            models.mkdir(parents=True)
            workflows.mkdir(parents=True)
            archive_root.mkdir()
            (models / "audio.safetensors").write_bytes(b"model")
            workflow = workflows / "Music.json"
            workflow.write_text('{"nodes":[{"id":1,"type":"VAELoader","widgets_values":["audio.safetensors"]}]}', encoding="utf-8")
            GUI.engine.build_package(workflow, comfy_root, archive_root / "Music.zip", False, False)
            (archive_root / "Broken.zip").write_bytes(b"not a zip")

            warnings = []
            progress = []
            packages = GUI.WorkflowSwitcherApi._scan_packages(
                comfy_root,
                archive_root,
                lambda *args: progress.append(args),
                warnings,
            )

            self.assertEqual([item["name"] for item in packages], ["Music"])
            self.assertEqual(warnings[0]["name"], "Broken.zip")
            self.assertEqual(len(progress), 2)


if __name__ == "__main__":
    unittest.main()
