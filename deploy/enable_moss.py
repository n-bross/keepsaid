"""Back up live review code/state, enable Moss jobs, and restart only review."""
import datetime
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess

root=Path('/mnt/App-Data/soundcore')
backup=root/'config'/('moss-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
backup.mkdir(mode=0o700)
app=root/'review-app'
for name in ['review_server.py','review_ui.html','wortzeiten.py','hoerfassungen.py','.env']:
    if (app/name).exists():shutil.copy2(app/name,backup/name)
for file in (root/'review-state').glob('*.sqlite3'):
    with sqlite3.connect(f'file:{file}?mode=ro',uri=True) as src,sqlite3.connect(backup/file.name) as dst:src.backup(dst)
for f in backup.iterdir():os.chmod(f,0o600)
# Staged and syntax-checked files are copied before the single service restart.
for file in (app/'moss-staged').iterdir():
    if not file.is_file():continue
    shutil.copy2(file,app/file.name);os.chmod(app/file.name,0o644)
env=app/'.env'
lines=[s for s in env.read_text().splitlines() if not s.startswith(('SOUNDCORE_MOSS=','SOUNDCORE_AUTO_WORTZEITEN='))]
env.write_text('\n'.join(lines+['SOUNDCORE_MOSS=1','SOUNDCORE_AUTO_WORTZEITEN=0'])+'\n')
os.chmod(env,0o600);os.chown(env,568,568)
audio=root/'review-state/mossformer';audio.mkdir(exist_ok=True);os.chown(audio,568,568)
subprocess.run(['docker','restart','soundcore-review'],check=True,stdout=subprocess.DEVNULL)
print('MossFormer2 aktiviert. Backup:',backup.name)
