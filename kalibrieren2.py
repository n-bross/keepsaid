"""Vergleicht Stimmabdruck-Modelle und Ähnlichkeitsschwellen für die Sprechertrennung.

Die erste Fassung (kalibrieren.py) hat nur die Schwelle variiert, mit dem kleinen Vorgabemodell.
Hier kommen die großen Modelle dazu — ein stärkerer Stimmabdruck trennt Personen zuverlässiger und
zerlegt dieselbe Stimme seltener in mehrere Sprecher.

    python kalibrieren2.py <name-ohne-endung> [weitere ...]

Ergebnis steht am Ende als Tabelle im Log und liegt zusätzlich auf dem Share unter
stimmprofile/kalibrierung.json, damit der Server es auswerten kann.
"""

import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from worker_mac import (MODELL_DIR, WORK, hochladen, holen, liste, log,  # noqa: E402
                        modelle_sicherstellen, nach_wav, zeit)

BASIS_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/")

# Bewusst nur auf Englisch/VoxCeleb trainierte Modelle — die chinesischen 3dspeaker-Varianten
# passen schlechter auf deutsche Stimmen.
# Das kleine campplus ist schon durchgemessen (bis zu 42 Sprecher bei einer Person) — hier nur die großen.
EMB_MODELLE = {
    "titanet_large": "nemo_en_titanet_large.onnx",
    "resnet293": "wespeaker_en_voxceleb_resnet293_LM.onnx",
}

SCHWELLEN = [0.5, 0.6, 0.7, 0.8]


def modell_holen(dateiname: str) -> Path:
    import urllib.request
    ziel = MODELL_DIR / dateiname
    if not ziel.exists():
        log(f"lade {dateiname}")
        urllib.request.urlretrieve(BASIS_URL + dateiname, ziel)
    return ziel


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
    anteile: dict[int, float] = {}
    for r in sd.process(samples).sort_by_start_time():
        anteile[r.speaker] = anteile.get(r.speaker, 0.0) + (r.end - r.start)
    werte = sorted(anteile.values(), reverse=True)
    gesamt = sum(werte) or 1.0
    return {
        "sprecher": len(werte),
        "sprecher_ueber_5prozent": sum(1 for x in werte if x / gesamt >= 0.05),
        "top5_anteile": [round(x / gesamt * 100, 1) for x in werte[:5]],
        "sekunden": round(time.time() - start, 1),
    }


def main() -> int:
    namen = sys.argv[1:]
    if not namen:
        print(__doc__)
        return 2

    seg, _ = modelle_sicherstellen()
    WORK.mkdir(exist_ok=True)
    dateien = {Path(f).stem: Path(f).suffix for f in liste("audio")}
    ergebnisse = []

    for name in namen:
        endung = dateien.get(name)
        if not endung:
            log(f"nicht gefunden: {name}")
            continue
        quelle, wav = WORK / f"{name}{endung}", WORK / f"{name}.16k.wav"
        try:
            holen(f"audio/{name}{endung}", quelle)
            samples = nach_wav(quelle, wav)
            log(f"=== {name}: {zeit(len(samples) / 16000)} Audio")
            for kurz, datei in EMB_MODELLE.items():
                emb = modell_holen(datei)
                for schwelle in SCHWELLEN:
                    r = laufen(samples, seg, emb, schwelle)
                    r.update(aufnahme=name, modell=kurz, schwelle=schwelle)
                    ergebnisse.append(r)
                    log(f"  {kurz:15s} Schwelle {schwelle}: {r['sprecher']:3d} Sprecher "
                        f"({r['sprecher_ueber_5prozent']} über 5 %), Anteile {r['top5_anteile']}, "
                        f"{r['sekunden']}s")
        finally:
            quelle.unlink(missing_ok=True)
            wav.unlink(missing_ok=True)

    pfad = WORK / "kalibrierung.json"
    pfad.write_text(json.dumps({"erstellt": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                "ergebnisse": ergebnisse}, ensure_ascii=False, indent=2), encoding="utf-8")
    hochladen(pfad, "stimmprofile")
    pfad.unlink(missing_ok=True)
    log("Tabelle liegt auf dem Share: stimmprofile/kalibrierung.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
