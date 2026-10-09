"""Empty LoRA slots are not missing dependencies; real selections still are."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import comfy_archive as engine


class BypassedLoraWarningTests(unittest.TestCase):
    @staticmethod
    def node(**overrides):
        return {"id": 201, "type": "LoraLoaderModelOnly", "mode": 4,
                "widgets_values": ["", 0.7], "inputs": [
                    {"name": "model", "link": 27}, {"name": "lora_name", "link": None},
                ], **overrides}

    def warnings(self, node):
        data = {"nodes": [node]}
        assets, _ = engine.parse_ui_workflow(data, {})
        return engine.parser_warnings(data, assets)

    def test_empty_bypassed_standard_lora_slots_do_not_warn(self):
        for kind in ("LoraLoaderModelOnly", "LoraLoader"):
            with self.subTest(kind=kind):
                self.assertEqual(self.warnings(self.node(type=kind)), [])

    def test_active_muted_and_unspecified_modes_keep_warning(self):
        for mode in (0, 2, None, "4"):
            with self.subTest(mode=mode):
                self.assertEqual(self.warnings(self.node(mode=mode))[0]["code"], "unresolved-loader")

    def test_linked_filename_keeps_warning_including_link_zero(self):
        for link in (0, 12):
            for name, widget in (("lora_name", None), ("renamed", {"name": "lora_name"}), ("custom_input", None)):
                with self.subTest(link=link, name=name):
                    node = self.node(inputs=[{"name": name, "widget": widget, "link": link}])
                    self.assertEqual(self.warnings(node)[0]["code"], "unresolved-loader")

    def test_selected_lora_is_collected_even_in_bypass(self):
        node = self.node(widgets_values=["optional.safetensors", 0.7])
        data = {"nodes": [node]}
        assets, _ = engine.parse_ui_workflow(data, {})
        self.assertEqual([a.filename for a in assets], ["optional.safetensors"])
        self.assertFalse(engine._is_empty_bypassed_lora(node))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "ComfyUI"
            (root / "models").mkdir(parents=True)
            workflow = root / "Selected.json"
            workflow.write_text(json.dumps(data), encoding="utf-8")
            with patch.object(engine, "load_node_rules", return_value={}):
                result = engine.build_package(workflow, root, Path(temporary) / "Selected.zip", True, False)
            self.assertFalse(result["complete"])
            self.assertEqual(result["missing"][0]["name"], "optional.safetensors")

    def test_unrecognized_or_incomplete_node_shapes_are_not_silenced(self):
        cases = [self.node(type="CustomLoraLoader"), self.node(widgets_values=[]),
                 self.node(widgets_values=[None]), self.node(widgets_values=[" "]),
                 self.node(inputs={"lora_name": [2, 0]}), self.node(inputs=[None]),
                 {"class_type": "LoraLoaderModelOnly", "mode": 4, "inputs": {"lora_name": ""}}]
        for node in cases:
            with self.subTest(node=node):
                self.assertFalse(engine._is_empty_bypassed_lora(node))

    def package(self, base, nodes):
        content = json.dumps({"nodes": nodes}).encode()
        warnings = [{"code": "unresolved-loader", "node_id": str(node["id"]), "node_type": node["type"],
                     "filename": "", "field": "", "resolved_path": ""} for node in nodes]
        manifest = {"package_type": "comfyui-workflow-overlay", "parser_warnings": warnings,
                    "workflow": {"archive_path": "user/default/workflows/Fixture.json"},
                    "files": [{"role": "workflow", "path": "user/default/workflows/Fixture.json",
                               "size": len(content), "sha256": hashlib.sha256(content).hexdigest()}]}
        archive = base / "Legacy.zip"
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr("comfy-package.json", json.dumps(manifest))
            z.writestr(manifest["workflow"]["archive_path"], content)
        return archive, manifest

    def test_legacy_archive_display_filters_only_empty_bypassed_nodes_without_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            archive, manifest = self.package(base, [self.node(), self.node(id=202, mode=0),
                                                  self.node(id=203, inputs=[{"name": "lora_name", "link": 1}])])
            before = archive.read_bytes(), archive.stat().st_mtime_ns, json.dumps(manifest)
            with patch.object(engine, "sha256_file", side_effect=AssertionError("No model hashing during refresh")):
                visible = engine.package_parser_warnings(archive, manifest)
            self.assertEqual([w["node_id"] for w in visible], ["202", "203"])
            self.assertEqual(before, (archive.read_bytes(), archive.stat().st_mtime_ns, json.dumps(manifest)))
            self.assertEqual(list(base.iterdir()), [archive])

    def test_legacy_warning_retained_when_workflow_cannot_be_trusted(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive, manifest = self.package(Path(temporary), [self.node()])
            manifest["files"][0]["sha256"] = "0" * 64
            self.assertEqual(engine.package_parser_warnings(archive, manifest), manifest["parser_warnings"])
            archive.unlink()
            self.assertEqual(engine.package_parser_warnings(archive, manifest), manifest["parser_warnings"])

    def test_archives_without_relevant_warnings_do_not_open_workflow(self):
        warnings = [{"code": "heuristic", "filename": "a.safetensors", "node_type": "LoraLoaderModelOnly"}]
        with patch.object(engine.zipfile, "ZipFile", side_effect=AssertionError("No ZIP read needed")):
            self.assertEqual(engine.package_parser_warnings(Path("unused.zip"), {"parser_warnings": warnings}), warnings)
