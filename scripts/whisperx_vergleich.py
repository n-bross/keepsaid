"""Compare current Groq transcription, WhisperX ASR, and alignment of identical text.

Run with tools/whisperx-test/.venv/bin/python; production packages are untouched.
Input is exactly the 30-second MossFormer2 sample used for the listening comparison.
"""
import gc
import hashlib
import importlib.metadata
import json
import os
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
WORK=ROOT/'work/whisperx-vergleich'
AUDIO=ROOT/'beispiele/filtervergleich/whisperx-vergleich.wav'


def save(path,data):
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(data,ensure_ascii=False,indent=2));temp.replace(path)


def main(current_only=False):
    import torch
    import whisperx
    torch.set_num_threads(4)
    audio=whisperx.load_audio(str(AUDIO))
    source=hashlib.sha256(AUDIO.read_bytes()).hexdigest()
    current=json.loads((WORK/'groq.json').read_text())
    if source!=current['source_sha256']:raise ValueError('Groq and WhisperX inputs differ.')
    native_file=WORK/'whisperx-asr.json'
    if not current_only and not native_file.exists():
        print('WhisperX: Whisper large-v3, CPU int8, Silero VAD, Deutsch',flush=True)
        started=time.monotonic()
        model=whisperx.load_model('large-v3','cpu',compute_type='int8',language='de',
            vad_method='silero',threads=4,download_root=str(ROOT/'tools/whisperx-test/models'),
            asr_options={'temperatures':[0.0],'condition_on_previous_text':False})
        loaded=time.monotonic()
        result=model.transcribe(audio,batch_size=1,language='de')
        save(native_file,{**result,'source_sha256':source,'asr_seconds':round(time.monotonic()-loaded,2),
                          'loading_seconds':round(loaded-started,2)})
        del model;gc.collect()
    native=json.loads(native_file.read_text()) if native_file.exists() else None
    if native:print('WhisperX-Text:', ' '.join(s['text'] for s in native['segments']),flush=True)
    print('Lade deutsches Alignment-Modell VOXPOPULI_ASR_BASE_10K_DE',flush=True)
    model,metadata=whisperx.load_align_model(language_code='de',device='cpu',
        model_dir=str(ROOT/'tools/whisperx-test/models'))
    variants=[('groq-aligned',current['segments'])]
    if native:variants.insert(0,('whisperx',native['segments']))
    if (WORK/'mlx.json').exists():
        local=json.loads((WORK/'mlx.json').read_text())
        if local['source_sha256']!=source:raise ValueError('MacBook input differs.')
        variants.append(('mlx-aligned',local['segments']))
    for name,segments in variants:
        path=WORK/(name+'.json')
        if path.exists():continue
        print('Richte Wörter aus:',name,flush=True);started=time.monotonic()
        result=whisperx.align(segments,model,metadata,audio,'cpu',interpolate_method='ignore',return_char_alignments=False)
        save(path,{**result,'source_sha256':source,'align_model':'VOXPOPULI_ASR_BASE_10K_DE',
                   'alignment_seconds':round(time.monotonic()-started,2)})
        print(name, len(result['word_segments']), 'Wörter',flush=True)
    save(WORK/'versions.json',{p:importlib.metadata.version(p) for p in
        ['whisperx','torch','torchaudio','faster-whisper','ctranslate2','transformers']})


if __name__=='__main__':
    os.environ.setdefault('HF_HUB_DISABLE_XET','1')
    main('--align-current-only' in sys.argv)
