import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import workflow_file as subject


class WorkflowFileTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "ComfyUI"
        self.root.mkdir()
        self.archive = self.base / "Renamed package.zip"
        self.name = "user/default/workflows/Music/Subfolder/Original, ä.json"
        self.payload = json.dumps({"nodes": [], "extra": {"note": "my workflow"}}).encode()
        self.write_package()

    def write_package(self, *, payload=None, digest=None, name=None, include=True, duplicate=False, symlink=False):
        name = name if name is not None else self.name
        payload = payload if payload is not None else self.payload
        entry = {"path": name, "role": "workflow", "size": len(payload),
                 "sha256": digest or hashlib.sha256(payload).hexdigest()}
        manifest = {"package_type": "comfyui-workflow-overlay", "complete": False,
                    "workflow": {"archive_path": name}, "files": [entry,
                    {"path": "models/checkpoints/missing.safetensors", "role": "model",
                     "size": 100000000000, "sha256": "0" * 64}]}
        with zipfile.ZipFile(self.archive, "w") as archive:
            archive.writestr("comfy-package.json", json.dumps(manifest))
            if include:
                member = zipfile.ZipInfo(name)
                if symlink:
                    member.create_system = 3
                    member.external_attr = 0o120777 << 16
                archive.writestr(member, payload)
                if duplicate:
                    archive.writestr(name.upper(), payload)

    def restore(self):
        preview = subject.inspect(self.archive, self.root)
        return subject.restore(self.archive, self.root, preview["token"])

    def test_preview_is_read_only_and_uses_manifest_not_archive_name(self):
        before = self.archive.read_bytes()
        result = subject.inspect(self.archive, self.root)
        self.assertFalse(result["exists"])
        self.assertEqual(Path(result["path"]), self.root.joinpath(*self.name.split("/")))
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertEqual(self.archive.read_bytes(), before)

    def test_extract_json_returns_dict_without_modifying_files(self):
        result = subject.extract_json(self.archive)
        self.assertIn("workflow", result)
        self.assertEqual(result["workflow"]["extra"]["note"], "my workflow")
        self.assertEqual(list(self.root.iterdir()), [])

    def test_restore_only_json_from_incomplete_archive_no_models_or_verification(self):
        before = self.archive.read_bytes()
        with patch.object(subject.engine, "verify_package", side_effect=AssertionError("Full verification forbidden")):
            result = self.restore()
        self.assertTrue(result["restored"])
        self.assertEqual(Path(result["path"]).read_bytes(), self.payload)
        self.assertFalse((self.root / "models").exists())
        self.assertFalse((self.base / ".comfyshelf").exists())
        self.assertEqual(self.archive.read_bytes(), before)
        self.assertEqual([p for p in self.root.rglob("*") if p.is_file()], [Path(result["path"])])

    def test_existing_edited_json_is_never_overwritten(self):
        path = Path(self.restore()["path"])
        path.write_bytes(b"local edits")
        preview = subject.inspect(self.archive, self.root)
        self.assertTrue(preview["exists"])
        with self.assertRaises(FileExistsError):
            subject.restore(self.archive, self.root, preview["token"])
        self.assertEqual(path.read_bytes(), b"local edits")

    def test_changed_package_requires_new_confirmation(self):
        preview = subject.inspect(self.archive, self.root)
        self.write_package(payload=b'{"nodes": [], "changed": true}')
        with self.assertRaises(subject.WorkflowFileError) as caught:
            subject.restore(self.archive, self.root, preview["token"])
        self.assertEqual(caught.exception.code, "workflow_changed")
        self.assertEqual(list(self.root.iterdir()), [])

    def test_changed_destination_requires_new_confirmation(self):
        preview = subject.inspect(self.archive, self.root)
        other = self.base / "Other ComfyUI"
        other.mkdir()
        with self.assertRaises(subject.WorkflowFileError):
            subject.restore(self.archive, other, preview["token"])
        self.assertEqual(list(other.iterdir()), [])

    def test_checksum_failure_writes_nothing(self):
        self.write_package(digest="0" * 64)
        with self.assertRaises(subject.WorkflowFileError) as caught:
            self.restore()
        self.assertEqual(caught.exception.code, "workflow_integrity")
        self.assertEqual(list(self.root.iterdir()), [])

    def test_invalid_json_or_non_workflow_writes_nothing(self):
        for payload in (b"not JSON", b"[]", b'{"some": "document"}'):
            with self.subTest(payload=payload):
                self.write_package(payload=payload)
                with self.assertRaises(subject.WorkflowFileError):
                    self.restore()
                self.assertEqual(list(self.root.iterdir()), [])

    def test_missing_duplicate_or_symlink_member_is_rejected(self):
        for options in ({"include": False}, {"duplicate": True}, {"symlink": True}):
            with self.subTest(options=options):
                self.write_package(**options)
                with self.assertRaises(ValueError):
                    self.restore()
                self.assertEqual(list(self.root.iterdir()), [])

    def test_unsafe_manifest_paths_are_rejected(self):
        for name in ("../outside.json", "user/default/workflows/../outside.json",
                     "user/default/workflows/C:evil.json", "user/default/workflows/CON.json",
                     "user/default/workflows/a\\escape.json", "user/default/workflows/a.json.",
                     "user//workflows/a.json", "user/default/workflows/a.exe",
                     "models/workflows/a.json", "/user/workflows/a.json"):
            with self.subTest(name=name):
                self.write_package(name=name)
                with self.assertRaises(ValueError):
                    subject.inspect(self.archive, self.root)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_oversized_json_rejected_before_reading(self):
        with patch.object(subject, "MAX_JSON_BYTES", 5):
            with self.assertRaises(ValueError):
                self.restore()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_concurrent_file_creation_never_overwrites(self):
        original = subject._publish
        def competing_publish(temporary, target):
            target.write_bytes(b"someone else's JSON")
            original(temporary, target)
        with patch.object(subject, "_publish", side_effect=competing_publish):
            with self.assertRaises(FileExistsError):
                self.restore()
        target = self.root.joinpath(*self.name.split("/"))
        self.assertEqual(target.read_bytes(), b"someone else's JSON")
        self.assertEqual(list(self.root.rglob("*.tmp")), [])

    def test_publish_failure_cleans_temporary_file(self):
        with patch.object(subject, "_publish", side_effect=PermissionError("Locked destination")):
            with self.assertRaises(PermissionError):
                self.restore()
        self.assertEqual([p for p in self.root.rglob("*") if p.is_file()], [])


if __name__ == "__main__":
    unittest.main()
