"""Publish reviewed Git objects only after both isolated validation jobs passed."""
import base64
import json
import os
import subprocess
from pathlib import Path

base='6dbaa640710874a9fd8b2bfacb345c6ae13e8f65'
head='5553820f111b0cf22b66c70e394a1a2ec347d625'
tree='93a44d1235d77cc6f4b39f15095229202880dc71'
branch='fix/gui-reliability-20260918'
env=os.environ.copy()
auth=base64.b64encode(('x-access-token:'+env.pop('GH_TOKEN')).encode()).decode()
env.update(GIT_CONFIG_COUNT='1',GIT_CONFIG_KEY_0='http.https://github.com/.extraheader',
           GIT_CONFIG_VALUE_0='AUTHORIZATION: basic '+auth)
def git(*args):
    return subprocess.check_output(['git',*args],env=env).decode().strip()
assert git('rev-parse','HEAD') == head
assert git('rev-parse','HEAD^{tree}') == tree
remote=git('ls-remote','--heads','origin','main',branch)
refs={line.split()[1]:line.split()[0] for line in remote.splitlines()}
assert refs.get('refs/heads/main') == base, 'main moved; integrate before publishing'
assert refs.get('refs/heads/'+branch) in (None,base,head), 'feature branch changed unexpectedly'
# No force, no main write, no project imports or tests with these credentials.
git('push','origin',head+':refs/heads/'+branch)
assert git('ls-remote','--heads','origin',branch).split()[0] == head
out=Path(os.environ['RUNNER_TEMP'])/'gui-publication.json'
out.write_text(json.dumps({'base':base,'head':head,'tree':tree,'branch':branch},indent=2))
print(out.read_text())
