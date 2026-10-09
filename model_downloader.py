"""Hugging Face model downloader and resolver for ComfyShelf."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable
import zipfile

import comfy_archive as engine
import verification_cache as cache

LOGGER = logging.getLogger("comfyshelf.downloader")

KIND_TO_FOLDER: dict[str, str] = {
    "checkpoint": "checkpoints",
    "lora": "loras",
    "vae": "vae",
    "diffusion_model": "diffusion_models",
    "text_encoder": "text_encoders",
    "controlnet": "controlnet",
    "clip_vision": "clip_vision",
    "upscale_model": "upscale_models",
    "style_model": "style_models",
    "model": "checkpoints",
}


def default_category_for_kind(kind: str) -> str:
    """Return the default ComfyUI models subfolder for a given asset kind."""
    clean_kind = str(kind or "").strip().lower()
    return KIND_TO_FOLDER.get(clean_kind, "checkpoints")


def resolve_model_target_path(comfy_root: Path, filename: str, kind: str = "") -> Path:
    """Compute the safe target destination path inside ComfyUI's models directory."""
    clean_name = filename.strip().replace("\\", "/")
    pure = PurePosixPath(clean_name)
    if pure.is_absolute() or ".." in pure.parts or any(p in {"", "."} for p in pure.parts):
        raise ValueError(f"Unsicherer oder ungültiger Modellpfad: {filename}")
    category = default_category_for_kind(kind)
    models_dir = (comfy_root / "models").resolve()
    target = (models_dir / category / pure).resolve()
    try:
        target.relative_to(models_dir)
    except ValueError as exc:
        raise ValueError(f"Der Zielpfad muss innerhalb des ComfyUI-models-Ordners liegen: {filename}") from exc
    return target


def normalize_download_url(url_or_repo: str, filename: str = "") -> str:
    """Normalize a Hugging Face URL, repo ID or direct download link into a direct download URL."""
    value = url_or_repo.strip()
    if not value:
        raise ValueError("Keine URL oder Repo-ID angegeben.")

    if value.startswith("http://") or value.startswith("https://"):
        parsed = urllib.parse.urlsplit(value)
        if "huggingface.co" in parsed.netloc.lower():
            path = parsed.path
            if path.endswith(".git"):
                path = path[:-4]
            if "/blob/" in path:
                path = path.replace("/blob/", "/resolve/", 1)
                return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment))
            if "/resolve/" in path:
                return value
            parts = [p for p in path.strip("/").split("/") if p]
            if len(parts) >= 2 and filename:
                repo_owner, repo_name = parts[0], parts[1]
                clean_filename = PurePosixPath(filename.strip().replace("\\", "/")).name
                return f"https://huggingface.co/{repo_owner}/{repo_name}/resolve/main/{clean_filename}"
        return value

    parts = [p for p in value.strip("/").split("/") if p]
    if len(parts) == 2:
        clean_filename = PurePosixPath(filename.strip().replace("\\", "/")).name if filename else "model.safetensors"
        return f"https://huggingface.co/{parts[0]}/{parts[1]}/resolve/main/{clean_filename}"

    raise ValueError(f"Ungültige Download-URL oder Hugging Face Repo-ID: {url_or_repo}")


def search_huggingface_model(filename: str, timeout: float = 6.0) -> list[dict[str, Any]]:
    """Search Hugging Face Hub for candidate repositories containing the requested model file."""
    clean_name = PurePosixPath(filename.strip().replace("\\", "/")).name
    if not clean_name:
        return []

    stem = PurePosixPath(clean_name).stem
    search_term = stem.split("_")[0].split("-")[0]
    if len(search_term) < 3:
        search_term = stem

    api_url = f"https://huggingface.co/api/models?search={urllib.parse.quote(search_term)}&limit=10"
    headers = {"User-Agent": "ComfyShelf/0.1.0"}
    req = urllib.request.Request(api_url, headers=headers)
    candidates: list[dict[str, Any]] = []

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            models_data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        LOGGER.debug("Hugging Face model search failed or timed out: %s", exc)
        return []

    lower_wanted = clean_name.lower()
    for item in models_data[:6]:
        repo_id = item.get("id")
        if not repo_id or "/" not in repo_id:
            continue
        tree_url = f"https://huggingface.co/api/models/{repo_id}/tree/main"
        tree_req = urllib.request.Request(tree_url, headers=headers)
        try:
            with urllib.request.urlopen(tree_req, timeout=timeout) as tree_resp:
                tree_data = json.loads(tree_resp.read().decode("utf-8"))
            for entry in tree_data:
                entry_path = entry.get("path", "")
                if entry_path.lower() == lower_wanted or PurePosixPath(entry_path).name.lower() == lower_wanted:
                    oid = (entry.get("lfs") or {}).get("oid")
                    candidates.append({
                        "repo_id": repo_id,
                        "path": entry_path,
                        "size": entry.get("size", 0),
                        "sha256": oid,
                        "downloads": item.get("downloads", 0),
                        "likes": item.get("likes", 0),
                        "download_url": f"https://huggingface.co/{repo_id}/resolve/main/{entry_path}",
                    })
        except Exception:
            continue

    candidates.sort(
        key=lambda c: (
            c["path"].lower() == lower_wanted,
            c.get("downloads", 0),
            c.get("likes", 0),
        ),
        reverse=True,
    )
    return candidates


