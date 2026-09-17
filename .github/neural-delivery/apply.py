"""Restore only the exact reviewed commit series onto its pinned clean base."""
import base64
import hashlib
import json
import lzma
import os
from pathlib import Path
import subprocess
import tempfile

BASE = 'f279eea822b3f04f96fbca5dc3f5d6234607c3da'
BASE_TREE = 'd0f94db0584265c579d3f4b2fe094f7bb7e0dfdc'
HEAD = '9668c04c05942ad1f6bb725860b86d1ffdb36993'
TREE = '0bfc680fc677b6e022b2575afc5b4487c1051e0e'
DIGEST = 'd6437f0af576e896ad8a0ef87ee0c483e92b1a88867ff5ee8c0bd98560f6a7ae'


def git(*args, data=None):
    return subprocess.check_output(['git', *args], input=data).decode().strip()


def read_series():
    root = Path(__file__).resolve().parent
    encoded = ''.join((root / f'part-{i}.b64').read_text().strip() for i in range(5))
    assert len(encoded) == 38488
    decoder = lzma.LZMADecompressor(memlimit=128 * 1024 * 1024)
    raw = decoder.decompress(base64.b64decode(encoded, validate=True), max_length=2 * 1024 * 1024)
    assert decoder.eof and not decoder.unused_data
    assert hashlib.sha256(raw).hexdigest() == DIGEST
    series = json.loads(raw)
    assert isinstance(series, list) and len(series) == 7
    return series


def apply():
    assert git('rev-parse', 'HEAD') == BASE
    assert git('rev-parse', 'HEAD^{tree}') == BASE_TREE
    assert not git('status', '--porcelain')
    subprocess.run(['git', 'config', 'core.autocrlf', 'false'], check=True)
    ledger = []
    parent = BASE
    for item in read_series():
        assert set(item) == {'commit', 'raw', 'tree', 'patch'}
        raw = item['raw'].encode('utf-8')
        assert raw.startswith(f"tree {item['tree']}\nparent {parent}\n".encode())
        fd, name = tempfile.mkstemp(suffix='.patch')
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(item['patch'].encode('utf-8'))
            subprocess.run(['git', 'apply', '--check', name], check=True)
            subprocess.run(['git', 'apply', name], check=True)
            subprocess.run(['git', 'add', '-A'], check=True)
            changes = git('diff', '--cached', '--name-only').splitlines()
            assert all(not p.startswith('.github/') or p == '.github/workflows/neural-ci.yml'
                       for p in changes)
            assert git('write-tree') == item['tree']
            assert git('hash-object', '-t', 'commit', '-w', '--stdin', data=raw) == item['commit']
            subprocess.run(['git', 'update-ref', 'HEAD', item['commit'], parent], check=True)
            subprocess.run(['git', 'reset', '--hard', item['commit']], check=True)
            assert not git('status', '--porcelain')
            parent = item['commit']
            ledger.append({'sha': parent, 'tree': item['tree'], 'subject': git('show', '-s', '--format=%s')})
        finally:
            Path(name).unlink(missing_ok=True)
    assert git('rev-parse', 'HEAD') == HEAD and git('rev-parse', 'HEAD^{tree}') == TREE
    target = Path(os.environ.get('RUNNER_TEMP', tempfile.gettempdir())) / 'neural-evidence'
    target.mkdir(exist_ok=True)
    (target / 'module-ledger.json').write_text(json.dumps(ledger, indent=2))
    print(json.dumps({'head': HEAD, 'tree': TREE, 'modules': ledger}, indent=2))


if __name__ == '__main__':
    apply()
