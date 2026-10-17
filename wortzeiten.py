"""Echte ASR-Wortzeiten speichern und unveränderte Wörter im Review-Text zuordnen."""
import difflib
import hashlib
import json
import math
import os
import re
import secrets
import sqlite3
import subprocess
import threading
import time
import unicodedata
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from titel import lade_env


def normalwort(text):
    return ''.join(c for c in unicodedata.normalize('NFKC', text).casefold() if c.isalnum())


def gueltige_woerter(woerter, dauer=None):
    result = []
    for w in woerter:
        if hasattr(w, 'model_dump'):
            w = w.model_dump()
        try:
            start, ende = float(w['start']), float(w['end'])
            wort = w['word']
            if (isinstance(wort, str) and normalwort(wort) and math.isfinite(start) and math.isfinite(ende)
                    and 0 <= start < ende and (dauer is None or ende <= dauer + .05)):
                result.append({'word': wort, 'start': round(start, 3), 'end': round(ende, 3)})
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(result, key=lambda w: (w['start'], w['end']))


def zuordnen(segmente, woerter):
    """Keine lineare Zeitschätzung: nur tatsächlich erkannte, passende Wörter übernehmen."""
    # Bind matching to the segment's actual time window. A repeated phrase hours
    # later must not steal its timestamps; short local windows also avoid quadratic
    # whole-recording matching for thousands of repeated conversational words.
    if segmente and all(isinstance(s.get('start'),(int,float)) and isinstance(s.get('end'),(int,float)) for s in segmente):
        from bisect import bisect_left, bisect_right
        ordered=sorted(woerter,key=lambda w:(w['start']+w['end'])/2)
        midpoints=[(w['start']+w['end'])/2 for w in ordered]
        return [zuordnen([{'text':s['text']}],ordered[
            bisect_left(midpoints,s['start']-.35):bisect_right(midpoints,s['end']+.35)])[0] for s in segmente]
    tokens = [(i, m) for i, s in enumerate(segmente) for m in re.finditer(r'\S+', s['text'])]
    links = [normalwort(m.group()) for _, m in tokens]
    rechts = [normalwort(w['word']) for w in woerter]
    ergebnis = [[] for _ in segmente]
    # Einzelne Füllwörter in wiederkehrenden Sätzen sind keine zuverlässigen Anker.
    from collections import Counter
    links_anzahl, rechts_anzahl = Counter(links), Counter(rechts)
    matcher = difflib.SequenceMatcher(None, links, rechts, autojunk=False)
    for block in matcher.get_matching_blocks():
        for j in range(block.size):
            a, b = block.a+j, block.b+j
            i, match = tokens[a]
            if not links[a] or (block.size == 1 and (links_anzahl[links[a]] != 1 or rechts_anzahl[links[a]] != 1)):
                continue
            ergebnis[i].append({**woerter[b], 'word': match.group(),
                               'char_start': match.start(), 'char_end': match.end()})
    return ergebnis


def neue_segmente(segmente, woerter=None):
    """Die Modelle liefern Wortzeiten; Segmenttext bleibt als Original erhalten."""
    result = [{'start': round(s['start'], 2), 'end': round(s['end'], 2), 'text': s['text'].strip(),
               'words': gueltige_woerter(s.get('words') or [])} for s in segmente]
    if woerter:
        zuordnung = zuordnen(result, gueltige_woerter(woerter))
        for segment, worte in zip(result, zuordnung):
            segment['words'] = [{k: w[k] for k in ['word', 'start', 'end']} for w in worte]
    return result


