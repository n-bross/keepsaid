# WhisperX-Vergleich

Isolierter Test, keine Umstellung des produktiven Neulaufs. Python 3.11, WhisperX 3.8.6; genaue Paketversionen in `requirements.lock.txt`.

Eingang: dieselben ersten 30 Sekunden von Hörprobe 3 (2026-10-08, 09:19:50), vorhandene MossFormer2-Ausgabe `beispiele/filtervergleich/anfang-mossformer2.wav`. Für alle Anbieter identisch auf 16 kHz Mono resampelt und mit einem konstanten, spitzenbegrenzten Gain angehoben. Quelle und SHA256 stehen in `work/whisperx-vergleich/source.json`. Alle Ergebnisse tragen dieselbe SHA256 der ASR-WAV-Datei.

Vergleich:

- Unser Groq-Pfad: `whisper-large-v3`, Deutsch, Temperatur 0, Wort- und Segmentzeitstempel; derselbe Request und dieselbe Abschnittsaufbereitung wie im produktiven Moss-Neulauf.
- Unser MacBook-Pfad: `mlx-community/whisper-large-v3-mlx`, Deutsch, Temperatur 0, `word_timestamps=True`, kein vorheriger Textkontext; gemeinsamer GPU-Lock mit dem produktiven Worker.
- WhisperX: `large-v3` über faster-whisper/CTranslate2, CPU int8, vier Threads, Batch 1, Silero VAD, Deutsch, Temperatur 0, kein vorheriger Textkontext. Kein zweites Entrauschen.
- Deutscher Wortabgleich: `VOXPOPULI_ASR_BASE_10K_DE`, CPU. Zusätzlich den unveränderten Groq- und MacBook-Text ausrichten, um den Einfluss der Wortzeiten vom Einfluss der ASR-Ausgabe zu trennen. `interpolate_method='ignore'`: fehlende Zeiten werden nicht interpoliert.

WhisperX verwendet denselben Whisper-Modelltyp, aber einen anderen Decoder, VAD und einen zusätzlichen Alignment-Schritt. Der Textvergleich prüft damit die gesamte Konfiguration. Nur der Modus „Gleicher Text wie links“ isoliert den Alignment-Schritt. Ohne manuell geprüfte Referenz sind Zeitverschiebungen oder Wortanzahlen kein Genauigkeitsmaß.

Ausführen:

```
tools/whisperx-test/.venv/bin/python scripts/whisperx_vergleich.py
tools/whisperx-test/.venv/bin/python scripts/whisperx_vergleich.py --align-current-only
.venv/bin/python scripts/whisperx_vergleich_seite.py
```

Ergebnisse und Laufzeiten: `work/whisperx-vergleich/*.json`. Neue Berechnung überspringt vorhandene Ergebnisdateien; vor einem Versuch mit anderen Einstellungen einen neuen Ergebnisordner verwenden. Der erstmalige Modell-Download ist in der separat gespeicherten Ladezeit enthalten; ASR- und Alignment-Zeiten werden getrennt gemessen.

Hör-/Mitlesevergleich: http://127.0.0.1:8769/whisperx-vergleich.html. Portable HTML, WAV, MP3 und Textdateien in `beispiele/filtervergleich`. Beide Player spielen exakt dieselbe Audiodatei; Unterschiede liegen ausschließlich in Text und Wortzeiten.

Quellen: https://github.com/m-bain/whisperX und https://huggingface.co/Systran/faster-whisper-large-v3.
