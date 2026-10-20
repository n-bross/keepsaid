import tempfile
import json
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
import numpy as np

import worker_mac as worker
from titel import erstelle_titel


class GroqIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_titel_mit_groq_mock(self):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="Kurzer Test Titel"))]
        with patch("groq.Groq") as mock_client:
            mock_client.return_value.chat.completions.create.return_value = mock_response
            titel = erstelle_titel("test_aufnahme_1", "Das ist ein längerer Text über das Thema Programmierung und KI-Modelle in Python.")
            self.assertEqual(titel, "Kurzer Test Titel")

    def test_worker_kurze_aufnahme_waehlt_groq(self):
        # 30 Sekunden Audio (unter 600s)
        samples = np.zeros(30 * 16000, dtype=np.float32)
        mock_res = MagicMock()
        mock_res.segments = [{"start": 0.0, "end": 2.5, "text": "Hallo Groq"}]
        mock_res.words = [{"word": "Hallo", "start": .1, "end": .5}, {"word": "Groq", "start": 1.2, "end": 1.8}]
        uploaded = {}
        def upload(path, folder):
            uploaded[path.name] = path.read_text()
        
        with patch.object(worker, "WORK", self.root), \
             patch.object(worker, "holen"), \
             patch.object(worker, "hochladen", side_effect=upload), \
             patch.object(worker, "nach_wav", return_value=samples), \
             patch.object(worker, "modelle_sicherstellen", return_value=(None, None)), \
             patch.object(worker, "diarisieren", return_value=[]), \
             patch.object(worker, "erstelle_titel", return_value="Test Titel"), \
             patch("groq.Groq") as mock_client:
            
            mock_client.return_value.audio.transcriptions.create.return_value = mock_res
            # Erstelle Dummy-WAV damit die Datei-Existenzprüfung greift
            (self.root / "kurz.16k.wav").write_bytes(b"RIFF" + b"\x00" * 100)
            
            summary = worker.verarbeite("kurz", ".wav", "schnell", False)
            self.assertEqual(summary["modell"], "groq/whisper-large-v3-turbo")
            self.assertEqual(summary["segmente"], 1)
            self.assertEqual(mock_client.return_value.audio.transcriptions.create.call_args.kwargs["timestamp_granularities"], ["word", "segment"])
            result = json.loads(uploaded["kurz.schnell.json"])
            self.assertEqual(result["segments"][0]["words"], mock_res.words)

    def test_worker_lange_aufnahme_nutzt_lokal_mlx(self):
        # 700 Sekunden Audio (> 600s)
        samples = np.zeros(700 * 16000, dtype=np.float32)
        mock_mlx = MagicMock()
        mock_mlx.transcribe.return_value = {"segments": [{"start": 0.0, "end": 2.5, "text": "Hallo MLX"}]}

        with patch.object(worker, "WORK", self.root), \
             patch.object(worker, "holen"), \
             patch.object(worker, "hochladen"), \
             patch.object(worker, "nach_wav", return_value=samples), \
             patch.object(worker, "modelle_sicherstellen", return_value=(None, None)), \
             patch.object(worker, "diarisieren", return_value=[]), \
             patch.object(worker, "erstelle_titel", return_value="Test Titel"), \
             patch.dict("sys.modules", {"mlx_whisper": mock_mlx}):

            (self.root / "lang.16k.wav").write_bytes(b"RIFF" + b"\x00" * 100)
            summary = worker.verarbeite("lang", ".wav", "schnell", False)
            self.assertEqual(summary["modell"], "mlx-community/whisper-large-v3-turbo")
            self.assertTrue(mock_mlx.transcribe.call_args.kwargs["word_timestamps"])
            self.assertEqual(summary["segmente"], 1)

    def _groq_lauf(self, sekunden):
        samples = np.zeros(int(sekunden * 16000), dtype=np.float32)
        mock_res = MagicMock()
        mock_res.segments = [{"start": 1.0, "end": 4.0, "text": "Hallo"}]
        mock_res.words = []
        diar = MagicMock(return_value=[{"start": 1.0, "end": 4.0, "speaker": "speaker1"}])
        abdruck = MagicMock(return_value={})
        with patch.object(worker, "WORK", self.root), \
             patch.object(worker, "holen"), \
             patch.object(worker, "hochladen"), \
             patch.object(worker, "nach_wav", return_value=samples), \
             patch.object(worker, "modelle_sicherstellen", return_value=(None, None)), \
             patch.object(worker, "diarisieren", diar), \
             patch.object(worker, "stimmabdruecke", abdruck), \
             patch.object(worker, "erstelle_titel", return_value=None), \
             patch.object(worker, "GROQ_API_KEY", "gsk_test"), \
             patch("groq.Groq") as mock_client:
            mock_client.return_value.audio.transcriptions.create.return_value = mock_res
            (self.root / "x.16k.wav").write_bytes(b"RIFF")
            summary = worker.verarbeite("x", ".wav", "schnell", True, lokal="nie")
        return summary, diar, abdruck

    def test_kurze_sprachnachricht_ohne_sprechertrennung(self):
        summary, diar, abdruck = self._groq_lauf(60)
        diar.assert_not_called()
        self.assertFalse(summary["sprecher_getrennt"])
        self.assertEqual(summary["redeanteil_sekunden"], {"speaker1": 3.0})
        # Stimmabdruck für Namensvorschläge wird trotzdem gebildet
        self.assertEqual(abdruck.call_args.args[1], [{"start": 1.0, "end": 4.0, "speaker": "speaker1"}])

    def test_mittlere_aufnahme_mit_sprechertrennung_ohne_gpu(self):
        # lokal="nie": Groq + Sprechertrennung dürfen die GPU-Sperre nicht brauchen
        summary, diar, _ = self._groq_lauf(300)
        diar.assert_called_once()
        self.assertTrue(summary["sprecher_getrennt"])

    def test_lokale_transkription_bei_belegter_gpu(self):
        import fcntl
        samples = np.zeros(700 * 16000, dtype=np.float32)
        mock_mlx = MagicMock()
        with patch.object(worker, "WORK", self.root), \
             patch.object(worker, "holen"), \
             patch.object(worker, "nach_wav", return_value=samples), \
             patch.dict("sys.modules", {"mlx_whisper": mock_mlx}), \
             open(self.root / ".worker.lock", "a") as fremd:
            fcntl.flock(fremd, fcntl.LOCK_EX)
            with self.assertRaises(worker.GpuBelegt):
                worker.verarbeite("lang", ".wav", "schnell", False, lokal="sofort")
            with self.assertRaises(worker.GpuBelegt):
                worker.verarbeite("lang", ".wav", "schnell", False, lokal="nie")
        mock_mlx.transcribe.assert_not_called()

    def test_belegte_gpu_stellt_lange_aufnahmen_zurueck(self):
        aufrufe = []
        def verarbeite(name, endung, stufe, mit_sprechern, **kw):
            aufrufe.append((name, kw["lokal"]))
            if name == "lang" and kw["lokal"] == "sofort":
                raise worker.GpuBelegt("belegt")
            modell = "mlx/x" if name == "lang" else "groq/x"
            return {"modell": modell, "audio_sekunden": 1, "rechen_sekunden_text": 0,
                    "rechen_sekunden_sprecher": 0, "faktor": 0, "sprecher_anzahl": 1, "woerter": 1}
        with patch.object(worker, "WORK", self.root), \
             patch.object(worker, "offene", return_value=[("lang", ".ogg"), ("kurz1", ".ogg"), ("kurz2", ".ogg")]), \
             patch.object(worker, "verarbeite", side_effect=verarbeite), \
             patch("sys.argv", ["worker_mac.py", "--limit", "1"]):
            self.assertEqual(worker.main(), 0)
        self.assertEqual(aufrufe, [("lang", "sofort"), ("kurz1", "sofort"), ("kurz2", "sofort"), ("lang", "warten")])


if __name__ == "__main__":
    unittest.main()
