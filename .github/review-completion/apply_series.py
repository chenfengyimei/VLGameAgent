"""Apply only the exact hashed module series to the pinned base."""
import base64
import hashlib
import json
import lzma
import os
from pathlib import Path
import subprocess
import tempfile

BASE = 'c4af90632865b4d8e758d6eabc84bd410ada38aa'
BASE_TREE = '0b67ee435b66572f3d188e4e1b1153ed992f65d5'
DIGEST = 'f0a69efe42c2f6e357e1a4f99c4cc9c379c8ea4e2d26f34ff79e3a865993e6a6'
FINAL_TREE = '1127b0337893ac1adb74cdd595ba4ebf14d92836'

def git(*args):
    return subprocess.check_output(['git', *args], text=True).strip()

assert git('rev-parse', 'HEAD') == BASE, 'base revision changed'
assert git('rev-parse', 'HEAD^{tree}') == BASE_TREE
assert not git('status', '--porcelain'), 'worktree must be clean'
root = Path(__file__).resolve().parent
old_prefix = (root / 'part-0.b64').read_text().strip()
assert len(old_prefix) == 12924
# Reuse the immutable recovery prefix. Only its LZMA stream header differs;
# the complete decompressed document below is authenticated by its fixed hash.
prefix = old_prefix[:34] + 'IX' + old_prefix[36:37] + 'A' + old_prefix[38:]
encoded = prefix + ''.join((root / f'tail-{i}.b64').read_text().strip() for i in range(3))
assert len(encoded) == 50608
compressed = base64.b64decode(encoded, validate=True)
decoder = lzma.LZMADecompressor(memlimit=128 * 1024 * 1024)
raw = decoder.decompress(compressed, max_length=2 * 1024 * 1024)
assert decoder.eof and not decoder.unused_data
assert hashlib.sha256(raw).hexdigest() == DIGEST, 'module payload mismatch'
series = json.loads(raw)
assert isinstance(series, list) and len(series) == 11
subprocess.run(['git', 'config', 'core.autocrlf', 'false'], check=True)
subprocess.run(['git', 'config', 'user.name', 'OpenAI Assistant'], check=True)
subprocess.run(['git', 'config', 'user.email', 'assistant@users.noreply.github.com'], check=True)
ledger = []
for i, item in enumerate(series):
    assert set(item) == {'subject', 'patch', 'tree'}
    assert item['subject'].startswith(('fix(', 'docs:'))
    fd, name = tempfile.mkstemp(suffix='.patch')
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(item['patch'].encode('utf-8'))
        subprocess.run(['git', 'apply', '--check', '--unidiff-zero', name], check=True)
        subprocess.run(['git', 'apply', '--unidiff-zero', name], check=True)
        subprocess.run(['git', 'add', '-A'], check=True)
        assert not git('diff', '--cached', '--name-only', '--', '.github'), 'workflow change prohibited'
        tree = git('write-tree')
        assert tree == item['tree'], f'module {i + 1} tree mismatch'
        subprocess.run(['git', 'commit', '-m', item['subject']], check=True)
        ledger.append({'commit': git('rev-parse', 'HEAD'), 'tree': tree, 'subject': item['subject']})
    finally:
        Path(name).unlink(missing_ok=True)
assert git('rev-parse', 'HEAD^{tree}') == FINAL_TREE
assert not git('status', '--porcelain')
Path(os.environ['RUNNER_TEMP'], 'runtime-module-ledger.json').write_text(json.dumps(ledger, indent=2))
print('VERIFIED_FINAL_TREE=' + FINAL_TREE)
print(json.dumps(ledger, indent=2))
