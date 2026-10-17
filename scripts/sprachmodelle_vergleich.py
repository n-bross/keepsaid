"""Lokaler MossFormer2/Resemble-Test auf dem Anfang der Aufnahme aus Hörprobe 3.

tools/speech-lab/.venv/bin/python scripts/sprachmodelle_vergleich.py mossformer2
tools/speech-lab/.venv/bin/python scripts/sprachmodelle_vergleich.py resemble
tools/speech-lab/.venv/bin/python scripts/sprachmodelle_vergleich.py seite
"""
import base64
import importlib.metadata
import json
import os
import subprocess
import sys
from pathlib import Path
from hoervergleich_layout import compact

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'beispiele/filtervergleich'
SOURCE = OUT / 'anfang-original.wav'
MODELS = ['mossformer2', 'resemble', 'frcrn', 'mossformergan', 'voicefixer']
CLEARVOICE_MODELS = {'mossformer2': 'MossFormer2_SE_48K',
                     'frcrn': 'FRCRN_SE_16K', 'mossformergan': 'MossFormerGAN_SE_16K'}


def generate(model):
    import torch
    torch.set_num_threads(4)
    if model in CLEARVOICE_MODELS:
        from clearvoice import ClearVoice
        engine = ClearVoice(task='speech_enhancement', model_names=[CLEARVOICE_MODELS[model]])
        if model == 'mossformergan':
            # torch.angle für komplexe Spektren wird von Metal nicht unterstützt.
            for network in engine.models:
                network.device = torch.device('cpu')
                network.args.use_cuda = 0
                network.model.to('cpu')
        result = engine(input_path=str(SOURCE), online_write=False)
        import numpy as np
        import soundfile as sf
        # FRCRN kann sehr leise ausgeben: Float erhält die Details vor dem Pegelausgleich.
        audio = np.asarray(result).squeeze()
        if audio.ndim != 1 or not np.isfinite(audio).all():
            raise ValueError('Ungültige Mono-Ausgabe des Sprachmodells.')
        sf.write(OUT / f'anfang-{model}.wav', audio,
                 engine.models[0].args.sampling_rate, subtype='FLOAT')
    elif model == 'voicefixer':
        from voicefixer import VoiceFixer
        VoiceFixer().restore(input=str(SOURCE), output=str(OUT / 'anfang-voicefixer.wav'),
                             cuda=False, mode=0)
    else:
        import soundfile as sf
        sys.path.insert(0, str(ROOT / 'tools/speech-lab/resemble-enhance'))
        from resemble_enhance.enhancer.inference import enhance
        wav, sr = sf.read(SOURCE)
        result, sr = enhance(torch.from_numpy(wav).float(), sr, 'cpu',
                             nfe=32, solver='midpoint', lambd=0.5, tau=0.5)
        sf.write(OUT / 'anfang-resemble.wav', result.numpy(), sr, subtype='PCM_24')


