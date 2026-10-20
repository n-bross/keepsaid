"""Transkribiert Aufnahmen auf dem Mac (Apple-Chip): Text + Sprechertrennung + Stimmabdrücke.

Der Server legt die Aufnahmen auf dem Share ab, dieses Skript holt sie sich dort, arbeitet sie ab und
lädt die Ergebnisse zurück. Es braucht keinen Hugging-Face-Zugang: Whisper läuft über mlx (Apple)
bzw. bei kurzen Sprachnachrichten über Groq Cloud, die Sprechertrennung über sherpa-onnx (frei zugängliche Modelle, kein Token).

    python worker_mac.py --stufe gruendlich --nur <name>     # eine Aufnahme, zum Testen
    python worker_mac.py --stufe gruendlich --loop           # Warteschlange abarbeiten

Ergebnis je Aufnahme auf dem Share:
    transkripte/<name>.<stufe>.json   Segmente mit Sprecher + Zeitstempel + Messwerte
    transkripte/<name>.<stufe>.md     lesbares Transkript, nach Sprecher gegliedert
    stimmprofile/<name>.stimmen.json  Stimmabdruck je Sprecher (für die spätere Namenszuordnung)

Die Sprecher heißen zunächst speaker1, speaker2, … — Namen kommen später über die Stimmabdrücke dazu.
"""

import argparse
import fcntl
import json
import os
import sqlite3
import tempfile
import subprocess
import sys
import tarfile
import time
import urllib.request
import wave
from pathlib import Path

import numpy as np

from stimmen import Stimmen
from titel import erstelle_titel

from keepsaid_config import env, DEFAULT_SHARE
from wortzeiten import neue_segmente
from rauschfilter import wav_bereinigen, VERSION as RAUSCHFILTER_VERSION

SHARE = env("SHARE_URL", DEFAULT_SHARE)

MODELLE = {
    "schnell": "mlx-community/whisper-large-v3-turbo",
    "gruendlich": "mlx-community/whisper-large-v3-mlx",
}

AUDIO_ENDUNGEN = {".ogg", ".opus", ".m4a", ".mp3", ".wav", ".flac", ".aac", ".mp4"}

BASIS = Path(__file__).resolve().parent
WORK = BASIS / "work"
MODELL_DIR = BASIS / "modelle"


def lade_env() -> None:
    env_pfad = BASIS / ".env"
    if env_pfad.exists():
        for zeile in env_pfad.read_text(encoding="utf-8").splitlines():
            zeile = zeile.strip()
            if zeile and not zeile.startswith("#") and "=" in zeile:
                k, v = zeile.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))


lade_env()

# Groq-Konfiguration für kurze Sprachnachrichten
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_MAX_AUDIO_SEKUNDEN = float(os.environ.get("GROQ_MAX_SEKUNDEN", "600"))
GROQ_MAX_DATEIGROESSE_MB = float(os.environ.get("GROQ_MAX_MB", "24"))
GROQ_MODELLE = {
    "schnell": "whisper-large-v3-turbo",
    "gruendlich": "whisper-large-v3",
}

# Sprachnachrichten bis zu dieser Länge haben praktisch immer nur einen Sprecher;
# die Sprechertrennung kostet dort mehr Zeit als Groq und bringt nichts.
OHNE_SPRECHER_BIS = float(env("OHNE_SPRECHER_BIS", "120"))


class GpuBelegt(Exception):
    """Die lokale Transkription muss warten, weil ein anderer Worker die GPU nutzt."""

SEGMENTIERUNG_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/"
                     "speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2")
EMB_BASIS_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/"
# Stimmabdruck-Modell und Ähnlichkeitsschwelle: beides per kalibrieren2.py ermittelt.
# Kleinere Schwelle = mehr Sprecher. Mit dem Vorgabewert 0.5 wurden aus einer Person bis zu 40 gemacht.
EMB_MODELL = env("EMB_MODELL", "wespeaker_en_voxceleb_resnet293_LM.onnx")
SCHWELLE = float(env("SCHWELLE", "0.7"))
EMBEDDING_URL = EMB_BASIS_URL + EMB_MODELL

