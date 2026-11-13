"""Simulation workers run in the selected Isaac Python process."""

import argparse
import importlib
import sys

WORKERS = {
    "doctor": "environment_check",
    "cafe-video": "cafe_video",
    "mission": "substation_mission",
    "mission-audit": "mission_audit",
    "mission-video": "mission_video",
    "export-bundle": "asset_bundle",
    "physical-probe": "physical_probe",
    "depth-observation": "depth_observation",
    "prepare-scene": "scene_preparation",
}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in WORKERS:
        worker, remaining = argv[0], argv[1:]
        sys.argv = [f"simready simulation {worker}", *remaining]
        return importlib.import_module(f"simready.simulation.{WORKERS[worker]}").main()
    parser = argparse.ArgumentParser(prog="simready simulation", description=__doc__)
    parser.add_argument("worker", choices=WORKERS)
    args, remaining = parser.parse_known_args(argv)
    sys.argv = [f"simready simulation {args.worker}", *remaining]
    return importlib.import_module(f"simready.simulation.{WORKERS[args.worker]}").main()
