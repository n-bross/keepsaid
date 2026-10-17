import json
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
import wave
from pathlib import Path
from http.server import ThreadingHTTPServer

from stimmen import Stimmen
from review_server import Anwendung, Handler, Quelle


class StimmenTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.stimmen = Stimmen(self.root/'test.sqlite3', self.root/'leer.json')

    def test_lernen_korrigieren_zuruecknehmen_und_neustart(self):
        s = self.stimmen
        self.assertIsNone(s.vorschlag([1,0], 'modell'))
        s.bestaetigen('a', 'speaker1', 'modell', ' Anna ', [1,0])
        self.assertEqual(s.vorschlag([1,0], 'modell')['name'], 'Anna')
        s.bestaetigen('a', 'speaker1', 'modell', 'Ben', [1,0])
        self.assertEqual(s.namen(), ['Ben'])
        self.assertEqual(Stimmen(s.pfad, s.referenzen).reviews('a','modell'), {'speaker1':'Ben'})
        self.assertIsNone(s.vorschlag([1,0], 'modell', ('a','speaker1')))
        s.entfernen('a', 'speaker1', 'modell')
        self.assertIsNone(s.vorschlag([1,0], 'modell'))

    def test_modell_und_dimensionen_bleiben_getrennt(self):
        s = self.stimmen
        s.bestaetigen('a','s','eins','Anna',[1,0])
        self.assertIsNone(s.vorschlag([1,0], 'zwei'))
        self.assertIsNone(s.vorschlag([1,0,0], 'eins'))

    def test_unsichere_treffer(self):
        s = self.stimmen
        s.bestaetigen('a','s','m','Anna',[1,0])
        s.bestaetigen('b','s','m','Ben',[.99,.1])
        self.assertFalse(s.vorschlag([1,0], 'm')['eindeutig'])

    def test_rangliste_und_textkorrekturen(self):
        s = self.stimmen
        s.bestaetigen('a','s','m','Anna',[1,0])
        s.bestaetigen('b','s','m','Ben',[.8,.6])
        s.bestaetigen('c','s','anders','Falsch',[1,0])
        self.assertEqual([v['name'] for v in s.vorschlaege([1,0],'m')], ['Anna','Ben'])
        self.assertTrue(s.vorschlaege([1,0],'m')[0]['eindeutig'])
        s.text_speichern('a','a.json',0,'Falsch','Richtig')
        neu = Stimmen(s.pfad, s.referenzen)
        self.assertEqual(neu.textkorrekturen('a','a.json')[0]['text'],'Richtig')
        self.assertEqual(neu.textkorrekturen('a','b.json'),{})
        neu.text_speichern('a','a.json',0,'Falsch','Falsch')
        self.assertEqual(neu.textkorrekturen('a','a.json'),{})

    def test_ungueltige_eingaben(self):
        for name, v in [('',[1,0]),('A',[0,0]),('A',[float('nan'),1]),('A',[])]:
            with self.assertRaises(ValueError):
                self.stimmen.bestaetigen('a','s','m',name,v)

    def test_alte_referenzen_brauchen_modellangabe(self):
        self.stimmen.referenzen.write_text(json.dumps({'Alt': {'vektor':[1,0]}}))
        self.assertEqual(self.stimmen.namen(), ['Alt'])
        self.assertIsNone(self.stimmen.vorschlag([1,0], 'm'))
        self.stimmen.referenzen.write_text(json.dumps({'Alt': {'vektor':[1,0], 'modell':'m'}}))
        self.assertTrue(self.stimmen.vorschlag([1,0], 'm')['eindeutig'])

    def test_worker_laesst_server_snapshot_und_offline_cache_gelten(self):
        import io
        import urllib.error
        from unittest.mock import patch
        import worker_mac as worker
        self.stimmen.bestaetigen('a','s','m','Anna',[1,0])
        with self.stimmen.db() as db:
            snapshot = db.serialize()
        with patch.object(worker,'BASIS',self.root), patch('worker_mac.urllib.request.urlopen',return_value=io.BytesIO(snapshot)):
            bibliothek = worker.stimmenbibliothek()
            self.assertEqual(bibliothek.reviews('a','m'),{'s':'Anna'})
        # Entfernte Server-Bestätigungen dürfen nicht im Mac-Cache weiterlernen.
        self.stimmen.entfernen('a','s','m')
        with self.stimmen.db() as db:
            snapshot = db.serialize()
        with patch.object(worker,'BASIS',self.root), patch('worker_mac.urllib.request.urlopen',return_value=io.BytesIO(snapshot)):
            self.assertEqual(worker.stimmenbibliothek().reviews('a','m'),{})
        with patch.object(worker,'BASIS',self.root), patch('worker_mac.urllib.request.urlopen',side_effect=urllib.error.URLError('offline')):
            self.assertEqual(worker.stimmenbibliothek().reviews('a','m'),{})

    def test_worker_exportiert_namensvorschlaege(self):
        import sys
        import types
        from unittest.mock import patch
        import numpy as np
        import worker_mac as worker
        self.stimmen.bestaetigen('referenz','speaker1',worker.EMB_MODELL,'Anna',[1,0])
        hochgeladen = {}
        def upload(pfad, ordner):
            hochgeladen[pfad.name] = pfad.read_text()
        profile = {'speaker1': {'vektor':[1,0], 'sekunden_genutzt':2,
                   'sekunden_gesamt':2, 'segmente':1, 'name':None}}
        fake_mlx = types.SimpleNamespace(transcribe=lambda *a, **k: {
            'segments':[{'start':0,'end':2,'text':'Hallo'}]})
        with patch.dict(sys.modules, {'mlx_whisper': fake_mlx}), \
             patch.object(worker, 'WORK', self.root), \
             patch.object(worker, 'holen'), \
             patch.object(worker, 'erstelle_titel', return_value='Testgespräch über die Oberfläche'), \
             patch.object(worker, 'nach_wav', return_value=np.zeros(32000)), \
             patch.object(worker, 'modelle_sicherstellen', return_value=(None,None)), \
             patch.object(worker, 'diarisieren', return_value=[{'start':0,'end':2,'speaker':'speaker1'}]), \
             patch.object(worker, 'stimmabdruecke', return_value=profile), \
             patch.object(worker, 'stimmenbibliothek', return_value=self.stimmen), \
             patch.object(worker, 'hochladen', side_effect=upload):
            worker.verarbeite('neu','.wav','schnell',True)
        text = json.loads(hochgeladen['neu.schnell.json'])
        self.assertEqual(text['segments'][0]['speaker_name'],'Anna')
        self.assertEqual(text['segments'][0]['speaker'],'speaker1')
        self.assertIn('## Anna',hochgeladen['neu.schnell.md'])
        profil = json.loads(hochgeladen['neu.stimmen.json'])['sprecher']['speaker1']
        self.assertFalse(profil['name_bestaetigt'])
        self.assertEqual(self.stimmen.reviews('neu',worker.EMB_MODELL), {})

    def test_audio_nur_benoetigte_bereiche_laden(self):
        import io
        import struct
        from http.server import BaseHTTPRequestHandler
        audio = io.BytesIO()
        with wave.open(audio,'wb') as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(b'\0\0'*16000*110 + struct.pack('<h',10000)*16000*10)
        daten = audio.getvalue()
        bereiche = []
        class RangeHandler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def log_message(self, *args):
                pass
            def do_GET(self):
                bereich = self.headers.get('Range')
                if not bereich:
                    self.send_error(400, 'Range erforderlich')
                    return
                start, ende = bereich.removeprefix('bytes=').split('-')
                start = int(start)
                ende = min(int(ende) if ende else len(daten)-1, len(daten)-1)
                bereiche.append((start,ende))
                self.send_response(206)
                self.send_header('Content-Type','audio/wav')
                self.send_header('Accept-Ranges','bytes')
                self.send_header('Content-Range',f'bytes {start}-{ende}/{len(daten)}')
                self.send_header('Content-Length',str(ende-start+1))
                self.end_headers()
                try:
                    self.wfile.write(daten[start:ende+1])
                except (BrokenPipeError, ConnectionResetError):
                    pass
        server = ThreadingHTTPServer(('127.0.0.1',0),RangeHandler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        app = Anwendung(Quelle(f'http://127.0.0.1:{server.server_port}'),self.stimmen)
        spaeter = app.clip('range-test.wav',110.5,1)
        with wave.open(io.BytesIO(spaeter),'rb') as w:
            samples = struct.unpack('<'+'h'*16000,w.readframes(16000))
            self.assertGreater(sum(abs(x) for x in samples)/len(samples),9000)
        self.assertTrue(any(start > 3000000 for start,ende in bereiche))
        self.assertLess(sum(ende-start+1 for start,ende in bereiche),len(daten)//2)
        vorher = len(bereiche)
        self.assertEqual(app.clip('range-test.wav',110.5,1),spaeter)
        self.assertEqual(len(bereiche),vorher)

    def test_kein_komplettdownload_ohne_range_support(self):
        from unittest.mock import MagicMock,patch
        antwort = MagicMock()
        antwort.__enter__.return_value = antwort
        antwort.status = 200
        app = Anwendung(Quelle('https://test.invalid'),self.stimmen)
        with patch('review_server.urllib.request.urlopen',return_value=antwort):
            with self.assertRaisesRegex(ValueError,'Teilabrufe'):
                app.clip('nicht-lokal.wav',0,10)
        antwort.read.assert_not_called()

    def test_http_review_clip_und_schutz(self):
        for d in ['audio','transkripte','stimmprofile']:
            (self.root/d).mkdir()
        (self.root/'stimmprofile/test.stimmen.json').write_text(json.dumps({
            'name':'test','modell':'m','sprecher':{'speaker1':{'vektor':[1,0],'sekunden_gesamt':2}}}))
        (self.root/'transkripte/test.gruendlich.json').write_text(json.dumps({
            'segments':[{'start':0,'end':2,'speaker':'speaker1','text':'Hallo'}],
            'sprecher_segmente':[{'start':0,'end':2,'speaker':'speaker1'}]}))
        with wave.open(str(self.root/'audio/test.wav'),'wb') as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b'\0\0'*32000)
        server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
        server.app = Anwendung(Quelle('', self.root), self.stimmen)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f'http://127.0.0.1:{server.server_port}'
        def get(path):
            with urllib.request.urlopen(base+path) as r: return r.read()
        server.base_path = '/review'
        self.assertIn(b'<base href="/review/">', get('/review/'))
        self.assertTrue(get('/review/api/bibliothek').startswith(b'SQLite format 3\0'))
        self.assertEqual(len(json.loads(get('/review/api/aufnahmen'))['aufnahmen']),1)
        index = json.loads(get('/api/aufnahmen'))
        self.assertEqual(len(index['aufnahmen']),1)
        # Metadaten liefern den Inhaltstitel und das tatsächliche Aufnahmedatum.
        (self.root/'audio/test.json').write_text(json.dumps({
            'title':'Gespräch über Software-Support', 'recorded_at':'2026-10-15T09:30:18'}))
        index = json.loads(get('/api/aufnahmen?neu=1'))
        self.assertEqual(index['aufnahmen'][0]['titel'], 'Gespräch über Software-Support')
        self.assertEqual(index['aufnahmen'][0]['aufgenommen'], '2026-10-15T09:30:18')
        # Zeitstempel sind kein Inhaltstitel; lokal erzeugte Titel ergänzen sie.
        (self.root/'audio/test.json').write_text(json.dumps({'title':'2026-10-15 09:30:18'}))
        server.app.titel.speichern('test','Rückfragen zur ausgeschriebenen Stelle')
        index = json.loads(get('/api/aufnahmen?neu=1'))
        self.assertEqual(index['aufnahmen'][0]['titel'], 'Rückfragen zur ausgeschriebenen Stelle')
        data = {'aufnahme':'test','sprecher':'speaker1','name':'Anna'}
        req = urllib.request.Request(base+'/api/review', data=json.dumps(data).encode(),
                                     headers={'Content-Type':'application/json'})
        with self.assertRaises(urllib.error.HTTPError) as e: urllib.request.urlopen(req)
        self.assertEqual(e.exception.code,403)
        req.add_header('X-Review-Token',index['token'])
        with urllib.request.urlopen(req) as r: result=json.load(r)
        self.assertEqual(result['sprecher'][0]['name'],'Anna')
        clip = get('/api/clip?name=test&sprecher=speaker1')
        self.assertEqual(clip[:4], b'RIFF')
        self.assertGreater(len(clip),64000)
        # Fertige Clips werden wiederverwendet, ohne erneut ffmpeg zu starten.
        from unittest.mock import patch
        with patch('review_server.subprocess.run', side_effect=AssertionError('Clip nicht gecacht')):
            self.assertEqual(get('/api/clip?name=test&sprecher=speaker1'), clip)
        text_req = urllib.request.Request(base+'/api/text',
            data=json.dumps({'aufnahme':'test','segment':0,'vorher':'Hallo','text':'Hallo Welt!'}).encode(),
            headers={'Content-Type':'application/json','X-Review-Token':index['token']})
        with urllib.request.urlopen(text_req) as r: result=json.load(r)
        self.assertEqual(result['transkript'][0]['text'],'Hallo Welt!')
        self.assertTrue(result['transkript'][0]['korrigiert'])
        self.assertEqual(result['sprecher'][0]['text'][0]['text'],'Hallo Welt!')
        self.assertEqual(json.loads((self.root/'transkripte/test.gruendlich.json').read_text())['segments'][0]['text'],'Hallo')
        with self.assertRaises(urllib.error.HTTPError) as e: urllib.request.urlopen(text_req)
        self.assertEqual(e.exception.code,409)
        # Geänderte Originaltranskripte erhalten keine veralteten Korrekturen.
        original = self.root/'transkripte/test.gruendlich.json'
        original.write_text(original.read_text().replace('Hallo','Neuer Text'))
        server.app.quelle.json.cache_clear()
        self.assertEqual(server.app.detail('test')['transkript'][0]['text'],'Neuer Text')
        data['aktion']='entfernen'
        req.data=json.dumps(data).encode()
        with urllib.request.urlopen(req) as r: result=json.load(r)
        self.assertIsNone(result['sprecher'][0]['name'])
        with self.assertRaises(ValueError): server.app.quelle.lesen('audio','../test.wav')


if __name__ == '__main__':
    unittest.main()
