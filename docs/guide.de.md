# ComfyShelf – Deutsche Anleitung

[English user guide](guide.en.md) · [Projektübersicht (Englisch)](../README.md)

ComfyShelf hilft dir, auf deinem ComfyUI-Laufwerk Platz zu schaffen, ohne jedes
Modell einzeln verschieben zu müssen. Du archivierst die erkannten Dateien eines
Workflows, entfernst bei Bedarf die geprüften lokalen Kopien und lädst sie später
wieder zurück.

Hier findest du alles für Einrichtung und Alltag. Du musst weder Python kennen
noch Konfigurationsdateien bearbeiten oder PowerShell-Befehle eingeben.

## 1. Das brauchst du

- Einen Windows-Rechner mit einer funktionierenden ComfyUI-Installation (Standard-Installation, offizielle Portable-Version oder All-in-One-Pakete wie [ComfyUI-Easy-Install](https://github.com/Tavris1/ComfyUI-Easy-Install)).
- Installiertes **64-Bit-Python ab Version 3.10**: die Software, mit der diese
  ComfyShelf-Version läuft. Dass ComfyUI bereits funktioniert, bedeutet nicht
  automatisch, dass dessen mitgeliefertes Python für andere Apps verfügbar ist.
- Internet bei der ersten Einrichtung für zusätzliche Python-Komponenten und
  ausreichend freien Platz für die App und deine archivierten Dateien.

Ab Version **v1.0.0** bietet ComfyShelf einen bequemen **Windows-Installer (`.exe`)** sowie eine
**Portable Standalone-Version (`.zip`)**, die beide direkt ohne manuelle Python-Installation startklar sind.
Für Entwickler oder bestehende Setups bleibt das bewährte Skript `Setup_ComfyShelf.cmd` weiterhin
vollwertig erhalten; andere Betriebssysteme als Windows sind bislang nicht geprüft.

**Falls Python fehlt:** Lade es von der
[offiziellen Python-Seite](https://www.python.org/downloads/windows/) herunter.
Installiere eine 64-Bit-Version ab 3.10. Wenn du den Python Install Manager
installierst, öffne danach einmal Python und lasse die Einrichtung der
Python-Laufzeit abschließen. Starte dann die ComfyShelf-Einrichtung erneut.
ComfyShelf installiert Python nicht selbst.

Für das App-Fenster werden außerdem Microsofts **WebView2 Runtime** und das von
pywebview benötigte **.NET Framework** gebraucht (ab 4.6.2 für die Chromium-
Darstellung). Sie können bereits vorhanden sein. Meldet die Einrichtung oder
App eine fehlende Komponente, nutze den offiziellen
[WebView2-Download](https://developer.microsoft.com/en-us/microsoft-edge/webview2/)
oder [.NET-Framework-Download](https://dotnet.microsoft.com/en-us/download/dotnet-framework).
Die ComfyShelf-Einrichtung installiert diese Windows-Komponenten nicht.
[Technische Angaben des Herstellers](https://pywebview.flowrl.com/guide/web_engine)

## 2. Die App einmalig einrichten

1. Lege den **vollständigen ComfyShelf-Ordner** an einem beschreibbaren Ort ab,
   zum Beispiel unter Dokumente. Falls du eine ZIP erhalten hast: Rechtsklick,
   **Alle extrahieren**, dann den entpackten Ordner öffnen. Nicht direkt in der
   ZIP starten und nicht nur die Startdatei einzeln kopieren.
2. Doppelklicke auf **Setup_ComfyShelf.cmd**.
3. Tippe **D** und drücke Enter für Deutsch. Enter allein wählt Englisch.
4. Lies die Erklärung. Bestätige die deutsche Nachfrage mit **J** und Enter
   (die englische mit **Y**). Enter ohne Antwort bricht ab.
5. Warte, bis die Einrichtung fertig ist. Im Textfenster erscheinen Download-
   und Installationsmeldungen; das ist normal. Bei einem Fehler hilft der
   Abschnitt weiter unten.
6. Öffne ComfyShelf per Doppelklick auf **Start_ComfyShelf.cmd**.

Die Einrichtung erstellt eine eigene App-Umgebung und installiert pywebview für
die Oberfläche samt Abhängigkeiten von PyPI, dem Paketdienst für Python.
ComfyUI, Modelle und globale Python-Pakete werden nicht verändert. Nicht als
Administrator starten. Schließe ComfyShelf, bevor du die Einrichtung erneut startest.

Der dabei angelegte Ordner `.venv` gehört zur App. Du musst ihn weder öffnen
noch verstehen oder eine `.env`-Datei anlegen. Später genügt immer
**Start_ComfyShelf.cmd**. Die Einrichtung läuft nicht bei jedem Start erneut.

## 3. Ordner und Sprache wählen

Die Oberfläche startet auf Englisch. Öffne **Settings** und wähle **DE** für
Deutsch. Deine Auswahl wird gespeichert.

Wähle beim ersten Start oder unter **Einstellungen** diese zwei Orte:

- **ComfyUI-Hauptordner:** der Ordner mit `models`, `custom_nodes` und `user`.
  Nicht den Ordner `models` selbst und nicht einen übergeordneten Ordner einer
  portablen Installation auswählen.
- **Auslagerungsordner:** hier bleiben Dateien, die du gerade nicht brauchst.
  Am besten auf einem anderen Laufwerk mit ausreichend freiem Platz.

Die beiden Ordner dürfen nicht ineinander liegen. ComfyShelf kann den
Auslagerungsordner anlegen. Beim Archivieren kann zusätzlich vorübergehend
Speicher benötigt werden. FAT32-Laufwerke sind als Auslagerungsziel ungeeignet;
wähle bei einer entsprechenden Meldung ein anderes geeignetes Laufwerk, statt
vorschnell eines zu formatieren.

## 4. Den ersten Workflow archivieren

1. Wähle **Workflow archivieren** (oder ziehe deine `.png`-, `.json`- oder `.zip`-Datei einfach per **Drag & Drop** in das App-Fenster).
2. Wähle die `.json`-Datei oder ein generiertes `.png`-Bild: ComfyUI bettet Workflow-Metadaten direkt in generierte PNGs ein. ComfyShelf liest den Modell-Graphen daraus aus und nutzt das Bild gleichzeitig als Cover-Vorschaubild im Regal.
3. Prüfe die Dateiliste und eventuelle Hinweise. Mit Dateien sind hier auch
   Modelle, VAEs, LoRAs, Text Encoder und weitere unterstützte Modelldateien gemeint.
4. Bestätige und warte, bis das Archiv erfolgreich erstellt wurde.

**Archivieren entfernt noch nichts aus ComfyUI.** Es bereitet das Archiv vor.
Die App liest Modellverweise aus dem gewählten Workflow aus; sie packt nicht
einfach deinen gesamten Modellordner ein.

Fehlt eine Datei, siehst du ihren Namen. Mit dem Kopier-Button kannst du den
Dateinamen übernehmen und selbst danach suchen. Du kannst abbrechen und die
Datei zuerst ergänzen oder bewusst ein unvollständiges Archiv anlegen.
Fehlende Dateien werden nicht automatisch heruntergeladen.

## 5. Platz freigeben und Dateien wieder laden

Beende zuerst laufende Generierungen in ComfyUI.

Wähle den archivierten Workflow und **Workflow entladen**. Lies die Vorschau und
bestätige nur, wenn du die aufgelisteten Dateien wirklich entfernen möchtest.

Entladen **löscht die geprüften lokalen Modellkopien auf deinem ComfyUI-Laufwerk**.
Die archivierten Kopien bleiben im Auslagerungsordner. Workflow-JSONs und
Custom-Node-Ordner werden nicht entfernt.

Wenn du den Workflow wieder brauchst, wähle **Workflow laden**. ComfyShelf stellt
fehlende Dateien mit ihren ursprünglichen Namen und Unterordnern wieder her.
Liegt am selben Ziel bereits eine andere Modelldatei, wird das Laden blockiert,
statt sie zu überschreiben.

Je nach Zustand heißen die Buttons auch **Fehlende Modelle laden** oder
**Vorhandene Modelle entladen**. „Geladen“ meint Dateien in den ComfyUI-Ordnern,
nicht Modelle, die gerade im Grafikkartenspeicher liegen.

### Den Workflow wieder in ComfyUI öffnen

Klicke unter dem Workflow-Namen auf **Workflow-Datei anzeigen**. Der Explorer
öffnet sich mit markierter JSON; ziehe sie in ComfyUI im Browser. Du musst die
ZIP nicht selbst entpacken. Fehlt die JSON lokal, fragt ComfyShelf nach, bevor es
nur diese Datei aus dem Paket wiederherstellt. Es werden keine Modelle geladen,
vorhandene JSONs (auch deine lokalen Änderungen) nicht überschrieben und das
Paket bleibt unverändert. Dabei wird nur die JSON geprüft, nicht das ganze Paket;
der Verifiziert-Status ändert sich nicht. Der Button startet ComfyUI nicht und
öffnet auch keinen Workflow automatisch im Browser.

### Workflows umbenennen, ausblenden oder löschen

- **Umbenennen:** Klicke auf das Stift-Icon neben dem Workflow-Namen. Der Name wird sofort aktualisiert.
- **Ausblenden:** Mit dem Augen-Icon neben dem Namen kannst du einen Workflow aus der Hauptansicht ausblenden. Er erscheint dann nur noch im Filter-Reiter **Ausgeblendet**. Aktive (geladene) Workflows können nicht ausgeblendet werden, damit keine Modelle unbemerkt Speicherplatz auf der SSD belegen.
- **Löschen:** Über **Archiv löschen…** in der Fußleiste kannst du das Paket in den Windows-Papierkorb verschieben oder unwiderruflich löschen.

## 6. Zwischen Workflows wechseln

- **Nur zusätzlich laden:** lädt die Dateien des gewählten Workflows und lässt
  andere geladene Dateien liegen.
- **Zu diesem Workflow wechseln:** lädt zuerst das Ziel und versucht anschließend,
  geeignete Dateien der bisherigen Workflow-Pakete zu entfernen.

Dateien, die als weiterhin benötigt erkannt werden, sind geschützt; die App kann
dich um die Prüfung einer Ausnahme bitten. Warnungen nicht übergehen, wenn du
deren Folgen nicht kennst. Unbekannte oder erst während der Ausführung bestimmte
Dateiverweise können bei dieser Erkennung fehlen.

Vor dem Entladen oder Wechseln hebt eine bernsteinfarbene Warnbox betroffene
lokale Workflows hervor. Unter **Dateien und Workflows anzeigen** siehst du die
einzelnen Dateien sowie Namen und Pfade der Workflows, die darauf verweisen.
Das Entfernen geteilter Dateien musst du weiterhin ausdrücklich bestätigen;
das Aufklappen der Liste erteilt keine Freigabe.

Bei längeren Vorgängen siehst du Fortschritt und aktuelle Meldungen. Abbrechen
ist möglich, solange der Dialog es anbietet. Während der endgültigen Löschphase
wird der Abbruch deaktiviert. Stoppt ein Wechsel mittendrin, lies das Ergebnis:
Das Ziel kann schon geladen sein, obwohl alte Dateien nicht entfernt werden konnten.

## 7. Archivplatz sparen und einen Workflow mitnehmen

ComfyShelf bewahrt identische Modelldaten nur einmal im Archiv auf. Alle Workflows,
die sie brauchen, teilen diese Kopie. Das ist der **Modellpool**. Er liegt im
Auslagerungsordner unter `.comfyshelf`.

Deshalb können die Workflow-ZIPs deiner Bibliothek winzig sein: Die Modelle liegen
daneben im gemeinsamen Pool und nicht in jeder ZIP erneut.

Im Bereich Hinweise siehst du außerdem Dateien, die andere archivierte Workflows
verwenden. **Dateien und Workflows anzeigen** klappt die passenden Pakete auf.
Dafür werden nur bereits gespeicherte Paketmetadaten verglichen, nicht die großen
Modelldateien neu eingelesen. Das ist weder eine erneute Integritätsprüfung noch
die oben beschriebene Sicherheitsprüfung lokaler Workflows.

**Einen Workflow mitnehmen:** Wähle den Workflow und anschließend **Portable ZIP
exportieren…**. Im Speichern-Dialog bestimmst du Ordner und Dateinamen. Vor dem
Export zeigt ComfyShelf das Ziel, den geschätzten Platzbedarf und den freien Platz.
Die ZIP enthält den Workflow und seine archivierten Modelle, VAEs, LoRAs und
Text-Encoder. Deinen gemeinsamen Modellpool braucht sie nicht; eine passende
ComfyUI-Installation ist weiterhin nötig. Custom Nodes werden aufgelistet, ihr
Code und ihre Python-Abhängigkeiten sind aber nicht enthalten.

Bei unvollständigen Paketen werden die fehlenden Dateinamen angezeigt. Du musst
ausdrücklich bestätigen, dass du ohne diese Dateien exportieren möchtest.
Der Export kann fehlende Dateien nicht nachliefern. Die fertige ZIP wird geprüft,
bevor sie am gewählten Ziel erscheint; vorhandene Dateien werden nicht
überschrieben. Das ursprüngliche Paket und der Pool bleiben unverändert.

**Die gesamte Bibliothek sichern oder umziehen:** Kopiere den kompletten
Auslagerungsordner einschließlich `.comfyshelf` und behalte die Struktur bei.
Die Dateien im Pool nicht selbst umbenennen, bearbeiten oder löschen. Wähle nach
einem Umzug den kopierten Auslagerungsordner in den Einstellungen, bevor du weiterarbeitest.

Bei älteren vollständigen ZIP-Paketen gibt es zusätzlich **Speicher optimieren**.
Dabei wandern ihre Modelle in den Pool. Exportieren kannst du auch ohne diesen Schritt.
**Gesamte Bibliothek optimieren** in den Einstellungen erledigt das für ältere
Pakete gemeinsam. Auch Entladen bereitet solche Pakete vor dem lokalen Löschen vor.

Nutze auf dem anderen Rechner möglichst die Import- und Ladefunktionen von
ComfyShelf. Direktes Entpacken einer vollständigen exportierten ZIP in ComfyUI
umgeht die Konfliktprüfung. Die kleine Bibliotheks-ZIP allein reicht nicht.

## 8. Archive prüfen oder importieren

**Dateien fehlten früher, sind aber inzwischen vorhanden?** Wähle das Paket und
klicke über der Modellliste auf **Abhängigkeiten prüfen**. Der Scan liest nur den
gewählten Workflow, passende ComfyUI-Pfade und Pool-Verweise anderer Pakete. Er
hasht nicht den gesamten Modellordner und verändert nichts. Pool-Dateien ohne
Paketverweis lassen sich nicht über ihren ursprünglichen Namen finden; eine lokale
Kopie kann beim Ergänzen trotzdem anhand ihres Hashes wiederverwendet werden.

Die Vorschau unterscheidet **Bereits eingebunden**, **Ergänzbar**, **Weiterhin
fehlend** und **Mehrdeutig**. Bereits eingebunden beschreibt die Paketangaben,
nicht eine neue Integritätsprüfung. Mehrdeutige Treffer kannst du aufklappen.
Hinweise zu Custom Loadern bleiben wichtig; unbekannte Kategorien benötigen
gegebenenfalls eine lokale Datei, damit der Pfad eindeutig zugeordnet werden kann.

Mit **Gefundene Dateien ergänzen** werden alle eindeutigen Treffer gehasht,
vorhandene Pool-Dateien wiederverwendet und die neue ZIP geprüft. Erst danach
wird das Paket ersetzt. Haben lokale Datei und Pool-Treffer verschiedene Hashes,
stoppt der Vorgang. Deine ComfyUI-Dateien werden nicht verschoben oder entfernt.
Große Dateien brauchen Zeit für die Prüfung. Die Vorschau schätzt zusätzlichen
Platz für neue Pool-Dateien, temporäre ZIP und Sicherung. Verändere die Bibliothek
und ihre Dateien währenddessen nicht.

Die bisherige ZIP bleibt daneben als `Name.before-dependencies-….zip.bak` liegen;
der Abschlussdialog zeigt den genauen Pfad. Diese Sicherung erscheint nicht als
zusätzlicher Workflow. Behalte sie, bis du mit dem neuen Paket zufrieden bist;
danach kannst du sie selbst löschen, um ihren Platz freizugeben. Vorhandene
Pool-Verweise bleiben darin erhalten, der Pool muss also ebenfalls bleiben.
Fehlende und mehrdeutige Abhängigkeiten werden weiterhin dokumentiert. Scheitert
die Aktualisierung oder brichst du ab, bleibt die ursprüngliche ZIP erhalten.
Bereits gespeicherte Pool-Dateien können zur späteren Wiederverwendung bleiben;
die App löscht sie nicht automatisch.

**Paket prüfen** kontrolliert, ob die erfassten Dateien unbeschädigt sind. Ein
Archiv kann diese Prüfung bestehen und trotzdem unvollständig sein. Das ist keine
Garantie, dass der Workflow läuft, ein Modell sicher oder der Ersteller vertrauenswürdig ist.

Leere Standard-LoRA-Loader im Bypass-Modus brauchen keine Warnung zu ungeklärten
Dateien, sofern auch ihr Dateinamen-Eingang nicht verknüpft ist. Ausgewählte LoRAs
werden dagegen weiterhin archiviert, auch im Bypass-Modus. Aktive oder dynamisch
verknüpfte Loader mit ungeklärter Datei behalten den Hinweis. Das gilt für die
Anzeige älterer Pakete ebenfalls, ohne deren ZIP oder gespeicherten Prüfstatus
zu verändern.

Das Prüfergebnis bleibt jetzt auch nach dem Schließen der App gespeichert.
Fahre mit dem Mauszeiger über den Integritätsstatus, um den Zeitpunkt zu sehen.
Die Ergebnisse liegen bei deiner Archivbibliothek; ein App-Update oder ein neuer
App-Ordner löscht sie daher nicht. Auch beim Umbenennen in ComfyShelf bleibt der
Prüfstand eines unveränderten Pakets erhalten.

Beim Start oder Aktualisieren vergleicht ComfyShelf nur Dateieigenschaften, ohne
große Modelle neu einzulesen. Haben sich die ZIP oder zugehörige Pool-Dateien
geändert oder fehlen sie, erscheint **Erneut prüfen**. Auch das Neupacken einer
ZIP oder ein Umzug der Bibliothek auf ein anderes Laufwerk kann eine neue Prüfung
erfordern. Das ist ein gespeicherter Prüfstand, keine laufende Überwachung.
Sicherheitsprüfungen vor dem Entfernen lesen und prüfen die Daten weiterhin.
Von älteren Versionen vergessene Ergebnisse lassen sich nicht wiederherstellen:
Prüfe diese Pakete einmal erneut, damit ihr Status gespeichert wird. Scheitert das
Speichern, prüfe den Schreibzugriff auf den Auslagerungsordner.

**Paket importieren** nimmt unterstützte Workflow-ZIPs an, auch selbst ergänzte
ComfyShelf-Pakete. Die App liest den Workflow neu ein und erstellt das
Dateiverzeichnis automatisch. Du musst keine Manifest-Datei selbst anlegen.
Zusätzliche Modelle, die der erkannte Workflow nicht verwendet, werden angezeigt
und nicht übernommen.

Im Importdialog kannst du das Paket ausgelagert lassen oder seine Dateien
zusätzlich in ComfyUI laden. Auch beim direkten Laden bleibt das Archiv erhalten.
Prüfe fehlende Dateien und den Platzbedarf auf beiden Laufwerken. Nicht jede
beliebig aufgebaute ZIP wird unterstützt.

## 9. Wenn etwas nicht klappt

- **Python wurde nicht gefunden:** Befolge Abschnitt 1 und starte die Einrichtung
  erneut. Ist Python installiert, wird aber nicht erkannt, frage nach Hilfe,
  statt die Python-Installation von ComfyUI zu verändern.
- **Die Einrichtung schlägt fehl:** Bewahre die Fehlermeldung auf. Details stehen
  in `comfyshelf-setup.log` neben der App. Prüfe Internetverbindung und freien
  Speicher. Nach einem Downloadfehler kannst du die Einrichtung erneut starten.
  Meldet sie eine beschädigte `.venv` oder gemeinsam genutzte globale Pakete,
  schließe die App und benenne diesen Ordner im Explorer in `.venv-old` um.
  Bewahre ihn zur Fehlersuche auf und starte die Einrichtung erneut. Eine
  vorhandene `.venv-old` nicht überschreiben; wähle dann einen anderen freien Namen.
- **Das Fenster öffnet sich nicht oder bleibt leer:** Prüfe die Windows-
  Anzeigekomponenten aus Abschnitt 1. Bewahre gegebenenfalls
  `workflow-switcher-error.log` neben der App und das Protokoll unter
  `.gui-state/comfyshelf.log` auf. Eine fertige Einrichtung allein beweist noch
  nicht, dass das native Fenster startet.
- **Eine Datei lässt sich nicht entfernen:** Beende Generierungen und schließe
  Programme, die sie verwenden. Aktualisiere dann die Anzeige und prüfe die
  Vorschau erneut. Die App beendet keine fremden Programme und entfernt keinen Schreibschutz.
- **Ein Vorgang ist nur teilweise fertig:** Lies, welche Dateien entfernt wurden
  und welche bleiben. Geprüfte Archivkopien bleiben erhalten; erfolgreiche lokale
  Löschungen werden nicht automatisch rückgängig gemacht. Lösche übrige Dateien
  nicht selbst, um den Vorgang zu erzwingen.
- **Laufwerk oder Liste wirken veraltet:** Nutze die Aktualisieren-Buttons für
  Bibliothek oder Speicherplatz. In den Workflow-Details kannst du auch umbenennen.

Bei Problemen in der geöffneten App hilft **Einstellungen → Diagnoseprotokoll öffnen**.
Technische Statusmeldungen sind Englisch; Originalfehler können anderssprachige
Details enthalten. Entferne vor dem Weitergeben von Logs oder Workflows private
Pfade, Prompts und andere persönliche Angaben.

## 10. Wichtige Grenzen

- ComfyShelf liest den gespeicherten Workflow, führt ihn aber nicht aus. Inaktive
  Zweige können enthalten sein. Ungewöhnliche Loader, während der Ausführung
  zusammengesetzte Dateinamen und in anderen Nodes verschachtelte Workflows können
  fehlen. Prüfe Hinweise zu unsicheren Zuordnungen.
- Der Schutz von Dateien, die andere Workflows ebenfalls verwenden, hat dieselben
  Erkennungsgrenzen.
- Modellorte aus `extra_model_paths.yaml` werden noch nicht unterstützt. Gemeint
  sind zusätzliche Modellordner außerhalb der üblichen ComfyUI-Struktur.
- Custom Nodes werden aufgelistet, nicht installiert oder gesichert. Auf einem
  anderen Rechner brauchst du weiterhin passende Nodes, Software und Hardware.
- Eine Dateiprüfung verhindert nicht, dass ein anderes Programm die Datei danach
  verändert oder sperrt. Sichere unersetzliche Dateien zusätzlich separat. Das
  Archiv ist keine zusätzliche Sicherung, wenn es deine einzige verbleibende Kopie enthält.
- Prüfe vor dem Teilen die Weitergaberechte der Modelle. Kontrolliere auch
  Workflow-Texte und Archivangaben auf persönliche Informationen.

Befehle und eigene Loader-Regeln stehen bei Interesse in der
[deutschen technischen Referenz](reference.de.md). Für die normale Bedienung
brauchst du sie nicht.
