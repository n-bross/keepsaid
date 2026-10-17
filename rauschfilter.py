"""Zeitgetreue Sprachentrauschung mit FFmpeg/RNNoise (keine Stille entfernen)."""
import hashlib
import os
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from keepsaid_config import env

VERSION = 'rnnoise-bd-mix85-hp70-v1'
MODELL = Path(__file__).resolve().parent / 'modelle/rauschfilter/bd.rnnn'
SHA256 = 'ae3f7411e1e6a884f839a4a145c394408398f09854dbc1216ee02faafc98a17b'


def ffmpeg():
    return env('FFMPEG') or shutil.which('ffmpeg-full') or 'ffmpeg'


@lru_cache(maxsize=1)
def filterkette():
    if hashlib.sha256(MODELL.read_bytes()).hexdigest() != SHA256:
        raise ValueError('Das Rauschfilter-Modell fehlt oder ist beschädigt.')
    path = str(MODELL).replace('\\', '\\\\').replace(':', '\\:').replace("'", "'\\''")
    return f"aformat=channel_layouts=mono,aresample=48000,highpass=f=70,arnndn=m='{path}':mix=0.85"


def wav_bereinigen(quelle, ziel, ab=None, dauer=None):
    args = [ffmpeg(), '-nostdin', '-v', 'error', '-y']
    if ab is not None:
        args += ['-ss', str(ab)]
    args += ['-i', str(quelle)]
    if dauer is not None:
        args += ['-t', str(dauer)]
    args += ['-af', filterkette(), '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', '-threads', '1', str(ziel)]
    subprocess.run(args, check=True, capture_output=True, timeout=3600)
