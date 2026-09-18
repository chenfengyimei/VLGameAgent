"""Restore pinned Git data and push one feature ref; never execute project code."""
from pathlib import Path
import base64
import hashlib
import json
import lzma
import os
import subprocess
import urllib.error
import urllib.request

BASE = '97030c7d5fb1cc27b6464392511059317fc857d5'
HEAD = '84b9a14e08263383ce5d2d12fbc85214cfd590c9'
TREE = '321bd5a92978f2ea2bba27819de3a4f8784f33a5'
BRANCH = 'fix/gui-closed-loop-20260918'
REPO = 'chenfengyimei/VLGameAgent'
DIGEST = '01dd9067f3a61b8a4c484ffe4e2cc03d1929dd3a583317431a185d439fbb957c'

def git(*args, data=None, env=None):
    return subprocess.check_output(['git', *args], input=data, env=env)

def read_api(path):
    request = urllib.request.Request('https://api.github.com/repos/' + REPO + path,
        headers={'Authorization': 'Bearer ' + os.environ['GH_TOKEN'],
                 'Accept': 'application/vnd.github+json', 'User-Agent': 'gui-pr-publish'})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)

assert git('rev-parse', 'HEAD').decode().strip() == BASE
assert not git('status', '--porcelain')
root = Path(__file__).resolve().parent
encoded = ''.join((root / ('part-%02d.txt' % i)).read_text() for i in range(4))
compressed = base64.b64decode(encoded, validate=True)
assert hashlib.sha256(compressed).hexdigest() == DIGEST
unpacker = lzma.LZMADecompressor(memlimit=256 * 1024 * 1024)
raw = unpacker.decompress(compressed, max_length=4 * 1024 * 1024)
assert unpacker.eof and not unpacker.unused_data
payload = json.loads(raw)
assert payload['base'] == BASE and payload['head'] == HEAD
assert len(payload['modules']) == 8
previous = BASE
ledger = []
for module in payload['modules']:
    commit = module['commit'].encode()
    expected = hashlib.sha1(b'commit ' + str(len(commit)).encode() + b'\0' + commit).hexdigest()
    assert expected == module['sha']
    headers = module['commit'].split('\n\n', 1)[0].splitlines()
    assert [line for line in headers if line.startswith('parent ')] == ['parent ' + previous]
    expected_tree = headers[0].removeprefix('tree ')
    patch = module['patch'].encode()
    git('apply', '--check', '--index', '--binary', '-', data=patch)
    git('apply', '--index', '--binary', '-', data=patch)
    assert git('write-tree').decode().strip() == expected_tree
    assert git('hash-object', '-t', 'commit', '-w', '--stdin', data=commit).decode().strip() == expected
    git('reset', '--hard', expected)
    ledger.append({'sha': expected, 'tree': expected_tree})
    previous = expected
assert previous == HEAD
assert git('rev-parse', 'HEAD^{tree}').decode().strip() == TREE
assert not git('status', '--porcelain')
git('diff', '--check', BASE, HEAD)
assert read_api('/git/ref/heads/main')['object']['sha'] == BASE, 'main advanced; do not push'
try:
    existing = read_api('/git/ref/heads/' + BRANCH)['object']['sha']
except urllib.error.HTTPError as exc:
    if exc.code != 404:
        raise
    existing = None
assert existing in (None, HEAD), 'feature branch exists with different work; do not overwrite'
if existing is None:
    env = dict(os.environ)
    credential = base64.b64encode(('x-access-token:' + env.pop('GH_TOKEN')).encode()).decode()
    env.update(GIT_CONFIG_COUNT='2',
        GIT_CONFIG_KEY_0='http.https://github.com/.extraheader',
        GIT_CONFIG_VALUE_0='AUTHORIZATION: basic ' + credential,
        GIT_CONFIG_KEY_1='credential.helper', GIT_CONFIG_VALUE_1='')
    git('push', 'origin', HEAD + ':refs/heads/' + BRANCH, env=env)
assert read_api('/git/ref/heads/' + BRANCH)['object']['sha'] == HEAD
result = {'repository': REPO, 'branch': BRANCH, 'head': HEAD, 'tree': TREE,
          'base_at_publication': BASE, 'modules': ledger, 'main_updated': False}
evidence = Path(os.environ['RUNNER_TEMP']) / 'gui-pr-publication'
evidence.mkdir(exist_ok=True)
(evidence / 'publication.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result, indent=2))
