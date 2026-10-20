import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import worker_mac as worker
from review_server import Anwendung, Quelle
from stimmen import Stimmen


class UnsichereSprecherTest(unittest.TestCase):
    def test_kurze_und_fehlende_stimmen_nicht_erzwungen(self):
        profile = {'a': {'vektor': [1, 0]}, 'b': {'vektor': [0, 1]}}
        result = worker.sprecher_aufraeumen(profile, {'a': 100, 'b': 1, 'ohne_vektor': .5})
        self.assertEqual(len(set(result.values())), 3)
        self.assertEqual(result['a'], 'speaker1')
        result = worker.sprecher_aufraeumen({}, {'a': .5, 'b': .2})
        self.assertEqual(len(set(result.values())), 2)

    def test_keine_transitive_verschmelzung(self):
        # A≈B und B≈C, aber A und C unterschreiten die Schwelle.
        profile = {name: {'vektor': [float(np.cos(angle)), float(np.sin(angle))],
                          'sekunden_genutzt': 2} for name, angle in [('a', 0), ('b', .4), ('c', .8)]}
        result = worker.verschmelze_aehnliche(profile)
        self.assertEqual(result['a'], result['b'])
        self.assertNotEqual(result['a'], result['c'])

    def test_unsichere_namen_und_fehlender_vektor_gehen_zur_review(self):
        for vector in ([1, 0], None):
            with self.subTest(vector=vector), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                for folder in ['audio', 'transkripte', 'stimmprofile']:
                    (root / folder).mkdir()
                (root / 'audio/neu.wav').touch()
                db = Stimmen(root / 'stimmen.sqlite3', root / 'leer.json')
                db.bestaetigen('referenz_a', 's', worker.EMB_MODELL, 'Anna', [1, 0])
                db.bestaetigen('referenz_b', 's', worker.EMB_MODELL, 'Ben', [.99, .1])
                uploaded = {}
                def upload(path, folder):
                    uploaded[path.name] = path.read_text()
                    (root / folder / path.name).write_text(path.read_text())
                profiles = {'s': {'vektor': vector, 'sekunden_genutzt': 2,
                                  'sekunden_gesamt': 2, 'segmente': 1}} if vector else {}
                mlx = types.SimpleNamespace(transcribe=lambda *args, **kwargs: {
                    'segments': [{'start': 0, 'end': 2, 'text': 'Hallo'}]})
                with patch.dict('sys.modules', {'mlx_whisper': mlx}), \
                     patch.object(worker, 'WORK', root), \
                     patch.object(worker, 'holen'), \
                     patch.object(worker, 'nach_wav', return_value=np.zeros(32000)), \
                     patch.object(worker, 'modelle_sicherstellen', return_value=(None, None)), \
                     patch.object(worker, 'OHNE_SPRECHER_BIS', 0), \
                     patch.object(worker, 'diarisieren', return_value=[{'start': 0, 'end': 2, 'speaker': 's'}]), \
                     patch.object(worker, 'stimmabdruecke', return_value=profiles), \
                     patch.object(worker, 'erstelle_titel', return_value='Testgespräch mit unbekannter Person'), \
                     patch.object(worker, 'stimmenbibliothek', return_value=db), \
                     patch.object(worker, 'hochladen', side_effect=upload):
                    summary = worker.verarbeite('neu', '.wav', 'schnell', True, groq_aktiv=False)
                self.assertEqual(summary['sprecher_pruefen'], ['speaker1'])
                text = json.loads(uploaded['neu.schnell.json'])
                self.assertIsNone(text['segments'][0]['speaker_name'])
                self.assertEqual(text['sprecher_namen'], {})
                self.assertNotIn('## Anna', uploaded['neu.schnell.md'])
                profile = json.loads(uploaded['neu.stimmen.json'])['sprecher']['speaker1']
                self.assertTrue(profile['pruefen'])
                self.assertFalse(profile['name_bestaetigt'])
                if vector:
                    self.assertFalse(profile['namensvorschlag']['eindeutig'])
                detail = Anwendung(Quelle('', root), db).detail('neu')
                self.assertEqual(len(detail['sprecher']), 1)
                self.assertIsNone(detail['sprecher'][0]['name'])
                self.assertTrue(detail['sprecher'][0]['benennbar'])
                self.assertEqual(db.reviews('neu', worker.EMB_MODELL), {})


if __name__ == '__main__':
    unittest.main()
