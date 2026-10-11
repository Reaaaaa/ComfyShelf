#!/usr/bin/env python3
"""Inspect ComfyUI workflows without modifying the ComfyUI installation."""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import shutil
import stat
import urllib.parse
import struct
import sys
import tempfile
import uuid
import zipfile
import zlib
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable


MODEL_EXTENSIONS = {
    ".safetensors",
    ".ckpt",
    ".pt",
    ".pth",
    ".bin",
    ".onnx",
    ".gguf",
}

CONFIG_ENV_VAR = "COMFY_WORKFLOW_SWITCHER_CONFIG"
CONFIG_SCHEMA_VERSION = 1


def default_config_path() -> Path:
    override = os.environ.get(CONFIG_ENV_VAR)
    if override:
        return Path(override).expanduser()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "ComfyWorkflowSwitcher" / "config.json"
    return Path.home() / ".config" / "comfy-workflow-switcher" / "config.json"


def empty_config() -> dict[str, Any]:
    return {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "active_installation": None,
        "installations": {},
    }


def load_config(config_path: Path | None = None) -> dict[str, Any]:
    path = config_path or default_config_path()
    if not path.is_file():
        return empty_config()
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Ungültige Konfigurationsdatei {path}: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema_version") != CONFIG_SCHEMA_VERSION:
        raise ValueError(f"Nicht unterstützte Konfiguration: {path}")
    if not isinstance(data.get("installations"), dict):
        raise ValueError(f"Konfiguration enthält keine gültigen Installationen: {path}")
    return data


def save_config(data: dict[str, Any], config_path: Path | None = None) -> Path:
    path = config_path or default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False, mode="w", encoding="utf-8"
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(serialized)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
        temporary_name = None
    finally:
        if temporary_name:
            try:
                Path(temporary_name).unlink(missing_ok=True)
            except OSError:
                pass
    return path


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _filesystem_type(path: Path) -> str | None:
    if os.name != "nt":
        return None
    try:
        import ctypes

        volume_root = Path(path.resolve().anchor)
        fs_buffer = ctypes.create_unicode_buffer(256)
        ok = ctypes.windll.kernel32.GetVolumeInformationW(
            str(volume_root), None, 0, None, None, None, fs_buffer, len(fs_buffer)
        )
        return fs_buffer.value if ok else None
    except (AttributeError, OSError, ValueError):
        return None


def validate_installation_paths(
    comfy_root: Path,
    archive_root: Path,
    create_archive: bool = False,
) -> dict[str, Any]:
    comfy_root = comfy_root.expanduser().resolve()
    archive_root = archive_root.expanduser().resolve()
    errors: list[str] = []
    warnings: list[str] = []

    if not comfy_root.is_dir():
        errors.append(f"ComfyUI-Hauptordner existiert nicht: {comfy_root}")
    else:
        if not (comfy_root / "models").is_dir():
            errors.append(f"Kein models-Ordner gefunden: {comfy_root / 'models'}")
        if not (comfy_root / "user").is_dir():
            warnings.append(f"Kein user-Ordner gefunden: {comfy_root / 'user'}")
        if not (comfy_root / "custom_nodes").is_dir():
            warnings.append(f"Kein custom_nodes-Ordner gefunden: {comfy_root / 'custom_nodes'}")

    if not archive_root.exists() and create_archive:
        try:
            archive_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            errors.append(f"Auslagerungsordner konnte nicht erstellt werden: {exc}")
    if not archive_root.is_dir():
        errors.append(f"Auslagerungsordner existiert nicht: {archive_root}")
    elif not os.access(archive_root, os.W_OK):
        errors.append(f"Auslagerungsordner ist nicht beschreibbar: {archive_root}")

    if comfy_root.exists() and archive_root.exists():
        if _is_relative_to(archive_root, comfy_root) or _is_relative_to(comfy_root, archive_root):
            errors.append("ComfyUI- und Auslagerungsordner dürfen nicht ineinander liegen.")
        if comfy_root.drive and archive_root.drive and comfy_root.drive.casefold() == archive_root.drive.casefold():
            warnings.append(
                "ComfyUI und Archiv liegen auf demselben Laufwerk; Entladen schafft dort keinen zusätzlichen freien Speicher."
            )

    filesystem = _filesystem_type(archive_root) if archive_root.exists() else None
    if filesystem and filesystem.upper() == "FAT32":
        errors.append("Der Auslagerungsordner liegt auf FAT32; ZIP-Dateien über 4 GiB werden dort nicht unterstützt.")

    comfy_free = shutil.disk_usage(comfy_root).free if comfy_root.is_dir() else None
    archive_free = shutil.disk_usage(archive_root).free if archive_root.is_dir() else None
    return {
        "valid": not errors,
        "comfy_root": str(comfy_root),
        "archive_root": str(archive_root),
        "errors": errors,
        "warnings": warnings,
        "comfy_free": comfy_free,
        "archive_free": archive_free,
        "archive_filesystem": filesystem,
    }


def set_installation_config(
    installation_id: str,
    name: str,
    comfy_root: Path,
    archive_root: Path,
    config_path: Path | None = None,
    link_mode: str = "auto",
) -> tuple[dict[str, Any], dict[str, Any], Path]:
    if not installation_id or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for char in installation_id):
        raise ValueError("Installations-ID darf nur Kleinbuchstaben, Zahlen, '-' und '_' enthalten.")
    validation = validate_installation_paths(comfy_root, archive_root, create_archive=True)
    if not validation["valid"]:
        details = "\n".join(f"- {error}" for error in validation["errors"])
        raise ValueError(f"Pfade konnten nicht gespeichert werden:\n{details}")
    if link_mode not in {"copy", "auto", "hardlink", "symlink"}:
        link_mode = "auto"
    data = load_config(config_path)
    data["installations"][installation_id] = {
        "name": name.strip() or installation_id,
        "comfy_root": validation["comfy_root"],
        "archive_root": validation["archive_root"],
        "link_mode": link_mode,
    }
    data["active_installation"] = installation_id
    saved_path = save_config(data, config_path)
    return data, validation, saved_path


def active_installation(config_path: Path | None = None) -> dict[str, Any] | None:
    data = load_config(config_path)
    active_id = data.get("active_installation")
    installation = data.get("installations", {}).get(active_id)
    if not installation:
        return None
    return {"id": active_id, "link_mode": installation.get("link_mode", "auto"), **installation}

# node type -> widget position -> (asset kind, model directory categories)
UI_NODE_RULES: dict[str, dict[int, tuple[str, tuple[str, ...]]]] = {
    "CheckpointLoaderSimple": {0: ("checkpoint", ("checkpoints",))},
    "CheckpointLoader": {0: ("checkpoint", ("checkpoints",))},
    "UNETLoader": {0: ("diffusion_model", ("diffusion_models", "unet"))},
    "VAELoader": {0: ("vae", ("vae",))},
    "CLIPLoader": {0: ("text_encoder", ("text_encoders", "clip"))},
    "DualCLIPLoader": {
        0: ("text_encoder", ("text_encoders", "clip")),
        1: ("text_encoder", ("text_encoders", "clip")),
    },
    "TripleCLIPLoader": {
        0: ("text_encoder", ("text_encoders", "clip")),
        1: ("text_encoder", ("text_encoders", "clip")),
        2: ("text_encoder", ("text_encoders", "clip")),
    },
    "LoraLoader": {0: ("lora", ("loras",))},
    "LoraLoaderModelOnly": {0: ("lora", ("loras",))},
    "ControlNetLoader": {0: ("controlnet", ("controlnet",))},
    "DiffControlNetLoader": {0: ("controlnet", ("controlnet",))},
    "CLIPVisionLoader": {0: ("clip_vision", ("clip_vision",))},
    "UpscaleModelLoader": {0: ("upscale_model", ("upscale_models",))},
    "StyleModelLoader": {0: ("style_model", ("style_models",))},
}

INPUT_RULES: dict[str, tuple[str, tuple[str, ...]]] = {
    "ckpt_name": ("checkpoint", ("checkpoints",)),
    "checkpoint_name": ("checkpoint", ("checkpoints",)),
    "unet_name": ("diffusion_model", ("diffusion_models", "unet")),
    "model_name": ("model", ()),
    "vae_name": ("vae", ("vae",)),
    "clip_name": ("text_encoder", ("text_encoders", "clip")),
    "clip_name1": ("text_encoder", ("text_encoders", "clip")),
    "clip_name2": ("text_encoder", ("text_encoders", "clip")),
    "clip_name3": ("text_encoder", ("text_encoders", "clip")),
    "lora_name": ("lora", ("loras",)),
    "control_net_name": ("controlnet", ("controlnet",)),
    "controlnet_name": ("controlnet", ("controlnet",)),
    "clip_vision": ("clip_vision", ("clip_vision",)),
    "clip_vision_name": ("clip_vision", ("clip_vision",)),
    "upscale_model": ("upscale_model", ("upscale_models",)),
    "model_file": ("model", ()),
}


NODE_RULES_ENV_VAR = "COMFY_WORKFLOW_SWITCHER_NODE_RULES"


def load_node_rules(path: Path | None = None) -> dict[str, Any]:
    """Load declarative local mappings; never load code or rules from a workflow/ZIP."""
    override = os.environ.get(NODE_RULES_ENV_VAR)
    if path:
        selected = path
    elif override:
        selected = Path(override).expanduser()
    else:
        candidates = [Path(__file__).resolve().with_name("node-rules.json")]
        if getattr(sys, "frozen", False):
            exe_rules = Path(sys.executable).resolve().with_name("node-rules.json")
            if exe_rules not in candidates:
                candidates.insert(0, exe_rules)
            meipass = getattr(sys, "_MEIPASS", None)
            if meipass:
                meipass_rules = Path(meipass) / "node-rules.json"
                if meipass_rules not in candidates:
                    candidates.append(meipass_rules)
        selected = candidates[0]
        for cand in candidates:
            if cand.exists():
                selected = cand
                break
    if not selected.exists() and path is None and not override:
        return {}
    try:
        data = json.loads(selected.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Cannot read loader mappings: {selected}") from exc
    if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("nodes"), dict):
        raise ValueError(f"Invalid loader mapping schema: {selected}")
    nodes = data["nodes"]
    for node_type, formats in nodes.items():
        if not isinstance(node_type, str) or not node_type or not isinstance(formats, dict) or not formats:
            raise ValueError("Loader mappings require a node type and ui/api rules")
        if set(formats) - {"ui", "api"}:
            raise ValueError(f"Unsupported loader mapping format for {node_type}")
        for rules in formats.values():
            if not isinstance(rules, list):
                raise ValueError(f"Loader mappings must be lists: {node_type}")
            for rule in rules:
                if not isinstance(rule, dict) or set(rule) != {"path", "kind", "categories"}:
                    raise ValueError("Each loader rule requires path, kind and categories")
                path_parts = rule["path"]
                if (not isinstance(path_parts, list) or not 1 <= len(path_parts) <= 16
                        or any(not (isinstance(part, str) and part) and not (type(part) is int and part >= 0) for part in path_parts)):
                    raise ValueError("Rule paths must contain JSON keys, non-negative indices or '*'")
                kind = rule["kind"]
                if not isinstance(kind, str) or not kind or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789_" for c in kind):
                    raise ValueError("Invalid model kind in loader mapping")
                categories = rule["categories"]
                if not isinstance(categories, list) or any(not isinstance(c, str) or _model_relative_path(c) is None for c in categories):
                    raise ValueError("Loader categories must be relative directories inside models")
    return nodes


def _mapped_values(value: Any, path: list[Any], prefix: str = "") -> Iterable[tuple[str, str]]:
    if not path:
        if isinstance(value, str):
            yield prefix, value
        return
    key, *rest = path
    if key == "*":
        children = value.items() if isinstance(value, dict) else enumerate(value) if isinstance(value, list) else []
    elif isinstance(value, dict) and isinstance(key, str) and key in value:
        children = [(key, value[key])]
    elif isinstance(value, list) and type(key) is int and key < len(value):
        children = [(key, value[key])]
    else:
        children = []
    for child_key, child in children:
        field = f"{prefix}[{child_key}]" if isinstance(child_key, int) else f"{prefix}.{child_key}" if prefix else str(child_key)
        yield from _mapped_values(child, rest, field)


def _mapped_assets(node: dict[str, Any], node_id: str, node_type: str, fmt: str, rules: dict[str, Any]) -> list[Asset]:
    result = []
    for rule in rules.get(node_type, {}).get(fmt, []):
        for field, value in _mapped_values(node, rule["path"]):
            if _is_model_filename(value):
                result.append(Asset(rule["kind"], value, node_id, node_type, field,
                                    "custom-node-rule", tuple(rule["categories"])))
    return result


@dataclass
class Asset:
    kind: str
    filename: str
    source_node_id: str
    source_node_type: str
    source_field: str
    detection: str
    search_categories: tuple[str, ...]
    resolved_path: str | None = None
    size: int | None = None
    sha256: str | None = None
    resolution: str | None = None


@dataclass(frozen=True)
class CustomNode:
    package: str
    revision: str | None


def read_png_metadata(data: bytes) -> dict[str, str]:
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("Keine gültige PNG-Datei.")
    offset = 8
    metadata: dict[str, str] = {}
    data_len = len(data)
    while offset + 8 <= data_len:
        length, chunk_type = struct.unpack(">I4s", data[offset:offset+8])
        offset += 8
        if offset + length > data_len:
            break
        chunk_data = data[offset:offset+length]
        offset += length + 4  # skip CRC
        if chunk_type == b"tEXt":
            parts = chunk_data.split(b"\x00", 1)
            if len(parts) == 2:
                key = parts[0].decode("latin-1", "replace")
                try:
                    val = parts[1].decode("utf-8")
                except UnicodeDecodeError:
                    val = parts[1].decode("latin-1", "replace")
                metadata[key] = val
        elif chunk_type == b"iTXt":
            parts = chunk_data.split(b"\x00", 1)
            if len(parts) == 2:
                key = parts[0].decode("utf-8", "replace")
                remainder = parts[1]
                if len(remainder) >= 2:
                    compression_flag = remainder[0]
                    null1 = remainder.find(b"\x00", 2)
                    if null1 != -1:
                        null2 = remainder.find(b"\x00", null1 + 1)
                        if null2 != -1:
                            text_bytes = remainder[null2 + 1:]
                            if compression_flag == 1:
                                try:
                                    text_bytes = zlib.decompress(text_bytes)
                                except Exception:
                                    continue
                            metadata[key] = text_bytes.decode("utf-8", "replace")
        elif chunk_type == b"IEND":
            break
    return metadata


def load_png_workflow_from_bytes(raw_bytes: bytes) -> dict[str, Any]:
    metadata = read_png_metadata(raw_bytes)
    target_raw = metadata.get("workflow") or metadata.get("prompt")
    if not target_raw:
        raise ValueError("In der PNG-Datei wurden keine ComfyUI-Metadaten ('workflow' oder 'prompt') gefunden.")
    try:
        data = json.loads(target_raw)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"Ungültige JSON-Metadaten im PNG-Bild ({exc})") from exc
    if not isinstance(data, dict):
        raise ValueError("Die extrahierten Workflow-Metadaten müssen ein JSON-Objekt sein.")
    return data


def load_png_workflow(path: Path) -> dict[str, Any]:
    try:
        raw_bytes = path.read_bytes()
    except FileNotFoundError as exc:
        raise ValueError(f"Workflow nicht gefunden: {path}") from exc
    except OSError as exc:
        raise ValueError(f"PNG-Datei konnte nicht gelesen werden: {exc}") from exc
    return load_png_workflow_from_bytes(raw_bytes)


