"""Shared synthetic inputs; no test collection or real device initialization."""

from simready.core.artifacts import digest, write_json


def arguments(assets, output, scene="starbucks", backend="local"):
    return [
        "--scene",
        scene,
        "--backend",
        backend,
        "--assets",
        str(assets),
        "--out",
        str(output),
        "--accept-eula",
    ]


def make_bundle(root, scene="starbucks"):
    root.mkdir(parents=True)
    filenames = (
        ["input.mp4", "scene.usda", "robot.usda"]
        if scene == "starbucks"
        else ["input.mp4", "mission.json"]
    )
    for name in filenames:
        (root / name).write_bytes(name.encode())
    bundle = {
        "schema_version": 1,
        "scene": scene,
        "source_video": "input.mp4",
        "source_video_sha256": digest(root / "input.mp4"),
        "files": [
            {
                "path": name,
                "bytes": (root / name).stat().st_size,
                "sha256": digest(root / name),
            }
            for name in filenames
        ],
    }
    bundle.update(
        {"scene_usd": "scene.usda", "robot_usd": "robot.usda"}
        if scene == "starbucks"
        else {"mission_config": "mission.json"}
    )
    write_json(root / "bundle.json", bundle)
    return root


def prepared_spec(tmp_path):
    source = tmp_path / "prepared"
    source.mkdir()
    (source / "video.mp4").write_bytes(b"video fixture")
    (source / "scene.usda").write_text('#usda 1.0\ndef Xform "World" {}\n')
    write_json(
        source / "spec.json",
        {
            "scene": "starbucks",
            "source_video": "video.mp4",
            "scene_usd": "scene.usda",
            "robot_usd": "scene.usda",
        },
    )
    return source / "spec.json"
