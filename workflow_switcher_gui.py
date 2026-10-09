#!/usr/bin/env python3
"""Desktop GUI for the ComfyUI workflow archive/switching engine."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import webbrowser
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Callable

APP_DIR = Path(__file__).resolve().parent
WEB_DIR = APP_DIR / "gui"
HISTORY_PATH = APP_DIR / ".gui-state" / "history.json"
LOG_PATH = APP_DIR / ".gui-state" / "comfyshelf.log"
PREFERENCES_PATH = APP_DIR / ".gui-state" / "preferences.json"
LOCALES_DIR = WEB_DIR / "locales"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

try:
    import webview
except ImportError:
    message = (
        "The GUI requires pywebview. Install requirements-gui.txt into your Python "
        "environment and start ComfyShelf with that environment. See README.md."
    )
    (APP_DIR / "workflow-switcher-error.log").write_text(message + "\n", encoding="utf-8")
    if os.name == "nt":
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, message, "ComfyShelf could not start", 0x10)
    else:
        print(message, file=sys.stderr)
    raise SystemExit(2)

import comfy_archive as engine
import verification_cache
import dependency_scan
import workflow_file


def _reveal_workflow_file(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"Workflow JSON not found: {path}")
    if os.name != "nt":
        raise OSError("Showing workflow files is currently supported on Windows only")
    subprocess.Popen(["explorer.exe", "/select,", str(path)], shell=False)
from webview.window import FixPoint


def _configure_logging() -> logging.Logger:
    logger = logging.getLogger("comfyshelf")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            LOG_PATH,
            maxBytes=1_000_000,
            backupCount=4,
            encoding="utf-8",
            delay=True,
        )
        handler.setFormatter(logging.Formatter(
            "%(asctime)s | %(levelname)-7s | %(threadName)s | %(message)s",
            "%Y-%m-%d %H:%M:%S",
        ))
        logger.addHandler(handler)
        webview_logger = logging.getLogger("pywebview")
        webview_logger.setLevel(logging.INFO)
        if not webview_logger.handlers:
            webview_logger.addHandler(handler)
            webview_logger.propagate = False
    except OSError:
        logger.addHandler(logging.NullHandler())
    return logger


LOGGER = _configure_logging()


def _fit_window_to_work_area(work_width: int, work_height: int) -> tuple[int, int]:
    """Keep the initial window inside the usable desktop area."""
    horizontal_margin = 32
    vertical_margin = 32
    width = max(980, min(1460, work_width - horizontal_margin))
    height = max(650, min(920, work_height - vertical_margin))
    return width, height


def _initial_window_size() -> tuple[int, int]:
    default_size = (1460, 920)
    if os.name != "nt":
        return default_size
    try:
        import ctypes
        from ctypes import wintypes

        work_area = wintypes.RECT()
        spi_get_work_area = 0x0030
        if ctypes.windll.user32.SystemParametersInfoW(
            spi_get_work_area,
            0,
            ctypes.byref(work_area),
            0,
        ):
            work_width = work_area.right - work_area.left
            work_height = work_area.bottom - work_area.top
            size = _fit_window_to_work_area(work_width, work_height)
            LOGGER.info(
                "Windows work area %sx%s; initial window %sx%s",
                work_width,
                work_height,
                size[0],
                size[1],
            )
            return size
    except (AttributeError, OSError, TypeError, ValueError):
        LOGGER.exception("Could not determine the Windows work area")
    return default_size


class WorkflowSwitcherApi:
    def __init__(self) -> None:
        # pywebview recursively inspects every public API attribute while building
        # its JavaScript bridge. A public Window reference creates a huge cyclic
        # object graph and can freeze the UI thread before ``pywebviewready``.
        self._window: Any = None
        self._maximized = False
        self._last_progress_emit = 0.0
        self._cancel_event = threading.Event()
        self._history_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._state_cache: dict[str, Any] | None = None
        self._scan_thread: threading.Thread | None = None
        self._frontend_ready = threading.Event()
        self._last_export_dir: Path | None = None

    def _attach_window(self, window: Any) -> None:
        self._window = window

    def _wait_for_frontend(self, timeout: float) -> bool:
        return self._frontend_ready.wait(timeout)

    def report_startup_stage(self, stage: str, detail: str = "") -> dict[str, Any]:
        """Record fixed-English startup diagnostics from the web frontend."""
        allowed_stages = {
            "bridge-ready",
            "bootstrap-started",
            "frontend-ready",
            "bootstrap-failed",
        }
        safe_stage = str(stage).strip().casefold()
        if safe_stage not in allowed_stages:
            safe_stage = "unknown"
        safe_detail = str(detail).replace("\r", " ").replace("\n", " ").strip()[:240]
        LOGGER.info(
            "Frontend startup stage: %s%s",
            safe_stage,
            f" | {safe_detail}" if safe_detail else "",
        )
        if safe_stage == "frontend-ready":
            self._frontend_ready.set()
        return {"ok": True, "data": True}

    def _progress(self, event: dict[str, Any]) -> None:
        if (
            self._cancel_event.is_set()
            and event.get("phase") != "complete"
            and event.get("cancellable", True)
        ):
            raise engine.OperationCancelled("Vorgang wurde sicher abgebrochen.")
        if not self._window:
            return
        now = time.monotonic()
        if not event.get("log") and event.get("phase") != "complete" and now - self._last_progress_emit < 0.08:
            return
        self._last_progress_emit = now
        payload = json.dumps(event, ensure_ascii=False)
        try:
            self._window.evaluate_js(f"window.workflowProgress({payload})")
        except Exception:
            pass

    @staticmethod
    def _error_code(exc: Exception) -> str:
        if isinstance(exc, (dependency_scan.DependencyError, workflow_file.WorkflowFileError)):
            return exc.code
        if isinstance(exc, FileNotFoundError):
            return "file_not_found"
        if isinstance(exc, FileExistsError):
            return "already_exists"
        if isinstance(exc, PermissionError):
            return "permission_denied"
        if isinstance(exc, ValueError):
            lowered = str(exc).casefold()
            if "zip" in lowered or "paket" in lowered or "manifest" in lowered:
                return "invalid_package"
            return "invalid_input"
        if isinstance(exc, OSError):
            return "filesystem_error"
        return "unexpected"

    @staticmethod
    def _result(action: Callable[[], Any]) -> dict[str, Any]:
        try:
            return {"ok": True, "data": action()}
        except Exception as exc:
            LOGGER.exception("API action failed: %s", exc)
            return {"ok": False, "error_code": WorkflowSwitcherApi._error_code(exc), "error": str(exc)}

    def _operation(self, action: Callable[[], Any]) -> dict[str, Any]:
        self._cancel_event.clear()
        started = time.perf_counter()
        try:
            result = action()
            LOGGER.info("Operation completed (%.2fs)", time.perf_counter() - started)
            return {"ok": True, "data": result}
        except engine.RemovalIncompleteError as exc:
            details = exc.result
            LOGGER.warning("Model removal stopped (%.2fs): %s | %s",
                           time.perf_counter() - started, exc, json.dumps(details, ensure_ascii=False))
            self._record_history(
                f"{details.get('action', 'unload')}_partial", details.get("package"),
                f"Operation stopped: {len(details.get('removed', []))} model file(s) removed; "
                f"{len(details.get('remaining', []))} not removed",
            )
            return {"ok": False, "error_code": "removal_incomplete", "error": str(exc), "details": details}
        except engine.OperationCancelled as exc:
            LOGGER.info("Operation cancelled safely (%.2fs)", time.perf_counter() - started)
            return {"ok": False, "cancelled": True, "error_code": "cancelled", "error": str(exc)}
        except Exception as exc:
            LOGGER.exception("Operation failed after %.2fs: %s", time.perf_counter() - started, exc)
            return {"ok": False, "error_code": self._error_code(exc), "error": str(exc)}
        finally:
            self._cancel_event.clear()

    def cancel_operation(self) -> bool:
        self._cancel_event.set()
        return True

    def _load_history(self) -> list[dict[str, Any]]:
        with self._history_lock:
            try:
                data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
                return data if isinstance(data, list) else []
            except (OSError, json.JSONDecodeError):
                return []

    def _record_history(
        self,
        action: str,
        package: str | None,
        detail: str,
        bytes_changed: int = 0,
    ) -> None:
        with self._history_lock:
            try:
                HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
                try:
                    data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
                    if not isinstance(data, list):
                        data = []
                except (OSError, json.JSONDecodeError):
                    data = []
                data.insert(
                    0,
                    {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "action": action,
                        "package": package or "Unknown package",
                        "detail": detail,
                        "bytes": int(bytes_changed or 0),
                    },
                )
                temporary = HISTORY_PATH.with_suffix(".tmp")
                temporary.write_text(
                    json.dumps(data[:100], ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                os.replace(temporary, HISTORY_PATH)
                LOGGER.info("Action: %s | Package: %s | %s", action, package or "Unknown", detail)
            except OSError:
                pass

    def get_history(self) -> dict[str, Any]:
        return self._result(self._load_history)

    def clear_history(self) -> dict[str, Any]:
        def action() -> bool:
            with self._history_lock:
                HISTORY_PATH.unlink(missing_ok=True)
            return True

        return self._result(action)

    @staticmethod
    def _read_preferences() -> dict[str, Any]:
        try:
            value = json.loads(PREFERENCES_PATH.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _locale_payload(language: str | None = None) -> dict[str, Any]:
        selected = language or str(WorkflowSwitcherApi._read_preferences().get("language", "en"))
        if selected not in {"en", "de"}:
            selected = "en"
        fallback = json.loads((LOCALES_DIR / "en.json").read_text(encoding="utf-8"))
        if selected == "en":
            messages = fallback
        else:
            translated = json.loads((LOCALES_DIR / f"{selected}.json").read_text(encoding="utf-8"))
            messages = {**fallback, **translated}
        return {"language": selected, "messages": messages}

    def get_locale(self) -> dict[str, Any]:
        return self._result(self._locale_payload)

    def set_language(self, language: str) -> dict[str, Any]:
        def action() -> dict[str, Any]:
            if language not in {"en", "de"}:
                raise ValueError("Unsupported interface language.")
            preferences = self._read_preferences()
            preferences["language"] = language
            PREFERENCES_PATH.parent.mkdir(parents=True, exist_ok=True)
            temporary = PREFERENCES_PATH.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(preferences, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, PREFERENCES_PATH)
            LOGGER.info("Interface language changed: %s", language)
            return self._locale_payload(language)

        return self._result(action)

    def minimize_window(self) -> None:
        if self._window:
            self._window.minimize()

    def toggle_maximize_window(self) -> bool:
        if not self._window:
            return False
        if self._maximized:
            self._window.restore()
        else:
            self._window.maximize()
        self._maximized = not self._maximized
        return self._maximized

    def close_window(self) -> None:
        if self._window:
            self._window.destroy()

    def resize_window(self, width: int, height: int, edge: str) -> None:
        if not self._window or self._maximized:
            return
        width = max(980, int(width))
        height = max(650, int(height))
        horizontal_anchor = FixPoint.EAST if "w" in edge else FixPoint.WEST
        vertical_anchor = FixPoint.SOUTH if "n" in edge else FixPoint.NORTH
        self._window.resize(width, height, horizontal_anchor | vertical_anchor)

    def choose_folder(self, kind: str) -> dict[str, Any]:
        def action() -> str | None:
            installation = engine.active_installation()
            initial = ""
            if installation:
                initial = installation.get("comfy_root" if kind == "comfy" else "archive_root", "")
            selected = self._window.create_file_dialog(
                webview.FileDialog.FOLDER,
                directory=initial,
                allow_multiple=False,
            )
            return selected[0] if selected else None

        return self._result(action)

    def choose_workflow(self) -> dict[str, Any]:
        def action() -> str | None:
            installation = engine.active_installation()
            initial = ""
            if installation:
                candidate = Path(installation["comfy_root"]) / "user" / "default" / "workflows"
                initial = str(candidate if candidate.is_dir() else Path(installation["comfy_root"]))
            selected = self._window.create_file_dialog(
                webview.FileDialog.OPEN,
                directory=initial,
                allow_multiple=False,
                file_types=(
                    "ComfyUI Workflows (*.json;*.png)",
                    "ComfyUI Workflow (*.json)",
                    "ComfyUI Image (*.png)",
                    "All files (*.*)",
                ),
            )
            return selected[0] if selected else None

        return self._result(action)

    def choose_archive(self) -> dict[str, Any]:
        def action() -> str | None:
            installation = engine.active_installation()
            initial = installation.get("archive_root", "") if installation else ""
            selected = self._window.create_file_dialog(
                webview.FileDialog.OPEN,
                directory=initial,
                allow_multiple=False,
                file_types=("ComfyShelf package (*.zip)",),
            )
            return selected[0] if selected else None

        return self._result(action)

    def save_settings(self, comfy_root: str, archive_root: str, link_mode: str = "auto") -> dict[str, Any]:
        def action() -> dict[str, Any]:
            result = self._save_settings(Path(comfy_root), Path(archive_root), link_mode)
            with self._state_lock:
                self._state_cache = None
            LOGGER.info("ComfyUI and archive paths saved (link_mode=%s)", link_mode)
            return result

        return self._result(action)

    @staticmethod
    def _save_settings(comfy_root: Path, archive_root: Path, link_mode: str = "auto") -> dict[str, Any]:
        _, validation, saved = engine.set_installation_config(
            "default",
            "ComfyUI",
            comfy_root,
            archive_root,
            link_mode=link_mode,
        )
        return {"validation": validation, "config_path": str(saved)}

    def get_state(self) -> dict[str, Any]:
        return self._result(self._start_state_scan)

    def _loading_state(self, installation: dict[str, Any]) -> dict[str, Any]:
        with self._state_lock:
            if self._state_cache:
                state = dict(self._state_cache)
                state["loading"] = True
                state.pop("scan_error", None)
                return state
        return {
            "configured": True,
            "installation": installation,
            "validation": {"comfy_free": None},
            "packages": [],
            "summary": {"loaded": 0, "archived": 0, "partial": 0, "archive_bytes": 0},
            "scan_warnings": [],
            "loading": True,
        }

    def _start_state_scan(self) -> dict[str, Any]:
        installation = engine.active_installation()
        if not installation:
            state = {
                "configured": False,
                "config_path": str(engine.default_config_path()),
                "packages": [],
                "loading": False,
            }
            with self._state_lock:
                self._state_cache = state
            return state
        with self._state_lock:
            running = self._scan_thread is not None and self._scan_thread.is_alive()
            if not running:
                self._scan_thread = threading.Thread(
                    target=self._refresh_state_worker,
                    name="library-scan",
                    daemon=True,
                )
                self._scan_thread.start()
        return self._loading_state(installation)

    def _refresh_state_worker(self) -> None:
        started = time.perf_counter()
        LOGGER.info("Library scan started")
        try:
            state = self._build_state()
            state["loading"] = False
            with self._state_lock:
                self._state_cache = state
            LOGGER.info(
                "Library scan completed: %d package(s), %d warning(s), %.2fs",
                len(state.get("packages", [])),
                len(state.get("scan_warnings", [])),
                time.perf_counter() - started,
            )
        except Exception as exc:
            LOGGER.exception("Library scan failed after %.2fs", time.perf_counter() - started)
            installation = engine.active_installation()
            state = self._loading_state(installation) if installation else {
                "configured": False,
                "packages": [],
            }
            state["loading"] = False
            state["scan_error"] = str(exc)
            with self._state_lock:
                self._state_cache = state
        self._emit_js("window.workflowStateUpdated", state)

    def _build_state(self) -> dict[str, Any]:
        installation = engine.active_installation()
        if not installation:
            return {"configured": False, "packages": []}
        comfy_root = Path(installation["comfy_root"])
        archive_root = Path(installation["archive_root"])
        self._emit_scan_progress("drives")
        validation = engine.validate_installation_paths(comfy_root, archive_root)
        warnings: list[dict[str, str]] = []
        self._emit_scan_progress("archive")
        packages = self._scan_packages(
            comfy_root,
            archive_root,
            self._emit_scan_progress,
            warnings,
        ) if archive_root.is_dir() else []
        return {
            "configured": True,
            "installation": installation,
            "validation": validation,
            "packages": packages,
            "scan_warnings": warnings,
            "summary": {
                "loaded": sum(item["state"] == "loaded" for item in packages),
                "archived": sum(item["state"] == "archived" for item in packages),
                "partial": sum(item["state"] == "partial" for item in packages),
                "archive_bytes": sum(item["archive_size"] for item in packages),
                "shared_packages": sum(bool(item.get("deduplicated")) for item in packages),
                "logical_model_bytes": sum(int(item.get("model_bytes", 0)) for item in packages),
            },
        }

    @staticmethod
    def _scan_packages(
        comfy_root: Path,
        archive_root: Path,
        progress: Callable[..., None] | None = None,
        warnings: list[dict[str, str]] | None = None,
    ) -> list[dict[str, Any]]:
        packages: list[dict[str, Any]] = []
        verification_stamps: dict[str, Any] = {}
        archive_paths = list(archive_root.glob("*.zip"))
        LOGGER.info("Archive directory contains %d ZIP package(s)", len(archive_paths))
        for index, archive_path in enumerate(archive_paths, 1):
            if progress:
                progress(
                    "package",
                    archive_path.name,
                    index,
                    len(archive_paths),
                )
            package_started = time.perf_counter()
            try:
                manifest = engine.load_package_manifest(archive_path)
            except (OSError, ValueError) as exc:
                LOGGER.warning("Package skipped: %s | %s", archive_path.name, exc)
                if warnings is not None:
                    warnings.append({"name": archive_path.name, "error": str(exc)})
                continue
            model_files = [
                entry for entry in manifest.get("files", []) if entry.get("role") == "model"
            ]
            assets_by_path = {
                item.get("archive_path"): item for item in manifest.get("assets", [])
            }
            models: list[dict[str, Any]] = []
            present = 0
            for entry in model_files:
                link_type = None
                try:
                    target = engine._safe_model_target(comfy_root, str(entry["path"]))
                    exists = target.is_file() and target.stat().st_size == int(entry.get("size", -1))
                    if exists:
                        stat_val = target.stat()
                        is_sym = target.is_symlink()
                        is_hard = not is_sym and getattr(stat_val, "st_nlink", 1) > 1
                        link_type = "symlink" if is_sym else ("hardlink" if is_hard else "copy")
                except (OSError, ValueError):
                    target = comfy_root / str(entry.get("path", ""))
                    exists = False
                if exists:
                    present += 1
                asset = assets_by_path.get(entry.get("path"), {})
                models.append(
                    {
                        "path": str(target),
                        "archive_path": entry.get("path"),
                        "filename": asset.get("filename") or Path(str(entry.get("path"))).name,
                        "kind": asset.get("kind", "model"),
                        "size": int(entry.get("size", 0)),
                        "present": exists,
                        "shared": entry.get("storage") == engine.SHARED_OBJECT_STORAGE,
                        "sha256": entry.get("sha256"),
                        "detection": asset.get("detection"),
                        "resolution": asset.get("resolution"),
                        "link_type": link_type,
                    }
                )
            if not model_files or present == len(model_files):
                state = "loaded"
            elif present == 0:
                state = "archived"
            else:
                state = "partial"
            packages.append(
                {
                    # The ZIP name is the portable, user-controlled display name.
                    # The manifest remains untouched so renaming a multi-GiB package is instant.
                    "name": archive_path.stem,
                    "path": str(archive_path.resolve()),
                    "archive_size": archive_path.stat().st_size,
                    "modified": archive_path.stat().st_mtime,
                    "complete": bool(manifest.get("complete")),
                    "state": state,
                    "present_models": present,
                    "model_count": len(model_files),
                    "model_bytes": sum(int(item.get("size", 0)) for item in model_files),
                    "models": models,
                    "missing": manifest.get("missing", []),
                    "parser_warnings": engine.package_parser_warnings(archive_path, manifest),
                    "custom_nodes": engine.enrich_custom_nodes(
                        manifest.get("custom_nodes", []), comfy_root
                    ),
                    "workflow": manifest.get("workflow", {}),
                    "storage_mode": manifest.get("storage_mode", "embedded-portable"),
                    "deduplicated": manifest.get("storage_mode") == "shared-object-pool",
                    "has_thumbnail": bool(
                        manifest.get("thumbnail")
                        or any(
                            f.get("role") == "thumbnail"
                            or Path(str(f.get("path", ""))).suffix.casefold() in {".png", ".jpg", ".jpeg", ".webp"}
                            for f in manifest.get("files", [])
                        )
                    ),
                    "verification": verification_cache.status(archive_path, manifest, verification_stamps),
                }
            )
            LOGGER.info(
                "Package indexed: %s | %d model(s) | %.3fs",
                archive_path.name,
                len(model_files),
                time.perf_counter() - package_started,
            )
        WorkflowSwitcherApi._annotate_shared_files(packages)
        packages.sort(key=lambda item: (-item["modified"], item["name"].casefold()))
        return packages

    @staticmethod
    def _annotate_shared_files(packages: list[dict[str, Any]]) -> None:
        """Index recorded hashes only; never read model payloads during startup."""
        index: dict[str, dict[str, dict[str, str]]] = {}
        for package in packages:
            for model in package["models"]:
                digest = str(model.get("sha256") or "").lower()
                if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                    continue
                index.setdefault(digest, {})[package["path"]] = {
                    "name": package["name"], "path": package["path"],
                }
        for package in packages:
            for model in package["models"]:
                digest = str(model.get("sha256") or "").lower()
                model["library_shared_by"] = sorted(
                    (reference for path, reference in index.get(digest, {}).items() if path != package["path"]),
                    key=lambda reference: (reference["name"].casefold(), reference["path"].casefold()),
                )

    def _emit_scan_progress(
        self,
        stage: str,
        package: str = "",
        current: int = 0,
        total: int = 0,
    ) -> None:
        self._emit_js("window.workflowScanProgress", {
            "stage": stage,
            "package": package,
            "current": current,
            "total": total,
        })

    def _emit_js(self, function_name: str, data: dict[str, Any]) -> None:
        if not self._window:
            return
        payload = json.dumps(data, ensure_ascii=False)
        try:
            self._window.evaluate_js(f"{function_name}({payload})")
        except Exception:
            LOGGER.debug("Could not send UI status", exc_info=True)

    def rename_archive(self, archive_path: str, new_name: str) -> dict[str, Any]:
        def action() -> dict[str, Any]:
            installation = engine.active_installation()
            if not installation:
                raise ValueError("Bitte zuerst ComfyUI- und Auslagerungsordner einrichten.")
            manifest = engine.load_package_manifest(Path(archive_path))
            previous_check = verification_cache.prepare_rename(Path(archive_path), manifest)
            result = engine.rename_package_archive(
                Path(archive_path),
                Path(installation["archive_root"]),
                new_name,
            )
            if result["changed"]:
                verification_cache.finish_rename(Path(result["path"]), manifest, previous_check)
                self._record_history(
                    "rename",
                    result["name"],
                    f'Archive renamed: {result["old_name"]} → {result["name"]}',
                )
            return result

        return self._result(action)

    def inspect_archive_deletion(self, archive_path: str) -> dict[str, Any]:
        def action() -> dict[str, Any]:
            installation = engine.active_installation()
            if not installation:
                raise ValueError("Bitte zuerst ComfyUI- und Auslagerungsordner einrichten.")
            return engine.inspect_archive_deletion(
                Path(archive_path),
                Path(installation["archive_root"]),
            )

        return self._result(action)

    def delete_archive(
        self,
        archive_path: str,
        send_to_trash: bool = True,
        delete_orphaned_models: bool = False,
    ) -> dict[str, Any]:
        def action() -> dict[str, Any]:
            installation = engine.active_installation()
            if not installation:
                raise ValueError("Bitte zuerst ComfyUI- und Auslagerungsordner einrichten.")
            target_path = Path(archive_path)
            result = engine.delete_package_archive(
                target_path,
                Path(installation["archive_root"]),
                send_to_trash=send_to_trash,
                delete_orphaned_models=delete_orphaned_models,
            )
            verification_cache.discard_record(target_path)
            mode = "Papierkorb" if send_to_trash else "unwiderruflich"
            detail = f'Workflow-Archiv gelöscht ({mode}): {result["name"]}'
            if result.get("orphaned_deleted_count"):
                detail += f' (+ {result["orphaned_deleted_count"]} verwaiste Modelle)'
            self._record_history("delete", result["name"], detail)
            return result

        return self._result(action)

    def get_archive_thumbnail(self, archive_path: str) -> dict[str, Any]:
        def action() -> str | None:
            return engine.get_package_thumbnail(Path(archive_path))

        return self._result(action)

    def inspect_for_pack(self, workflow_path: str) -> dict[str, Any]:
        return self._operation(lambda: self._inspect_for_pack(Path(workflow_path)))

    @staticmethod
    def _inspect_for_pack(workflow_path: Path) -> dict[str, Any]:
        installation = engine.active_installation()
        if not installation:
            raise ValueError("Bitte zuerst ComfyUI- und Auslagerungsordner einrichten.")
        comfy_root = Path(installation["comfy_root"])
        archive_root = Path(installation["archive_root"])
        report = engine.inspect_workflow(workflow_path, comfy_root, False)
        missing = [
            {
                "kind": asset["kind"],
                "name": asset["filename"],
            }
            for asset in report["assets"]
            if not asset["resolved_path"]
        ]
        return {
            "workflow": report["workflow"],
            "workflow_path": str(workflow_path),
            "output_path": str(archive_root / f"{workflow_path.stem}.zip"),
            "assets": report["assets"],
            "custom_nodes": report["custom_nodes"],
            "missing": missing,
            "parser_warnings": report.get("parser_warnings", []),
        }

    def pack_workflow(self, workflow_path: str, allow_missing: bool, force: bool) -> dict[str, Any]:
        def action() -> dict[str, Any]:
            result = self._pack_workflow(
                Path(workflow_path), allow_missing, force, self._progress
            )
            self._record_history(
                "archive", result.get("package"), "Workflow package created",
                int(result.get("archive_size", 0)),
            )
            return result

        return self._operation(action)

    @staticmethod
    def _pack_workflow(
        workflow_path: Path,
        allow_missing: bool,
        force: bool,
        progress: engine.ProgressCallback = None,
    ) -> dict[str, Any]:
        installation = engine.active_installation()
        if not installation:
            raise ValueError("Bitte zuerst ComfyUI- und Auslagerungsordner einrichten.")
        output = Path(installation["archive_root"]) / f"{workflow_path.stem}.zip"
        result = engine.build_package(
            workflow_path,
            Path(installation["comfy_root"]),
            output,
            allow_missing,
            force,
            progress=progress,
            shared_pool=True,
        )
        result["archive_size"] = output.stat().st_size
        return result

    def verify_archive(self, archive_path: str) -> dict[str, Any]:
        def action() -> dict[str, Any]:
            result = engine.verify_package(Path(archive_path), self._progress)
            with self._state_lock:
                for package in (self._state_cache or {}).get("packages", []):
                    if Path(package["path"]).resolve() == Path(archive_path).resolve():
                        package["verification"] = result["verification"]
            return result

        return self._operation(action)

    def inspect_archive_import(self, archive_path: str) -> dict[str, Any]:
        def action() -> dict[str, Any]:
            installation = engine.active_installation()
            if not installation:
                raise ValueError("Bitte zuerst ComfyUI- und Auslagerungsordner einrichten.")
            return engine.inspect_import_package(
                Path(archive_path),
                Path(installation["archive_root"]),
                Path(installation["comfy_root"]),
            )

        return self._operation(action)

    def scan_archive_dependencies(self, archive_path: str) -> dict[str, Any]:
        def action():
            installation = engine.active_installation()
            if not installation:
                raise ValueError("Configure ComfyUI and archive folders first.")
            return dependency_scan.scan_dependencies(
                Path(archive_path), Path(installation["comfy_root"]),
                Path(installation["archive_root"]), self._progress)
        return self._operation(action)

    def complete_archive_dependencies(self, archive_path: str, scan_token: str) -> dict[str, Any]:
        def action():
            installation = engine.active_installation()
            if not installation:
                raise ValueError("Configure ComfyUI and archive folders first.")
            result = dependency_scan.complete_dependencies(
                Path(archive_path), Path(installation["comfy_root"]),
                Path(installation["archive_root"]), scan_token, self._progress)
            self._record_history("dependencies", Path(archive_path).stem,
                                 f"Added {result['added']} dependencies; backup: {result['backup']}",
                                 result["new_object_bytes"])
            return result
        return self._operation(action)

    def import_archive(
        self,
        archive_path: str,
        allow_missing: bool,
        force: bool,
        load_after_import: bool,
    ) -> dict[str, Any]:
        def action() -> dict[str, Any]:
            installation = engine.active_installation()
            if not installation:
                raise ValueError("Bitte zuerst ComfyUI- und Auslagerungsordner einrichten.")
            link_mode = str(installation.get("link_mode") or "auto")
            result = engine.import_package(
                Path(archive_path),
                Path(installation["comfy_root"]),
                Path(installation["archive_root"]),
                allow_missing,
                force,
                load_after_import,
                progress=self._progress,
                link_mode=link_mode,
            )
            storage = engine.deduplicate_package(Path(result["output_path"]), self._progress)
            result["storage"] = storage
            self._record_history(
                "import_load" if load_after_import else "import",
                result.get("package"),
                "Package imported and loaded into ComfyUI" if load_after_import else "Package resealed",
                sum(int(item.get("size", 0)) for item in (result.get("restore") or {}).get("candidates", [])),
            )
            return result

        return self._operation(action)

    def optimize_archive(self, archive_path: str) -> dict[str, Any]:
        def action() -> dict[str, Any]:
            result = engine.deduplicate_package(Path(archive_path), self._progress)
            self._record_history(
                "optimize",
                result.get("package"),
                "Package moved into the shared model pool",
                int(result.get("reused_object_bytes", 0)),
            )
            return result

        return self._operation(action)

    def inspect_library_optimization(self) -> dict[str, Any]:
        def action() -> dict[str, Any]:
            installation = engine.active_installation()
            if not installation:
                raise ValueError("Bitte zuerst ComfyUI- und Auslagerungsordner einrichten.")
            return engine.inspect_library_optimization(Path(installation["archive_root"]))

        return self._operation(action)

    def optimize_library(self) -> dict[str, Any]:
        def action() -> dict[str, Any]:
            installation = engine.active_installation()
            if not installation:
                raise ValueError("Bitte zuerst ComfyUI- und Auslagerungsordner einrichten.")
            result = engine.optimize_library(
                Path(installation["archive_root"]),
                self._progress,
            )
            self._record_history(
                "optimize_library",
                "Library",
                f'{result["optimized_count"]} package(s) moved into the shared model pool',
                int(result.get("reused_object_bytes", 0)),
            )
            return result

        return self._operation(action)

    def choose_export_destination(self, archive_path: str) -> dict[str, Any]:
        def action() -> str | None:
            source = Path(archive_path)
            initial = self._last_export_dir or source.parent / "Portable Exports"
            if not initial.is_dir():
                initial = source.parent
            name = f"{source.stem} - portable.zip"
            suffix = 2
            while (initial / name).exists():
                name = f"{source.stem} - portable ({suffix}).zip"
                suffix += 1
            selected = self._window.create_file_dialog(
                webview.FileDialog.SAVE, directory=str(initial), save_filename=name,
                allow_multiple=False, file_types=("Portable workflow ZIP (*.zip)",),
            )
            if not selected:
                return None
            return str(selected if isinstance(selected, str) else selected[0])

        return self._result(action)

    def preview_portable_archive(self, archive_path: str, output_path: str) -> dict[str, Any]:
        return self._result(lambda: engine.preview_portable_export(Path(archive_path), Path(output_path)))

    def export_portable_archive(
        self, archive_path: str, output_path: str, allow_incomplete: bool = False,
    ) -> dict[str, Any]:
        def action() -> dict[str, Any]:
            source = Path(archive_path)
            result = engine.export_portable_package(
                source, Path(output_path), progress=self._progress, allow_incomplete=allow_incomplete,
            )
            self._last_export_dir = Path(result["output_path"]).parent
            self._record_history(
                "export",
                result.get("package"),
                f'Portable package exported: {Path(result["output_path"]).name}',
                int(result.get("archive_size", 0)),
            )
            return result

        return self._operation(action)

    def preview_action(self, action_name: str, archive_path: str) -> dict[str, Any]:
        return self._operation(
            lambda: self._run_action(
                action_name, Path(archive_path), False, False, False, self._progress
            )
        )

    def execute_action(
        self,
        action_name: str,
        archive_path: str,
        allow_incomplete: bool,
        allow_shared: bool,
    ) -> dict[str, Any]:
        def action() -> dict[str, Any]:
            result = self._run_action(
                action_name,
                Path(archive_path),
                True,
                allow_incomplete,
                allow_shared,
                self._progress,
            )
            changed = result.get("restored", []) if action_name == "restore" else result.get("removed", [])
            bytes_changed = sum(int(item.get("size", 0)) for item in result.get("candidates", []))
            self._record_history(
                action_name,
                result.get("package"),
                f"{len(changed)} file(s) {'loaded' if action_name == 'restore' else 'unloaded'}",
                bytes_changed,
            )
            return result

        return self._operation(action)

    def preview_switch(self, archive_path: str) -> dict[str, Any]:
        return self._operation(
            lambda: self._run_switch(Path(archive_path), False, False, False)
        )

    def execute_switch(
        self,
        archive_path: str,
        allow_incomplete: bool,
        allow_shared: bool,
    ) -> dict[str, Any]:
        def action() -> dict[str, Any]:
            result = self._run_switch(
                Path(archive_path), True, allow_incomplete, allow_shared
            )
            self._record_history(
                "switch",
                result.get("package"),
                f"Workflow switched: {len(result.get('removed', []))} old file(s) unloaded",
                int(result.get("bytes_required", 0)) + int(result.get("bytes_reclaimable", 0)),
            )
            return result

        return self._operation(action)

    def _run_switch(
        self,
        archive_path: Path,
        confirm: bool,
        allow_incomplete: bool,
        allow_shared: bool,
    ) -> dict[str, Any]:
        installation = engine.active_installation()
        if not installation:
            raise ValueError("Bitte zuerst ComfyUI- und Auslagerungsordner einrichten.")
        comfy_root = Path(installation["comfy_root"])
        archive_root = Path(installation["archive_root"])
        other_archives = [
            Path(item["path"])
            for item in self._scan_packages(comfy_root, archive_root)
            if item["path"].casefold() != str(archive_path.resolve()).casefold()
            and item["state"] in {"loaded", "partial"}
        ]
        link_mode = str(installation.get("link_mode") or "auto")
        return engine.switch_package(
            archive_path,
            other_archives,
            comfy_root,
            confirm,
            allow_incomplete,
            allow_shared,
            self._progress,
            link_mode=link_mode,
        )

    @staticmethod
    def _run_action(
        action_name: str,
        archive_path: Path,
        confirm: bool,
        allow_incomplete: bool,
        allow_shared: bool,
        progress: engine.ProgressCallback = None,
    ) -> dict[str, Any]:
        installation = engine.active_installation()
        if not installation:
            raise ValueError("Bitte zuerst ComfyUI- und Auslagerungsordner einrichten.")
        comfy_root = Path(installation["comfy_root"])
        if action_name == "restore":
            link_mode = str(installation.get("link_mode") or "auto")
            return engine.restore_package(
                archive_path, comfy_root, confirm, allow_incomplete, progress, link_mode=link_mode
            )
        if action_name == "unload":
            return engine.unload_package(
                archive_path,
                comfy_root,
                confirm,
                allow_incomplete,
                allow_shared,
                progress,
            )
        raise ValueError(f"Unknown action: {action_name}")

    def show_workflow_file(self, archive_path: str) -> dict[str, Any]:
        def action():
            installation = engine.active_installation()
            if not installation:
                raise ValueError("Configure a ComfyUI installation first")
            report = workflow_file.inspect(Path(archive_path), Path(installation["comfy_root"]))
            if report["exists"]:
                _reveal_workflow_file(Path(report["path"]))
                LOGGER.info("Workflow JSON shown in Explorer: %s", report["path"])
            return report
        return self._result(action)

    def restore_workflow_file(self, archive_path: str, token: str) -> dict[str, Any]:
        def action():
            installation = engine.active_installation()
            if not installation:
                raise ValueError("Configure a ComfyUI installation first")
            report = workflow_file.restore(Path(archive_path), Path(installation["comfy_root"]), token)
            LOGGER.info("Workflow JSON restored without models: %s", report["path"])
            _reveal_workflow_file(Path(report["path"]))
            return report
        return self._result(action)

    def open_archive_folder(self) -> dict[str, Any]:
        def action() -> bool:
            installation = engine.active_installation()
            if not installation:
                raise ValueError("Kein Auslagerungsordner konfiguriert.")
            path = Path(installation["archive_root"])
            if os.name == "nt":
                os.startfile(path)  # type: ignore[attr-defined]
            else:
                raise ValueError("Ordneröffnung ist in diesem Prototyp nur unter Windows verfügbar.")
            return True

        return self._result(action)

    def open_diagnostic_log(self) -> dict[str, Any]:
        def action() -> bool:
            LOGGER.info("Diagnostic log opened from the interface")
            if os.name == "nt":
                os.startfile(LOG_PATH)  # type: ignore[attr-defined]
            else:
                raise ValueError("The diagnostic log can currently only be opened on Windows.")
            return True

        return self._result(action)

    def open_external_url(self, url: str) -> dict[str, Any]:
        def action() -> bool:
            safe_url = str(url).strip()
            parsed = urllib.parse.urlparse(safe_url)
            if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
                raise ValueError("Ungültige oder unsichere URL.")
            webbrowser.open(safe_url)
            return True

        return self._result(action)


def _watch_frontend_startup(api: WorkflowSwitcherApi, app_closed: threading.Event) -> None:
    """Leave a precise diagnostic if the native bridge never finishes booting."""
    deadline = time.monotonic() + 12.0
    while time.monotonic() < deadline:
        if api._wait_for_frontend(0.25):
            LOGGER.info("Startup watchdog: frontend reported ready")
            return
        if app_closed.is_set():
            return
    LOGGER.error(
        "Startup watchdog: frontend did not report ready within 12 seconds; "
        "the pywebview bridge or frontend bootstrap may be blocked"
    )


def main() -> int:
    LOGGER.info("ComfyShelf starting | Python %s", sys.version.split()[0])
    html_path = WEB_DIR / "index.html"
    if not html_path.is_file():
        print(f"GUI file is missing: {html_path}", file=sys.stderr)
        return 2
    api = WorkflowSwitcherApi()
    initial_width, initial_height = _initial_window_size()
    window = webview.create_window(
        "ComfyShelf — Workflow Switcher",
        url=html_path.as_uri(),
        js_api=api,
        width=initial_width,
        height=initial_height,
        min_size=(980, 650),
        resizable=True,
        frameless=True,
        easy_drag=False,
        shadow=True,
        background_color="#111519",
        text_select=False,
    )
    api._attach_window(window)
    LOGGER.info("WebView window created; loading interface")
    app_closed = threading.Event()
    threading.Thread(
        target=_watch_frontend_startup,
        args=(api, app_closed),
        name="startup-watchdog",
        daemon=True,
    ).start()
    try:
        webview.start(debug=False, private_mode=False, storage_path=str(APP_DIR / ".gui-state"))
    finally:
        app_closed.set()
    LOGGER.info("ComfyShelf closed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException as exc:
        details = traceback.format_exc()
        log_path = APP_DIR / "workflow-switcher-error.log"
        try:
            log_path.write_text(details, encoding="utf-8")
        except OSError:
            pass
        message = f"The GUI could not be started.\n\n{exc}\n\nDetails: {log_path}"
        if os.name == "nt":
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, message, "ComfyShelf could not start", 0x10)
        else:
            print(details, file=sys.stderr)
        raise SystemExit(1)
