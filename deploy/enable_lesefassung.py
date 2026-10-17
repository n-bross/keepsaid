"""Enable the separate reading-version feature without replacing transcripts."""
import datetime
import os
import shutil
import subprocess
from pathlib import Path

root=Path('/mnt/App-Data/soundcore');app=root/'review-app'
backup=root/'config'/('lesefassung-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
backup.mkdir(mode=0o700)
for name in ['review_server.py','review_ui.html','lesefassung.py','.env']:
    if (app/name).exists():shutil.copy2(app/name,backup/name);os.chmod(backup/name,0o600)
for file in (app/'reading-staged').iterdir():
    if file.is_file():shutil.copy2(file,app/file.name);os.chmod(app/file.name,0o644)
env=app/'.env';lines=[line for line in env.read_text().splitlines() if not line.startswith('GROQ_LESEFASSUNG_MODELL=')]
env.write_text('\n'.join(lines+['GROQ_LESEFASSUNG_MODELL=qwen/qwen3.8-27b'])+'\n');os.chmod(env,0o600);os.chown(env,568,568)
subprocess.run(['docker','restart','soundcore-review'],check=True,stdout=subprocess.DEVNULL)
print('Lesefassung aktiviert; Sicherung:',backup.name)
