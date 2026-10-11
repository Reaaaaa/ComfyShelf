#!/usr/bin/env python3
"""Build and package ComfyShelf for Windows releases.

Produces:
1. Portable distribution folder: dist/ComfyShelf/
2. Portable ZIP archive: dist/ComfyShelf-v<VERSION>-windows-portable.zip
3. Inno Setup installer: dist/ComfyShelf-v<VERSION>-windows-setup.exe (if Inno Setup compiler is present)
4. Checksum file: dist/SHA256SUMS.txt
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VERSION = "1.0.0"


def find_iscc() -> Path | None:
    """Find the Inno Setup Compiler (ISCC.exe) executable."""
    on_path = shutil.which("iscc.exe") or shutil.which("iscc")
    if on_path:
        return Path(on_path)

    common_paths = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Inno Setup 6" / "ISCC.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Inno Setup 6" / "ISCC.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 7" / "ISCC.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Inno Setup 7" / "ISCC.exe",
    ]
    for candidate in common_paths:
        if candidate.is_file():
            return candidate

    # Search registry if on Windows
    if sys.platform == "win32":
        try:
            import winreg

            for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                for sub in (
                    r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
                    r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
                ):
                    try:
                        with winreg.OpenKey(root, sub) as key:
                            for i in range(winreg.QueryInfoKey(key)[0]):
                                try:
                                    kname = winreg.EnumKey(key, i)
                                    with winreg.OpenKey(key, kname) as subkey:
                                        vals = {}
                                        for j in range(winreg.QueryInfoKey(subkey)[1]):
                                            vname, val, _ = winreg.EnumValue(subkey, j)
                                            vals[vname] = val
                                        display = str(vals.get("DisplayName", ""))
                                        if "Inno Setup" in display:
                                            loc = vals.get("InstallLocation") or vals.get("Inno Setup: App Path")
                                            if loc:
                                                cand = Path(loc) / "ISCC.exe"
                                                if cand.is_file():
                                                    return cand
                                except OSError:
                                    pass
                    except OSError:
                        pass
        except Exception:
            pass

    return None


def sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def copy_folder_contents(src: Path, dst: Path, ignore_patterns: set[str] | None = None) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    patterns = ignore_patterns or set()
    for item in src.iterdir():
        if item.name in patterns or item.name.startswith("."):
            continue
        target = dst / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git*"))
        else:
            shutil.copy2(item, target)


def build(version: str = DEFAULT_VERSION, skip_pyinstaller: bool = False, skip_zip: bool = False, skip_installer: bool = False) -> int:
    start_time = time.time()
    dist_dir = PROJECT_ROOT / "dist"
    build_dir = PROJECT_ROOT / "build"
    app_dist_dir = dist_dir / "ComfyShelf"

    print("=" * 60)
    print(f"Building ComfyShelf v{version}")
    print(f"Project root: {PROJECT_ROOT}")
    print(f"Output dist:  {dist_dir}")
    print("=" * 60)

    # 1. Run PyInstaller
    if not skip_pyinstaller:
        print("\n[1/4] Running PyInstaller to create frozen executable...")
        pyinstaller_exe = sys.executable.replace("python.exe", "pyinstaller.exe")
        if not Path(pyinstaller_exe).is_file():
            pyinstaller_exe = shutil.which("pyinstaller") or "pyinstaller"

        cmd = [
            str(pyinstaller_exe),
            "--noconsole",
            "--name", "ComfyShelf",
            "--icon", str(PROJECT_ROOT / "assets" / "comfyshelf.ico"),
            "--onedir",
            "--clean",
            "--noconfirm",
            "--collect-all", "pywebview",
            "--collect-all", "pythonnet",
            "--hidden-import", "webview.platforms.winforms",
            str(PROJECT_ROOT / "workflow_switcher_gui.py"),
        ]
        print(f"Executing: {' '.join(cmd)}")
        result = subprocess.run(cmd, cwd=PROJECT_ROOT)
        if result.returncode != 0:
            print("ERROR: PyInstaller build failed!", file=sys.stderr)
            return result.returncode
    else:
        print("\n[1/4] Skipping PyInstaller step (--skip-pyinstaller)")

    if not (app_dist_dir / "ComfyShelf.exe").is_file():
        print(f"ERROR: Expected executable not found at {app_dist_dir / 'ComfyShelf.exe'}", file=sys.stderr)
        return 1

    # 2. Bundle static assets, GUI files, configs, documentation
    print("\n[2/4] Copying required runtime assets and configuration...")
    # Copy gui/
    gui_target = app_dist_dir / "gui"
    if gui_target.exists():
        shutil.rmtree(gui_target)
    copy_folder_contents(PROJECT_ROOT / "gui", gui_target)
    print("  -> Copied gui/ resources and locales")

    # Copy assets/
    assets_target = app_dist_dir / "assets"
    if assets_target.exists():
        shutil.rmtree(assets_target)
    copy_folder_contents(PROJECT_ROOT / "assets", assets_target)
    print("  -> Copied assets/ (icons, logos, icons/)")

    # Copy rules and documentation
    for file_name in ("node-rules.json", "node-rules.example.json", "README.md", "LICENSE"):
        src_file = PROJECT_ROOT / file_name
        if src_file.is_file():
            shutil.copy2(src_file, app_dist_dir / file_name)
            print(f"  -> Copied {file_name}")

    # Clean any runtime artifacts that might have been created during testing
    for dirty in (app_dist_dir / ".gui-state", app_dist_dir / "workflow-switcher-error.log"):
        if dirty.is_dir():
            shutil.rmtree(dirty, ignore_errors=True)
        elif dirty.is_file():
            dirty.unlink(missing_ok=True)

    # 3. Create Portable ZIP
    zip_path = dist_dir / f"ComfyShelf-v{version}-windows-portable.zip"
    if not skip_zip:
        print(f"\n[3/4] Creating portable ZIP archive: {zip_path.name}...")
        if zip_path.exists():
            zip_path.unlink()
        
        # Archive with top-level folder 'ComfyShelf'
        shutil.make_archive(
            base_name=str(dist_dir / f"ComfyShelf-v{version}-windows-portable"),
            format="zip",
            root_dir=dist_dir,
            base_dir="ComfyShelf",
        )
        zip_size_mb = zip_path.stat().st_size / (1024 * 1024)
        print(f"  -> Successfully generated {zip_path.name} ({zip_size_mb:.2f} MB)")
    else:
        print("\n[3/4] Skipping portable ZIP archive creation (--skip-zip)")

    # 4. Compile Inno Setup Installer
    installer_exe_path = dist_dir / f"ComfyShelf-v{version}-windows-setup.exe"
    if not skip_installer:
        print("\n[4/4] Checking Inno Setup Compiler...")
        iscc_path = find_iscc()
        if iscc_path:
            print(f"  -> Found Inno Setup Compiler: {iscc_path}")
            iss_script = PROJECT_ROOT / "scripts" / "installer.iss"
            iss_cmd = [
                str(iscc_path),
                f"/DMyAppVersion={version}",
                str(iss_script),
            ]
            print(f"  -> Compiling Windows Installer: {' '.join(iss_cmd)}")
            iss_result = subprocess.run(iss_cmd, cwd=PROJECT_ROOT / "scripts")
            if iss_result.returncode == 0 and installer_exe_path.is_file():
                inst_size_mb = installer_exe_path.stat().st_size / (1024 * 1024)
                print(f"  -> Successfully built installer: {installer_exe_path.name} ({inst_size_mb:.2f} MB)")
            else:
                print("WARNING: Inno Setup compilation failed or output not found.", file=sys.stderr)
        else:
            print("  -> Inno Setup Compiler (ISCC.exe) not found on PATH or common locations.")
            print("     You can compile 'scripts/installer.iss' manually using Inno Setup 6.")
            print("     Install via: winget install JRSoftware.InnoSetup")
    else:
        print("\n[4/4] Skipping installer compilation (--skip-installer)")

    # 5. Checksums
    checksum_lines = []
    print("\nRelease Artifacts:")
    for artifact in (installer_exe_path, zip_path):
        if artifact.is_file():
            csum = sha256_of_file(artifact)
            size_mb = artifact.stat().st_size / (1024 * 1024)
            checksum_lines.append(f"{csum}  {artifact.name}")
            print(f"  * {artifact.name:<45} ({size_mb:>6.2f} MB)  SHA256: {csum}")

    if checksum_lines:
        csum_file = dist_dir / "SHA256SUMS.txt"
        csum_file.write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")
        print(f"\nChecksums saved to {csum_file.name}")

    elapsed = time.time() - start_time
    print(f"\nBuild complete in {elapsed:.1f}s.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Build and package ComfyShelf for Windows releases.")
    parser.add_argument("--version", default=DEFAULT_VERSION, help=f"Release version string (default: {DEFAULT_VERSION})")
    parser.add_argument("--skip-pyinstaller", action="store_true", help="Skip PyInstaller build step and use existing dist/ComfyShelf")
    parser.add_argument("--skip-zip", action="store_true", help="Skip creating portable ZIP archive")
    parser.add_argument("--skip-installer", action="store_true", help="Skip compiling Inno Setup installer")
    args = parser.parse_args()

    return build(
        version=args.version,
        skip_pyinstaller=args.skip_pyinstaller,
        skip_zip=args.skip_zip,
        skip_installer=args.skip_installer,
    )


if __name__ == "__main__":
    raise SystemExit(main())
