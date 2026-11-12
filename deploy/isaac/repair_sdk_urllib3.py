"""Keep SDK prebundle copies consistent after Isaac Lab upgrades urllib3.

SDK 6.1 ships partially symlinked urllib3 2.7 packages. Isaac Lab 3.0 requires
2.8+, so pip removes the shared 2.7 files. Replace the remaining SDK copies with
the entire installed distribution; never combine files from different versions.
"""

import hashlib
import importlib.metadata
import json
import shutil
import subprocess
import sys
from pathlib import Path

import urllib3

source = Path(urllib3.__file__).resolve().parent
version = importlib.metadata.version("urllib3")
if "site-packages" not in source.parts or urllib3.__version__ != version:
    raise RuntimeError("Expected the complete pip-installed urllib3 distribution")

targets = sorted(
    path
    for root in (Path("/isaac-sim/exts"), Path("/isaac-sim/extscache"))
    for path in root.glob("*/pip_prebundle/urllib3")
)
for target in targets:
    if target.is_symlink():
        target.unlink()
    else:
        shutil.rmtree(target)
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__"))
    for original in source.rglob("*.py"):
        copied = target / original.relative_to(source)
        if (
            hashlib.sha256(original.read_bytes()).digest()
            != hashlib.sha256(copied.read_bytes()).digest()
        ):
            raise RuntimeError(f"SDK package copy differs: {copied}")
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.path.insert(0, sys.argv[1]); import urllib3; "
            "assert urllib3.__version__ == sys.argv[2]; "
            "from urllib3 import exceptions; from urllib3.util import retry",
            str(target.parent),
            version,
        ],
        check=True,
    )

report = {
    "version": version,
    "source": str(source),
    "sdk_copies": list(map(str, targets)),
}
Path("/opt/simready").mkdir(parents=True, exist_ok=True)
Path("/opt/simready/sdk_urllib3_repair.json").write_text(
    json.dumps(report, indent=2) + "\n"
)
print(json.dumps(report))
