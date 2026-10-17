"""Bestätigte Sprecherbeispiele und konservative, modellgebundene Namensvorschläge."""
import json
import math
import sqlite3
from pathlib import Path

BASIS = Path(__file__).resolve().parent


def normalisiert(vektor):
    if not isinstance(vektor, list) or not vektor:
        raise ValueError('Kein Stimmabdruck vorhanden.')
    v = [float(x) for x in vektor]
    norm = math.sqrt(sum(x*x for x in v))
    if not math.isfinite(norm) or norm == 0:
        raise ValueError('Ungültiger Stimmabdruck.')
    return [x/norm for x in v]


class Stimmen:
    def __init__(self, pfad=None, referenzen=None):
        self.pfad = Path(pfad) if pfad else BASIS / 'review' / 'stimmen.sqlite3'
        self.pfad.parent.mkdir(parents=True, exist_ok=True)
        self.referenzen = Path(referenzen) if referenzen else BASIS / 'sprecher_profile.json'
        with self.db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS beispiele (
                aufnahme TEXT, sprecher TEXT, modell TEXT, name TEXT,
                vektor TEXT, aktualisiert TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (aufnahme, sprecher, modell))''')

            db.execute('''CREATE TABLE IF NOT EXISTS textkorrekturen (
                aufnahme TEXT, transkript TEXT, segment INTEGER, original TEXT, text TEXT,
                PRIMARY KEY (aufnahme, transkript, segment))''')

            db.execute('''CREATE TABLE IF NOT EXISTS aufteilungen (
                id INTEGER PRIMARY KEY AUTOINCREMENT, aufnahme TEXT, transkript TEXT,
                modell TEXT, basis TEXT, quelle TEXT, ziel TEXT, start REAL, ende REAL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS manuelle_namen (
                aufnahme TEXT, sprecher TEXT, modell TEXT, name TEXT,
                PRIMARY KEY (aufnahme,sprecher,modell))''')
            db.execute('''CREATE TABLE IF NOT EXISTS lernsperren (
                aufnahme TEXT, sprecher TEXT, modell TEXT,
                PRIMARY KEY (aufnahme,sprecher,modell))''')

    def db(self):
        db = sqlite3.connect(self.pfad, timeout=15)
        db.row_factory = sqlite3.Row
        return db

    def bestaetigen(self, aufnahme, sprecher, modell, name, vektor):
        name = name.strip()
        if not name or len(name) > 120:
            raise ValueError('Bitte einen Namen mit 1–120 Zeichen eingeben.')
        v = normalisiert(vektor)
        with self.db() as db:
            if db.execute('SELECT 1 FROM lernsperren WHERE aufnahme=? AND sprecher=? AND modell=?', (aufnahme,sprecher,modell)).fetchone():
                raise ValueError('Aufgeteilte Stimmen können nur manuell benannt werden.')
            db.execute('''INSERT INTO beispiele (aufnahme,sprecher,modell,name,vektor)
                VALUES (?,?,?,?,?) ON CONFLICT(aufnahme,sprecher,modell) DO UPDATE SET
                name=excluded.name, vektor=excluded.vektor, aktualisiert=CURRENT_TIMESTAMP''',
                (aufnahme, sprecher, modell, name, json.dumps(v)))

    def entfernen(self, aufnahme, sprecher, modell):
        with self.db() as db:
            db.execute('DELETE FROM manuelle_namen WHERE aufnahme=? AND sprecher=? AND modell=?', (aufnahme,sprecher,modell))
            db.execute('DELETE FROM beispiele WHERE aufnahme=? AND sprecher=? AND modell=?',
                       (aufnahme, sprecher, modell))

    def reviews(self, aufnahme, modell):
        with self.db() as db:
            namen = {r['sprecher']: r['name'] for r in db.execute(
                'SELECT sprecher,name FROM beispiele WHERE aufnahme=? AND modell=?', (aufnahme, modell))}
            namen.update({r['sprecher']: r['name'] for r in db.execute(
                'SELECT sprecher,name FROM manuelle_namen WHERE aufnahme=? AND modell=?', (aufnahme,modell))})
            return namen

    def namen(self):
        seeds = json.loads(self.referenzen.read_text()) if self.referenzen.exists() else {}
        with self.db() as db:
            namen = {r[0] for r in db.execute('SELECT name FROM beispiele UNION SELECT name FROM manuelle_namen')}
        return sorted(namen | set(seeds), key=str.casefold)

    def vorschlaege(self, vektor, modell, ausschluss=None, limit=3):
        v = normalisiert(vektor)
        kandidaten = {}
        # Alte Referenzen ohne Modellangabe dienen nur als Namensliste. Gleich große
        # Vektoren verschiedener Modelle sind nicht miteinander vergleichbar.
        if self.referenzen.exists():
            for name, p in json.loads(self.referenzen.read_text()).items():
                if p.get('modell') == modell:
                    kandidaten.setdefault(name, []).append(p.get('vektor'))
        with self.db() as db:
            for r in db.execute('SELECT * FROM beispiele WHERE modell=?', (modell,)):
                if ausschluss and (r['aufnahme'], r['sprecher']) == ausschluss:
                    continue
                kandidaten.setdefault(r['name'], []).append(json.loads(r['vektor']))
        scores = []
        for name, beispiele in kandidaten.items():
            # Mehrere Referenzen erhalten verschiedene Mikrofone/Situationen.
            werte = []
            for b in beispiele:
                try:
                    b = normalisiert(b)
                except (ValueError, TypeError):
                    continue
                if len(b) == len(v):
                    werte.append(sum(a*x for a,x in zip(v,b)))
            if werte:
                scores.append((max(werte), name))
        scores.sort(reverse=True)
        ergebnis = []
        for i, (score, name) in enumerate(scores[:limit]):
            abstand = score - scores[1][0] if len(scores) > 1 else score
            ergebnis.append({'name': name, 'aehnlichkeit': round(score, 4),
                'eindeutig': i == 0 and score >= 0.75 and abstand >= 0.08})
        return ergebnis

    def vorschlag(self, vektor, modell, ausschluss=None):
        kandidaten = self.vorschlaege(vektor, modell, ausschluss)
        return kandidaten[0] if kandidaten else None

    def textkorrekturen(self, aufnahme, transkript):
        with self.db() as db:
            return {r['segment']: dict(r) for r in db.execute(
                'SELECT segment, original, text FROM textkorrekturen WHERE aufnahme=? AND transkript=?',
                (aufnahme, transkript))}

    def text_speichern(self, aufnahme, transkript, segment, original, text):
        if not isinstance(text, str) or len(text) > 20000:
            raise ValueError('Text darf höchstens 20.000 Zeichen enthalten.')
        with self.db() as db:
            if text == original:
                db.execute('DELETE FROM textkorrekturen WHERE aufnahme=? AND transkript=? AND segment=?',
                           (aufnahme, transkript, segment))
            else:
                db.execute('''INSERT INTO textkorrekturen VALUES (?,?,?,?,?)
                    ON CONFLICT(aufnahme,transkript,segment) DO UPDATE SET
                    original=excluded.original, text=excluded.text''',
                    (aufnahme, transkript, segment, original, text))

    def texte_speichern(self, aufnahme, transkript, texte, erwartet):
        if any(not isinstance(text, str) or len(text) > 100000 for _, _, text in texte):
            raise ValueError('Text ist zu lang.')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            for segment, original, text in texte:
                alt = db.execute('SELECT original,text FROM textkorrekturen WHERE aufnahme=? AND transkript=? AND segment=?',
                                 (aufnahme, transkript, segment)).fetchone()
                aktuell = alt['text'] if alt and alt['original'] == original else original
                if aktuell != erwartet[segment]:
                    raise ValueError('Text wurde inzwischen geändert. Bitte die Aufnahme neu öffnen.')
                if text == original:
                    db.execute('DELETE FROM textkorrekturen WHERE aufnahme=? AND transkript=? AND segment=?',
                               (aufnahme, transkript, segment))
                else:
                    db.execute('INSERT OR REPLACE INTO textkorrekturen VALUES (?,?,?,?,?)',
                               (aufnahme, transkript, segment, original, text))

    def manuell_benennen(self, aufnahme, sprecher, modell, name):
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120:
            raise ValueError('Bitte einen Namen mit 1–120 Zeichen eingeben.')
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO manuelle_namen VALUES (?,?,?,?)',
                       (aufnahme,sprecher,modell,name.strip()))

    def aufteilungen(self, aufnahme, transkript, modell):
        with self.db() as db:
            return [dict(r) for r in db.execute('SELECT * FROM aufteilungen WHERE aufnahme=? AND transkript=? AND modell=? ORDER BY id',
                    (aufnahme,transkript,modell))]

    def lernsperren(self, aufnahme, modell):
        with self.db() as db:
            return {r[0] for r in db.execute('SELECT sprecher FROM lernsperren WHERE aufnahme=? AND modell=?',(aufnahme,modell))}

    def aufteilen(self, aufnahme, transkript, modell, basis, version, quelle=None,
                  ziel=None, start=None, ende=None, name=None, zurueck=False, quellen=None):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            aktuell = [r[0] for r in db.execute('SELECT id FROM aufteilungen WHERE aufnahme=? AND transkript=? AND modell=? ORDER BY id',
                                               (aufnahme,transkript,modell))]
            if version != aktuell:
                raise ValueError('Die Aufteilung wurde inzwischen geändert. Bitte die Aufnahme neu öffnen.')
            if zurueck:
                letzte = db.execute('SELECT id FROM aufteilungen WHERE aufnahme=? AND transkript=? AND modell=? AND basis=? ORDER BY id DESC LIMIT 1',
                                    (aufnahme,transkript,modell,basis)).fetchone()
                if not letzte:
                    raise ValueError('Keine Aufteilung zum Zurücknehmen vorhanden.')
                db.execute('DELETE FROM aufteilungen WHERE id=?',(letzte[0],))
                return
            if name is not None and (not isinstance(name,str) or not 1 <= len(name.strip()) <= 120):
                raise ValueError('Bitte einen Namen mit 1–120 Zeichen eingeben.')
            db.execute('INSERT INTO aufteilungen (aufnahme,transkript,modell,basis,quelle,ziel,start,ende) VALUES (?,?,?,?,?,?,?,?)',
                       (aufnahme,transkript,modell,basis,quelle,ziel,start,ende))
            for speaker in ({quelle, ziel} if quellen is None else set(quellen) | {ziel}):
                # Gemischte Stimmabdrücke nicht weiter als Lernreferenz verwenden.
                db.execute('INSERT OR IGNORE INTO manuelle_namen SELECT aufnahme,sprecher,modell,name FROM beispiele WHERE aufnahme=? AND sprecher=? AND modell=?',
                           (aufnahme,speaker,modell))
                db.execute('DELETE FROM beispiele WHERE aufnahme=? AND sprecher=? AND modell=?',(aufnahme,speaker,modell))
                db.execute('INSERT OR IGNORE INTO lernsperren VALUES (?,?,?)',(aufnahme,speaker,modell))
            if name is not None:
                db.execute('INSERT OR REPLACE INTO manuelle_namen VALUES (?,?,?,?)',(aufnahme,ziel,modell,name.strip()))