def load_workflow(path: Path) -> dict[str, Any]:
    if path.suffix.casefold() == ".png":
        return load_png_workflow(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise ValueError(f"Workflow nicht gefunden: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Ungültige JSON-Datei ({exc})") from exc
    if not isinstance(data, dict):
        raise ValueError("Die oberste JSON-Struktur muss ein Objekt sein.")
    return data


def workflow_format(data: dict[str, Any]) -> str:
    if isinstance(data.get("nodes"), list):
        return "comfyui-ui-workflow"
    if data and all(
        isinstance(value, dict) and "class_type" in value for value in data.values()
    ):
        return "comfyui-api-prompt"
    if isinstance(data.get("prompt"), dict):
        return "comfyui-api-wrapper"
    return "unknown"


PLACEHOLDER_REGEX = re.compile(
    r"^(?:"
    r"\[?none\]?|"
    r"\(none\)|"
    r"placeholder(?:[_\-.\s].*)?|"
    r"dummy(?:[_\-.\s].*)?|"
    r"(?:put|insert|choose|select)[_-]your(?:[_\-.\s].*)?|"
    r"your[_-].*[_-]disabled(?:[_\-.\s].*)?|"
    r"your[_-](?:lora|checkpoint|model|unet|diffusion|vae|clip|controlnet)(?:[_\-.\s].*)?"
    r")$",
    re.IGNORECASE,
)


def is_placeholder_model_filename(value: Any) -> bool:
    """Return True if a string represents a template placeholder or dummy model filename."""
    if not isinstance(value, str):
        return False
    clean = value.strip().replace("\\", "/")
    if not clean:
        return False
    name = PurePosixPath(clean).name
    lower_name = name.lower()
    lower_stem = (
        PurePosixPath(name).stem.lower()
        if PurePosixPath(name).suffix.lower() in MODEL_EXTENSIONS
        else lower_name
    )
    return bool(PLACEHOLDER_REGEX.match(lower_name) or PLACEHOLDER_REGEX.match(lower_stem))


def _is_model_filename(value: Any) -> bool:
    return (
        isinstance(value, str)
        and not is_placeholder_model_filename(value)
        and Path(value).suffix.lower() in MODEL_EXTENSIONS
    )


def _generic_kind(node_type: str, field: str = "") -> tuple[str, tuple[str, ...]]:
    key = f"{node_type} {field}".lower()
    guesses = (
        ("lora", "lora", ("loras",)),
        ("vae", "vae", ("vae",)),
        ("control", "controlnet", ("controlnet",)),
        ("upscale", "upscale_model", ("upscale_models",)),
        ("clipvision", "clip_vision", ("clip_vision",)),
        ("clip_vision", "clip_vision", ("clip_vision",)),
        ("clip", "text_encoder", ("text_encoders", "clip")),
        ("text_encoder", "text_encoder", ("text_encoders", "clip")),
        ("unet", "diffusion_model", ("diffusion_models", "unet")),
        ("diffusion", "diffusion_model", ("diffusion_models", "unet")),
        ("checkpoint", "checkpoint", ("checkpoints",)),
    )
    for marker, kind, categories in guesses:
        if marker in key:
            return kind, categories
    return "model", ()


def _walk_strings(value: Any, prefix: str = "") -> Iterable[tuple[str, str]]:
    if isinstance(value, str):
        yield prefix, value
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_strings(item, f"{prefix}[{index}]")
    elif isinstance(value, dict):
        for key, item in value.items():
            child = f"{prefix}.{key}" if prefix else str(key)
            yield from _walk_strings(item, child)


def parse_ui_workflow(data: dict[str, Any], rules: dict[str, Any] | None = None) -> tuple[list[Asset], list[CustomNode]]:
    rules = load_node_rules() if rules is None else rules
    assets: list[Asset] = []
    custom_nodes: set[CustomNode] = set()

    for node in data.get("nodes", []):
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id", "?"))
        node_type = str(node.get("type", "unknown"))
        properties = node.get("properties") or {}
        if isinstance(properties, dict):
            package = properties.get("cnr_id")
            if package and package != "comfy-core":
                custom_nodes.add(CustomNode(str(package), properties.get("ver")))

        widget_values = node.get("widgets_values")
        if not isinstance(widget_values, list):
            widget_values = [] if widget_values is None else [widget_values]

        mapped = _mapped_assets(node, node_id, node_type, "ui", rules)
        assets.extend(mapped)
        claimed: set[str] = {asset.filename for asset in mapped}
        for position, (kind, categories) in UI_NODE_RULES.get(node_type, {}).items():
            if position < len(widget_values) and _is_model_filename(widget_values[position]) and widget_values[position] not in claimed:
                filename = widget_values[position]
                claimed.add(filename)
                assets.append(
                    Asset(
                        kind=kind,
                        filename=filename,
                        source_node_id=node_id,
                        source_node_type=node_type,
                        source_field=f"widgets_values[{position}]",
                        detection="known-node-rule",
                        search_categories=categories,
                    )
                )

        # Extension fallback catches model loaders supplied by custom nodes. It is
        # deliberately marked as a heuristic so callers can review it.
        for field, value in _walk_strings(widget_values, "widgets_values"):
            if _is_model_filename(value) and value not in claimed:
                kind, categories = _generic_kind(node_type, field)
                assets.append(
                    Asset(
                        kind=kind,
                        filename=value,
                        source_node_id=node_id,
                        source_node_type=node_type,
                        source_field=field,
                        detection="extension-heuristic",
                        search_categories=categories,
                    )
                )

    return _deduplicate_assets(assets), sorted(custom_nodes, key=lambda x: x.package.lower())


def parse_api_prompt(data: dict[str, Any], rules: dict[str, Any] | None = None) -> tuple[list[Asset], list[CustomNode]]:
    rules = load_node_rules() if rules is None else rules
    if isinstance(data.get("prompt"), dict):
        data = data["prompt"]
    assets: list[Asset] = []
    for node_id, node in data.items():
        if not isinstance(node, dict):
            continue
        node_type = str(node.get("class_type", "unknown"))
        inputs = node.get("inputs") or {}
        if not isinstance(inputs, dict):
            continue
        mapped = _mapped_assets(node, str(node_id), node_type, "api", rules)
        assets.extend(mapped)
        claimed: set[str] = {asset.filename for asset in mapped}
        for field, value in inputs.items():
            if field in INPUT_RULES and _is_model_filename(value) and value not in claimed:
                kind, categories = INPUT_RULES[field]
                claimed.add(value)
                assets.append(
                    Asset(
                        kind=kind,
                        filename=value,
                        source_node_id=str(node_id),
                        source_node_type=node_type,
                        source_field=field,
                        detection="named-input-rule",
                        search_categories=categories,
                    )
                )
        for field, value in _walk_strings(inputs, "inputs"):
            if _is_model_filename(value) and value not in claimed:
                kind, categories = _generic_kind(node_type, field)
                assets.append(
                    Asset(
                        kind=kind,
                        filename=value,
                        source_node_id=str(node_id),
                        source_node_type=node_type,
                        source_field=field,
                        detection="extension-heuristic",
                        search_categories=categories,
                    )
                )
    return _deduplicate_assets(assets), []


def _deduplicate_assets(assets: list[Asset]) -> list[Asset]:
    result: list[Asset] = []
    seen: set[tuple[str, str]] = set()
    for asset in assets:
        key = (asset.kind, asset.filename.replace("\\", "/").lower())
        if key not in seen:
            seen.add(key)
            result.append(asset)
    return result


def infer_comfy_root(workflow_path: Path) -> Path | None:
    for parent in workflow_path.resolve().parents:
        if parent.name.lower() == "comfyui" and (parent / "models").is_dir():
            return parent
    return None


def configured_roots(config_path: Path | None = None) -> tuple[Path | None, Path | None]:
    installation = active_installation(config_path)
    if not installation:
        return None, None
    return Path(installation["comfy_root"]), Path(installation["archive_root"])


def select_comfy_root(
    explicit: Path | None,
    workflow_path: Path | None = None,
    config_path: Path | None = None,
) -> Path | None:
    if explicit:
        return explicit
    if workflow_path:
        inferred = infer_comfy_root(workflow_path)
        if inferred:
            return inferred
    configured, _ = configured_roots(config_path)
    return configured


def _candidate_model_roots(comfy_root: Path, categories: tuple[str, ...]) -> list[Path]:
    models = comfy_root / "models"
    if categories:
        return [models / category for category in categories if (models / category).is_dir()
                and _is_relative_to((models / category).resolve(), models.resolve())]
    return [models] if models.is_dir() else []


def _model_relative_path(filename: str) -> PurePosixPath | None:
    value = filename.replace("\\", "/")
    path = PurePosixPath(value)
    if (not value or path.is_absolute() or ":" in value or "\x00" in value
            or any(part in {"", ".", ".."} for part in value.split("/"))):
        return None
    return path


def resolve_asset(asset: Asset, comfy_root: Path) -> tuple[Path | None, list[Path]]:
    asset.resolution = None
    roots = _candidate_model_roots(comfy_root, asset.search_categories)
    normalized = _model_relative_path(asset.filename)
    if normalized is None:
        asset.resolution = "unsafe-path"
        return None, []
    models_root = (comfy_root / "models").resolve()

    exact = [root.joinpath(*normalized.parts) for root in roots
             if root.joinpath(*normalized.parts).is_file()
             and _is_relative_to(root.joinpath(*normalized.parts).parent.resolve(), models_root)]
    if len(exact) == 1:
        asset.resolution = "exact-path"
        return exact[0], []
    if len(exact) > 1:
        asset.resolution = "ambiguous-path"
        return None, exact

    matches: list[Path] = []
    wanted_name = normalized.name.lower()
    for root in roots:
        try:
            matches.extend(
                path for path in root.rglob("*") if path.is_file() and path.name.lower() == wanted_name
                and _is_relative_to(path.parent.resolve(), models_root)
            )
        except OSError:
            continue
    unique = list(dict.fromkeys(path.parent.resolve() / path.name for path in matches))
    if len(normalized.parts) > 1:
        # With an unknown category, match the complete relative suffix. Never
        # replace an explicit subfolder with a same-named file from elsewhere.
        suffix = tuple(part.casefold() for part in normalized.parts)
        matching_paths = [path for path in unique if not asset.search_categories
                          and tuple(part.casefold() for part in path.relative_to(models_root).parts[-len(suffix):]) == suffix]
        if len(matching_paths) == 1:
            asset.resolution = "relative-suffix"
            return matching_paths[0], []
        asset.resolution = "ambiguous-path" if matching_paths else "path-mismatch" if unique else "not-found"
        return None, matching_paths or unique
    if len(unique) == 1:
        asset.resolution = "basename-fallback"
        return unique[0], []
    asset.resolution = "ambiguous-path" if unique else "not-found"
    return None, unique


def _is_empty_bypassed_lora(node: dict[str, Any]) -> bool:
    """Recognize only explicit, empty standard LoRA slots in UI bypass mode.

    This suppresses a diagnostic, never dependency collection. A selected LoRA
    stays part of the package even if its node is currently bypassed.
    """
    if node.get("type") not in {"LoraLoader", "LoraLoaderModelOnly"} or node.get("mode") != 4:
        return False
    values = node.get("widgets_values")
    if not isinstance(values, list) or not values or values[0] != "":
        return False
    inputs = node.get("inputs", [])
    if not isinstance(inputs, list):
        return False
    for item in inputs:
        if not isinstance(item, dict):
            return False
        widget = item.get("widget")
        is_filename = item.get("name") == "lora_name" or (
            isinstance(widget, dict) and widget.get("name") == "lora_name")
        # Unknown linked inputs may also supply a filename. Remain conservative.
        if item.get("link") is not None and (is_filename or item.get("name") not in {
            "model", "clip", "strength_model", "strength_clip",
        }):
            return False
    return True


def parser_warnings(data: dict[str, Any], assets: list[Asset]) -> list[dict[str, str]]:
    warnings = []
    for asset in assets:
        codes = []
        if asset.detection == "extension-heuristic":
            codes.append("heuristic")
        if asset.resolution in {"basename-fallback", "relative-suffix", "path-mismatch", "ambiguous-path", "unsafe-path"}:
            codes.append(asset.resolution)
        for code in codes:
            warnings.append({"code": code, "node_id": asset.source_node_id, "node_type": asset.source_node_type,
                             "field": asset.source_field, "filename": asset.filename,
                             "resolved_path": asset.resolved_path or ""})
    source = data.get("prompt", data)
    nodes = [(str(node.get("id", "?")), node) for node in data.get("nodes", []) if isinstance(node, dict)] if isinstance(data.get("nodes"), list) else list(source.items())
    detected = {asset.source_node_id for asset in assets}
    for node_id, node in nodes:
        if not isinstance(node, dict):
            continue
        node_type = str(node.get("type", node.get("class_type", "")))
        values = node.get("widgets_values", node.get("inputs", {}))
        if "loader" in node_type.casefold() and node_id not in detected:
            if _is_empty_bypassed_lora(node):
                continue
            if any(is_placeholder_model_filename(value) for _, value in _walk_strings(values)):
                continue
            if not any(_is_model_filename(value) for _, value in _walk_strings(values)):
                warnings.append({"code": "unresolved-loader", "node_id": str(node_id), "node_type": node_type,
                                 "field": "", "filename": "", "resolved_path": ""})
    return warnings


def package_parser_warnings(archive_path: Path, manifest: dict[str, Any]) -> list[dict[str, str]]:
    """Hide obsolete empty-bypass notices in old packages without rewriting ZIPs.

    Read only the small, checksum-checked workflow when such notices exist. Never
    read models or change saved integrity/completeness results during UI refresh.
    If the workflow cannot be checked, retain every original warning.
    """
    warnings = manifest.get("parser_warnings", [])
    candidates = [warning for warning in warnings
                  if warning.get("code") == "unresolved-loader" and not warning.get("filename")
                  and warning.get("node_type") in {"LoraLoader", "LoraLoaderModelOnly"}]
    if not candidates:
        return warnings
    try:
        member = manifest.get("workflow", {}).get("archive_path")
        entry = next((item for item in manifest.get("files", [])
                      if item.get("role") == "workflow" and item.get("path") == member), None)
        if entry is None:
            return warnings
        with zipfile.ZipFile(archive_path) as archive:
            if archive.getinfo(member).file_size > 32 * 1024 * 1024:
                return warnings
            content = archive.read(member)
        if hashlib.sha256(content).hexdigest() != entry.get("sha256"):
            return warnings
        data = json.loads(content)
        if not isinstance(data, dict) or not isinstance(data.get("nodes"), list):
            return warnings
        nodes_by_id: dict[str, list[dict[str, Any]]] = {}
        for node in data["nodes"]:
            if isinstance(node, dict):
                nodes_by_id.setdefault(str(node.get("id", "?")), []).append(node)
        result = []
        for warning in warnings:
            nodes = nodes_by_id.get(str(warning.get("node_id")), [])
            if warning in candidates and nodes and all(
                node.get("type") == warning["node_type"] and _is_empty_bypassed_lora(node) for node in nodes
            ):
                continue
            result.append(warning)
        return result
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, zipfile.BadZipFile):
        return warnings


ProgressCallback = Callable[[dict[str, Any]], None] | None


class OperationCancelled(Exception):
    """Raised by a progress callback when a reversible operation is cancelled."""


class RemovalIncompleteError(ValueError):
    """A verified backup exists, but local removal was blocked or only partial."""

    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        super().__init__(
            f"Model removal stopped: {len(result.get('removed', []))} removed, "
            f"{len(result.get('remaining', []))} not removed by this operation. "
            "Verified archive backups were preserved."
        )


def _emit_progress(
    callback: ProgressCallback,
    *,
    phase: str,
    message: str,
    current: int | None = None,
    total: int | None = None,
    file: str | None = None,
    rate: str | None = None,
    eta: str | None = None,
    level: str = "info",
    log: bool = False,
    cancellable: bool = True,
) -> None:
    if callback:
        payload: dict[str, Any] = {
            "phase": phase,
            "message": message,
            "current": current,
            "total": total,
            "file": file,
            "level": level,
            "log": log,
            "cancellable": cancellable,
        }
        if rate is not None:
            payload["rate"] = rate
        if eta is not None:
            payload["eta"] = eta
        callback(payload)


def sha256_file(
    path: Path,
    chunk_size: int = 8 * 1024 * 1024,
    progress: ProgressCallback = None,
    phase: str = "hash",
    message: str | None = None,
) -> str:
    digest = hashlib.sha256()
    total = path.stat().st_size
    current = 0
    label = message or f"Prüfe {path.name}"
    _emit_progress(
        progress, phase=phase, message=label, current=0, total=total, file=path.name, log=True
    )
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
            current += len(chunk)
            _emit_progress(
                progress,
                phase=phase,
                message=label,
                current=current,
                total=total,
                file=path.name,
            )
    return digest.hexdigest()


def inspect_workflow(
    workflow_path: Path, comfy_root: Path | None, calculate_hashes: bool
) -> dict[str, Any]:
    data = load_workflow(workflow_path)
    fmt = workflow_format(data)
    if fmt == "comfyui-ui-workflow":
        assets, custom_nodes = parse_ui_workflow(data)
    elif fmt in {"comfyui-api-prompt", "comfyui-api-wrapper"}:
        assets, custom_nodes = parse_api_prompt(data)
    else:
        raise ValueError("Unbekanntes Workflow-Format.")

    ambiguous: dict[str, list[str]] = {}
    if comfy_root:
        for asset in assets:
            resolved, alternatives = resolve_asset(asset, comfy_root)
            if resolved:
                asset.resolved_path = str(resolved)
                asset.size = resolved.stat().st_size
                if calculate_hashes:
                    asset.sha256 = sha256_file(resolved)
            elif alternatives:
                ambiguous[asset.filename] = [str(path) for path in alternatives]

    used_packages = {item.package for item in custom_nodes}
    extra_versions = (data.get("extra") or {}).get("node_versions") or {}
    unused_environment = {
        str(name): str(version)
        for name, version in extra_versions.items()
        if name != "comfy-core" and name not in used_packages
    }

    return {
        "schema_version": 1,
        "workflow": workflow_path.stem,
        "workflow_path": str(workflow_path.resolve()),
        "format": fmt,
        "comfy_root": str(comfy_root.resolve()) if comfy_root else None,
        "assets": [asdict(asset) for asset in assets],
        "custom_nodes": enrich_custom_nodes([asdict(node) for node in custom_nodes], comfy_root),
        "unused_environment_metadata": unused_environment,
        "ambiguous_matches": ambiguous,
        "parser_warnings": parser_warnings(data, assets),
    }


def _normalize_git_url(url: str) -> str:
    """Normalize git remote URLs (SSH, HTTPS, .git suffix) into a web URL."""
    clean = url.strip()
    if clean.endswith(".git"):
        clean = clean[:-4]
    ssh_match = re.match(r"^(?:ssh:\/\/)?git@([^:]+):(.+)$", clean)
    if ssh_match:
        host, repo = ssh_match.group(1), ssh_match.group(2).lstrip("/")
        return f"https://{host}/{repo}"
    return clean


def _read_git_remote_url(node_dir: Path) -> str | None:
    """Read the origin (or primary) remote URL from a directory's .git config."""
    git_dir = node_dir / ".git"
    git_config: Path | None = None
    if git_dir.is_dir():
        git_config = git_dir / "config"
    elif git_dir.is_file():
        try:
            content = git_dir.read_text(encoding="utf-8", errors="replace").strip()
            if content.startswith("gitdir:"):
                target = content.split("gitdir:", 1)[1].strip()
                target_path = Path(target)
                if not target_path.is_absolute():
                    target_path = (node_dir / target_path).resolve()
                git_config = target_path / "config"
        except Exception:
            return None

    if not git_config or not git_config.is_file():
        return None

    try:
        lines = git_config.read_text(encoding="utf-8", errors="replace").splitlines()
        in_origin_remote = False
        in_any_remote = False
        fallback_url: str | None = None
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("["):
                in_origin_remote = bool(re.match(r'\[remote\s+"origin"\]', stripped, re.IGNORECASE))
                in_any_remote = bool(re.match(r'\[remote\s+".+"\]', stripped, re.IGNORECASE))
                continue
            if "=" in stripped:
                key, _, val = stripped.partition("=")
                key = key.strip().lower()
                val = val.strip()
                if key == "url":
                    if in_origin_remote:
                        return _normalize_git_url(val)
                    if in_any_remote and fallback_url is None:
                        fallback_url = _normalize_git_url(val)
        return fallback_url
    except Exception:
        return None


def _clean_node_name(name: str) -> str:
    return re.sub(r"[\s\-_\.]+", "", name).casefold()


def _core_node_name(name: str) -> str:
    clean = _clean_node_name(name)
    if clean.startswith("comfyui"):
        clean = clean[7:]
    elif clean.startswith("comfy"):
        clean = clean[5:]
    if clean.endswith("comfyui"):
        clean = clean[:-7]
    elif clean.endswith("comfy"):
        clean = clean[:-5]
    return clean