# Stimmabdruck je Sprecher aus höchstens so vielen Sekunden bilden — mehr bringt kaum Genauigkeit.
PROFIL_SEKUNDEN = 40.0

# Zwei Cluster ab dieser Cosinus-Ähnlichkeit sind dieselbe Person. Gemessen an fertigen Aufnahmen:
# echte Dubletten liegen bei 0.87 bis 0.99, verschiedene Personen darunter bei 0.64 und weniger.
# Nötig, weil die Redezeit-Regel bei langen Aufnahmen nicht greift: 20 s sind dort nur 0,3 %.
GLEICHE_STIMME = 0.85


def log(*a) -> None:
    print(time.strftime("[%Y-%m-%d %H:%M:%S]"), *a, flush=True)


def stimmenbibliothek():
    """Server-Reviews sind maßgeblich; offline den letzten Snapshot verwenden."""
    pfad = BASIS / 'review/server-stimmen.sqlite3'
    try:
        url = env('REVIEW_URL', SHARE + '/review').rstrip('/')
        with urllib.request.urlopen(url + '/api/bibliothek', timeout=10) as r:
            daten = r.read(16 * 1024 * 1024 + 1)
        if len(daten) > 16 * 1024 * 1024 or not daten.startswith(b'SQLite format 3\0'):
            raise ValueError('Ungültiger Bibliothek-Snapshot.')
        pfad.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=pfad.parent, suffix='.sqlite3', delete=False) as f:
            temp = Path(f.name)
            f.write(daten)
        try:
            with sqlite3.connect(f'file:{temp}?mode=ro', uri=True) as db:
                if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('Bibliothek-Snapshot ist beschädigt.')
                db.execute('SELECT aufnahme, sprecher, modell, name, vektor FROM beispiele LIMIT 1')
            temp.replace(pfad)
        finally:
            temp.unlink(missing_ok=True)
    except Exception as e:
        log('Namensbibliothek: Server nicht erreichbar, nutze lokalen Stand:', e)
    return Stimmen(pfad) if pfad.exists() else Stimmen()


def zeit(sekunden: float) -> str:
    s = int(sekunden)
    return f"{s // 3600:d}:{s // 60 % 60:02d}:{s % 60:02d}"


# ── Share ──────────────────────────────────────────────────────────────────

def liste(ordner: str) -> list[str]:
    for versuch in range(1, 5):
        try:
            with urllib.request.urlopen(f"{SHARE}/{ordner}/?ls", timeout=60) as r:
                return [f["href"].lstrip("/") for f in json.load(r).get("files", [])]
        except Exception as exc:
            if versuch == 4:
                raise
            log(f"Warnung: Abruf {ordner}/?ls fehlgeschlagen ({exc}), Versuch {versuch}/3 in {5 * versuch}s...")
            time.sleep(5 * versuch)
    return []


def holen(pfad: str, ziel: Path) -> None:
    subprocess.run(["curl", "-sSf", "--retry", "3", "--retry-delay", "2", "--max-time", "900", "-o", str(ziel), f"{SHARE}/{pfad}"], check=True)


def hochladen(pfad: Path, ordner: str) -> None:
    """Vorhandene Fassung ersetzen — copyparty würde beim PUT sonst umbenennen."""
    ziel = f"{SHARE}/{ordner}/{pfad.name}"
    subprocess.run(["curl", "-sS", "--max-time", "60", "-X", "POST", f"{ziel}?delete", "-o", "/dev/null"],
                   check=False)
    subprocess.run(["curl", "-sSf", "--retry", "3", "--retry-delay", "2", "--max-time", "900", "-T", str(pfad), ziel, "-o", "/dev/null"], check=True)


# ── Audio ──────────────────────────────────────────────────────────────────

def nach_wav(quelle: Path, ziel: Path) -> np.ndarray:
    """In 16-kHz-Mono wandeln (das erwarten beide Modelle) und als float32 laden."""
    wav_bereinigen(quelle, ziel)
    with wave.open(str(ziel), "rb") as w:
        roh = w.readframes(w.getnframes())
    return np.frombuffer(roh, dtype=np.int16).astype(np.float32) / 32768.0


