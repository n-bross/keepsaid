# Lokaler Vergleich von Sprachmodellen

Isolierte Python-3.11-Umgebung für Hörbeispiele und den produktiven MossFormer2-Filter.

- ClearVoice 0.1.2 / MossFormer2_SE_48K: https://github.com/modelscope/ClearerVoice-Studio
- Resemble Enhance, unveränderter Quellcode: https://github.com/resemble-ai/resemble-enhance
- Konkrete Paketversionen: requirements.lock.txt

Aus dem Projektverzeichnis:

```
tools/speech-lab/.venv/bin/python scripts/sprachmodelle_vergleich.py mossformer2
tools/speech-lab/.venv/bin/python scripts/sprachmodelle_vergleich.py resemble
tools/speech-lab/.venv/bin/python scripts/sprachmodelle_vergleich.py frcrn
tools/speech-lab/.venv/bin/python scripts/sprachmodelle_vergleich.py mossformergan
tools/speech-lab/.venv/bin/python scripts/sprachmodelle_vergleich.py seite
tools/speech-lab/.venv/bin/python scripts/sprachmodelle_vergleich.py alle
tools/speech-lab/.venv/bin/python scripts/sprachmodelle_vergleich.py voicefixer
tools/speech-lab/.venv/bin/python scripts/flowse_hoerprobe.py
tools/speech-lab/.venv/bin/python scripts/sprachmodelle_vergleich.py restauration
```

Eingang: erste 30 Sekunden der Aufnahme vom 8. Oktober 2026, 09:19:50; mono, 48 kHz, mit gemeinsamem Headroom (Faktor 0.5). Kein RNNoise/DeepFilterNet davor.

Die Vergleichsseite enthält zusätzlich eine originale Hörprobe; die drei Dateien werden mit konstanter Verstärkung je Datei in der Lautstärke angenähert. Rohresultate bleiben als WAV erhalten. Die portable HTML enthält alle drei MP3-Dateien.

FRCRN und MossFormerGAN sind weitere ClearVoice-Modelle. Neue ClearVoice-Rohresultate werden als Float-WAV mit der nativen Abtastrate gespeichert, um sehr leise Ergebnisse vor dem Pegelausgleich nicht auf 16 Bit zu quantisieren. MossFormerGAN verwendet die CPU, weil torch.angle für komplexe Spektren auf MPS nicht unterstützt wird.

SAM Audio wurde noch nicht ausgeführt: keine Hugging-Face-Anmeldung hinterlegt, Modellzugang manuell freizuschalten, Abruf der Gewichte HTTP 401. Offizieller Zugang: https://huggingface.co/facebook/sam-audio-small. Kein Ersatzmodell als SAM Audio ausgegeben und keine Originalaufnahme an einen externen Testdienst geschickt.

Weitere lokale Tests:

- VoiceFixer 0.1.3, Modus 0, CPU. Offizielle Gewichte werden aus Zenodo geladen: https://github.com/haoheliu/voicefixer
- FlowSE: https://github.com/Honee-W/FlowSE, offizieller Checkpoint https://huggingface.co/flowse/wenetspeech4tts_Premium.pt.tar. Ohne Textvorgabe, 32 Euler-Schritte, CFG 1, Seed 0; Vocos-Vocoder aus charactr/vocos-mel-24khz. Verwendet auf dem Mac MPS, sonst CPU. Für den Mac in 5-Sekunden-Kernen mit jeweils 0,5 Sekunden Kontext verarbeitet und überlappend zusammengeblendet; der ungeteilte Durchlauf war zu langsam.
- Modellgewichte liegen in flowse-model/; der Download umfasst rund 4 GB. x-transformers ist entsprechend der FlowSE-Requirements auf 1.43.2 festgelegt.
- Vergleich: beispiele/filtervergleich/voicefixer-flowse.html, einschließlich Original und MossFormer2. Portable HTML und Rezept mit Einstellungen werden daneben erstellt.
- FlowSE verwendet MPS-Autocast mit Float16 für das große Netz und Float32 auf der CPU für den Vocos-Vocoder (iSTFT ist auf der verwendeten MPS-Version unvollständig). Zwischenstände der Abschnitte werden als NumPy-Dateien gespeichert; Seed ist der jeweilige Abschnittsindex. Originale bleiben unverändert.

Der lokale Hörvergleich auf http://127.0.0.1:8769/ läuft unabhängig von T3 als Benutzer-LaunchAgent `dev.keepsaid.soundcore-hoervergleich`. Konfiguration: `~/Library/LaunchAgents/dev.keepsaid.soundcore-hoervergleich.plist`, Protokoll: `work/hoervergleich-server.log`. Er startet bei der Benutzeranmeldung und nach einem Prozessabbruch automatisch. Der Server ist ausschließlich an die lokale Loopback-Adresse gebunden.

## MossFormer2-Neulauf

`moss_worker.py` läuft als Benutzer-LaunchAgent `dev.keepsaid.soundcore-neulauf`, Log `work/moss.log`. Die NAS-Datenbank `review-state/mosslaeufe.sqlite3` enthält pro Aufnahme drei getrennte Aufträge: Filter, Groq und MacBook. Kurze Aufnahmen zuerst; beide ASR-Anbieter müssen unabhängig fertig werden. Groq-Kontingentfehler verzögern nur Groq. Checkpoints nach Filterabschnitten und ASR-Abschnitten ermöglichen die Fortsetzung nach einem Neustart. Der Agent prüft alle fünf Minuten erneut, auch nach vollständigem Abschluss.