def resolve_custom_node(package: str, comfy_root: Path) -> Path | None:
    custom_root = comfy_root / "custom_nodes"
    if not custom_root.is_dir():
        return None
    wanted = package.strip().casefold()
    if not wanted:
        return None

    package_short = package.split("/")[-1].split("\\")[-1]
    wanted_short = package_short.strip().casefold()

    try:
        children = [child for child in custom_root.iterdir() if child.is_dir() and not child.name.startswith(".")]
    except OSError:
        return None

    # Pass 1: exact case-insensitive match
    for child in children:
        if child.name.casefold() == wanted or child.name.casefold() == wanted_short:
            return child

    # Pass 2: normalized match (hyphens, underscores, dots, spaces stripped)
    clean_wanted = _clean_node_name(wanted_short)
    if clean_wanted:
        for child in children:
            if _clean_node_name(child.name) == clean_wanted:
                return child

    # Pass 3: core name match (stripping 'comfyui' / 'comfy' prefix and suffix)
    core_wanted = _core_node_name(wanted_short)
    if len(core_wanted) >= 3:
        for child in children:
            if _core_node_name(child.name) == core_wanted:
                return child

    # Pass 4: git remote repository name match
    for child in children:
        remote_url = _read_git_remote_url(child)
        if remote_url:
            repo_part = remote_url.rstrip("/").split("/")[-1]
            if repo_part.casefold() == wanted or repo_part.casefold() == wanted_short:
                return child
            if clean_wanted and _clean_node_name(repo_part) == clean_wanted:
                return child
            if len(core_wanted) >= 3 and _core_node_name(repo_part) == core_wanted:
                return child

    return None


def scan_installed_custom_nodes(comfy_root: Path | None) -> list[dict[str, Any]]:
    """Scan all installed custom node directories in comfy_root."""
    if not comfy_root:
        return []
    custom_root = comfy_root / "custom_nodes"
    if not custom_root.is_dir():
        return []
    results: list[dict[str, Any]] = []
    try:
        children = sorted(
            [child for child in custom_root.iterdir() if child.is_dir() and not child.name.startswith(".")],
            key=lambda p: p.name.casefold(),
        )
    except OSError:
        return []
    for child in children:
        git_url = _read_git_remote_url(child)
        results.append({
            "folder": child.name,
            "path": str(child.resolve()),
            "git_url": git_url,
        })
    return results


def enrich_custom_nodes(
    custom_nodes: list[dict[str, Any]] | list[CustomNode],
    comfy_root: Path | None,
) -> list[dict[str, Any]]:
    """Enrich custom node specifications with live installation status and repository/search links."""
    enriched: list[dict[str, Any]] = []
    for item in custom_nodes:
        if isinstance(item, CustomNode):
            base = asdict(item)
        elif isinstance(item, dict):
            base = dict(item)
        else:
            base = {"package": str(item)}

        pkg_name = str(base.get("package", "")).strip()
        if not pkg_name:
            continue

        installed = False
        installed_folder: str | None = None
        git_url: str | None = None

        if comfy_root:
            found_dir = resolve_custom_node(pkg_name, comfy_root)
            if found_dir:
                installed = True
                installed_folder = found_dir.name
                git_url = _read_git_remote_url(found_dir)

        repository_url: str | None = None
        if git_url:
            repository_url = git_url
        elif pkg_name.startswith("http://") or pkg_name.startswith("https://"):
            repository_url = pkg_name
        elif re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", pkg_name):
            repository_url = f"https://github.com/{pkg_name}"

        search_query = pkg_name
        if "/" in search_query:
            search_query = search_query.rstrip("/").split("/")[-1]
        search_url = f"https://github.com/search?q={urllib.parse.quote(search_query)}&type=repositories"

        base["installed"] = installed
        base["installed_folder"] = installed_folder
        base["git_url"] = git_url
        base["repository_url"] = repository_url
        base["search_url"] = search_url
        enriched.append(base)

    return enriched


def _zip_path(path: Path | str) -> str:
    value = Path(path).as_posix().lstrip("/")
    if not value or value == "." or ".." in Path(value).parts:
        raise ValueError(f"Unsicherer ZIP-Pfad: {path}")
    return value


def _workflow_archive_path(workflow_path: Path, comfy_root: Path) -> str:
    try:
        relative = workflow_path.resolve().relative_to(comfy_root.resolve())
        if relative.parts and relative.parts[0].lower() == "user":
            return _zip_path(relative)
    except ValueError:
        pass
    return _zip_path(Path("user") / "default" / "workflows" / workflow_path.name)


def _add_file_to_zip(
    archive: zipfile.ZipFile,
    source: Path,
    archive_path: str,
    role: str,
    progress: ProgressCallback = None,
) -> dict[str, Any]:
    archive_path = _zip_path(archive_path)
    digest = hashlib.sha256()
    size = source.stat().st_size
    current = 0
    compression = (
        zipfile.ZIP_STORED
        if Path(archive_path).suffix.lower() in MODEL_EXTENSIONS
        else zipfile.ZIP_DEFLATED
    )
    label = f"Schreibe {source.name} ins Paket"
    _emit_progress(
        progress,
        phase="pack",
        message=label,
        current=0,
        total=size,
        file=source.name,
        log=True,
    )
    info = zipfile.ZipInfo.from_file(source, archive_path)
    info.compress_type = compression
    with source.open("rb") as source_handle, archive.open(info, "w", force_zip64=True) as target_handle:
        while chunk := source_handle.read(8 * 1024 * 1024):
            target_handle.write(chunk)
            digest.update(chunk)
            current += len(chunk)
            _emit_progress(
                progress,
                phase="pack",
                message=label,
                current=current,
                total=size,
                file=source.name,
            )
    return {
        "path": archive_path,
        "role": role,
        "size": size,
        "sha256": digest.hexdigest(),
    }


def _add_bytes_to_zip(
    archive: zipfile.ZipFile,
    content: bytes,
    archive_path: str,
    role: str,
    progress: ProgressCallback = None,
) -> dict[str, Any]:
    archive_path = _zip_path(archive_path)
    archive.writestr(archive_path, content, compress_type=zipfile.ZIP_DEFLATED)
    _emit_progress(
        progress,
        phase="pack",
        message=f"Schreibe {archive_path} ins Paket",
        current=len(content),
        total=len(content),
        file=archive_path,
        log=True,
    )
    return {
        "path": archive_path,
        "role": role,
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def custom_nodes_text(custom_nodes: list[dict[str, Any]]) -> str:
    lines = [
        "Verwendete externe ComfyUI Custom Nodes",
        "=======================================",
        "",
        "Diese Pakete werden vom Workflow verwendet, sind aber nicht in dieser ZIP enthalten.",
        "Installiere sie bei Bedarf separat und beachte möglichst die angegebene Revision.",
        "",
    ]
    if not custom_nodes:
        lines.append("Keine externen Custom Nodes erkannt.")
    for dependency in custom_nodes:
        lines.append(f"- {dependency['package']}")
        lines.append(f"  Revision: {dependency.get('revision') or 'nicht bekannt'}")
        lines.append(f"  Beim Packen installiert: {'ja' if dependency.get('installed_when_packed') else 'nein'}")
        node_types = dependency.get("node_types") or []
        if node_types:
            lines.append(f"  Verwendete Nodes: {', '.join(node_types)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_package(
    workflow_path: Path,
    comfy_root: Path,
    output_path: Path,
    allow_missing: bool,
    force: bool,
    progress: ProgressCallback = None,
    shared_pool: bool = False,
) -> dict[str, Any]:
    _emit_progress(
        progress,
        phase="scan",
        message="Workflow-Abhängigkeiten werden analysiert",
        log=True,
    )
    report = inspect_workflow(workflow_path, comfy_root, calculate_hashes=False)
    missing: list[dict[str, str]] = []

    for asset in report["assets"]:
        if not asset["resolved_path"]:
            reason = {"path-mismatch": "Unterpfad stimmt nicht überein", "unsafe-path": "unsicherer Modellpfad"}.get(
                asset.get("resolution"), "mehrdeutig" if asset["filename"] in report["ambiguous_matches"] else "nicht gefunden")
            missing.append(
                {
                    "type": "asset",
                    "kind": asset["kind"],
                    "name": asset["filename"],
                    "reason": reason,
                }
            )

    if missing and not allow_missing:
        details = "\n".join(f"- {item['kind']}: {item['name']} ({item['reason']})" for item in missing)
        raise ValueError(
            "Paket nicht erstellt, weil Abhängigkeiten fehlen:\n"
            f"{details}\nMit --allow-missing kann bewusst ein unvollständiges Paket erstellt werden."
        )

    if output_path.suffix.lower() != ".zip":
        output_path = output_path.with_suffix(".zip")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists() and not force:
        raise ValueError(f"Zieldatei existiert bereits: {output_path} (für Überschreiben --force verwenden)")

    manifest: dict[str, Any] = {
        "schema_version": 2 if shared_pool else 1,
        "package_type": "comfyui-workflow-overlay",
        "package": workflow_path.stem,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "complete": not missing,
        "created_with_allow_missing": bool(allow_missing),
        "parser_warnings": report["parser_warnings"],
        "workflow": {
            "filename": workflow_path.name,
            "format": report["format"],
        },
        "environment": {
            "frontend_version": None,
            "comfy_core_version": None,
        },
        "assets": [],
        "custom_nodes_policy": "record-only",
        "custom_nodes": [],
        "missing": missing,
        "files": [],
    }
    if shared_pool:
        manifest["storage_mode"] = "shared-object-pool"
        manifest["deduplicated_at"] = datetime.now(timezone.utc).isoformat()
    raw_workflow = load_workflow(workflow_path)
    extra = raw_workflow.get("extra") or {}
    manifest["environment"]["frontend_version"] = extra.get("frontendVersion")
    manifest["environment"]["comfy_core_version"] = (extra.get("node_versions") or {}).get("comfy-core")

    nodes_by_package: dict[str, set[str]] = {}
    if isinstance(raw_workflow.get("nodes"), list):
        for node in raw_workflow["nodes"]:
            if not isinstance(node, dict):
                continue
            properties = node.get("properties") or {}
            if not isinstance(properties, dict):
                continue
            package = properties.get("cnr_id")
            if package and package != "comfy-core":
                nodes_by_package.setdefault(str(package), set()).add(str(node.get("type", "unknown")))
    for dependency in report["custom_nodes"]:
        installed_path = resolve_custom_node(dependency["package"], comfy_root)
        manifest["custom_nodes"].append(
            {
                "package": dependency["package"],
                "revision": dependency["revision"],
                "installed_when_packed": installed_path is not None,
                "node_types": sorted(nodes_by_package.get(dependency["package"], set())),
                "packaged": False,
            }
        )

    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{output_path.stem}.", suffix=".tmp", dir=output_path.parent, delete=False
        ) as temporary:
            temporary_name = temporary.name

        archive_paths: set[str] = set()
        with zipfile.ZipFile(temporary_name, "w", allowZip64=True) as archive:
            workflow_arc = _workflow_archive_path(workflow_path, comfy_root)
            manifest["workflow"]["archive_path"] = workflow_arc
            manifest["files"].append(
                _add_file_to_zip(archive, workflow_path, workflow_arc, "workflow", progress)
            )
            archive_paths.add(workflow_arc.casefold())

            if workflow_path.suffix.casefold() == ".png":
                manifest["thumbnail"] = workflow_arc
            else:
                companion = None
                for ext in (".png", ".jpg", ".jpeg", ".webp"):
                    cand = workflow_path.with_suffix(ext)
                    if cand.is_file():
                        companion = cand
                        break
                if not companion:
                    for name in ("cover.png", "thumbnail.png", "cover.jpg", "thumbnail.jpg", "cover.webp", "thumbnail.webp"):
                        cand = workflow_path.parent / name
                        if cand.is_file():
                            companion = cand
                            break
                if companion:
                    thumb_arc = f"thumbnail{companion.suffix.lower()}"
                    manifest["thumbnail"] = thumb_arc
                    manifest["files"].append(
                        _add_file_to_zip(archive, companion, thumb_arc, "thumbnail", progress)
                    )
                    archive_paths.add(thumb_arc.casefold())

            for asset in report["assets"]:
                entry = {
                    "kind": asset["kind"],
                    "filename": asset["filename"],
                    "source_node_id": asset["source_node_id"],
                    "source_node_type": asset["source_node_type"],
                    "source_field": asset["source_field"],
                    "detection": asset["detection"],
                    "resolution": asset["resolution"],
                    "status": "missing",
                    "archive_path": None,
                    "sha256": None,
                    "size": None,
                }
                if asset["resolved_path"]:
                    source = Path(asset["resolved_path"])
                    try:
                        relative = (source.parent.resolve() / source.name).relative_to(comfy_root.resolve())
                    except ValueError as exc:
                        raise ValueError(f"Asset liegt außerhalb des ComfyUI-Roots: {source}") from exc
                    arc = _zip_path(relative)
                    if arc.casefold() not in archive_paths:
                        if shared_pool:
                            object_path, digest, reused = _store_file_as_object(
                                output_path,
                                source,
                                progress,
                            )
                            file_entry = {
                                "path": arc,
                                "role": "model",
                                "size": source.stat().st_size,
                                "sha256": digest,
                                "storage": SHARED_OBJECT_STORAGE,
                                "object_sha256": digest,
                            }
                            del object_path, reused
                        else:
                            file_entry = _add_file_to_zip(archive, source, arc, "model", progress)
                        manifest["files"].append(file_entry)
                        archive_paths.add(arc.casefold())
                    else:
                        file_entry = next(item for item in manifest["files"] if item["path"].casefold() == arc.casefold())
                    entry.update(
                        status="included",
                        archive_path=arc,
                        sha256=file_entry["sha256"],
                        size=file_entry["size"],
                    )
                manifest["assets"].append(entry)

            custom_text = custom_nodes_text(manifest["custom_nodes"]).encode("utf-8")
            manifest["files"].append(
                _add_bytes_to_zip(
                    archive,
                    custom_text,
                    "custom-nodes.txt",
                    "custom_node_inventory",
                    progress,
                )
            )

            archive.writestr(
                "comfy-package.json",
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                compress_type=zipfile.ZIP_DEFLATED,
            )

        os.replace(temporary_name, output_path)
        temporary_name = None
    finally:
        if temporary_name:
            try:
                Path(temporary_name).unlink(missing_ok=True)
            except OSError:
                pass

    manifest["output_path"] = str(output_path.resolve())
    manifest["archive_size"] = output_path.stat().st_size
    _emit_progress(
        progress,
        phase="complete",
        message="Workflow-Paket wurde erfolgreich erstellt",
        current=1,
        total=1,
        log=True,
    )
    return manifest


@dataclass(frozen=True)
class ImportMember:
    path: str
    info: zipfile.ZipInfo


def _safe_import_members(archive: zipfile.ZipFile) -> dict[str, ImportMember]:
    validated: list[tuple[str, zipfile.ZipInfo]] = []
    for info in archive.infolist():
        raw = info.filename.replace("\\", "/")
        pure = PurePosixPath(raw)
        if (
            pure.is_absolute()
            or not pure.parts
            or any(part in {"", ".", ".."} for part in pure.parts)
            or ":" in pure.parts[0]
        ):
            raise ValueError(f"ZIP enthält einen unsicheren Pfad: {info.filename}")
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            raise ValueError(f"ZIP enthält einen nicht erlaubten symbolischen Link: {info.filename}")
        if info.flag_bits & 0x1:
            raise ValueError(f"Verschlüsselte ZIP-Einträge werden nicht unterstützt: {info.filename}")
        if info.is_dir():
            continue
        validated.append((pure.as_posix(), info))

    manifest_paths = [path for path, _ in validated if PurePosixPath(path).name.casefold() == "comfy-package.json"]
    wrapper = ""
    if len(manifest_paths) == 1:
        parent = PurePosixPath(manifest_paths[0]).parent.as_posix()
        if parent != "." and all(path.casefold().startswith(f"{parent}/".casefold()) for path, _ in validated):
            wrapper = f"{parent}/"

    members: dict[str, ImportMember] = {}
    for original, info in validated:
        normalized = original[len(wrapper):] if wrapper else original
        if normalized.casefold().startswith("models/models/"):
            normalized = normalized[len("models/"):]
        key = normalized.casefold()
        if key in members:
            raise ValueError(f"ZIP enthält einen doppelten Pfad: {normalized}")
        members[key] = ImportMember(normalized, info)
    return members


def _workflow_data_from_import(
    archive: zipfile.ZipFile,
    members: dict[str, ImportMember],
) -> tuple[ImportMember, dict[str, Any], dict[str, Any] | None]:
    old_manifest: dict[str, Any] | None = None
    manifest_info = members.get("comfy-package.json")
    if manifest_info:
        try:
            candidate = json.loads(archive.read(manifest_info.info).decode("utf-8-sig"))
            if isinstance(candidate, dict):
                old_manifest = candidate
        except (UnicodeDecodeError, json.JSONDecodeError, zipfile.BadZipFile):
            old_manifest = None

    preferred = None
    if old_manifest:
        declared = old_manifest.get("workflow", {}).get("archive_path")
        if isinstance(declared, str):
            preferred = members.get(declared.replace("\\", "/").casefold())

    candidates = [
        member
        for key, member in members.items()
        if (key.endswith(".json") or key.endswith(".png"))
        and key != "comfy-package.json"
        and key.startswith("user/")
        and "/workflows/" in key
    ]
    if preferred is not None and preferred not in candidates:
        preferred = None
    if preferred is None:
        if len(candidates) != 1:
            raise ValueError(
                "Im Paket muss genau ein ComfyUI-Workflow unter user/.../workflows liegen."
            )
        preferred = candidates[0]
    if preferred.info.file_size > 64 * 1024 * 1024:
        raise ValueError("Die Workflow-Datei ist ungewöhnlich groß und wird nicht importiert.")
    if preferred.path.casefold().endswith(".png"):
        data = load_png_workflow_from_bytes(archive.read(preferred.info))
    else:
        try:
            data = json.loads(archive.read(preferred.info).decode("utf-8-sig"))
        except UnicodeDecodeError as exc:
            raise ValueError("Die Workflow-JSON ist nicht als UTF-8 lesbar.") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"Ungültige Workflow-JSON im Paket ({exc})") from exc
    if not isinstance(data, dict):
        raise ValueError("Die oberste Workflow-Struktur muss ein Objekt sein.")
    return preferred, data, old_manifest


