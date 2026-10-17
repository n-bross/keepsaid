import io
import json
import os
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import test_aufteilung
from review_server import Handler
from zusammenfassung import Zusammenfassungen


class DashboardTest(unittest.TestCase):
    def setUp(self):
        test_aufteilung.AufteilungTest.setUp(self)

    def server(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.app = self.app
        server.base_path = '/review'
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f'http://127.0.0.1:{server.server_port}/review'

    def test_dashboard_zaehlt_und_zeigt_offene_aufnahmen(self):
        result = self.app.dashboard()
        self.assertEqual(result['aufnahmen_gesamt'], 1)
        self.assertEqual(result['personen_gesamt'], 1)
        self.assertEqual(result['zuordnungen_bestaetigt'], 1)
        self.assertEqual(result['stimmen_gesamt'], 2)
        self.assertEqual(result['offene_aufnahmen'][0]['offen'], 1)
        self.stimmen.manuell_benennen('test', 's2', 'm', 'Ben')
        result = self.app.dashboard()
        self.assertEqual(result['offene_aufnahmen'], [])
        self.assertEqual(result['zuordnungen_bestaetigt'], 2)
        with urllib.request.urlopen(self.server() + '/api/dashboard') as response:
            self.assertEqual(json.load(response)['aufnahmen_gesamt'], 1)

    def test_audio_range_und_sprung_ohne_komplettdownload(self):
        daten = b'0123456789' * 1000
        (self.root / 'audio/test.wav').write_bytes(daten)
        url = self.server() + '/api/audio?name=test'
        for value, expected in [('bytes=100-199', daten[100:200]), ('bytes=-50', daten[-50:]),
                                ('bytes=9990-', daten[9990:])]:
            with self.subTest(value=value):
                req = urllib.request.Request(url, headers={'Range': value})
                with urllib.request.urlopen(req) as response:
                    self.assertEqual(response.status, 206)
                    self.assertEqual(response.read(), expected)
                    self.assertEqual(response.headers['Accept-Ranges'], 'bytes')
                    self.assertEqual(int(response.headers['Content-Length']), len(expected))
        with urllib.request.urlopen(url) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), daten)
        for value in ['bytes=10000-', 'bytes=5-2', 'bytes=0-1,4-5', 'bytes=-0', 'bytes=-']:
            with self.subTest(value=value), self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(urllib.request.Request(url, headers={'Range': value}))
            self.assertEqual(error.exception.code, 416)
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(url.replace('name=test', 'name=../secret'))
        self.assertEqual(error.exception.code, 400)

    def test_zusammenfassung_cache_korrekturen_und_schutz(self):
        base = self.server()
        def post(token=True):
            headers = {'Content-Type': 'application/json'}
            if token:
                headers['X-Review-Token'] = self.app.token
            req = urllib.request.Request(base+'/api/zusammenfassung', json.dumps({'aufnahme': 'test'}).encode(), headers)
            return urllib.request.urlopen(req)
        with self.assertRaises(urllib.error.HTTPError) as error:
            post(token=False)
        self.assertEqual(error.exception.code, 403)
        with patch.dict(os.environ, {'GROQ_API_KEY': 'test-key'}), \
             patch.object(Zusammenfassungen, 'anfragen', return_value='Die Personen besprechen das Projekt.') as llm:
            with post() as response:
                result = json.load(response)
            self.assertIn('Projekt', result['zusammenfassung']['text'])
            with post() as response:
                self.assertFalse(json.load(response)['zusammenfassung']['veraltet'])
            self.assertEqual(llm.call_count, 1)
            self.assertIn('Zwei Personen sprechen.', llm.call_args.args[0])
            self.stimmen.text_speichern('test', 'test.gruendlich.json', 0,
                                        'Zwei Personen sprechen.', 'Korrigierter Gesprächsinhalt.')
            with urllib.request.urlopen(base+'/api/zusammenfassung?name=test') as response:
                self.assertTrue(json.load(response)['zusammenfassung']['veraltet'])
            with post() as response:
                self.assertFalse(json.load(response)['zusammenfassung']['veraltet'])
            self.assertEqual(llm.call_count, 2)
            self.assertIn('Korrigierter Gesprächsinhalt.', llm.call_args.args[0])
            restarted = Zusammenfassungen(self.app.zusammenfassungen.pfad)
            text = self.app.zusammenfassung_text(self.app.detail('test'))
            self.assertEqual(restarted.lesen('test', text)['text'], 'Die Personen besprechen das Projekt.')

    def test_lange_zusammenfassung_verarbeitet_alle_teile(self):
        text = 'A' * 16000 + 'B' * 16000 + 'Das Ende'
        with patch.dict(os.environ, {'GROQ_API_KEY': 'test-key'}), \
             patch.object(Zusammenfassungen, 'anfragen', side_effect=['Teil A', 'Teil B', 'Das Ende', 'Gesamt']) as llm:
            result = self.app.zusammenfassungen.erstellen('lang', text)
        self.assertEqual(result['text'], 'Gesamt')
        self.assertEqual(llm.call_args_list[2].args[0], 'Das Ende')
        self.assertEqual(llm.call_args_list[-1].args[0], 'Teil A\n\nTeil B\n\nDas Ende')

    def test_groq_transport_und_leere_antwort(self):
        antwort = io.BytesIO(json.dumps({'choices': [{'message': {'content': 'Eine Zusammenfassung.'}}]}).encode())
        with patch('zusammenfassung.urllib.request.urlopen', return_value=antwort) as transport:
            self.assertEqual(Zusammenfassungen.anfragen('Inhalt', 'test-key'), 'Eine Zusammenfassung.')
            request = transport.call_args.args[0]
            body = json.loads(request.data)
            self.assertIn('Inhalt', body['messages'][1]['content'])
        antwort = io.BytesIO(b'{"choices":[{"message":{"content":""}}]}')
        with patch('zusammenfassung.urllib.request.urlopen', return_value=antwort), self.assertRaises(ValueError):
            Zusammenfassungen.anfragen('Inhalt', 'test-key')


if __name__ == '__main__':
    unittest.main()
