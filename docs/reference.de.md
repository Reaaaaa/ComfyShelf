# ComfyShelf – Technische Referenz

[Deutsche Anleitung](guide.de.md) · [Technical reference (English)](reference.en.md)

Diese Referenz ist optional. Für die normale Bedienung reicht die grafische
Oberfläche. Alle Befehle werden im App-Ordner ausgeführt; die Beispielpfade sind
Platzhalter und müssen angepasst werden.

## Manuelle Einrichtung

Die Doppelklick-Einrichtung erstellt nach Zustimmung eine eigene `.venv` und
installiert die Pakete aus `requirements-gui.txt` von PyPI. Sie installiert weder
Python noch Windows-Komponenten und richtet ComfyUI nicht ein. Benötigt wird ein
installiertes 64-Bit-Python ab Version 3.10.

Alternativ in PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-gui.txt
.\.venv\Scripts\python.exe .\workflow_switcher_gui.py
```

Ist `py` nicht verfügbar, im ersten Befehl den vollständigen Pfad zum geeigneten
Python verwenden. Die Umgebung muss nicht aktiviert werden.
`Start_ComfyShelf.cmd` nutzt `.venv\Scripts\pythonw.exe`; `COMFY_PYTHON` kann
diesen Pfad ausdrücklich überschreiben. Die Einrichtung installiert weiterhin
nur in die App-`.venv`, nicht in diese alternative Umgebung.
`COMFYSHELF_SETUP_PYTHON` wählt bei Bedarf den Basisinterpreter für die Einrichtung.
Ein ungültiger expliziter Pfad führt zum Abbruch, nicht zu einem stillen Ersatz.

Das Einrichtungsskript deaktiviert automatische Python-Installationen durch den
Launcher und überspringt Windows-Store-Aliase von `python.exe`. Die
PowerShell-Ausführungsrichtlinie wird nur für den gestarteten Prozess übersteuert;
Systemrichtlinien bleiben unverändert. Richtlinien verwalteter Rechner können die
Ausführung trotzdem verhindern. Organisatorische Schutzmaßnahmen nicht abschalten.

Einrichtungsdetails stehen in `comfyshelf-setup.log` neben der App. Geeignete
vorhandene Umgebungen werden wiederverwendet, ohne pauschal alle Pakete zu
aktualisieren. Beschädigte oder umgeleitete Umgebungen werden nicht automatisch
gelöscht. Nach Fehlern kann eine teilweise eingerichtete Umgebung zurückbleiben.
Übernommene Pip-Konfiguration und Installationsziel-Variablen werden bei dieser
Einrichtung ignoriert; installiert wird von PyPI in die App-Umgebung.

Für die Fensterdarstellung benötigt pywebview WebView2 Runtime und .NET Framework.
Die [Herstellerdokumentation](https://pywebview.flowrl.com/guide/web_engine) nennt
die Voraussetzungen. Ein erfolgreicher Paketimport prüft noch keinen nativen
Fensterstart.

## Gemeinsamer Modellpool

Die GUI schreibt erkannte Modelldateien direkt in den gemeinsamen Pool.
Importierte Pakete werden nach der Prüfung ebenfalls umgewandelt. Der CLI-Befehl
`pack` erstellt dagegen eine vollständige ZIP. Ältere Vollpakete können über
**Speicher optimieren** oder **Gesamte Bibliothek optimieren** umgewandelt werden;
auch Entladen und Wechseln bereiten sie vor dem Entfernen lokaler Dateien vor.

Pool-Objekte liegen unter `.comfyshelf/objects/sha256/<Präfix>/<Hash>` im
Auslagerungsordner, absichtlich ohne Endung. Das Paket hält ursprüngliche Namen,
Unterordner und Prüfsummen fest. Identische Dateiinhalte teilen sich ein Objekt.
Fehlende oder beschädigte Objekte blockieren Prüfung, Wiederherstellung und Export.

## Erweiterbare Loader-Regeln und Erkennungsqualität

Die lokale Datei `node-rules.json` neben `comfy_archive.py` ergänzt die eingebauten
Loader-Regeln. Sie ist zunächst leer. `node-rules.example.json` zeigt das Schema
an einem **erfundenen Beispiel-Node**; die Beispiele sind keine vorgefertigten
Regeln für bestimmte Custom-Node-Pakete.

Ein Eintrag verwendet den exakten Node-Typ aus der Workflow-JSON und getrennte
Listen für `ui` und `api`. Jede Regel enthält:

- `path`: JSON-Pfad ab dem Node-Objekt, z. B. `["widgets_values", 0]` oder
  `["properties", "adapters", "*", "filename"]`. `*` durchläuft eine Liste oder
  die Werte eines Objekts. Bei API-Prompts beginnen typische Pfade mit `inputs`.
- `kind`: Modellkategorie für die Anzeige, z. B. `lora`, `vae`, `text_encoder`.
- `categories`: Relative Suchordner unter `models`, z. B. `["loras"]` oder
  `["diffusion_models", "unet"]`. Eine leere Liste erlaubt die Suche ohne bekannte
  Kategorie. Absolute Pfade und `..` sind nicht erlaubt.

Es werden weiterhin nur Dateinamen mit unterstützten Modell-Endungen übernommen.
Explizite lokale Regeln haben für die zugeordneten Werte Vorrang vor den
eingebauten Regeln und der Dateiendungs-Heuristik. Über
`COMFY_WORKFLOW_SWITCHER_NODE_RULES` lässt sich eine alternative Regeldatei wählen.
Regeln werden bei der nächsten Analyse neu gelesen. Ungültige Regeln brechen
die Analyse ab, statt unbemerkt die Erkennung oder den Schutz gemeinsamer Modelle
zu deaktivieren. Regeln aus einem importierten ZIP werden niemals ausgeführt oder
automatisch installiert.

Vermutete Zuordnungen, Ersatztreffer nach Dateiname und Loader ohne direkt
erkennbaren Modellnamen werden beim Archivieren/Importieren angezeigt und in neuen
Paketmanifesten gespeichert. Die Bibliotheksdetails zeigen diese Hinweise ebenfalls.
Bestehende Pakete erhalten diese Metadaten nicht rückwirkend. Eine Hashprüfung
beweist Dateiintegrität, nicht die Vollständigkeit der Abhängigkeitserkennung.

Relative Unterordner bleiben über Archiv, Pool, Export und Wiederherstellung
erhalten. Wird ein ausdrücklich angegebener Unterpfad nicht gefunden, werden
gleichnamige Dateien in anderen Ordnern **nicht** automatisch übernommen. Bei
einem einfachen Dateinamen bleibt ein eindeutiger Unterordner-Treffer möglich,
wird aber als Ersatzzuordnung gekennzeichnet. Mehrdeutige Treffer bleiben ungelöst.

## ComfyUI- und Auslagerungsordner konfigurieren

Die gemeinsame Konfiguration wird auch von der GUI verwendet. Der
Auslagerungsordner wird bei Bedarf erstellt:

```powershell
.\.venv\Scripts\python.exe .\comfy_archive.py config set `
  --id main `
  --name "Meine ComfyUI-Installation" `
  --comfy-root "C:\AI\ComfyUI" `
  --archive-root "E:\WorkflowArchive"