def _match_import_asset(
    asset: Asset,
    model_members: list[ImportMember],
) -> tuple[ImportMember | None, list[str]]:
    asset.resolution = None
    normalized = _model_relative_path(asset.filename)
    if normalized is None:
        asset.resolution = "unsafe-path"
        return None, []
    expected = {
        PurePosixPath("models", category, *normalized.parts).as_posix().casefold()
        for category in asset.search_categories
    }
    exact = [member for member in model_members if member.path.casefold() in expected]
    if len(exact) == 1:
        asset.resolution = "exact-path"
        return exact[0], []
    if len(exact) > 1:
        asset.resolution = "ambiguous-path"
        return None, [member.path for member in exact]

    wanted_name = normalized.name.casefold()
    fallback = []
    for member in model_members:
        pure = PurePosixPath(member.path)
        if pure.name.casefold() != wanted_name:
            continue
        if asset.search_categories and (
            len(pure.parts) < 2 or pure.parts[1].casefold() not in {c.casefold() for c in asset.search_categories}
        ):
            continue
        fallback.append(member)
    if len(normalized.parts) > 1:
        suffix = tuple(part.casefold() for part in normalized.parts)
        matching_paths = [member for member in fallback if not asset.search_categories
                          and tuple(part.casefold() for part in PurePosixPath(member.path).parts[1:][-len(suffix):]) == suffix]
        if len(matching_paths) == 1:
            asset.resolution = "relative-suffix"
            return matching_paths[0], []
        asset.resolution = "ambiguous-path" if matching_paths else "path-mismatch" if fallback else "not-found"
        return None, [member.path for member in matching_paths or fallback]
    if len(fallback) == 1:
        asset.resolution = "basename-fallback"
        return fallback[0], []
    asset.resolution = "ambiguous-path" if fallback else "not-found"
    return None, [member.path for member in fallback]


def inspect_import_package(
    source_path: Path,
    archive_root: Path,
    comfy_root: Path | None = None,
) -> dict[str, Any]:
    if not source_path.is_file():
        raise ValueError(f"ZIP-Paket nicht gefunden: {source_path}")
    try:
        with zipfile.ZipFile(source_path, "r") as archive:
            members = _safe_import_members(archive)
            workflow_info, workflow_data, old_manifest = _workflow_data_from_import(archive, members)
            fmt = workflow_format(workflow_data)
            if fmt == "comfyui-ui-workflow":
                assets, custom_nodes = parse_ui_workflow(workflow_data)
            elif fmt in {"comfyui-api-prompt", "comfyui-api-wrapper"}:
                assets, custom_nodes = parse_api_prompt(workflow_data)
            else:
                raise ValueError("Unbekanntes Workflow-Format im ZIP-Paket.")

            model_members = [
                member
                for key, member in members.items()
                if key.startswith("models/") and PurePosixPath(key).suffix.lower() in MODEL_EXTENSIONS
            ]
            matched_paths: set[str] = set()
            asset_rows: list[dict[str, Any]] = []
            missing: list[dict[str, str]] = []
            for asset in assets:
                matched, ambiguous = _match_import_asset(asset, model_members)
                if matched:
                    archive_path = matched.path
                    matched_paths.add(archive_path.casefold())
                    status = "included"
                    reason = None
                    size = matched.info.file_size
                else:
                    archive_path = None
                    status = "missing"
                    reason = {"path-mismatch": "Unterpfad stimmt nicht überein", "unsafe-path": "unsicherer Modellpfad"}.get(
                        asset.resolution, "mehrdeutig" if ambiguous else "nicht gefunden")
                    size = None
                    missing.append(
                        {"type": "asset", "kind": asset.kind, "name": asset.filename, "reason": reason}
                    )
                asset_rows.append(
                    {
                        **asdict(asset),
                        "status": status,
                        "archive_path": archive_path,
                        "size": size,
                        "alternatives": ambiguous,
                    }
                )

            extra_models = [
                {"path": member.path, "size": member.info.file_size}
                for member in model_members
                if member.path.casefold() not in matched_paths
            ]
            target = archive_root / source_path.name
            reseal_bytes = workflow_info.info.file_size + sum(
                int(asset.get("size") or 0) for asset in asset_rows if asset["status"] == "included"
            )
            archive_usage_path = archive_root if archive_root.exists() else archive_root.parent
            archive_free = shutil.disk_usage(archive_usage_path).free
            direct_load: dict[str, Any] | None = None
            if comfy_root:
                required = 0
                present_models = 0
                conflicts: list[dict[str, str]] = []
                hash_checks = 0
                for asset in asset_rows:
                    if asset["status"] != "included" or not asset["archive_path"]:
                        continue
                    try:
                        local_target = _safe_model_target(comfy_root, str(asset["archive_path"]))
                    except ValueError as exc:
                        conflicts.append({"path": str(asset["archive_path"]), "reason": str(exc)})
                        continue
                    if not local_target.exists():
                        required += int(asset.get("size") or 0)
                    elif not local_target.is_file():
                        conflicts.append({"path": str(local_target), "reason": "Ziel ist keine reguläre Datei"})
                    elif local_target.stat().st_size != int(asset.get("size") or 0):
                        conflicts.append({"path": str(local_target), "reason": "Lokale Datei hat eine andere Größe"})
                    else:
                        present_models += 1
                        hash_checks += 1
                comfy_free = shutil.disk_usage(comfy_root).free
                blockers = []
                if conflicts:
                    blockers.append("Mindestens ein lokaler Modellpfad steht im Konflikt mit dem Paket.")
                if required > comfy_free:
                    blockers.append(
                        f"Nicht genügend freier Speicher: benötigt {human_size(required)}, frei {human_size(comfy_free)}."
                    )
                direct_load = {
                    "bytes_required": required,
                    "bytes_free": comfy_free,
                    "present_models": present_models,
                    "hash_checks_required": hash_checks,
                    "conflicts": conflicts,
                    "blockers": blockers,
                }
            return {
                "source_path": str(source_path.resolve()),
                "target_path": str(target.resolve()),
                "target_exists": target.exists(),
                "source_is_target": source_path.resolve() == target.resolve(),
                "package": PurePosixPath(workflow_info.path).stem,
                "workflow_path": workflow_info.path,
                "workflow_format": fmt,
                "archive_size": source_path.stat().st_size,
                "assets": asset_rows,
                "matched_count": len(asset_rows) - len(missing),
                "missing": missing,
                "extra_models": extra_models,
                "custom_nodes": [asdict(item) for item in custom_nodes],
                "had_manifest": old_manifest is not None,
                "parser_warnings": parser_warnings(workflow_data, assets),
                "old_manifest_complete": bool(old_manifest and old_manifest.get("complete")),
                "reseal_bytes_required": reseal_bytes,
                "archive_bytes_free": archive_free,
                "archive_space_sufficient": reseal_bytes <= archive_free,
                "direct_load": direct_load,
            }
    except zipfile.BadZipFile as exc:
        raise ValueError(f"Ungültige oder beschädigte ZIP-Datei: {source_path}") from exc


def _copy_import_member(
    source_archive: zipfile.ZipFile,
    source_member: ImportMember,
    target_archive: zipfile.ZipFile,
    archive_path: str,
    role: str,
    progress: ProgressCallback,
) -> dict[str, Any]:
    archive_path = _zip_path(archive_path)
    source_info = source_member.info
    total = source_info.file_size
    current = 0
    digest = hashlib.sha256()
    label = f"Übernehme {PurePosixPath(archive_path).name}"
    _emit_progress(
        progress, phase="import", message=label, current=0, total=total,
        file=PurePosixPath(archive_path).name, log=True,
    )
    target_info = zipfile.ZipInfo(archive_path, date_time=source_info.date_time)
    target_info.compress_type = (
        zipfile.ZIP_STORED
        if PurePosixPath(archive_path).suffix.lower() in MODEL_EXTENSIONS
        else zipfile.ZIP_DEFLATED
    )
    with source_archive.open(source_info, "r") as source, target_archive.open(
        target_info, "w", force_zip64=True
    ) as target:
        while chunk := source.read(8 * 1024 * 1024):
            target.write(chunk)
            digest.update(chunk)
            current += len(chunk)
            _emit_progress(
                progress, phase="import", message=label, current=current, total=total,
                file=PurePosixPath(archive_path).name,
            )
    return {"path": archive_path, "role": role, "size": total, "sha256": digest.hexdigest()}


def import_package(
    source_path: Path,
    comfy_root: Path,
    archive_root: Path,
    allow_missing: bool,
    force: bool,
    load_after_import: bool = False,
    progress: ProgressCallback = None,
    link_mode: str = "auto",
) -> dict[str, Any]:
    _emit_progress(progress, phase="scan", message="Importpaket wird neu analysiert", log=True)
    report = inspect_import_package(source_path, archive_root, comfy_root)
    if report["missing"] and not allow_missing:
        details = "\n".join(
            f"- {item['kind']}: {item['name']} ({item['reason']})" for item in report["missing"]
        )
        raise ValueError(f"Import abgebrochen, weil Workflow-Abhängigkeiten fehlen:\n{details}")
    target_path = Path(report["target_path"])
    archive_root.mkdir(parents=True, exist_ok=True)
    if target_path.exists() and not force:
        raise ValueError(f"Zieldatei existiert bereits: {target_path}")
    if not report["archive_space_sufficient"]:
        raise ValueError(
            "Nicht genügend temporärer Speicher im Zwischenlager: "
            f"benötigt etwa {human_size(report['reseal_bytes_required'])}, "
            f"frei {human_size(report['archive_bytes_free'])}."
        )
    if load_after_import and report["direct_load"] and report["direct_load"]["blockers"]:
        details = "\n".join(f"- {item}" for item in report["direct_load"]["blockers"])
        raise ValueError(f"Direktes Laden ist derzeit blockiert:\n{details}")

    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{target_path.stem}.import.", suffix=".tmp", dir=archive_root, delete=False
        ) as temporary:
            temporary_name = temporary.name

        with zipfile.ZipFile(source_path, "r") as source_archive:
            members = _safe_import_members(source_archive)
            workflow_info, workflow_data, _ = _workflow_data_from_import(source_archive, members)
            fmt = workflow_format(workflow_data)
            if fmt == "comfyui-ui-workflow":
                parsed_assets, parsed_custom_nodes = parse_ui_workflow(workflow_data)
            else:
                parsed_assets, parsed_custom_nodes = parse_api_prompt(workflow_data)
            model_members = [
                member for key, member in members.items()
                if key.startswith("models/") and PurePosixPath(key).suffix.lower() in MODEL_EXTENSIONS
            ]

            nodes_by_package: dict[str, set[str]] = {}
            if isinstance(workflow_data.get("nodes"), list):
                for node in workflow_data["nodes"]:
                    if not isinstance(node, dict):
                        continue
                    properties = node.get("properties") or {}
                    package = properties.get("cnr_id") if isinstance(properties, dict) else None
                    if package and package != "comfy-core":
                        nodes_by_package.setdefault(str(package), set()).add(str(node.get("type", "unknown")))

            missing = report["missing"]
            extra = workflow_data.get("extra") or {}
            manifest: dict[str, Any] = {
                "schema_version": 1,
                "package_type": "comfyui-workflow-overlay",
                "package": report["package"],
                "created_at": datetime.now(timezone.utc).isoformat(),
                "complete": not missing,
                "created_with_allow_missing": bool(allow_missing),
                "imported_and_resealed": True,
                "parser_warnings": report["parser_warnings"],
                "workflow": {
                    "filename": PurePosixPath(workflow_info.path).name,
                    "format": fmt,
                    "archive_path": workflow_info.path,
                },
                "environment": {
                    "frontend_version": extra.get("frontendVersion"),
                    "comfy_core_version": (extra.get("node_versions") or {}).get("comfy-core"),
                },
                "assets": [],
                "custom_nodes_policy": "record-only",
                "custom_nodes": [],
                "missing": missing,
                "files": [],
            }
            for dependency in parsed_custom_nodes:
                manifest["custom_nodes"].append(
                    {
                        "package": dependency.package,
                        "revision": dependency.revision,
                        "installed_when_packed": resolve_custom_node(dependency.package, comfy_root) is not None,
                        "node_types": sorted(nodes_by_package.get(dependency.package, set())),
                        "packaged": False,
                    }
                )

            copied: dict[str, dict[str, Any]] = {}
            with zipfile.ZipFile(temporary_name, "w", allowZip64=True) as target_archive:
                workflow_arc = workflow_info.path
                workflow_entry = _copy_import_member(
                    source_archive, workflow_info, target_archive, workflow_arc, "workflow", progress
                )
                manifest["files"].append(workflow_entry)
                for asset in parsed_assets:
                    matched, _ = _match_import_asset(asset, model_members)
                    asset_entry = {
                        "kind": asset.kind,
                        "filename": asset.filename,
                        "source_node_id": asset.source_node_id,
                        "source_node_type": asset.source_node_type,
                        "source_field": asset.source_field,
                        "detection": asset.detection,
                        "resolution": asset.resolution,
                        "status": "missing",
                        "archive_path": None,
                        "sha256": None,
                        "size": None,
                    }
                    if matched:
                        arc = matched.path
                        if arc.casefold() not in copied:
                            copied[arc.casefold()] = _copy_import_member(
                                source_archive, matched, target_archive, arc, "model", progress
                            )
                            manifest["files"].append(copied[arc.casefold()])
                        file_entry = copied[arc.casefold()]
                        asset_entry.update(
                            status="included", archive_path=arc,
                            sha256=file_entry["sha256"], size=file_entry["size"],
                        )
                    manifest["assets"].append(asset_entry)

                custom_text = custom_nodes_text(manifest["custom_nodes"]).encode("utf-8")
                manifest["files"].append(
                    _add_bytes_to_zip(
                        target_archive, custom_text, "custom-nodes.txt", "custom_node_inventory", progress
                    )
                )
                target_archive.writestr(
                    "comfy-package.json",
                    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                    compress_type=zipfile.ZIP_DEFLATED,
                )

        verification = verify_package(Path(temporary_name), progress)
        if not verification["valid"]:
            raise ValueError("Das neu versiegelte Paket hat die Integritätsprüfung nicht bestanden.")
        restore_preview = None
        if load_after_import:
            restore_preview = restore_package(
                Path(temporary_name),
                comfy_root,
                False,
                allow_missing,
                progress,
                archive_preverified=True,
                link_mode=link_mode,
            )
            if restore_preview["blockers"]:
                details = "\n".join(f"- {item}" for item in restore_preview["blockers"])
                raise ValueError(f"Paket ist gültig, kann aber nicht direkt geladen werden:\n{details}")
        os.replace(temporary_name, target_path)
        temporary_name = None
    finally:
        if temporary_name:
            try:
                Path(temporary_name).unlink(missing_ok=True)
            except OSError:
                pass

    restore_result = None
    if load_after_import:
        restore_result = restore_package(
            target_path,
            comfy_root,
            True,
            allow_missing,
            progress,
            archive_preverified=True,
            link_mode=link_mode,
        )

    _emit_progress(
        progress, phase="complete", message="Paket wurde geprüft und importiert",
        current=1, total=1, level="success", log=True,
    )
    return {
        "output_path": str(target_path.resolve()),
        "package": report["package"],
        "complete": not report["missing"],
        "missing": report["missing"],
        "excluded_models": report["extra_models"],
        "verification": verification,
        "loaded": load_after_import,
        "restore": restore_result,
    }


SHARED_OBJECT_STORAGE = "shared-object"


def _shared_object_path(archive_path: Path, digest: str) -> Path:
    normalized = str(digest).casefold()
    if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
        raise ValueError(f"Ungültiger SHA-256-Objektschlüssel: {digest}")
    return archive_path.parent / ".comfyshelf" / "objects" / "sha256" / normalized[:2] / normalized


def _copy_stream(
    source: Any,
    target: Any,
    *,
    expected_hash: str,
    expected_size: int,
    progress: ProgressCallback = None,
    phase: str,
    label: str,
) -> tuple[str, int]:
    digest = hashlib.sha256()
    written = 0
    _emit_progress(progress, phase=phase, message=label, current=0, total=expected_size, log=True)
    while chunk := source.read(8 * 1024 * 1024):
        target.write(chunk)
        digest.update(chunk)
        written += len(chunk)
        _emit_progress(progress, phase=phase, message=label, current=written, total=expected_size)
    actual_hash = digest.hexdigest()
    if expected_hash and actual_hash != expected_hash:
        raise ValueError(f"SHA-256-Prüfung fehlgeschlagen: {label}")
    if expected_size >= 0 and written != expected_size:
        raise ValueError(f"Dateigröße stimmt nicht: {label}")
    return actual_hash, written


def _store_zip_member_as_object(
    archive_path: Path,
    archive: zipfile.ZipFile,
    entry: dict[str, Any],
    progress: ProgressCallback = None,
) -> tuple[Path, bool]:
    digest = str(entry.get("sha256", "")).casefold()
    size = int(entry.get("size", -1))
    member = str(entry.get("path", ""))
    target = _shared_object_path(archive_path, digest)
    if target.is_file():
        if target.stat().st_size != size or sha256_file(target) != digest:
            raise ValueError(f"Der gemeinsame Objektpool enthält eine beschädigte Datei: {target}")
        return target, True

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{digest}.", suffix=".object-tmp", dir=target.parent, delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
            with archive.open(member, "r") as source:
                _copy_stream(
                    source,
                    temporary,
                    expected_hash=digest,
                    expected_size=size,
                    progress=progress,
                    phase="deduplicate",
                    label=f"Speichere {Path(member).name} im gemeinsamen Pool",
                )
            temporary.flush()
            os.fsync(temporary.fileno())
        if target.exists():
            if target.is_file() and target.stat().st_size == size and sha256_file(target) == digest:
                temporary_path.unlink(missing_ok=True)
                temporary_path = None
                return target, True
            raise ValueError(f"Objektziel erschien während des Speicherns: {target}")
        os.replace(temporary_path, target)
        temporary_path = None
        return target, False
    finally:
        if temporary_path:
            temporary_path.unlink(missing_ok=True)


def _store_file_as_object(
    archive_path: Path,
    source_path: Path,
    progress: ProgressCallback = None,
) -> tuple[Path, str, bool]:
    size = source_path.stat().st_size
    temporary_root = archive_path.parent / ".comfyshelf" / "objects" / ".tmp"
    temporary_root.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=".incoming.", suffix=".object-tmp", dir=temporary_root, delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
            with source_path.open("rb") as source:
                digest, _ = _copy_stream(
                    source,
                    temporary,
                    expected_hash="",
                    expected_size=size,
                    progress=progress,
                    phase="deduplicate",
                    label=f"Hashing and storing {source_path.name}",
                )
            temporary.flush()
            os.fsync(temporary.fileno())
        target = _shared_object_path(archive_path, digest)
        if target.is_file():
            if target.stat().st_size != size or sha256_file(target) != digest:
                raise ValueError(f"Der gemeinsame Objektpool enthält eine beschädigte Datei: {target}")
            temporary_path.unlink(missing_ok=True)
            temporary_path = None
            return target, digest, True
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if target.is_file() and target.stat().st_size == size and sha256_file(target) == digest:
                temporary_path.unlink(missing_ok=True)
                temporary_path = None
                return target, digest, True
            raise ValueError(f"Objektziel erschien während des Speicherns: {target}")
        os.replace(temporary_path, target)
        temporary_path = None
        return target, digest, False
    finally:
        if temporary_path:
            temporary_path.unlink(missing_ok=True)


