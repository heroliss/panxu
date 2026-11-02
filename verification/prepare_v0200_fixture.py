"""Prepare isolated release profiles from a previously owned specialist fixture.

No user profile, credentials, cloud provider or system mutation is used. SQLite
backup produces consistent snapshots; the new recovery samples remain intact.
"""
from contextlib import closing
import argparse
import hashlib
import json
import re
from pathlib import Path
import sqlite3
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from diskpilot.server import Application
from diskpilot.scanner import scan_directory


def backup(source, destination):
    destination.mkdir(parents=True, exist_ok=False)
    for name in ('simulation.sqlite3', 'scan_index.sqlite3', 'developer_trace.sqlite3'):
        with closing(sqlite3.connect((source / name).as_uri() + '?mode=ro', uri=True)) as origin:
            with closing(sqlite3.connect(destination / name)) as target:
                origin.backup(target)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label', default='v0200')
    args = parser.parse_args()
    if not re.fullmatch(r'v0200(?:-[a-z0-9-]+)?', args.label):
        parser.error('Use an owned v0200 fixture label.')
    old = ROOT / 'verification/source-v0190'
    target = ROOT / ('verification/source-' + args.label)
    if target.exists():
        raise RuntimeError('Existing release fixtures are preserved; choose a new preparation version.')
    metadata = json.loads((old / 'fixture-meta.json').read_text(encoding='utf-8'))
    scope = Path(metadata['scope'])
    if not scope.is_dir() or not scope.name.startswith('panxu-owned-v0190-'):
        raise RuntimeError('Previously owned specialist scope is unavailable.')
    target.mkdir()
    backup(old / 'data2', target / 'data')
    recovery_scope = Path(tempfile.mkdtemp(prefix='panxu-owned-v0200-recovery-'))
    for name in ('可还原的缓存.bin', '可永久删除的临时文件.tmp'):
        (recovery_scope / name).write_bytes(('Owned release fixture: ' + name).encode('utf-8') * 200)
    app = Application(target / 'data')
    try:
        app.state.publish(scan_directory(str(recovery_scope))['items'], 'v0200-owned-recovery')
        files = [i for i in app.state.snapshot()['items'] if i.get('path', '').startswith(str(recovery_scope)) and i['kind'] == 'file']
        plan = app.state.prepare([i['id'] for i in files], action='delete')
        app.state.execute(plan['id'], operation_id='owned-v0200-recovery-seed')
        metadata['recovery_fixture'] = str(recovery_scope)
        metadata['recovery_hashes'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in recovery_scope.iterdir()}
        metadata['fixture_version'] = '0.20.0'
    finally:
        app.close()
    # Neither profile receives old model_settings.json. Fixture advice keeps its
    # original provenance, while no new model request can leave this machine.
    backup(target / 'data', ROOT / ('verification/native-' + args.label + '/data'))
    (target / 'fixture-meta.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'status': 'prepared', 'metadata': str(target / 'fixture-meta.json'), 'retained_samples': len(files)}))


if __name__ == '__main__':
    main()
