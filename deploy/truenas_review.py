"""Auf TrueNAS ausführen, nachdem Code und SQLite-Snapshots bereitliegen."""
import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import yaml


def midclt(*args):
    return json.loads(subprocess.check_output(['midclt', 'call', *args]))


root = Path('/mnt/App-Data/soundcore')
config = midclt('app.config', 'soundcore')
stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
backup = root / 'config' / ('review-backup-' + stamp)
backup.mkdir(mode=0o700)
(backup / 'compose.json').write_text(json.dumps(config, indent=2))
os.chmod(backup / 'compose.json', 0o600)
serve_path = root / 'config/tailscale/serve.json'
shutil.copy2(serve_path, backup / 'serve.json')
shutil.copy2(root / 'config/compose.yaml', backup / 'compose.yaml')

state = root / 'review-state'
state.mkdir(exist_ok=True)
os.chown(state, 568, 568)
for name in ['stimmen.sqlite3', 'titel.sqlite3']:
    target = state / name
    if not target.exists():
        shutil.copy2(root / 'review-app' / ('initial-' + name), target)
        os.chown(target, 568, 568)
        os.chmod(target, 0o600)

config['services']['review'] = {
    'image': 'soundcore-review:rnnoise-v1',
    'pull_policy': 'never',
    'container_name': 'soundcore-review',
    'restart': 'unless-stopped',
    'user': '568:568',
    'network_mode': 'service:tailscale',
    'depends_on': {'tailscale': {'condition': 'service_healthy'}},
    'entrypoint': ['python3'],
    'command': ['/app/review_server.py', '--daten', '/daten', '--db',
                '/state/stimmen.sqlite3', '--referenzen', '/app/sprecher_profile.json',
                '--base-path', '/review', '--neu-alle'],
    'volumes': [str(root / 'review-app') + ':/app:ro',
                str(state) + ':/state', '/mnt/Storage/soundcore:/daten:ro'],
    'read_only': True,
    'tmpfs': ['/tmp:rw,noexec,nosuid,size=64m'],
    'cap_drop': ['ALL'],
    'security_opt': ['no-new-privileges:true'],
    'mem_limit': '512m',
    'healthcheck': {'test': ['CMD', 'python3', '-c',
        'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8765/",timeout=5).read(1)'],
        'interval': '30s', 'timeout': '10s', 'retries': 3},
}
serve = json.loads(serve_path.read_text())
for site in serve['Web'].values():
    site['Handlers']['/review/'] = {'Proxy': 'http://127.0.0.1:8765'}
serve_path.write_text(json.dumps(serve, indent=2) + '\n')
try:
    # TrueNAS verwaltet die Erweiterung dauerhaft, auch nach App-Updates/Neustarts.
    result = subprocess.run(['midclt', 'call', '--job', 'app.update', 'soundcore',
        json.dumps({'custom_compose_config': config})], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError('TrueNAS app.update fehlgeschlagen: ' + result.stderr[-3000:])
except Exception:
    shutil.copy2(backup / 'serve.json', serve_path)
    raise
(root / 'config/compose.yaml').write_text(yaml.safe_dump(config, sort_keys=False))
os.chmod(root / 'config/compose.yaml', 0o600)
print('Review-Service eingerichtet. Konfigurationsbackup:', backup)