# ── Sprechertrennung ───────────────────────────────────────────────────────

def modelle_sicherstellen() -> tuple[Path, Path]:
    MODELL_DIR.mkdir(parents=True, exist_ok=True)
    seg = MODELL_DIR / "sherpa-onnx-pyannote-segmentation-3-0" / "model.onnx"
    emb = MODELL_DIR / Path(EMBEDDING_URL).name
    if not seg.exists():
        log("lade Segmentierungsmodell")
        archiv = MODELL_DIR / "segmentation.tar.bz2"
        urllib.request.urlretrieve(SEGMENTIERUNG_URL, archiv)
        with tarfile.open(archiv, "r:bz2") as t:
            t.extractall(MODELL_DIR)
        archiv.unlink()
    if not emb.exists():
        log("lade Stimmabdruck-Modell")
        urllib.request.urlretrieve(EMBEDDING_URL, emb)
    return seg, emb


def diarisieren(samples: np.ndarray, seg: Path, emb: Path) -> list[dict]:
    import sherpa_onnx
    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(seg))),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(emb)),
        # num_clusters=-1 heißt: Anzahl der Sprecher selbst bestimmen (über die Ähnlichkeitsschwelle)
        clustering=sherpa_onnx.FastClusteringConfig(num_clusters=-1, threshold=SCHWELLE),
        min_duration_on=0.3,
        min_duration_off=0.5,
    )
    sd = sherpa_onnx.OfflineSpeakerDiarization(config)
    if sd.sample_rate != 16000:
        raise RuntimeError(f"Modell erwartet {sd.sample_rate} Hz")
    return [{"start": r.start, "end": r.end, "speaker": f"speaker{r.speaker + 1}"}
            for r in sd.process(samples).sort_by_start_time()]


def stimmabdruecke(samples: np.ndarray, sprecher_segmente: list[dict], emb: Path) -> dict:
    """Je Sprecher einen gemittelten Stimmabdruck bilden — die Grundlage für die Namenszuordnung."""
    import sherpa_onnx
    extractor = sherpa_onnx.SpeakerEmbeddingExtractor(
        sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(emb)))

    nach_sprecher: dict[str, list[dict]] = {}
    for s in sprecher_segmente:
        nach_sprecher.setdefault(s["speaker"], []).append(s)

    profile = {}
    for name, segmente in nach_sprecher.items():
        gesamt = sum(s["end"] - s["start"] for s in segmente)
        vektoren, genutzt = [], 0.0
        for s in sorted(segmente, key=lambda x: x["end"] - x["start"], reverse=True):
            if genutzt >= PROFIL_SEKUNDEN:
                break
            stueck = samples[int(s["start"] * 16000):int(s["end"] * 16000)]
            if len(stueck) < 16000 * 0.5:          # unter einer halben Sekunde ist der Abdruck unbrauchbar
                continue
            stream = extractor.create_stream()
            stream.accept_waveform(sample_rate=16000, waveform=stueck)
            stream.input_finished()
            vektoren.append(np.array(extractor.compute(stream), dtype=np.float32))
            genutzt += s["end"] - s["start"]
        if not vektoren:
            continue
        mittel = np.mean(vektoren, axis=0)
        mittel /= np.linalg.norm(mittel) or 1.0
        profile[name] = {
            "vektor": [round(float(x), 6) for x in mittel],
            "sekunden_gesamt": round(gesamt, 1),
            "sekunden_genutzt": round(genutzt, 1),
            "segmente": len(segmente),
            "name": None,                            # wird später vom Server gesetzt
        }
    return profile


