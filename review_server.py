"""Lokale Sprecherprüfung: .venv/bin/python review_server.py"""
import argparse
import gzip
import json
import mimetypes
import math
import os
import re
import secrets
import subprocess
import threading
from functools import lru_cache
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from keepsaid_config import env, DEFAULT_SHARE
from hoerfassungen import Hoerfassungen
from neutransk import Neutranskription
from mosslaeufe import MossLaeufe
from transkript_bloecke import bloecke, text_verteilen

from wortzeiten import Wortzeiten, zuordnen, gueltige_woerter
from zusammenfassung import Zusammenfassungen
from lesefassung import Lesefassungen, quelle as lesequelle
from stimmen import Stimmen
from titel import Titel, brauchbarer_titel, lade_env
from aufteilung import original_bereiche, signatur, anwenden, text_bereiche, zeitwert, mit_unbekannten_bereichen

BASIS = Path(__file__).resolve().parent
ENDUNGEN = {'.ogg', '.opus', '.m4a', '.mp3', '.wav', '.flac', '.aac', '.mp4'}


class Quelle:
    def __init__(self, share, lokal=None):
        self.share = share.rstrip('/')
        self.lokal = Path(lokal).resolve() if lokal else None

    def lesen(self, ordner, datei):
        if Path(datei).name != datei or datei in {'.', '..'}:
            raise ValueError('Ungültiger Dateiname.')
        if self.lokal:
            return (self.lokal / ordner / datei).read_bytes()
        url = self.share + '/' + ordner + '/' + urllib.parse.quote(datei, safe='')
        # Audiodateien können bei langen Aufnahmen deutlich größer sein als JSONs.
        timeout = 180 if ordner == 'audio' and Path(datei).suffix.lower() in ENDUNGEN else 30
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.read()

    def liste(self, ordner):
        if self.lokal:
            return [p.name for p in (self.lokal / ordner).iterdir() if p.is_file()]
        with urllib.request.urlopen(self.share + '/' + ordner + '/?ls', timeout=30) as r:
            return [urllib.parse.unquote(f['href'].lstrip('/')) for f in json.load(r).get('files', [])]

    @lru_cache(maxsize=512)
    def json(self, ordner, datei):
        return json.loads(self.lesen(ordner, datei))


