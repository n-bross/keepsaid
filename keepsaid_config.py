"""Keepsaid settings with compatibility for existing installations."""
import os
from pathlib import Path


def load_env():
    path=Path(__file__).resolve().parent/'.env'
    if path.exists():
        for line in path.read_text(encoding='utf-8').splitlines():
            line=line.strip()
            if line and not line.startswith('#') and '=' in line:
                key,value=line.split('=',1)
                os.environ.setdefault(key.strip(),value.strip().strip('\"\''))


load_env()


def env(name,default=None):
    """New Keepsaid configuration wins; legacy names remain accepted."""
    return os.environ.get('KEEPSAID_'+name,os.environ.get('SOUNDCORE_'+name,default))


DEFAULT_SHARE='https://keepsaid.example.ts.net'
