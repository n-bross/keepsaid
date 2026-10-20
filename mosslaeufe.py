"""Separate, durable MossFormer2/Groq/MacBook runs; never modify source transcripts."""
import hashlib
import json
import math
import sqlite3
import subprocess
import time
from pathlib import Path

from wortzeiten import Wortzeiten, gueltige_woerter

REZEPT = 'mossformer2-48k-clearvoice012-context3-core60-v1'
ANBIETER = ('filter', 'groq', 'macbook')


class MossLaeufe:
    def __init__(self, pfad):
        self.pfad = Path(pfad)
        self.audio = self.pfad.parent / 'mossformer'
        self.audio.mkdir(exist_ok=True)
        with self.db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS runs (
                name TEXT, basis TEXT, anbieter TEXT, dauer REAL, status TEXT DEFAULT 'wartet',
                fortschritt INTEGER DEFAULT 0, daten TEXT, fehler TEXT, retry REAL DEFAULT 0,
                PRIMARY KEY(name,basis,anbieter))''')
            db.execute('CREATE TABLE IF NOT EXISTS aktiv (name TEXT PRIMARY KEY, basis TEXT, daten TEXT)')

    def db(self):
        db = sqlite3.connect(self.pfad, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def basis(datei):
        return hashlib.sha256((Wortzeiten.basis(datei) + REZEPT).encode()).hexdigest()

    @staticmethod
    def dateidauer(datei):
        try:
            dauer=float(subprocess.check_output(['ffprobe','-v','error','-show_entries','format=duration',
                '-of','csv=p=0',str(datei)],timeout=30,stderr=subprocess.DEVNULL))
        except (OSError,ValueError,subprocess.SubprocessError):
            return None
        return dauer if math.isfinite(dauer) and dauer>0 else None

    def einreihen(self, name, datei, dauer):
        basis = self.basis(datei)
        with self.db() as db:
            for anbieter in ANBIETER:
                db.execute('INSERT OR IGNORE INTO runs(name,basis,anbieter,dauer) VALUES (?,?,?,?)',
                           (name, basis, anbieter, dauer))

    def jobs(self, aufnahmen, pfad):
        result = []
        for r in aufnahmen:
            datei = pfad(r['name'])
            self.einreihen(r['name'], datei, r.get('dauer') or 0)
            basis = self.basis(datei)
            with self.db() as db:
                rows = db.execute('SELECT * FROM runs WHERE name=? AND basis=?', (r['name'], basis)).fetchall()
            for row in rows:
                if row['anbieter']!='filter' and not any(r['anbieter']=='filter' and r['status']=='bereit' for r in rows):
                    continue
                if row['status'] != 'bereit' and row['retry'] <= time.time():
                    result.append({'name':r['name'], 'basis':basis, 'datei':datei.name,
                                   'dauer':row['dauer'], 'anbieter':row['anbieter']})
        # Finish each short recording, while cloud backoff never prevents local processing.
        return sorted(result, key=lambda j:(j['dauer'], j['name'], ANBIETER.index(j['anbieter'])))

    def lesen(self, name, datei):
        with self.db() as db:
            rows = db.execute('SELECT * FROM runs WHERE name=? AND basis=?', (name,self.basis(datei))).fetchall()
        return {r['anbieter']:{'status':r['status'], 'fortschritt':r['fortschritt'],
                'error':r['fehler'], 'daten':json.loads(r['daten']) if r['daten'] else None} for r in rows}

    def aktive_fassung(self, name, datei, korrekturen, runs):
        basis=self.basis(datei)
        with self.db() as db:
            row=db.execute('SELECT * FROM aktiv WHERE name=? AND basis=?',(name,basis)).fetchone()
            if korrekturen:
                return json.loads(row['daten']) if row else None
            result=next((runs[p]['daten'] for p in ('groq','macbook')
                         if runs.get(p,{}).get('status')=='bereit'),None)
            if result:
                encoded=json.dumps(result,ensure_ascii=False)
                if not row or row['daten']!=encoded:
                    db.execute('INSERT OR REPLACE INTO aktiv VALUES (?,?,?)',(name,basis,encoded))
            return result

    def statistik(self):
        with self.db() as db:
            # Superseded source versions remain archived, but are not counted twice.
            rows = db.execute('SELECT anbieter,status,COUNT(*) n FROM runs WHERE rowid IN '
                '(SELECT MAX(rowid) FROM runs GROUP BY name,anbieter) GROUP BY anbieter,status').fetchall()
        out = {p:{'gesamt':0,'fertig':0,'laeuft':0,'fehler':0} for p in ANBIETER}
        for r in rows:
            d=out[r['anbieter']];d['gesamt']+=r['n']
            if r['status']=='bereit':d['fertig']+=r['n']
            elif r['status']=='laeuft':d['laeuft']+=r['n']
            elif r['status']=='fehler':d['fehler']+=r['n']
        return out

    def speichern(self, data, datei):
        name,basis,anbieter=data['name'],data['basis'],data['anbieter']
        if anbieter not in ANBIETER or basis != self.basis(datei):
            raise ValueError('Auftrag passt nicht mehr zur Aufnahme.')
        state=data['status']
        if state not in ('bereit','laeuft','fehler'):
            raise ValueError('Ungültiger Auftragsstatus.')
        result=data.get('ergebnis')
        if state=='bereit':
            if anbieter=='filter':
                for suffix in ('.flac','.mp3'):
                    if not (self.audio/(basis+suffix)).is_file():
                        raise ValueError('Bereinigte Audiodatei fehlt.')
            else:
                if not isinstance(result,dict) or result.get('rezept') != REZEPT:
                    raise ValueError('Falsches Filterrezept.')
                with self.db() as db:
                    row=db.execute('SELECT dauer FROM runs WHERE name=? AND basis=? AND anbieter=?',
                                   (name,basis,anbieter)).fetchone()
                dauer=float(result['duration'])
                # Soundcore metadata can overstate the length; the stored audio file is authoritative.
                soll=(self.dateidauer(datei) or row['dauer']) if row else 0
                if not row or not math.isfinite(dauer) or dauer<=0 or abs(dauer-soll)>max(2,soll*.005):
                    raise ValueError('Audiodauer passt nicht zum Auftrag.')
                for s in result['segments']:
                    if not isinstance(s.get('text'),str) or not 0<=float(s['start'])<float(s['end'])<=dauer+.05:
                        raise ValueError('Ungültiges Transkriptsegment.')
                result={**result,'words':gueltige_woerter(result.get('words') or [],dauer)}
        with self.db() as db:
            changed=db.execute('''UPDATE runs SET status=?,fortschritt=?,daten=COALESCE(?,daten),fehler=?,retry=?
                WHERE name=? AND basis=? AND anbieter=?''', (state,100 if state=='bereit' else
                max(0,min(99,int(data.get('fortschritt',0)))),json.dumps(result,ensure_ascii=False) if result else None,
                str(data.get('error') or '')[:400],time.time()+min(86400,max(0,float(data.get('retry',0)))),
                name,basis,anbieter))
            if not changed.rowcount:raise ValueError('Auftrag fehlt.')
