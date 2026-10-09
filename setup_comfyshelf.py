"""Consent-based Windows source setup. Never imports or configures ComfyUI."""
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
from datetime import datetime


TEXT = {
    "en": {
        "intro": "ComfyShelf setup\n\nThis prepares a separate Python environment inside the app folder.\nAfter your confirmation it downloads pywebview and its dependencies from PyPI.\nIt does not change ComfyUI, your models or your global Python packages.\nPython and Windows components are not installed automatically.\nClose ComfyShelf before continuing. Administrator rights are not needed.",
        "folder": "App folder: ",
        "confirm": "Set up ComfyShelf now? [y/N]: ",
        "cancel": "Setup cancelled. Nothing was installed.",
        "working": "Preparing ComfyShelf. Download and installation details follow below...",
        "success": "\nPython setup finished. Double-click Start_ComfyShelf.cmd to open the app.\nIf the window does not appear correctly, see docs/guide.en.md for help.",
        "failed": "\nSetup could not finish. Your ComfyUI files were not changed.\nSee docs/guide.en.md, section 'If something goes wrong'.",
        "log": "Technical details: ",
        "interrupted": "\nSetup interrupted. An unfinished app environment may remain; see the guide before retrying.",
    },
    "de": {
        "intro": "ComfyShelf einrichten\n\nHier wird eine eigene Python-Umgebung im App-Ordner vorbereitet.\nNach deiner Zustimmung werden pywebview und dessen Abhängigkeiten von PyPI heruntergeladen.\nComfyUI, deine Modelle und globale Python-Pakete werden nicht verändert.\nPython und Windows-Komponenten werden nicht automatisch installiert.\nBitte ComfyShelf vorher schließen. Administratorrechte sind nicht nötig.",
        "folder": "App-Ordner: ",
        "confirm": "ComfyShelf jetzt einrichten? [j/N]: ",
        "cancel": "Einrichtung abgebrochen. Es wurde nichts installiert.",
        "working": "ComfyShelf wird eingerichtet. Darunter erscheinen technische Installationsmeldungen...",
        "success": "\nPython-Einrichtung abgeschlossen. Öffne die App per Doppelklick auf Start_ComfyShelf.cmd.\nFalls das Fenster nicht richtig erscheint, hilft docs/guide.de.md weiter.",
        "failed": "\nDie Einrichtung konnte nicht abgeschlossen werden. Deine ComfyUI-Dateien wurden nicht verändert.\nHilfe: docs/guide.de.md, Abschnitt 'Wenn etwas nicht klappt'.",
        "log": "Technische Details: ",
        "interrupted": "\nEinrichtung unterbrochen. Eine unvollständige App-Umgebung kann zurückbleiben; bitte die Anleitung beachten.",
    },
}


class SetupError(Exception):
    pass


def child_environment():
    """Do not inherit pip destinations/indexes or an unrelated Python environment."""
    blocked = {"PYTHONHOME", "PYTHONPATH", "PYTHONUSERBASE", "VIRTUAL_ENV"}
    env = {key: value for key, value in os.environ.items()
           if key.upper() not in blocked and not key.upper().startswith("PIP_")}
    env.update(PIP_CONFIG_FILE=os.devnull, PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
               PYTHON_MANAGER_AUTOMATIC_INSTALL="false")
    env.pop("PYLAUNCHER_ALLOW_INSTALL", None)
    return env


def write_log(log_path, message):
    with log_path.open("a", encoding="utf-8") as stream:
        stream.write(f"{datetime.now().isoformat(timespec='seconds')} {message}\n")


def run_step(command, root, log_path):
    """Stream dependency progress and retain English diagnostics. No shell parsing."""
    write_log(log_path, "Running: " + subprocess.list2cmdline([str(part) for part in command]))
    with subprocess.Popen(command, cwd=root, env=child_environment(), shell=False,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, encoding="utf-8", errors="replace") as process:
        try:
            for line in process.stdout:
                print(line.rstrip(), flush=True)
                write_log(log_path, line.rstrip())
            result = process.wait()
        except KeyboardInterrupt:
            process.terminate()
            process.wait()
            raise
    if result:
        raise SetupError(f"Step failed with exit code {result}. See the preceding output.")