class Anwendung:
    def __init__(self, quelle, stimmen):
        self.quelle, self.stimmen = quelle, stimmen
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.Lock()
        self.clip_lock = threading.Semaphore(2)
        self.index = None
        self.text_undo = {}
        self.titel = Titel(stimmen.pfad.with_name("titel.sqlite3"))
        self.zusammenfassungen = Zusammenfassungen(stimmen.pfad.with_name("zusammenfassungen.sqlite3"))
        self.wortzeiten = Wortzeiten(stimmen.pfad.with_name("wortzeiten.sqlite3"))
        self.hoerfassungen = Hoerfassungen(stimmen.pfad.parent / "hoerfassungen")
        self.lesefassungen = Lesefassungen(stimmen.pfad.with_name("lesefassungen.sqlite3"))
        self.neu = Neutranskription(stimmen.pfad.with_name("neutranskription.sqlite3"))
        self.moss = MossLaeufe(stimmen.pfad.with_name("mosslaeufe.sqlite3"))

    def aufnahmen(self, neu=False):
        with self.lock:
            if self.index is None or neu:
                if neu:
                    self.quelle.json.cache_clear()
                profile = self.quelle.liste('stimmprofile')
                audio = self.quelle.liste('audio')
                texte = set(self.quelle.liste('transkripte'))
                audios = {Path(f).stem: f for f in audio if Path(f).suffix.lower() in ENDUNGEN}
                self.index = {}
                metadaten = set(audio)
                titel_cache = self.titel.alle()
                for f in sorted(profile, reverse=True):
                    if not f.endswith('.stimmen.json'):
                        continue
                    name = f[:-len('.stimmen.json')]
                    text = next((f'{name}.{s}.json' for s in ['gruendlich', 'schnell']
                                 if f'{name}.{s}.json' in texte), None)
                    if text and name in audios:
                        self.index[name] = {'name': name, 'audio': audios[name], 'transkript': text}
                def beschriften(eintrag):
                    name = eintrag['name']
                    daten = {}
                    if name + '.json' in metadaten:
                        try:
                            daten = self.quelle.json('audio', name + '.json')
                        except (OSError, ValueError):
                            pass
                    titel = daten.get('title')
                    if not brauchbarer_titel(titel):
                        titel = titel_cache.get(name)
                    if not titel:
                        try:
                            transkript = self.quelle.json('transkripte', eintrag['transkript'])
                            kandidat = transkript.get('titel')
                            if brauchbarer_titel(kandidat):
                                titel = kandidat
                        except (OSError, ValueError):
                            pass
                    # Bereits aussagekräftige Dateinamen bleiben auch ohne Metadaten lesbar.
                    if not titel:
                        rest = name.split('_', 2)[-1]
                        if brauchbarer_titel(rest):
                            titel = rest.replace('-', ' ').replace('_', ' ')
                    dauer = daten.get('audio_duration')  # Soundcore-Metadaten: Millisekunden.
                    dauer = dauer / 1000 if isinstance(dauer, (int, float)) and math.isfinite(dauer) and dauer > 0 else None
                    if dauer is None and self.quelle.lokal:
                        try:
                            dauer = float(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries',
                                'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1',
                                str(self.quelle.lokal / 'audio' / eintrag['audio'])], timeout=15, stderr=subprocess.DEVNULL))
                            if not math.isfinite(dauer) or dauer <= 0:
                                dauer = None
                        except (OSError, ValueError, subprocess.SubprocessError):
                            dauer = None
                    eintrag.update(titel=titel or 'Noch kein KI-Titel', aufgenommen=daten.get('recorded_at'), dauer=dauer)
                with ThreadPoolExecutor(max_workers=8) as pool:
                    list(pool.map(beschriften, self.index.values()))
            # Neue Titel aus dem Worker/Import werden ohne erneuten Share-Abruf sichtbar.
            cache = self.titel.alle()
            for eintrag in self.index.values():
                if eintrag['titel'] == 'Noch kein KI-Titel' and eintrag['name'] in cache:
                    eintrag['titel'] = cache[eintrag['name']]
            return list(self.index.values())

    def wortzeiten_automatisch(self):
        """Alle lokalen Aufnahmen einreihen; die Wortzeiten-Pool verarbeitet seriell."""
        if self.neu.statistik()['gesamt'] or not self.quelle.lokal or not os.environ.get('GROQ_API_KEY'):
            return
        for eintrag in self.aufnahmen():
            try:
                text = self.quelle.json('transkripte', eintrag['transkript'])
                if any(gueltige_woerter(s.get('words') or []) for s in text.get('segments', [])):
                    continue
                self.wortzeiten.starten(eintrag['name'], self.audio_pfad(eintrag['name']))
            except (OSError, ValueError) as exc:
                print(f"Wortzeiten {eintrag['name']}: {exc}", flush=True)

    def audio_automatisch(self):
        if not self.quelle.lokal:
            return
        for eintrag in sorted(self.aufnahmen(),key=lambda r:r.get('dauer') or 0):
            try:
                self.hoerfassungen.anfordern(self.audio_pfad(eintrag['name']), prioritaet=10)
                self.hoerfassungen.anfordern(self.audio_pfad(eintrag['name']), prioritaet=10, bereinigt=True)
            except (OSError, ValueError):
                pass

    def neu_alle(self):
        lade_env()
        if not self.quelle.lokal:
            raise ValueError('Neutranskription bitte auf dem NAS starten.')
        if not os.environ.get('GROQ_API_KEY'):
            raise ValueError('Groq-API-Schlüssel fehlt.')
        for r in sorted(self.aufnahmen(),key=lambda r:r.get('dauer') or 0):
            self.neu.einreihen(r['name'],self.audio_pfad(r['name']),r.get('dauer'))
        self.neu.starten()
        return self.neu.statistik()

    def detail(self, name):
        eintrag = next((x for x in self.aufnahmen() if x['name'] == name), None)
        if not eintrag:
            raise ValueError('Aufnahme nicht gefunden.')
        profil = self.quelle.json('stimmprofile', name + '.stimmen.json')
        text = self.quelle.json('transkripte', eintrag['transkript'])
        modell = profil['modell']
        reviews = self.stimmen.reviews(name, modell)
        original = original_bereiche(text)
        basis = signatur(original)
        alle_aufteilungen = self.stimmen.aufteilungen(name, eintrag['transkript'], modell)
        korrekturen_zeit = [k for k in alle_aufteilungen if k['basis'] == basis]
        bereiche = anwenden(mit_unbekannten_bereichen(original, text), korrekturen_zeit)
        gesperrt = self.stimmen.lernsperren(name, modell)
        korrekturen = self.stimmen.textkorrekturen(name, eintrag['transkript'])
        neu_info = {'status':'fehlt'}
        moss_runs = {}
        moss_aktiv = None
        if self.quelle.lokal:
            datei = self.audio_pfad(name)
            aktiv = self.neu.aktive_segmente(name,datei,text.get('segments',[]),korrekturen)
            text = {**text,'segments':aktiv}
            neu_info = self.neu.lesen(name,datei)
            moss_runs = self.moss.lesen(name,datei)
            moss_aktiv = self.moss.aktive_fassung(name,datei,korrekturen,moss_runs)
            if moss_aktiv:
                text = {**text,'segments':moss_aktiv['segments']}
                neu_info = {'status':'bereit','daten':moss_aktiv,'geschuetzt':False}
            bereiche = anwenden(mit_unbekannten_bereichen(original, text), korrekturen_zeit)
        segmente_text = []
        for i, segment in enumerate(text.get('segments', [])):
            original = segment.get('text', '')
            korrektur = korrekturen.get(i)
            gueltig = korrektur and korrektur['original'] == original
            anteile = text_bereiche(segment, bereiche)
            segmente_text.append({**segment, 'bereiche': anteile, 'id': i, 'original': original,
                'text': korrektur['text'] if gueltig else original, 'korrigiert': bool(gueltig)})
        wortdaten = None
        audio_info = {'status': 'fehlt'}
        audio_clean = {'status': 'fehlt'}
        wortstatus = {'status': 'fehlt'}
        try:
            audio_pfad = self.audio_pfad(name)
            audio_info = {**self.hoerfassungen.status(audio_pfad), 'version': self.hoerfassungen.ziel(audio_pfad).stem}
            audio_clean = {**self.hoerfassungen.status(audio_pfad,True), 'version': self.hoerfassungen.ziel(audio_pfad,True).stem}
            audio_basis = self.wortzeiten.basis(audio_pfad)
            wortdaten = self.wortzeiten.lesen(name, audio_basis)
            wortstatus = self.wortzeiten.status(name, audio_basis)
        except (OSError, ValueError):
            pass
        audio_dauer = text.get('audio_sekunden')
        if not isinstance(audio_dauer, (int, float)) or not math.isfinite(audio_dauer) or audio_dauer <= 0:
            audio_dauer = None
        eingebettet = gueltige_woerter([w for s in text.get('segments', []) for w in (s.get('words') or [])], audio_dauer)
        if neu_info.get('daten') is not None:
            wortdaten = neu_info['daten']
            wortstatus = {'status':'bereit','woerter':len(wortdaten['words'])}
        wortquelle = wortdaten['words'] if wortdaten else eingebettet
        if wortquelle:
            wortstatus = {'status': 'bereit', 'woerter': len(wortquelle)}
        zuordnung = zuordnen(segmente_text, wortquelle) if wortquelle else [[] for _ in segmente_text]
        for segment, woerter in zip(segmente_text, zuordnung):
            segment['words'] = woerter
            segment['wortanzahl'] = len(segment['text'].split())
            segment['playback_start'] = woerter[0]['start'] if woerter else None
            segment['playback_end'] = woerter[-1]['end'] if woerter else None
            if woerter:
                segment['bereiche'] = text_bereiche({'start': segment['playback_start'],
                                                     'end': segment['playback_end']}, bereiche)
        sprecher = []
        keys = list(profil['sprecher'])
        for bereich in bereiche:
            if bereich['speaker'] not in keys:
                keys.append(bereich['speaker'])
        for key in keys:
            p = profil['sprecher'].get(key, {})
            segmente = [s for s in bereiche if s['speaker'] == key]
            if not segmente:
                continue
            lernbar = bool(p.get('vektor')) and key not in gesperrt
            kandidaten = self.stimmen.vorschlaege(p['vektor'], modell, (name, key), limit=8) if lernbar else []
            sprecher.append({'id': key, 'name': reviews.get(key), 'bisher': p.get('name'),
                'sekunden': sum(s['end']-s['start'] for s in segmente),
                'clips': sorted(segmente, key=lambda s: s['end']-s['start'], reverse=True)[:3],
                'vorschlag': kandidaten[0] if kandidaten else None, 'vorschlaege': kandidaten,
                'lernbar': lernbar, 'manuell': key in gesperrt, 'benennbar': True,
                'text': [s for s in segmente_text if any(b['speaker'] == key for b in s['bereiche'])][:4]})
        dauer = wortdaten['duration'] if wortdaten else (eintrag.get('dauer') or text.get('audio_sekunden'))
        if not isinstance(dauer, (int, float)) or not math.isfinite(dauer) or dauer <= 0:
            dauer = max([s['end'] for s in bereiche] + [s['end'] for s in segmente_text] + [0])
        return {'name': name, 'titel': eintrag['titel'], 'aufgenommen': eintrag['aufgenommen'],
                'modell': modell, 'sprecher': sprecher, 'namen': self.stimmen.namen(),
                'audio': audio_info, 'audio_bereinigt': audio_clean,
                'moss': {p:{k:v for k,v in state.items() if k!='daten'} for p,state in moss_runs.items()},
                'transkript_anbieter': (moss_aktiv or {}).get('anbieter'),
                'neutranskript': {**{k:v for k,v in neu_info.items() if k!='daten'},
                    'vergleich': '\n'.join(s['text'] for s in (neu_info.get('daten') or {}).get('segments',[])) if neu_info.get('geschuetzt') else None},
                'transkript': segmente_text, 'bloecke': bloecke(segmente_text, bereiche, reviews, dauer), 'bereiche': bereiche, 'dauer': dauer, 'wortzeiten': wortstatus,
                'aufteilungen': korrekturen_zeit, 'aufteilung_basis': basis,
                'aufteilung_version': [k['id'] for k in alle_aufteilungen],
                'aufteilung_veraltet': len(alle_aufteilungen)-len(korrekturen_zeit)}

    def audio_pfad(self, name):
        eintrag = next((r for r in self.aufnahmen() if r['name'] == name), None)
        if not eintrag:
            raise ValueError('Aufnahme nicht gefunden.')
        pfad = self.quelle.lokal / 'audio' / eintrag['audio'] if self.quelle.lokal else BASIS / 'work' / eintrag['audio']
        if not pfad.is_file():
            raise ValueError('Die Wortzeiten können direkt auf dem NAS mit den lokalen Audiodateien erstellt werden.')
        return pfad

    @staticmethod
    def zusammenfassung_text(detail):
        namen = {s['id']: s['name'] or 'Unbekannte Person' for s in detail['sprecher']}
        return '\n'.join(f"[{s['start']:.2f}] " +
            ', '.join(dict.fromkeys(namen.get(b['speaker'], 'Unbekannte Person') for b in s['bereiche'])) +
            ': ' + s['text'] for s in detail['transkript'])

    def dashboard(self):
        aufnahmen = self.aufnahmen()
        offen, bestaetigt, stimmen_gesamt, fehler = [], 0, 0, 0
        # Der NAS liest diese kleinen JSON-Dateien lokal. Keine Audioabrufe.
        def zaehlen(eintrag):
            try:
                name = eintrag['name']
                profil = self.quelle.json('stimmprofile', name + '.stimmen.json')
                text = self.quelle.json('transkripte', eintrag['transkript'])
                original = original_bereiche(text)
                basis = signatur(original)
                korrekturen = [k for k in self.stimmen.aufteilungen(name, eintrag['transkript'], profil['modell'])
                               if k['basis'] == basis]
                bereiche = anwenden(mit_unbekannten_bereichen(original, text), korrekturen)
                namen = self.stimmen.reviews(name, profil['modell'])
                ids = {b['speaker'] for b in bereiche}
                offen = {sp for sp in ids if not namen.get(sp)}
                erster = next((b for b in bereiche if b['speaker'] in offen), None)
                segment = next((i for i, s in enumerate(text.get('segments', [])) if erster and
                                s['start'] < erster['end'] and s['end'] > erster['start']), 0)
                return eintrag, len(ids), len(offen), segment
            except (OSError, ValueError, KeyError, TypeError):
                return eintrag, None, 0, 0
        with ThreadPoolExecutor(max_workers=6) as pool:
            for eintrag, anzahl, offen_anzahl, segment in pool.map(zaehlen, aufnahmen):
                if anzahl is None:
                    fehler += 1
                    continue
                stimmen_gesamt += anzahl
                bestaetigt += anzahl-offen_anzahl
                if offen_anzahl:
                    offen.append({**eintrag, 'offen': offen_anzahl, 'segment': segment})
        return {'aufnahmen_gesamt': len(aufnahmen), 'personen_gesamt': len(self.stimmen.namen()),
                'zuordnungen_bestaetigt': bestaetigt, 'stimmen_gesamt': stimmen_gesamt,
                'offene_aufnahmen': offen, 'nicht_geladen': fehler}

    def aufteilen(self, data):
        name = data['aufnahme']
        d = self.detail(name)
        args = (name, self.index[name]['transkript'], d['modell'], d['aufteilung_basis'], data.get('version'))
        if data.get('aktion') == 'zurueck':
            self.stimmen.aufteilen(*args, zurueck=True)
        else:
            quelle, ziel = data['quelle'], data['ziel']
            ids = {s['id'] for s in d['sprecher']}
            if (quelle not in ids and quelle != '__bereich__') or (ziel != 'neu' and ziel not in ids):
                raise ValueError('Bitte einen vorhandenen oder neuen Sprecher auswählen.')
            if ziel == quelle:
                raise ValueError('Quell- und Zielsprecher müssen verschieden sein.')
            start, ende = zeitwert(data['start']), zeitwert(data['ende'])
            if start >= ende or ende > d['dauer']:
                raise ValueError('Bitte einen Bereich mit Start vor Ende innerhalb der Aufnahme wählen.')
            if not any((quelle == '__bereich__' or s['speaker'] == quelle) and s['start'] < ende and s['end'] > start for s in d['bereiche']):
                raise ValueError('Der gewählte Sprecher spricht in diesem Zeitbereich nicht.')
            neuer_name = data.get('name') if ziel == 'neu' else None
            if ziel == 'neu':
                if not isinstance(neuer_name,str) or not neuer_name.strip():
                    raise ValueError('Bitte den Namen für den neuen Sprecher eingeben.')
                ziel = 'manuell_' + secrets.token_hex(8)
            self.stimmen.aufteilen(*args, quelle=quelle, ziel=ziel, start=start, ende=ende, name=neuer_name,
                quellen={b['speaker'] for b in d['bereiche'] if b['start'] < ende and b['end'] > start} if quelle == '__bereich__' else None)
        return self.detail(name)

    @lru_cache(maxsize=128)
    def audio_url(self, datei):
        url = self.quelle.share + '/audio/' + urllib.parse.quote(datei, safe='')
        # Keine stundenlange Aufnahme als stillen Fallback herunterladen.
        req = urllib.request.Request(url, headers={'Range': 'bytes=0-0'})
        with urllib.request.urlopen(req, timeout=15) as r:
            if r.status != 206 or not r.headers.get('Content-Range', '').startswith('bytes 0-0/'):
                raise ValueError('Der Audio-Share unterstützt keine Teilabrufe. Stimmprobe kann nicht schnell geladen werden.')
            r.read(1)
        return url

    @lru_cache(maxsize=96)
    def clip(self, datei, start, dauer):
        if Path(datei).name != datei or datei in {'.', '..'}:
            raise ValueError('Ungültiger Dateiname.')
        with self.clip_lock:
            lokal = self.quelle.lokal / 'audio' / datei if self.quelle.lokal else BASIS / 'work' / datei
            optionen = []
            if lokal.is_file():
                quelle = str(lokal)
            else:
                if self.quelle.lokal:
                    raise ValueError('Lokale Audiodatei nicht gefunden.')
                quelle = self.audio_url(datei)
                # Kleine, begrenzte HTTP-Range-Anfragen: Header, Suchposition und
                # die wenigen Sekunden Audio. Auch kein großer Read-ahead-Download.
                optionen = ['-rw_timeout', '15000000', '-seekable', '1',
                    '-multiple_requests', '1', '-request_size', '65536',
                    '-initial_request_size', '65536']
            p = subprocess.run(['ffmpeg', '-v', 'error', '-ss', str(start),
                *optionen, '-i', quelle, '-t', str(dauer), '-ac', '1',
                '-ar', '16000', '-f', 'wav', 'pipe:1'],
                capture_output=True, timeout=60, check=True)
        return p.stdout



