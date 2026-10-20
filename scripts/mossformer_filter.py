"""Bounded-memory, resumable MossFormer2 enhancement. Run in speech-lab venv."""
import argparse
import hashlib
import json
import math
import os
import subprocess
from pathlib import Path


def run(source, target):
    import numpy as np
    import soundfile as sf
    import torch
    from scipy.signal import resample_poly
    from clearvoice import ClearVoice
    torch.set_num_threads(4)
    checkpoint=Path(__file__).resolve().parents[1]/'checkpoints/MossFormer2_SE_48K/last_best_checkpoint.pt'
    if hashlib.sha256(checkpoint.read_bytes()).hexdigest()!='03692b9f773bbd6bb43b9c5a41f96b1e28affd66e13796b7bec66ad3d8b227c6':
        raise ValueError('MossFormer2-Modell stimmt nicht mit dem getesteten Filter überein.')
    target=Path(target);target.mkdir(parents=True,exist_ok=True)
    duration=float(subprocess.check_output(['ffprobe','-v','error','-show_entries',
                  'format=duration','-of','csv=p=0',source]))
    engine=None
    for start in range(0,math.ceil(duration),60):
        flac=target/f'{start:06}.flac'
        if flac.is_file():continue
        if engine is None:engine=ClearVoice(task='speech_enhancement',model_names=['MossFormer2_SE_48K'])
        ab=max(0,start-3);end=min(duration,start+63)
        wav=target/'input.wav'
        # Corrupt Opus packets are dropped on decode; fill them with silence to keep the time axis.
        subprocess.run(['ffmpeg','-nostdin','-v','error','-y','-ss',str(ab),'-i',source,
                        '-t',str(end-ab),'-ac','1','-ar','48000','-af','aresample=async=1:first_pts=0,volume=0.5',str(wav)],check=True)
        with torch.inference_mode():audio=np.asarray(engine(input_path=str(wav),online_write=False)).squeeze()
        expected=round((end-ab)*48000)
        if audio.ndim!=1 or not np.isfinite(audio).all() or abs(len(audio)-expected)>480:
            raise ValueError('MossFormer2 liefert keine zeitgetreue Ausgabe.')
        count=round(min(60,duration-start)*48000);offset=round((start-ab)*48000)
        # Original time axis is preserved, including silence and segment boundaries.
        audio=audio[offset:offset+count]
        audio=np.pad(audio,(0,max(0,count-len(audio))))[:count]
        if np.max(np.abs(audio),initial=0)>=1:raise ValueError('Übersteuertes Filterergebnis.')
        temp=flac.with_suffix('.partial.flac')
        sf.write(temp,resample_poly(audio,1,3),16000,subtype='PCM_24');temp.replace(flac)
        wav.unlink(missing_ok=True)
        print(json.dumps({'fortschritt':min(99,round(100*(start+60)/duration))}),flush=True)
        if torch.backends.mps.is_available():torch.mps.empty_cache()
    files=[target/f'{start:06}.flac' for start in range(0,math.ceil(duration),60)]
    # Streaming concat keeps memory bounded even for multi-hour recordings.
    with sf.SoundFile(target/'clean.partial.flac','w',samplerate=16000,channels=1,subtype='PCM_24') as out:
        for f in files:
            for block in sf.blocks(f,blocksize=16000*10,dtype='float32'):out.write(block)
    (target/'clean.partial.flac').replace(target/'clean.flac')
    probe=subprocess.run(['ffmpeg','-nostdin','-hide_banner','-i',str(target/'clean.flac'),
        '-af','loudnorm=I=-20:TP=-2:LRA=11:print_format=json','-f','null','-'],capture_output=True,text=True,check=True)
    measured=json.JSONDecoder().raw_decode(probe.stderr[probe.stderr.rfind('{'):])[0]
    level=float(measured['input_i']);peak=float(measured['input_tp'])
    gain=min(30,-20-level,-2-peak) if math.isfinite(level) and math.isfinite(peak) else 0
    # Use lossless continuous source to avoid MP3 padding at minute boundaries.
    subprocess.run(['ffmpeg','-nostdin','-v','error','-y','-i',str(target/'clean.flac'),
                    '-af',f'volume={gain}dB','-ar','24000','-ac','1','-c:a','libmp3lame',
                    '-b:a','64k','-write_xing','1',str(target/'clean.partial.mp3')],check=True)
    (target/'clean.partial.mp3').replace(target/'clean.mp3')
    actual=sf.info(target/'clean.flac').duration
    if abs(actual-duration)>.01:raise ValueError('Filter hat die Audiodauer verändert.')
    (target/'duration.json').write_text(json.dumps({'duration':actual,'gain_db':gain}))
    print(json.dumps({'fertig':True,'duration':actual}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('source');p.add_argument('target');a=p.parse_args()
    os.chdir(Path(__file__).resolve().parents[1]);run(a.source,a.target)
