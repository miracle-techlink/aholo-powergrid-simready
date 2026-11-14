"""Public configuration entry; all execution delegates to the video workflow."""

import argparse
import json
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="simready run --pipeline", description=__doc__
    )
    parser.add_argument("--pipeline", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    state = parser.add_mutually_exclusive_group()
    state.add_argument(
        "--resume", action="store_true", help="Require exactly the frozen plan"
    )
    state.add_argument(
        "--upgrade",
        action="store_true",
        help="Archive the plan and reuse only stages with identical identities",
    )
    parser.add_argument(
        "--until",
        help="Stop after a configured stage with STAGE_READY, not scene delivery",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--accept-eula", action="store_true")
    parser.add_argument("--submit-reconstruction", action="store_true")
    args = parser.parse_args(argv)
    from simready.workflows.pipelines.video_pipeline import execute_video, make_plan
    from simready.workflows.planning.pipeline_spec import load_pipeline

    try:
        options = vars(args).copy()
        path, output = options.pop("pipeline"), options.pop("out")
        config = load_pipeline(path, output, **options)
        plan = make_plan(config)
        if args.dry_run:
            result, code = (
                {
                    "status": "PLANNED",
                    **plan,
                    "new_cloud_submission_authorized": args.submit_reconstruction,
                },
                0,
            )
        else:
            result, code = execute_video(config, plan)
    except (ValueError, OSError, KeyError, TypeError) as error:
        result, code = {"status": "INVALID", "error": str(error)}, 3
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
