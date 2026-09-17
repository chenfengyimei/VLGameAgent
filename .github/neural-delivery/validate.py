"""Read-only exact-tree validation with durable command logs and test XML."""
import os
from pathlib import Path
import subprocess
import sys

from apply import HEAD, TREE, git

assert git('rev-parse', 'HEAD') == HEAD and git('rev-parse', 'HEAD^{tree}') == TREE
out = Path(os.environ['RUNNER_TEMP']) / 'neural-evidence'
out.mkdir(exist_ok=True)
windows = sys.platform == 'win32'


def run(name, command, cwd=None):
    result = subprocess.run(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding='utf-8', errors='replace', timeout=900)
    (out / (name + '.log')).write_text(result.stdout, encoding='utf-8')
    print(name, 'exit', result.returncode, flush=True)
    print('\n'.join(result.stdout.splitlines()[-80:]), flush=True)
    if result.returncode:
        raise SystemExit(result.returncode)


run('base-dependencies', [sys.executable, '-m', 'pip', 'install', '--require-hashes', '-r', 'requirements-lock.txt'])
lock = 'requirements-neural-cpu-' + ('win32' if windows else 'linux') + '.txt'
run('neural-dependencies', [sys.executable, '-m', 'pip', 'install', '--require-hashes', '-r', lock])
run('editable-install', [sys.executable, '-m', 'pip', 'install', '--no-deps', '--no-build-isolation', '-e', '.'])
run('require-torch', [sys.executable, '-c', "import torch; assert torch.__version__ == '2.10.0+cpu'; assert not torch.cuda.is_available(); print(torch.__version__)"])
run('ruff', ['ruff', 'check', '.'])
run('mypy', ['mypy', 'uga', 'apps', '--platform', 'win32'])
if windows:
    run('rust-fmt', ['cargo', 'fmt', '--all', '--check'], 'native')
    run('rust-clippy', ['cargo', 'clippy', '--workspace', '--all-targets', '--locked', '--', '-D', 'warnings'], 'native')
    run('rust-tests', ['cargo', 'test', '--workspace', '--locked'], 'native')
    run('native-build', ['cargo', 'build', '-p', 'uga-capture', '--release', '--locked'], 'native')
    import hashlib
    dll = Path('native/target/release/uga_capture.dll').resolve()
    os.environ['UGA_NATIVE_CAPTURE_DLL'] = str(dll)
    os.environ['UGA_NATIVE_CAPTURE_SHA256'] = hashlib.sha256(dll.read_bytes()).hexdigest()
command = [sys.executable, '-m', 'pytest', '-q', '-ra', '--junitxml=' + str(out / 'full-tests.xml')]
if not windows:
    command += ['--ignore=tests/windows']
run('full-tests', command)
run('neural-tests', [sys.executable, '-m', 'pytest', 'tests/neural', '-q', '-ra', '--junitxml=' + str(out / 'neural-tests.xml')])
run('agent-smoke', [sys.executable, '-m', 'apps.agent'])
run('fixture-smoke', [sys.executable, '-m', 'apps.example_game', '--headless-smoke'])
run('build', [sys.executable, '-m', 'build', '--no-isolation'])
if windows:
    run('benchmark-config', [sys.executable, '-m', 'apps.benchmark', 'validate-config', 'configs/benchmarks/uga-bench-smoke.yaml'])
    run('dependency-inventory', [sys.executable, '-m', 'apps.dependency_inventory', '--require-known', '--output', str(out / 'dependency-inventory.json')])
    for name, args in [('npm-install', ['ci', '--include=dev']), ('typescript', ['run', 'typecheck']), ('ui-build', ['run', 'build'])]:
        run(name, ['npm.cmd', *args])
print('ALL_VALIDATION_PASSED', HEAD, TREE)
