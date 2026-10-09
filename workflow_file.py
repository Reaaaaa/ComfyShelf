"""Locate or restore only a package's workflow JSON, never its model payloads."""

import hashlib
import json
import os
import tempfile
import zipfile
from pathlib import Path

import comfy_archive as engine

MAX_JSON_BYTES = 32 * 1024 * 1024


class WorkflowFileError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _metadata(archive_path, comfy_root):
    archive_path, comfy_root = Path(archive_path).resolve(), Path(comfy_root).resolve()
    if not comfy_root.is_dir():
        raise FileNotFoundError(f"ComfyUI root not found: {comfy_root}")
    manifest = engine.load_package_manifest(archive_path)
    name = manifest.get("workflow", {}).get("archive_path", "")
    parts = name.split("/") if isinstance(name, str) else []
    if (not parts or any(not p or p in {".", ".."} or p.endswith((".", " "))
                         or any(c in p for c in '\\:<>"|?*')
                         or any(ord(c) < 32 for c in p)
                         or p.split(".")[0].upper() in engine._WINDOWS_RESERVED_STEMS
                         for p in parts) or not name.lower().endswith(".json")):
        raise ValueError("Invalid workflow JSON path in package manifest")
    target = engine._safe_restore_target(comfy_root, name, "workflow")
    target.relative_to(comfy_root)
    entries = [entry for entry in manifest.get("files", [])
               if str(entry.get("path", "")).casefold() == name.casefold()]
    if len(entries) != 1 or entries[0].get("role") != "workflow":
        raise ValueError("Package manifest must contain exactly one matching workflow entry")
    entry = entries[0]
    stamp = archive_path.stat()
    token = hashlib.sha256(json.dumps(
        [str(archive_path), str(target), entry, stamp.st_size, stamp.st_mtime_ns,
         stamp.st_ctime_ns, stamp.st_ino], sort_keys=True).encode()).hexdigest()
    if target.exists() and not target.is_file():
        raise FileExistsError(f"Workflow destination is not a file: {target}")
    return {"path": str(target), "exists": target.is_file(), "token": token}, name, entry


def inspect(archive_path, comfy_root):
    """Read metadata only; leave even a locally edited JSON untouched."""
    return _metadata(archive_path, comfy_root)[0]


def _publish(temporary, target):
    # Windows rename fails if the destination exists. POSIX rename would replace
    # it, so use an exclusive hard-link publication there instead.
    if os.name == "nt":
        os.rename(temporary, target)
    else:
        os.link(temporary, target)


def restore(archive_path, comfy_root, token):
    """Validate bounded JSON before writing; never overwrite or verify models."""
    report, name, entry = _metadata(archive_path, comfy_root)
    if report["token"] != token:
        raise WorkflowFileError("workflow_changed", "Package or destination changed; preview again")
    target = Path(report["path"])
    if report["exists"]:
        raise FileExistsError(f"Workflow already exists and was not overwritten: {target}")
    with zipfile.ZipFile(archive_path) as archive:
        members = [info for info in archive.infolist() if info.filename.casefold() == name.casefold()]
        if len(members) != 1:
            raise ValueError("Workflow JSON is missing or duplicated in ZIP")
        info = members[0]
        if (info.is_dir() or info.file_size > MAX_JSON_BYTES or info.flag_bits & 1
                or (info.external_attr >> 16) & 0o170000 == 0o120000):
            raise ValueError("Unsafe or oversized workflow JSON in ZIP")
        with archive.open(info) as source:
            payload = source.read(MAX_JSON_BYTES + 1)
    if (len(payload) > MAX_JSON_BYTES or len(payload) != entry.get("size")
            or hashlib.sha256(payload).hexdigest() != entry.get("sha256")):
        raise WorkflowFileError("workflow_integrity", "Workflow JSON checksum or size mismatch")
    try:
        data = json.loads(payload)
        if not isinstance(data, dict) or engine.workflow_format(data) == "unknown":
            raise ValueError("Unsupported workflow JSON")
    except (ValueError, UnicodeError) as exc:
        raise WorkflowFileError("workflow_integrity", "Invalid workflow JSON") from exc
    if inspect(archive_path, comfy_root)["token"] != token:
        raise WorkflowFileError("workflow_changed", "Package or destination changed; preview again")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".comfyshelf-workflow-",
                                         suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if inspect(archive_path, comfy_root)["token"] != token:
            raise WorkflowFileError("workflow_changed", "Package or destination changed; preview again")
        _publish(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return {**report, "exists": True, "restored": True}
