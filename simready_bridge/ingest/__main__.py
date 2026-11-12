import argparse
import json

from .frames import extract_frames


def main():
    parser = argparse.ArgumentParser(
        description="Extract filtered, deduplicated video observations."
    )
    parser.add_argument("--video", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--fps", type=float, default=2)
    parser.add_argument("--min-laplacian", type=float, default=60)
    parser.add_argument("--dedup-bits", type=int, default=6)
    parser.add_argument("--heldout-every", type=int, default=10)
    args = parser.parse_args()
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
