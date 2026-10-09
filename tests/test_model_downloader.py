"""Unit tests for model downloader and Hugging Face resolver (Feature A)."""

from io import BytesIO
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch
import zipfile

import comfy_archive as engine
import model_downloader as downloader


class ModelDownloaderTests(unittest.TestCase):
    def test_default_category_for_kind(self):
        self.assertEqual(downloader.default_category_for_kind("checkpoint"), "checkpoints")
        self.assertEqual(downloader.default_category_for_kind("lora"), "loras")
        self.assertEqual(downloader.default_category_for_kind("vae"), "vae")
        self.assertEqual(downloader.default_category_for_kind("diffusion_model"), "diffusion_models")
        self.assertEqual(downloader.default_category_for_kind("text_encoder"), "text_encoders")
        self.assertEqual(downloader.default_category_for_kind("controlnet"), "controlnet")
        self.assertEqual(downloader.default_category_for_kind("clip_vision"), "clip_vision")
        self.assertEqual(downloader.default_category_for_kind("upscale_model"), "upscale_models")
        self.assertEqual(downloader.default_category_for_kind("unknown_kind"), "checkpoints")

    def test_resolve_model_target_path(self):
        root = Path("C:/ComfyUI")
        target = downloader.resolve_model_target_path(root, "my_model.safetensors", "checkpoint")
        self.assertEqual(target, (root / "models" / "checkpoints" / "my_model.safetensors").resolve())

        subfolder_target = downloader.resolve_model_target_path(root, "anime/my_lora.safetensors", "lora")
        self.assertEqual(subfolder_target, (root / "models" / "loras" / "anime" / "my_lora.safetensors").resolve())

        with self.assertRaises(ValueError):
            downloader.resolve_model_target_path(root, "../unsafe.safetensors", "checkpoint")

        with self.assertRaises(ValueError):
            downloader.resolve_model_target_path(root, "C:/absolute/unsafe.safetensors", "checkpoint")

    def test_normalize_download_url(self):
        # Hugging Face blob URL
        blob_url = "https://huggingface.co/OnomaAIResearch/Illustrious-XL-v2.0/blob/main/Illustrious-XL-v2.0.safetensors"
        expected_resolve = "https://huggingface.co/OnomaAIResearch/Illustrious-XL-v2.0/resolve/main/Illustrious-XL-v2.0.safetensors"
        self.assertEqual(downloader.normalize_download_url(blob_url), expected_resolve)

        # Hugging Face resolve URL untouched
        self.assertEqual(downloader.normalize_download_url(expected_resolve), expected_resolve)

        # Repo URL with filename
        repo_url = "https://huggingface.co/OnomaAIResearch/Illustrious-XL-v2.0"
        self.assertEqual(downloader.normalize_download_url(repo_url, "Illustrious-XL-v2.0.safetensors"), expected_resolve)

        # Repo ID with filename
        repo_id = "OnomaAIResearch/Illustrious-XL-v2.0"
        self.assertEqual(downloader.normalize_download_url(repo_id, "Illustrious-XL-v2.0.safetensors"), expected_resolve)

        # Direct external URL
        direct_url = "https://civitai.com/api/download/models/12345"
        self.assertEqual(downloader.normalize_download_url(direct_url), direct_url)

        # Invalid URL / repo ID
        with self.assertRaises(ValueError):
            downloader.normalize_download_url("just_a_word")
        with self.assertRaises(ValueError):
            downloader.normalize_download_url("")

    @patch("urllib.request.urlopen")
    def test_search_huggingface_model(self, mock_urlopen):
        models_api_resp = json.dumps([{"id": "OnomaAIResearch/Illustrious-XL-v2.0"}]).encode("utf-8")
        tree_api_resp = json.dumps([
            {
                "path": "Illustrious-XL-v2.0.safetensors",
                "size": 6938040674,
                "lfs": {"oid": "c2a1a3eaa13d4c107dc7e00c3fe830cab427aa026362740ea094745b3422a331"},
            }
        ]).encode("utf-8")

        mock_context1 = MagicMock()
        mock_context1.read.return_value = models_api_resp
        mock_context1.__enter__.return_value = mock_context1

        mock_context2 = MagicMock()
        mock_context2.read.return_value = tree_api_resp
        mock_context2.__enter__.return_value = mock_context2

        mock_urlopen.side_effect = [mock_context1, mock_context2]

        results = downloader.search_huggingface_model("Illustrious-XL-v2.0.safetensors")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["repo_id"], "OnomaAIResearch/Illustrious-XL-v2.0")
        self.assertEqual(results[0]["path"], "Illustrious-XL-v2.0.safetensors")
        self.assertEqual(results[0]["size"], 6938040674)
        self.assertEqual(results[0]["sha256"], "c2a1a3eaa13d4c107dc7e00c3fe830cab427aa026362740ea094745b3422a331")
        self.assertEqual(
            results[0]["download_url"],
            "https://huggingface.co/OnomaAIResearch/Illustrious-XL-v2.0/resolve/main/Illustrious-XL-v2.0.safetensors",
        )

    @patch("urllib.request.urlopen")
    def test_search_huggingface_model_error_resilience(self, mock_urlopen):
        mock_urlopen.side_effect = Exception("Network offline")
        results = downloader.search_huggingface_model("Illustrious-XL-v2.0.safetensors")
        self.assertEqual(results, [])

    def test_download_model_file_streaming_and_hash(self):
        payload = b"test-model-content-streamed-via-http"
        import hashlib
        expected_hash = hashlib.sha256(payload).hexdigest()

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "ComfyUI"
            (root / "models" / "checkpoints").mkdir(parents=True)

            mock_response = MagicMock()
            mock_response.headers.get.return_value = str(len(payload))
            mock_response.read.side_effect = [payload[:10], payload[10:], b""]
            mock_response.__enter__.return_value = mock_response

            progress_events = []
            def on_progress(event):
                progress_events.append(event)

            with patch("urllib.request.urlopen", return_value=mock_response):
                res = downloader.download_model_file(
                    url_or_repo="https://example.com/fake.safetensors",
                    filename="fake.safetensors",
                    comfy_root=root,
                    kind="checkpoint",
                    expected_hash=expected_hash,
                    progress=on_progress,
                )

            self.assertTrue(res["ok"])
            self.assertEqual(res["sha256"], expected_hash)
            self.assertEqual(res["size"], len(payload))
            target_path = Path(res["target_path"])
            self.assertTrue(target_path.is_file())
            self.assertEqual(target_path.read_bytes(), payload)

            # Check that progress events were emitted
            phases = [e.get("phase") for e in progress_events]
            self.assertIn("download", phases)
            self.assertIn("complete", phases)

    def test_download_model_file_hash_mismatch_cleans_up(self):
        payload = b"corrupted-payload"
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "ComfyUI"
            (root / "models" / "checkpoints").mkdir(parents=True)

            mock_response = MagicMock()
            mock_response.headers.get.return_value = str(len(payload))
            mock_response.read.side_effect = [payload, b""]
            mock_response.__enter__.return_value = mock_response

            with patch("urllib.request.urlopen", return_value=mock_response):
                with self.assertRaises(ValueError):
                    downloader.download_model_file(
                        url_or_repo="https://example.com/corrupt.safetensors",
                        filename="corrupt.safetensors",
                        comfy_root=root,
                        kind="checkpoint",
                        expected_hash="0" * 64,  # wrong hash
                    )

            target = root / "models" / "checkpoints" / "corrupt.safetensors"
            self.assertFalse(target.exists())
            self.assertFalse(target.with_name(".corrupt.safetensors.download.tmp").exists())

    def test_download_model_file_cancellation(self):
        payload = b"some-data-stream"
        cancel_evt = threading.Event()
        cancel_evt.set()  # Cancel immediately

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "ComfyUI"
            (root / "models" / "checkpoints").mkdir(parents=True)

            mock_response = MagicMock()
            mock_response.headers.get.return_value = str(len(payload))
            mock_response.read.side_effect = [payload, b""]
            mock_response.__enter__.return_value = mock_response

            with patch("urllib.request.urlopen", return_value=mock_response):
                with self.assertRaises(engine.OperationCancelled):
                    downloader.download_model_file(
                        url_or_repo="https://example.com/cancelled.safetensors",
                        filename="cancelled.safetensors",
                        comfy_root=root,
                        kind="checkpoint",
                        cancel_event=cancel_evt,
                    )

            target = root / "models" / "checkpoints" / "cancelled.safetensors"
            self.assertFalse(target.exists())

    def test_download_model_file_archive_integration(self):
        payload = b"downloaded-and-integrated-content"
        import hashlib
        sha = hashlib.sha256(payload).hexdigest()

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "ComfyUI"
            (root / "models" / "checkpoints").mkdir(parents=True)

            # Create an incomplete package ZIP missing "model.safetensors"
            archive_path = Path(tmp_dir) / "TestWorkflow.zip"
            manifest = {
                "schema_version": 1,
                "package_type": "comfyui-workflow-overlay",
                "complete": False,
                "files": [],
                "assets": [{"filename": "model.safetensors", "kind": "checkpoint", "status": "missing"}],
                "missing": [{"type": "asset", "kind": "checkpoint", "name": "model.safetensors", "reason": "nicht gefunden"}],
            }
            with zipfile.ZipFile(archive_path, "w") as z:
                z.writestr("comfy-package.json", json.dumps(manifest))

            mock_response = MagicMock()
            mock_response.headers.get.return_value = str(len(payload))
            mock_response.read.side_effect = [payload, b""]
            mock_response.__enter__.return_value = mock_response

            with patch("urllib.request.urlopen", return_value=mock_response):
                res = downloader.download_model_file(
                    url_or_repo="https://example.com/model.safetensors",
                    filename="model.safetensors",
                    comfy_root=root,
                    kind="checkpoint",
                    archive_path=archive_path,
                )

            self.assertTrue(res["ok"])
            self.assertTrue(res["archive_updated"])

            # Verify that manifest was updated in the ZIP
            updated_manifest = engine.load_package_manifest(archive_path)
            self.assertTrue(updated_manifest["complete"])
            self.assertEqual(updated_manifest["missing"], [])
            model_files = [f for f in updated_manifest["files"] if f.get("role") == "model"]
            self.assertEqual(len(model_files), 1)
            self.assertEqual(model_files[0]["sha256"], sha)


if __name__ == "__main__":
    unittest.main()
