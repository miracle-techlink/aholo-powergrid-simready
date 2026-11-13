"""Select new-video construction or reviewed-scene reproduction explicitly."""

import argparse
import sys


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    selector = argparse.ArgumentParser(add_help=False)
    group = selector.add_mutually_exclusive_group()
    group.add_argument("--video")
    group.add_argument("--scene")
    mode, _ = selector.parse_known_args(argv)
    if mode.video is not None:
        from simready.workflows.video_pipeline import main as video_main

        return video_main(argv)
    if mode.scene is not None:
        from simready.workflows.scene_pipeline import main as scene_main

        return scene_main(argv)
    parser = argparse.ArgumentParser(
        prog="simready run",
        description="Construct a new video-derived scene or reproduce a reviewed scene.",
        epilog=(
            "New input: simready run --video input.mp4 --out runs/new-scene "
            "--submit-reconstruction --accept-eula. "
            "Use --video input.mp4 --help or --scene substation --help for mode options."
        ),
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--video", help="Video → reconstruction → scene → survey video")
    group.add_argument("--scene", choices=("substation", "starbucks"))
    parser.parse_args(argv)
    return 0
