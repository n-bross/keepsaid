import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from review_server import Anwendung, Handler, Quelle
from stimmen import Stimmen


class AufteilungTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        for folder in ['audio', 'transkripte', 'stimmprofile']:
            (self.root / folder).mkdir()
        self.original = {'segments': [{'start': 0, 'end': 20, 'speaker': 's1', 'text': 'Zwei Personen sprechen.'}],
                         'sprecher_segmente': [{'start': 0, 'end': 10, 'speaker': 's1'},
                                              {'start': 10, 'end': 12, 'speaker': 's2'},
                                              {'start': 12, 'end': 20, 'speaker': 's1'}]}
        (self.root / 'audio/test.wav').touch()
        (self.root / 'transkripte/test.gruendlich.json').write_text(json.dumps(self.original))
        (self.root / 'stimmprofile/test.stimmen.json').write_text(json.dumps({
            'modell': 'm', 'sprecher': {'s1': {'vektor': [1, 0]}, 's2': {'vektor': [0, 1]}}}))
        self.stimmen = Stimmen(self.root / 'stimmen.sqlite3', self.root / 'leer.json')
        self.stimmen.bestaetigen('test', 's1', 'm', 'Anna', [1, 0])
        self.app = Anwendung(Quelle('', self.root), self.stimmen)

    def split(self, **overrides):
        data = {'aufnahme': 'test', 'quelle': 's1', 'ziel': 'neu', 'name': 'Ben',
                'start': 4.25, 'ende': 15.5,
                'version': self.app.detail('test')['aufteilung_version']}
        data.update(overrides)
        return self.app.aufteilen(data)

    def test_split_persistenz_text_und_lernsperre(self):
        self.stimmen.text_speichern('test', 'test.gruendlich.json', 0,
                                    'Zwei Personen sprechen.', 'Korrigierter Text.')
        result = self.split()
        new = next(s for s in result['sprecher'] if s['name'] == 'Ben')
        self.assertEqual([(b['start'], b['end']) for b in result['bereiche'] if b['speaker'] == new['id']],
                         [(4.25, 10), (12, 15.5)])
        self.assertEqual(next(b for b in result['bereiche'] if b['speaker'] == 's2'),
                         {'start': 10, 'end': 12, 'speaker': 's2'})
        self.assertEqual(result['transkript'][0]['text'], 'Korrigierter Text.')
        self.assertEqual(len(result['transkript'][0]['bereiche']), 5)
        self.assertTrue(all(not s['lernbar'] for s in result['sprecher'] if s['id'] != 's2'))
        self.assertIsNone(self.stimmen.vorschlag([1, 0], 'm'))
        with self.assertRaisesRegex(ValueError, 'manuell'):
            self.stimmen.bestaetigen('test', 's1', 'm', 'Anna', [1, 0])
        restarted = Anwendung(Quelle('', self.root), Stimmen(self.stimmen.pfad, self.stimmen.referenzen))
        self.assertEqual(restarted.detail('test')['bereiche'], result['bereiche'])
        self.assertEqual(json.loads((self.root / 'transkripte/test.gruendlich.json').read_text()), self.original)
        self.app.aufteilen({'aufnahme': 'test', 'aktion': 'zurueck', 'version': result['aufteilung_version']})
        self.assertEqual(self.app.detail('test')['bereiche'], self.original['sprecher_segmente'])
        # Rücknahme darf einen als gemischt erkannten Vektor nicht wieder lernen.
        self.assertIsNone(self.stimmen.vorschlag([1, 0], 'm'))

    def test_vorhandener_sprecher_und_konflikt(self):
        first = self.split(ziel='s2')
        self.assertEqual(len(first['sprecher']), 2)
        self.assertAlmostEqual(sum(b['end'] - b['start'] for b in first['bereiche']), 20)
        with self.assertRaisesRegex(ValueError, 'inzwischen'):
            self.split(version=[], start=0, ende=1)
        self.assertEqual(self.app.detail('test')['bereiche'], first['bereiche'])

    def test_ungueltige_bereiche_ohne_schreiben(self):
        for overrides in [{'start': -1}, {'ende': 21}, {'start': 8, 'ende': 8},
                          {'start': 'nan'}, {'ende': 'inf'}, {'start': True},
                          {'quelle': 'fehlt'}, {'ziel': 's1'}, {'name': ' '},
                          {'name': 'x' * 121}, {'start': 10, 'ende': 12}]:
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                self.split(**overrides)
        self.assertEqual(self.app.detail('test')['aufteilungen'], [])
        self.assertIsNotNone(self.stimmen.vorschlag([1, 0], 'm'))

    def test_geaendertes_original(self):
        result = self.split()
        self.original['sprecher_segmente'][0]['end'] = 9
        (self.root / 'transkripte/test.gruendlich.json').write_text(json.dumps(self.original))
        self.app.quelle.json.cache_clear()
        changed = self.app.detail('test')
        self.assertEqual(changed['aufteilung_veraltet'], 1)
        self.assertEqual(changed['aufteilungen'], [])
        self.assertNotEqual(changed['aufteilung_basis'], result['aufteilung_basis'])

    def test_http_aufteilen_benennen_und_zuruecknehmen(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.app = self.app
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f'http://127.0.0.1:{server.server_port}'

        def post(path, data, token=True):
            headers = {'Content-Type': 'application/json'}
            if token:
                headers['X-Review-Token'] = self.app.token
            req = urllib.request.Request(base + path, json.dumps(data).encode(), headers)
            with urllib.request.urlopen(req) as response:
                return json.load(response)

        data = {'aufnahme': 'test', 'quelle': 's1', 'ziel': 'neu', 'name': 'Ben',
                'start': 4.25, 'ende': 8.75, 'version': []}
        with self.assertRaises(urllib.error.HTTPError) as error:
            post('/api/aufteilung', data, token=False)
        self.assertEqual(error.exception.code, 403)
        result = post('/api/aufteilung', data)
        new = next(s for s in result['sprecher'] if s['name'] == 'Ben')
        renamed = post('/api/review', {'aufnahme': 'test', 'sprecher': new['id'], 'name': 'Benedikt'})
        self.assertEqual(next(s for s in renamed['sprecher'] if s['id'] == new['id'])['name'], 'Benedikt')
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(base + '/api/ausschnitt?name=test&start=5&ende=5')
        self.assertEqual(error.exception.code, 400)
        undone = post('/api/aufteilung', {'aufnahme': 'test', 'aktion': 'zurueck',
                                        'version': result['aufteilung_version']})
        self.assertEqual(undone['bereiche'], self.original['sprecher_segmente'])

    def test_nicht_zugeordnete_zeiten_lassen_sich_bearbeiten(self):
        from aufteilung import mit_unbekannten_bereichen
        text = {'segments': [{'start': 0, 'end': 8, 'text': 'Hallo'},
                             {'start': 7, 'end': 12, 'text': 'Welt'}]}
        bereiche = [{'start': 4, 'end': 6, 'speaker': 's1'}]
        result = mit_unbekannten_bereichen(bereiche, text)
        self.assertEqual([(r['start'], r['end']) for r in result if r['speaker'] == '__unzugeordnet__'],
                         [(0, 4), (6, 12)])
        ueberlappt = mit_unbekannten_bereichen([
            {'start': 2, 'end': 8, 'speaker': 's1'}, {'start': 4, 'end': 6, 'speaker': 's2'},
            {'start': 8, 'end': 10, 'speaker': 's1'}, {'start': 15, 'end': 18, 'speaker': 's2'}],
            {'segments': [{'start': 0, 'end': 20, 'text': 'Alles'}, {'start': 5, 'end': 16, 'text': 'Teil'}]})
        self.assertEqual([(b['start'], b['end']) for b in ueberlappt if b['speaker'] == '__unzugeordnet__'],
                         [(0, 2), (10, 15), (18, 20)])
        self.original['sprecher_segmente'] = [{'start': 4, 'end': 6, 'speaker': 's1'}]
        (self.root / 'transkripte/test.gruendlich.json').write_text(json.dumps(self.original))
        self.app.quelle.json.cache_clear()
        detail = self.app.detail('test')
        unknown = next(s for s in detail['sprecher'] if s['id'] == '__unzugeordnet__')
        self.assertFalse(unknown['lernbar'])
        result = self.split(quelle='__unzugeordnet__', start=0, ende=2)
        self.assertTrue(any(r['start'] == 0 and r['end'] == 2 and r['speaker'].startswith('manuell_')
                            for r in result['bereiche']))

    def test_mehrfach_aufteilen(self):
        first = self.split(start=2, ende=8)
        new = next(s for s in first['sprecher'] if s['name'] == 'Ben')
        second = self.split(quelle=new['id'], start=4, ende=6, name='Clara')
        self.assertEqual(len(second['sprecher']), 4)
        self.app.aufteilen({'aufnahme': 'test', 'aktion': 'zurueck', 'version': second['aufteilung_version']})
        self.assertEqual(self.app.detail('test')['bereiche'], first['bereiche'])


if __name__ == '__main__':
    unittest.main()
