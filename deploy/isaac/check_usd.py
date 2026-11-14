"""Fail the image build if its CPU USD compiler cannot author core scene assets."""

import importlib.metadata
import json
from pathlib import Path

from pxr import Usd, UsdGeom, UsdPhysics, UsdVol


def main():
    stage = Usd.Stage.CreateInMemory()
    body = UsdGeom.Xform.Define(stage, "/World").GetPrim()
    UsdPhysics.RigidBodyAPI.Apply(body)
    UsdVol.ParticleField3DGaussianSplat.Define(stage, "/World/Splats")
    Path("/opt/simready/usd-environment.json").write_text(
        json.dumps(
            {
                "usd_exchange": importlib.metadata.version("usd-exchange"),
                "openusd": list(Usd.GetVersion()),
                "gaussian_schema_authoring": "PASS",
                "scope": "CPU compilation only; Kit rendering requires separate GPU validation",
            },
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
