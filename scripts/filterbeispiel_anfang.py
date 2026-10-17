"""Anfang der Aufnahme aus Hörprobe 3, einschließlich milder DF3-Variante."""
import base64
import json
import subprocess
from filterbeispiele import ROOT, OUT, DF, MODEL, BOOST, ff, filterkette
from hoervergleich_layout import compact


def main():
    source = ROOT / 'work/2026-10-08_091950_2026-10-08-091950.ogg'
    raw = OUT / 'anfang-original.wav'
    # Gemeinsamer Headroom, damit DF3 keine zusätzlichen Spitzen abschneidet.
    ff(source, raw, '-t', '30', '-af', 'volume=0.5', '-ac', '1', '-ar', '48000')
    for label, extra in [('voll', []), ('mild', ['--atten-lim-db', '6'])]:
        folder = OUT / ('anfang-' + label)
        folder.mkdir(exist_ok=True)
        subprocess.run([str(DF), '-D', '-m', str(MODEL), *extra, '-o', str(folder), str(raw)], check=True)
    variants = [
        ('original', 'Original', raw, None),
        ('rnnoise', 'Bisheriger Filter · RNNoise', raw, filterkette()),
        ('deepfilter', 'DeepFilterNet3 · bisherige Stärke', OUT / 'anfang-voll' / raw.name, None),
        ('mild', 'DeepFilterNet3 · mild (max. 6 dB)', OUT / 'anfang-mild' / raw.name, None),
        ('mild-boost', 'Mild + Stimme anheben', OUT / 'anfang-mild' / raw.name, BOOST),
    ]
    rows = []
    for key, title, audio, filters in variants:
        path = OUT / f'anfang-{key}.mp3'
        chain = ['apad', 'atrim=duration=30']
        if filters:
            chain.append(filters)
        ff(audio, path, '-af', ','.join(chain), '-ac', '1', '-ar', '48000', '-b:a', '64k')
        rows.append(f'<label class="variant"><span>{title}</span><audio controls preload="metadata" src="{path.name}"></audio></label>')
    existing = (OUT / 'index.html').read_text()
    head = existing.split('</style>')[0] + '</style>'
    script = existing[existing.index('<script>'):].replace('24.8', '29.8')
    page = head + '''<h1>Hörprobe 3 · der Anfang</h1><p>8. Oktober · 0:00–0:30. Bei „mild“ ist die Geräuschabsenkung auf 6 dB begrenzt; dadurch bleibt mehr Originalton erhalten. Die letzte Variante hebt zusätzlich leise Passagen an.</p><button id="reset" type="button">Alle auf Anfang</button><section><h2>Dieselben 30 Sekunden</h2><p>Beim Wechsel bleibt die Abspielposition erhalten. Alle Varianten erhalten vor der Verarbeitung denselben um 6 dB abgesenkten Eingangspegel, um Übersteuerung zu vermeiden.</p>''' + ''.join(rows) + '</section>' + script
    page = compact(page)
    (OUT / 'anfang.html').write_text(page)
    portable = page
    for key, *_ in variants:
        path = OUT / f'anfang-{key}.mp3'
        portable = portable.replace(f'src="{path.name}"', f'src="data:audio/mpeg;base64,{base64.b64encode(path.read_bytes()).decode()}"')
    (OUT / 'anfang-portabel.html').write_text(portable)
    (OUT / 'anfang-rezept.json').write_text(json.dumps({'quelle': source.name, 'start': 0, 'dauer': 30, 'eingangspegel_faktor': 0.5, 'mild_atten_lim_db': 6, 'boost': BOOST}, indent=2))
    print('Fertig:', OUT / 'anfang.html')


if __name__ == '__main__':
    main()