def _copy_zip_member(
    source_archive: zipfile.ZipFile,
    source_info: zipfile.ZipInfo,
    target_archive: zipfile.ZipFile,
    progress: ProgressCallback = None,
) -> None:
    target_info = zipfile.ZipInfo(source_info.filename, date_time=source_info.date_time)
    target_info.compress_type = source_info.compress_type
    target_info.external_attr = source_info.external_attr
    target_info.comment = source_info.comment
    with source_archive.open(source_info, "r") as source, target_archive.open(target_info, "w", force_zip64=True) as target:
        current = 0
        label = f"Writing {Path(source_info.filename).name} into package"
        _emit_progress(progress, phase="pack", message=label, current=0,
                       total=source_info.file_size, file=Path(source_info.filename).name, log=True)
        while chunk := source.read(8 * 1024 * 1024):
            target.write(chunk)
            current += len(chunk)
            _emit_progress(progress, phase="pack", message=label, current=current,
                           total=source_info.file_size, file=Path(source_info.filename).name)


def deduplicate_package(
    archive_path: Path,
    progress: ProgressCallback = None,
) -> dict[str, Any]:
    """Move embedded model payloads into the archive root's shared SHA-256 pool."""
    verification = verify_package(archive_path, progress)
    if not verification["valid"]:
        details = "\n".join(f"- {error}" for error in verification["errors"])
        raise ValueError(f"Paket kann nicht optimiert werden, weil die Integrität fehlerhaft ist:\n{details}")
    manifest = load_package_manifest(archive_path)
    embedded_models = [
        entry for entry in manifest.get("files", [])
        if entry.get("role") == "model" and entry.get("storage") != SHARED_OBJECT_STORAGE
    ]
    if not embedded_models:
        return {
            "archive": str(archive_path.resolve()),
            "package": manifest.get("package"),
            "changed": False,
            "model_bytes": sum(int(entry.get("size", 0)) for entry in manifest.get("files", []) if entry.get("role") == "model"),
            "new_object_bytes": 0,
            "reused_object_bytes": 0,
        }

    temporary_name: str | None = None
    new_object_bytes = 0
    reused_object_bytes = 0
    excluded_paths = {str(entry.get("path", "")).casefold() for entry in embedded_models}
    try:
        with zipfile.ZipFile(archive_path, "r") as source_archive:
            for entry in embedded_models:
                _, reused = _store_zip_member_as_object(archive_path, source_archive, entry, progress)
                if reused:
                    reused_object_bytes += int(entry.get("size", 0))
                else:
                    new_object_bytes += int(entry.get("size", 0))
                entry["storage"] = SHARED_OBJECT_STORAGE
                entry["object_sha256"] = entry.get("sha256")

            manifest["schema_version"] = max(2, int(manifest.get("schema_version", 1)))
            manifest["storage_mode"] = "shared-object-pool"
            manifest["deduplicated_at"] = datetime.now(timezone.utc).isoformat()
            with tempfile.NamedTemporaryFile(
                prefix=f".{archive_path.stem}.dedupe.", suffix=".tmp", dir=archive_path.parent, delete=False
            ) as temporary:
                temporary_name = temporary.name
            with zipfile.ZipFile(temporary_name, "w", allowZip64=True) as target_archive:
                for info in source_archive.infolist():
                    if info.filename.casefold() == "comfy-package.json" or info.filename.casefold() in excluded_paths:
                        continue
                    _copy_zip_member(source_archive, info, target_archive)
                target_archive.writestr(
                    "comfy-package.json",
                    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                    compress_type=zipfile.ZIP_DEFLATED,
                )
        os.replace(temporary_name, archive_path)
        temporary_name = None
    finally:
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)

    _emit_progress(progress, phase="complete", message="Paket nutzt jetzt den gemeinsamen Modellpool", current=1, total=1, level="success", log=True)
    return {
        "archive": str(archive_path.resolve()),
        "package": manifest.get("package"),
        "changed": True,
        "model_bytes": sum(int(entry.get("size", 0)) for entry in embedded_models),
        "new_object_bytes": new_object_bytes,
        "reused_object_bytes": reused_object_bytes,
        "archive_size": archive_path.stat().st_size,
    }


def inspect_library_optimization(archive_root: Path) -> dict[str, Any]:
    """Describe portable packages that can be converted to the shared pool."""
    packages: list[dict[str, Any]] = []
    warnings: list[dict[str, str]] = []
    already_shared = 0
    for archive_path in sorted(archive_root.glob("*.zip"), key=lambda path: path.name.casefold()):
        try:
            manifest = load_package_manifest(archive_path)
        except (OSError, ValueError) as exc:
            warnings.append({"name": archive_path.name, "error": str(exc)})
            continue
        embedded_models = [
            entry for entry in manifest.get("files", [])
            if entry.get("role") == "model" and entry.get("storage") != SHARED_OBJECT_STORAGE
        ]
        if not embedded_models:
            if manifest.get("storage_mode") == "shared-object-pool":
                already_shared += 1
            continue
        reusable_bytes = 0
        for entry in embedded_models:
            try:
                object_path = _shared_object_path(archive_path, str(entry.get("sha256", "")))
                if object_path.is_file() and object_path.stat().st_size == int(entry.get("size", -1)):
                    reusable_bytes += int(entry.get("size", 0))
            except (OSError, ValueError):
                pass
        packages.append({
            "name": archive_path.stem,
            "path": str(archive_path.resolve()),
            "model_count": len(embedded_models),
            "model_bytes": sum(int(entry.get("size", 0)) for entry in embedded_models),
            "reusable_bytes": reusable_bytes,
            "complete": bool(manifest.get("complete")),
        })
    return {
        "archive_root": str(archive_root.resolve()),
        "packages": packages,
        "package_count": len(packages),
        "already_shared": already_shared,
        "model_count": sum(item["model_count"] for item in packages),
        "model_bytes": sum(item["model_bytes"] for item in packages),
        "reusable_bytes": sum(item["reusable_bytes"] for item in packages),
        "warnings": warnings,
    }


def optimize_library(
    archive_root: Path,
    progress: ProgressCallback = None,
) -> dict[str, Any]:
    report = inspect_library_optimization(archive_root)
    optimized: list[dict[str, Any]] = []
    for index, package in enumerate(report["packages"], 1):
        _emit_progress(
            progress,
            phase="library-optimize",
            message=f"Optimiere {package['name']} ({index}/{report['package_count']})",
            current=index - 1,
            total=report["package_count"],
            file=Path(package["path"]).name,
            log=True,
        )
        optimized.append(deduplicate_package(Path(package["path"]), progress))
    return {
        **report,
        "optimized": optimized,
        "optimized_count": len(optimized),
        "new_object_bytes": sum(int(item.get("new_object_bytes", 0)) for item in optimized),
        "reused_object_bytes": sum(int(item.get("reused_object_bytes", 0)) for item in optimized),
    }


def preview_portable_export(archive_path: Path, output_path: Path) -> dict[str, Any]:
    """Read-only destination/space preview; integrity is checked during export."""
    source = archive_path.expanduser().resolve()
    output = output_path.expanduser()
    if output.suffix.casefold() != ".zip":
        output = output.with_suffix(".zip")
    output = output.resolve()
    manifest = load_package_manifest(source)
    blockers: list[dict[str, str]] = []
    if output == source:
        blockers.append({"code": "source_target"})
    elif output.exists():
        blockers.append({"code": "exists"})
    pool = (source.parent / ".comfyshelf").resolve()
    if _is_relative_to(output, pool) or ".comfyshelf" in [part.casefold() for part in output.parts]:
        blockers.append({"code": "pool_target"})
    payload_bytes = 0
    model_bytes = 0
    with zipfile.ZipFile(source, "r") as archive:
        for entry in manifest.get("files", []):
            member = _zip_path(str(entry.get("path", "")))
            if entry.get("storage") == SHARED_OBJECT_STORAGE:
                obj = _shared_object_path(source, str(entry.get("object_sha256") or entry.get("sha256")))
                if not obj.is_file():
                    blockers.append({"code": "missing_file", "path": member})
                    continue
                size = obj.stat().st_size
            else:
                try:
                    size = archive.getinfo(member).file_size
                except KeyError:
                    blockers.append({"code": "missing_file", "path": member})
                    continue
            payload_bytes += size
            if entry.get("role") == "model":
                model_bytes += size
    # Conservative allowance for ZIP headers, manifest and compression overhead.
    required = payload_bytes + payload_bytes // 100 + 1024 * 1024
    required += len(json.dumps(manifest, ensure_ascii=False).encode("utf-8")) + 8192 * len(manifest.get("files", []))
    ancestor = output.parent
    while not ancestor.exists() and ancestor != ancestor.parent:
        ancestor = ancestor.parent
    free = None
    if not ancestor.is_dir():
        blockers.append({"code": "invalid_folder"})
    else:
        try:
            free = shutil.disk_usage(ancestor).free
            if free < required:
                blockers.append({"code": "space"})
            if not os.access(ancestor, os.W_OK):
                blockers.append({"code": "not_writable"})
            if _filesystem_type(ancestor) == "FAT32" and required >= 4 * 1024**3:
                blockers.append({"code": "fat32"})
        except OSError:
            blockers.append({"code": "unavailable"})
    return {
        "package": source.stem, "output_path": str(output),
        "model_bytes": model_bytes, "bytes_required": required, "bytes_free": free,
        "complete": bool(manifest.get("complete")) and not manifest.get("missing"),
        "missing": manifest.get("missing", []), "blockers": blockers,
    }


def export_portable_package(
    archive_path: Path,
    output_path: Path,
    force: bool = False,
    progress: ProgressCallback = None,
    allow_incomplete: bool = False,
) -> dict[str, Any]:
    """Export embedded or pool-backed files, verifying before publishing the ZIP."""
    report = preview_portable_export(archive_path, output_path)
    blockers = [item for item in report["blockers"] if not (force and item["code"] == "exists")]
    if any(item["code"] == "exists" for item in blockers):
        raise FileExistsError(f"Export destination already exists: {report['output_path']}")
    if blockers:
        raise ValueError("Portable ZIP export blocked: " + ", ".join(item["code"] for item in blockers))
    if not report["complete"] and not allow_incomplete:
        raise ValueError("Incomplete package: explicitly accept missing dependencies before exporting.")
    output_path = Path(report["output_path"])
    verification = verify_package(archive_path, progress)
    if not verification["valid"]:
        raise ValueError("The library package failed integrity verification.")
    if output_path.suffix.casefold() != ".zip":
        output_path = output_path.with_suffix(".zip")
    if output_path.resolve() == archive_path.resolve():
        raise ValueError("Der portable Export darf das Bibliothekspaket nicht überschreiben.")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists() and not force:
        raise FileExistsError(f"Zieldatei existiert bereits: {output_path}")

    manifest = load_package_manifest(archive_path)
    portable_manifest = json.loads(json.dumps(manifest))
    portable_manifest["storage_mode"] = "embedded-portable"
    portable_manifest["exported_at"] = datetime.now(timezone.utc).isoformat()
    portable_manifest.pop("deduplicated_at", None)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix=f".{output_path.stem}.", suffix=".tmp", dir=output_path.parent, delete=False) as temporary:
            temporary_name = temporary.name
        with zipfile.ZipFile(archive_path, "r") as source_archive, zipfile.ZipFile(temporary_name, "w", allowZip64=True) as target_archive:
            source_names = set(source_archive.namelist())
            for entry in portable_manifest.get("files", []):
                path = str(entry.get("path", ""))
                if entry.get("storage") == SHARED_OBJECT_STORAGE:
                    object_path = _shared_object_path(archive_path, str(entry.get("object_sha256") or entry.get("sha256")))
                    _add_file_to_zip(target_archive, object_path, path, str(entry.get("role", "model")), progress)
                    entry.pop("storage", None)
                    entry.pop("object_sha256", None)
                elif path in source_names:
                    _copy_zip_member(source_archive, source_archive.getinfo(path), target_archive, progress)
            target_archive.writestr(
                "comfy-package.json",
                json.dumps(portable_manifest, ensure_ascii=False, indent=2) + "\n",
                compress_type=zipfile.ZIP_DEFLATED,
            )
        exported_verification = verify_package(Path(temporary_name), progress)
        if not exported_verification["valid"]:
            raise ValueError("The portable ZIP failed integrity verification.")
        _emit_progress(progress, phase="pack", message="Publishing verified portable ZIP",
                       file=output_path.name, log=True)
        # Windows rename also refuses a target created after this final check.
        if force:
            os.replace(temporary_name, output_path)
        else:
            if output_path.exists():
                raise FileExistsError(f"Export destination already exists: {output_path}")
            os.rename(temporary_name, output_path)
        temporary_name = None
    finally:
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)

    exported_verification["archive"] = str(output_path.resolve())
    return {
        "package": manifest.get("package"),
        "output_path": str(output_path.resolve()),
        "archive_size": output_path.stat().st_size,
        "verification": exported_verification,
    }


def verify_package(archive_path: Path, progress: ProgressCallback = None) -> dict[str, Any]:
    import verification_cache

    if not archive_path.is_file():
        raise ValueError(f"ZIP-Paket nicht gefunden: {archive_path}")
    errors: list[str] = []
    with zipfile.ZipFile(archive_path, "r") as archive:
        try:
            manifest = json.loads(archive.read("comfy-package.json"))
        except KeyError as exc:
            raise ValueError("Kein comfy-package.json im ZIP-Paket gefunden.") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"Ungültiges Paketmanifest: {exc}") from exc

        checked_snapshot = verification_cache.safe_snapshot(archive_path, manifest, opened_archive=archive.fp)
        names = set(archive.namelist())
        for entry in manifest.get("files", []):
            path = entry.get("path")
            expected = entry.get("sha256")
            shared = entry.get("storage") == SHARED_OBJECT_STORAGE
            object_path: Path | None = None
            if shared:
                try:
                    object_path = _shared_object_path(archive_path, str(entry.get("object_sha256") or expected))
                except ValueError as exc:
                    errors.append(str(exc))
                    continue
                if not object_path.is_file():
                    errors.append(f"Fehlt im gemeinsamen Pool: {path}")
                    continue
            elif path not in names:
                errors.append(f"Fehlt im ZIP: {path}")
                continue
            digest = hashlib.sha256()
            total = int(entry.get("size", 0))
            current = 0
            label = f"Prüfe {Path(path).name}"
            _emit_progress(
                progress,
                phase="verify",
                message=label,
                current=0,
                total=total,
                file=Path(path).name,
                log=True,
            )
            try:
                handle_context = object_path.open("rb") if object_path else archive.open(path, "r")
                with handle_context as handle:
                    while chunk := handle.read(8 * 1024 * 1024):
                        digest.update(chunk)
                        current += len(chunk)
                        _emit_progress(
                            progress,
                            phase="verify",
                            message=label,
                            current=current,
                            total=total,
                            file=Path(path).name,
                        )
            except zipfile.BadZipFile as exc:
                errors.append(f"ZIP-CRC-Prüfung fehlgeschlagen: {path} ({exc})")
                continue
            if digest.hexdigest() != expected:
                errors.append(f"Hash stimmt nicht: {path}")

        # Check the open ZIP against its current path again after hashing.
        # Keep the initial snapshot for remember() to diagnose metadata changes;
        # an unavailable/mismatched handle must never yield a successful result.
        if verification_cache.safe_snapshot(archive_path, manifest, opened_archive=archive.fp) is None:
            checked_snapshot = None

    remembered = verification_cache.remember(archive_path, manifest, checked_snapshot, not errors)
    if remembered["state"] == "stale":
        errors.append("Package files changed during verification or their metadata could not be read; verify again.")
    _emit_progress(
        progress,
        phase="complete",
        message="Paketprüfung abgeschlossen" if not errors else "Paketprüfung meldet Fehler",
        current=1,
        total=1,
        level="error" if errors else "success",
        log=True,
    )

    return {
        "archive": str(archive_path.resolve()),
        "package": manifest.get("package"),
        "complete": manifest.get("complete"),
        "declared_file_count": len(manifest.get("files", [])),
        "valid": not errors,
        "verification": remembered,
        "errors": errors,
        "missing": manifest.get("missing", []),
    }


def load_package_manifest(archive_path: Path) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            manifest = json.loads(archive.read("comfy-package.json"))
    except FileNotFoundError as exc:
        raise ValueError(f"ZIP-Paket nicht gefunden: {archive_path}") from exc
    except zipfile.BadZipFile as exc:
        raise ValueError(f"Ungültige ZIP-Datei: {archive_path}") from exc
    except KeyError as exc:
        raise ValueError("Kein comfy-package.json im ZIP-Paket gefunden.") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Ungültiges Paketmanifest: {exc}") from exc
    if manifest.get("package_type") != "comfyui-workflow-overlay":
        raise ValueError("Das ZIP ist kein unterstütztes ComfyUI-Workflow-Paket.")
    return manifest


_WINDOWS_RESERVED_STEMS = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


def rename_package_archive(
    archive_path: Path,
    archive_root: Path,
    new_name: str,
) -> dict[str, Any]:
    """Rename one package ZIP inside the configured archive root without rewriting it."""
    root = archive_root.resolve()
    source = archive_path.resolve()
    if source.parent != root:
        raise ValueError("Das Paket liegt nicht direkt im eingerichteten Auslagerungsordner.")
    if not source.is_file() or source.suffix.casefold() != ".zip":
        raise ValueError("Das umzubenennende ZIP-Paket wurde nicht gefunden.")

    candidate = str(new_name or "").strip()
    if candidate.casefold().endswith(".zip"):
        candidate = candidate[:-4].rstrip()
    if not candidate:
        raise ValueError("Bitte gib einen Namen für den Workflow ein.")
    if candidate.endswith((".", " ")):
        raise ValueError("Der Name darf nicht mit einem Punkt oder Leerzeichen enden.")
    if len(candidate) > 180:
        raise ValueError("Der Workflow-Name darf höchstens 180 Zeichen lang sein.")
    if any(ord(char) < 32 or char in '<>:"/\\|?*' for char in candidate):
        raise ValueError('Der Name enthält ein unter Windows nicht erlaubtes Zeichen: < > : " / \\ | ? *')
    if candidate.split(".", 1)[0].upper() in _WINDOWS_RESERVED_STEMS:
        raise ValueError("Dieser Name ist unter Windows für ein Gerät reserviert.")

    target = root / f"{candidate}.zip"
    if target.name == source.name:
        return {
            "old_name": source.stem,
            "name": source.stem,
            "old_path": str(source),
            "path": str(source),
            "changed": False,
        }

    for sibling in root.iterdir():
        if sibling.name.casefold() == target.name.casefold() and sibling.resolve() != source:
            raise FileExistsError(f'Im Auslagerungsordner existiert bereits „{target.name}“.')

    old_name = source.stem
    os.replace(source, target)
    return {
        "old_name": old_name,
        "name": target.stem,
        "old_path": str(source),
        "path": str(target.resolve()),
        "changed": True,
    }


