"""Read-only dependency discovery and explicit, transactional package completion.

Only workflow references and neighbouring package manifests are indexed. The scan
never hashes model payloads; applying a scan always checks the actual bytes.
"""
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import uuid
import zipfile

import comfy_archive as engine
import verification_cache as cache

LOGGER = logging.getLogger("comfyshelf.dependencies")


class DependencyError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _key(kind, name):
    return str(kind), str(name).replace("\\", "/").casefold()


def _stamp(path):
    return cache.file_stamp(Path(path).stat())


def _pool_path(archive, digest):
    path = engine._shared_object_path(archive, digest)
    if path.resolve() != path.absolute():
        raise ValueError("Redirected model pool paths are not supported for dependency completion.")
    return path


def _load(archive):
    manifest = engine.load_package_manifest(archive)
    member = manifest.get("workflow", {}).get("archive_path")
    entry = next((item for item in manifest.get("files", [])
                  if item.get("path") == member and item.get("role") == "workflow"), None)
    if not entry:
        raise ValueError("The package has no recorded workflow file to scan.")
    with zipfile.ZipFile(archive) as source:
        if source.getinfo(member).file_size > 32 * 1024 * 1024:
            raise ValueError("The workflow JSON is too large to scan safely (limit: 32 MiB).")
        content = source.read(member)
    if hashlib.sha256(content).hexdigest() != entry.get("sha256"):
        raise ValueError("The workflow JSON failed its recorded hash check. Verify the package first.")
    data = json.loads(content)
    rules = engine.load_node_rules()
    fmt = engine.workflow_format(data)
    if fmt == "comfyui-ui-workflow":
        assets, _ = engine.parse_ui_workflow(data, rules)
    elif fmt in {"comfyui-api-prompt", "comfyui-api-wrapper"}:
        assets, _ = engine.parse_api_prompt(data, rules)
    else:
        raise ValueError("Unsupported workflow format for dependency scanning.")
    return manifest, data, assets


