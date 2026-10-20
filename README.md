# Keepsaid

Dein persönliches Audioarchiv: Aufnahmen transkribieren, Stimmen zuordnen, Texte korrigieren und wortweise mitlesen. Dieses Projekt hieß bisher `soundcore-worker`; der neue Anwendungsname ist **Keepsaid**.

## Auf einem weiteren Rechner nutzen

```sh
git clone git@github.com:n-bross/keepsaid.git
cd keepsaid
python3 -m venv .venv
cp .env.example .env
```

Die Review-Oberfläche benötigt Python 3 und FFmpeg. Den eigenen bzw. bisherigen Share in `.env` über `KEEPSAID_SHARE_URL` und `KEEPSAID_REVIEW_URL` einstellen. Für den privaten Tailscale-Share muss der zweite Rechner ebenfalls Zugriff auf das Tailnet haben.

```sh
.venv/bin/python review_server.py
```

Danach http://127.0.0.1:8765 öffnen. Die bestehenden Aufnahmen und bestätigten Zuordnungen auf dem NAS sind über die zentrale Review-Seite erreichbar. Ein lokal gestarteter Review-Server hat eine eigene lokale Review-Datenbank; für dieselben Korrekturen auf allen Geräten die zentrale NAS-Seite benutzen. Git synchronisiert den Anwendungscode, nicht deine Aufnahmen oder Datenbanken.

Für lokale Transkription auf Apple Silicon:

```sh
.venv/bin/python -m pip install -r requirements-worker.txt
.venv/bin/python worker_mac.py --help
```

MossFormer2 verwendet eine zusätzliche isolierte Umgebung; Einrichtung steht in `tools/speech-lab/README.md`. Modelle werden nicht über Git verteilt. Der RNNoise-Download und die FFmpeg-Prüfung sind mit `python3 scripts/setup_keepsaid.py` ausführbar. Lokale MLX-Transkription setzt einen Apple-Silicon-Mac voraus; die Review-Oberfläche kann auch auf anderen Rechnern laufen.

## Bestehende Installation

Die Oberfläche heißt Keepsaid. Die bisherige Tailscale-Adresse und NAS-/Dienstnamen bleiben als kompatible Infrastrukturkennungen erhalten. Ein Umbenennen dieser Speicherpfade ist für den neuen App-Namen nicht erforderlich. Neue Konfiguration verwendet `KEEPSAID_*`, bestehende `SOUNDCORE_*`-Variablen funktionieren weiterhin. Der bisherige lokale Ordner bleibt wegen laufender Worker und gemeinsamer Sperrdateien vorerst unverändert.

## Private Dateien

`.env`, Aufnahmen, Transkripte, Stimmprofile, SQLite-Datenbanken, Modellgewichte und Arbeitsverzeichnisse sind in `.gitignore` ausgeschlossen. Jeder Rechner bekommt seine eigene `.env`. Bestehende Datenbanken und NAS-Daten separat sichern.

## Technische Dokumentation der bestehenden Installation