def _send_to_trash_nt(path: Path) -> None:
    """Send a file to the Windows Recycle Bin using SHFileOperationW."""
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", wintypes.WORD),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", wintypes.LPVOID),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

    resolved = str(path.resolve())
    op = SHFILEOPSTRUCTW()
    op.wFunc = 3  # FO_DELETE
    op.pFrom = resolved + "\0"  # double null terminated
    op.fFlags = 0x0040 | 0x0010 | 0x0004 | 0x0400  # FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI

    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if result != 0 or op.fAnyOperationsAborted:
        raise OSError(f"Konnte Datei nicht in den Papierkorb verschieben (Fehlercode {result}): {path.name}")


def _delete_file(path: Path, send_to_trash: bool = True) -> None:
    if send_to_trash and os.name == "nt":
        _send_to_trash_nt(path)
    else:
        path.unlink(missing_ok=True)


def _cleanup_empty_pool_parents(object_path: Path, archive_root: Path) -> None:
    try:
        pool_prefix_dir = object_path.parent
        if pool_prefix_dir.is_dir() and not any(pool_prefix_dir.iterdir()):
            pool_prefix_dir.rmdir()
    except OSError:
        pass


def inspect_archive_deletion(
    archive_path: Path,
    archive_root: Path,
) -> dict[str, Any]:
    """Inspect one package ZIP inside the archive root and find unreferenced pool models."""
    root = archive_root.resolve()
    source = archive_path.resolve()
    if source.parent != root:
        raise ValueError("Das Paket liegt nicht direkt im eingerichteten Auslagerungsordner.")
    if not source.is_file() or source.suffix.casefold() != ".zip":
        raise ValueError("Das zu löschende ZIP-Paket wurde nicht gefunden.")

    archive_size = source.stat().st_size
    orphaned = []
    try:
        manifest = load_package_manifest(source)
    except Exception:
        manifest = {}

    target_objects: dict[str, dict[str, Any]] = {}
    for asset in manifest.get("assets", []):
        sha = str(asset.get("sha256", "")).casefold()
        if sha and len(sha) == 64:
            target_objects[sha] = {
                "sha256": sha,
                "filename": asset.get("filename", ""),
                "kind": asset.get("kind", ""),
                "size": int(asset.get("size", 0)),
            }
    for file_entry in manifest.get("files", []):
        if file_entry.get("role") == "model":
            sha = str(file_entry.get("sha256", "")).casefold()
            if sha and len(sha) == 64 and sha not in target_objects:
                target_objects[sha] = {
                    "sha256": sha,
                    "filename": Path(str(file_entry.get("path", ""))).name,
                    "kind": "model",
                    "size": int(file_entry.get("size", 0)),
                }

    if target_objects:
        other_hashes: set[str] = set()
        for sibling in root.iterdir():
            if sibling.is_file() and sibling.suffix.casefold() == ".zip" and sibling.resolve() != source:
                try:
                    s_manifest = load_package_manifest(sibling)
                    for asset in s_manifest.get("assets", []):
                        s_sha = str(asset.get("sha256", "")).casefold()
                        if s_sha:
                            other_hashes.add(s_sha)
                    for file_entry in s_manifest.get("files", []):
                        if file_entry.get("role") == "model":
                            s_sha = str(file_entry.get("sha256", "")).casefold()
                            if s_sha:
                                other_hashes.add(s_sha)
                except Exception:
                    continue

        for sha, info in target_objects.items():
            if sha not in other_hashes:
                try:
                    obj_path = _shared_object_path(source, sha)
                    if obj_path.is_file():
                        info["path"] = str(obj_path)
                        orphaned.append(info)
                except Exception:
                    pass

    return {
        "archive_path": str(source),
        "name": source.stem,
        "archive_size": archive_size,
        "orphaned_models": orphaned,
        "orphaned_bytes": sum(m["size"] for m in orphaned),
    }


def delete_package_archive(
    archive_path: Path,
    archive_root: Path,
    *,
    send_to_trash: bool = True,
    delete_orphaned_models: bool = False,
) -> dict[str, Any]:
    """Delete or trash one package ZIP and optionally purge orphaned shared pool models."""
    inspection = inspect_archive_deletion(archive_path, archive_root)
    source = archive_path.resolve()
    old_name = source.stem

    deleted_orphans = []
    if delete_orphaned_models:
        for orphan in inspection["orphaned_models"]:
            obj_path = Path(orphan["path"])
            if obj_path.is_file():
                _delete_file(obj_path, send_to_trash=send_to_trash)
                _cleanup_empty_pool_parents(obj_path, archive_root)
                deleted_orphans.append(orphan)

    _delete_file(source, send_to_trash=send_to_trash)

    return {
        "name": old_name,
        "path": str(source),
        "trashed": send_to_trash,
        "orphaned_deleted_count": len(deleted_orphans),
        "orphaned_deleted_bytes": sum(m["size"] for m in deleted_orphans),
    }


def get_package_thumbnail(archive_path: Path, max_bytes: int = 15 * 1024 * 1024) -> str | None:
    """Extract a thumbnail/cover preview data URI from a package ZIP."""
    if not archive_path.is_file():
        return None
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            namelist = archive.namelist()
            names_by_lower = {name.casefold(): name for name in namelist}

            target_entry = None
            try:
                manifest = json.loads(archive.read("comfy-package.json"))
                thumb = manifest.get("thumbnail")
                if thumb and thumb.casefold() in names_by_lower:
                    target_entry = names_by_lower[thumb.casefold()]
            except Exception:
                pass

            if not target_entry:
                for candidate in (
                    "thumbnail.png", "workflow.png", "cover.png",
                    "thumbnail.jpg", "cover.jpg", "thumbnail.webp", "cover.webp"
                ):
                    if candidate.casefold() in names_by_lower:
                        target_entry = names_by_lower[candidate.casefold()]
                        break

            if not target_entry:
                for name in namelist:
                    lower = name.casefold()
                    if lower.endswith((".png", ".jpg", ".jpeg", ".webp")) and not lower.startswith(".comfyshelf/"):
                        target_entry = name
                        break

            if not target_entry:
                return None

            info = archive.getinfo(target_entry)
            if info.file_size > max_bytes:
                return None

            raw = archive.read(target_entry)
            ext = Path(target_entry).suffix.casefold()
            mime = "image/png"
            if ext in (".jpg", ".jpeg"):
                mime = "image/jpeg"
            elif ext == ".webp":
                mime = "image/webp"
            encoded = base64.b64encode(raw).decode("ascii")
            return f"data:{mime};base64,{encoded}"
    except Exception:
        return None


def _safe_model_target(comfy_root: Path, archive_path: str) -> Path:
    pure = PurePosixPath(archive_path)
    if pure.is_absolute() or not pure.parts or pure.parts[0].casefold() != "models":
        raise ValueError(f"Manifest enthält einen unerlaubten Modellpfad: {archive_path}")
    if any(part in {"", ".", ".."} for part in pure.parts):
        raise ValueError(f"Manifest enthält einen unsicheren Modellpfad: {archive_path}")
    models_root = (comfy_root / "models").resolve()
    target = comfy_root.joinpath(*pure.parts)
    parent_resolved = target.parent.resolve()
    if not _is_relative_to(parent_resolved, models_root):
        raise ValueError(f"Modellpfad verlässt den Models-Ordner: {archive_path}")
    return parent_resolved / target.name


def _asset_key(kind: str, filename: str) -> tuple[str, str]:
    return kind.casefold(), filename.replace("\\", "/").casefold()


def find_shared_model_references(
    manifest: dict[str, Any], comfy_root: Path
) -> dict[tuple[str, str], list[str]]:
    rules = load_node_rules()
    included_keys = {
        _asset_key(asset.get("kind", "model"), asset.get("filename", ""))
        for asset in manifest.get("assets", [])
        if asset.get("status") == "included"
    }
    references: dict[tuple[str, str], list[str]] = {key: [] for key in included_keys}
    keys_by_path: dict[str, set[tuple[str, str]]] = {}
    included_names = set()
    for asset in manifest.get("assets", []):
        if asset.get("status") != "included" or not asset.get("archive_path"):
            continue
        target = _safe_model_target(comfy_root, asset["archive_path"])
        key = _asset_key(asset.get("kind", "model"), asset.get("filename", ""))
        keys_by_path.setdefault(str(target).casefold(), set()).add(key)
        included_names.add(target.name.casefold())
    user_root = comfy_root / "user"
    if not user_root.is_dir() or not included_keys:
        return references

    packaged_workflow = manifest.get("workflow", {}).get("archive_path")
    packaged_path = comfy_root.joinpath(*PurePosixPath(packaged_workflow).parts).resolve() if packaged_workflow else None
    for workflow_path in user_root.rglob("*.json"):
        if "workflows" not in {part.casefold() for part in workflow_path.parts}:
            continue
        try:
            if packaged_path and workflow_path.resolve() == packaged_path:
                continue
            data = load_workflow(workflow_path)
            fmt = workflow_format(data)
            if fmt == "comfyui-ui-workflow":
                assets, _ = parse_ui_workflow(data, rules)
            elif fmt in {"comfyui-api-prompt", "comfyui-api-wrapper"}:
                assets, _ = parse_api_prompt(data, rules)
            else:
                continue
        except (OSError, ValueError):
            continue
        for asset in assets:
            key = _asset_key(asset.kind, asset.filename)
            matched_keys = {key} if key in references else set()
            if PurePosixPath(asset.filename.replace("\\", "/")).name.casefold() in included_names:
                resolved, _ = resolve_asset(asset, comfy_root)
                if resolved:
                    matched_keys.update(keys_by_path.get(str(resolved.parent.resolve() / resolved.name).casefold(), set()))
            for matched_key in matched_keys:
                reference = str(workflow_path.resolve())
                if reference not in references[matched_key]:
                    references[matched_key].append(reference)
    return references


def _removal_os_error(path: Path, exc: OSError) -> dict[str, Any]:
    code = "file_locked" if getattr(exc, "winerror", None) in {32, 33} else (
        "permission_denied" if isinstance(exc, PermissionError) else
        "file_missing" if isinstance(exc, FileNotFoundError) else "filesystem_error"
    )
    reasons = {"file_locked": "File is in use by another process", "permission_denied": "Removal access denied",
               "file_missing": "File no longer exists", "filesystem_error": "Filesystem operation failed"}
    return {"path": str(path), "code": code, "reason": reasons[code],
            "errno": exc.errno, "winerror": getattr(exc, "winerror", None)}


def _deletion_access_issue(path: Path) -> dict[str, Any] | None:
    """Non-mutating, advisory check. Actual unlink errors must still be handled."""
    try:
        info = path.stat()
        if getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_READONLY", 0x1):
            return {"path": str(path), "code": "read_only", "reason": "File is read-only"}
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            create_file = kernel.CreateFileW
            create_file.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                    wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
            create_file.restype = wintypes.HANDLE
            close_handle = kernel.CloseHandle
            close_handle.argtypes = [wintypes.HANDLE]
            close_handle.restype = wintypes.BOOL
            # DELETE permission, full sharing, OPEN_EXISTING. No delete-on-close
            # flag: opening/closing this probe must never change the model file.
            flags = 0x00200000 if path.is_symlink() else 0
            handle = create_file(str(path), 0x00010000, 0x7, None, 3, flags, None)
            if handle == ctypes.c_void_p(-1).value:
                raise ctypes.WinError(ctypes.get_last_error())
            close_handle(handle)
        elif not os.access(path.parent, os.W_OK | os.X_OK):
            return {"path": str(path), "code": "permission_denied", "reason": "Directory does not allow removal"}
    except OSError as exc:
        return _removal_os_error(path, exc)
    return None