def scan_dependencies(archive, comfy_root, archive_root, progress=None):
    archive, comfy_root, archive_root = map(lambda p: Path(p).absolute(), (archive, comfy_root, archive_root))
    if archive.resolve().parent != archive_root.resolve() or archive.suffix.lower() != ".zip":
        raise ValueError("Select a ZIP directly inside the configured archive folder.")
    before = _stamp(archive)
    manifest, workflow, assets = _load(archive)
    engine._emit_progress(progress, phase="dependency-scan", message="Scanning workflow dependency references", log=True)
    wanted = {_key(asset.kind, asset.filename) for asset in assets}
    pool_index = {}
    warnings = []
    for sibling in sorted(archive_root.glob("*.zip")):
        if sibling.resolve() == archive.resolve():
            continue
        engine._emit_progress(progress, phase="dependency-scan", message=f"Reading package references: {sibling.name}", file=sibling.name)
        try:
            other = engine.load_package_manifest(sibling)
            files = {item.get("path"): item for item in other.get("files", [])}
            for asset in other.get("assets", []):
                key = _key(asset.get("kind"), asset.get("filename"))
                if key not in wanted or asset.get("status") != "included":
                    continue
                entry = files.get(asset.get("archive_path"), {})
                if entry.get("role") != "model" or entry.get("storage") != engine.SHARED_OBJECT_STORAGE:
                    continue
                digest = str(entry.get("sha256", "")).lower()
                if entry.get("object_sha256", digest).lower() != digest:
                    continue
                obj = _pool_path(archive, digest)
                if not obj.is_file() or obj.stat().st_size != int(entry.get("size", -1)):
                    continue
                pool_index.setdefault(key, {})[digest] = {
                    "path": str(obj), "sha256": digest, "size": obj.stat().st_size,
                    "stamp": _stamp(obj), "package": sibling.name,
                    "archive_path": entry["path"],
                }
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            LOGGER.warning("Dependency scan skipped package %s: %s", sibling.name, exc)
            warnings.append(sibling.name)

    files = {item.get("path"): item for item in manifest.get("files", [])}
    recorded = {_key(item.get("kind"), item.get("filename")): item for item in manifest.get("assets", [])}
    rows, seen = [], set()
    for asset in assets:
        key = _key(asset.kind, asset.filename)
        if key in seen:
            continue
        seen.add(key)
        row = {"name": asset.filename, "kind": asset.kind, "asset": asdict(asset), "state": "missing",
               "source": None, "local": None, "pool": None, "candidates": [], "reason": "not-found"}
        old = recorded.get(key, {})
        if old.get("status") == "included" and files.get(old.get("archive_path"), {}).get("role") == "model":
            row.update(state="included", reason="already-linked")
            rows.append(row)
            continue
        engine._emit_progress(progress, phase="dependency-scan", message=f"Locating dependency: {asset.filename}", file=asset.filename, log=True)
        local, alternatives = engine.resolve_asset(asset, comfy_root)
        row["asset"] = asdict(asset)
        row["reason"] = asset.resolution or "not-found"
        pools = list(pool_index.get(key, {}).values())
        if alternatives or len(pools) > 1:
            row.update(state="ambiguous", reason="multiple-candidates",
                       candidates=[str(p) for p in alternatives] + [p["package"] + ": " + p["sha256"] for p in pools])
        elif asset.resolution == "unsafe-path":
            row["reason"] = "unsafe-path"
        elif local:
            target = (local.parent.resolve() / local.name).relative_to(comfy_root.resolve()).as_posix()
            engine._safe_model_target(comfy_root, target)
            row.update(state="found", source="local", archive_path=target,
                       local={"path": str(local), "stamp": _stamp(local), "size": local.stat().st_size},
                       pool=pools[0] if pools else None)
        elif pools:
            candidate = pools[0]
            # Keep the workflow's explicit category/subfolder, not an unrelated
            # basename match. Unknown custom categories need a local resolution.
            relative = engine._model_relative_path(asset.filename)
            parts = PurePosixPath(candidate["archive_path"]).parts
            if (relative and asset.search_categories and len(parts) >= 3 and parts[0] == "models"
                    and parts[1] in asset.search_categories
                    and "/".join(parts[2:]).casefold() == relative.as_posix().casefold()):
                engine._safe_model_target(comfy_root, candidate["archive_path"])
                row.update(state="found", source="pool", archive_path=candidate["archive_path"], pool=candidate)
        rows.append(row)
    if _stamp(archive) != before:
        raise ValueError("The package changed during the scan. Scan again.")
    report = {"archive": str(archive), "comfy_root": str(comfy_root.resolve()), "archive_stamp": before,
              "rows": rows, "warnings": warnings,
              "parser_warnings": engine.parser_warnings(workflow, assets),
              "counts": {state: sum(row["state"] == state for row in rows) for state in ("included", "found", "missing", "ambiguous")}}
    # Bound the confirmation to paths, file identities, parser output and package
    # contents. Apply reconstructs this plan; it never trusts client-supplied paths.
    report["manifest_hash"] = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    report["token"] = hashlib.sha256(json.dumps(report, sort_keys=True).encode()).hexdigest()
    report["space_required"] = 2 * archive.stat().st_size + 1024 * 1024 + sum(
        row["local"]["size"] for row in rows if row["state"] == "found" and row["local"] and not row["pool"])
    report["space_free"] = shutil.disk_usage(archive_root).free
    engine._emit_progress(progress, phase="dependency-scan", message="Dependency scan completed; no files changed", log=True)
    LOGGER.info("Dependency scan completed for %s: %s", archive.name, report["counts"])
    return report


def _hash_checked(path, stamp, progress, label):
    if _stamp(path) != stamp:
        raise DependencyError("dependency_stale", "A dependency changed since the scan. Scan again.")
    digest = engine.sha256_file(path, progress=progress, phase="dependency-hash", message=label)
    if _stamp(path) != stamp:
        raise DependencyError("dependency_stale", "A dependency changed while hashing. Scan again.")
    return digest