def page(extended=False, restoration=False):
    variants = [('original', 'Original'), ('mossformer2', 'MossFormer2'), ('resemble', 'Resemble Enhance')]
    if extended:
        variants += [('frcrn', 'FRCRN'), ('mossformergan', 'MossFormerGAN')]
    if restoration:
        variants = [('original', 'Original'), ('mossformer2', 'MossFormer2 · bisheriger Favorit'),
                    ('voicefixer', 'VoiceFixer'), ('flowse', 'FlowSE · abschnittsweise')]
    matched = extended or restoration
    rows = []
    measurements = {}
    for key, title in variants:
        source = OUT / f'anfang-{key}.wav'
        if not source.is_file():
            raise FileNotFoundError(source)
        # Nur konstante Verstärkung pro Datei, keine zusätzliche Entrauschung.
        probe = subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-i', str(source), '-af',
                                'loudnorm=I=-20:TP=-2:LRA=11:print_format=json', '-f', 'null', '-'],
                               check=True, capture_output=True, text=True)
        measured, _ = json.JSONDecoder().raw_decode(probe.stderr[probe.stderr.rfind('{'):])
        measurements[key] = measured
    # Gleiche integrierte Lautheit, begrenzt durch die Spitzen des Originals.
    target_i = min([-20] + [float(m['input_i']) - 2 - float(m['input_tp'])
                            for m in measurements.values()]) if matched else -20
    prefix = 'restauration' if restoration else ('alle' if extended else 'neu')
    output_measurements = {}
    for key, title in variants:
        source = OUT / f'anfang-{key}.wav'
        measured = measurements[key]
        gain = min(target_i - float(measured['input_i']), -2 - float(measured['input_tp']))
        target = OUT / f'{prefix}-{key}.mp3'
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', str(source),
                        '-af', f'volume={gain:.4f}dB', '-ac', '1', '-ar', '48000', '-b:a', '128k', str(target)], check=True)
        if matched:
            # Bei sehr leisen Eingängen verändert das absolute LUFS-Gate die Messung.
            # Daher die tatsächliche Wiedergabedatei nachmessen und ggf. nachregeln.
            for _ in range(3):
                probe = subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-i', str(target), '-af',
                                        'loudnorm=I=-20:TP=-2:LRA=11:print_format=json', '-f', 'null', '-'],
                                       check=True, capture_output=True, text=True)
                actual, _ = json.JSONDecoder().raw_decode(probe.stderr[probe.stderr.rfind('{'):])
                delta = target_i - float(actual['input_i'])
                if abs(delta) < 0.2:
                    output_measurements[key] = actual
                    break
                gain = min(gain + delta, -2 - float(measured['input_tp']))
                subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', str(source),
                                '-af', f'volume={gain:.4f}dB', '-ac', '1', '-ar', '48000', '-b:a', '128k', str(target)], check=True)
        rows.append(f'<label class="variant"><span>{title}</span><audio controls preload="metadata" src="{target.name}"></audio></label>')
    template = (OUT / 'anfang.html').read_text()
    header = template.split('</style>')[0] + '</style>'
    script = template[template.index('<script>'):]
    document = header + '''<h1>Zwei andere Sprachmodelle</h1><p>Hörprobe 3 · 8. Oktober · 0:00–0:30. Direkt aus derselben Originalaufnahme verarbeitet. Die Wiedergabelautstärke wurde mit einer konstanten Verstärkung je Datei angenähert, soweit die Spitzen dies zulassen.</p><button id="reset" type="button">Alle auf Anfang</button><section><h2>Original gegen neue Modelle</h2><p>Beim Wechsel bleibt die Abspielposition erhalten.</p>''' + ''.join(rows) + '</section>' + script
    if matched:
        document = document.replace('Zwei andere Sprachmodelle', 'Sprachmodelle im Vergleich')
        document = document.replace('Die Wiedergabelautstärke wurde mit einer konstanten Verstärkung je Datei angenähert, soweit die Spitzen dies zulassen.', 'Die integrierte Lautstärke ist angeglichen. Bitte die Gerätelautstärke bei Bedarf erhöhen und zwischen den Varianten gleich lassen.')
    if extended:
        document = document.replace('</section>', '</section><section><h2>SAM Audio · Zugang fehlt</h2><p>Der Download der Modellgewichte wurde mit HTTP 401 abgewiesen. Meta verlangt eine Anmeldung und Freischaltung bei Hugging Face. <a href="https://huggingface.co/facebook/sam-audio-small" target="_blank" rel="noopener">Modellzugang beantragen</a>. Für SAM Audio liegt deshalb noch keine Hörprobe vor.</p></section>', 1)
    if restoration:
        document = document.replace('</section>', '<p>FlowSE wurde ohne Textvorgabe in kurzen, überlappenden Abschnitten berechnet und anschließend zusammengeblendet.</p></section>', 1)
    basename = 'voicefixer-flowse' if restoration else ('alle-modelle' if extended else 'neue-modelle')
    document = compact(document)
    (OUT / f'{basename}.html').write_text(document)
    for key, _ in variants:
        target = OUT / f'{prefix}-{key}.mp3'
        document = document.replace(f'src="{target.name}"', f'src="data:audio/mpeg;base64,{base64.b64encode(target.read_bytes()).decode()}"')
    (OUT / f'{basename}-portabel.html').write_text(document)
    metadata = {'source': str(SOURCE.relative_to(ROOT)), 'mossformer2': 'MossFormer2_SE_48K',
                'resemble': {'nfe': 32, 'solver': 'midpoint', 'lambd': 0.5, 'tau': 0.5, 'device': 'cpu'},
                'resemble_commit': subprocess.check_output(['git', '-C', str(ROOT / 'tools/speech-lab/resemble-enhance'), 'rev-parse', 'HEAD'], text=True).strip(),
                'packages': {p: importlib.metadata.version(p) for p in ['torch', 'torchaudio', 'clearvoice', 'deepspeed']}}
    metadata['clearvoice_models'] = {key: CLEARVOICE_MODELS[key] for key, _ in variants if key in CLEARVOICE_MODELS}
    metadata['target_lufs'] = target_i
    metadata['measurements'] = measurements
    metadata['output_measurements'] = output_measurements
    if restoration:
        metadata['voicefixer'] = {'version': importlib.metadata.version('voicefixer'), 'mode': 0, 'cuda': False}
        metadata['flowse'] = json.loads((OUT / 'flowse-einstellungen.json').read_text())
        metadata['flowse_commit'] = subprocess.check_output(['git', '-C', str(ROOT / 'tools/speech-lab/FlowSE'), 'rev-parse', 'HEAD'], text=True).strip()
    (OUT / f'{basename}-rezept.json').write_text(json.dumps(metadata, indent=2))
    print('Vergleich fertig:', OUT / f'{basename}.html', flush=True)


if __name__ == '__main__':
    os.chdir(ROOT)
    if sys.argv[1] == 'seite':
        page()
    elif sys.argv[1] == 'alle':
        page(extended=True)
    elif sys.argv[1] == 'restauration':
        page(restoration=True)
    elif sys.argv[1] in MODELS:
        generate(sys.argv[1])
    else:
        raise SystemExit('Modell: mossformer2, resemble, frcrn, mossformergan; Seite: seite oder alle')
