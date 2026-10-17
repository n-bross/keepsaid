import io
import json
import os
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from lesefassung import Lesefassungen, quelle, pruefen


class LesefassungTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=Lesefassungen(Path(self.tmp.name)/'reading.sqlite3')
        self.addCleanup(lambda:self.store.pool.shutdown(wait=True))
        self.parts=[{'text':'Dann dann gehen wir in Raum 1.','start':4,'end':8,'name':'Anna'}]

    def test_async_dedup_cache_and_changed_source(self):
        release=threading.Event();self.addCleanup(release.set)
        def generate(text):release.wait(3);return 'Dann gehen wir in Raum 1.'
        with patch.dict(os.environ,{'GROQ_API_KEY':'test'}),patch.object(Lesefassungen,'anfragen',side_effect=generate) as call:
            self.store.starten('a',self.parts);self.store.starten('a',self.parts)
            release.set();self.store.pool.shutdown(wait=True)
            self.assertEqual(call.call_count,1)
        result=self.store.lesen('a',self.parts)
        self.assertEqual(result['status'],'bereit');self.assertFalse(result['veraltet'])
        self.assertEqual(result['teile'][0]['text'],self.parts[0]['text'])
        self.assertEqual(result['teile'][0]['start'],4)
        changed=[{**self.parts[0],'text':'Mein korrigierter Text.'}]
        self.assertTrue(self.store.lesen('a',changed)['veraltet'])

    def test_numbers_and_truncated_output_are_rejected(self):
        for content,reason in [('Raum 2.','stop'),('Raum 1.','length')]:
            response={'choices':[{'message':{'content':content},'finish_reason':reason}]}
            with patch.dict(os.environ,{'GROQ_API_KEY':'test'}),patch('lesefassung.urllib.request.urlopen',return_value=io.BytesIO(json.dumps(response).encode())):
                with self.assertRaises(ValueError):self.store.anfragen('Raum 1.')

    def test_audio_windows_follow_words_when_splitting_long_blocks(self):
        text=('Ein langer Satz. '*400).strip()
        detail={'sprecher':[{'id':'s1','name':'Anna'}],'bloecke':[{'start':0,'end':90,'text':text,
            'bereiche':[{'speaker':'s1'}],'words':[{'char_start':0,'start':2,'end':3},
                                               {'char_start':5000,'start':70,'end':71}]}]}
        chunks=quelle(detail)
        self.assertGreater(len(chunks),1)
        self.assertEqual(chunks[0]['start'],2);self.assertEqual(chunks[1]['start'],70)
        self.assertTrue(all(c['name']=='Anna' for c in chunks))
        self.assertEqual(' '.join(c['text'] for c in chunks),text)

    def test_resume_keeps_completed_parts(self):
        basis=self.store.basis(self.parts*2)
        done=[{**self.parts[0],'lesetext':'Schon fertig.'}]
        with self.store.db() as db:db.execute('INSERT INTO lesefassungen VALUES (?,?,?,?,?,?)',
            ('a',basis,json.dumps(self.parts*2),json.dumps(done),'fehler','interrupted'))
        with patch.dict(os.environ,{'GROQ_API_KEY':'test'}),patch.object(Lesefassungen,'anfragen',return_value='Zweiter Teil.') as call:
            self.store.starten('a',self.parts*2);self.store.pool.shutdown(wait=True)
            self.assertEqual(call.call_count,1)
        self.assertEqual(self.store.lesen('a',self.parts*2)['teile'][0]['lesetext'],'Schon fertig.')

    def test_content_additions_need_labels_and_negation_cannot_disappear(self):
        with self.assertRaises(ValueError):pruefen('Raum 1 ist nicht frei.','Raum 1 ist frei.')
        with self.assertRaises(ValueError):pruefen('Wir haben Räume.','Wir haben schöne Räume.')
        pruefen('Wir haben Räume.','Wir haben [KI-Ergänzung: schöne] Räume.')


class ReadingHttpTest(unittest.TestCase):
    def setUp(self):
        import test_aufteilung
        test_aufteilung.AufteilungTest.setUp(self)
        self.addCleanup(lambda:self.app.lesefassungen.pool.shutdown(wait=True))

    def test_authentication_and_original_remain_unchanged(self):
        from review_server import Handler
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.app=self.app
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        url=f'http://127.0.0.1:{server.server_port}/api/lesefassung'
        request=urllib.request.Request(url,json.dumps({'aufnahme':'test'}).encode(),{'Content-Type':'application/json'})
        with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(request)
        self.assertEqual(error.exception.code,403)
        request.add_header('X-Review-Token',self.app.token)
        with patch.dict(os.environ,{'GROQ_API_KEY':'test'}),patch.object(Lesefassungen,'anfragen',return_value='Zwei Personen sprechen.'):
            with urllib.request.urlopen(request) as response:self.assertEqual(response.status,202)
            self.app.lesefassungen.pool.shutdown(wait=True)
        with urllib.request.urlopen(url+'?name=test') as response:result=json.load(response)['lesefassung']
        self.assertEqual(result['status'],'bereit');self.assertEqual(result['teile'][0]['start'],0)
        self.assertEqual(self.app.detail('test')['transkript'][0]['text'],'Zwei Personen sprechen.')
        self.assertEqual(json.loads((self.root/'transkripte/test.gruendlich.json').read_text()),self.original)


if __name__=='__main__':unittest.main()
