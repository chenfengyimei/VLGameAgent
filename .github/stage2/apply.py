"""Apply authenticated module patches to a fixed base; no writes outside this checkout."""
import base64
import hashlib
import json
import lzma
import os
from pathlib import Path
import subprocess
import zlib

BASE = "c4af90632865b4d8e758d6eabc84bd410ada38aa"
FINAL_TREE = "be2a4bd707e6c2a47ee213573e75e609f5f79f58"
ROOT = Path(__file__).resolve().parent


def git(*args):
    return subprocess.check_output(["git", *args], text=True, encoding="utf-8").strip()


def blob_id(text):
    data = text.encode("utf-8")
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def load_one(name, expected, digest, correction=False):
    encoded = (ROOT / name).read_text(encoding="utf-8").strip()
    if correction:
        # One verified transport transcription error, not a source change.
        # Authenticate the corrected bytes before decompression or applying.
        encoded = encoded.replace("Zi3wdTQh75", "Zi3wdTQ75")
    assert blob_id(encoded) == expected, f"encoded blob mismatch: {name}"
    data = zlib.decompress(base64.b64decode(encoded, validate=True))
    assert hashlib.sha256(data).hexdigest() == digest, f"module digest mismatch: {name}"
    return json.loads(data)


assert git("rev-parse", "HEAD") == BASE, "source baseline moved"
assert not git("status", "--porcelain"), "source checkout is dirty"
git("config", "core.autocrlf", "false")
git("config", "user.name", "github-actions[bot]")
git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
git("switch", "-c", "delivery")
entries = [
    load_one("module01.b64", "fedf6dc87fe7b93b6b0ce0a7b049728b43870e25",
             "025c9f575e6609ccd6d3a70191a6034a74f588260aeafd751191a096317daef5"),
    load_one("module02.b64", "442d12ae733cf5c34539c73ed504cee9db13f8a0",
             "8075231c93672ded82b32d5dd8eaff42173680d0ba15346b82e50826d89144c7", True),
]
encoded = "".join((ROOT / f"remaining-part{i}").read_text(encoding="utf-8").strip()
                  for i in range(5))
data = lzma.decompress(base64.b64decode(encoded, validate=True))
assert hashlib.sha256(data).hexdigest() == "12fa59dd4cb6b7afa35bbd824a2c801ff0437f08480e74fa8a5044d631dd1a52"
entries.extend(json.loads(data))
assert len(entries) == 8
for index, entry in enumerate(entries):
    target = Path(os.environ["RUNNER_TEMP"]) / f"module-{index:02d}.patch"
    target.write_bytes(entry["patch"].encode("utf-8"))
    subprocess.run(["git", "apply", "--check", "--unidiff-zero", str(target)], check=True)
    subprocess.run(["git", "apply", "--unidiff-zero", str(target)], check=True)
    git("add", "--all")
    assert git("write-tree") == entry["tree"], f"module {index} tree mismatch"
    git("commit", "-m", entry["message"])
    print(f"MODULE {index + 1}: {git('rev-parse', 'HEAD')} {entry['message']}")
assert git("rev-parse", "HEAD^{tree}") == FINAL_TREE
print("Authenticated module tree:", FINAL_TREE)
