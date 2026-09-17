"""Publish reviewed Git objects after exact-tree validation; never change branch refs."""
import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.error
import urllib.request

from apply import BASE, HEAD, TREE, git, read_series

ROOT = 'https://api.github.com/repos/chenfengyimei/VLGameAgent'
TOKEN = os.environ.pop('GH_TOKEN')


def api(path, data=None):
    req = urllib.request.Request(ROOT + path, data=None if data is None else json.dumps(data).encode(),
        headers={'Authorization': 'Bearer ' + TOKEN, 'Accept': 'application/vnd.github+json',
                 'X-GitHub-Api-Version': '2022-11-28', 'Content-Type': 'application/json'},
        method='GET' if data is None else 'POST')
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.load(response)


def existing(path):
    try:
        return api(path)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def person(raw, key):
    found = re.search(r'^' + key + r' (.+) <([^<>]+)> ([0-9]+) ([+-][0-9]{4})$', raw, re.MULTILINE)
    assert found
    name, email, seconds, offset = found.groups()
    minutes = (int(offset[1:3]) * 60 + int(offset[3:])) * (1 if offset[0] == '+' else -1)
    zone = datetime.timezone(datetime.timedelta(minutes=minutes))
    return {'name': name, 'email': email,
            'date': datetime.datetime.fromtimestamp(int(seconds), zone).isoformat()}


assert git('rev-parse', 'HEAD') == HEAD and git('rev-parse', 'HEAD^{tree}') == TREE
run = api('/actions/runs/35240697636')
assert run['head_sha'] == 'fd511769b1c3253c779754673e03517ff9d8e3ef'
checks = {j['name']: j['conclusion'] for j in api('/actions/runs/35240697636/jobs')['jobs']}
assert checks.get('verify (ubuntu-latest)') == 'success'
assert checks.get('verify (windows-latest)') == 'success'
assert api('/git/ref/heads/main')['object']['sha'] == BASE
assert api('/git/ref/heads/feat/neural-training-20260917')['object']['sha'] == BASE
parent = BASE
ledger = []
for item in read_series():
    created_tree = existing('/git/trees/' + item['tree'])
    if created_tree is None:
        paths = subprocess.check_output(['git', 'diff', '--no-renames', '--name-only', '-z', parent, item['commit']]).decode().split('\0')
        entries = []
        for path in filter(None, paths):
            row = git('ls-tree', item['commit'], '--', path)
            if not row:
                entries.append({'path': path, 'type': 'blob', 'mode': '100644', 'sha': None})
                continue
            mode, kind, blob = row.split('\t')[0].split()
            assert kind == 'blob' and mode == '100644'
            content = subprocess.check_output(['git', 'cat-file', 'blob', blob]).decode('utf-8')
            entries.append({'path': path, 'mode': mode, 'type': kind, 'content': content})
        created_tree = api('/git/trees', {'base_tree': git('rev-parse', parent + '^{tree}'), 'tree': entries})
    assert created_tree['sha'] == item['tree']
    created = existing('/git/commits/' + item['commit'])
    if created is None:
        raw = item['raw']
        created = api('/git/commits', {'message': raw.split('\n\n', 1)[1], 'tree': item['tree'],
            'parents': [parent], 'author': person(raw, 'author'), 'committer': person(raw, 'committer')})
    assert created['sha'] == item['commit']
    parent = item['commit']
    ledger.append({'sha': parent, 'tree': item['tree']})
assert parent == HEAD
folder = Path(os.environ['RUNNER_TEMP']) / 'neural-published'
folder.mkdir(exist_ok=True)
(folder / 'objects.json').write_text(json.dumps({'head': HEAD, 'tree': TREE, 'modules': ledger}, indent=2))
print('Verified Git objects published; no branch was modified:', HEAD, TREE)