def verschmelze_aehnliche(profile: dict) -> dict:
    """Nur Cluster verschmelzen, deren Mitglieder alle zueinander ähnlich genug sind."""
    namen = [n for n in profile if profile[n].get("vektor")]
    eltern = {n: n for n in namen}

    def finde(x: str) -> str:
        while eltern[x] != x:
            eltern[x] = eltern[eltern[x]]
            x = eltern[x]
        return x

    for i, a in enumerate(namen):
        va = np.array(profile[a]["vektor"], dtype=np.float32)
        for b in namen[i + 1:]:
            vb = np.array(profile[b]["vektor"], dtype=np.float32)
            if float(va @ vb) >= GLEICHE_STIMME:
                gruppe_a = [n for n in namen if finde(n) == finde(a)]
                gruppe_b = [n for n in namen if finde(n) == finde(b)]
                # A≈B und B≈C reichen nicht aus, wenn A und C verschieden klingen.
                if all(float(np.dot(profile[x]["vektor"], profile[y]["vektor"])) >= GLEICHE_STIMME
                       for x in gruppe_a for y in gruppe_b):
                    eltern[finde(a)] = finde(b)
    return {n: finde(n) for n in namen}


def mittle(profile: dict, mitglieder: list[str]) -> dict:
    """Mehrere Stimmabdrücke zu einem zusammenfassen, gewichtet nach genutzter Sprechzeit."""
    vektoren = [np.array(profile[m]["vektor"], dtype=np.float32) for m in mitglieder]
    gewichte = [max(profile[m].get("sekunden_genutzt", 1.0), 0.1) for m in mitglieder]
    mittel = np.average(vektoren, axis=0, weights=gewichte)
    mittel /= np.linalg.norm(mittel) or 1.0
    return {
        "vektor": [round(float(x), 6) for x in mittel],
        "sekunden_gesamt": round(sum(profile[m].get("sekunden_gesamt", 0.0) for m in mitglieder), 1),
        "sekunden_genutzt": round(sum(profile[m].get("sekunden_genutzt", 0.0) for m in mitglieder), 1),
        "segmente": sum(profile[m].get("segmente", 0) for m in mitglieder),
        "name": None,
    }


def sprecher_aufraeumen(profile: dict, dauer_je_sprecher: dict) -> dict:
    """Ähnliche Stimmen verschmelzen, übrige Stimmen getrennt zur Prüfung behalten."""
    if not dauer_je_sprecher:
        return {}
    erste = verschmelze_aehnliche(profile)
    for n in dauer_je_sprecher:
        erste.setdefault(n, n)

    zwischen_profile, zwischen_dauer = {}, {}
    for alt, neu in erste.items():
        zwischen_dauer[neu] = zwischen_dauer.get(neu, 0.0) + dauer_je_sprecher.get(alt, 0.0)
    for neu in zwischen_dauer:
        mitglieder = [alt for alt, ziel in erste.items() if ziel == neu and profile.get(alt, {}).get("vektor")]
        if mitglieder:
            zwischen_profile[neu] = mittle(profile, mitglieder)

    zweite = zusammenfassen(zwischen_profile, zwischen_dauer)
    return {alt: zweite[neu] for alt, neu in erste.items()}


def zusammenfassen(profile: dict, dauer_je_sprecher: dict) -> dict:
    """Cluster nach Redezeit benennen; kurze oder unbekannte Stimmen nicht erzwingen."""
    if not dauer_je_sprecher:
        return {}
    zuordnung = {name: name for name in dauer_je_sprecher}

    # Nach Redezeit sortiert neu benennen: speaker1 ist immer der, der am meisten spricht.
    neue_dauer: dict[str, float] = {}
    for alt, neu in zuordnung.items():
        neue_dauer[neu] = neue_dauer.get(neu, 0.0) + dauer_je_sprecher[alt]
    reihenfolge = {alt_name: f"speaker{i}" for i, (alt_name, _) in
                   enumerate(sorted(neue_dauer.items(), key=lambda x: -x[1]), start=1)}
    return {alt: reihenfolge[neu] for alt, neu in zuordnung.items()}


def sprecher_zu_segment(segment: dict, sprecher_segmente: list[dict]) -> str | None:
    """Dem Textsegment den Sprecher mit der größten zeitlichen Überlappung zuordnen."""
    bester, beste_dauer = None, 0.0
    for s in sprecher_segmente:
        overlap = min(segment["end"], s["end"]) - max(segment["start"], s["start"])
        if overlap > beste_dauer:
            bester, beste_dauer = s["speaker"], overlap
    return bester