def complete_dependencies(archive, comfy_root, archive_root, token, progress=None):
    archive = Path(archive).absolute()
    report = scan_dependencies(archive, comfy_root, archive_root, progress)
    if not token or report["token"] != token:
        raise DependencyError("dependency_stale", "The scan is out of date. Scan dependencies again before adding files.")
    additions = [row for row in report["rows"] if row["state"] == "found"]
    if not additions:
        raise ValueError("There are no unambiguous dependencies to add.")
    if report["space_free"] < report["space_required"]:
        raise DependencyError("dependency_space", "Not enough free archive space for the new objects, temporary ZIP and backup.")
    existing_check = engine.verify_package(archive, progress)
    if not existing_check["valid"]:
        raise ValueError("The existing package failed integrity verification; no dependencies were added.")
    manifest, _, _ = _load(archive)
    added_keys, new_bytes, reused_bytes = set(), 0, 0
    for row in additions:
        local, pool = row["local"], row["pool"]
        expected = pool["sha256"] if pool else None
        if local:
            local_path = Path(local["path"])
            digest = _hash_checked(local_path, local["stamp"], progress, f"Hashing local dependency: {row['name']}")
            if expected and digest != expected:
                raise DependencyError("dependency_conflict", f"Local and pool files have different hashes for {row['name']}. No automatic choice was made.")
            size = local["size"]
        else:
            digest, size = expected, pool["size"]
        obj = _pool_path(archive, digest)
        if obj.is_file():
            stamp = pool["stamp"] if pool else _stamp(obj)
            actual = _hash_checked(obj, stamp, progress, f"Verifying pooled dependency: {row['name']}")
            if actual != digest or obj.stat().st_size != size:
                raise DependencyError("dependency_integrity", f"The pool object failed verification: {row['name']}")
            reused_bytes += size
        elif local:
            incoming = archive.parent / ".comfyshelf" / "objects" / ".tmp"
            if incoming.resolve() != incoming.absolute():
                raise ValueError("The pool temporary folder is redirected; no files were stored.")
            _, stored_digest, reused = engine._store_file_as_object(archive, local_path, progress)
            if stored_digest != digest or _stamp(local_path) != local["stamp"]:
                raise ValueError("A local dependency changed while storing it. The package was not updated.")
            if reused:
                reused_bytes += size
            else:
                new_bytes += size
        else:
            raise ValueError("A pooled dependency disappeared. Scan again.")
        entry = {"path": row["archive_path"], "role": "model", "size": size, "sha256": digest,
                 "storage": engine.SHARED_OBJECT_STORAGE, "object_sha256": digest}
        previous = next((item for item in manifest["files"] if item["path"].casefold() == entry["path"].casefold()), None)
        if previous and (previous.get("role") != "model" or previous.get("sha256") != digest):
            raise ValueError("An existing package path conflicts with the discovered dependency.")
        if previous is None:
            manifest["files"].append(entry)
        key = _key(row["kind"], row["name"])
        replacement = {k: v for k, v in row["asset"].items() if k not in {"resolved_path", "search_categories"}}
        replacement.update(status="included", archive_path=entry["path"], sha256=digest, size=size)
        matches = [i for i, item in enumerate(manifest["assets"]) if _key(item.get("kind"), item.get("filename")) == key]
        if matches:
            for i in matches:
                manifest["assets"][i] = {**manifest["assets"][i], **replacement}
        else:
            manifest["assets"].append(replacement)
        added_keys.add(key)
    manifest["missing"] = [item for item in manifest.get("missing", []) if _key(item.get("kind"), item.get("name")) not in added_keys]
    # Newly detected unresolved dependencies must not silently disappear from
    # completeness, even if an older parser did not record them in the manifest.
    missing_keys = {_key(item.get("kind"), item.get("name")) for item in manifest["missing"]}
    for row in report["rows"]:
        key = _key(row["kind"], row["name"])
        if row["state"] in {"missing", "ambiguous"} and key not in missing_keys:
            manifest["missing"].append({"type": "asset", "kind": row["kind"], "name": row["name"], "reason": row["reason"]})
            missing_keys.add(key)
    manifest["complete"] = not manifest["missing"]
    manifest["schema_version"] = max(2, manifest.get("schema_version", 1))
    manifest["storage_mode"] = "shared-object-pool" if all(
        entry.get("storage") == engine.SHARED_OBJECT_STORAGE for entry in manifest["files"]
        if entry.get("role") == "model") else "mixed"
    manifest["dependencies_updated_at"] = datetime.now(timezone.utc).isoformat()
    manifest["parser_warnings"] = report["parser_warnings"]
    temporary, backup = None, None
    try:
        with tempfile.NamedTemporaryFile(prefix=f".{archive.stem}.dependencies.", suffix=".tmp", dir=archive.parent, delete=False) as handle:
            temporary = Path(handle.name)
        with zipfile.ZipFile(archive) as source, zipfile.ZipFile(temporary, "w", allowZip64=True) as target:
            for info in source.infolist():
                if info.filename != "comfy-package.json":
                    engine._copy_zip_member(source, info, target, progress)
            target.writestr("comfy-package.json", json.dumps(manifest, ensure_ascii=False, indent=2), compress_type=zipfile.ZIP_DEFLATED)
        verified_snapshot = cache.safe_snapshot(temporary, manifest)
        checked = engine.verify_package(temporary, progress)
        if not checked["valid"]:
            raise ValueError("The completed package failed verification. The original ZIP was preserved.")
        if _stamp(archive) != report["archive_stamp"]:
            raise ValueError("The original ZIP changed during completion. It was not replaced.")
        backup = archive.with_name(f"{archive.stem}.before-dependencies-{uuid.uuid4().hex[:12]}.zip.bak")
        backup_complete = False
        try:
            with archive.open("rb") as source, backup.open("xb") as target:
                engine._copy_stream(source, target, expected_hash="", expected_size=archive.stat().st_size,
                                    progress=progress, phase="dependency-backup", label="Preserving original package")
                target.flush()
                os.fsync(target.fileno())
            backup_complete = True
        finally:
            if not backup_complete:
                backup.unlink(missing_ok=True)
        if _stamp(archive) != report["archive_stamp"]:
            raise ValueError("The original ZIP changed before replacement. It was not replaced.")
        engine._emit_progress(progress, phase="dependency-save", message="Publishing verified package", log=True)
        if verified_snapshot is None or cache.safe_snapshot(temporary, manifest) != verified_snapshot:
            raise DependencyError("dependency_stale", "Package payloads changed before publishing. The original was preserved.")
        os.replace(temporary, archive)
        temporary = None
        # The verified temporary ZIP moved to its final name without changing
        # payloads. Start from a fresh metadata snapshot for the historical badge.
        final_snapshot = cache.safe_snapshot(archive, manifest)
        # Rename may change ctime; all other ZIP fields and pool stamps must match.
        stable = (final_snapshot is not None and final_snapshot["objects"] == verified_snapshot["objects"]
                  and final_snapshot["zip"][:2] + final_snapshot["zip"][3:]
                  == verified_snapshot["zip"][:2] + verified_snapshot["zip"][3:])
        saved = cache.remember(archive, manifest, final_snapshot if stable else None, True)
        engine._emit_progress(progress, phase="complete", message="Missing dependencies added; original ZIP backup preserved",
                              log=True, cancellable=False)
        LOGGER.info("Dependencies added to %s: %d; backup: %s", archive, len(additions), backup)
        return {"archive": str(archive), "added": len(additions), "complete": manifest["complete"],
                "missing": manifest["missing"], "backup": str(backup), "new_object_bytes": new_bytes,
                "reused_object_bytes": reused_bytes, "verification": saved}
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