def validate_environment(root):
    environment = root / ".venv"
    python = environment / "Scripts" / "python.exe"
    # Do not follow a symlink/junction to a different Python installation.
    for path in (environment, environment / "Scripts", python,
                 environment / "Lib", environment / "Lib" / "site-packages"):
        if path.is_symlink() or path.resolve() != path.absolute():
            raise SetupError("The app environment points outside its expected location. No packages were installed.")
    config = environment / "pyvenv.cfg"
    if not config.is_file() or not python.is_file():
        raise SetupError("The .venv folder is incomplete or is not a Python environment. Keep it and rename it before running setup again.")
    values = dict(line.strip().lower().split("=", 1) for line in config.read_text(encoding="utf-8").splitlines() if "=" in line)
    values = {key.strip(): value.strip() for key, value in values.items()}
    if values.get("include-system-site-packages") != "false":
        raise SetupError("The existing environment shares global packages. Keep it and rename it before running setup again.")
    probe = subprocess.run(
        [str(python), "-I", "-c", "import json, struct, sys; print(json.dumps([sys.prefix, sys.base_prefix, list(sys.version_info[:2]), struct.calcsize('P')]))"],
        cwd=root, env=child_environment(), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=20, check=False,
    )
    try:
        prefix, base, version, pointer_size = json.loads(probe.stdout)
        valid = (probe.returncode == 0 and Path(prefix).resolve() == environment.resolve()
                 and Path(base).resolve() != environment.resolve()
                 and tuple(version) >= (3, 10) and pointer_size == 8)
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise SetupError("The existing .venv failed its isolated Python check. Keep it and rename it before running setup again.")
    return python


def prepare_environment(root, log_path):
    requirements = root / "requirements-gui.txt"
    if not requirements.is_file():
        raise SetupError("requirements-gui.txt is missing. Extract the complete app download first.")
    environment = root / ".venv"
    if not environment.exists() and not environment.is_symlink():
        run_step([sys.executable, "-I", "-m", "venv", str(environment)], root, log_path)
    python = validate_environment(root)
    run_step([str(python), "-I", "-m", "ensurepip"], root, log_path)
    run_step([str(python), "-I", "-m", "pip", "--disable-pip-version-check", "--no-input",
              "--require-virtualenv", "install", "--index-url", "https://pypi.org/simple",
              "-r", str(requirements)], root, log_path)
    run_step([str(python), "-I", "-m", "pip", "--disable-pip-version-check", "check"], root, log_path)
    run_step([str(python), "-I", "-c", "import webview; print('pywebview import: OK')"], root, log_path)


def main(root=None):
    root = Path(root or Path(__file__).resolve().parent).resolve()
    language = "en"
    log_path = None
    try:
        if os.name != "nt" or sys.version_info < (3, 10) or struct.calcsize("P") != 8:
            print("This setup needs Windows and 64-bit Python 3.10 or newer. / Windows und 64-Bit-Python ab 3.10 erforderlich.")
            return 2
        choice = input("Language / Sprache: [Enter] English, [D] Deutsch: ").strip().lower()
        language = "de" if choice in {"d", "de", "deutsch"} else "en"
        text = TEXT[language]
        print("\n" + text["intro"] + "\n\n" + text["folder"] + str(root))
        consent = input(text["confirm"]).strip().lower()
        if consent not in ({"j", "ja"} if language == "de" else {"y", "yes"}):
            print(text["cancel"])
            return 0
        # Nothing is written (and no dependency is downloaded) before confirmation.
        candidate_log = root / "comfyshelf-setup.log"
        if candidate_log.is_symlink() or candidate_log.resolve() != candidate_log.absolute():
            raise SetupError("The setup log path is redirected. No setup changes were made.")
        write_log(candidate_log, "Setup started")
        log_path = candidate_log
        print(text["working"])
        prepare_environment(root, log_path)
        write_log(log_path, "Setup completed; native GUI startup still needs validation.")
        print(text["success"])
        if os.environ.get("COMFY_PYTHON"):
            print("COMFY_PYTHON is set: Start_ComfyShelf.cmd will use that override instead of .venv.")
        return 0
    except (KeyboardInterrupt, EOFError):
        print(TEXT[language]["interrupted"])
        return 130
    except (OSError, SetupError, subprocess.SubprocessError) as error:
        detail = f"Setup failed: {error}"
        if log_path:
            try:
                write_log(log_path, detail)
            except OSError:
                pass
        print(TEXT[language]["failed"] + "\n" + detail)
        return 2
    finally:
        if log_path:
            print(TEXT[language]["log"] + str(log_path))


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    raise SystemExit(main())