class Wortzeiten:
    def __init__(self, pfad):
        self.pfad = Path(pfad)
        self.lock = threading.Lock()
        self.jobs = {}
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='wortzeiten')
        with sqlite3.connect(self.pfad) as db:
            db.execute('CREATE TABLE IF NOT EXISTS wortzeiten (aufnahme TEXT PRIMARY KEY, basis TEXT, daten TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS wortzeiten_teile (aufnahme TEXT, basis TEXT, start INTEGER, daten TEXT, PRIMARY KEY(aufnahme,basis,start))')

    @staticmethod
    def basis(datei):
        p = Path(datei)
        st = p.stat()
        return hashlib.sha256(f'{p.name}:{st.st_size}:{st.st_mtime_ns}'.encode()).hexdigest()

    def lesen(self, aufnahme, basis):
        with sqlite3.connect(self.pfad) as db:
            row = db.execute('SELECT basis,daten FROM wortzeiten WHERE aufnahme=?', (aufnahme,)).fetchone()
        return json.loads(row[1]) if row and row[0] == basis else None

    def status(self, aufnahme, basis):
        with self.lock:
            job = self.jobs.get((aufnahme, basis))
            if job:
                return dict(job)
        daten = self.lesen(aufnahme, basis)
        return {'status': 'bereit', 'woerter': len(daten['words'])} if daten else {'status': 'fehlt'}

    def starten(self, aufnahme, datei):
        lade_env()
        basis = self.basis(datei)
        with self.lock:
            key = (aufnahme, basis)
            if self.jobs.get(key, {}).get('status') in {'wartet', 'laeuft'}:
                return dict(self.jobs[key])
            vorhanden = self.lesen(aufnahme, basis)
            if vorhanden:
                return {'status': 'bereit', 'woerter': len(vorhanden['words'])}
            if not os.environ.get('GROQ_API_KEY'):
                raise ValueError('Die Wortzuordnung benötigt den bereits verwendeten Groq-API-Schlüssel.')
            self.jobs[key] = {'status': 'wartet', 'fortschritt': 0}
            self.pool.submit(self.verarbeiten, aufnahme, str(datei), basis)
            return dict(self.jobs[key])

    def verarbeiten(self, aufnahme, datei, basis):
        key = (aufnahme, basis)
        try:
            with self.lock:
                self.jobs[key] = {'status': 'laeuft', 'fortschritt': 0}
            # Tatsächliche dekodierte Dauer prüfen, nicht alte Transkript-Zeitmarken.
            probe = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries',
                                'format=duration', '-of', 'json', datei], timeout=30))
            dauer = float(probe['format']['duration'])
            if not math.isfinite(dauer) or dauer <= 0:
                raise ValueError('Die Audiodauer konnte nicht bestimmt werden.')
            woerter = []
            # Überlappende Ausschnitte begrenzen Upload und RAM, auch bei mehrstündigen Aufnahmen.
            for start in range(0, math.ceil(dauer), 240):
                ab = max(0, start-1)
                bis = min(dauer, start+241)
                with sqlite3.connect(self.pfad) as db:
                    teil = db.execute('SELECT daten FROM wortzeiten_teile WHERE aufnahme=? AND basis=? AND start=?',
                                      (aufnahme, basis, start)).fetchone()
                if teil:
                    daten = json.loads(teil[0])
                else:
                    p = subprocess.run(['ffmpeg', '-v', 'error', '-ss', str(ab), '-i', datei,
                        '-t', str(bis-ab), '-ac', '1', '-ar', '16000', '-f', 'wav', 'pipe:1'],
                        capture_output=True, timeout=180, check=True)
                    daten = self.transkribieren(p.stdout)
                    with sqlite3.connect(self.pfad) as db:
                        db.execute('INSERT OR REPLACE INTO wortzeiten_teile VALUES (?,?,?,?)',
                                   (aufnahme, basis, start, json.dumps(daten)))
                for w in gueltige_woerter(daten.get('words') or [], bis-ab):
                    mitte = (w['start']+w['end'])/2+ab
                    if start <= mitte < min(start+240, dauer):
                        woerter.append({**w, 'start': round(w['start']+ab, 3), 'end': round(w['end']+ab, 3)})
                with self.lock:
                    self.jobs[key] = {'status': 'laeuft', 'fortschritt': min(99, round(100*bis/dauer))}
            if not woerter:
                raise ValueError('Es wurden keine verlässlichen Wortzeiten erkannt.')
            if basis != self.basis(datei):
                raise ValueError('Die Audiodatei wurde inzwischen geändert. Bitte erneut starten.')
            result = {'duration': dauer, 'words': gueltige_woerter(woerter, dauer)}
            with sqlite3.connect(self.pfad) as db:
                db.execute('INSERT OR REPLACE INTO wortzeiten VALUES (?,?,?)', (aufnahme, basis, json.dumps(result)))
                db.execute('DELETE FROM wortzeiten_teile WHERE aufnahme=?', (aufnahme,))
            with self.lock:
                self.jobs[key] = {'status': 'bereit', 'woerter': len(result['words']), 'fortschritt': 100}
        except Exception as exc:
            with self.lock:
                self.jobs[key] = {'status': 'fehler', 'error': str(exc) if isinstance(exc, ValueError) else
                                  'Wortzuordnung fehlgeschlagen. Bitte später erneut versuchen.'}

    @staticmethod
    def transkribieren(audio, on_retry=None, attempts=9):
        boundary = 'keepsaid-' + secrets.token_hex(16)
        body = bytearray()
        fields = [('model', 'whisper-large-v3'), ('language', 'de'), ('response_format', 'verbose_json'),
                  ('timestamp_granularities[]', 'word'), ('timestamp_granularities[]', 'segment'), ('temperature', '0')]
        for name, value in fields:
            body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
        body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="audio.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode())
        body.extend(audio)
        body.extend(f'\r\n--{boundary}--\r\n'.encode())
        req = urllib.request.Request('https://api.groq.com/openai/v1/audio/transcriptions', data=bytes(body),
            headers={'Authorization': 'Bearer '+os.environ['GROQ_API_KEY'], 'User-Agent': 'Keepsaid-Review/1.0',
                     'Content-Type': 'multipart/form-data; boundary='+boundary})
        try:
            for attempt in range(attempts):
                try:
                    with urllib.request.urlopen(req, timeout=180) as response:
                        return json.load(response)
                except urllib.error.HTTPError as exc:
                    if exc.code != 429 or attempt == attempts-1:
                        raise
                    try:
                        delay = float(exc.headers.get('Retry-After', '0'))
                    except (TypeError, ValueError):
                        delay = 0
                    delay = min(3600, max(60 * 2 ** attempt, delay))
                    if on_retry:
                        on_retry(delay)
                    time.sleep(delay)
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                raise ValueError('Groq ist gerade ausgelastet oder das Zeitkontingent ist erreicht. Bitte später erneut starten.') from exc
            raise ValueError('Der Spracherkennungsdienst konnte keine Wortzeiten liefern. Bitte später erneut versuchen.') from exc
        except urllib.error.URLError as exc:
            raise ValueError('Der Spracherkennungsdienst ist nicht erreichbar. Bitte später erneut versuchen.') from exc
