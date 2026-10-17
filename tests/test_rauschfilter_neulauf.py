import json
import math
import struct
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from neutransk import Neutranskription
from rauschfilter import wav_bereinigen
from hoerfassungen import Hoerfassungen
import test_aufteilung


class RauschfilterTest(unittest.TestCase):
    def test_neural_filter_reduces_stationary_noise_without_time_shift(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);src=root/'noise.wav';out=root/'clean.wav'
            import random
            rng=random.Random(12)
            with wave.open(str(src),'wb') as w:
                w.setparams((1,2,48000,0,'NONE','not compressed'))
                w.writeframes(b''.join(struct.pack('<h',int(rng.uniform(-1000,1000))) for _ in range(48000*3)))
            wav_bereinigen(src,out)
            with wave.open(str(out),'rb') as w:
                self.assertAlmostEqual(w.getnframes()/w.getframerate(),3,delta=.02)
                samples=struct.unpack('<'+'h'*w.getnframes(),w.readframes(w.getnframes()))
            rms=math.sqrt(sum(x*x for x in samples[len(samples)//2:])/(len(samples)-len(samples)//2))
            self.assertLess(rms,350)  # Ausgangsrauschen: etwa 577 RMS.
            cache=Hoerfassungen(root/'cache')
            self.assertNotEqual(cache.ziel(src),cache.ziel(src,True))


class NeulaufTest(unittest.TestCase):
    def setUp(self):
        test_aufteilung.AufteilungTest.setUp(self)
        self.neu=self.app.neu
        self.audio=self.root/'audio/test.wav'
        self.neu.einreihen('test',self.audio,20)
        self.data={'duration':20,'segments':[{'start':4,'end':6,'text':'Ganz neuer Text.'}],
                   'words':[{'word':'Ganz','start':4,'end':4.5},{'word':'neuer','start':4.6,'end':5},{'word':'Text.','start':5.2,'end':6}]}

    def ready(self):
        with self.neu.db() as db:
            db.execute("UPDATE jobs SET status='bereit',daten=? WHERE name='test'",(json.dumps(self.data),))

    def test_new_text_becomes_active_and_later_manual_edits_stay_active(self):
        self.ready()
        d=self.app.detail('test')
        self.assertEqual(d['transkript'][0]['text'],'Ganz neuer Text.')
        self.stimmen.text_speichern('test','test.gruendlich.json',0,'Ganz neuer Text.','Mein korrigierter Text.')
        self.assertEqual(self.app.detail('test')['transkript'][0]['text'],'Mein korrigierter Text.')
        self.assertEqual(self.app.detail('test')['bereiche'],self.original['sprecher_segmente'])

    def test_preexisting_corrections_keep_old_base_and_new_text_is_available(self):
        self.stimmen.text_speichern('test','test.gruendlich.json',0,'Zwei Personen sprechen.','Von mir korrigiert.')
        self.ready()
        d=self.app.detail('test')
        self.assertEqual(d['transkript'][0]['text'],'Von mir korrigiert.')
        self.assertTrue(d['neutranskript']['geschuetzt'])
        self.assertEqual(d['neutranskript']['vergleich'],'Ganz neuer Text.')
        self.assertEqual(d['sprecher'][0]['name'],'Anna')
        self.assertEqual(json.loads((self.root/'transkripte/test.gruendlich.json').read_text()),self.original)

    def test_checkpoint_resumes_and_short_jobs_come_first(self):
        path=self.root/'audio/short.wav';path.touch();self.neu.einreihen('short',path,2)
        with self.neu.db() as db:
            row=dict(db.execute("SELECT * FROM jobs ORDER BY dauer LIMIT 1").fetchone())
        self.assertEqual(row['name'],'short')
        data={'segments':[{'start':0,'end':1,'text':'Hallo'}],'words':[{'word':'Hallo','start':.1,'end':.8}]}
        with patch('neutransk.subprocess.check_output',return_value=b'{"format":{"duration":"2"}}'), \
             patch('neutransk.subprocess.run',return_value=SimpleNamespace(stdout=b'wav')), \
             patch('neutransk.Wortzeiten.transkribieren',return_value=data) as asr:
            self.neu.verarbeiten(row)
            asr.assert_called_once()
        self.assertEqual(self.neu.statistik()['fertig'],1)
        self.assertEqual(self.neu.lesen('short',path)['daten']['segments'][0]['text'],'Hallo')
        self.neu.einreihen('short',path,2)
        self.assertEqual(self.neu.statistik()['fertig'],1)

    def test_local_claim_survives_restart_and_can_complete_without_cloud(self):
        job=self.neu.lokal_uebernehmen()
        self.assertEqual(self.neu.statistik()['lokal'],1)
        self.assertIsNone(self.neu.lesen('test',self.audio)['error'])
        restarted=Neutranskription(self.neu.pfad)
        self.assertEqual(restarted.lesen('test',self.audio)['status'],'lokal')
        self.assertIsNone(restarted.lokal_uebernehmen())
        restarted.lokal_ergebnis({**job,'aktion':'fortschritt','fortschritt':40})
        self.assertEqual(restarted.lesen('test',self.audio)['fortschritt'],40)
        restarted.lokal_ergebnis({**job,'ergebnis':self.data})
        self.assertEqual(restarted.statistik()['fertig'],1)
        with self.assertRaises(ValueError):restarted.lokal_ergebnis({**job,'ergebnis':self.data})
