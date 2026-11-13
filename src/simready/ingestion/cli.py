import argparse
import json
import sys


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "ground":
        from simready.ingestion.grounded_segmentation import main as ground_main

        return ground_main(argv[1:])
    if argv and argv[0] == "segment":
        from simready.ingestion.semantic_segmentation import main as segment_main

        return segment_main(argv[1:])
    if argv and argv[0] == "aholo":
        from simready.ingestion.aholo_reconstruction import main as aholo_main

        return aholo_main(argv[1:])
    parser = argparse.ArgumentParser(
        description="Extract video observations or submit/resume Aholo reconstruction."
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser(
        "segment", help="Source-bound semantic masks or explicit mask import"
    )
    subcommands.add_parser("ground", help="Open-vocabulary Grounding DINO + SAM masks")
    subcommands.add_parser(
        "aholo", help="Receipt-based cloud reconstruction and monitoring"
    )
    command = subcommands.add_parser("extract")
    command.add_argument("--video", required=True)
    command.add_argument("--out", required=True)
    command.add_argument("--fps", type=float, default=2)
    command.add_argument("--min-laplacian", type=float, default=60)
    command.add_argument("--dedup-bits", type=int, default=6)
    command.add_argument("--heldout-every", type=int, default=10)
    args = parser.parse_args(argv)
    from simready.ingestion.video_frames import extract_frames

    result = extract_frames(
        args.video,
        args.out,
        fps=args.fps,
        min_laplacian=args.min_laplacian,
        dedup_bits=args.dedup_bits,
        heldout_every=args.heldout_every,
    )
    print(
        json.dumps(
            {"frames": len(result["frames"]), "video_sha256": result["video_sha256"]}
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
