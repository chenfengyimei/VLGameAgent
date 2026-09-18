"""Restore verified diffs and exact Git objects without importing project code."""
import base64
import hashlib
import json
import lzma
import subprocess
from pathlib import Path

BASE = '6dbaa640710874a9fd8b2bfacb345c6ae13e8f65'
HEAD = '5553820f111b0cf22b66c70e394a1a2ec347d625'
TREE = '93a44d1235d77cc6f4b39f15095229202880dc71'
PAYLOAD_SHA256 = 'd913476092e7820ef645efe4b0b2f94c32f1038b132b9bd84e3c796df1a6a5c8'

def git(*args, data=None):
    return subprocess.check_output(['git', *args], input=data)

encoded = ''.join(p.read_text() for p in sorted(Path(__file__).parent.glob('payload-*.txt')))
compressed = base64.b64decode(encoded, validate=True)
assert hashlib.sha256(compressed).hexdigest() == PAYLOAD_SHA256
record = json.loads(lzma.decompress(compressed))
assert (record['base'], record['head'], record['tree']) == (BASE, HEAD, TREE)
assert git('rev-parse', 'HEAD').decode().strip() == BASE
for commit in record['commits']:
    assert git('rev-parse', 'HEAD').decode().strip() == commit['parent']
    git('apply', '--index', '--binary', '-', data=commit['patch'].encode('utf-8'))
    assert git('write-tree').decode().strip() == commit['tree']
    sha = git('hash-object', '-t', 'commit', '-w', '--stdin', data=commit['commit'].encode('utf-8'))
    assert sha.decode().strip() == commit['sha']
    git('reset', '--hard', commit['sha'])
assert git('rev-parse', 'HEAD').decode().strip() == HEAD
assert git('rev-parse', 'HEAD^{tree}').decode().strip() == TREE
print('RESTORED', HEAD, TREE)

# Follow-up isolated after the first Windows validation found budget rounding.
extra_bytes = base64.b64decode(Path(__file__).with_name('extra.txt').read_text(), validate=True)
assert hashlib.sha256(extra_bytes).hexdigest() == '5278d0d355db82874c02d0c6ee6f0703dbb20cb65c30308211ad2b9cb4989f9d'
extra = json.loads(lzma.decompress(extra_bytes))
assert extra['parent'] == HEAD
assert extra['sha'] == 'b0479606e500a884bbe6f55035a9e120b4f6b7c8'
assert extra['tree'] == 'fdc1b7c0c6a5abeb6aa73970a4d0768124731864'
assert git('rev-parse', 'HEAD').decode().strip() == extra['parent']
git('apply', '--index', '--binary', '-', data=extra['patch'].encode('utf-8'))
assert git('write-tree').decode().strip() == extra['tree']
created = git('hash-object', '-t', 'commit', '-w', '--stdin', data=extra['commit'].encode('utf-8'))
assert created.decode().strip() == extra['sha']
git('reset', '--hard', extra['sha'])
print('RESTORED_FINAL', extra['sha'], extra['tree'])