def format_bytes(bytes_count: int | float | None) -> str:
    """Format byte counts into human-readable strings."""
    if bytes_count is None:
        return "—"
    value = float(bytes_count)
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    unit = 0
    while value >= 1024 and unit < len(units) - 1:
        value /= 1024
        unit += 1
    return f"{value:.1f} {units[unit]}" if unit > 0 else f"{int(value)} B"


def _format_rate(speed_bytes_sec: float) -> str:
    if speed_bytes_sec <= 0:
        return "—"
    if speed_bytes_sec >= 1024 * 1024:
        return f"{speed_bytes_sec / (1024 * 1024):.1f} MiB/s"
    if speed_bytes_sec >= 1024:
        return f"{speed_bytes_sec / 1024:.1f} KiB/s"
    return f"{int(speed_bytes_sec)} B/s"


def _format_eta(seconds: float) -> str:
    if seconds < 0 or seconds > 86400 * 7:
        return "—"
    sec = int(seconds)
    if sec < 60:
        return f"{sec}s"
    minutes = sec // 60
    remaining_sec = sec % 60
    if minutes < 60:
        return f"{minutes}m {remaining_sec:02d}s"
    hours = minutes // 60
    remaining_min = minutes % 60
    return f"{hours}h {remaining_min:02d}m"


def download_model_file(
    url_or_repo: str,
    filename: str,
    comfy_root: Path,
    kind: str = "",
    expected_hash: str = "",
    archive_path: Path | None = None,
    store_in_pool: bool = False,
    progress: engine.ProgressCallback = None,
    cancel_event: threading.Event | None = None,
    chunk_size: int = 1024 * 1024,
) -> dict[str, Any]:
    """Download a model file with streaming progress, SHA-256 validation and archive integration."""
    target_path = resolve_model_target_path(comfy_root, filename, kind)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    url = normalize_download_url(url_or_repo, filename)
    LOGGER.info("Starting model download: %s -> %s", url, target_path)

    engine._emit_progress(
        progress,
        phase="download",
        message=f"Verbindung zu Hugging Face wird hergestellt ({filename})...",
        file=filename,
        current=0,
        total=0,
        log=True,
    )

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "ComfyShelf/0.1.0 (https://github.com/Reaaaaa/ComfyShelf)",
        },
    )

    temp_path = target_path.with_name(f".{target_path.name}.download.tmp")
    if temp_path.exists():
        temp_path.unlink(missing_ok=True)

    started_at = time.monotonic()
    last_emit = 0.0
    downloaded = 0
    total_size = 0
    actual_hash = ""

    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            content_length_header = response.headers.get("Content-Length")
            if content_length_header and content_length_header.isdigit():
                total_size = int(content_length_header)

            if total_size > 0:
                free_space = shutil.disk_usage(target_path.parent).free
                if free_space < total_size + 50 * 1024 * 1024:
                    raise OSError(
                        f"Nicht genügend freier Speicherplatz auf dem Ziellaufwerk "
                        f"({format_bytes(free_space)} frei, {format_bytes(total_size)} benötigt)."
                    )

            digest = hashlib.sha256()
            with open(temp_path, "wb") as handle:
                while True:
                    if cancel_event and cancel_event.is_set():
                        raise engine.OperationCancelled("Download wurde sicher abgebrochen.")
                    chunk = response.read(chunk_size)
                    if not chunk:
                        break
                    handle.write(chunk)
                    digest.update(chunk)
                    downloaded += len(chunk)

                    now = time.monotonic()
                    if now - last_emit >= 0.2:
                        last_emit = now
                        elapsed = max(0.001, now - started_at)
                        speed = downloaded / elapsed
                        eta = (total_size - downloaded) / speed if (total_size > downloaded and speed > 0) else 0.0
                        engine._emit_progress(
                            progress,
                            phase="download",
                            file=filename,
                            current=downloaded,
                            total=total_size,
                            rate=_format_rate(speed),
                            eta=f"ETA {_format_eta(eta)}" if total_size > 0 else "",
                            message=f"{filename}: {format_bytes(downloaded)}"
                            + (f" von {format_bytes(total_size)}" if total_size > 0 else ""),
                        )

                handle.flush()
                os.fsync(handle.fileno())

            actual_hash = digest.hexdigest()

        if expected_hash and expected_hash.strip():
            clean_expected = expected_hash.strip().lower()
            if actual_hash.lower() != clean_expected:
                raise ValueError(
                    f"SHA-256-Integritätsprüfung fehlgeschlagen für {filename}: "
                    f"Erwartet {clean_expected}, erhalten {actual_hash}"
                )

        os.replace(temp_path, target_path)
        LOGGER.info("Model download complete: %s (%d bytes, sha256=%s)", target_path, downloaded, actual_hash)

    except Exception:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        raise

    archive_updated = False
    if archive_path and archive_path.is_file():
        try:
            archive_updated = _integrate_downloaded_model(
                archive_path=archive_path,
                model_path=target_path,
                filename=filename,
                kind=kind,
                size=downloaded,
                sha256=actual_hash,
                store_in_pool=store_in_pool,
                progress=progress,
            )
        except Exception as exc:
            LOGGER.warning("Could not automatically update package manifest: %s", exc)

    engine._emit_progress(
        progress,
        phase="complete",
        file=filename,
        current=downloaded,
        total=total_size or downloaded,
        message=f"Modell erfolgreich heruntergeladen: {filename}",
        log=True,
    )

    return {
        "ok": True,
        "filename": filename,
        "target_path": str(target_path),
        "size": downloaded,
        "sha256": actual_hash,
        "archive_updated": archive_updated,
    }


