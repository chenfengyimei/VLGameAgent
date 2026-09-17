"""Apply the last verified negative-case fix on top of the published module series."""
import hashlib
from pathlib import Path
import subprocess

BASE = "bcea453c7a5983510b0b4cd7ace39e64ef1aa60a"
TREE = "66da31280c93a83ab786910be65feea589a411fe"
patch = Path(__file__).resolve().parent / "finalization.patch"
assert hashlib.sha256(patch.read_bytes()).hexdigest() == "17ac63aeebfca2bcb3c223265cbe0ec6dd37628ca43b324bc83d5a289f295b17"

def git(*args):
    return subprocess.check_output(["git", *args], text=True, encoding="utf-8").strip()

assert git("rev-parse", "HEAD") == BASE
assert not git("status", "--porcelain")
git("config", "core.autocrlf", "false")
git("config", "user.name", "github-actions[bot]")
git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
git("switch", "-c", "delivery")
subprocess.run(["git", "apply", "--check", str(patch)], check=True)
subprocess.run(["git", "apply", str(patch)], check=True)
git("add", "--all")
assert git("write-tree") == TREE
git("commit", "-m", "fix(recording): make codec finalization failures non-retryable and close once")
print("Validated candidate tree:", TREE)
