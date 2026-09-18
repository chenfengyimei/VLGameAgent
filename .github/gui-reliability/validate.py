"""Validate restored source with locked dependencies and retain test evidence."""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

root = Path.cwd()
evidence = Path(os.environ['RUNNER_TEMP']) / 'gui-reliability-evidence'
evidence.mkdir(exist_ok=True)
ledger = []

def run(name, command, cwd=root, timeout=600):
    result = subprocess.run(command, cwd=cwd, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=timeout)
    (evidence / (name + '.log')).write_bytes(result.stdout)
    ledger.append({'name':name,'returncode':result.returncode,'command':command})
    (evidence / 'commands.json').write_text(json.dumps(ledger, indent=2))
    print(name, 'exit', result.returncode, flush=True)
    print(result.stdout.decode('utf-8', 'replace')[-6000:], flush=True)
    if result.returncode:
        raise SystemExit(result.returncode)

py = sys.executable
run('base-dependencies', [py,'-m','pip','install','--require-hashes','-r','requirements-lock.txt'])
lock = 'win32' if sys.platform == 'win32' else 'linux'
run('neural-dependencies', [py,'-m','pip','install','--require-hashes','-r',f'requirements-neural-cpu-{lock}.txt'])
run('install', [py,'-m','pip','install','--no-deps','--no-build-isolation','-e','.'])
run('torch-required', [py,'-c',"import torch; assert torch.__version__ == '2.10.0+cpu'; assert not torch.cuda.is_available()"])
run('ruff', [py,'-m','ruff','check','.'])
run('mypy', [py,'-m','mypy','uga','apps','--platform','win32'])
if sys.platform == 'win32':
    for name, args in [('rust-fmt',['fmt','--all','--check']),
                       ('rust-clippy',['clippy','--workspace','--all-targets','--locked','--','-D','warnings']),
                       ('rust-tests',['test','--workspace','--locked']),
                       ('native-build',['build','-p','uga-capture','--release','--locked'])]:
        run(name, ['cargo',*args], root/'native')
    dll=(root/'native/target/release/uga_capture.dll').resolve()
    os.environ['UGA_NATIVE_CAPTURE_DLL']=str(dll)
    os.environ['UGA_NATIVE_CAPTURE_SHA256']=hashlib.sha256(dll.read_bytes()).hexdigest()
args=[py,'-m','pytest','-q','-ra',f'--junitxml={evidence / "full-tests.xml"}']
if sys.platform != 'win32':
    args.append('--ignore=tests/windows')
run('full-tests',args)
for attempt in range(3):
    run(f'gui-regression-{attempt+1}', [py,'-m','pytest','-q',
        'tests/unit/test_capture_cadence.py','tests/unit/test_capture_hub.py',
        'tests/unit/test_gui_effect_evidence.py','tests/integration/test_gui_final_state.py',
        'tests/integration/test_gui_model_roundtrip.py','tests/unit/test_deadline_precision.py',
        f'--junitxml={evidence / ("gui-regression-"+str(attempt+1)+".xml")}'])
run('agent-smoke',[py,'-m','apps.agent'])
run('fixture-smoke',[py,'-m','apps.example_game','--headless-smoke'])
run('benchmark-config',[py,'-m','apps.benchmark','validate-config','configs/benchmarks/uga-bench-smoke.yaml'])
run('inventory',[py,'-m','apps.dependency_inventory','--require-known','--output',str(evidence/'dependencies.json')])
run('build',[py,'-m','build','--no-isolation'])
if sys.platform == 'win32':
    for name,args in [('npm-install',['ci','--include=dev']),
                       ('typescript',['run','typecheck']),('ui-build',['run','build'])]:
        run(name,['npm.cmd',*args])
run('source-unchanged',['git','diff','--exit-code','HEAD'])
head=subprocess.check_output(['git','rev-parse','HEAD']).decode().strip()
tree=subprocess.check_output(['git','rev-parse','HEAD^{tree}']).decode().strip()
(evidence/'revision.json').write_text(json.dumps({'head':head,'tree':tree,'python':sys.version,
                                                'platform':sys.platform},indent=2))
print('ALL_VALIDATION_PASSED',head,tree,flush=True)