class Handler(BaseHTTPRequestHandler):
    def antwort(self, status, daten, typ='application/json; charset=utf-8'):
        if not isinstance(daten, bytes):
            daten = json.dumps(daten, ensure_ascii=False, separators=(',', ':')).encode()
        komprimiert = False
        akzeptiert = self.headers.get('Accept-Encoding', '')
        gzip_erlaubt = any(part.strip().split(';')[0].lower() == 'gzip' and
                          not re.search(r';\s*q=0(?:\.0*)?\s*$', part)
                          for part in akzeptiert.split(','))
        if len(daten) >= 1024 and (typ.startswith('application/json') or typ.startswith('text/')) and gzip_erlaubt:
            gepackt = gzip.compress(daten, compresslevel=3, mtime=0)
            if len(gepackt) < len(daten):
                daten, komprimiert = gepackt, True
        self.send_response(status)
        self.send_header('Vary', 'Accept-Encoding')
        if komprimiert:
            self.send_header('Content-Encoding', 'gzip')
        self.send_header('Content-Type', typ)
        self.send_header('Content-Length', str(len(daten)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(daten)

    def route(self):
        url = urllib.parse.urlsplit(self.path)
        pfad = url.path
        prefix = getattr(self.server, 'base_path', '')
        if prefix and (pfad == prefix or pfad.startswith(prefix + '/')):
            pfad = pfad[len(prefix):] or '/'
        return pfad, urllib.parse.parse_qs(url.query)

    @property
    def app(self):
        return self.server.app

    def do_GET(self):
        try:
            pfad, q = self.route()
            if pfad == '/':
                html = (BASIS / 'review_ui.html').read_text()
                prefix = getattr(self.server, 'base_path', '')
                if prefix:
                    html = html.replace('<style>', f'<base href="{prefix}/"><style>', 1)
                self.antwort(200, html.encode(), 'text/html; charset=utf-8')
            elif pfad == '/api/bibliothek':
                # Konsistenter SQLite-Snapshot für den Mac-Worker, auch während Reviews.
                with self.app.stimmen.db() as db:
                    daten = db.serialize()
                self.antwort(200, daten, 'application/vnd.sqlite3')
            elif pfad == '/api/aufnahmen':
                self.antwort(200, {'aufnahmen': self.app.aufnahmen(q.get('neu') == ['1']), 'token': self.app.token})
            elif pfad == '/api/wortzeiten':
                name = q['name'][0]
                pfad_audio = self.app.audio_pfad(name)
                self.antwort(200, self.app.wortzeiten.status(name, self.app.wortzeiten.basis(pfad_audio)))
            elif pfad == '/api/moss':
                self.antwort(200, self.app.moss.statistik())
            elif pfad == '/api/moss-fassung':
                state = self.app.moss.lesen(q['name'][0],self.app.audio_pfad(q['name'][0])).get(q['anbieter'][0],{})
                self.antwort(200, {'status':state.get('status','fehlt'),'daten':state.get('daten')})
            elif pfad == '/api/neutranskription':
                self.antwort(200, self.app.neu.statistik())
            elif pfad == '/api/dashboard':
                self.antwort(200, self.app.dashboard())
            elif pfad == '/api/lesefassung':
                d = self.app.detail(q['name'][0])
                self.antwort(200, {'lesefassung':self.app.lesefassungen.lesen(d['name'],lesequelle(d))})
            elif pfad == '/api/zusammenfassung':
                d = self.app.detail(q['name'][0])
                self.antwort(200, {'zusammenfassung': self.app.zusammenfassungen.lesen(d['name'], self.app.zusammenfassung_text(d))})
            elif pfad == '/api/audio-status':
                datei = self.app.audio_pfad(q['name'][0])
                bereinigt = q.get('bereinigt') == ['1']
                state = self.app.hoerfassungen.status(datei,bereinigt)
                if state['status'] != 'fehler' or q.get('neu') == ['1']:
                    state = self.app.hoerfassungen.anfordern(datei,bereinigt=bereinigt)
                self.antwort(200, state)
            elif pfad == '/api/audio':
                self.audio(q['name'][0], kompakt=q.get('kompakt') == ['1'], bereinigt=q.get('bereinigt') == ['1'], moss_asr=q.get('moss_asr') == ['1'])
            elif pfad == '/api/aufnahme':
                self.antwort(200, self.app.detail(q['name'][0]))
            elif pfad == '/api/ausschnitt':
                name = q['name'][0]
                d = self.app.detail(name)
                start, ende = zeitwert(q['start'][0]), zeitwert(q['ende'][0])
                if not start < ende <= d['dauer']:
                    raise ValueError('Bitte Start und Ende innerhalb der Aufnahme wählen.')
                self.antwort(200, self.app.clip(self.app.index[name]['audio'], start, min(ende-start,15)), 'audio/wav')
            elif pfad == '/api/clip':
                name, speaker = q['name'][0], q['sprecher'][0]
                d = self.app.detail(name)
                sp = next(s for s in d['sprecher'] if s['id'] == speaker)
                clip = sp['clips'][int(q.get('clip', ['0'])[0])]
                start, dauer = clip['start'], min(clip['end']-clip['start'], 15)
                eintrag = self.app.index[name]
                self.antwort(200, self.app.clip(eintrag['audio'], start, dauer), 'audio/wav')
            else:
                self.antwort(404, {'error': 'Nicht gefunden.'})
        except (ValueError, KeyError, IndexError, StopIteration) as e:
            self.antwort(400, {'error': str(e) or 'Ungültige Auswahl.'})
        except Exception as e:
            self.log_error('%s', e)
            self.antwort(502, {'error': 'Daten konnten nicht geladen werden. Share-Verbindung und ffmpeg prüfen.'})

    def audio(self, name, kompakt=False, bereinigt=False, moss_asr=False):
        eintrag = next((r for r in self.app.aufnahmen() if r['name'] == name), None)
        if not eintrag:
            raise ValueError('Aufnahme nicht gefunden.')
        datei = eintrag['audio']
        lokal = (self.app.quelle.lokal / 'audio' / datei if self.app.quelle.lokal else BASIS / 'work' / datei)
        if not lokal.is_file():
            self.send_response(302)
            self.send_header('Location', self.app.quelle.share + '/audio/' + urllib.parse.quote(datei, safe=''))
            self.end_headers()
            return
        if moss_asr:
            lokal = self.app.moss.audio / (self.app.moss.basis(lokal)+'.flac')
            if not lokal.is_file():
                return self.antwort(503, {'error':'MossFormer2 wird vorbereitet.'})
            datei = lokal.name
        if kompakt:
            ziel = self.app.hoerfassungen.ziel(lokal,bereinigt)
            if not ziel.is_file():
                self.app.hoerfassungen.anfordern(lokal,bereinigt=bereinigt)
                return self.antwort(503, {'error': 'Hörfassung wird vorbereitet.'})
            lokal, datei = ziel, ziel.name
        groesse = lokal.stat().st_size
        start, ende, status = 0, groesse-1, 200
        range_header = self.headers.get('Range')
        if range_header:
            match = re.fullmatch(r'bytes=(\d*)-(\d*)', range_header.strip())
            if not match or not any(match.groups()):
                return self.audio_range_fehler(groesse)
            a, b = match.groups()
            if a:
                start, ende = int(a), min(int(b), ende) if b else ende
            else:
                start = max(0, groesse-int(b))
            if not 0 <= start <= ende < groesse:
                return self.audio_range_fehler(groesse)
            status = 206
        self.send_response(status)
        self.send_header('Content-Type', mimetypes.guess_type(datei)[0] or 'application/octet-stream')
        self.send_header('Accept-Ranges', 'bytes')
        if kompakt:
            self.send_header('Cache-Control', 'private, max-age=86400, immutable')
        self.send_header('Content-Length', str(max(0, ende-start+1)))
        if status == 206:
            self.send_header('Content-Range', f'bytes {start}-{ende}/{groesse}')
        self.end_headers()
        try:
            with lokal.open('rb') as audio:
                audio.seek(start)
                rest = ende-start+1
                while rest > 0:
                    block = audio.read(min(rest, 65536))
                    if not block:
                        break
                    self.wfile.write(block)
                    rest -= len(block)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def audio_range_fehler(self, groesse):
        self.send_response(416)
        self.send_header('Content-Range', f'bytes */{groesse}')
        self.send_header('Content-Length', '0')
        self.end_headers()

    def do_POST(self):
        pfad, _ = self.route()
        if pfad not in {'/api/lesefassung', '/api/moss', '/api/review', '/api/neutranskription', '/api/neu-lokal', '/api/text-zurueck', '/api/textblock', '/api/text', '/api/aufteilung', '/api/zusammenfassung', '/api/wortzeiten'}:
            return self.antwort(404, {'error': 'Nicht gefunden.'})
        if not secrets.compare_digest(self.headers.get('X-Review-Token', ''), self.app.token):
            return self.antwort(403, {'error': 'Bitte die Seite neu laden.'})
        try:
            laenge = int(self.headers.get('Content-Length', 0))
            if not 0 < laenge <= (16000000 if pfad in {'/api/neu-lokal','/api/moss'} else 1000000):
                raise ValueError('Ungültige Anfragegröße.')
            data = json.loads(self.rfile.read(laenge))
            if pfad == '/api/moss':
                if data.get('aktion')=='holen':
                    return self.antwort(200, {'jobs':self.app.moss.jobs(self.app.aufnahmen(),self.app.audio_pfad)})
                self.app.moss.speichern(data,self.app.audio_pfad(data['name']))
                return self.antwort(200, {'ok':True})
            if pfad == '/api/neu-lokal':
                if data.get('aktion')=='holen':
                    return self.antwort(200, {'job':self.app.neu.lokal_uebernehmen()})
                self.app.neu.lokal_ergebnis(data)
                return self.antwort(200, {'ok':True})
            if pfad == '/api/neutranskription':
                return self.antwort(202, self.app.neu_alle())
            if pfad == '/api/wortzeiten':
                name = data['aufnahme']
                return self.antwort(202, self.app.wortzeiten.starten(name, self.app.audio_pfad(name)))
            if pfad == '/api/lesefassung':
                d = self.app.detail(data['aufnahme'])
                return self.antwort(202, {'lesefassung':self.app.lesefassungen.starten(d['name'],lesequelle(d))})
            if pfad == '/api/zusammenfassung':
                d = self.app.detail(data['aufnahme'])
                return self.antwort(200, {'zusammenfassung': self.app.zusammenfassungen.erstellen(d['name'], self.app.zusammenfassung_text(d))})
            if pfad == '/api/aufteilung':
                return self.antwort(200, self.app.aufteilen(data))
            if pfad == '/api/text-zurueck':
                with self.app.lock:
                    undo = self.app.text_undo.get(data['token'])
                if not undo or undo['name'] != data['aufnahme']:
                    raise ValueError('Diese Rücknahme ist nicht mehr verfügbar.')
                self.app.stimmen.texte_speichern(undo['name'], undo['transkript'], undo['texte'], undo['erwartet'])
                with self.app.lock:
                    self.app.text_undo.pop(data['token'], None)
                return self.antwort(200, self.app.detail(undo['name']))
            if pfad == '/api/textblock':
                name = data['aufnahme']
                d = self.app.detail(name)
                block = next((b for b in d['bloecke'] if b['id'] == data['block']), None)
                if not block or data.get('version') != block['version'] or data.get('vorher') != block['text']:
                    return self.antwort(409, {'error': 'Text oder Sprecher wurde inzwischen geändert. Bitte die Aufnahme neu öffnen.'})
                texte = text_verteilen(block, data['text'], d['transkript'])
                self.app.stimmen.texte_speichern(name, self.app.index[name]['transkript'], texte,
                    {i:s['text'] for i,s in enumerate(d['transkript'])})
                token = secrets.token_urlsafe(24)
                with self.app.lock:
                    self.app.text_undo[token] = {'name': name, 'transkript': self.app.index[name]['transkript'],
                        'texte': [(i, original, d['transkript'][i]['text']) for i,original,text in texte],
                        'erwartet': {i:text for i,original,text in texte}}
                    while len(self.app.text_undo) > 100:
                        self.app.text_undo.pop(next(iter(self.app.text_undo)))
                result = self.app.detail(name)
                result['text_undo'] = token
                return self.antwort(200, result)
            if pfad == '/api/text':
                name = data['aufnahme']
                d = self.app.detail(name)
                segment = data['segment']
                if type(segment) is not int or not 0 <= segment < len(d['transkript']):
                    raise ValueError('Textabschnitt nicht gefunden.')
                s = d['transkript'][segment]
                if data.get('vorher') != s['text']:
                    return self.antwort(409, {'error': 'Dieser Text wurde inzwischen geändert. Bitte die Aufnahme neu öffnen.'})
                self.app.stimmen.text_speichern(name, self.app.index[name]['transkript'],
                    segment, s['original'], data['text'])
                return self.antwort(200, self.app.detail(name))
            name, speaker = data['aufnahme'], data['sprecher']
            # Niemals vom Browser angelieferte Vektoren als Referenzen übernehmen.
            d = self.app.detail(name)
            sp = next((s for s in d['sprecher'] if s['id'] == speaker),None)
            if not sp:
                raise ValueError('Sprecher nicht gefunden.')
            if data.get('aktion') == 'entfernen':
                self.app.stimmen.entfernen(name, speaker, d['modell'])
            elif not sp['lernbar']:
                self.app.stimmen.manuell_benennen(name,speaker,d['modell'],data['name'])
            else:
                p = self.app.quelle.json('stimmprofile', name + '.stimmen.json')
                self.app.stimmen.bestaetigen(name, speaker, d['modell'], data['name'], p['sprecher'][speaker]['vektor'])
            self.antwort(200, self.app.detail(name))
        except (ValueError, KeyError, TypeError) as e:
            self.antwort(400, {'error': str(e)})
        except Exception as e:
            self.log_error('%s', e)
            self.antwort(502, {'error': 'Speichern fehlgeschlagen. Bitte erneut versuchen.'})


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--base-path', default='', help='URL-Präfix hinter dem Reverse-Proxy, z.B. /review')
    ap.add_argument('--referenzen', help='JSON-Datei mit bekannten Stimmreferenzen')
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--share', default=env('SHARE_URL', DEFAULT_SHARE))
    ap.add_argument('--daten', help='Lokales Verzeichnis mit audio/, transkripte/, stimmprofile/ statt Share')
    ap.add_argument('--db', help='Alternative SQLite-Datei')
    ap.add_argument('--neu-alle', action='store_true', help='Alle Aufnahmen mit Rauschfilter neu transkribieren, kurze zuerst')
    args = ap.parse_args()
    lade_env()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    prefix = args.base_path.rstrip('/')
    if prefix and (not prefix.startswith('/') or any(c not in '/abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in prefix)):
        ap.error('Ungültiger URL-Präfix.')
    server.base_path = prefix
    server.app = Anwendung(Quelle(args.share, args.daten), Stimmen(args.db, args.referenzen))
    if args.daten:
        if env('MOSS', '0') != '1':
            if args.neu_alle:
                server.app.neu_alle()
            else:
                server.app.neu.starten()
        else:
            for r in server.app.aufnahmen():
                server.app.moss.einreihen(r['name'],server.app.audio_pfad(r['name']),r.get('dauer') or 0)
        threading.Thread(target=server.app.audio_automatisch, daemon=True, name='audio-alle').start()
    if args.daten and env('AUTO_WORTZEITEN', '1') == '1':
        threading.Thread(target=server.app.wortzeiten_automatisch, daemon=True, name='wortzeiten-alle').start()
    print(f'Stimmen prüfen: http://localhost:{args.port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
