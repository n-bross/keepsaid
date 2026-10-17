"""Transkribiert eine Aufnahme vom soundcore-Share auf einem Mac mit Apple-Chip (mlx-whisper oder Groq).

Wird vom Server angestoßen und liegt dort unter /srv/projects/soundcore-export/worker/. Auf dem Mac läuft es
im venv von ~/Programming/soundcore-worker. Zugriff auf den Share über die Tailscale-Identität, ohne Passwort.

    python transcribe_mlx.py --stufe schnell 2026-09-19_215217_2026-09-19-215217

Ergebnis auf dem Share unter transkripte/:
    <name>.<stufe>.json   Segmente mit Zeitstempeln + Messwerte
    <name>.<stufe>.md     lesbares Transkript
Die letzte Zeile der Ausgabe ist eine JSON-Zusammenfassung für den Server.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from keepsaid_config import env, DEFAULT_SHARE
from wortzeiten import neue_segmente
from rauschfilter import wav_bereinigen, VERSION as RAUSCHFILTER_VERSION

SHARE = env("SHARE_URL", DEFAULT_SHARE)

MODELLE = {
    "schnell": "mlx-community/whisper-large-v3-turbo",
    "gruendlich": "mlx-community/whisper-large-v3-mlx",
}
GROQ_MODELLE = {
    "schnell": "whisper-large-v3-turbo",
    "gruendlich": "whisper-large-v3",
}

WORK = Path(__file__).resolve().parent / "work"


def lade_env() -> None:
    env_pfad = Path(__file__).resolve().parent / ".env"
    if env_pfad.exists():
        for zeile in env_pfad.read_text(encoding="utf-8").splitlines():
            zeile = zeile.strip()
            if zeile and not zeile.startswith("#") and "=" in zeile:
                k, v = zeile.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))


lade_env()


def curl(*args: str) -> None:
    subprocess.run(["curl", "-sSf", "--max-time", "900", *args], check=True)


def zeit(sekunden: float) -> str:
    s = int(sekunden)
    return f"{s // 3600:d}:{s // 60 % 60:02d}:{s % 60:02d}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("name", help="Dateiname ohne Endung, wie in audio/")
    parser.add_argument("--stufe", choices=MODELLE, default="schnell")
    parser.add_argument("--endung", default=".ogg")
    parser.add_argument("--ohne-groq", action="store_true", help="Groq deaktivieren")
    args = parser.parse_args()

    WORK.mkdir(exist_ok=True)
    audio = WORK / f"{args.name}{args.endung}"
    curl("-o", str(audio), f"{SHARE}/audio/{args.name}{args.endung}")

    quelle = audio
    audio = WORK / f"{args.name}.bereinigt.wav"
    wav_bereinigen(quelle, audio)

    segmente = []
    genutztes_modell = MODELLE[args.stufe]
    dauer_rechnen = 0.0

    groq_key = os.environ.get("GROQ_API_KEY")
    groq_max_sek = float(os.environ.get("GROQ_MAX_SEKUNDEN", "600"))
    dateigroesse_mb = audio.stat().st_size / (1024 * 1024) if audio.exists() else 0.0

    # Versuche Groq bei kleinen/kurzen Dateien (< 24 MB)
    if groq_key and not args.ohne_groq and dateigroesse_mb <= 24.0:
        groq_modell = GROQ_MODELLE.get(args.stufe, "whisper-large-v3-turbo")
        try:
            start = time.time()
            import groq
            client = groq.Groq(api_key=groq_key)
            with open(audio, "rb") as f:
                res = client.audio.transcriptions.create(
                    file=f,
                    model=groq_modell,
                    response_format="verbose_json",
                        timestamp_granularities=["word", "segment"],
                    language="de",
                    temperature=0.0,
                )
            dauer_rechnen = time.time() - start
            groq_segmente = getattr(res, "segments", None) or []
            segmente = neue_segmente(groq_segmente, getattr(res, "words", None) or [])
            dauer_audio = segmente[-1]["end"] if segmente else 0.0
            # Wenn Datei unerwartet doch zu lang war für Groq-Konvention, war sie trotzdem erfolgreich transkribiert
            genutztes_modell = f"groq/{groq_modell}"
        except Exception:
            segmente = []

    try:
        if not segmente:
            import mlx_whisper
            start = time.time()
            result = mlx_whisper.transcribe(
                str(audio),
                path_or_hf_repo=MODELLE[args.stufe],
                language="de",
                # verhindert, dass sich Whisper nach Pausen in Wiederholungsschleifen verrennt
                condition_on_previous_text=False,
                word_timestamps=True,
            )
            dauer_rechnen = time.time() - start
            segmente = neue_segmente(result["segments"])
            genutztes_modell = MODELLE[args.stufe]
    finally:
        audio.unlink(missing_ok=True)
        quelle.unlink(missing_ok=True)

    dauer_audio = segmente[-1]["end"] if segmente else 0.0

    summary = {
        "name": args.name,
        "stufe": args.stufe,
        "modell": genutztes_modell,
        "rauschfilter": RAUSCHFILTER_VERSION,
        "audio_sekunden": round(dauer_audio, 1),
        "rechen_sekunden": round(dauer_rechnen, 1),
        "faktor": round(dauer_rechnen / dauer_audio, 3) if dauer_audio else None,
        "segmente": len(segmente),
        "woerter": sum(len(s["text"].split()) for s in segmente),
        "erstellt": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }

    json_pfad = WORK / f"{args.name}.{args.stufe}.json"
    md_pfad = WORK / f"{args.name}.{args.stufe}.md"
    json_pfad.write_text(json.dumps({**summary, "segments": segmente}, ensure_ascii=False, indent=2), encoding="utf-8")
    zeilen = [f"# {args.name}", "", f"_Stufe: {args.stufe} · Modell: {genutztes_modell} · {summary['erstellt']}_", ""]
    zeilen += [f"**[{zeit(s['start'])}]** {s['text']}" for s in segmente]
    md_pfad.write_text("\n\n".join(zeilen) + "\n", encoding="utf-8")

    try:
        for pfad in (json_pfad, md_pfad):
            curl("-T", str(pfad), f"{SHARE}/transkripte/{pfad.name}?replace", "-o", "/dev/null")
    finally:
        json_pfad.unlink(missing_ok=True)
        md_pfad.unlink(missing_ok=True)

    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
