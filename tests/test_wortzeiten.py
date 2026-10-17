import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import wave
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import test_aufteilung
from review_server import Handler
from wortzeiten import Wortzeiten, gueltige_woerter, neue_segmente, zuordnen


class WortzeitenTest(unittest.TestCase):
    def setUp(self):
        test_aufteilung.AufteilungTest.setUp(self)

    def test_echte_zeiten_statt_gleichmaessiger_verteilung(self):
        segmente = [{'text': ' Hallo, liebe Welt!'}, {'text': 'Nicht gesprochen.'}]
        worte = [{'word': 'Hallo', 'start': 42.1, 'end': 42.4},
                 {'word': 'Welt', 'start': 45.7, 'end': 46.2}]
        result = zuordnen(segmente, worte)
        self.assertEqual([w['word'] for w in result[0]], ['Hallo,', 'Welt!'])
        self.assertEqual([w['start'] for w in result[0]], [42.1, 45.7])
        for wort in result[0]:
            self.assertEqual(segmente[0]['text'][wort['char_start']:wort['char_end']], wort['word'])
        self.assertEqual(result[1], [])

    def test_wiederholte_einzelwoerter_werden_nicht_geraten(self):
        result = zuordnen([{'text': 'ja falsch ja'}], [{'word': 'ja', 'start': 4, 'end': 5}])
        self.assertEqual(result, [[]])

    def test_ungueltige_zeiten_und_export(self):
        words = [{'word': 'Hallo', 'start': 1, 'end': 1.5}, {'word': 'Welt', 'start': 2, 'end': 3},
                 {'word': 'falsch', 'start': -1, 'end': 1}, {'word': 'nan', 'start': float('nan'), 'end': 5},
                 {'word': 'falsch', 'start': 2, 'end': 2}, {'word': 'zu spät', 'start': 8, 'end': 9}]
        self.assertEqual(len(gueltige_woerter(words, 5)), 2)
        result = neue_segmente([{'start': 0, 'end': 5, 'text': 'Hallo Welt'}], words[:2])
        self.assertEqual(result[0]['words'], words[:2])
        self.assertEqual(neue_segmente(result)[0]['words'], words[:2])

    def test_detail_nutzt_wortzeiten_und_erhaelt_original_und_korrekturen(self):
        path = self.root / 'audio/test.wav'
        basis = self.app.wortzeiten.basis(path)
        daten = {'duration': 20, 'words': [{'word': 'Zwei', 'start': 4, 'end': 4.4},
                 {'word': 'Personen', 'start': 4.8, 'end': 5.2}, {'word': 'sprechen', 'start': 5.5, 'end': 6}]}
        with __import__('sqlite3').connect(self.app.wortzeiten.pfad) as db:
            db.execute('INSERT INTO wortzeiten VALUES (?,?,?)', ('test', basis, json.dumps(daten)))
        d = self.app.detail('test')
        self.assertEqual(d['transkript'][0]['playback_start'], 4)
        self.assertEqual(d['transkript'][0]['start'], 0)
        self.assertEqual(len(d['transkript'][0]['words']), 3)
        self.stimmen.text_speichern('test', 'test.gruendlich.json', 0, 'Zwei Personen sprechen.', 'Zwei neue Personen sprechen.')
        d = self.app.detail('test')
        self.assertEqual(len(d['transkript'][0]['words']), 3)
        self.assertNotIn('neue', [w['word'] for w in d['transkript'][0]['words']])
        self.assertEqual(d['transkript'][0]['text'], 'Zwei neue Personen sprechen.')
        self.assertEqual(json.loads((self.root/'transkripte/test.gruendlich.json').read_text()), self.original)
        # Geänderte Audiodateien dürfen keine alten Wortzeiten erhalten.
        path.write_bytes(b'andere Audiodatei')
        self.assertEqual(self.app.detail('test')['transkript'][0]['words'], [])

    def test_hintergrundjob_deduplizierung_http_und_neustart(self):
        path = self.root/'audio/test.wav'
        with wave.open(str(path), 'wb') as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b'\0\0'*32000)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.app = self.app
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        self.addCleanup(lambda: self.app.wortzeiten.pool.shutdown(wait=True))
        base = f'http://127.0.0.1:{server.server_port}'
        req = urllib.request.Request(base+'/api/wortzeiten', json.dumps({'aufnahme': 'test'}).encode(),
                                     {'Content-Type': 'application/json'})
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(req)
        self.assertEqual(error.exception.code, 403)
        req.add_header('X-Review-Token', self.app.token)
        release = threading.Event()
        self.addCleanup(release.set)
        def transkribieren(audio):
            release.wait(timeout=5)
            return {'words': [{'word': 'Hallo', 'start': .3, 'end': .8}]}
        with patch.dict(os.environ, {'GROQ_API_KEY': 'test-key'}), \
             patch.object(Wortzeiten, 'transkribieren', side_effect=transkribieren) as request:
            with urllib.request.urlopen(req) as r:
                self.assertEqual(r.status, 202)
            with urllib.request.urlopen(req) as r:
                self.assertIn(json.load(r)['status'], ['wartet', 'laeuft'])
            release.set()
            self.app.wortzeiten.pool.shutdown(wait=True)
            self.assertEqual(request.call_count, 1)
        with urllib.request.urlopen(base+'/api/wortzeiten?name=test') as r:
            self.assertEqual(json.load(r)['status'], 'bereit')
        restarted = Wortzeiten(self.app.wortzeiten.pfad)
        self.addCleanup(lambda: restarted.pool.shutdown(wait=True))
        self.assertEqual(restarted.lesen('test', restarted.basis(path))['words'][0]['start'], .3)

    def test_lange_aufnahmen_behalten_absolute_wortzeiten(self):
        import types
        service = self.app.wortzeiten
        path = self.root/'audio/test.wav'
        basis = service.basis(path)
        with patch('wortzeiten.subprocess.check_output', return_value=b'{"format":{"duration":500}}'), \
             patch('wortzeiten.subprocess.run', return_value=types.SimpleNamespace(stdout=b'RIFF')) as ffmpeg, \
             patch.object(Wortzeiten, 'transkribieren', return_value={'words': [
                 {'word': 'Wort', 'start': 10, 'end': 11}]}):
            service.verarbeiten('test', str(path), basis)
        self.assertEqual([w['start'] for w in service.lesen('test', basis)['words']], [10, 249, 489])
        self.assertEqual([call.args[0][4] for call in ffmpeg.call_args_list], ['0', '239', '479'])

    def test_multipart_fordert_wortzeitstempel_an(self):
        import io
        response = io.BytesIO(b'{"words":[{"word":"Hallo","start":0.1,"end":0.6}]}')
        with patch.dict(os.environ, {'GROQ_API_KEY': 'test-key'}), \
             patch('wortzeiten.urllib.request.urlopen', return_value=response) as request:
            self.assertEqual(Wortzeiten.transkribieren(b'RIFFtest')['words'][0]['word'], 'Hallo')
            body = request.call_args.args[0].data
            self.assertIn(b'timestamp_granularities[]', body)
            self.assertIn(b'word', body)
            self.assertIn(b'RIFFtest', body)


if __name__ == '__main__':
    unittest.main()
