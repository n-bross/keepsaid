import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import test_aufteilung
from wortzeiten import Wortzeiten


class ReviewAnnotationsTest(unittest.TestCase):
    def setUp(self):
        test_aufteilung.AufteilungTest.setUp(self)

    def test_sidebar_duration_is_seconds_and_automatic_queue_skips_embedded_words(self):
        (self.root/'audio/test.json').write_text(json.dumps({'audio_duration': 125500}))
        self.assertEqual(self.app.aufnahmen(neu=True)[0]['dauer'], 125.5)
        with patch.dict(os.environ, {'GROQ_API_KEY': 'fixture'}), patch.object(self.app.wortzeiten, 'starten') as start:
            self.app.wortzeiten_automatisch()
            start.assert_called_once_with('test', (self.root/'audio/test.wav').resolve())
            start.reset_mock()
            self.original['segments'][0]['words'] = [{'word': 'Zwei', 'start': 0, 'end': 1}]
            (self.root/'transkripte/test.gruendlich.json').write_text(json.dumps(self.original))
            self.app.quelle.json.cache_clear()
            self.app.wortzeiten_automatisch()
            start.assert_not_called()

    def test_completed_chunks_resume_after_interruption(self):
        datei = self.root/'audio/test.wav'
        job = self.app.wortzeiten
        basis = job.basis(datei)
        data = {'words': [{'word': 'Hallo', 'start': 1, 'end': 1.5}]}
        with patch('wortzeiten.subprocess.check_output', return_value=b'{"format":{"duration":"480"}}'), \
             patch('wortzeiten.subprocess.run', return_value=SimpleNamespace(stdout=b'wav')) as ffmpeg, \
             patch.object(job, 'transkribieren', side_effect=[data, ValueError('offline')]):
            job.verarbeiten('test', str(datei), basis)
            self.assertEqual(job.status('test', basis)['status'], 'fehler')
            self.assertEqual(ffmpeg.call_count, 2)
        with patch('wortzeiten.subprocess.check_output', return_value=b'{"format":{"duration":"480"}}'), \
             patch('wortzeiten.subprocess.run', return_value=SimpleNamespace(stdout=b'wav')) as ffmpeg, \
             patch.object(job, 'transkribieren', return_value=data):
            job.verarbeiten('test', str(datei), basis)
            self.assertEqual(job.status('test', basis)['status'], 'bereit')
            ffmpeg.assert_called_once()
        self.assertEqual(len(job.lesen('test', basis)['words']), 2)
        with sqlite3.connect(job.pfad) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM wortzeiten_teile').fetchone()[0], 0)
