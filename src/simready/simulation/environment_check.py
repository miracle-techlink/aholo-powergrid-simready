"""Check installed SDKs; --launch performs a bounded headless Kit smoke test."""

import argparse
import importlib.metadata
import importlib.util
import json
import os
import platform
import shutil


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--launch", action="store_true")
    parser.add_argument("--require-lab", action="store_true")
    args = parser.parse_args()
    versions = {}
    for package in ("isaacsim", "isaaclab", "torch", "numpy", "onnxruntime"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    required = ["isaacsim", "numpy", "PIL", "pxr"]
    if args.require_lab:
        required += ["isaaclab", "isaaclab_physx", "onnxruntime", "cv2", "scipy"]
    missing = [name for name in required if not importlib.util.find_spec(name)]
    ffmpeg = bool(shutil.which("ffmpeg"))
    passed = not missing and ffmpeg
    result = {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "packages": versions,
        "ffmpeg": ffmpeg,
        "missing_modules": missing,
        "kit_launched": False,
    }
    if passed and args.launch:
        from isaacsim import SimulationApp

        app = SimulationApp(
            {"headless": True},
            experience=os.path.join(os.environ["EXP_PATH"], "isaacsim.exp.base.kit"),
        )
        for _ in range(10):
            app.update()
        result["kit_launched"] = True
        result["passed"] = bool(passed)
        print(json.dumps(result, indent=2), flush=True)
        app.close()
        return 0 if passed else 2
    result["passed"] = bool(passed)
    print(json.dumps(result, indent=2))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
