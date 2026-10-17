"""Auf dem NAS: getestetes Review-Image aktivieren, Datenbanken vorher sichern."""
import datetime
import json
import os
import shutil
import sqlite3
import yaml
import subprocess
from pathlib import Path

root=Path('/mnt/App-Data/soundcore')
config=json.loads(subprocess.check_output(['midclt','call','app.config','soundcore']))
backup=root/'config'/('noise-rollout-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
backup.mkdir(mode=0o700)
(backup/'app-config.json').write_text(json.dumps(config))
os.chmod(backup/'app-config.json',0o600)
shutil.copy2(root/'config/compose.yaml',backup/'compose.yaml')
for path in (root/'review-state').glob('*.sqlite3'):
    with sqlite3.connect(f'file:{path}?mode=ro',uri=True) as source,sqlite3.connect(backup/path.name) as target:
        source.backup(target)
    os.chmod(backup/path.name,0o600)
review=config['services']['review']
review['image']='soundcore-review:rnnoise-v1'
if '--neu-alle' not in review['command']:
    review['command'].append('--neu-alle')
# Der Container bleibt als unprivilegierter Benutzer mit schreibgeschütztem Code/Daten-Mount bestehen.
result=subprocess.run(['midclt','call','--job','app.update','soundcore',json.dumps({'custom_compose_config':config})],capture_output=True)
(backup/'deploy-result.log').write_bytes(result.stdout+result.stderr)
os.chmod(backup/'deploy-result.log',0o600)
if result.returncode:
    raise SystemExit('App-Update fehlgeschlagen; Details liegen im geschützten Backup.')
(root/'config/compose.yaml').write_text(yaml.safe_dump(config,sort_keys=False))
os.chmod(root/'config/compose.yaml',0o600)
print('Review-Image mit Sprachfilter aktiviert; Sicherung:',backup.name)
