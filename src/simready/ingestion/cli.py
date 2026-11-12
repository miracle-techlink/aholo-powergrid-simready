import argparse
import json


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Extract filtered, deduplicated video observations."
    )
    command = parser.add_subparsers(dest="command", required=True).add_parser("extract")
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
