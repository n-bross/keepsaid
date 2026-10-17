"""Benchmark für mlx-whisper: Laufzeit, Real-Time-Factor, Wortfehlerrate, Speicher.

Aufruf: .venv/bin/python bench.py [modell]
"""

import json
import re
import resource
import subprocess
import sys
import time
from pathlib import Path

import mlx.core as mx
import mlx_whisper

MODEL = sys.argv[1] if len(sys.argv) > 1 else "mlx-community/whisper-large-v3-turbo"
DATA = Path(__file__).parent / "testdata"

# (Datei, Sprache, Referenztext, Wiederholungen des Referenztexts)
CASES = [
    ("en.m4a", "en", "en_ref.txt", 1),
    ("de.m4a", "de", "de_ref.txt", 1),
    ("de.m4a", None, "de_ref.txt", 1),  # automatische Spracherkennung
    ("de_long.m4a", "de", "de_ref.txt", 5),
]


def words(text):
    return re.findall(r"\w+", text.lower())


def wer(ref, hyp):
    """Wortfehlerrate per Levenshtein auf Wortebene."""
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i]
        for j, h in enumerate(hyp, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h)))
        prev = cur
    return prev[-1] / len(ref)


def duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout)


def main():
    print(f"Modell: {MODEL}")
    # Warm-up: Modell laden, damit das nicht in die erste Messung fällt
    t = time.perf_counter()
    mlx_whisper.transcribe(str(DATA / "en.m4a"), path_or_hf_repo=MODEL, language="en")
    print(f"Warm-up (inkl. Modell laden): {time.perf_counter() - t:.1f}s\n")

    results = []
    for fname, lang, ref_file, reps in CASES:
        path = DATA / fname
        ref = words((DATA / ref_file).read_text()) * reps
        mx.reset_peak_memory()
        t = time.perf_counter()
        r = mlx_whisper.transcribe(str(path), path_or_hf_repo=MODEL, language=lang)
        elapsed = time.perf_counter() - t
        dur = duration(path)
        res = {
            "datei": fname,
            "sprache": lang or f"auto→{r['language']}",
            "audio_s": round(dur, 1),
            "laufzeit_s": round(elapsed, 1),
            "rtf": round(elapsed / dur, 3),
            "x_echtzeit": round(dur / elapsed, 1),
            "wer_prozent": round(100 * wer(ref, words(r["text"])), 1),
            "gpu_peak_gb": round(mx.get_peak_memory() / 1e9, 2),
            "segmente": len(r["segments"]),
        }
        results.append(res)
        print(json.dumps(res, ensure_ascii=False))
        (DATA / f"out_{Path(fname).stem}_{lang or 'auto'}.txt").write_text(r["text"].strip() + "\n")

    rss_gb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e9  # macOS: Bytes
    print(f"\nProzess max. RSS: {rss_gb:.2f} GB")
    (DATA / "results.json").write_text(json.dumps(
        {"modell": MODEL, "max_rss_gb": round(rss_gb, 2), "ergebnisse": results},
        ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
