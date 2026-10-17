"""Dauerhafter Neulauf mit Rauschfilter, kurzen Dateien zuerst und getrennten Textfassungen."""
import hashlib
import json
import math
import secrets
import sqlite3
import subprocess
import threading
import time
from pathlib import Path

from rauschfilter import VERSION, ffmpeg, filterkette
from wortzeiten import Wortzeiten, gueltige_woerter

class Uebernommen(Exception):
    pass


REZEPT = VERSION + ':whisper-large-v3:de:chunks240-v1'


class Neutranskription:
    def __init__(self, pfad):
        self.pfad = Path(pfad)
        self.wakeup = threading.Event()
        with self.db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS jobs (name TEXT PRIMARY KEY, basis TEXT, datei TEXT,
                dauer REAL, status TEXT, fortschritt INTEGER DEFAULT 0, daten TEXT, aktiv TEXT,
                geschuetzt INTEGER DEFAULT 0, fehler TEXT, versuch_ab REAL DEFAULT 0)''')
            db.execute('''CREATE TABLE IF NOT EXISTS teile (name TEXT, basis TEXT, start INTEGER,
                daten TEXT, PRIMARY KEY(name,basis,start))''')
            db.execute("UPDATE jobs SET status='wartet' WHERE status IN ('laeuft','kontingent')")
        self.thread = None

    def db(self):
        db = sqlite3.connect(self.pfad, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def basis(datei):
        return hashlib.sha256((Wortzeiten.basis(datei)+REZEPT).encode()).hexdigest()

    def einreihen(self, name, datei, dauer):
        basis = self.basis(datei)
        with self.db() as db:
            row = db.execute('SELECT basis FROM jobs WHERE name=?',(name,)).fetchone()
            if row and row['basis'] == basis:
                return
            db.execute('INSERT OR REPLACE INTO jobs(name,basis,datei,dauer,status) VALUES (?,?,?,?,?)',
                       (name,basis,str(datei),dauer or 0,'wartet'))
        self.wakeup.set()

    def starten(self):
        if self.thread is None:
            self.thread = threading.Thread(target=self.arbeiten,daemon=True,name='neu-transkribieren')
            self.thread.start()

    def statistik(self):
        with self.db() as db:
            zahlen = {r['status']:r['anzahl'] for r in db.execute('SELECT status,COUNT(*) anzahl FROM jobs GROUP BY status')}
            kurz = db.execute('SELECT COUNT(*) FROM jobs WHERE dauer<=600 AND status=?',('bereit',)).fetchone()[0]
        return {'gesamt':sum(zahlen.values()),'fertig':zahlen.get('bereit',0),'kurze_fertig':kurz,
                'lokal':zahlen.get('lokal',0),'laeuft':zahlen.get('laeuft',0)+zahlen.get('lokal',0),'wartet':zahlen.get('wartet',0),
                'kontingent':zahlen.get('kontingent',0),'fehler':zahlen.get('fehler',0)}

    def lokal_uebernehmen(self):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE jobs SET status='wartet' WHERE status='lokal' AND versuch_ab<?",(time.time(),))
            row=db.execute("SELECT * FROM jobs WHERE status IN ('wartet','kontingent') ORDER BY CASE status WHEN 'wartet' THEN 0 ELSE 1 END,dauer,name LIMIT 1").fetchone()
            if not row:
                return None
            token=secrets.token_urlsafe(24)
            # Der Lease-Token ist keine Anbieter-Zugangsdaten; er schützt die Arbeitsübernahme.
            db.execute("UPDATE jobs SET status='lokal',fehler=?,versuch_ab=? WHERE name=?",(token,time.time()+21600,row['name']))
            return {'name':row['name'],'basis':row['basis'],'token':token,'datei':Path(row['datei']).name,'dauer':row['dauer']}

    def lokal_ergebnis(self, data):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM jobs WHERE name=?',(data['name'],)).fetchone()
            if not row or row['status']!='lokal' or not secrets.compare_digest(row['fehler'] or '',data['token']) or row['basis']!=data['basis']:
                raise ValueError('Lokaler Auftrag wurde inzwischen geändert.')
            if data.get('aktion')=='fortschritt':
                db.execute('UPDATE jobs SET fortschritt=?,versuch_ab=? WHERE name=?',(max(0,min(99,int(data['fortschritt']))),time.time()+21600,data['name']))
                return
            result=data['ergebnis']
            dauer=float(result['duration'])
            if not math.isfinite(dauer) or dauer<=0 or abs(dauer-row['dauer'])>max(3,row['dauer']*.01):
                raise ValueError('Ergebnis passt nicht zur Audiodauer.')
            if self.basis(row['datei'])!=row['basis']:
                raise ValueError('Audiodatei wurde inzwischen geändert.')
            segments=result.get('segments') or []
            for s in segments:
                if not isinstance(s.get('text'),str) or not 0<=float(s['start'])<float(s['end'])<=dauer+.1:
                    raise ValueError('Ungültiges lokales Segment.')
            result={**result,'segments':segments,'words':gueltige_woerter(result.get('words') or [],dauer)}
            db.execute("UPDATE jobs SET status='bereit',fortschritt=100,daten=?,fehler=NULL WHERE name=?",(json.dumps(result,ensure_ascii=False),data['name']))
            db.execute('DELETE FROM teile WHERE name=?',(data['name'],))

    def lesen(self, name, datei):
        with self.db() as db:
            row = db.execute('SELECT * FROM jobs WHERE name=?',(name,)).fetchone()
        if not row or row['basis'] != self.basis(datei):
            return {'status':'fehlt'}
        return {'status':row['status'],'fortschritt':row['fortschritt'], 'error':row['fehler'] if row['status']!='lokal' else None,
                'geschuetzt':bool(row['geschuetzt']), 'daten':json.loads(row['daten']) if row['daten'] else None}

    def aktive_segmente(self, name, datei, original, korrekturen):
        """Aktivierte Fassung einfrieren, sobald darauf Textkorrekturen entstehen."""
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM jobs WHERE name=?',(name,)).fetchone()
            if not row or row['basis'] != self.basis(datei) or row['status'] != 'bereit':
                return original
            if row['aktiv']:
                return json.loads(row['aktiv'])
            segments = original if korrekturen else json.loads(row['daten'])['segments']
            db.execute('UPDATE jobs SET aktiv=?,geschuetzt=? WHERE name=?',
                       (json.dumps(segments,ensure_ascii=False),bool(korrekturen),name))
            return segments

    def zustand(self, name, status, fortschritt=None, fehler=None):
        with self.db() as db:
            changed=db.execute("UPDATE jobs SET status=?,fortschritt=COALESCE(?,fortschritt),fehler=? WHERE name=? AND status NOT IN ('lokal','bereit')",
                       (status,fortschritt,fehler,name))
            if not changed.rowcount:
                raise Uebernommen()

    def arbeiten(self):
        while True:
            with self.db() as db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute("SELECT * FROM jobs WHERE status='wartet' OR (status='fehler' AND versuch_ab<?) ORDER BY dauer,name LIMIT 1",(time.time(),)).fetchone()
                if row:
                    db.execute("UPDATE jobs SET status='laeuft' WHERE name=?",(row['name'],))
            if not row:
                self.wakeup.wait(15);self.wakeup.clear();continue
            try:
                self.verarbeiten(dict(row))
            except Uebernommen:
                continue
            except Exception as exc:
                try:
                    self.zustand(row['name'],'fehler',fehler=str(exc) if isinstance(exc,ValueError) else 'Neutranskription unterbrochen. Automatischer Wiederholungsversuch folgt.')
                except Uebernommen:
                    continue
                with self.db() as db:
                    db.execute('UPDATE jobs SET versuch_ab=? WHERE name=?',(time.time()+3600,row['name']))

    def verarbeiten(self, job):
        name,datei,basis = job['name'],job['datei'],job['basis']
        self.zustand(name,'laeuft',0)
        probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_entries','format=duration','-of','json',datei],timeout=30))
        dauer=float(probe['format']['duration'])
        if not math.isfinite(dauer) or dauer<=0:
            raise ValueError('Audiodauer ist ungültig.')
        words,segments=[],[]
        for start in range(0,math.ceil(dauer),240):
            ab,bis=max(0,start-1),min(dauer,start+241)
            with self.db() as db:
                old=db.execute('SELECT daten FROM teile WHERE name=? AND basis=? AND start=?',(name,basis,start)).fetchone()
            if old:
                data=json.loads(old[0])
            else:
                wav=subprocess.run([ffmpeg(),'-nostdin','-v','error','-ss',str(ab),'-i',datei,'-t',str(bis-ab),
                    '-af',filterkette(),'-ac','1','-ar','16000','-threads','1','-f','wav','pipe:1'],capture_output=True,check=True,timeout=300).stdout
                def warten(delay):
                    self.zustand(name,'kontingent',fehler=f'Spracherkennungskontingent erreicht. Automatischer Versuch in {math.ceil(delay/60)} Minuten.')
                data=Wortzeiten.transkribieren(wav,on_retry=warten)
                self.zustand(name,'laeuft')
                with self.db() as db:
                    db.execute('INSERT OR REPLACE INTO teile VALUES (?,?,?,?)',(name,basis,start,json.dumps(data)))
            for w in gueltige_woerter(data.get('words') or [],bis-ab):
                if start <= (w['start']+w['end'])/2+ab < min(start+240,dauer):
                    words.append({**w,'start':round(w['start']+ab,3),'end':round(w['end']+ab,3)})
            for s in data.get('segments') or []:
                if s.get('no_speech_prob',0)>.85 and s.get('avg_logprob',0)<-.6:
                    continue
                a,b=float(s['start'])+ab,float(s['end'])+ab
                if start <= (a+b)/2 < min(start+240,dauer) and s.get('text','').strip():
                    segments.append({'start':max(0,round(a,3)),'end':min(dauer,round(b,3)),'text':s['text'].strip()})
            self.zustand(name,'laeuft',min(99,round(100*bis/dauer)))
        if self.basis(datei)!=basis:
            raise ValueError('Audiodatei wurde inzwischen verändert.')
        result={'segments':segments,'words':gueltige_woerter(words,dauer),'duration':dauer,
                'rezept':REZEPT,'erstellt':time.strftime('%Y-%m-%dT%H:%M:%S')}
        with self.db() as db:
            changed=db.execute("UPDATE jobs SET status='bereit',fortschritt=100,daten=?,fehler=NULL WHERE name=? AND status='laeuft'",(json.dumps(result,ensure_ascii=False),name))
            if not changed.rowcount:
                raise Uebernommen()
            db.execute('DELETE FROM teile WHERE name=?',(name,))