def _removal_preflight(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    issues = []
    for item in candidates:
        target = Path(item["path"])
        try:
            info = target.stat()
            if info.st_size != item["size"] or info.st_mtime_ns != item["mtime_ns"]:
                issues.append({"path": str(target), "code": "file_changed", "reason": "File changed after verification"})
                continue
        except OSError as exc:
            issues.append(_removal_os_error(target, exc))
            continue
        issue = _deletion_access_issue(target)
        if issue:
            issues.append(issue)
    return issues


def _stop_removal(result: dict[str, Any], candidates: list[dict[str, Any]], issues: list[dict[str, Any]]) -> None:
    result["removal_issues"] = issues
    result["remaining"] = list(dict.fromkeys(
        item["path"] for item in [*candidates, *issues, *result.get("conflicts", [])]
        if item["path"] not in result["removed"]
    ))
    result["status"] = "partial" if result["removed"] or result.get("restore", {}).get("restored") else "blocked"
    result["dry_run"] = False
    raise RemovalIncompleteError(result)


def _remove_verified_models(
    candidates: list[dict[str, Any]], result: dict[str, Any], progress: ProgressCallback, phase: str,
) -> None:
    issues = _removal_preflight(candidates)
    if issues:
        _stop_removal(result, candidates, issues)
    for item in candidates:
        target = Path(item["path"])
        # Re-check each file too: earlier removals or a concurrent process may
        # have changed access since the all-files preflight.
        issues = _removal_preflight([item])
        if issues:
            _stop_removal(result, candidates, issues)
        try:
            target.unlink()
        except OSError as exc:
            _stop_removal(result, candidates, [_removal_os_error(target, exc)])
        result["removed"].append(str(target))
        _emit_progress(progress, phase=phase, message=f"Removed: {target.name}",
                       current=item["size"], total=item["size"], file=target.name,
                       log=True, cancellable=False)


def unload_package(
    archive_path: Path,
    comfy_root: Path,
    confirm: bool,
    allow_incomplete: bool,
    allow_shared: bool,
    progress: ProgressCallback = None,
) -> dict[str, Any]:
    initial_manifest = load_package_manifest(archive_path)
    pool_preparation_required = any(
        entry.get("role") == "model" and entry.get("storage") != SHARED_OBJECT_STORAGE
        for entry in initial_manifest.get("files", [])
    )
    pool_preparation = None
    if confirm and pool_preparation_required:
        _emit_progress(
            progress,
            phase="pool-prepare",
            message="Modelle werden vor dem Entladen im gemeinsamen Pool gesichert",
            log=True,
        )
        pool_preparation = deduplicate_package(archive_path, progress)
    verification = verify_package(archive_path, progress)
    if not verification["valid"]:
        details = "\n".join(f"- {error}" for error in verification["errors"])
        raise ValueError(f"Paketintegrität ist fehlerhaft; Entladen abgebrochen:\n{details}")
    manifest = load_package_manifest(archive_path)
    if not (comfy_root / "models").is_dir():
        raise ValueError(f"Kein ComfyUI-Modellordner gefunden unter: {comfy_root}")

    blockers: list[str] = []
    if not manifest.get("complete", False) and not allow_incomplete:
        blockers.append("Paket ist unvollständig (für bewusstes Fortfahren --allow-incomplete verwenden).")

    _emit_progress(
        progress,
        phase="references",
        message="Andere lokale Workflows werden auf gemeinsame Modelle geprüft",
        log=True,
    )
    shared = find_shared_model_references(manifest, comfy_root)
    asset_by_path = {
        asset.get("archive_path"): asset
        for asset in manifest.get("assets", [])
        if asset.get("status") == "included" and asset.get("archive_path")
    }
    candidates: list[dict[str, Any]] = []
    already_missing: list[str] = []
    conflicts: list[dict[str, str]] = []
    removal_issues: list[dict[str, Any]] = []
    seen: set[str] = set()

    for file_entry in manifest.get("files", []):
        if file_entry.get("role") != "model":
            continue
        archive_model_path = str(file_entry.get("path", ""))
        if archive_model_path.casefold() in seen:
            continue
        seen.add(archive_model_path.casefold())
        target = _safe_model_target(comfy_root, archive_model_path)
        if not target.is_file():
            already_missing.append(str(target))
            continue
        issue = _deletion_access_issue(target)
        if issue:
            removal_issues.append(issue)
            continue
        expected_hash = file_entry.get("sha256")
        is_same = False
        if file_entry.get("storage") == SHARED_OBJECT_STORAGE:
            try:
                source_obj = _shared_object_path(
                    archive_path,
                    str(file_entry.get("object_sha256") or expected_hash),
                )
                if source_obj.is_file() and os.path.samefile(target, source_obj):
                    is_same = True
            except (OSError, ValueError):
                pass

        if is_same:
            actual_hash = expected_hash
        else:
            try:
                actual_hash = sha256_file(
                    target, progress=progress, phase="local-hash",
                    message=f"Compare local model {target.name}",
                )
            except OSError as exc:
                removal_issues.append(_removal_os_error(target, exc))
                continue
        if not expected_hash or actual_hash != expected_hash:
            conflicts.append(
                {
                    "path": str(target),
                    "reason": "Lokale Datei unterscheidet sich vom archivierten Hash",
                }
            )
            continue
        asset = asset_by_path.get(archive_model_path, {})
        key = _asset_key(asset.get("kind", "model"), asset.get("filename", target.name))
        shared_by = shared.get(key, [])
        stat = target.stat()
        candidates.append(
            {
                "path": str(target),
                "archive_path": archive_model_path,
                "size": stat.st_size,
                "sha256": actual_hash,
                "mtime_ns": stat.st_mtime_ns,
                "shared_by": shared_by,
            }
        )
        if shared_by and not allow_shared:
            blockers.append(
                f"{target.name} wird noch von {len(shared_by)} anderem Workflow(s) referenziert "
                "(für bewusstes Fortfahren --allow-shared verwenden)."
            )

    if conflicts:
        blockers.append("Mindestens eine lokale Modelldatei weicht vom ZIP-Manifest ab.")
    if removal_issues:
        blockers.append("Some model files cannot be removed; close applications using them or check permissions.")

    result = {
        "package": manifest.get("package"),
        "archive": str(archive_path.resolve()),
        "comfy_root": str(comfy_root.resolve()),
        "package_complete": bool(manifest.get("complete")),
        "missing_dependencies": manifest.get("missing", []),
        "dry_run": not confirm,
        "candidates": candidates,
        "already_missing": already_missing,
        "conflicts": conflicts,
        "removal_issues": removal_issues,
        "action": "unload",
        "blockers": list(dict.fromkeys(blockers)),
        "bytes_reclaimable": sum(item["size"] for item in candidates),
        "pool_preparation_required": pool_preparation_required,
        "pool_preparation": pool_preparation,
        "removed": [],
        "removed_empty_directories": [],
    }
    if not confirm:
        return result
    if removal_issues:
        _stop_removal(result, candidates, removal_issues)
    if result["blockers"]:
        details = "\n".join(f"- {blocker}" for blocker in result["blockers"])
        raise ValueError(f"Entladen aus Sicherheitsgründen blockiert:\n{details}")

    _remove_verified_models(candidates, result, progress, "remove")

    models_root = (comfy_root / "models").resolve()
    parents = sorted(
        {Path(item["path"]).parent for item in candidates},
        key=lambda path: len(path.parts),
        reverse=True,
    )
    for parent in parents:
        current = parent
        while current != models_root:
            try:
                current.rmdir()
                result["removed_empty_directories"].append(str(current))
            except OSError:
                break
            current = current.parent
    result["dry_run"] = False
    _emit_progress(
        progress,
        phase="complete",
        message="Workflow-Modelle wurden erfolgreich entladen",
        current=1,
        total=1,
        level="success",
        log=True,
        cancellable=False,
    )
    return result


def _safe_restore_target(comfy_root: Path, archive_path: str, role: str) -> Path:
    if role == "model":
        return _safe_model_target(comfy_root, archive_path)
    pure = PurePosixPath(archive_path)
    if (
        role != "workflow"
        or pure.is_absolute()
        or not pure.parts
        or pure.parts[0].casefold() != "user"
        or "workflows" not in {part.casefold() for part in pure.parts}
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise ValueError(f"Manifest enthält einen unerlaubten Restore-Pfad: {archive_path}")
    user_root = (comfy_root / "user").resolve()
    target = comfy_root.joinpath(*pure.parts)
    parent_resolved = target.parent.resolve()
    if not _is_relative_to(parent_resolved, user_root):
        raise ValueError(f"Workflow-Pfad verlässt den User-Ordner: {archive_path}")
    return parent_resolved / target.name


def restore_package(
    archive_path: Path,
    comfy_root: Path,
    confirm: bool,
    allow_incomplete: bool,
    progress: ProgressCallback = None,
    archive_preverified: bool = False,
    link_mode: str = "auto",
) -> dict[str, Any]:
    manifest = load_package_manifest(archive_path)
    if not archive_preverified:
        import verification_cache
        cache_status = verification_cache.status(archive_path, manifest)
        if cache_status.get("state") == "verified":
            archive_preverified = True

    verification = (
        {"valid": True, "errors": []}
        if archive_preverified
        else verify_package(archive_path, progress)
    )
    if not verification["valid"]:
        details = "\n".join(f"- {error}" for error in verification["errors"])
        raise ValueError(f"Paketintegrität ist fehlerhaft; Wiederherstellung abgebrochen:\n{details}")
    if not comfy_root.is_dir():
        raise ValueError(f"ComfyUI-Root nicht gefunden: {comfy_root}")

    blockers: list[str] = []
    if not manifest.get("complete", False) and not allow_incomplete:
        blockers.append("Paket ist unvollständig (für bewusstes Fortfahren --allow-incomplete verwenden).")

    candidates: list[dict[str, Any]] = []
    identical: list[str] = []
    kept_existing_workflows: list[dict[str, str]] = []
    conflicts: list[dict[str, str]] = []
    ignored: list[str] = []
    seen: set[str] = set()

    for file_entry in manifest.get("files", []):
        role = str(file_entry.get("role", ""))
        archive_member = str(file_entry.get("path", ""))
        if role not in {"model", "workflow"}:
            if archive_member:
                ignored.append(archive_member)
            continue
        if archive_member.casefold() in seen:
            continue
        seen.add(archive_member.casefold())
        target = _safe_restore_target(comfy_root, archive_member, role)
        expected_hash = str(file_entry.get("sha256", ""))
        expected_size = int(file_entry.get("size", 0))
        if target.is_file():
            is_same = False
            if file_entry.get("storage") == SHARED_OBJECT_STORAGE:
                try:
                    source_obj = _shared_object_path(
                        archive_path,
                        str(file_entry.get("object_sha256") or expected_hash),
                    )
                    if source_obj.is_file() and os.path.samefile(target, source_obj):
                        is_same = True
                except (OSError, ValueError):
                    pass
            if is_same:
                identical.append(str(target))
                continue

            actual_hash = sha256_file(
                target,
                progress=progress,
                phase="local-hash",
                message=f"Vergleiche vorhandene Datei {target.name}",
            )
            if expected_hash and actual_hash == expected_hash:
                identical.append(str(target))
            elif role == "workflow":
                kept_existing_workflows.append(
                    {
                        "path": str(target),
                        "reason": "Lokaler Workflow wurde verändert und bleibt unangetastet",
                    }
                )
            else:
                conflicts.append(
                    {
                        "path": str(target),
                        "reason": "Gleicher Modellpfad, aber anderer SHA-256-Hash",
                    }
                )
            continue
        if target.exists():
            conflicts.append(
                {
                    "path": str(target),
                    "reason": "Zielpfad existiert, ist aber keine reguläre Datei",
                }
            )
            continue
        candidates.append(
            {
                "path": str(target),
                "archive_path": archive_member,
                "role": role,
                "size": expected_size,
                "sha256": expected_hash,
                "storage": file_entry.get("storage"),
                "object_sha256": file_entry.get("object_sha256"),
            }
        )

    if conflicts:
        blockers.append("Mindestens ein lokales Modell kollidiert mit dem Paketmanifest.")

    bytes_linkable = 0
    bytes_copy_needed = 0
    for item in candidates:
        can_link = (
            item.get("role") == "model"
            and item.get("storage") == SHARED_OBJECT_STORAGE
            and link_mode in {"auto", "hardlink", "symlink"}
        )
        if can_link and link_mode == "hardlink":
            if archive_path.anchor.casefold() != comfy_root.anchor.casefold():
                can_link = False
        if can_link:
            bytes_linkable += item["size"]
        else:
            bytes_copy_needed += item["size"]

    required = bytes_copy_needed if (link_mode in {"auto", "hardlink", "symlink"} and bytes_linkable > 0) else sum(item["size"] for item in candidates)
    free = shutil.disk_usage(comfy_root).free
    if required > free:
        blockers.append(
            f"Nicht genügend freier Speicher: benötigt {human_size(required)}, frei {human_size(free)}."
        )

    result = {
        "package": manifest.get("package"),
        "archive": str(archive_path.resolve()),
        "comfy_root": str(comfy_root.resolve()),
        "package_complete": bool(manifest.get("complete")),
        "missing_dependencies": manifest.get("missing", []),
        "dry_run": not confirm,
        "candidates": candidates,
        "identical": identical,
        "kept_existing_workflows": kept_existing_workflows,
        "conflicts": conflicts,
        "ignored": ignored,
        "blockers": list(dict.fromkeys(blockers)),
        "bytes_required": required,
        "bytes_linkable": bytes_linkable,
        "bytes_free": free,
        "link_mode": link_mode,
        "restored": [],
        "restored_methods": {},
        "rolled_back": [],
    }
    if not confirm:
        return result
    if result["blockers"]:
        details = "\n".join(f"- {blocker}" for blocker in result["blockers"])
        raise ValueError(f"Wiederherstellung aus Sicherheitsgründen blockiert:\n{details}")

    restored_paths: list[Path] = []
    temporary_paths: list[Path] = []
    restored_methods: dict[str, str] = result["restored_methods"]
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            for item in candidates:
                target = Path(item["path"])
                target.parent.mkdir(parents=True, exist_ok=True)
                role = str(item.get("role", ""))
                storage = item.get("storage")

                link_method_used = None
                if (
                    role == "model"
                    and storage == SHARED_OBJECT_STORAGE
                    and link_mode in {"auto", "hardlink", "symlink"}
                ):
                    source_obj = _shared_object_path(
                        archive_path,
                        str(item.get("object_sha256") or item["sha256"]),
                    )
                    if source_obj.is_file():
                        temp_link = target.parent / f".{target.name}.{uuid.uuid4().hex[:8]}.restore-tmp"
                        try:
                            if link_mode in {"hardlink", "auto"}:
                                same_volume = (
                                    source_obj.anchor.casefold() == target.parent.anchor.casefold()
                                    if (source_obj.anchor and target.parent.anchor)
                                    else True
                                )
                                if same_volume or link_mode == "hardlink":
                                    try:
                                        os.link(source_obj, temp_link)
                                        link_method_used = "hardlink"
                                    except OSError:
                                        if link_mode == "hardlink":
                                            raise
                            if not link_method_used and link_mode in {"symlink", "auto"}:
                                try:
                                    os.symlink(source_obj, temp_link)
                                    link_method_used = "symlink"
                                except OSError:
                                    if link_mode == "symlink":
                                        raise

                            if link_method_used:
                                temporary_paths.append(temp_link)
                                if temp_link.stat().st_size != item["size"]:
                                    raise ValueError(
                                        f"Verknüpftes Modell hat unerwartete Größe: {target} "
                                        f"({temp_link.stat().st_size} != {item['size']})"
                                    )
                                if target.exists():
                                    if target.is_file() and sha256_file(target) == item["sha256"]:
                                        temp_link.unlink(missing_ok=True)
                                        temporary_paths.remove(temp_link)
                                        continue
                                    raise ValueError(f"Zieldatei erschien während der Wiederherstellung: {target}")
                                os.replace(temp_link, target)
                                temporary_paths.remove(temp_link)
                                restored_paths.append(target)
                                result["restored"].append(str(target))
                                restored_methods[str(target)] = link_method_used
                                _emit_progress(
                                    progress,
                                    phase="restore",
                                    message=f"Verknüpft: {target.name} ({link_method_used})",
                                    current=item["size"],
                                    total=item["size"],
                                    file=target.name,
                                    log=True,
                                )
                                continue
                        except Exception:
                            if temp_link.exists() or temp_link.is_symlink():
                                try:
                                    temp_link.unlink(missing_ok=True)
                                except OSError:
                                    pass
                            if temp_link in temporary_paths:
                                temporary_paths.remove(temp_link)
                            if link_mode != "auto":
                                raise

                with tempfile.NamedTemporaryFile(
                    prefix=f".{target.name}.", suffix=".restore-tmp", dir=target.parent, delete=False
                ) as temporary:
                    temporary_path = Path(temporary.name)
                    temporary_paths.append(temporary_path)
                    digest = hashlib.sha256()
                    written = 0
                    label = f"Stelle {target.name} wieder her"
                    _emit_progress(
                        progress,
                        phase="restore",
                        message=label,
                        current=0,
                        total=item["size"],
                        file=target.name,
                        log=True,
                    )
                    if item.get("storage") == SHARED_OBJECT_STORAGE:
                        source_context = _shared_object_path(
                            archive_path,
                            str(item.get("object_sha256") or item["sha256"]),
                        ).open("rb")
                    else:
                        source_context = archive.open(item["archive_path"], "r")
                    with source_context as source:
                        while chunk := source.read(8 * 1024 * 1024):
                            temporary.write(chunk)
                            digest.update(chunk)
                            written += len(chunk)
                            _emit_progress(
                                progress,
                                phase="restore",
                                message=label,
                                current=written,
                                total=item["size"],
                                file=target.name,
                            )
                    temporary.flush()
                    os.fsync(temporary.fileno())

                if digest.hexdigest() != item["sha256"] or written != item["size"]:
                    raise ValueError(f"Hashprüfung nach dem Extrahieren fehlgeschlagen: {target}")
                if target.exists():
                    if target.is_file() and sha256_file(target) == item["sha256"]:
                        temporary_path.unlink(missing_ok=True)
                        temporary_paths.remove(temporary_path)
                        continue
                    raise ValueError(f"Zieldatei erschien während der Wiederherstellung: {target}")
                os.replace(temporary_path, target)
                temporary_paths.remove(temporary_path)
                restored_paths.append(target)
                result["restored"].append(str(target))
                restored_methods[str(target)] = "copy"
    except Exception:
        for temporary_path in temporary_paths:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
        for restored in reversed(restored_paths):
            try:
                restored.unlink(missing_ok=True)
                result["rolled_back"].append(str(restored))
            except OSError:
                pass
        raise

    result["dry_run"] = False
    _emit_progress(
        progress,
        phase="complete",
        message="Workflow-Dateien wurden erfolgreich wiederhergestellt",
        current=1,
        total=1,
        level="success",
        log=True,
    )
    return result


def switch_package(
    target_archive: Path,
    other_archives: list[Path],
    comfy_root: Path,
    confirm: bool,
    allow_incomplete: bool,
    allow_shared: bool,
    progress: ProgressCallback = None,
    link_mode: str = "auto",
) -> dict[str, Any]:
    """Load the target first, then remove verified models not needed by it."""
    pool_preparations: list[dict[str, Any]] = []
    if confirm:
        involved_archives = [target_archive, *other_archives]
        seen_archives: set[str] = set()
        for archive_path in involved_archives:
            resolved_key = str(archive_path.resolve()).casefold()
            if resolved_key in seen_archives:
                continue
            seen_archives.add(resolved_key)
            manifest = load_package_manifest(archive_path)
            needs_pool = any(
                entry.get("role") == "model" and entry.get("storage") != SHARED_OBJECT_STORAGE
                for entry in manifest.get("files", [])
            )
            if needs_pool:
                _emit_progress(
                    progress,
                    phase="pool-prepare",
                    message=f"Bereite {archive_path.stem} für den sicheren Wechsel vor",
                    log=True,
                )
                pool_preparations.append(deduplicate_package(archive_path, progress))
    target_manifest = load_package_manifest(target_archive)
    target_paths = {
        str(item.get("path", "")).casefold()
        for item in target_manifest.get("files", [])
        if item.get("role") == "model"
    }
    target_keys = {
        _asset_key(item.get("kind", "model"), item.get("filename", ""))
        for item in target_manifest.get("assets", [])
        if item.get("status") == "included"
    }
    restore_preview = restore_package(
        target_archive, comfy_root, False, allow_incomplete, progress, link_mode=link_mode
    )
    blockers = list(restore_preview.get("blockers", []))
    conflicts: list[dict[str, str]] = []
    unload_candidates: list[dict[str, Any]] = []
    removal_issues: list[dict[str, Any]] = []
    seen_targets: set[str] = set()
    source_packages: list[str] = []
    shared_count = 0

    for archive_path in other_archives:
        if archive_path.resolve() == target_archive.resolve():
            continue
        manifest = load_package_manifest(archive_path)
        import verification_cache
        cache_status = verification_cache.status(archive_path, manifest)
        if cache_status.get("state") == "verified":
            verification = {"valid": True, "errors": []}
        else:
            verification = verify_package(archive_path, progress)
        if not verification["valid"]:
            blockers.append(f"Anderes Paket ist beschädigt: {archive_path.name}")
            continue
        source_packages.append(str(manifest.get("package") or archive_path.stem))
        shared = find_shared_model_references(manifest, comfy_root)
        assets_by_path = {
            item.get("archive_path"): item
            for item in manifest.get("assets", [])
            if item.get("status") == "included" and item.get("archive_path")
        }
        for file_entry in manifest.get("files", []):
            if file_entry.get("role") != "model":
                continue
            archive_model_path = str(file_entry.get("path", ""))
            asset = assets_by_path.get(archive_model_path, {})
            asset_key = _asset_key(
                asset.get("kind", "model"), asset.get("filename", Path(archive_model_path).name)
            )
            if archive_model_path.casefold() in target_paths or asset_key in target_keys:
                continue
            target = _safe_model_target(comfy_root, archive_model_path)
            target_key = str(target).casefold()
            if target_key in seen_targets or not target.is_file():
                continue
            issue = _deletion_access_issue(target)
            if issue:
                removal_issues.append(issue)
                continue
            expected_size = int(file_entry.get("size", 0))
            if target.stat().st_size != expected_size:
                conflicts.append({"path": str(target), "reason": "Lokale Datei hat eine andere Größe"})
                continue
            is_same = False
            if file_entry.get("storage") == SHARED_OBJECT_STORAGE:
                try:
                    source_obj = _shared_object_path(
                        archive_path,
                        str(file_entry.get("object_sha256") or file_entry.get("sha256")),
                    )
                    if source_obj.is_file() and os.path.samefile(target, source_obj):
                        is_same = True
                except (OSError, ValueError):
                    pass

            if is_same:
                actual_hash = file_entry.get("sha256")
            else:
                try:
                    actual_hash = sha256_file(
                        target, progress=progress, phase="local-hash",
                        message=f"Check previous workflow model {target.name}",
                    )
                except OSError as exc:
                    removal_issues.append(_removal_os_error(target, exc))
                    continue
            if actual_hash != file_entry.get("sha256"):
                conflicts.append({"path": str(target), "reason": "Lokaler SHA-256-Hash weicht ab"})
                continue
            references = shared.get(asset_key, [])
            if references:
                shared_count += 1
                if not allow_shared:
                    blockers.append(
                        f"{target.name} wird von {len(references)} weiterem lokalen Workflow(s) referenziert."
                    )
            stat = target.stat()
            unload_candidates.append(
                {
                    "path": str(target),
                    "archive_path": archive_model_path,
                    "size": stat.st_size,
                    "sha256": actual_hash,
                    "mtime_ns": stat.st_mtime_ns,
                    "shared_by": references,
                }
            )
            seen_targets.add(target_key)

    if conflicts:
        blockers.append("Mindestens ein altes Workflow-Modell stimmt nicht mit seinem Paket überein.")
    if removal_issues:
        blockers.append("Some model files cannot be removed; close applications using them or check permissions.")
    result = {
        "package": target_manifest.get("package"),
        "target_archive": str(target_archive.resolve()),
        "source_packages": source_packages,
        "dry_run": not confirm,
        "restore": restore_preview,
        "unload_candidates": unload_candidates,
        "action": "switch",
        "removal_issues": removal_issues,
        "conflicts": conflicts,
        "shared_count": shared_count,
        "blockers": list(dict.fromkeys(blockers)),
        "bytes_required": int(restore_preview.get("bytes_required", 0)),
        "bytes_reclaimable": sum(item["size"] for item in unload_candidates),
        "pool_preparations": pool_preparations,
        "removed": [],
    }
    if not confirm:
        return result
    if removal_issues:
        _stop_removal(result, unload_candidates, removal_issues)
    if result["blockers"]:
        details = "\n".join(f"- {item}" for item in result["blockers"])
        raise ValueError(f"Workflow-Wechsel aus Sicherheitsgründen blockiert:\n{details}")

    issues = _removal_preflight(unload_candidates)
    if issues:
        _stop_removal(result, unload_candidates, issues)
    restore_result = restore_package(
        target_archive, comfy_root, True, allow_incomplete, progress, archive_preverified=True, link_mode=link_mode
    )
    # Preserve the actual restore outcome if a subsequent removal fails.
    result["restore"] = restore_result

    _emit_progress(
        progress,
        phase="switch-remove",
        message="Ziel ist geladen; nicht mehr benötigte Modelle werden entladen",
        log=True,
        cancellable=False,
    )
    _remove_verified_models(unload_candidates, result, progress, "switch-remove")
    result["restore"] = restore_result
    result["dry_run"] = False
    _emit_progress(
        progress,
        phase="complete",
        message="Workflow wurde erfolgreich gewechselt",
        current=1,
        total=1,
        level="success",
        log=True,
        cancellable=False,
    )
    return result


def human_size(size: int | None) -> str:
    if size is None:
        return ""
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.2f} {unit}"
        value /= 1024
    return f"{size} B"


def print_report(report: dict[str, Any]) -> None:
    print(f"Workflow: {report['workflow']}")
    print(f"Format:   {report['format']}")
    print(f"ComfyUI:  {report['comfy_root'] or 'nicht angegeben/erkannt'}")
    print("\nModelle:")
    assets = report["assets"]
    if not assets:
        print("  Keine Modelldateien erkannt.")
    for asset in assets:
        status = "OK" if asset["resolved_path"] else "NICHT GEFUNDEN"
        heuristic = " [prüfen]" if asset["detection"] == "extension-heuristic" else ""
        size = f" ({human_size(asset['size'])})" if asset["size"] is not None else ""
        print(f"  {status:15} {asset['kind']:18} {asset['filename']}{size}{heuristic}")
        if asset["resolved_path"]:
            print(f"                  -> {asset['resolved_path']}")
        if asset["sha256"]:
            print(f"                  SHA-256 {asset['sha256']}")

    print("\nVerwendete Custom-Node-Pakete:")
    custom_nodes = report["custom_nodes"]
    if not custom_nodes:
        print("  Keine externen Pakete in den Node-Metadaten erkannt.")
    for node in custom_nodes:
        suffix = f" @ {node['revision']}" if node["revision"] else ""
        print(f"  {node['package']}{suffix}")

    unused = report["unused_environment_metadata"]
    if unused:
        print("\nNur als Umgebungsmetadaten vorhanden (nicht als Abhängigkeit gewertet):")
        for package in sorted(unused):
            print(f"  {package} @ {unused[package]}")

    ambiguous = report["ambiguous_matches"]
    if ambiguous:
        print("\nAbweichende oder mehrdeutige Dateitreffer:")
        for filename, paths in ambiguous.items():
            print(f"  {filename}")
            for path in paths:
                print(f"    - {path}")
    for warning in report.get("parser_warnings", []):
        print(f"  REVIEW [{warning['code']}]: {warning['node_type']} #{warning['node_id']} "
              f"{warning['field']} {warning['filename']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="comfy-archive",
        description="ComfyUI-Workflows sicher analysieren, archivieren und wechseln.",
    )
    parser.add_argument(
        "--config-file",
        type=Path,
        help=f"Alternative Konfigurationsdatei (sonst Umgebungsvariable {CONFIG_ENV_VAR} oder Benutzerprofil)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    config_parser = subparsers.add_parser("config", help="ComfyUI- und Archivpfade verwalten")
    config_commands = config_parser.add_subparsers(dest="config_command", required=True)
    config_set = config_commands.add_parser("set", help="Installation speichern und aktivieren")
    config_set.add_argument("--id", default="default", help="Kurze Installations-ID")
    config_set.add_argument("--name", default="ComfyUI", help="Anzeigename")
    config_set.add_argument("--comfy-root", type=Path, required=True, help="ComfyUI-Hauptordner")
    config_set.add_argument("--archive-root", type=Path, required=True, help="Auslagerungsordner")
    config_set.add_argument(
        "--link-mode",
        choices=["copy", "auto", "hardlink", "symlink"],
        default="auto",
        help="Standard-Lademethode für Modelle (copy, auto, hardlink, symlink)",
    )
    config_show = config_commands.add_parser("show", help="Aktive Konfiguration anzeigen")
    config_show.add_argument("--json", action="store_true", help="JSON ausgeben")
    config_validate = config_commands.add_parser("validate", help="Aktive Pfade erneut prüfen")
    config_validate.add_argument("--json", action="store_true", help="JSON ausgeben")
    config_commands.add_parser("path", help="Pfad der Konfigurationsdatei anzeigen")
    inspect_parser = subparsers.add_parser("inspect", help="Workflow-Abhängigkeiten anzeigen")
    inspect_parser.add_argument("workflow", type=Path, help="Pfad zur Workflow-JSON")
    inspect_parser.add_argument(
        "--comfy-root", type=Path, help="ComfyUI-Hauptordner (wird sonst möglichst erkannt)"
    )
    inspect_parser.add_argument(
        "--hash", action="store_true", help="SHA-256 nur für erkannte und gefundene Assets berechnen"
    )
    inspect_parser.add_argument(
        "--json", action="store_true", help="Maschinenlesbaren JSON-Bericht ausgeben"
    )
    inspect_parser.add_argument(
        "--write-manifest", type=Path, help="Bericht zusätzlich als Manifest speichern"
    )

    pack_parser = subparsers.add_parser("pack", help="Portables ComfyUI-Overlay-ZIP erstellen")
    pack_parser.add_argument("workflow", type=Path, help="Pfad zur Workflow-JSON")
    pack_parser.add_argument(
        "--output", "-o", type=Path, help="Ziel-ZIP (sonst <Auslagerungsordner>/<Workflow>.zip)"
    )
    pack_parser.add_argument(
        "--comfy-root", type=Path, help="ComfyUI-Hauptordner (wird sonst möglichst erkannt)"
    )
    pack_parser.add_argument(
        "--allow-missing",
        action="store_true",
        help="Unvollständige ZIP bewusst erstellen und fehlende Abhängigkeiten manifestieren",
    )
    pack_parser.add_argument(
        "--force", action="store_true", help="Eine bereits vorhandene Ziel-ZIP überschreiben"
    )

    verify_parser = subparsers.add_parser("verify", help="Paketstruktur und SHA-256-Hashes prüfen")
    verify_parser.add_argument("archive", type=Path, help="Zu prüfende ZIP-Datei")
    verify_parser.add_argument("--json", action="store_true", help="JSON-Bericht ausgeben")

    unload_parser = subparsers.add_parser(
        "unload", help="Archivierte Modelle geprüft aus ComfyUI entfernen"
    )
    unload_parser.add_argument("archive", type=Path, help="Verifiziertes Workflow-ZIP")
    unload_parser.add_argument("--comfy-root", type=Path, help="ComfyUI-Hauptordner")
    unload_parser.add_argument(
        "--confirm",
        action="store_true",
        help="Geprüfte Modelldateien wirklich entfernen (ohne diesen Schalter nur Vorschau)",
    )
    unload_parser.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="Entladen trotz bewusst unvollständigem Paket erlauben",
    )
    unload_parser.add_argument(
        "--allow-shared",
        action="store_true",
        help="Auch Modelle entfernen, die andere lokale Workflows referenzieren",
    )
    unload_parser.add_argument("--json", action="store_true", help="JSON-Bericht ausgeben")

    restore_parser = subparsers.add_parser(
        "restore", help="Modelle und fehlende Workflow-JSON sicher wiederherstellen"
    )
    restore_parser.add_argument("archive", type=Path, help="Verifiziertes Workflow-ZIP")
    restore_parser.add_argument("--comfy-root", type=Path, help="ComfyUI-Hauptordner")
    restore_parser.add_argument(
        "--confirm",
        action="store_true",
        help="Fehlende Dateien wirklich wiederherstellen (ohne diesen Schalter nur Vorschau)",
    )
    restore_parser.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="Wiederherstellung trotz bewusst unvollständigem Paket erlauben",
    )
    restore_parser.add_argument(
        "--link-mode",
        choices=["copy", "auto", "hardlink", "symlink"],
        default="auto",
        help="Lademethode für Modelle: auto (Hardlink/Symlink mit Fallback), copy, hardlink, symlink",
    )
    restore_parser.add_argument("--json", action="store_true", help="JSON-Bericht ausgeben")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "config":
            config_path = args.config_file or default_config_path()
            if args.config_command == "path":
                print(config_path.resolve())
                return 0
            if args.config_command == "set":
                _, validation, saved_path = set_installation_config(
                    args.id,
                    args.name,
                    args.comfy_root,
                    args.archive_root,
                    config_path,
                    link_mode=args.link_mode,
                )
                print(f"Konfiguration gespeichert: {saved_path.resolve()}")
                print(f"Aktive Installation:       {args.name}")
                print(f"ComfyUI-Hauptordner:        {validation['comfy_root']}")
                print(f"Auslagerungsordner:         {validation['archive_root']}")
                print(f"ComfyUI frei:               {human_size(validation['comfy_free'])}")
                print(f"Archiv frei:                {human_size(validation['archive_free'])}")
                if validation["archive_filesystem"]:
                    print(f"Archiv-Dateisystem:         {validation['archive_filesystem']}")
                for warning in validation["warnings"]:
                    print(f"WARNUNG: {warning}")
                return 0
            installation = active_installation(config_path)
            if not installation:
                raise ValueError(
                    f"Noch keine aktive Installation konfiguriert. Konfigurationspfad: {config_path}"
                )
            if args.config_command == "show":
                data = {
                    "config_path": str(config_path.resolve()),
                    "active_installation": installation,
                }
                if args.json:
                    print(json.dumps(data, ensure_ascii=False, indent=2))
                else:
                    print(f"Konfiguration:       {data['config_path']}")
                    print(f"Aktive Installation: {installation['name']} ({installation['id']})")
                    print(f"ComfyUI:             {installation['comfy_root']}")
                    print(f"Archiv:              {installation['archive_root']}")
                return 0
            if args.config_command == "validate":
                validation = validate_installation_paths(
                    Path(installation["comfy_root"]), Path(installation["archive_root"])
                )
                if args.json:
                    print(json.dumps(validation, ensure_ascii=False, indent=2))
                else:
                    print(f"Status:       {'OK' if validation['valid'] else 'FEHLER'}")
                    print(f"ComfyUI:      {validation['comfy_root']}")
                    print(f"Archiv:       {validation['archive_root']}")
                    print(f"ComfyUI frei: {human_size(validation['comfy_free'])}")
                    print(f"Archiv frei:  {human_size(validation['archive_free'])}")
                    for error in validation["errors"]:
                        print(f"FEHLER:  {error}")
                    for warning in validation["warnings"]:
                        print(f"WARNUNG: {warning}")
                return 0 if validation["valid"] else 2

        if args.command == "inspect":
            comfy_root = select_comfy_root(args.comfy_root, args.workflow, args.config_file)
            if comfy_root and not (comfy_root / "models").is_dir():
                raise ValueError(f"Kein ComfyUI-Modellordner gefunden unter: {comfy_root}")
            report = inspect_workflow(args.workflow, comfy_root, args.hash)
            serialized = json.dumps(report, ensure_ascii=False, indent=2)
            if args.write_manifest:
                args.write_manifest.parent.mkdir(parents=True, exist_ok=True)
                args.write_manifest.write_text(serialized + "\n", encoding="utf-8")
            if args.json:
                print(serialized)
            else:
                print_report(report)
                if args.write_manifest:
                    print(f"\nManifest geschrieben: {args.write_manifest.resolve()}")
            return 0

        if args.command == "pack":
            comfy_root = select_comfy_root(args.comfy_root, args.workflow, args.config_file)
            if not comfy_root:
                raise ValueError("ComfyUI-Root konnte nicht erkannt werden; bitte --comfy-root angeben.")
            if not (comfy_root / "models").is_dir():
                raise ValueError(f"Kein ComfyUI-Modellordner gefunden unter: {comfy_root}")
            output_path = args.output
            if not output_path:
                _, archive_root = configured_roots(args.config_file)
                if not archive_root:
                    raise ValueError(
                        "Kein Auslagerungsordner konfiguriert; bitte --output angeben oder config set verwenden."
                    )
                output_path = archive_root / f"{args.workflow.stem}.zip"
            result = build_package(
                args.workflow,
                comfy_root,
                output_path,
                args.allow_missing,
                args.force,
            )
            state = "VOLLSTÄNDIG" if result["complete"] else "UNVOLLSTÄNDIG"
            print(f"Paket erstellt: {result['output_path']}")
            print(f"Status:         {state}")
            print(f"Dateien:        {len(result['files'])}")
            print(f"ZIP-Größe:      {human_size(result['archive_size'])}")
            if result["missing"]:
                print("\nFehlende Abhängigkeiten (im Manifest dokumentiert):")
                for item in result["missing"]:
                    print(f"  {item['kind']}: {item['name']} ({item['reason']})")
            return 0

        if args.command == "verify":
            result = verify_package(args.archive)
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print(f"Paket:    {result['package']}")
                print(f"Dateien:  {result['declared_file_count']}")
                print(f"Integrität: {'OK' if result['valid'] else 'FEHLER'}")
                print(f"Inhalt:   {'vollständig' if result['complete'] else 'unvollständig'}")
                for error in result["errors"]:
                    print(f"  {error}")
                if result["missing"]:
                    print("Fehlende Abhängigkeiten laut Manifest:")
                    for item in result["missing"]:
                        print(f"  {item['kind']}: {item['name']} ({item['reason']})")
            return 0 if result["valid"] else 3

        if args.command == "unload":
            comfy_root = select_comfy_root(args.comfy_root, None, args.config_file)
            if not comfy_root:
                raise ValueError("Kein ComfyUI-Root konfiguriert; bitte --comfy-root angeben.")
            result = unload_package(
                args.archive,
                comfy_root,
                args.confirm,
                args.allow_incomplete,
                args.allow_shared,
            )
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 0
            print(f"Paket:             {result['package']}")
            print(f"Paketinhalt:       {'vollständig' if result['package_complete'] else 'unvollständig'}")
            print(f"Modell-Dateien:    {len(result['candidates'])}")
            print(f"Freigebbarer Platz: {human_size(result['bytes_reclaimable'])}")
            print(f"Modus:             {'ENTFERNEN' if args.confirm else 'NUR VORSCHAU'}")
            for item in result["candidates"]:
                print(f"  {'ENTFERNT' if item['path'] in result['removed'] else 'BEREIT'}  {item['path']}")
                if item["shared_by"]:
                    print(f"          Von {len(item['shared_by'])} anderem Workflow(s) referenziert:")
                    for workflow in item["shared_by"]:
                        print(f"            - {workflow}")
            for path in result["already_missing"]:
                print(f"  FEHLT   {path}")
            for conflict in result["conflicts"]:
                print(f"  KONFLIKT {conflict['path']}: {conflict['reason']}")
            if result["blockers"]:
                print("\nSicherheitsblocker:")
                for blocker in result["blockers"]:
                    print(f"  - {blocker}")
            if not args.confirm:
                print("\nEs wurde nichts entfernt. Nach Prüfung denselben Befehl mit --confirm ausführen.")
            else:
                print(f"\nEntfernt: {len(result['removed'])} Modelldatei(en)")
            return 0

        if args.command == "restore":
            comfy_root = select_comfy_root(args.comfy_root, None, args.config_file)
            if not comfy_root:
                raise ValueError("Kein ComfyUI-Root konfiguriert; bitte --comfy-root angeben.")
            result = restore_package(
                args.archive,
                comfy_root,
                args.confirm,
                args.allow_incomplete,
                link_mode=args.link_mode,
            )
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 0
            print(f"Paket:              {result['package']}")
            print(f"Paketinhalt:        {'vollständig' if result['package_complete'] else 'unvollständig'}")
            print(f"Wiederherstellbar:  {len(result['candidates'])} Datei(en)")
            print(f"Benötigter Platz:    {human_size(result['bytes_required'])}")
            print(f"Freier Platz:        {human_size(result['bytes_free'])}")
            print(f"Bereits identisch:   {len(result['identical'])}")
            print(f"Modus:               {'WIEDERHERSTELLEN' if args.confirm else 'NUR VORSCHAU'}")
            for item in result["candidates"]:
                state = "WIEDERHERGESTELLT" if item["path"] in result["restored"] else "BEREIT"
                print(f"  {state:18} {item['path']}")
            for path in result["identical"]:
                print(f"  IDENTISCH          {path}")
            for item in result["kept_existing_workflows"]:
                print(f"  WORKFLOW BEHALTEN  {item['path']}: {item['reason']}")
            for item in result["conflicts"]:
                print(f"  KONFLIKT           {item['path']}: {item['reason']}")
            if result["missing_dependencies"]:
                print("\nFehlende Abhängigkeiten laut Paketmanifest:")
                for item in result["missing_dependencies"]:
                    print(f"  {item['kind']}: {item['name']} ({item['reason']})")
            if result["blockers"]:
                print("\nSicherheitsblocker:")
                for blocker in result["blockers"]:
                    print(f"  - {blocker}")
            if not args.confirm:
                print("\nEs wurde nichts wiederhergestellt. Nach Prüfung denselben Befehl mit --confirm ausführen.")
            else:
                print(f"\nWiederhergestellt: {len(result['restored'])} Datei(en)")
            return 0

        raise ValueError(f"Unbekannter Befehl: {args.command}")
    except RemovalIncompleteError as exc:
        if getattr(args, "json", False):
            print(json.dumps({"ok": False, "error_code": "removal_incomplete", "details": exc.result}, ensure_ascii=False, indent=2))
        else:
            print(str(exc), file=sys.stderr)
            for path in exc.result.get("removed", []):
                print(f"  REMOVED: {path}", file=sys.stderr)
            for path in exc.result.get("remaining", []):
                print(f"  NOT REMOVED: {path}", file=sys.stderr)
            for issue in exc.result.get("removal_issues", []):
                print(f"  {issue['code']}: {issue['path']} | {issue['reason']}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
