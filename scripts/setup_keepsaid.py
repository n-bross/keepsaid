"""Download the RNNoise filter model and check the local FFmpeg installation."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rauschfilter import ffmpeg

MODEL = Path(__file__).resolve().parents[1] / 'modelle/rauschfilter/bd.rnnn'
URL = 'https://raw.githubusercontent.com/GregorR/rnnoise-models/master/beguiling-drafter-2018-08-30/bd.rnnn'
SHA256 = 'ae3f7411e1e6a884f839a4a145c394408398f09854dbc1216ee02faafc98a17b'


def main():
    if not MODEL.exists() or hashlib.sha256(MODEL.read_bytes()).hexdigest() != SHA256:
        data = urllib.request.urlopen(URL, timeout=60).read()
        if hashlib.sha256(data).hexdigest() != SHA256:
            raise RuntimeError('RNNoise-Prüfsumme stimmt nicht überein.')
        MODEL.parent.mkdir(parents=True, exist_ok=True)
        temp = MODEL.with_suffix('.download')
        temp.write_bytes(data)
        os.replace(temp, MODEL)
    print('RNNoise-Modell bereit.')
    binary = ffmpeg()
    result = subprocess.run([binary, '-hide_banner', '-filters'], capture_output=True, text=True, check=True)
    if 'arnndn' not in result.stdout:
        raise RuntimeError('FFmpeg benötigt arnndn; KEEPSAID_FFMPEG auf FFmpeg mit RNNoise setzen.')
    print('FFmpeg mit RNNoise bereit:', binary)


if __name__ == '__main__':
    main()
