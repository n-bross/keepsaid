import tempfile
import unittest
import wave
import subprocess
from pathlib import Path
from hoerfassungen import Hoerfassungen


class HoerfassungenTest(unittest.TestCase):
    def test_conversion_is_seekable_small_and_cached(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'test.wav'
            with wave.open(str(source),'wb') as w:
                w.setparams((2,2,48000,0,'NONE','not compressed'));w.writeframes(b'\0'*48000*4*3)
            cache=Hoerfassungen(root/'cache')
            cache.anfordern(source)
            cache.queue.join()
            self.assertEqual(cache.status(source)['status'],'bereit')
            result=cache.ziel(source)
            self.assertLess(result.stat().st_size,source.stat().st_size/10)
            duration=float(subprocess.check_output(['ffprobe','-v','error','-show_entries','format=duration','-of','default=nw=1:nk=1',str(result)]))
            self.assertAlmostEqual(duration,3,delta=.15)
            mtime=result.stat().st_mtime_ns
            cache.anfordern(source)
            self.assertEqual(result.stat().st_mtime_ns,mtime)
            source.write_bytes(source.read_bytes()+b'\0')
            self.assertNotEqual(cache.ziel(source),result)
