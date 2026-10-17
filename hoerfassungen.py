"""Kleine, direkt suchbare MP3-Hörfassungen; Quelldateien bleiben unverändert."""
import hashlib
import itertools
import queue
import subprocess
import threading
from pathlib import Path

from rauschfilter import ffmpeg, filterkette, VERSION
from mosslaeufe import MossLaeufe


class Hoerfassungen:
    def __init__(self, ordner):
        self.ordner = Path(ordner)
        self.ordner.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.jobs = {}
        self.queue = queue.PriorityQueue()
        self.serial = itertools.count()
        threading.Thread(target=self.arbeiten, daemon=True, name='hoerfassungen').start()

    def ziel(self, datei, bereinigt=False):
        datei = Path(datei)
        if bereinigt:
            moss = self.ordner.parent / 'mossformer' / (MossLaeufe.basis(datei)+'.mp3')
            if moss.is_file():
                return moss
        st = datei.stat()
        variante = VERSION+':' if bereinigt else ''
        key = hashlib.sha256(f'{variante}mp3-mono32-v1:{datei.name}:{st.st_size}:{st.st_mtime_ns}'.encode()).hexdigest()
        return self.ordner / (key + '.mp3')

    def anfordern(self, datei, prioritaet=0, bereinigt=False):
        ziel = self.ziel(datei, bereinigt)
        if ziel.is_file():
            return {'status': 'bereit'}
        with self.lock:
            alt = self.jobs.get(str(ziel), {})
            if alt.get('status') == 'laeuft':
                return {'status': 'laeuft'}
            if alt.get('status') == 'wartet' and alt['prioritaet'] <= prioritaet:
                return {'status': 'wartet'}
            self.jobs[str(ziel)] = {'status': 'wartet', 'prioritaet': prioritaet}
            self.queue.put((prioritaet, next(self.serial), str(datei), str(ziel), bereinigt))
        return {'status': 'wartet'}

    def status(self, datei, bereinigt=False):
        ziel = self.ziel(datei, bereinigt)
        if ziel.is_file():
            return {'status': 'bereit'}
        with self.lock:
            state = self.jobs.get(str(ziel), {'status': 'fehlt'})
            return {k: v for k, v in state.items() if k != 'prioritaet'}

    @staticmethod
    def konvertieren(quelle, ziel, bereinigt=False):
        subprocess.run([ffmpeg(), '-nostdin', '-v', 'error', '-y', '-i', str(quelle),
                        '-map', '0:a:0', '-vn', *(['-af', filterkette()] if bereinigt else []), '-ac', '1', '-ar', '24000',
                        '-c:a', 'libmp3lame', '-b:a', '32k', '-threads', '1',
                        '-write_xing', '1', str(ziel)], check=True, capture_output=True, timeout=1800)

    def arbeiten(self):
        while True:
            prioritaet, _, quelle, ziel, bereinigt = self.queue.get()
            temp = Path(ziel).with_suffix('.partial.mp3')
            try:
                with self.lock:
                    state = self.jobs.get(ziel, {})
                    if state.get('status') != 'wartet' or state.get('prioritaet') != prioritaet:
                        continue
                    self.jobs[ziel] = {'status': 'laeuft'}
                self.konvertieren(quelle, temp, bereinigt=bereinigt)
                if self.ziel(quelle, bereinigt) != Path(ziel):
                    raise ValueError('Quelldatei wurde geändert.')
                temp.replace(ziel)
                with self.lock:
                    self.jobs[ziel] = {'status': 'bereit'}
            except Exception:
                with self.lock:
                    self.jobs[ziel] = {'status': 'fehler', 'error': 'Hörfassung konnte nicht erstellt werden. Bitte erneut versuchen.'}
            finally:
                temp.unlink(missing_ok=True)
                self.queue.task_done()
