"""Durable Mac worker: enhance once, transcribe independently with Groq and MLX."""
import fcntl
import json
import math
import os
import re
import shutil
import subprocess
import time
import urllib.parse
from pathlib import Path

from keepsaid_config import env
from neu_mlx_worker import api, URL, log
from mosslaeufe import REZEPT
from asr_abschnitte import abschnitt
from titel import lade_env
from wortzeiten import Wortzeiten, gueltige_woerter

ROOT=Path(__file__).resolve().parent
WORK=ROOT/'work/moss'
MODEL='mlx-community/whisper-large-v3-mlx'
REMOTE=env('MOSS_REMOTE','/mnt/App-Data/soundcore/review-state/mossformer/').rstrip('/')+'/'
SSH_HOST=env('SSH_HOST','truenas')


def atomic(path,data):
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(data,ensure_ascii=False));temp.replace(path)


def update(job,status,**kwargs):
    return api('api/moss',{**job,'status':status,**kwargs})


def download(job,clean=False):
    folder=WORK/job['basis'];folder.mkdir(exist_ok=True)
    path=folder/('clean.flac' if clean else 'source'+Path(job['datei']).suffix)
    if not path.is_file():
        temp=path.with_suffix('.download')
        subprocess.run(['curl','-f','-sS','--retry','4','--max-time','1800','-o',str(temp),
            URL+'api/audio?'+urllib.parse.urlencode({'name':job['name'],'moss_asr':int(clean)})],check=True)
        temp.replace(path)
    return path


def enhance(job):
    source=download(job);folder=source.parent
    process=subprocess.Popen([str(ROOT/'tools/speech-lab/.venv/bin/python'),'-u',
        str(ROOT/'scripts/mossformer_filter.py'),str(source),str(folder)],cwd=ROOT,
        stdout=subprocess.PIPE,text=True)
    for line in process.stdout:
        try:d=json.loads(line)
        except ValueError:
            log(line.strip()[:180]);continue
        if 'fortschritt' in d:
            log('MossFormer2',job['name'],d['fortschritt'])
            update(job,'laeuft',fortschritt=d['fortschritt'])
    if process.wait():raise RuntimeError('MossFormer2 abgebrochen; Abschnitte bleiben gespeichert.')
    # Publish complete assets atomically; filenames are server-generated SHA256 values.
    for suffix in ('.flac','.mp3'):
        name=job['basis']+suffix
        subprocess.run(['scp',str(folder/('clean'+suffix)),SSH_HOST+':'+REMOTE+name+'.partial'],check=True)
        subprocess.run(['ssh',SSH_HOST,f'chmod 644 {REMOTE}{name}.partial && mv {REMOTE}{name}.partial {REMOTE}{name}'],check=True)
    update(job,'bereit')
    source.unlink(missing_ok=True)
    for p in folder.glob('[0-9]*.flac'):p.unlink()
    (folder/'clean.mp3').unlink(missing_ok=True)


def transcribe(job):
    audio=download(job,clean=True);folder=audio.parent
    checkpoint=folder/(job['anbieter']+'.json')
    duration=float(subprocess.check_output(['ffprobe','-v','error','-show_entries',
        'format=duration','-of','csv=p=0',str(audio)]))
    state=json.loads(checkpoint.read_text()) if checkpoint.exists() else {'next':0,'segments':[],'words':[]}
    for start in range(state['next'],math.ceil(duration),240):
        ab=max(0,start-2);end=min(duration,start+242);part=folder/'asr.wav'
        subprocess.run(['ffmpeg','-nostdin','-v','error','-y','-ss',str(ab),'-i',str(audio),
            '-t',str(end-ab),'-ac','1','-ar','16000',str(part)],check=True)
        if job['anbieter']=='groq':
            # One attempt: back off this provider without blocking MacBook jobs.
            result=Wortzeiten.transkribieren(part.read_bytes(),attempts=1)
        else:
            import mlx_whisper
            result=mlx_whisper.transcribe(str(part),path_or_hf_repo=MODEL,language='de',
                condition_on_previous_text=False,word_timestamps=True,temperature=0.0)
        accepted,words=abschnitt(result,ab,start,min(start+240,duration),duration)
        state['words'].extend(words)
        state['segments'].extend(accepted);state['next']=start+240
        atomic(checkpoint,state)
        update(job,'laeuft',fortschritt=min(99,round(100*end/duration)))
        log(job['anbieter'],job['name'],f'{min(start+240,duration):.0f}/{duration:.0f}s')
        part.unlink(missing_ok=True)
    result={'segments':state['segments'],'words':gueltige_woerter(state['words'],duration),
            'duration':duration,'rezept':REZEPT,'anbieter':job['anbieter'],
            'modell':MODEL if job['anbieter']=='macbook' else 'whisper-large-v3',
            'erstellt':time.strftime('%Y-%m-%dT%H:%M:%S')}
    update(job,'bereit',ergebnis=result)


def main():
    os.umask(0o077);lade_env();WORK.mkdir(parents=True,exist_ok=True)
    with open(WORK/'.lock','a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        while True:
            job=None
            try:
                jobs=api('api/moss',{'aktion':'holen'})['jobs']
                if not jobs:
                    stats=api('api/moss')
                    if all(s['gesamt'] and s['fertig']==s['gesamt'] for s in stats.values()):
                        log('MossFormer2, Groq und MacBook: alle Aufnahmen fertig.');return
                    time.sleep(30);continue
                job=jobs[0]
                if not re.fullmatch('[a-f0-9]{64}',job['basis']):raise ValueError('Ungültiger Auftragsschlüssel.')
                if shutil.disk_usage(WORK).free<180*1024**2:raise RuntimeError('Weniger als 180 MB frei; warte auf Speicherplatz.')
                with open(ROOT/'work/.worker.lock','a') as gpu:
                    fcntl.flock(gpu,fcntl.LOCK_EX)
                    update(job,'laeuft');log('Starte',job['anbieter'],job['name'])
                    if job['anbieter']=='filter':enhance(job)
                    else:transcribe(job)
                # Both transcripts are remote and durable; discard this recording's local cache.
                if job['anbieter'] in ('macbook','groq'):
                    states=api('api/moss-fassung?'+urllib.parse.urlencode({'name':job['name'],'anbieter':'groq' if job['anbieter']=='macbook' else 'macbook'}))
                    if states['status']=='bereit':shutil.rmtree(WORK/job['basis'],ignore_errors=True)
                log('Fertig',job['anbieter'],job['name'])
            except Exception as exc:
                log('Wiederholung folgt:',type(exc).__name__,str(exc)[:250])
                if job:
                    try:update(job,'fehler',error=str(exc)[:250],retry=3600 if job['anbieter']=='groq' else 120)
                    except Exception:pass
                time.sleep(10)


if __name__=='__main__':main()