Rechenknecht für lokale Transkription mit [mlx-whisper](https://github.com/ml-explore/mlx-examples/tree/main/whisper) auf Apple Silicon sowie Cloud-Beschleunigung via Groq für kurze Sprachnachrichten und Titel. Ein Server schickt später Audiodateien, die hier transkribiert werden.

## Inhalt

- `.venv/`: Python-3.12-venv (mit `uv` erstellt) mit `mlx-whisper`, `groq`, `sherpa-onnx` und `numpy`
- Modelle liegen im Hugging-Face-Cache (`~/.cache/huggingface/hub`), nicht in diesem Ordner:
  - `mlx-community/whisper-large-v3-turbo` (geladen)
  - `mlx-community/whisper-large-v3-mlx` (noch nicht geladen, zu wenig Platz)

## venv aktivieren

```bash
cd ~/Programming/soundcore-worker
source .venv/bin/activate
```

## Kurztest

```bash
python -c "import numpy as np, mlx_whisper; print(mlx_whisper.transcribe(np.zeros(32000, dtype=np.float32), path_or_hf_repo='mlx-community/whisper-large-v3-turbo')['text'])"
```

Für echte Audiodateien (`mlx_whisper.transcribe('datei.m4a', ...)`) wird `ffmpeg` gebraucht (vorhanden unter `/opt/homebrew/bin/ffmpeg`).

## Groq Cloud Integration (Option)

Groq beschleunigt die Verarbeitung für kurze Sprachaufnahmen und die Titelgenerierung:

- **Kurze Sprachnachrichten (Audio):** Werden bei gesetztem `GROQ_API_KEY` in Sekundenbruchteilen über Groqs `whisper-large-v3` / `whisper-large-v3-turbo` transkribiert (Standard: bis zu 10 Minuten bzw. 24 MB).
- **Längere Aufnahmen:** Laufen wie gewohnt voll lokal und ressourcenschonend auf dem Mac über Apple Silicon `mlx-whisper`.
- **Titel:** Werden über ein schnelles Groq-LLM (`qwen/qwen3.8-27b` oder `openai/gpt-oss-20b`) generiert, ohne lokalen RAM mit einem MLX-Modell zu belegen. Fällt automatisch auf lokales `Qwen3-4B` zurück, falls kein Key vorhanden oder Groq offline ist.

Konfiguration in `.env`:
```env
GROQ_API_KEY=gsk_...
GROQ_MAX_SEKUNDEN=600   # Maximale Länge für Groq (Standard: 600s = 10 min)
GROQ_MAX_MB=24          # Maximales Upload-Limit für Groq Audio
```

Worker mit Groq starten (Standard):
```bash
.venv/bin/python worker_mac.py --loop
```

Groq für einen Lauf erzwingend deaktivieren:
```bash
.venv/bin/python worker_mac.py --loop --ohne-groq
```

Kurze Aufnahmen warten nicht auf den MossFormer-Neulauf: Die gemeinsame GPU-Sperre
`work/.worker.lock` gilt nur für die lokale Transkription mit mlx-whisper. Groq-Transkription
und Sprechertrennung (sherpa-onnx, CPU) laufen parallel. Ist die GPU belegt, wird eine lange
Aufnahme zurückgestellt, bis alle kurzen fertig sind; erst dann wartet der Worker auf die GPU.
`--limit` zählt nur lokale Transkriptionen, Groq-Aufnahmen werden immer vollständig abgearbeitet.

Aufnahmen bis `KEEPSAID_OHNE_SPRECHER_BIS` Sekunden (Standard 120) bekommen keine
Sprechertrennung, sondern durchgehend `speaker1`. Der Stimmabdruck wird trotzdem gebildet,
damit Namensvorschläge und die Benennung im Review weiter funktionieren.

## Stimmen im Browser prüfen

Die Oberfläche läuft auf dem NAS direkt neben den Audiodateien:
**https://keepsaid.example.ts.net/review/** (mit Tailscale erreichbar).
Der Mac muss dafür nicht laufen. ffmpeg schneidet lokal auf dem NAS aus;
zum Browser wird ausschließlich die kurze WAV-Stimmprobe übertragen.

Die bestehende TrueNAS-App `soundcore` enthält den zusätzlichen Dienst `review`.
Code: `/mnt/App-Data/soundcore/review-app`, Datenbank und Titel:
`/mnt/App-Data/soundcore/review-state/`. Dieses Verzeichnis sichern.
Die Audiodaten sind für den Review-Dienst nur lesbar eingebunden. Proxy und
Dienst werden von TrueNAS/Tailscale nach einem Neustart wiederhergestellt.
`deploy/truenas_review.py` dokumentiert die Einrichtung und sichert vorherige
Konfigurationen unter `config/review-backup-<Zeitstempel>` auf dem NAS.

Der Mac-Worker lädt ab seinem nächsten Prozessstart vor jeder Sprecherzuordnung
einen konsistenten Snapshot der Server-Bibliothek nach
`review/server-stimmen.sqlite3`. So gelten auch korrigierte oder zurückgenommene
Bestätigungen. Ohne Verbindung nutzt er den letzten Snapshot, anfangs die alte
lokale Bibliothek. `SOUNDCORE_REVIEW_URL` erlaubt eine andere Review-Adresse.

Für Entwicklung oder eine eigenständige lokale Oberfläche:

```bash
.venv/bin/python review_server.py
```

Öffne **http://localhost:8765**. Der Server läuft ausschließlich lokal und braucht
Python 3 sowie `ffmpeg`, keine weiteren Web-Abhängigkeiten. Für den Share muss
Tailscale verbunden sein.

Die Startseite ist ein Dashboard mit Aufnahmen gesamt, bekannten Personen, bestätigten
Sprecherzuordnungen und einer Liste der Aufnahmen mit offenen Stimmen. Die Zahlen
werden aus den verfügbaren Transkripten und den gespeicherten Bestätigungen berechnet;
automatische Namensvorschläge zählen nicht als bestätigt.

1. Eine Aufnahme links oder aus der Liste der offenen Prüfungen öffnen.
2. Oben steht ein gemeinsamer Audioplayer für die Aufnahme. Ein Klick auf einen
   Transkriptblock springt zu dessen Zeitmarke und startet die Wiedergabe.
3. Der Stift neben jedem Namen öffnet direkt in der Zeile ein Eingabefeld und wird
   zum Bestätigungshaken. Bekannte Namen erscheinen darunter nach Stimmähnlichkeit
   sortiert und werden beim Tippen gefiltert. Einen Vorschlag auswählen und mit Haken
   oder Enter bestätigen; Escape oder × bricht ab. Namen gelten für diese Stimme
   in der gesamten Aufnahme. Bei mehreren Personen ist jeder Name separat bearbeitbar.
4. Der Stift neben dem gesprochenen Text öffnet den Editor. Mit „Text speichern“ sichern;
   „Original einsetzen“ lädt den Originaltext in den Editor. Ungespeicherte Entwürfe
   bleiben bei Seiten-, Filter- und Aufnahmewechseln erhalten.
5. „+ Person hinzufügen“ ergänzt eine weitere Person mit Name/Bezeichnung und Start-/Endsekunde.
   Mehrere Einträge können vorbereitet und einzeln gespeichert werden. Alternativ eine vorhandene
   Person als Ziel wählen. Ohne Namen wird eine Bezeichnung wie „Person 3“ vergeben.
   Zeitangaben beziehen sich auf den Aufnahmebeginn und erlauben Nachkommastellen.
6. „Zusammenfassung erstellen“ erzeugt auf Knopfdruck eine deutsche Zusammenfassung aus
   dem korrigierten Transkript über Groq. Bei langen Aufnahmen werden alle Textteile einzeln
   zusammengefasst und anschließend zusammengeführt. Ergebnisse werden dauerhaft in
   `zusammenfassungen.sqlite3` neben der Stimmen-Datenbank gespeichert. Text- und
   Namensänderungen markieren eine vorhandene Zusammenfassung als veraltet.
7. „Noch zu prüfen“ filtert Abschnitte mit unbenannten Stimmen. Lange Transkripte haben
   Seiten mit 40 Abschnitten. Unter „Gespeicherte Zeitkorrekturen“ lässt sich die letzte
   zeitliche Aufteilung zurücknehmen. Ohne Wortzeitstempel bleiben Textabschnitte gemeinsam bearbeitbar.

Der gemeinsame Player streamt die Originaldatei über `/api/audio` mit HTTP-Range-
Unterstützung direkt vom NAS. Vor dem ersten Abspielen ist `preload=none` gesetzt.
Die kurzen Vorschauen in der Personenbearbeitung bleiben auf 15 Sekunden begrenzt.

Für die Zusammenfassung liest der Review-Dienst `GROQ_API_KEY` aus seiner Umgebung
oder aus `/app/.env` (NAS: `review-app/.env`, nur für Dienstbenutzer 568 lesbar).
`GROQ_ZUSAMMENFASSUNG_MODELL` erlaubt ein anderes Modell; Standard ist `openai/gpt-oss-20b`.
Die Datei mit dem Schlüssel nicht veröffentlichen. Die Datenbank `review-state/zusammenfassungen.sqlite3`
bei Sicherungen mit aufnehmen.

Zeitliche Aufteilungen werden in derselben Datenbank gespeichert. Sie gelten in
der Review-Oberfläche und ändern noch keine Worker-Exporte. Wird die ursprüngliche
Sprechertrennung neu erzeugt, werden nicht mehr passende Aufteilungen nicht angewandt.

Textkorrekturen liegen ebenfalls in `review/stimmen.sqlite3` und erscheinen nach
einem Neustart wieder in der Oberfläche. Sie ändern keine Dateien auf dem Share
und werden noch nicht in Worker-Exporte übernommen. Verändert sich ein ursprünglicher
Textabschnitt, wird dessen alte Korrektur nicht auf den neuen Text angewandt.

Stimmproben verwenden vorhandene Originale in `work/` oder im lokalen
Datenverzeichnis. Sonst greift ffmpeg per HTTP-Range direkt auf die benötigte
Stelle auf dem Share zu: Es werden nur kleine Datenbereiche statt der ganzen
Aufnahme abgerufen. Der Share muss Range-Anfragen unterstützen (HTTP 206).
Fertige WAV-Ausschnitte werden zusätzlich im Arbeitsspeicher wiederverwendet.
Es gibt keinen stillen Fallback auf den Download der gesamten Aufnahme.

Bestätigungen liegen in `review/stimmen.sqlite3`. Diese Datei sichern: Sie enthält
die neuen Lernbeispiele. Eine erneute Bestätigung derselben Stimme ersetzt das
bisherige Beispiel, statt es mehrfach zu zählen. Zurücknehmen entfernt es wieder.
Ein Name gilt für den gesamten aktuellen Sprechercluster. Enthält ein Cluster
mehrere Personen, zuerst zeitlich aufteilen. Die beteiligten Stimmen lassen sich
danach manuell benennen; ihre bisherigen Stimmabdrücke werden als Lernreferenz
entfernt und auch nach einer Rücknahme nicht automatisch wieder aktiviert.

Der Worker liest diese Bibliothek ab dem nächsten Prozessstart bei jeder Aufnahme.
Er vergleicht nur Stimmabdrücke desselben Modells und derselben Dimension.
Cosinus-Ähnlichkeit ab 0,75 und ein Abstand von mindestens 0,08 zum zweitbesten
Namen gelten zunächst als eindeutiger Vorschlag. Das sind konservative Startwerte,
keine kalibrierten Wahrscheinlichkeiten. Automatische Vorschläge werden nie als
bestätigte Lernbeispiele gespeichert. Das Modell selbst wird nicht neu trainiert;
die Wiedererkennung nutzt zunehmend bestätigte Referenzbeispiele.

Die bisherigen Einträge aus `sprecher_profile.json` sind als Namen verfügbar.
Ihre alten 512-dimensionalen Vektoren ohne Modellangabe werden nicht mit den
aktuellen 256-dimensionalen Vektoren verglichen. Referenzen mit explizitem
`modell` können bei passender Dimension ebenfalls verglichen werden.

Neue Transkript-JSONs enthalten `sprecher_namen` und `speaker_name` je Segment;
die stabilen `speaker`-IDs bleiben erhalten. Stimmprofile enthalten `namensvorschlag`
und `name_bestaetigt`. Eindeutige Vorschläge erscheinen als Namen im Markdown.
Bestehende Dateien auf dem Share werden durch die Review-Oberfläche nicht geändert;
Korrekturen gelten lokal und für künftige Worker-Läufe auf diesem Mac. Ein bereits
laufender Worker muss nach seinem aktuellen Lauf neu gestartet werden.

Für lokale Daten statt Share:

```bash
python3 review_server.py --daten /pfad/zum/share --port 8766 --db /pfad/stimmen.sqlite3
```

Das Verzeichnis braucht `audio/`, `transkripte/` und `stimmprofile/` mit denselben
Dateinamen wie auf dem Share. `--share URL` erlaubt einen anderen Share.

Tests (inklusive Audio-Konvertierung mit ffmpeg):

```bash
.venv/bin/python -m unittest discover -s tests -v
```

### Inhaltstitel und Aufnahmedatum

Die Aufnahmeliste zeigt oben einen Inhaltstitel und darunter das Aufnahmedatum
mit Uhrzeit. Titel und Datum stammen bevorzugt aus den Aufnahme-Metadaten
(`audio/<name>.json`, Felder `title` und `recorded_at`). Reine Zeitstempel zählen
nicht als Titel. Die Suche findet sowohl Titel als auch Datum und Dateinamen.

Fehlende Titel erzeugt der Worker mit Groq (oder lokal mit
`mlx-community/Qwen3-4B-Instruct-2507-4bit` als Fallback).

Die Titel werden in `review/titel.sqlite3` gespeichert und in neue
Transkript-JSONs als `titel` übernommen. Vorhandene Metadatentitel haben Vorrang.
Bei fehlendem Modell oder einem Fehler verarbeitet der Worker die Aufnahme
weiter; die Titelgenerierung blockiert den Transkript-Export nicht durch einen
Fehler. KI-Titel geben eine grobe Orientierung und können ungenau sein.

Für historische Transkripte kann eine JSON-Liste mit `name` und `text` importiert
werden; bereits erzeugte Titel werden wiederverwendet:

```bash
.venv/bin/python titel.py --import-transkripte /pfad/transkripte.json
```

Unsichere Sprecher bleiben zur manuellen Prüfung auf der Website: Kurze Redezeit
ist kein Grund mehr, eine Stimme einer anderen Person zuzuschlagen. Beim
Zusammenführen müssen alle beteiligten Stimmabdrücke mindestens die konfigurierte
Ähnlichkeit erreichen. Ohne Stimmabdruck bleibt eine Stimme ebenfalls sichtbar
und manuell benennbar. Neue Exporte führen unklare Stimmen in `sprecher_pruefen`;
unsichere Namensvorschläge bleiben ohne `speaker_name`.

### Wortgenaue Wiedergabe

Neue Worker-Transkripte speichern echte `words` mit `word`, `start` und `end`
(MLX: `word_timestamps=True`, Groq: Wort- und Segmentzeitstempel). Der laufende
Mac-Worker übernimmt die Änderung erst beim nächsten Prozessstart.

Für vorhandene Aufnahmen gibt es im Review „Wortzeiten erstellen“. Der NAS liest
die Originalaufnahme und lässt sie in überlappenden 4-Minuten-Ausschnitten mit Groq
neu erkennen. Das passiert im Hintergrund; der Mac muss dafür nicht laufen.
Die Zeiten werden in `review-state/wortzeiten.sqlite3` gespeichert. Diese Datei sichern.
Mehrfachklicks starten keinen zweiten Job für dieselbe Audiodatei.

Der Review-Text wird dabei nicht ersetzt: Passende Wörter werden mit ihren tatsächlich
erkannten Zeiten verknüpft. Ein Klick spielt ab diesem Wort weiter, das gerade gesprochene
Wort wird hervorgehoben. Textkorrekturen bleiben erhalten. Neue oder abweichende Wörter,
die sich nicht sicher zuordnen lassen, bleiben punktiert unterstrichen ohne erfundene Zeit.
Auch die Sprungmarke eines ganzen Textblocks wird aus den Wortzeiten abgeleitet;
fehlerhafte alte Abschnittszeiten dienen nicht mehr als Ersatz. Ändert sich eine
Audiodatei, werden deren alte Wortzeiten nicht mehr angewandt.

Im Transkript schaltet „Nicht gesprochen“ die Wortentfernung für einen Abschnitt ein.
Ein Klick auf ein Wort entfernt es sofort als gespeicherte Textkorrektur, auch ohne
sichere Wortzeit. „Wort zurückholen“ nimmt die letzte Entfernung zurück; „Fertig“
kehrt zur Wiedergabe zurück. Offene Textentwürfe müssen vorher gespeichert werden.

### Vereinfachte Aufnahmeprüfung

Die Aufnahmeliste zeigt die Laufzeit als Minuten:Sekunden. Das Keepsaid-Logo
führt zum Dashboard; die zusätzlichen Dashboard-Schaltflächen und der Abschnittsfilter
entfallen. Oben erscheinen nur benannte Personen, sonst ein einzelner Punkt.
Der Player bietet Sprünge um zehn Sekunden in beide Richtungen.
„Nicht gesprochen“ steht unten links neben „Person hinzufügen“.

Beim Hinzufügen einer Person reichen Name, Beginn und Ende im Format `Minuten:Sekunden,00`.
Zwei Schieberegler passen den Bereich innerhalb des Abschnitts an; bekannte Namen
können erneut verwendet werden. Der bisherige Sprecher wird aus dem Zeitbereich ermittelt.

Auf dem NAS reiht der Review-Server beim Start alle lokalen Aufnahmen ohne eingebettete
Wortzeiten automatisch ein (abschaltbar mit `SOUNDCORE_AUTO_WORTZEITEN=0`).
Die Verarbeitung läuft seriell; gespeicherte Wortzeiten werden wiederverwendet.
Abgeschlossene Vier-Minuten-Stücke werden in `wortzeiten.sqlite3` zwischengespeichert,
damit ein Neustart keine erneute Verarbeitung der fertigen Stücke erfordert.
Bei API-Kontingentgrenzen wird mit Wartezeit erneut versucht. Wörter ohne zuverlässigen
Treffer bleiben offen; die automatische Verarbeitung garantiert keine vollständige Zuordnung.

### Ladezeiten

Review-HTML und JSON werden bei Browser-Unterstützung mit gzip komprimiert.
Audio bleibt unverändert und unterstützt weiterhin HTTP-Range. Der JSON-Cache
hält bis zu 512 Quelldateien, damit die Bibliotheksübersicht nicht ständig
dieselben Dateien neu einliest. Beim Aufnahmewechsel werden veraltete
Detail- und Zusammenfassungsanfragen sowie der alte Audiostream abgebrochen.
Der Player lädt Audio erst nach einer Wiedergabe- oder Sprungaktion.

### Sprecherblöcke und Hörfassungen

Die Review-Ansicht bildet zusammenhängende Beiträge pro bestätigtem Namen.
Verschiedene Sprecher-IDs mit demselben Namen werden zusammengefasst. Wortzeiten
trennen Beiträge bei einem Sprecherwechsel innerhalb eines ursprünglichen Abschnitts.
Wörter behalten ihre echten Audiopositionen. Ohne Wortanker springt ein Block zu
seiner vorhandenen, gültigen Abschnittszeit; Zeiten außerhalb der Aufnahme werden
nicht als Sprungziel verwendet. Leere Textkorrekturen erscheinen nicht als Blöcke.

Textänderungen am Block werden auf seine Quellspannen verteilt und in einer
SQLite-Transaktion gespeichert. Andere Sprechertexte bleiben erhalten; konkurrierende
Änderungen werden zurückgewiesen. Nach dem Speichern schließt sich das Textfeld.
„Wort zurückholen“ kann die letzte Entfernung auch nach dem Löschen eines ganzen
Blocks zurücknehmen (in derselben Serversitzung).

Unter `review-state/hoerfassungen/` entstehen automatisch Mono-MP3-Dateien mit
32 kbit/s, 24 kHz und Suchindex. Die Quelldateien werden nicht verändert. Ein
Hintergrundprozess bereitet alle lokalen Aufnahmen vor; die gerade ausgewählte
Aufnahme wird vor wartenden Dateien priorisiert. `/api/audio-status` zeigt den
Zustand, `/api/audio?name=…&kompakt=1` liefert die fertige Hörfassung mit Range-Support.
Der Player zeigt die Vorbereitung an. Bei fertigen Hörfassungen lädt er nur die
Metadaten vorab; Wiedergabe und Sprünge laden die benötigten Audioabschnitte.
Versionierte Hörfassungen dürfen einen Tag im privaten Browser-Cache bleiben.
Der Hörfassungsordner ist ein neu erzeugbarer Cache und benötigt keine Datensicherung.

### Rauschfilter und erneute Transkription

Im Player schaltet „Rauschen reduzieren“ zwischen normaler und gefilterter Hörfassung
um. Der RNNoise-Filter `arnndn` verwendet das Modell `bd.rnnn` (SHA256 in
`modelle/rauschfilter/README.md`), einen Hochpass bei 70 Hz und 85 % gefilterten Anteil.
Er reduziert Aufnahmegeräusche, garantiert aber keine vollständige Entfernung
von Hall, Wind oder überlagerter Sprache. Die Zeitachse bleibt erhalten.
Die Filterung läuft vor der MP3-Kompression; für ASR wird direkt aus der Quelle
ein gefiltertes 16-kHz-Mono-WAV erzeugt.

Das NAS-Image wird mit `deploy/Dockerfile.review` als `soundcore-review:rnnoise-v1`
gebaut; es enthält vollständiges FFmpeg mit `arnndn`. Die normale Copyparty-Variante
enthält diese Filter nicht. `deploy/enable_noise.py` sichert Konfiguration und alle
Review-Datenbanken und aktiviert das neue Image mit `--neu-alle`.
Die beiden Mac-Transkriptionsskripte filtern neue Aufnahmen ebenfalls vor ASR.

`--neu-alle` reiht alle vorhandenen Aufnahmen in `neutranskription.sqlite3` ein,
sortiert nach tatsächlicher Dauer (kürzeste zuerst). Neustarts übernehmen die
Warteschlange. Fertige Vier-Minuten-Teile werden wiederverwendet. Groq-Kontingentgrenzen
führen zu automatischen Wartezeiten; Fortschritt und Pausen stehen auf der Website.
Der ältere automatische Wortzeiten-Lauf wird während dieses Neulaufs nicht parallel
gestartet. Die neuen Transkripte enthalten zugleich die neuen Wortzeiten.

Unbearbeitete Texte wechseln zur neuen Fassung. Bereits manuell korrigierte
Aufnahmen behalten ihre Fassung; das neue Ergebnis ist daneben zum Vergleichen
verfügbar. Einmal aktivierte Textgrundlagen bleiben stabil, damit auch anschließend
gespeicherte Korrekturen gültig bleiben. Bestätigte Namen, Sprecher-IDs und manuelle
Zeitaufteilungen bleiben erhalten. Die ursprünglichen Share-Dateien werden nicht
überschrieben. Für eine vollständige Sicherung deshalb **den gesamten Review-State**,
insbesondere `stimmen.sqlite3` und `neutranskription.sqlite3`, gemeinsam sichern.

Der zusätzliche Mac-Worker `neu_mlx_worker.py` übernimmt über die Review-API
exklusiv einzelne wartende Aufträge. Er nutzt denselben Rauschfilter und lokales
Whisper large-v3 über MLX und ist dadurch unabhängig vom Groq-Kontingent.
Das Modell wird beim ersten Aufruf in den Hugging-Face-Cache geladen.
`dev.keepsaid.soundcore-neulauf` ist als LaunchAgent registriert: Fehler starten
den Prozess erneut, ein vollständig abgeschlossener Lauf beendet ihn erfolgreich.
Log: `work/neulauf.log`; lokale Zwischenergebnisse: `work/neulauf/`.
Der Worker teilt die Sperre mit `worker_mac.py`, verarbeitet begrenzte Vier-Minuten-
Stücke und lädt Ergebnisse mit echten Wortzeiten auf das NAS. Dateidauer, Quellversion
und exklusive Arbeitsübernahme werden beim Upload geprüft. Bei leerer Warteschlange
kann er auch einen durch Groq-Kontingent wartenden Auftrag übernehmen.
