"""Zusätzlicher lokaler Neulauf; übernimmt dauerhafte NAS-Aufträge, ohne Groq-Kontingent."""
import fcntl
import gzip
import json
import math
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from keepsaid_config import env, DEFAULT_SHARE
from rauschfilter import wav_bereinigen, VERSION
from wortzeiten import neue_segmente, gueltige_woerter

ROOT=Path(__file__).resolve().parent
WORK=ROOT/'work/neulauf'
URL=env('REVIEW_URL', env('SHARE_URL', DEFAULT_SHARE).rstrip('/')+'/review').rstrip('/')+'/'
MODEL='mlx-community/whisper-large-v3-mlx'
token=None


def log(*args):
    print(time.strftime('%Y-%m-%d %H:%M:%S'),*args,flush=True)


def api(path,data=None):
    global token
    for attempt in range(2):
        if data is not None and token is None:
            token=api('api/aufnahmen')['token']
        headers={'Accept-Encoding':'gzip'}
        if data is not None:headers.update({'Content-Type':'application/json','X-Review-Token':token})
        req=urllib.request.Request(URL+path,None if data is None else json.dumps(data,ensure_ascii=False).encode(),headers)
        try:
            with urllib.request.urlopen(req,timeout=180) as r:
                raw=r.read()
                if r.headers.get('Content-Encoding')=='gzip':raw=gzip.decompress(raw)
                return json.loads(raw)
        except urllib.error.HTTPError as e:
            if e.code!=403 or attempt:raise
            token=None


def process(job):
    import mlx_whisper
    audio=WORK/job['datei'];part=WORK/'chunk.wav';checkpoint=WORK/(job['basis']+'.json')
    if not audio.exists():
        log('Lade Aufnahme',job['name'],f"({job['dauer']:.1f} s)")
        temp=audio.with_suffix(audio.suffix+'.download')
        subprocess.run(['curl','-f','-sS','--retry','6','--retry-delay','3','--max-time','1800','-o',str(temp),URL+'api/audio?'+urllib.parse.urlencode({'name':job['name']})],check=True)
        temp.replace(audio)
    duration=float(subprocess.check_output(['ffprobe','-v','error','-show_entries','format=duration','-of','default=nw=1:nk=1',str(audio)]))
    state=json.loads(checkpoint.read_text()) if checkpoint.exists() else {'segments':[],'words':[],'next':0}
    for start in range(state['next'],math.ceil(duration),240):
        ab=max(0,start-1);bis=min(duration,start+241)
        wav_bereinigen(audio,part,ab,bis-ab)
        log('Lokales Whisper',job['name'],f'{start:.0f}/{duration:.0f} s')
        result=mlx_whisper.transcribe(str(part),path_or_hf_repo=MODEL,language='de',condition_on_previous_text=False,
                                     word_timestamps=True,temperature=0.0)
        segs=neue_segmente(result['segments'])
        for s in segs:
            words=gueltige_woerter(s.get('words') or [])
            for w in words:
                if start<=(w['start']+w['end'])/2+ab<min(start+240,duration):
                    state['words'].append({**w,'start':round(w['start']+ab,3),'end':round(w['end']+ab,3)})
            if start<=(s['start']+s['end'])/2+ab<min(start+240,duration) and max(0,s['start']+ab)<min(duration,s['end']+ab):
                state['segments'].append({'text':s['text'],'start':max(0,round(s['start']+ab,3)),
                                          'end':min(duration,round(s['end']+ab,3))})
        state['next']=start+240
        temp=checkpoint.with_suffix('.tmp');temp.write_text(json.dumps(state,ensure_ascii=False));temp.replace(checkpoint)
        api('api/neu-lokal',{**job,'aktion':'fortschritt','fortschritt':min(99,round(100*bis/duration))})
    result={'segments':state['segments'],'words':state['words'],'duration':duration,'rezept':VERSION,
            'modell':MODEL,'erstellt':time.strftime('%Y-%m-%dT%H:%M:%S')}
    api('api/neu-lokal',{**job,'aktion':'fertig','ergebnis':result})
    log('Fertig',job['name'],len(state['segments']),'Segmente')
    audio.unlink(missing_ok=True);part.unlink(missing_ok=True);checkpoint.unlink(missing_ok=True)


def main():
    os.umask(0o077);WORK.mkdir(parents=True,exist_ok=True)
    local_lock=open(WORK/'.lokal.lock','w')
    try:fcntl.flock(local_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:return
    current=WORK/'auftrag.json'
    while True:
        with open(ROOT/'work/.worker.lock','a') as lock:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:time.sleep(30);continue
            try:
                job=json.loads(current.read_text()) if current.exists() else api('api/neu-lokal',{'aktion':'holen'})['job']
                if not job:
                    stats=api('api/neutranskription')
                    if stats['gesamt'] and stats['fertig']==stats['gesamt']:
                        log('Alle Aufnahmen fertig.');return
                    fcntl.flock(lock,fcntl.LOCK_UN)
                    time.sleep(30);continue
                current.write_text(json.dumps(job))
                process(job)
                current.unlink(missing_ok=True)
            except urllib.error.HTTPError as e:
                if e.code in [400,409]:
                    log('Auftrag wurde geändert, suche nächsten.');current.unlink(missing_ok=True)
                else:log('Verbindung unterbrochen, neuer Versuch folgt.',e.code)
                time.sleep(30)
            except Exception as exc:
                log('Pausiert, setzt in einer Minute fort:',type(exc).__name__,str(exc)[:300])
                time.sleep(60)


if __name__=='__main__':main()