# ── Ablauf je Aufnahme ─────────────────────────────────────────────────────

def ein_sprecher(segmente: list[dict]) -> list[dict]:
    """Ganze Sprachnachricht als eine Stimme, damit sie im Review benennbar bleibt."""
    if not segmente:
        return []
    return [{"start": min(s["start"] for s in segmente),
             "end": max(s["end"] for s in segmente), "speaker": "speaker1"}]


def gpu_sperre(warten: bool):
    """Gemeinsame GPU-Sperre mit moss_worker.py und neu_mlx_worker.py."""
    fd = open(WORK / ".worker.lock", "a")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX if warten else fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fd.close()
        raise GpuBelegt("GPU wird gerade von einem anderen Worker genutzt")
    return fd


def verarbeite(name: str, endung: str, stufe: str, mit_sprechern: bool,
               groq_aktiv: bool = True, groq_max_sekunden: float | None = None,
               lokal: str = "warten") -> dict:
    """lokal: "warten" auf die GPU, "sofort" (sonst GpuBelegt) oder "nie" (immer GpuBelegt)."""
    import mlx_whisper

    WORK.mkdir(exist_ok=True)
    quelle = WORK / f"{name}{endung}"
    wav = WORK / f"{name}.16k.wav"
    erzeugt = [quelle, wav]
    try:
        holen(f"audio/{name}{endung}", quelle)
        samples = nach_wav(quelle, wav)
        audio_sekunden = len(samples) / 16000

        max_sek = groq_max_sekunden if groq_max_sekunden is not None else GROQ_MAX_AUDIO_SEKUNDEN
        dateigroesse_mb = (quelle.stat().st_size / (1024 * 1024)) if quelle.exists() else 0.0
        ist_kurz = audio_sekunden <= max_sek and dateigroesse_mb <= GROQ_MAX_DATEIGROESSE_MB
        nutze_groq = bool(groq_aktiv and GROQ_API_KEY and ist_kurz and (quelle.exists() or wav.exists()))

        segmente = []
        genutztes_modell = MODELLE[stufe]
        dauer_text = 0.0
        erfolg_groq = False

        if nutze_groq:
            groq_modell = GROQ_MODELLE.get(stufe, "whisper-large-v3-turbo")
            try:
                log(f"  transkribiere kurze Aufnahme ({zeit(audio_sekunden)}, {dateigroesse_mb:.1f} MB) via Groq ({groq_modell})...")
                start = time.time()
                import groq
                client = groq.Groq(api_key=GROQ_API_KEY)
                datei_pfad = wav
                with open(datei_pfad, "rb") as f:
                    res = client.audio.transcriptions.create(
                        file=f,
                        model=groq_modell,
                        response_format="verbose_json",
                        timestamp_granularities=["word", "segment"],
                        language="de",
                        temperature=0.0,
                    )
                dauer_text = time.time() - start
                groq_segmente = getattr(res, "segments", None) or []
                segmente = neue_segmente(groq_segmente, getattr(res, "words", None) or [])
                genutztes_modell = f"groq/{groq_modell}"
                erfolg_groq = True
                log(f"  Groq-Transkription fertig in {dauer_text:.1f}s ({len(segmente)} Segmente)")
            except Exception as exc:
                log(f"  Groq fehlgeschlagen ({exc}), wechsle zu lokalem mlx_whisper...")
                erfolg_groq = False

        if not erfolg_groq:
            if lokal == "nie":
                raise GpuBelegt("lokale Transkription für diesen Lauf ausgeschöpft")
            with gpu_sperre(lokal == "warten"):
                start = time.time()
                ergebnis = mlx_whisper.transcribe(str(wav), path_or_hf_repo=MODELLE[stufe],
                                                  language="de", condition_on_previous_text=False, word_timestamps=True)
                dauer_text = time.time() - start
            segmente = neue_segmente(ergebnis["segments"])
            genutztes_modell = MODELLE[stufe]

        sprecher_segmente, profile, dauer_sprecher, roh_anzahl = [], {}, 0.0, 0
        sprecher_getrennt = mit_sprechern and audio_sekunden > OHNE_SPRECHER_BIS
        if mit_sprechern:
            seg_modell, emb_modell = modelle_sicherstellen()
            start = time.time()
            # Der Stimmabdruck entsteht auch ohne Trennung, damit Namensvorschläge weiter greifen.
            sprecher_segmente = (diarisieren(samples, seg_modell, emb_modell) if sprecher_getrennt
                                 else ein_sprecher(segmente))
            roh_profile = stimmabdruecke(samples, sprecher_segmente, emb_modell)

            dauer_je_sprecher: dict[str, float] = {}
            for sp in sprecher_segmente:
                dauer_je_sprecher[sp["speaker"]] = (dauer_je_sprecher.get(sp["speaker"], 0.0)
                                                    + sp["end"] - sp["start"])
            if dauer_je_sprecher:
                zuordnung = sprecher_aufraeumen(roh_profile, dauer_je_sprecher)
                roh_anzahl = len(dauer_je_sprecher)
                for sp in sprecher_segmente:
                    sp["speaker"] = zuordnung[sp["speaker"]]

                # Stimmabdrücke der zusammengefassten Sprecher neu bilden, gewichtet nach Redezeit
                profile = {}
                for alt, neu in zuordnung.items():
                    q = roh_profile.get(alt)
                    if not q:
                        continue
                    e = profile.setdefault(neu, {"vektoren": [], "gewichte": [], "sekunden_gesamt": 0.0,
                                                 "segmente": 0, "name": None})
                    e["vektoren"].append(np.array(q["vektor"], dtype=np.float32))
                    e["gewichte"].append(q["sekunden_genutzt"])
                    e["sekunden_gesamt"] += q["sekunden_gesamt"]
                    e["segmente"] += q["segmente"]
                for e in profile.values():
                    mittel = np.average(e.pop("vektoren"), axis=0, weights=e.pop("gewichte"))
                    mittel /= np.linalg.norm(mittel) or 1.0
                    e["vektor"] = [round(float(x), 6) for x in mittel]
                    e["sekunden_gesamt"] = round(e["sekunden_gesamt"], 1)

                # Auch ohne verwertbaren Stimmabdruck muss eine Stimme auf der
                # Review-Seite sichtbar und manuell benennbar bleiben.
                for sp in sprecher_segmente:
                    e = profile.setdefault(sp["speaker"], {"vektor": None, "name": None,
                                                          "sekunden_gesamt": 0.0, "segmente": 0})
                    if not e.get("vektor"):
                        e["sekunden_gesamt"] += sp["end"] - sp["start"]
                        e["segmente"] += 1
                for s in segmente:
                    s["speaker"] = sprecher_zu_segment(s, sprecher_segmente)
            dauer_sprecher = time.time() - start

        # Bestätigte Zuordnungen bleiben fest; automatische Treffer sind ausschließlich
        # Vorschläge und gelangen niemals von selbst in die Referenzbibliothek.
        namen = {}
        if profile:
            bibliothek = stimmenbibliothek()
            bestaetigt = bibliothek.reviews(name, EMB_MODELL)
            for sprecher, profil in profile.items():
                vorschlag = (bibliothek.vorschlag(profil["vektor"], EMB_MODELL, (name, sprecher))
                             if profil.get("vektor") else None)
                profil["namensvorschlag"] = vorschlag
                profil["name_bestaetigt"] = sprecher in bestaetigt
                profil["name"] = bestaetigt.get(sprecher)
                if not profil["name"] and vorschlag and vorschlag["eindeutig"]:
                    profil["name"] = vorschlag["name"]
                profil["pruefen"] = not bool(profil["name"])
                if profil["name"]:
                    namen[sprecher] = profil["name"]
            offen = [sp for sp, p in profile.items() if p["pruefen"]]
            if offen:
                log(f"  Sprecher zur manuellen Prüfung: {', '.join(offen)} — {SHARE}/review/")
            for segment in segmente:
                segment["speaker_name"] = namen.get(segment.get("speaker"))

        redeanteil = {}
        for s in sprecher_segmente:
            redeanteil[s["speaker"]] = round(redeanteil.get(s["speaker"], 0.0) + s["end"] - s["start"], 1)

        titel = None
        try:
            titel = erstelle_titel(name, " ".join(s["text"] for s in segmente))
        except Exception as exc:
            log(f"  Titel konnte nicht erzeugt werden: {exc}")

        summary = {
            "name": name,
            "titel": titel,
            "stufe": stufe,
            "modell": genutztes_modell,
            "sprecher_modell": EMB_MODELL,
            "rauschfilter": RAUSCHFILTER_VERSION,
            "sprecher_schwelle": SCHWELLE,
            "worker": os.uname().nodename,
            "audio_sekunden": round(audio_sekunden, 1),
            "rechen_sekunden_text": round(dauer_text, 1),
            "rechen_sekunden_sprecher": round(dauer_sprecher, 1),
            "faktor": round((dauer_text + dauer_sprecher) / audio_sekunden, 3) if audio_sekunden else None,
            "segmente": len(segmente),
            "woerter": sum(len(s["text"].split()) for s in segmente),
            "sprecher_anzahl": len(redeanteil),
            "sprecher_vor_zusammenfassung": roh_anzahl if mit_sprechern else None,
            "sprecher_getrennt": sprecher_getrennt,
            "sprecher_namen": namen,
            "sprecher_pruefen": [sp for sp, p in profile.items() if p.get("pruefen")],
            "redeanteil_sekunden": dict(sorted(redeanteil.items(), key=lambda x: -x[1])),
            "erstellt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }

        json_pfad = WORK / f"{name}.{stufe}.json"
        md_pfad = WORK / f"{name}.{stufe}.md"
        erzeugt += [json_pfad, md_pfad]
        json_pfad.write_text(json.dumps({**summary, "segments": segmente,
                                         "sprecher_segmente": sprecher_segmente},
                                        ensure_ascii=False, indent=2), encoding="utf-8")

        kopf = [f"# {name}", "",
                f"_Stufe: {stufe} · Modell: {genutztes_modell} · {summary['erstellt']}_"]
        if mit_sprechern:
            if summary["sprecher_anzahl"] > 0:
                anteile = ", ".join(f"{k}: {zeit(v)}" for k, v in summary["redeanteil_sekunden"].items())
                kopf.append(f"_{summary['sprecher_anzahl']} Sprecher erkannt — {anteile}_")
            else:
                kopf.append("_0 Sprecher erkannt (keine Sprache in der Aufnahme)_")
        kopf.append("")
        zeilen, letzter = [], None
        for s in segmente:
            sp = s.get("speaker")
            if mit_sprechern and sp != letzter:
                zeilen.append(f"## {namen.get(sp, sp) or 'unbekannt'}")
                letzter = sp
            zeilen.append(f"**[{zeit(s['start'])}]** {s['text']}")
        md_pfad.write_text("\n\n".join(kopf + zeilen) + "\n", encoding="utf-8")

        hochladen(json_pfad, "transkripte")
        hochladen(md_pfad, "transkripte")

        if profile:
            profil_pfad = WORK / f"{name}.stimmen.json"
            erzeugt.append(profil_pfad)
            profil_pfad.write_text(json.dumps(
                {"name": name, "modell": Path(EMBEDDING_URL).name, "erstellt": summary["erstellt"],
                 "sprecher": profile}, ensure_ascii=False, indent=2), encoding="utf-8")
            hochladen(profil_pfad, "stimmprofile")

        return summary
    finally:
        for p in erzeugt:
            p.unlink(missing_ok=True)


def offene(stufe: str) -> list[tuple[str, str]]:
    audio = [f for f in liste("audio") if Path(f).suffix.lower() in AUDIO_ENDUNGEN]
    fertig = {f for f in liste("transkripte")}
    offen = [(Path(f).stem, Path(f).suffix) for f in audio
             if f"{Path(f).stem}.{stufe}.json" not in fertig]
    return sorted(offen, reverse=True)      # Dateiname beginnt mit dem Datum → neueste zuerst


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    # Nur gegen einen zweiten worker_mac.py; die GPU-Sperre gilt allein für mlx_whisper,
    # damit kurze Aufnahmen über Groq nicht hinter dem MossFormer-Neulauf warten.
    lock_datei = WORK / ".mac_worker.lock"
    lock_fd = open(lock_datei, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        log("Ein anderer Worker-Prozess läuft bereits. Beende diesen Lauf.")
        return 0

    ap = argparse.ArgumentParser()
    ap.add_argument("--stufe", choices=MODELLE, default="gruendlich")
    ap.add_argument("--limit", type=int, default=0,
                    help="höchstens so viele lokale Transkriptionen je Lauf, 0 = alles (Groq zählt nicht)")
    ap.add_argument("--loop", action="store_true", help="danach auf neue Aufnahmen warten")
    ap.add_argument("--nur", help="genau diese Aufnahme, auch wenn sie schon fertig ist")
    ap.add_argument("--ohne-sprecher", action="store_true", help="nur Text, keine Sprechertrennung")
    ap.add_argument("--ohne-groq", action="store_true", help="Groq deaktivieren, immer lokal transkribieren")
    ap.add_argument("--groq-max-sekunden", type=float, default=GROQ_MAX_AUDIO_SEKUNDEN,
                    help=f"Maximale Audiolänge für Groq in Sekunden (Default: {int(GROQ_MAX_AUDIO_SEKUNDEN)}s)")
    args = ap.parse_args()

    fertig = lokal_fertig = 0
    while True:
        try:
            if args.nur:
                treffer = [(Path(f).stem, Path(f).suffix) for f in liste("audio") if Path(f).stem == args.nur
                           and Path(f).suffix.lower() in AUDIO_ENDUNGEN]
                if not treffer:
                    log(f"nicht gefunden: {args.nur}")
                    return 1
                liste_offen = treffer
            else:
                liste_offen = offene(args.stufe)
        except Exception as exc:
            log(f"Fehler beim Ermitteln der offenen Aufnahmen: {exc}")
            if not args.loop:
                return 1
            time.sleep(30)
            continue

        if not liste_offen:
            if not args.loop:
                log("nichts offen")
                return 0
            time.sleep(300)
            continue

        log(f"{len(liste_offen)} offen")
        # Erst alles über Groq, damit kurze Aufnahmen nie hinter einer stundenlangen
        # lokalen Transkription warten; lange Aufnahmen danach mit GPU.
        zurueckgestellt = []
        for durchgang, aufnahmen in (("nie", liste_offen), ("warten", zurueckgestellt)):
            for name, endung in aufnahmen:
                limit_erreicht = bool(args.limit and lokal_fertig >= args.limit)
                log(f"→ {name}")
                try:
                    s = verarbeite(name, endung, args.stufe, not args.ohne_sprecher,
                                   groq_aktiv=not args.ohne_groq, groq_max_sekunden=args.groq_max_sekunden,
                                   lokal="nie" if limit_erreicht else durchgang)
                except GpuBelegt as exc:
                    if durchgang == "nie" and not limit_erreicht:
                        log("  zurückgestellt: braucht lokale Transkription")
                        zurueckgestellt.append((name, endung))
                    else:
                        log(f"  übersprungen, Limit {args.limit} lokaler Transkriptionen erreicht")
                    continue
                except Exception as exc:
                    log(f"  FEHLER {name}: {exc}")
                    continue
                fertig += 1
                if not s["modell"].startswith("groq/"):
                    lokal_fertig += 1
                log(f"  fertig: {zeit(s['audio_sekunden'])} Audio in {zeit(s['rechen_sekunden_text'] + s['rechen_sekunden_sprecher'])}"
                    f" (Faktor {s['faktor']}), {s['sprecher_anzahl']} Sprecher, {s['woerter']} Wörter")
        if args.nur or not args.loop:
            log(f"Ende, {fertig} Aufnahme(n)")
            return 0


if __name__ == "__main__":
    sys.exit(main())
