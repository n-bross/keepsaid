import json
import threading
import unittest
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer

import test_aufteilung
from review_server import Handler


class TextblockHTTPTest(unittest.TestCase):
    def setUp(self):
        test_aufteilung.AufteilungTest.setUp(self)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.server.app=self.app
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)
        self.base=f'http://127.0.0.1:{self.server.server_port}'

    def post(self,path,data):
        req=urllib.request.Request(self.base+path,json.dumps(data).encode(),{'Content-Type':'application/json','X-Review-Token':self.app.token})
        with urllib.request.urlopen(req) as r:return json.load(r)

    def test_delete_entire_block_undo_and_conflict(self):
        block=self.app.detail('test')['bloecke'][0]
        body={'aufnahme':'test','block':block['id'],'version':block['version'],'vorher':block['text'],'text':''}
        deleted=self.post('/api/textblock',body)
        self.assertEqual(deleted['bloecke'],[])
        with self.assertRaises(urllib.error.HTTPError) as error:self.post('/api/textblock',body)
        self.assertEqual(error.exception.code,409)
        undo={'aufnahme':'test','token':deleted['text_undo']}
        restored=self.post('/api/text-zurueck',undo)
        self.assertEqual(restored['bloecke'][0]['text'],block['text'])
        deleted=self.post('/api/textblock',body)
        self.stimmen.text_speichern('test','test.gruendlich.json',0,self.original['segments'][0]['text'],'Anders bearbeitet.')
        with self.assertRaises(urllib.error.HTTPError):self.post('/api/text-zurueck',{'aufnahme':'test','token':deleted['text_undo']})
        self.assertEqual(self.app.detail('test')['transkript'][0]['text'],'Anders bearbeitet.')
        self.assertEqual(json.loads((self.root/'transkripte/test.gruendlich.json').read_text()),self.original)

    def test_time_range_covers_multiple_old_speakers_atomically(self):
        d=self.app.detail('test')
        split=self.post('/api/aufteilung',{'aufnahme':'test','quelle':'__bereich__','ziel':'neu','name':'Ben','start':4,'ende':15,'version':d['aufteilung_version']})
        new=next(sp['id'] for sp in split['sprecher'] if sp['name']=='Ben')
        selected=[b for b in split['bereiche'] if b['start']<15 and b['end']>4]
        self.assertTrue(all(b['speaker']==new for b in selected))
        self.assertTrue(all(not sp['lernbar'] for sp in split['sprecher']))
        undone=self.post('/api/aufteilung',{'aufnahme':'test','aktion':'zurueck','version':split['aufteilung_version']})
        self.assertEqual(undone['bereiche'],d['bereiche'])


class AbschnittBenennenHTTPTest(unittest.TestCase):
    post = TextblockHTTPTest.post

    def setUp(self):
        TextblockHTTPTest.setUp(self)
        self.original = {'segments': [{'start': 0, 'end': 10, 'speaker': 's1', 'text': 'Erster Teil.'},
                                      {'start': 10, 'end': 12, 'speaker': 's2', 'text': 'Dazwischen.'},
                                      {'start': 12, 'end': 20, 'speaker': 's1', 'text': 'Zweiter Teil.'}]}
        (self.root / 'transkripte/test.gruendlich.json').write_text(json.dumps(self.original))

    def namen(self, d):
        namen = {sp['id']: sp['name'] for sp in d['sprecher']}
        return [namen.get(b['speaker_ids'][0]) for b in d['bloecke']]

    def benennen(self, block, name):
        sprecher = block['speaker_ids'][0]
        return self.post('/api/review', {'aufnahme': 'test', 'sprecher': sprecher, 'name': name,
                                         'aktion': 'bestaetigen', 'block': block['id']})

    def test_bestaetigte_abschnitte_werden_nicht_ueberschrieben(self):
        d = self.app.detail('test')
        self.assertEqual(self.namen(d), ['Anna', None, 'Anna'])
        d = self.benennen(d['bloecke'][2], 'Ben')
        self.assertTrue(d['nur_abschnitt'])
        self.assertEqual(self.namen(d), ['Anna', None, 'Ben'])
        # Unbenannte Stimme: wie bisher für die ganze Stimme.
        d = self.benennen(d['bloecke'][1], 'Carla')
        self.assertNotIn('nur_abschnitt', d)
        self.assertEqual(self.namen(d), ['Anna', 'Carla', 'Ben'])
        # Vorhandener Name: Abschnitt wird der bestehenden Person zugeordnet.
        d = self.benennen(d['bloecke'][0], 'ben')
        self.assertEqual(self.namen(d), ['Ben', 'Carla', 'Ben'])
        self.assertEqual(len({b['speaker_ids'][0] for b in d['bloecke'] if b['speaker_ids'][0] != 's2'}), 1)
        # Gleicher Name bestätigt nur, ohne neue Zeitkorrektur.
        version = d['aufteilung_version']
        d = self.benennen(d['bloecke'][1], 'Carla')
        self.assertEqual(d['aufteilung_version'], version)
