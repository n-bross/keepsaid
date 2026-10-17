"""Probiert für die Sprechertrennung mehrere Ähnlichkeitsschwellen durch und zählt die Sprecher.

Hintergrund: Mit der Vorgabe 0.5 fand sherpa-onnx in einer 14-Minuten-Aufnahme 40 Sprecher — viel zu viele.
Die Schwelle bestimmt, ab welcher Unähnlichkeit zwei Stimmen als verschiedene Personen gelten: kleiner Wert
= mehr Sprecher. Dieses Skript zeigt, wie die Zahl über die Schwelle läuft, damit wir einen Wert wählen,
der zur Wirklichkeit passt.

    python kalibrieren.py <name-ohne-endung> [weitere ...]
"""

import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from worker_mac import (SHARE, WORK, holen, liste, log, modelle_sicherstellen,  # noqa: E402
                        nach_wav, zeit)

SCHWELLEN = [0.4, 0.5, 0.6, 0.7, 0.8, 0.9]


def laufen(samples: np.ndarray, seg: Path, emb: Path, schwelle: float) -> dict:
    import sherpa_onnx
    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(seg))),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(emb)),
        clustering=sherpa_onnx.FastClusteringConfig(num_clusters=-1, threshold=schwelle),
        min_duration_on=0.3,
        min_duration_off=0.5,
    )
    sd = sherpa_onnx.OfflineSpeakerDiarization(config)
    start = time.time()
    ergebnis = sd.process(samples).sort_by_start_time()
    anteile: dict[int, float] = {}
    for r in ergebnis:
        anteile[r.speaker] = anteile.get(r.speaker, 0.0) + (r.end - r.start)
    sortiert = sorted(anteile.values(), reverse=True)
    gesamt = sum(sortiert) or 1.0
    return {
        "schwelle": schwelle,
        "sprecher": len(sortiert),
        # Wie viele Sprecher tragen mindestens 5 % der Sprechzeit? Das ist die belastbarere Zahl.
        "sprecher_ueber_5prozent": sum(1 for x in sortiert if x / gesamt >= 0.05),
        "top5_anteile": [round(x / gesamt * 100, 1) for x in sortiert[:5]],
        "sekunden": round(time.time() - start, 1),
    }


def main() -> int:
    namen = sys.argv[1:]
    if not namen:
        print(__doc__)
        return 2
    seg, emb = modelle_sicherstellen()
    WORK.mkdir(exist_ok=True)
    dateien = {Path(f).stem: Path(f).suffix for f in liste("audio")}

    for name in namen:
        endung = dateien.get(name)
        if not endung:
            log(f"nicht gefunden: {name}")
            continue
        quelle, wav = WORK / f"{name}{endung}", WORK / f"{name}.16k.wav"
        try:
            holen(f"audio/{name}{endung}", quelle)
            samples = nach_wav(quelle, wav)
            log(f"{name}: {zeit(len(samples) / 16000)} Audio")
            for schwelle in SCHWELLEN:
                r = laufen(samples, seg, emb, schwelle)
                log(f"  Schwelle {schwelle}: {r['sprecher']:3d} Sprecher "
                    f"({r['sprecher_ueber_5prozent']} über 5 % Redeanteil), "
                    f"Anteile {r['top5_anteile']}, {r['sekunden']}s")
        finally:
            quelle.unlink(missing_ok=True)
            wav.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
