"""Select a configured pipeline or direct video construction."""

import argparse
import sys


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    selector = argparse.ArgumentParser(add_help=False)
    group = selector.add_mutually_exclusive_group()
    group.add_argument("--pipeline")
    group.add_argument("--video")
    mode, _ = selector.parse_known_args(argv)
    if mode.pipeline is not None:
        from simready.workflows.pipelines.pipeline_config import main as pipeline_main

        return pipeline_main(argv)
    if mode.video is not None:
        from simready.workflows.pipelines.video_pipeline import main as video_main

        return video_main(argv)
    parser = argparse.ArgumentParser(
        prog="simready run",
        description="Construct and validate a scene from video or a pipeline config.",
        epilog=(
            "New input: simready run --video input.mp4 --out runs/new-scene "
            "--submit-reconstruction --accept-eula. "
            "Use --pipeline pipeline.json --help or --video input.mp4 --help."
        ),
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--pipeline", help="Versioned modular pipeline configuration JSON"
    )
    group.add_argument("--video", help="Video → reconstruction → scene → survey video")
    parser.parse_args(argv)
    return 0