MossFormer2 verarbeitet auf dem Mac 48-kHz-Mono-Audio in 60-Sekunden-Kernen mit 3 Sekunden Kontext. Kein Entfernen von Stille oder Ändern der Zeitachse. Die Filterausgabe wird auf exakte Länge geprüft, auf 16 kHz resampelt und als 24-Bit-FLAC für beide ASR-Läufe gespeichert. Die Hörfassung ist eine direkt suchbare 64-kbit/s-MP3 mit begrenzter konstanter Verstärkung. Dateien werden atomar auf dem NAS veröffentlicht. Bis zur Fertigstellung bleibt die bisherige Hörfassung verfügbar.

Groq nutzt `whisper-large-v3`, lokal `mlx-community/whisper-large-v3-mlx`; beide auf Deutsch, Temperatur 0, mit eigenen Wortzeitstempeln. 240-Sekunden-Kerne mit 2 Sekunden Überlappung; Wörter werden anhand ihrer Mitte genau einem Kern zugeordnet. Die daraus erzeugten Texte und Wortzeiten bleiben zusammen gespeichert. Groq wird bevorzugt, danach MacBook, bis dahin die bisherige Fassung. Sobald manuelle Korrekturen bestehen, bleibt deren aktive Textbasis eingefroren; beide neuen Ergebnisse sind als aufklappbare Vergleichsfassungen zugänglich. Quelltranskripte, bestätigte Namen und manuelle Sprecherbereiche bleiben erhalten.

Live-Status: `/review/api/moss`. Der Review-Dienst wird über `SOUNDCORE_MOSS=1` in seiner geschützten `.env` auf diese Pipeline umgestellt; der bisherige konkurrierende Groq-Neulauf und separate automatische Wortzeitlauf werden damit nicht weiter gestartet. Deployment und Sicherung: `deploy/enable_moss.py`.

Der reguläre Import-Worker ist auf eine neue Aufnahme je zweistündlichem Lauf begrenzt, damit er den gemeinsamen GPU-Lock nicht über den gesamten Bestand hält. Der Neulauf und der Import-Worker verwenden denselben Lock. MacBook und angemeldete Benutzersitzung müssen für die lokale Verarbeitung verfügbar sein; T3 kann geschlossen werden.

## Lesefassung

Die produktive Review-Seite bietet unter „Lesefassung“ eine zusätzliche, auf Anfrage erzeugte Textfassung. `lesefassung.py` speichert pro Aufnahme und Quellfingerabdruck die Ausgangstexte, realen Audiobereiche, bestätigten Namen und KI-Vorschläge in `lesefassungen.sqlite3`. Die Erstellung läuft asynchron und setzt nach Prozessneustarts mit dem nächsten fehlenden Abschnitt fort. Änderungen am Transkript führen zur Kennzeichnung „Transkript geändert“; vorhandene Vorschläge überschreiben niemals das Transkript oder seine Wortzeiten.

Modell: `GROQ_LESEFASSUNG_MODELL`, ansonsten das konfigurierte Titelmodell bzw. `qwen/qwen3.8-27b`. Textabschnitte bis 4500 Zeichen, Temperatur 0. Zahlen werden auf Erhaltung geprüft; verlorene Inhaltswörter lösen einen konservativeren Versuch aus. Wenige zusätzliche Inhaltswörter werden ausdrücklich als `[KI-Ergänzung: …]` markiert, umfangreichere Änderungen verworfen. Diese Prüfungen sind kein Nachweis semantischer Korrektheit; alle Texte bleiben KI-Vorschläge. Absatzverweise spielen die ursprünglichen Audioabschnitte ab, keine erfundenen Wortzeiten für umformulierte Sätze.

Test mit derselben Hörprobe und der ungefähren Mitschrift: http://127.0.0.1:8769/lesefassung-vergleich.html. Ergebnisse unter `work/whisperx-vergleich/lesefassungen-v5.json`; Generator `scripts/lesefassung_beispiel.py`. Portable HTML und reine Textfassungen in `beispiele/filtervergleich`. Deployment mit Code-/Konfigurationssicherung: `deploy/enable_lesefassung.py`.

## MossFormer2 auf einem weiteren Mac einrichten

Python 3.11 und FFmpeg vorausgesetzt, im Projektverzeichnis:

```sh
python3.11 -m venv tools/speech-lab/.venv
tools/speech-lab/.venv/bin/python -m pip install clearvoice==0.1.2
```

Die getesteten vollständigen Paketversionen stehen in `requirements.lock.txt`. Die zusätzlichen Vergleichsmodelle darin sind für MossFormer2 nicht erforderlich. Den offiziellen Checkpoint `MossFormer2_SE_48K/last_best_checkpoint.pt` aus [ClearVoice](https://huggingface.co/alibabasglab/MossFormer2_SE_48K) nach `checkpoints/MossFormer2_SE_48K/last_best_checkpoint.pt` herunterladen. Erwartete SHA256: `03692b9f773bbd6bb43b9c5a41f96b1e28affd66e13796b7bec66ad3d8b227c6`. Der Worker prüft diese vor jedem Lauf.