def _integrate_downloaded_model(
    archive_path: Path,
    model_path: Path,
    filename: str,
    kind: str,
    size: int,
    sha256: str,
    store_in_pool: bool = False,
    progress: engine.ProgressCallback = None,
) -> bool:
    """Update a package manifest so the downloaded model is marked as present and not missing."""
    manifest = engine.load_package_manifest(archive_path)
    clean_filename = PurePosixPath(filename.strip().replace("\\", "/")).name
    category = default_category_for_kind(kind)
    rel_path = f"models/{category}/{clean_filename}"

    missing = manifest.get("missing", [])
    updated_missing = [
        item for item in missing
        if PurePosixPath(str(item.get("name", ""))).name.lower() != clean_filename.lower()
    ]
    manifest["missing"] = updated_missing
    manifest["complete"] = len(updated_missing) == 0

    if store_in_pool:
        engine._store_file_as_object(archive_path, model_path, progress)
        storage_type = engine.SHARED_OBJECT_STORAGE
    else:
        storage_type = "local"

    files = manifest.setdefault("files", [])
    existing_file = next((f for f in files if PurePosixPath(str(f.get("path", ""))).name.lower() == clean_filename.lower()), None)
    if existing_file:
        existing_file["size"] = size
        existing_file["sha256"] = sha256
        if store_in_pool:
            existing_file["storage"] = storage_type
            existing_file["object_sha256"] = sha256
    else:
        file_entry = {
            "path": rel_path,
            "role": "model",
            "size": size,
            "sha256": sha256,
            "storage": storage_type,
        }
        if store_in_pool:
            file_entry["object_sha256"] = sha256
        files.append(file_entry)

    assets = manifest.setdefault("assets", [])
    existing_asset = next((a for a in assets if PurePosixPath(str(a.get("filename", ""))).name.lower() == clean_filename.lower()), None)
    if existing_asset:
        existing_asset["status"] = "included"
        existing_asset["archive_path"] = rel_path
        existing_asset["size"] = size
        existing_asset["sha256"] = sha256
    else:
        assets.append({
            "filename": filename,
            "kind": kind or "model",
            "status": "included",
            "archive_path": rel_path,
            "size": size,
            "sha256": sha256,
        })

    temp_zip: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{archive_path.stem}.update.",
            suffix=".tmp",
            dir=archive_path.parent,
            delete=False,
        ) as handle:
            temp_zip = Path(handle.name)

        with zipfile.ZipFile(archive_path, "r") as source, zipfile.ZipFile(
            temp_zip, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True
        ) as target:
            for info in source.infolist():
                if info.filename != "comfy-package.json":
                    engine._copy_zip_member(source, info, target)
            target.writestr(
                "comfy-package.json",
                json.dumps(manifest, ensure_ascii=False, indent=2),
            )
        os.replace(temp_zip, archive_path)
        temp_zip = None
        return True
    finally:
        if temp_zip and temp_zip.exists():
            temp_zip.unlink(missing_ok=True)
