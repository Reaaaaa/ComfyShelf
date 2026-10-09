"""Historical integrity results for the UI, never authorization to remove files.

File metadata detects ordinary changes, not all possible tampering/bit rot.
Only a full verification may create a new successful check timestamp.
"""

from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import stat
import tempfile

LOGGER = logging.getLogger("comfyshelf.verification")
STAMP_FIELDS = ("st_size", "st_mtime_ns", "st_ctime_ns", "st_dev", "st_ino")


def cache_path(archive):
    archive = Path(archive).absolute()
    root = archive.parent.resolve()
    directory = root / ".comfyshelf" / "verification"
    # Do not follow a redirected cache directory into unrelated user data.
    if directory.resolve() != directory:
        raise OSError("Verification cache directory is redirected")
    key = hashlib.sha256(os.path.normcase(archive.name).encode("utf-8")).hexdigest()
    return directory / f"{key}.json"


def _stamp(path, stamps=None):
    key = str(path.absolute())
    if stamps is not None and key in stamps:
        return stamps[key]
    try:
        info = path.stat()
    except FileNotFoundError:
        value = None
    else:
        if not stat.S_ISREG(info.st_mode):
            raise OSError("Verification payload is not a regular file")
        value = file_stamp(info)
    if stamps is not None:
        stamps[key] = value
    return value


def file_stamp(info):
    return [getattr(info, field) for field in STAMP_FIELDS]


def _stamp_difference(before, after):
    if before is None or after is None:
        return "file missing or metadata unavailable"
    return ", ".join(field for field, left, right in zip(STAMP_FIELDS, before, after) if left != right)


def snapshot(archive, manifest, stamps=None, *, opened_archive=None):
    """Read only metadata, including every referenced pool object, not payloads."""
    archive = Path(archive).absolute()
    first = _stamp(archive)  # Always fresh for the ZIP, even within one scan.
    if first is None:
        raise FileNotFoundError(archive)
    objects = {}
    for entry in manifest.get("files", []):
        if entry.get("storage") != "shared-object":
            continue
        digest = str(entry.get("object_sha256") or entry.get("sha256") or "").lower()
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("Invalid pool object digest")
        if digest not in objects:
            objects[digest] = _stamp(archive.parent / ".comfyshelf" / "objects" / "sha256" / digest[:2] / digest, stamps)
    if opened_archive is not None:
        # Compare handle with handle, never stat(path) with fstat(handle).
        # Windows Python 3.13 can expose creation time through the former and
        # change time through the latter. Both may describe the same file.
        # A fresh read-only handle still detects a replaced/redirected ZIP;
        # no payload is read and this probe is not used during UI refresh.
        with archive.open("rb") as current:
            opened = file_stamp(os.fstat(opened_archive.fileno()))
            named = file_stamp(os.fstat(current.fileno()))
            if opened != named:
                raise OSError("Open ZIP does not match the current archive path; differing fields: "
                              + _stamp_difference(opened, named))
    if first != _stamp(archive):
        raise OSError("Archive changed while reading verification metadata")
    return {
        "zip": first, "objects": objects,
        "manifest": hashlib.sha256(json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest(),
    }


def safe_snapshot(archive, manifest, stamps=None, *, opened_archive=None):
    try:
        return snapshot(archive, manifest, stamps, opened_archive=opened_archive)
    except (OSError, ValueError, TypeError) as exc:
        LOGGER.warning("Verification metadata unavailable for %s: %s", archive, exc)
        return None


def read_record(archive):
    try:
        record = json.loads(cache_path(archive).read_text(encoding="utf-8"))
        if not isinstance(record, dict) or record.get("schema") != 1:
            return None
        if record.get("state") not in {"verified", "invalid", "stale"}:
            return None
        if not isinstance(record.get("snapshot"), dict):
            return None
        checked = datetime.fromisoformat(record["checked_at"])
        if checked.tzinfo is None:
            return None
        return record
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError, KeyError):
        LOGGER.warning("Verification record unavailable or invalid: %s", archive)
        return None


def status(archive, manifest, stamps=None):
    record = read_record(archive)
    if record is None:
        return {"state": "unknown", "checked_at": None}
    current = safe_snapshot(archive, manifest, stamps)
    state = record["state"] if current is not None and current == record["snapshot"] else "stale"
    return {"state": state, "checked_at": record["checked_at"], "persisted": True}


def _write_record(archive, record):
    path = cache_path(archive)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix=".verification-", suffix=".tmp",
                                         dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(record, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def remember(archive, manifest, before, valid):
    after = safe_snapshot(archive, manifest)
    state = ("verified" if valid else "invalid") if before is not None and before == after else "stale"
    if state == "stale":
        if before is None or after is None:
            reason = "a stable verification snapshot could not be established (see preceding warnings)"
        else:
            changes = []
            if before["zip"] != after["zip"]:
                changes.append("ZIP fields: " + _stamp_difference(before["zip"], after["zip"]))
            if before["objects"] != after["objects"]:
                changed = [digest for digest in before["objects"].keys() | after["objects"].keys()
                           if before["objects"].get(digest) != after["objects"].get(digest)]
                changes.append("pool objects: " + ", ".join(sorted(changed)))
            if before["manifest"] != after["manifest"]:
                changes.append("manifest changed")
            reason = "; ".join(changes)
        LOGGER.warning("Verification cannot be accepted for %s: %s", archive, reason)
    checked_at = datetime.now(timezone.utc).isoformat()
    result = {"state": state, "checked_at": checked_at, "persisted": False}
    # Temporary ZIPs are not library packages. Never leave cache records for them.
    if Path(archive).suffix.lower() != ".zip":
        return result
    record = {"schema": 1, "state": state, "checked_at": checked_at, "snapshot": after or {}}
    try:
        _write_record(archive, record)
        result["persisted"] = True
    except OSError:
        LOGGER.warning("Integrity check finished, but its result could not be saved: %s", archive, exc_info=True)
    return result


def prepare_rename(archive, manifest):
    record = read_record(archive)
    if record and record["snapshot"] == safe_snapshot(archive, manifest):
        return record
    return None


def finish_rename(archive, manifest, record):
    if record is None:
        return
    after = safe_snapshot(archive, manifest)
    before = record["snapshot"]
    if after is None or after["objects"] != before.get("objects") or after["manifest"] != before.get("manifest"):
        return
    # Renaming can change ctime on POSIX. Identity, size and modification time
    # must still match; renaming must never give an unrelated ZIP a green badge.
    if after["zip"][:2] + after["zip"][3:] != before["zip"][:2] + before["zip"][3:]:
        return
    try:
        _write_record(archive, {**record, "snapshot": after})
    except OSError:
        LOGGER.warning("Could not retain verification record after rename: %s", archive, exc_info=True)


def discard_record(archive):
    """Remove any cached verification record for an archive being deleted."""
    try:
        record_file = cache_path(archive)
        if record_file.is_file():
            record_file.unlink()
    except Exception:
        LOGGER.debug("Could not remove verification record: %s", archive, exc_info=True)

