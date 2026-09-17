"""Reconstruct only authenticated, locally tested source blobs; never execute them."""
import hashlib
import json
import lzma
import os
from pathlib import Path, PurePosixPath
import re
import subprocess

MAIN = '17856c7210d493ef6e7b670397596dcc70cca65d'
PR = 'a51ff89cda0e31f19ba972ed0f78e9a58dd803fb'
TREE = '060a5818e253f36f04c31be42a10033ce0b4f1fc'
COMMIT = '8ba3549a072407be94a43013c6c47db99cc79389'
DIGEST = '7d78d1bf08106c0e8810ff080a537cda046a3a595582fe42fd9e15d90802b282'

def git(*args, data=None):
    return subprocess.check_output(['git', *args], input=data)

def sha_blob(data):
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()

assert git('rev-parse', 'HEAD').decode().strip() == MAIN
assert not git('status', '--porcelain').strip()
git('cat-file', '-e', PR)
decoder = lzma.LZMADecompressor(memlimit=128 * 1024 * 1024)
raw = decoder.decompress(Path(__file__).with_name('manifest.xz').read_bytes(), max_length=2**20)
assert decoder.eof and not decoder.unused_data
assert hashlib.sha256(raw).hexdigest() == DIGEST
manifest = json.loads(raw)
assert (manifest['main'], manifest['pr'], manifest['tree'], manifest['commit']) == (MAIN, PR, TREE, COMMIT)
assert len(manifest['files']) == 44
root = Path.cwd().resolve()
outputs = {}
for entry in manifest['files']:
    path = PurePosixPath(entry['path'])
    assert not path.is_absolute() and '..' not in path.parts
    assert path.parts[0] not in {'.git', '.github'}
    target = root.joinpath(*path.parts)
    assert root in target.resolve().parents
    assert not any(p.is_symlink() for p in (target, *target.parents))
    assert target not in outputs
    assert not entry.get('delete'), 'this integration has no deletions'
    base = entry['base']
    assert base is None or re.fullmatch('[0-9a-f]{40}', base)
    original = b'' if base is None else git('cat-file', 'blob', base)
    assert base is None or sha_blob(original) == base
    lines = original.decode('utf-8').splitlines(keepends=True)
    limit = len(lines)
    for start, end, replacement in reversed(entry['edits']):
        assert type(start) is int and type(end) is int and 0 <= start <= end <= limit
        lines[start:end] = [replacement]
        limit = start
    data = ''.join(lines).encode('utf-8')
    assert sha_blob(data) == entry['sha'], str(path)
    outputs[target] = data
for target, data in outputs.items():
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
git('add', '-A')
assert not git('diff', '--cached', '--name-only', '--', '.github').strip()
assert git('write-tree').decode().strip() == TREE
git('diff', '--cached', '--check')
commit = git('hash-object', '-t', 'commit', '-w', '--stdin', data=manifest['commit_object'].encode()).decode().strip()
assert commit == COMMIT
git('update-ref', 'HEAD', COMMIT, MAIN)
assert not git('status', '--porcelain').strip()
assert git('rev-list', '--parents', '-n', '1', 'HEAD').decode().strip() == f'{COMMIT} {PR} {MAIN}'
print('VERIFIED_MERGE_COMMIT=' + COMMIT)
print('VERIFIED_MERGE_TREE=' + TREE)
if 'RUNNER_TEMP' in os.environ:
    Path(os.environ['RUNNER_TEMP'], 'merge-identity.json').write_text(json.dumps({
        'commit':COMMIT,'tree':TREE,'parents':[PR,MAIN],'manifest_sha256':DIGEST
    }, indent=2))
