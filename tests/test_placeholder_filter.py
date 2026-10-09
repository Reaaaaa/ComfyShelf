"""Unit tests for template dummy and placeholder model exclusion (Feature B)."""

import json
from pathlib import Path
import tempfile
import unittest

import comfy_archive as engine


class PlaceholderFilterTests(unittest.TestCase):
    def test_placeholder_patterns_recognized(self):
        placeholders = [
            "YOUR_LORA_2_DISABLED.safetensors",
            "your_lora_2_disabled.safetensors",
            "YOUR_CHECKPOINT_DISABLED.safetensors",
            "YOUR_MODEL_DISABLED.ckpt",
            "YOUR_LORA.safetensors",
            "YOUR_LORA_HERE.safetensors",
            "your_lora_name.safetensors",
            "PUT_YOUR_LORA_HERE",
            "PUT_YOUR_LORA_HERE.safetensors",
            "put_your_model_here.safetensors",
            "PUT_YOUR_CHECKPOINT_HERE.safetensors",
            "PLACEHOLDER",
            "placeholder",
            "PLACEHOLDER.safetensors",
            "placeholder_lora.safetensors",
            "PLACEHOLDER_CHECKPOINT.safetensors",
            "[none]",
            "[None]",
            "none",
            "NONE",
            "(none)",
            "(None)",
            "[none].safetensors",
            "none.safetensors",
            "DUMMY.safetensors",
            "dummy_lora.safetensors",
            "INSERT_YOUR_LORA_HERE.safetensors",
            "CHOOSE_YOUR_LORA.safetensors",
            "SELECT_YOUR_CHECKPOINT.safetensors",
            "models/loras/YOUR_LORA_2_DISABLED.safetensors",
            "loras\\PUT_YOUR_LORA_HERE.safetensors",
        ]
        for name in placeholders:
            with self.subTest(name=name):
                self.assertTrue(
                    engine.is_placeholder_model_filename(name),
                    f"Expected '{name}' to be recognized as a placeholder",
                )
                self.assertFalse(
                    engine._is_model_filename(name),
                    f"Expected '{name}' not to be recognized as a real model file",
                )

    def test_legitimate_models_not_treated_as_placeholders(self):
        real_models = [
            "Illustrious-XL-v2.0.safetensors",
            "sdxl.vae.safetensors",
            "v1-5-pruned-emaonly.safetensors",
            "dreamshaper_8.safetensors",
            "sd_xl_base_1.0.safetensors",
            "flux1-dev.safetensors",
            "lora_film_grain.safetensors",
            "my_checkpoint_v2.safetensors",
            "none_of_this_matters.safetensors",
            "models/checkpoints/Illustrious-XL-v2.0.safetensors",
        ]
        for name in real_models:
            with self.subTest(name=name):
                self.assertFalse(
                    engine.is_placeholder_model_filename(name),
                    f"Expected '{name}' not to be treated as a placeholder",
                )
                self.assertTrue(
                    engine._is_model_filename(name),
                    f"Expected '{name}' to be recognized as a real model file",
                )

    def test_empty_and_non_strings(self):
        self.assertFalse(engine.is_placeholder_model_filename(""))
        self.assertFalse(engine.is_placeholder_model_filename("   "))
        self.assertFalse(engine.is_placeholder_model_filename(None))
        self.assertFalse(engine.is_placeholder_model_filename(123))

    def test_parse_ui_workflow_ignores_placeholder_models(self):
        data = {
            "nodes": [
                {
                    "id": 1,
                    "type": "CheckpointLoaderSimple",
                    "widgets_values": ["Illustrious-XL-v2.0.safetensors"],
                },
                {
                    "id": 2,
                    "type": "LoraLoader",
                    "widgets_values": ["YOUR_LORA_2_DISABLED.safetensors", 0.8, 0.8],
                },
                {
                    "id": 3,
                    "type": "LoraLoader",
                    "widgets_values": ["PUT_YOUR_LORA_HERE", 1.0, 1.0],
                },
                {
                    "id": 4,
                    "type": "LoraLoader",
                    "widgets_values": ["[none]", 1.0, 1.0],
                },
            ]
        }
        assets, _ = engine.parse_ui_workflow(data, {})
        filenames = [asset.filename for asset in assets]
        self.assertEqual(filenames, ["Illustrious-XL-v2.0.safetensors"])

    def test_parse_api_prompt_ignores_placeholder_models(self):
        data = {
            "1": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": "Illustrious-XL-v2.0.safetensors"},
            },
            "2": {
                "class_type": "LoraLoader",
                "inputs": {"lora_name": "PLACEHOLDER.safetensors"},
            },
            "3": {
                "class_type": "LoraLoader",
                "inputs": {"lora_name": "YOUR_LORA_HERE.safetensors"},
            },
        }
        assets, _ = engine.parse_api_prompt(data, {})
        filenames = [asset.filename for asset in assets]
        self.assertEqual(filenames, ["Illustrious-XL-v2.0.safetensors"])

    def test_workflow_with_only_placeholders_and_real_model_is_complete(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "ComfyUI"
            checkpoints_dir = root / "models" / "checkpoints"
            checkpoints_dir.mkdir(parents=True)
            real_ckpt = checkpoints_dir / "Illustrious-XL-v2.0.safetensors"
            real_ckpt.write_bytes(b"dummy-model-bytes")

            wf_path = root / "workflow.json"
            wf_data = {
                "nodes": [
                    {
                        "id": 1,
                        "type": "CheckpointLoaderSimple",
                        "widgets_values": ["Illustrious-XL-v2.0.safetensors"],
                    },
                    {
                        "id": 2,
                        "type": "LoraLoader",
                        "widgets_values": ["YOUR_LORA_2_DISABLED.safetensors", 0.7, 0.7],
                    },
                ]
            }
            wf_path.write_text(json.dumps(wf_data), encoding="utf-8")

            report = engine.inspect_workflow(wf_path, root, calculate_hashes=False)
            self.assertEqual(len(report["assets"]), 1)
            self.assertEqual(report["assets"][0]["filename"], "Illustrious-XL-v2.0.safetensors")
            self.assertIsNotNone(report["assets"][0]["resolved_path"])
            self.assertEqual(report["parser_warnings"], [])

            pkg_path = Path(tmp_dir) / "output.zip"
            result = engine.build_package(
                wf_path, root, pkg_path, allow_missing=False, force=True
            )
            self.assertTrue(result["complete"])
            self.assertEqual(result["missing"], [])


if __name__ == "__main__":
    unittest.main()
