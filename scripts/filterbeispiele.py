"""Reproduzierbare Hörproben; verändert weder Originale noch Transkripte."""
import base64
import hashlib
import html
import json
import subprocess
import sys
from pathlib import Path
from hoervergleich_layout import compact

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rauschfilter import filterkette

OUT = ROOT / 'beispiele/filtervergleich'
DF = ROOT / 'tools/deepfilter/deep-filter'
MODEL = DF.parent / 'DeepFilterNet3_onnx.tar.gz'
SAMPLES = [
    ('01', '19. September · 0:40–1:05', '2026-09-19_215217_2026-09-19-215217.ogg', 40),
    ('02', '19. September · 2:40–3:05', '2026-09-19_215217_2026-09-19-215217.ogg', 160),
    ('03', '8. Oktober · 1:20–1:45', '2026-10-08_091950_2026-10-08-091950.ogg', 80),
]
VARIANTS = [('original', 'Original'), ('rnnoise', 'Bisheriger Filter'),
            ('deepfilter', 'DeepFilterNet3'), ('boost', 'DeepFilterNet3 + Stimme anheben')]
BOOST = 'dynaudnorm=f=250:g=15:p=0.9:m=4:r=0.15:t=0.008:b=1,alimiter=limit=0.95:level=false:latency=true'


def ff(source, target, *options):
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', str(source),
                    *options, str(target)], check=True)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = {'model_sha256': hashlib.sha256(MODEL.read_bytes()).hexdigest(),
                'boost': BOOST, 'rnnoise': filterkette(), 'samples': SAMPLES}
    cards = []
    for ident, title, filename, start in SAMPLES:
        print('Erstelle', title, flush=True)
        source = ROOT / 'work' / filename
        raw = OUT / f'{ident}-original.wav'
        ff(source, raw, '-ss', str(start), '-t', '25', '-ac', '1', '-ar', '48000')
        enhanced_dir = OUT / 'df'
        enhanced_dir.mkdir(exist_ok=True)
        subprocess.run([str(DF), '-D', '-m', str(MODEL), '-o', str(enhanced_dir), str(raw)], check=True)
        enhanced = enhanced_dir / raw.name
        rows = []
        for key, label in VARIANTS:
            path = OUT / f'{ident}-{key}.mp3'
            inputs = enhanced if key in ('deepfilter', 'boost') else raw
            filters = ['apad', 'atrim=duration=25']
            if key == 'rnnoise': filters.insert(0, filterkette())
            if key == 'boost': filters.append(BOOST)
            ff(inputs, path, '-af', ','.join(filters), '-ac', '1', '-ar', '48000', '-b:a', '64k')
            rows.append(f'<label class="variant"><span>{html.escape(label)}</span><audio controls preload="none" data-variant="{key}" src="{path.name}"></audio></label>')
        cards.append(f'<section><h2>Hörprobe {ident}</h2><p>{html.escape(title)} · 25 Sekunden</p>{"".join(rows)}</section>')
    page = '''<!doctype html><html lang="de"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Soundcore · Filtervergleich</title><style>
    body{margin:0;font-family:var(--font-sans,system-ui);color:var(--foreground,#292738)}
    h1{font-size:23px;margin:0 0 10px}h2{font-size:17px;margin:0}p{color:var(--muted-foreground,#77717e);font-size:14px;line-height:1.6;margin:6px 0 16px}
    section{border:1px solid var(--border,#ddd);border-radius:12px;padding:18px;margin:18px 0;background:var(--card,#faf9fc)}
    .variant{display:grid;grid-template-columns:210px minmax(0,1fr);align-items:center;gap:12px;padding:9px 0;font-size:13px}audio{width:100%;height:38px;min-width:0}
    button{font:inherit;border:1px solid var(--border,#ddd);border-radius:8px;padding:8px 12px;background:var(--secondary,#f2eff9);color:inherit;cursor:pointer}
    @media(max-width:540px){.variant{grid-template-columns:1fr;gap:5px}section{padding:16px}}
    </style><h1>Welcher Filter klingt für dich besser?</h1><p>Dieselben Stellen aus deinen Aufnahmen, jeweils in vier Varianten. Beim Wechsel startet die neue Variante an derselben Stelle. Die Lautstärke am Gerät bitte gleich lassen.</p><button id="reset" type="button">Alle Hörproben auf Anfang</button>'''
    page += ''.join(cards)
    page += '''<p>Die Version „Stimme anheben“ ist absichtlich lauter. Achte zusätzlich darauf, ob Wörter verständlicher werden und leise Stimmen natürlich bleiben. Originale und Transkripte wurden für diesen Vergleich nicht verändert.</p><script>
    let active=null;
    document.querySelectorAll('audio').forEach(a=>a.addEventListener('play',()=>{
      if(active&&active!==a&&active.closest('section')===a.closest('section')&&active.currentTime<24.8)a.currentTime=active.currentTime;
      document.querySelectorAll('audio').forEach(b=>{if(b!==a)b.pause()});active=a;
    }));
    document.querySelector('#reset').onclick=()=>{active=null;document.querySelectorAll('audio').forEach(a=>{a.pause();a.currentTime=0})};
    </script></html>'''
    page = compact(page)
    (OUT / 'index.html').write_text(page)
    portable = page
    for path in OUT.glob('*.mp3'):
        encoded = base64.b64encode(path.read_bytes()).decode()
        portable = portable.replace(f'src="{path.name}"', f'src="data:audio/mpeg;base64,{encoded}"')
    (OUT / 'hoervergleich.html').write_text(portable)
    (OUT / 'rezept.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    print('Fertig:', OUT / 'index.html', flush=True)


if __name__ == '__main__':
    main()
