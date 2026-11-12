"""Three synthetic scenes demonstrate learning, validation and subsequent reuse."""

import argparse
import sys
from pathlib import Path

if __package__:
    from .fixture_worker import render_model
else:
    from fixture_worker import render_model
from simready_bridge.artifacts import digest, write_json
from simready_bridge.benchmark.contracts import reference
from simready_bridge.pipeline import run_campaign


def prepare(root, scene, *, role="development", provider="rules", model=None):
    root = Path(root).resolve()
    inputs = root / f"scene_{scene}"
    inputs.mkdir(parents=True, exist_ok=False)
    initial = {"seed": scene + 12, "gain": 0.3}
    write_json(inputs / "model.json", initial)
    render_model(initial, inputs / "render.png")
    render_model(dict(initial, gain=1), inputs / "source.png")
    protocol = dict(
        schema_version=1,
        benchmark_id="agent-fixture-only",
        profile="observer_calibration",
        phase="development",
        fit_frame_ids=[],
        fit_image_sha256=[],
        views=[
            dict(
                view_id="front",
                asset_id="synthetic-panel",
                frame_id="test",
                split="development",
                reference_kind="synthetic_gt",
                rgb=reference(inputs / "source.png"),
                regions=[],
            )
        ],
        checks=[
            dict(
                check_id="coverage",
                track="coverage",
                metric="coverage.observation_fraction",
                op=">=",
                limit=1,
                required=True,
                severity="critical",
                calibration="structural",
                defect_kind="missing_observation",
            ),
            dict(
                check_id="rgb",
                track="appearance",
                metric="appearance.psnr_db.mean",
                op=">=",
                limit=22,
                required=True,
                severity="major",
                calibration="structural",
                defect_kind="appearance_mismatch",
            ),
        ],
    )
    write_json(inputs / "protocol.json", protocol)
    write_json(
        inputs / "candidate.json",
        dict(
            schema_version=1,
            candidate_id=f"fixture-{scene}",
            protocol_sha256=digest(inputs / "protocol.json"),
            artifacts=[dict(artifact_id="model", **reference(inputs / "model.json"))],
            views=[dict(view_id="front", rgb=reference(inputs / "render.png"))],
            fit_frame_ids=[],
            fit_image_sha256=[],
        ),
    )
    registry = {
        "schema_version": 1,
        "execution_enabled": True,
        "plugins": [
            dict(
                plugin_id="fixture_gain",
                version="1",
                description="Restore the known unit gain in this synthetic model.",
                priority=10,
                fixture=True,
                handles=["appearance_mismatch"],
                profiles=["observer_calibration"],
                families=["synthetic-gain"],
                parameter_schema={
                    "gain": {"type": "number", "minimum": 0, "maximum": 2}
                },
                variants=[{"name": "unit_gain", "parameters": {"gain": 1}}],
                result_task="repair",
                tasks=[
                    dict(
                        id="repair",
                        stage="synthetic_repair_and_render",
                        deps=[],
                        inputs={
                            "worker": str(
                                Path(__file__).with_name("fixture_worker.py").resolve()
                            )
                        },
                        argv=[
                            "{python}",
                            "{input:worker}",
                            "--candidate",
                            "{input:candidate}",
                            "--parameters",
                            "{input:parameters}",
                            "--out",
                            "{out}",
                        ],
                        outputs=["candidate.json", "model.json", "render.png"],
                        runtime_identity=f"fixture-python-{sys.version.split()[0]}",
                        timeout_s=30,
                        resources={"kind": "cpu", "cpu": 1, "ram_gb": 1},
                    )
                ],
            )
        ],
    }
    write_json(inputs / "registry.json", registry)
    write_json(
        inputs / "inventory.json",
        {
            "schema_version": 1,
            "cpu_slots": 2,
            "ram_gb": 2,
            "cloud_slots": 0,
            "gpus": [],
        },
    )
    agent = {"kind": provider}
    if model:
        agent["model"] = model
    config = dict(
        schema_version=1,
        scene_id=f"synthetic-{scene}",
        family="synthetic-gain",
        role=role,
        protocol="protocol.json",
        candidate="candidate.json",
        registry="registry.json",
        inventory="inventory.json",
        memory=str(root / "experience.sqlite"),
        agent=agent,
        auto_promote_skills=True,
        budget={
            "max_rounds": 3,
            "max_agent_calls": 3,
            "max_total_tokens": 30000,
            "wall_seconds": 300,
            "agent_timeout_s": 180,
            "verifier_timeout_s": 30,
        },
    )
    write_json(inputs / "campaign.json", config)
    return config, inputs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--provider", choices=["rules", "codex"], default="rules")
    parser.add_argument("--model")
    parser.add_argument("--scenes", type=int, choices=[1, 2, 3], default=3)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    reports = []
    for scene in range(args.scenes):
        config, base = prepare(
            args.out,
            scene,
            role="validation" if scene == 1 else "development",
            provider=args.provider,
            model=args.model,
        )
        result = run_campaign(config, base=base, out=args.out / f"run_{scene}")
        reports.append(
            {
                "scene": scene,
                "status": result["status"],
                "rounds": len(result["rounds"]),
                "tokens": result["total_tokens"],
                "release_authorized": result["release_authorized"],
            }
        )
        if result["status"] != "DEVELOPMENT_PASS":
            raise RuntimeError(result)
    write_json(
        args.out / "summary.json",
        {"synthetic_only": True, "provider": args.provider, "scenes": reports},
    )
    print(reports)


if __name__ == "__main__":
    main()