```

Gespeicherte Werte anzeigen und erneut prüfen:

```powershell
.\.venv\Scripts\python.exe .\comfy_archive.py config show
.\.venv\Scripts\python.exe .\comfy_archive.py config validate
.\.venv\Scripts\python.exe .\comfy_archive.py config path
```

Die Konfiguration unterstützt bereits mehrere benannte Installationen und markiert
eine davon als aktiv. Geprüft werden unter anderem ComfyUI-Struktur, Schreibbarkeit,
freier Speicher, verschachtelte Pfade, gleiches Laufwerk und FAT32.

Explizite Parameter haben Vorrang. Für `inspect` und `pack` wird ohne
`--comfy-root` zunächst ein ComfyUI-Root aus dem Workflow-Pfad abgeleitet und erst
danach die aktive Installation verwendet. `restore` und `unload` verwenden ohne
expliziten Root die aktive Installation. Ohne `--output` nutzt `pack` deren
Auslagerungsordner. Damit genügt nach der Einrichtung beispielsweise:

```powershell
.\.venv\Scripts\python.exe .\comfy_archive.py pack workflow.json
.\.venv\Scripts\python.exe .\comfy_archive.py restore "E:\WorkflowArchive\workflow.zip"
```

Mit `--config-file` oder der Umgebungsvariable
`COMFY_WORKFLOW_SWITCHER_CONFIG` kann eine portable beziehungsweise alternative
Konfigurationsdatei verwendet werden.

## Workflow untersuchen

```powershell
.\.venv\Scripts\python.exe .\comfy_archive.py inspect "C:\AI\ComfyUI\user\default\workflows\Minimax Music 3 - Text to Music.json"
```

Liegt der Workflow außerhalb des ComfyUI-Ordners, kann dessen Hauptordner explizit
angegeben werden:

```powershell
.\.venv\Scripts\python.exe .\comfy_archive.py inspect C:\Workflows\music.json --comfy-root C:\AI\ComfyUI
```

## Nur die erkannten Assets hashen

```powershell
.\.venv\Scripts\python.exe .\comfy_archive.py inspect C:\Workflows\music.json --comfy-root C:\AI\ComfyUI --hash
```

Bei großen Modellen kann SHA-256 einige Zeit dauern. Unreferenzierte Dateien werden
nicht gehasht.

## Analyse-Manifest schreiben

```powershell
.\.venv\Scripts\python.exe .\comfy_archive.py inspect C:\Workflows\music.json --comfy-root C:\AI\ComfyUI --hash --write-manifest C:\Workflows\music.manifest.json
```

Ohne `--write-manifest` schreibt `inspect` überhaupt keine Dateien.

## Portables Workflow-Paket erstellen

```powershell
.\.venv\Scripts\python.exe .\comfy_archive.py pack "C:\AI\ComfyUI\user\default\workflows\Minimax Music 3 - Text to Music.json" --output "E:\WorkflowArchive\Minimax Music 3.zip"
```

Standardmäßig wird bei jedem fehlenden Modell abgebrochen. Eine bereits vorhandene
Ziel-ZIP wird nur mit `--force` ersetzt. Lokale Quelldateien werden niemals verändert
oder entfernt.

Große Modelldateien werden ohne wirkungsarme ZIP-Kompression gespeichert; JSON und
andere kleinere Dateien werden komprimiert. Alle enthaltenen Dateien werden im
`comfy-package.json` mit SHA-256 dokumentiert.

Custom-Node-Ordner werden bewusst nicht eingepackt. Stattdessen enthält jedes Paket
eine lesbare `custom-nodes.txt`; das Manifest speichert zusätzlich Paketname,
Workflow-Node-Typen, bekannte Revision und ob das Paket beim Packen installiert war.

### Bewusst unvollständiges Paket

```powershell
.\.venv\Scripts\python.exe .\comfy_archive.py pack workflow.json --output workflow.zip --allow-missing
```

`--allow-missing` erzeugt das Paket trotz fehlender Abhängigkeiten. Das Manifest setzt
`complete` auf `false` und führt jedes fehlende Element samt Grund auf. Der Schalter
übergeht keine beschädigten oder mehrdeutigen Treffer stillschweigend.

## ZIP überprüfen

```powershell
.\.venv\Scripts\python.exe .\comfy_archive.py verify "E:\WorkflowArchive\Minimax Music 3.zip"
```

`verify` prüft die ZIP-Struktur, CRC und den SHA-256-Hash jeder im Manifest genannten
Datei. Ein Paket kann technisch integer, aber absichtlich unvollständig sein; beide
Zustände werden getrennt ausgegeben.

## ZIP wieder einfügen

Dieser Abschnitt gilt nur für vollständige ZIPs aus CLI-`pack` oder **ZIP
exportieren**, nicht für die kleinen Pool-Pakete der Bibliothek. Diese benötigen
zusätzlich den Ordner `.comfyshelf` neben der jeweiligen ZIP.

Die vollständige ZIP besitzt Pfade relativ zum ComfyUI-Hauptordner:

```text
models/...
user/default/workflows/...
custom-nodes.txt
comfy-package.json
```

Sie kann daher direkt in den ComfyUI-Root entpackt werden. Sicherer ist der eingebaute
`restore`-Befehl, weil er vorhandene Dateien niemals ungefragt überschreibt.

## Workflow-Paket sicher wiederherstellen

Ohne `--confirm` zeigt `restore` nur die geplante Wiederherstellung:

```powershell
.\.venv\Scripts\python.exe .\comfy_archive.py restore "E:\WorkflowArchive\Minimax Music 3.zip" --comfy-root "C:\AI\ComfyUI"
```

Dabei werden ZIP-Integrität und SHA-256-Hashes, vorhandene Zieldateien und freier
Speicher geprüft. Identische Dateien werden übersprungen. Ein Modell am gleichen Pfad
mit einem anderen Hash blockiert die gesamte Wiederherstellung. Eine lokal veränderte
Workflow-JSON wird dagegen behalten und niemals überschrieben.

Nach Prüfung stellt `--confirm` ausschließlich fehlende Dateien wieder her:

```powershell
.\.venv\Scripts\python.exe .\comfy_archive.py restore workflow.zip --comfy-root C:\AI\ComfyUI --confirm
```

Für ein bewusst unvollständiges Paket ist zusätzlich `--allow-incomplete` erforderlich.
Dateien werden zuerst unter einem temporären Namen extrahiert und erneut geprüft. Bei
einem Fehler werden alle in diesem Lauf bereits wiederhergestellten Dateien entfernt.

## Archivierte Modelle aus ComfyUI entladen

Ohne `--confirm` zeigt `unload` ausschließlich eine Vorschau und entfernt nichts:

```powershell
.\.venv\Scripts\python.exe .\comfy_archive.py unload "E:\WorkflowArchive\Minimax Music 3.zip" --comfy-root "C:\AI\ComfyUI"
```

Vor dem Entladen werden die ZIP-Integrität, alle archivierten SHA-256-Hashes, die
lokalen Modelldateien und Referenzen aus anderen lokalen Workflow-JSONs geprüft.
Custom Nodes und Workflow-JSONs werden grundsätzlich nicht entfernt.

Erst der zusätzliche Schalter `--confirm` entfernt die exakt geprüften Modelldateien
dauerhaft und räumt anschließend ausschließlich dadurch leer gewordene Unterordner
unter `models` auf:

```powershell
.\.venv\Scripts\python.exe .\comfy_archive.py unload workflow.zip --comfy-root C:\AI\ComfyUI --confirm
```

Bewusste Sicherheitsausnahmen müssen separat angegeben werden:

```text
--allow-incomplete  Entladen trotz unvollständigem Paket
--allow-shared      Modell wird noch von anderen lokalen Workflows referenziert
```

Diese Schalter sind unabhängig. Ein abweichender lokaler Datei-Hash kann niemals
übergangen werden.

## Aktuelle Grenzen

- Standard-Loader werden über feste, überprüfbare Regeln erkannt.
- Zusätzliche Custom-Loader können über die lokale `node-rules.json` beschrieben
  werden; es gibt dafür noch keinen grafischen Regel-Editor.
- Unbekannte Custom Nodes werden ergänzend anhand typischer Modelldateiendungen
  erkannt und in der Ausgabe mit `[prüfen]` markiert.
- Die Analyse ist statisch. Sie wertet keine Switch-Entscheidungen aus und führt
  keinen Workflow-Code aus. Inaktive Zweige können enthalten sein; dynamische
  Dateinamen, unbekannte Ablagefelder oder eingebettete Subgraphs können fehlen.
- Der Schutz gemeinsam verwendeter Modelle basiert ebenfalls auf dieser statischen
  Erkennung und kann nicht jede unbekannte oder dynamische Referenz erfassen.
- `extra_model_paths.yaml` wird noch nicht ausgewertet.
- API-Workflows und normale UI-Workflows werden unterstützt; Custom-Node-Pakete sind
  im UI-Format meist besser dokumentiert.
- Custom Nodes und deren Python-Abhängigkeiten werden nur dokumentiert, nicht
  mitgeliefert oder automatisch installiert.


## Lokale Daten und Diagnose

Die Windows-Konfiguration liegt normalerweise unter
`%LOCALAPPDATA%\ComfyWorkflowSwitcher\config.json`. Der bisherige Ordnername bleibt
aus Kompatibilitätsgründen erhalten. Einstellungen, Verlauf, Browserdaten und
rotierende App-Protokolle liegen unter `.gui-state/` neben der App.
Startfehler können zusätzlich in `workflow-switcher-error.log` stehen.

Das App-Protokoll lässt sich über **Einstellungen → Diagnoseprotokoll öffnen**
anzeigen. Statusmeldungen sind Englisch; Originalfehler aus Backend oder
Bibliotheken können anderssprachige Details enthalten. Vor dem Weitergeben auf
persönliche Pfade, Namen und andere private Angaben prüfen. Die Startüberwachung
meldet eine fehlende Bereitschaft der Oberfläche, kann aber nicht jeden
nativen Fensterfehler beheben.

Bei teilweise fehlgeschlagenem Entladen beendet die CLI den Vorgang mit
Fehlercode 2. Mit `--json` enthält die Antwort `error_code: removal_incomplete`
und `details` mit `removed`, `remaining`, `removal_issues` und gegebenenfalls
`restore`. Erfolgreiche lokale Löschungen werden nicht automatisch zurückgerollt.

Die CLI liefert bei normalem Abschluss 0, bei abgefangenen Eingabe-/Dateifehlern 2
und bei ungültiger Integrität durch `verify` 3. Eine Vorschau mit Code 0 kann
Blockaden nennen; ein erfolgreich geprüftes Paket kann weiterhin unvollständig
sein. Andere Fehler können trotz `--json` als Text auf stderr erscheinen.
